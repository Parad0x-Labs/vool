from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

from core import policy_engine
from core.local_model_bundles import model_metadata, model_parameter_billions
from core.local_model_policy import manifest_is_local
from core.model_health import get_provider_health
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, is_verified_free_cloud_manifest
from storage.model_provider_manifest import ModelProviderManifest

if TYPE_CHECKING:
    from core.orchestration.task_envelope import TaskEnvelopeV1

ProviderRole = Literal["auto", "drone", "queen"]


@dataclass(frozen=True)
class ProviderCapabilityTruth:
    provider_id: str
    model_id: str
    role_fit: str
    context_window: int
    tool_support: tuple[str, ...]
    structured_output_support: bool
    tokens_per_second: float
    ram_budget_gb: float
    vram_budget_gb: float
    quantization: str
    locality: str
    privacy_class: str
    queue_depth: int
    max_safe_concurrency: int
    availability_state: str = "ready"
    circuit_open: bool = False
    last_error: str | None = None
    measurement_source: str = "manifest"
    measured_at: str = ""
    hardware_fit: bool | None = None
    hardware_fit_reason: str = ""
    # Re-confirmed live 2026-08-04 (final-skeptic pass): `_role_bonus`'s free-cloud priority fix
    # only reaches `rank_provider_candidates`'s own ordering. For tool_intent/drone turns that
    # ordering never decides the actual pick -- `_resolve_lane` in `local_inference_autopilot.py`
    # routes every tool_intent turn to the "tiny" lane, `_select_primary_capability`/`_primary_score`
    # make an INDEPENDENT selection from `ProviderCapabilityTruth` alone (no manifest, no
    # `capabilities` list, so no way to see `is_verified_free_cloud_manifest` or a declared
    # `tool_intent` tag), and `_can_prioritize_autopilot_selection` in `memory_first_router.py`
    # unconditionally moves that autopilot pick to the front whenever `allow_paid_fallback` is
    # False -- which it always is for role=="drone" (`_resolve_allow_paid_fallback`). So the
    # `_role_bonus` fix was provably inert for the actual live tool_intent path measured with real
    # unpinned drives. This field is what lets the autopilot's own scoring see the same signal
    # `_role_bonus` already uses, without needing to pass the whole manifest/capabilities set
    # through a code path that was only ever given `ProviderCapabilityTruth`.
    is_verified_free_cloud_tool_capable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.provider_capability.v1",
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "role_fit": self.role_fit,
            "context_window": self.context_window,
            "tool_support": list(self.tool_support),
            "structured_output_support": self.structured_output_support,
            "tokens_per_second": self.tokens_per_second,
            "ram_budget_gb": self.ram_budget_gb,
            "vram_budget_gb": self.vram_budget_gb,
            "quantization": self.quantization,
            "locality": self.locality,
            "privacy_class": self.privacy_class,
            "queue_depth": self.queue_depth,
            "max_safe_concurrency": self.max_safe_concurrency,
            "availability_state": self.availability_state,
            "circuit_open": self.circuit_open,
            "last_error": self.last_error,
            "measurement_source": self.measurement_source,
            "measured_at": self.measured_at,
            "hardware_fit": self.hardware_fit,
            "hardware_fit_reason": self.hardware_fit_reason,
            "is_verified_free_cloud_tool_capable": self.is_verified_free_cloud_tool_capable,
        }


@dataclass(frozen=True)
class ProviderRoutingPlan:
    role: ProviderRole
    task_kind: str
    output_mode: str
    allow_paid_fallback: bool
    swarm_size: int
    preferred_provider: str | None
    preferred_model: str | None
    selected: ModelProviderManifest | None
    candidates: tuple[ModelProviderManifest, ...]
    capability_truth: tuple[ProviderCapabilityTruth, ...] = field(default_factory=tuple)
    task_envelope: dict[str, Any] = field(default_factory=dict)
    routing_requirements: dict[str, Any] = field(default_factory=dict)
    rejected_candidates: tuple[dict[str, str], ...] = field(default_factory=tuple)
    selection_notes: tuple[str, ...] = field(default_factory=tuple)
    # Per-candidate decision receipt (envelope path): final score plus the decisive value
    # terms, so a reviewer can see why a fast cheap route lost to a stronger one — or won —
    # without the chat surface carrying developer scoring internals.
    selection_scores: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def candidate_provider_ids(self) -> tuple[str, ...]:
        return tuple(manifest.provider_id for manifest in self.candidates)


def rank_provider_candidates(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
    enforce_hardware_fit: bool = False,
    local_only: bool = False,
    min_context_tokens: Any = 0,
) -> list[ModelProviderManifest]:
    """Rank the candidates for one model call.

    ``min_context_tokens`` is the call's serialized size (messages, tool schemas and completion
    headroom) when the caller knows it, read through ``parse_token_count``. A declared context
    window below it is not eligible; an unknown window stays eligible but ranks after every window
    known to cover it. Absent or 0 means the caller supplied no size, and nothing is filtered. A
    malformed size cannot be verified against any window, so nothing is eligible.
    """
    return _rank_provider_candidates_internal(
        registry,
        task_kind=task_kind,
        output_mode=output_mode,
        role=role,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        allow_paid_fallback=allow_paid_fallback,
        swarm_size=swarm_size,
        min_trust=min_trust,
        enforce_hardware_fit=enforce_hardware_fit,
        limit_to_swarm_size=True,
        local_only=local_only,
        min_context_tokens=min_context_tokens,
    )


def _context_eligible(manifests: list[ModelProviderManifest], required_tokens: int) -> list[ModelProviderManifest]:
    """Drop declared windows smaller than the call; keep unknown windows, after the known ones."""
    if required_tokens <= 0:
        return manifests
    known: list[ModelProviderManifest] = []
    unknown: list[ModelProviderManifest] = []
    for manifest in manifests:
        window = provider_capability_truth_for_manifest(manifest).context_window
        if window <= 0:
            unknown.append(manifest)
        elif window >= required_tokens:
            known.append(manifest)
    return known + unknown


def _rank_provider_candidates_internal(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
    enforce_hardware_fit: bool = False,
    limit_to_swarm_size: bool = True,
    local_only: bool = False,
    min_context_tokens: Any = 0,
) -> list[ModelProviderManifest]:
    required_context_tokens = parse_token_count(min_context_tokens)
    if required_context_tokens is None:
        # Fail closed: a size that is not a usable count is never read as "no size" (which would
        # disable context eligibility) and never truncated below what was asked.
        return []
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    base_ranked = registry.rank_manifests(
        ModelSelectionRequest(
            task_kind=task_kind,
            output_mode=output_mode,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            preferred_source_types=["http", "local_path", "subprocess"],
            # Local Only removes the paid arm as well, so the two never disagree about a turn: a
            # role whose default is paid-eligible must not re-open one manifest deep.
            allow_paid_fallback=resolved_allow_paid and not local_only,
            min_trust=min_trust,
            local_only=bool(local_only),
        )
    )
    if enforce_hardware_fit:
        base_ranked = [
            manifest
            for manifest in base_ranked
            if not provider_capability_truth_for_manifest(manifest).hardware_fit_reason
        ]
    # AUTHORSHIP ESCALATION ACROSS THE PREFERRED-MODEL FILTER. A preferred/pinned
    # model narrows base_ranked to itself; when that ONE candidate is an uncertified
    # local author (the precall fence will refuse it) and certified local authors
    # exist in the registry, the ranking re-reads WITHOUT the pin so the turn
    # escalates instead of failing on the only candidate it was handed (measured:
    # tool_intent turns attempted exactly one provider, then failed the turn).
    from core.final_answer_authorship import local_manifest_authorship_certified

    def _any_certified_local(manifests: list[ModelProviderManifest]) -> bool:
        return any(
            manifest_is_local(m) and local_manifest_authorship_certified(m)
            for m in manifests
        )

    if base_ranked and not _any_certified_local(base_ranked) and (
        str(preferred_model or "").strip() or str(preferred_provider or "").strip()
    ):
        unfiltered = registry.rank_manifests(
            ModelSelectionRequest(
                task_kind=task_kind,
                output_mode=output_mode,
                preferred_source_types=["http", "local_path", "subprocess"],
                allow_paid_fallback=resolved_allow_paid and not local_only,
                min_trust=min_trust,
                local_only=bool(local_only),
            )
        )
        if _any_certified_local(unfiltered):
            base_ranked = unfiltered
    if normalized_role == "auto":
        base_ranked = _context_eligible(base_ranked, required_context_tokens)
        return base_ranked[:resolved_swarm_size] if limit_to_swarm_size else base_ranked

    total = max(len(base_ranked), 1)
    rescored: list[tuple[float, ModelProviderManifest]] = []
    # AUTHORSHIP ESCALATION AT THE RANKING OWNER. The precall fence in
    # memory_first_router refuses an uncertified local author one candidate at a
    # time and relies on THIS order to escalate to a certified one. Ranking that
    # put the auto-registered default above every certified local manifest made
    # the "next candidate" the same refused candidate (measured: tool_intent
    # turns attempted exactly one provider and failed the turn). A local manifest
    # WITHOUT a completed final-answer certification ranks below every local
    # manifest WITH one; the fence stays untouched as the backstop.

    local_certified = [
        manifest
        for manifest in base_ranked
        if manifest_is_local(manifest) and local_manifest_authorship_certified(manifest)
    ]
    for index, manifest in enumerate(base_ranked):
        base_score = float(total - index)
        role_bonus = _role_bonus(manifest, normalized_role)
        if (
            local_certified
            and manifest_is_local(manifest)
            and not local_manifest_authorship_certified(manifest)
        ):
            # Below every certified local author, still above nothing else changed.
            base_score -= float(total) + 1.0
        rescored.append((base_score + role_bonus, manifest))
    rescored.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    ranked = _context_eligible([manifest for _, manifest in rescored], required_context_tokens)
    return ranked[:resolved_swarm_size] if limit_to_swarm_size else ranked


def resolve_provider_routing_plan(
    registry: ModelRegistry,
    *,
    task_kind: str,
    output_mode: str,
    role: ProviderRole = "auto",
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int = 1,
    min_trust: float = 0.0,
    task_envelope: dict[str, Any] | None = None,
    local_only: bool = False,
) -> ProviderRoutingPlan:
    normalized_role = _normalize_role(role)
    resolved_allow_paid = _resolve_allow_paid_fallback(normalized_role, allow_paid_fallback)
    resolved_swarm_size = _resolve_swarm_size(normalized_role, swarm_size)
    ranked_candidates = tuple(
        rank_provider_candidates(
            registry,
            task_kind=task_kind,
            output_mode=output_mode,
            role=normalized_role,
            preferred_provider=preferred_provider,
            preferred_model=preferred_model,
            allow_paid_fallback=resolved_allow_paid,
            swarm_size=resolved_swarm_size,
            min_trust=min_trust,
            local_only=bool(local_only),
        )
    )
    total = max(len(ranked_candidates), 1)
    accepted: list[tuple[float, ModelProviderManifest, ProviderCapabilityTruth]] = []
    rejected: list[dict[str, str]] = []
    selection_notes: list[str] = []
    for index, manifest in enumerate(ranked_candidates):
        capability = provider_capability_truth_for_manifest(manifest)
        if capability.availability_state == "blocked":
            rejected.append({"provider_id": manifest.provider_id, "reason": "provider_circuit_open"})
            continue
        score = float(max(1, total - index))
        if capability.availability_state == "degraded":
            score -= 0.8
        accepted.append((score, manifest, capability))
    accepted.sort(key=lambda item: (item[0], item[1].provider_name, item[1].model_name), reverse=True)
    candidates = tuple(item[1] for item in accepted)
    capability_truth = tuple(item[2] for item in accepted)
    if rejected:
        selection_notes.append("Provider routing skipped one or more circuit-open lanes.")
    if any(item.availability_state == "degraded" for item in capability_truth):
        selection_notes.append("Provider routing penalized degraded lanes with recent failures.")
    return ProviderRoutingPlan(
        role=normalized_role,
        task_kind=task_kind,
        output_mode=output_mode,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        selected=candidates[0] if candidates else None,
        candidates=candidates,
        capability_truth=capability_truth,
        task_envelope=dict(task_envelope or {}),
        rejected_candidates=tuple(rejected),
        selection_notes=tuple(selection_notes),
    )


def resolve_provider_routing_plan_for_envelope(
    registry: ModelRegistry,
    *,
    envelope: TaskEnvelopeV1,
    task_kind: str,
    output_mode: str,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    allow_paid_fallback: bool | None = None,
    swarm_size: int | None = None,
    min_trust: float = 0.0,
) -> ProviderRoutingPlan:
    from core.orchestration import provider_role_for_task_role

    resolved_role = provider_role_for_task_role(envelope.role)
    explicit_swarm_size = int(swarm_size or envelope.model_constraints.get("swarm_size") or 0)
    resolved_swarm_size = _resolve_swarm_size(_normalize_role(resolved_role), explicit_swarm_size)
    resolved_allow_paid = _resolve_allow_paid_fallback(
        _normalize_role(resolved_role),
        allow_paid_fallback if allow_paid_fallback is not None else envelope.model_constraints.get("allow_paid_fallback"),
    )
    ranked = _rank_provider_candidates_internal(
        registry,
        task_kind=task_kind,
        output_mode=output_mode,
        role=resolved_role,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        min_trust=min_trust,
        enforce_hardware_fit=False,
        limit_to_swarm_size=False,
    )
    requirements = _build_envelope_routing_requirements(envelope, output_mode=output_mode, provider_role=resolved_role)
    accepted: list[tuple[float, ModelProviderManifest, ProviderCapabilityTruth, dict[str, float]]] = []
    rejected: list[dict[str, str]] = []
    selection_notes = list(requirements["notes"])
    total = max(len(ranked), 1)
    for index, manifest in enumerate(ranked):
        capability = provider_capability_truth_for_manifest(manifest)
        rejection_reason = _routing_rejection_reason(capability, requirements)
        if rejection_reason:
            rejected.append({"provider_id": manifest.provider_id, "reason": rejection_reason})
            continue
        score, terms = _envelope_manifest_score(
            capability,
            requirements=requirements,
            provider_role=resolved_role,
            rank_index=index,
            total_candidates=total,
        )
        accepted.append((score, manifest, capability, terms))
    # A declared context requirement orders an UNKNOWN window after every window known to cover
    # it. Unknown stays eligible (most manifests carry no window metadata) but is never treated
    # as verified: measured on 57fdc03, the ordinal rank lead outweighed the +0.3 known-sufficient
    # term and an unknown-window model was selected over a 32768-window one for a 12000-token ask.
    required_window = int(requirements.get("required_window_tokens") or 0)

    def _window_verified(item: tuple[float, ModelProviderManifest, ProviderCapabilityTruth, dict[str, float]]) -> int:
        return 0 if required_window > 0 and item[2].context_window <= 0 else 1

    accepted.sort(
        key=lambda item: (_window_verified(item), item[0], item[1].provider_name, item[1].model_name),
        reverse=True,
    )
    unverified_windows = [item[1].model_name for item in accepted if not _window_verified(item)]
    if unverified_windows:
        selection_notes.append(
            f"Context window unknown for {', '.join(unverified_windows)}; ranked after candidates whose "
            f"declared window covers the {required_window}-token requirement."
        )
    selected_rows = accepted[:resolved_swarm_size]
    candidates = tuple(item[1] for item in selected_rows)
    capability_truth = tuple(item[2] for item in selected_rows)
    selection_scores = tuple(
        {
            "provider_id": item[1].provider_id,
            "model_id": item[1].model_name,
            "score": round(item[0], 4),
            "terms": {key: (round(value, 4) if isinstance(value, float) else value) for key, value in item[3].items()},
        }
        for item in accepted
    )
    if not candidates and requirements["fail_closed"]:
        selection_notes.append("No providers satisfied the envelope's hard locality requirements; routing failed closed.")
    elif any(item[2].queue_depth > 0 for item in selected_rows):
        selection_notes.append("Queue-depth pressure was applied while scoring provider candidates.")
    # Decision evidence for the value tradeoff: when the turn declared a latency or quality
    # budget and the winner is not the fastest MEASURED candidate, name both numbers so the
    # "expensive/slow but justified" choice is auditable. Measured-only: unmeasured candidates
    # never appear (their selection was deterministic-prior, not observation-driven).
    rejection_reasons_seen = {row["reason"] for row in rejected}
    if "context_window_too_small" in rejection_reasons_seen:
        selection_notes.append(
            "Candidates with a declared context window below the envelope's token requirement were rejected."
        )
    if "missing_required_tool_support" in rejection_reasons_seen:
        selection_notes.append("Candidates missing a hard-required model tool capability were rejected.")
    measured = [
        (float(item[2].tokens_per_second or 0.0), item[1])
        for item in accepted
        if float(item[2].tokens_per_second or 0.0) > 0.0
    ]
    if candidates and len(measured) >= 2 and str(requirements.get("latency_budget") or "") == "low_latency":
        winner = candidates[0]
        fastest = max(measured, key=lambda entry: entry[0])[1]
        winner_tps = next(
            (tps for tps, manifest in measured if manifest.provider_id == winner.provider_id),
            0.0,
        )
        fastest_tps = max(tps for tps, _ in measured)
        if winner.provider_id != fastest.provider_id:
            selection_notes.append(
                f"low_latency budget was overridden by other requirements: selected {winner.model_name} "
                f"({winner_tps:.1f} tok/s measured) over {fastest.model_name} ({fastest_tps:.1f} tok/s)."
            )
    return ProviderRoutingPlan(
        role=resolved_role,
        task_kind=task_kind,
        output_mode=output_mode,
        allow_paid_fallback=resolved_allow_paid,
        swarm_size=resolved_swarm_size,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        selected=candidates[0] if candidates else None,
        candidates=candidates,
        capability_truth=capability_truth,
        task_envelope=envelope.to_dict(),
        routing_requirements={key: value for key, value in requirements.items() if key != "notes"},
        rejected_candidates=tuple(rejected),
        selection_notes=tuple(selection_notes),
        selection_scores=selection_scores,
    )


# Upper bound of the token-count domain. Far above any declared context window, and small enough
# that a decimal string of it never approaches the interpreter's integer-conversion limit.
MAX_TOKEN_COUNT = 1_000_000_000
_MAX_TOKEN_COUNT_DIGITS = len(str(MAX_TOKEN_COUNT))


def parse_token_count(value: Any) -> int | None:
    """A declared token count, 0 when absent, or None when the value is not a usable count.

    The one domain for both selection entry points that take a size: the live ranking call
    (``rank_provider_candidates(min_context_tokens=)``) and the envelope's token requirements.
    Absent (None, a blank string) and 0 mean the caller supplied no size. A usable count is a
    whole number in ``[0, MAX_TOKEN_COUNT]``: an int, a finite float (a fraction rounds UP, so a
    requirement is never weakened below what was asked) or a string of ASCII decimal digits.
    Everything else is malformed, and a malformed hard requirement must not quietly become "no
    requirement": measured on 57fdc03, ``min_context_tokens="12,000"`` read as 0 and a
    4096-window model was selected. It must not raise either: ``str.isdigit`` admits
    superscript and other non-decimal digits that ``int`` cannot read.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_TOKEN_COUNT else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value > MAX_TOKEN_COUNT:
            return None
        return math.ceil(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if not (text.isascii() and text.isdigit()):
            return None
        digits = text.lstrip("0") or "0"
        if len(digits) > _MAX_TOKEN_COUNT_DIGITS:
            return None
        count = int(digits)
        return count if count <= MAX_TOKEN_COUNT else None
    return None


def _tool_requirement(value: Any) -> tuple[str, ...] | None:
    """Required tool names as a tuple; one bare name counts as one tool; None when malformed."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: list[Any] = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        return None
    if any(not isinstance(item, str) for item in items):
        return None
    return tuple(dict.fromkeys(item.strip() for item in items if item.strip()))


def _build_envelope_routing_requirements(
    envelope: TaskEnvelopeV1,
    *,
    output_mode: str,
    provider_role: str,
) -> dict[str, Any]:
    constraints = dict(envelope.model_constraints or {})
    tool_permissions = {str(item).strip() for item in envelope.tool_permissions if str(item).strip()}
    allowed_side_effects = {str(item).strip() for item in envelope.allowed_side_effects if str(item).strip()}
    privacy_class = str(envelope.privacy_class or "").strip().lower()
    inferred_requires_local = privacy_class in {"local_private"} or bool(
        allowed_side_effects & {"workspace_write", "memory_write"}
    )
    required_locality = str(constraints.get("required_locality") or "").strip()
    requires_local = inferred_requires_local or required_locality == "local"
    preferred_tool_support: list[str] = []
    preferred_tool_support.extend(str(item).strip() for item in list(constraints.get("preferred_tool_support") or []) if str(item).strip())
    if any(permission.startswith("web.") for permission in tool_permissions) and "web_search" not in preferred_tool_support:
        preferred_tool_support.append("web_search")
    notes: list[str] = []
    if requires_local:
        notes.append("Envelope requires a local provider because the task is private or has mutating side effects.")
    if constraints.get("queue_pressure_strategy") == "fail_closed":
        notes.append("Queue-pressure strategy is fail-closed for this envelope.")
    # Context eligibility, through the same requirements channel `CloudTaskRequirements` uses on
    # the cloud lane (`min_context_tokens` + `expected_output_tokens`). The number is the
    # serialized-payload token estimate the ENVELOPE OWNER supplies — tool schema bytes and
    # completion headroom belong in it, exactly as the cloud lane's contract already states;
    # provider routing does not re-estimate packing here (that owner is `core.prompt_budget`).
    malformed: list[str] = []
    parsed_context = parse_token_count(constraints.get("min_context_tokens"))
    parsed_output = parse_token_count(constraints.get("expected_output_tokens"))
    if parsed_context is None:
        malformed.append("min_context_tokens")
    if parsed_output is None:
        malformed.append("expected_output_tokens")
    min_context_tokens = parsed_context or 0
    expected_output_tokens = parsed_output or 0
    required_window_tokens = min_context_tokens + expected_output_tokens
    # REQUIRED tool support is an explicit operator constraint, distinct from the soft preference
    # inferred from a web.* permission GRANT (a permission to use VOOL's runtime web tool is not
    # a demand for model-native web_search). Unknown-declared tool support fails this gate — an
    # unknown required capability is never assumed true.
    parsed_tools = _tool_requirement(constraints.get("required_tool_support"))
    if parsed_tools is None:
        malformed.append("required_tool_support")
    required_tool_support = parsed_tools or ()
    for name in malformed:
        notes.append(
            f"Envelope constraint {name}={constraints.get(name)!r} is not a usable requirement; "
            "no candidate can be verified against it, so routing failed closed."
        )
    if required_window_tokens > 0:
        notes.append(
            f"Envelope requires a context window of at least {required_window_tokens} tokens "
            f"(payload {min_context_tokens} + completion {expected_output_tokens})."
        )
    if required_tool_support:
        notes.append(f"Envelope hard-requires model tool support: {', '.join(required_tool_support)}.")
    return {
        "required_locality": required_locality or ("local" if requires_local else None),
        "preferred_locality": str(constraints.get("preferred_locality") or "").strip() or ("local" if envelope.role in {"coder", "verifier", "memory_clerk"} else None),
        "preferred_role_fit": str(constraints.get("preferred_provider_role") or "").strip() or (provider_role if provider_role != "auto" else ""),
        "preferred_tool_support": tuple(dict.fromkeys(preferred_tool_support)),
        "required_tool_support": required_tool_support,
        "min_context_tokens": min_context_tokens,
        "expected_output_tokens": expected_output_tokens,
        "required_window_tokens": required_window_tokens,
        "malformed_constraints": tuple(malformed),
        # Value priors from the envelope's own budget fields (the vocabulary task_router mints
        # and resource_scheduler already consumes): low_latency wants measured speed,
        # quality high wants the stronger lane, draft tolerates the small fast one.
        "latency_budget": str(envelope.latency_budget or "balanced").strip().lower() or "balanced",
        "quality_target": str(envelope.quality_target or "standard").strip().lower() or "standard",
        "prefer_structured_output": bool(constraints.get("prefer_structured_output", output_mode != "plain_text" or envelope.role in {"coder", "verifier", "queen", "narrator"})),
        "prefer_long_context": bool(constraints.get("prefer_long_context", envelope.role in {"queen", "researcher"})),
        "prefer_code_complex": bool(constraints.get("prefer_code_complex", envelope.role == "coder")),
        "fail_closed": requires_local,
        "notes": tuple(notes),
    }


def _routing_rejection_reason(capability: ProviderCapabilityTruth, requirements: dict[str, Any]) -> str:
    if requirements.get("malformed_constraints"):
        return "malformed_requirement"
    if capability.availability_state == "blocked":
        return "provider_circuit_open"
    required_locality = str(requirements.get("required_locality") or "").strip()
    if required_locality and capability.locality != required_locality:
        return "requires_local_provider"
    # Context eligibility is a HARD gate, mirroring the cloud lane's `context_too_small` law in
    # `core.cloud_routing`: a declared window smaller than the envelope's serialized-payload
    # requirement can never serve that turn regardless of score. Measured on the pinned base,
    # a 4096-window model was SELECTED for a 12000-token requirement because context was at
    # most a `prefer_long_context` bonus. A window of 0 means UNKNOWN, never "small": unknown
    # data must not hard-reject (that would brick every manifest without window metadata), it
    # only loses the known-sufficient preference in scoring.
    required_window = int(requirements.get("required_window_tokens") or 0)
    if required_window > 0 and capability.context_window > 0 and capability.context_window < required_window:
        return "context_window_too_small"
    required_tools = tuple(str(item).strip().lower() for item in requirements.get("required_tool_support") or ())
    if required_tools:
        declared_tools = {str(item).strip().lower() for item in capability.tool_support}
        if any(tool not in declared_tools for tool in required_tools):
            return "missing_required_tool_support"
    return ""


@lru_cache(maxsize=1)
def _device_probe():
    """Probe the host once per process; hardware is stable for a run.

    Returns None if probing fails for any reason, so scoring degrades to its
    prior behavior rather than erroring.
    """
    try:
        from core.hardware_tier import probe_machine

        return probe_machine()
    except Exception:
        return None


def _hardware_fit_adjustment(
    capability: ProviderCapabilityTruth,
    *,
    ram_gb: float,
    vram_gb: float,
    accelerator: str,
) -> float:
    """Score delta for whether a manifest's declared footprint fits this machine.

    Penalizes a model that will not fit the box the user actually has (the core
    commodity-hardware concern); mildly prefers one that leaves headroom. INERT
    when the manifest declares no footprint — we never penalize on missing data,
    so manifests without ram/vram budgets keep their prior score exactly.

    Unified memory (Apple mps) has no separate VRAM pool: weights live in the
    SAME RAM the OS uses, and the probe reports vram_gb == ram_gb. Gauging the
    requirement against a phantom VRAM budget would double-count and over-admit,
    so on mps the footprint is measured against total RAM.
    """
    need_ram = float(capability.ram_budget_gb or 0.0)
    need_vram = float(capability.vram_budget_gb or 0.0)
    if need_ram <= 0.0 and need_vram <= 0.0:
        return 0.0
    accel = (accelerator or "cpu").strip().lower()
    if accel == "mps":
        need, budget = max(need_ram, need_vram), ram_gb
    elif accel in {"cuda", "directml"} and need_vram > 0.0 and vram_gb > 0.0:
        need, budget = need_vram, vram_gb
    else:
        need, budget = max(need_ram, need_vram), ram_gb
    if need <= 0.0 or budget <= 0.0:
        return 0.0
    if need > budget:
        # Over capacity — strong, overage-scaled penalty that sinks the candidate
        # below any model that actually fits. Base scores differ by O(1), so this
        # is decisive without being a hard reject (a no-fit box can still pick the
        # least-bad option if nothing fits).
        return -3.0 - min(5.0, (need - budget) / budget)
    # Fits — small headroom bonus (<= +0.6) that nudges among fitting models
    # without overriding role/locality intent.
    return min(0.6, (budget - need) / budget * 0.6)


def estimated_local_model_resident_gb(model_id: str, *, artifact_size_gb: float = 0.0) -> float:
    """Conservative resident load budget from measured weights plus runtime headroom.

    Ollama's ``/api/tags`` size is the strongest artifact evidence available at boot, but weights
    are not the whole resident allocation: runtime/KV overhead still needs the same 1.5 GB floor
    used by manifest capability truth. When artifact bytes are unavailable, retain the established
    parameter-based estimate. MoE active parameters are intentionally irrelevant to both paths.
    """

    measured_weights_gb = max(0.0, float(artifact_size_gb or 0.0))
    if measured_weights_gb > 0.0:
        return round(measured_weights_gb + 1.5, 2)
    metadata_count = str(model_metadata(model_id).get("parameter_count") or "").strip().lower().rstrip("b")
    try:
        total_parameters_b = float(metadata_count) if metadata_count else 0.0
    except ValueError:
        total_parameters_b = 0.0
    if total_parameters_b <= 0.0:
        # Unknown MoE tags often encode both total and active parameters (``30b-a3b``). Compute
        # routing may reasonably score the active count, but residency must hold every expert's
        # weights, so the largest declared parameter count is the conservative total.
        declared_counts = [
            float(match)
            for match in re.findall(r"(\d+(?:\.\d+)?)b", str(model_id or "").strip().lower())
        ]
        total_parameters_b = max(declared_counts, default=0.0)
    if total_parameters_b <= 0.0:
        total_parameters_b = model_parameter_billions(model_id)
    return round(total_parameters_b * 0.75 + 1.5, 2)


def provider_hardware_fit_rejection(
    capability: ProviderCapabilityTruth,
    *,
    probe: Any | None = None,
    force_cpu: bool = False,
) -> str:
    """Return a hard rejection reason when a local model cannot fit this host.

    A score penalty is too weak for failover: if every fitting model fails, a
    too-large model eventually rises to the front and the runtime crashes. This
    gate removes proven no-fit candidates from the chain entirely.
    """
    if capability.locality != "local":
        return ""
    active_probe = _device_probe() if probe is None else probe
    if active_probe is None:
        return "hardware_capacity_unknown" if model_parameter_billions(capability.model_id) >= 13.0 else ""
    ram_gb = float(getattr(active_probe, "ram_gb", 0.0) or 0.0)
    vram_gb = float(getattr(active_probe, "vram_gb", 0.0) or 0.0)
    accelerator = (
        "cpu"
        if force_cpu
        else str(getattr(active_probe, "accelerator", "cpu") or "cpu").strip().lower()
    )
    need_ram = float(capability.ram_budget_gb or 0.0)
    need_vram = float(capability.vram_budget_gb or 0.0)
    if need_ram <= 0.0 and need_vram <= 0.0:
        return "hardware_footprint_unknown" if model_parameter_billions(capability.model_id) >= 13.0 else ""
    if accelerator == "mps":
        required = max(need_ram, need_vram)
        available = max(0.0, ram_gb * 0.75)
    elif accelerator in {"cuda", "directml"} and vram_gb > 0.0:
        required = need_vram or need_ram
        available = max(0.0, vram_gb - 1.2)
    else:
        required = max(need_ram, need_vram)
        available = max(0.0, ram_gb * 0.70)
    if required <= 0.0 or available <= 0.0:
        return "hardware_capacity_unknown" if model_parameter_billions(capability.model_id) >= 13.0 else ""
    return "model_exceeds_hardware_budget" if required > available else ""


def _envelope_manifest_score(
    capability: ProviderCapabilityTruth,
    *,
    requirements: dict[str, Any],
    provider_role: str,
    rank_index: int,
    total_candidates: int,
) -> tuple[float, dict[str, float]]:
    """Score one eligible candidate and return the decisive terms for the decision receipt.

    The second element separates each value term from the base score so the routing plan can
    explain WHY a fast cheap route lost to a stronger one (or won) without dumping developer
    internals into the chat surface.
    """
    score = float(max(1, total_candidates - rank_index))
    terms: dict[str, float] = {"rank_base": score}
    cap_tools = {str(item).strip().lower() for item in capability.tool_support if str(item).strip()}
    preferred_role_fit = str(requirements.get("preferred_role_fit") or "").strip()
    if preferred_role_fit and capability.role_fit == preferred_role_fit:
        score += 1.4
        terms["role_fit"] = 1.4
    elif preferred_role_fit and capability.role_fit not in {"", "auto", preferred_role_fit}:
        score -= 0.6
        terms["role_fit"] = -0.6
    preferred_locality = str(requirements.get("preferred_locality") or "").strip()
    if preferred_locality and capability.locality == preferred_locality:
        score += 1.0
        terms["locality_fit"] = 1.0
    elif preferred_locality and capability.locality != preferred_locality:
        score -= 0.35
        terms["locality_fit"] = -0.35
    if bool(requirements.get("prefer_structured_output", False)):
        delta = 0.45 if capability.structured_output_support else -0.2
        score += delta
        terms["structured_output"] = delta
    if bool(requirements.get("prefer_long_context", False)) and capability.context_window > 0:
        delta = min(0.8, float(capability.context_window) / 64000.0)
        score += delta
        terms["long_context"] = delta
    if bool(requirements.get("prefer_code_complex", False)):
        if "code_complex" in cap_tools:
            score += 0.55
            terms["code_complex"] = 0.55
        elif capability.locality == "local":
            score += 0.15
            terms["code_complex"] = 0.15
    for preferred_tool in tuple(requirements.get("preferred_tool_support") or ()):
        if preferred_tool in cap_tools:
            score += 0.4
            terms[f"tool:{preferred_tool}"] = 0.4
        else:
            score -= 0.15
            terms[f"tool:{preferred_tool}"] = -0.15
    if capability.availability_state == "degraded":
        score -= 0.75
        terms["degraded"] = -0.75
    queue_pressure = float(capability.queue_depth) / float(max(1, capability.max_safe_concurrency))
    queue_penalty = min(2.5, queue_pressure)
    if queue_penalty:
        score -= queue_penalty
        terms["queue_pressure"] = -queue_penalty
    if provider_role == "queen" and capability.locality == "remote":
        score += 0.15
        terms["queen_remote"] = 0.15
    # Context-window preference (only meaningful once the turn declared a requirement):
    # a DECLARED window at or above the requirement is preferred over an UNKNOWN one; a
    # hard-too-small window never reaches here (rejected above), and unknown (0) stays
    # neutral rather than penalized on missing data.
    required_window = int(requirements.get("required_window_tokens") or 0)
    if required_window > 0 and capability.context_window >= required_window:
        delta = 0.3
        score += delta
        terms["context_known_sufficient"] = delta
    # Latency prior (low_latency budget): measured throughput only. tokens_per_second == 0 is
    # UNKNOWN, never "slow" — an unmeasured candidate keeps its prior score exactly, so
    # selection stays deterministic when optional enrichment (benchmarks, catalog) is absent.
    tokens_per_second = float(capability.tokens_per_second or 0.0)
    if str(requirements.get("latency_budget") or "") == "low_latency" and tokens_per_second > 0.0:
        speed_delta = min(1.2, tokens_per_second / 50.0)
        if tokens_per_second < 8.0:
            speed_delta -= 0.8
        score += speed_delta
        terms["measured_speed"] = round(speed_delta, 4)
    # Quality prior, local lane only: a remote model id carries no meaningful parameter count,
    # so bands would misread names rather than measure fit. draft prefers the small fast local
    # model; high prefers the stronger local lane.
    quality_target = str(requirements.get("quality_target") or "")
    if capability.locality == "local":
        size_b = model_parameter_billions(capability.model_id)
        if quality_target == "draft" and size_b > 0.0:
            delta = 0.4 if size_b <= 4.0 else (-0.2 if size_b >= 13.0 else 0.0)
            if delta:
                score += delta
                terms["quality_band"] = delta
        elif quality_target == "high" and ("code_complex" in cap_tools or size_b >= 13.0):
            delta = 0.4
            score += delta
            terms["quality_band"] = delta
    # Hardware-fit: prefer a model that fits the user's actual box, sink one that
    # won't. Inert for manifests that declare no ram/vram footprint.
    probe = _device_probe()
    if probe is not None:
        hardware_delta = _hardware_fit_adjustment(
            capability,
            ram_gb=float(probe.ram_gb or 0.0),
            vram_gb=float(probe.vram_gb or 0.0),
            accelerator=str(probe.accelerator or "cpu"),
        )
        if hardware_delta:
            score += hardware_delta
            terms["hardware_fit"] = round(hardware_delta, 4)
    return score, terms


def _normalize_role(role: str) -> ProviderRole:
    clean = str(role or "auto").strip().lower()
    if clean in {"drone", "queen"}:
        return clean
    return "auto"


def _resolve_allow_paid_fallback(role: ProviderRole, explicit: bool | None) -> bool:
    if explicit is not None:
        return bool(explicit)
    if role == "drone":
        return False
    if role == "queen":
        return bool(policy_engine.get("model_orchestration.queen_allow_paid_fallback", True))
    return True


def _resolve_swarm_size(role: ProviderRole, requested: int) -> int:
    requested_value = max(1, int(requested or 1))
    if role == "drone":
        default_width = int(policy_engine.get("model_orchestration.drone_swarm_width", 2) or 2)
    elif role == "queen":
        default_width = int(policy_engine.get("model_orchestration.queen_swarm_width", 1) or 1)
    else:
        default_width = requested_value
    return max(1, min(4, requested_value if requested else default_width))


def _role_bonus(manifest: ModelProviderManifest, role: ProviderRole) -> float:
    if role == "auto":
        return 0.0
    provider_hint = str(policy_engine.get("model_orchestration.queen_provider_hint", "kimi") or "kimi").strip().lower()
    drone_hint = str(policy_engine.get("model_orchestration.drone_provider_hint", "qwen") or "qwen").strip().lower()
    orchestration_role = str((manifest.metadata or {}).get("orchestration_role") or "").strip().lower()
    deployment_class = str((manifest.metadata or {}).get("deployment_class") or "").strip().lower()
    text_blob = " ".join(
        [
            manifest.provider_name,
            manifest.model_name,
            str(manifest.notes or ""),
            str((manifest.metadata or {}).get("runtime_family") or ""),
        ]
    ).lower()
    capabilities = {str(item).strip().lower() for item in list(manifest.capabilities or [])}
    is_local = manifest_is_local(manifest)
    is_remote = not is_local or deployment_class == "cloud"

    if role == "drone":
        score = 0.0
        if orchestration_role == "drone":
            score += 1.5
        if is_local:
            score += 0.9
        if drone_hint and drone_hint in text_blob:
            score += 0.55
        if "structured_json" in capabilities:
            score += 0.12
        # Finding G, 2026-08-04: `resolve_tool_intent` always calls with role=="drone", and a
        # free OpenRouter manifest is tagged orchestration_role=="queen" for unrelated (chat-lane
        # escalation) reasons, not because it is unfit for tool selection -- charging it the full
        # queen-tag penalty here punishes a mistagging, not a real drone-suitability signal.
        # Measured live: local (drone-tagged, qwen hint match) scored ~+3.07 while the free
        # manifest scored ~-1.78 -- a ~4.9pt gap the non-drone branch's existing free-cloud boost
        # (`is_verified_free_cloud_manifest`, Finding F) never reaches because it only fires in
        # that other branch. Exempt a manifest VERIFIED to be free from the queen-tag penalty (an
        # ordinary paid queen-tagged remote model keeps it in full), and mirror the non-drone
        # branch's exact +0.5 free-cloud bonus on top of the remote treatment -- same magnitude,
        # same reasoning: a genuinely free remote model costs the operator nothing, so it should
        # not be treated as a worse bet than an equally-capable paid one.
        verified_free_remote = is_remote and is_verified_free_cloud_manifest(manifest)
        if orchestration_role == "queen" and not verified_free_remote:
            score -= 1.25
        if provider_hint and provider_hint in text_blob:
            score -= 0.45
        if is_remote:
            score -= 0.65
            if verified_free_remote:
                score += 0.5
                # Re-confirmed live 2026-08-04 (final-skeptic pass): the lock-out removal above
                # only stopped this branch from scoring the free lane BELOW zero -- it left local
                # winning 100% of unpinned drives (measured: local +3.07 vs free-cloud -0.03, a
                # ~3.1pt gap), which is not what the operator asked for. The operator's mandate is
                # explicit and ordered: "the best of Cloud and local llm (priority is cloud[cloud]
                # free openrouter ai's)" -- free-cloud FIRST, local second, not local-first with
                # free-cloud merely reachable. A manifest that is BOTH verified free AND declares
                # `tool_intent` (it can genuinely select a tool from a schema -- see
                # `_ensure_openrouter_byok_provider`, not a ranking hack) gets enough bonus here to
                # not only clear 1.5 + 0.9 + 0.55 + 0.12 = 3.07 (the highest score a fully-tagged
                # local drone candidate can reach on THIS branch) but also the ordinal-position
                # `base_score` gap `_rank_provider_candidates_internal` adds on top from the
                # underlying `rank_manifests` ordering, which favors local by ~1 point per rank
                # position on trust (0.70 vs 0.63) and cost-class scoring even when both manifests
                # are otherwise comparable -- measured live: a role_bonus gap of +0.9 alone (a
                # smaller version of this same fix) was not enough to move `rank_provider_
                # candidates(role="drone")`'s actual first pick off `ollama-local`. +6.0 clears
                # both terms with room to spare while still losing outright to any local candidate
                # that is itself missing `tool_intent`/`structured_json`/drone tags (this bonus
                # never fires without a REAL verified-free, tool-capable manifest to attach to).
                # This is the ranking default only: `local_only_mode()` and an explicit pin are
                # both resolved before a manifest ever reaches this function
                # (`_hard_exclusion_reason` in `model_selection_policy.py` excludes every non-
                # `free_local` manifest outright when local-only is on), and a free-cloud manifest
                # that is not actually available -- no key, disabled, rate-limited -- never
                # satisfies `is_verified_free_cloud_manifest` in the first place, so local remains
                # the real, working fallback whenever free-cloud genuinely is not viable.
                if "tool_intent" in capabilities:
                    score += 6.0
        return score

    score = 0.0
    if orchestration_role == "queen":
        score += 1.7
    if provider_hint and provider_hint in text_blob:
        score += 1.35
    if "long_context" in capabilities:
        score += 0.45
    if "code_complex" in capabilities:
        score += 0.18
    if is_remote:
        score += 0.32
        # Finding F, 2026-08-04: this flat "remote" bonus does not distinguish a genuinely free
        # cloud model from an ordinary paid one, so audit routing only preferred free-cloud by
        # ACCIDENT -- because `enforce_hardware_fit=True` had already knocked large local queen
        # models out on a memory-constrained box, not because the free lane won on its own merit.
        # On a box with enough free RAM to keep a local queen model eligible, that accident stopped
        # applying and a local/free-cloud tie was decided by this +0.32 alone. An explicit boost
        # keyed on the manifest actually being priced at zero (not merely "remote") is what makes
        # free-cloud win because it is free, on every box, not only a memory-constrained one.
        if is_verified_free_cloud_manifest(manifest):
            score += 0.5
    if is_local and drone_hint and drone_hint in text_blob:
        score += 0.16
    if orchestration_role == "drone":
        score -= 0.55
    return score


def provider_capability_truth_for_manifest(manifest: ModelProviderManifest) -> ProviderCapabilityTruth:
    metadata = dict(manifest.metadata or {})
    runtime_config = dict(manifest.runtime_config or {})
    capabilities = {str(item).strip().lower() for item in list(manifest.capabilities or []) if str(item).strip()}
    health = get_provider_health(manifest.provider_id)
    availability_state = "blocked" if health.circuit_open else ("degraded" if health.consecutive_failures > 0 else "ready")
    orchestration_role = str(metadata.get("orchestration_role") or "").strip().lower()
    locality = "local" if manifest_is_local(manifest) else "remote"
    privacy_class = "local_private" if locality == "local" else "remote_provider"
    role_fit = orchestration_role or ("drone" if locality == "local" else "queen")
    estimated_footprint_gb = estimated_local_model_resident_gb(manifest.model_name) if locality == "local" else 0.0
    tool_support = tuple(
        str(item).strip()
        for item in list(metadata.get("tool_support") or [])
        if str(item).strip()
    ) or tuple(sorted(cap for cap in capabilities if cap in {"tool_calls", "structured_json", "web_search", "code_complex"}))
    truth = ProviderCapabilityTruth(
        provider_id=manifest.provider_id,
        model_id=manifest.model_name,
        role_fit=role_fit,
        context_window=max(0, int(metadata.get("context_window") or runtime_config.get("context_window") or 0)),
        tool_support=tool_support,
        structured_output_support="structured_json" in capabilities,
        tokens_per_second=float(metadata.get("tokens_per_second") or metadata.get("tps") or 0.0),
        ram_budget_gb=float(metadata.get("ram_budget_gb") or metadata.get("ram_gb") or estimated_footprint_gb),
        vram_budget_gb=float(metadata.get("vram_budget_gb") or metadata.get("vram_gb") or estimated_footprint_gb),
        quantization=str(metadata.get("quantization") or runtime_config.get("quantization") or "").strip(),
        locality=locality,
        privacy_class=privacy_class,
        queue_depth=max(0, int(metadata.get("queue_depth") or 0)),
        max_safe_concurrency=max(1, int(metadata.get("max_safe_concurrency") or 1)),
        availability_state=availability_state,
        circuit_open=health.circuit_open,
        last_error=health.last_error,
        # Same combined condition `_role_bonus` (role=="drone") already uses: a manifest the
        # OpenRouter catalog prices at zero AND that declares it can select a tool from a schema.
        # Computed here, once, from the real manifest -- the autopilot scoring in
        # `local_inference_autopilot.py` never sees the manifest or its raw `capabilities` list,
        # only this `ProviderCapabilityTruth`.
        is_verified_free_cloud_tool_capable=is_verified_free_cloud_manifest(manifest) and "tool_intent" in capabilities,
    )
    rejection = provider_hardware_fit_rejection(
        truth,
        force_cpu=str(runtime_config.get("num_gpu", "")).strip() == "0",
    )
    return ProviderCapabilityTruth(
        **{
            **truth.__dict__,
            "hardware_fit": None if rejection in {"hardware_capacity_unknown", "hardware_footprint_unknown"} else not bool(rejection),
            "hardware_fit_reason": rejection,
        }
    )


__all__ = [
    "ProviderCapabilityTruth",
    "ProviderRole",
    "ProviderRoutingPlan",
    "estimated_local_model_resident_gb",
    "provider_capability_truth_for_manifest",
    "provider_hardware_fit_rejection",
    "rank_provider_candidates",
    "resolve_provider_routing_plan",
    "resolve_provider_routing_plan_for_envelope",
]
