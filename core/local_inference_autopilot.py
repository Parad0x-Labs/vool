from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from core.hardware_tier import MachineProbe, probe_machine
from core.local_model_bundles import (
    model_active_parameter_billions,
    model_metadata,
    model_parameter_billions,
)
from core.model_lane_config import logical_lane
from core.provider_routing import (
    ProviderCapabilityTruth,
    ProviderRole,
    estimated_local_model_resident_gb,
    provider_hardware_fit_rejection,
)

AutopilotLane = Literal["tiny", "daily", "deep", "cloud", "human"]
AutopilotPhaseStatus = Literal["planned", "blocked"]
AutopilotFramework = Literal["ollama_mlx", "ollama_metal", "llama_cpp", "vulkan", "exllamav2", "unknown"]
RuntimeFlagValue = str | int | float | bool

_SECRET_RE = re.compile(
    r"(?i)\b("
    r"sk-[a-z0-9_-]{20,}|"
    r"sk-proj-[a-z0-9_-]{20,}|"
    r"AIza[a-z0-9_-]{20,}|"
    r"gh[pousr]_[a-z0-9_]{20,}|"
    r"xox[baprs]-[a-z0-9-]{20,}"
    r")\b"
)
_LONG_TOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9_-]{48,}\b)(?=.*[A-Z])(?=.*[a-z])(?=.*\d)[A-Za-z0-9_-]+\b")
_MAC_PATH_RE = re.compile(r"(?<![\w.-])/(?:Users|private|var|tmp)/[^\s'\"`<>)]{3,}")
_WIN_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\[^\s'\"`<>)]{3,}")
_HOME_PATH_RE = re.compile(r"(?<![\w.-])~/(?:[^\s'\"`<>)]{2,})")

_RISK_TERMS = {
    # explicit high-risk markers
    "api key",
    "credential",
    "secret",
    "high-risk",
    "high risk",
    "verifier required",
    "verification required",
    # destructive / irreversible ops
    "delete",
    "overwrite",
    "rm -rf",
    # engineering mutations that need review
    "failure mode",
    "patch",
    "refactor",
    "edit file",
    "write file",
    "production",
}

_EAGLE3_DRAFT_MAP = {
    "qwen3:8b": "AngelSlim/Qwen3-8B_eagle3",
    "qwen3:14b": "AngelSlim/Qwen3-14B_eagle3",
    "phi4:14b": "",
    # qwen2.5:7b is the Ollama bucket-B daily model; no verified EAGLE-3 draft exists for it, so
    # map it explicitly to "" (run plain, no draft) rather than leaving it absent — EAGLE-3 is a
    # llama.cpp-only speedup and is not active on the Ollama path anyway.
    "qwen2.5:7b": "",
    "llama3.3:70b": "AngelSlim/Llama-3.3-70B-Instruct_eagle3",
}
_EAGLE3_SPEEDUP_RANGE = (1.5, 2.5)
_ENTROPY_ESCALATION_THRESHOLD = 0.35
# Daily chat needs answer tokens, not theoretical reasoning capability. Live falsification on the
# reference host (2026-08-13): qwen3:4b ignored both Ollama `think:false` and Qwen `/no_think`,
# spending 17-39s to exhaust a 192-token budget with reasoning and no usable answer. After unloading
# it, qwen2.5:7b answered the same class of turn in 9.47s, `stop`, 94 answer tokens. This penalty is
# larger than the maximum throughput bonus (2.5) so an unproven thinking model cannot buy back the
# daily lane merely by reporting fast reasoning-token throughput. It is a score, not an exclusion:
# a machine with no alternative still has a model, and deep/explicit reasoning lanes are untouched.
_DAILY_UNCONTROLLED_THINKING_PENALTY = 4.0
_SUFFIX_DECODE_TASK_KINDS = frozenset(
    {
        "tool_intent",
        "tool_loop",
        "agentic_loop",
        "classification",
        "candidate_shard_generation",
        # trivial-pattern tasks — tiny models, suffix decode eligible
        "format",
        "extract",
        "tag",
    }
)

# Task kinds that route directly to the tiny lane regardless of text content
_TINY_TASK_KINDS = frozenset(
    {
        "classification",
        "tool_intent",
        "format",   # pure text reformatting; tiny model sufficient
        "extract",  # structured field pull; tiny model sufficient
        "tag",      # synonym for classify
    }
)

# Task kinds that always need deep lane (verifier path)
_DEEP_TASK_KINDS = frozenset(
    {
        "action_plan",
        "coding_help_complex",
        "reasoning",      # explicit multi-step reasoning
        "agent_planning", # multi-step plan building
    }
)


@dataclass(frozen=True)
class AutopilotPhase:
    name: str
    summary: str
    status: AutopilotPhaseStatus = "planned"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "summary": self.summary}


@dataclass(frozen=True)
class ContextCapsule:
    schema: str
    stable_prefix_hash: str
    task_summary: str
    compressed_prompt: str
    constraints: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    raw_chars: int = 0
    compressed_chars: int = 0
    omitted_private_items: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "stable_prefix_hash": self.stable_prefix_hash,
            "task_summary": self.task_summary,
            "compressed_prompt": self.compressed_prompt,
            "constraints": list(self.constraints),
            "evidence_refs": list(self.evidence_refs),
            "raw_chars": self.raw_chars,
            "compressed_chars": self.compressed_chars,
            "omitted_private_items": self.omitted_private_items,
        }


@dataclass(frozen=True)
class ResidencyAction:
    provider_id: str
    model_id: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PrefixCachePlan:
    stable_prefix_hash: str
    backend: str
    action: str
    supported: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_prefix_hash": self.stable_prefix_hash,
            "backend": self.backend,
            "action": self.action,
            "supported": self.supported,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class LocalInferenceAutopilotPlan:
    schema: str
    lane: AutopilotLane
    provider_role: ProviderRole
    task_kind: str
    output_mode: str
    selected_provider_id: str | None
    selected_model: str | None
    framework: AutopilotFramework
    runtime_flags: dict[str, RuntimeFlagValue]
    verifier_required: bool
    verifier_provider_id: str | None
    verifier_model: str | None
    entropy_escalation_threshold: float
    suffix_decode_eligible: bool
    # Whether THIS turn genuinely asked for a heavy/large model (an explicit size marker in the
    # request, or the caller's own autopilot_allow_heavy_model flag -- see
    # _explicit_heavy_requested). Distinct from `selected_model` merely PARSING as large: Auto
    # routing can pick a big model purely on ranking merit (e.g. a free cloud candidate winning
    # normal scoring) with no such request ever made. Consumers that want to apply an
    # intentional-heavy-request policy (like refusing to fall back to a smaller model after a
    # failure) must gate on this, not on selected_model's size alone.
    explicit_heavy: bool
    context: ContextCapsule
    phases: tuple[AutopilotPhase, ...]
    residency: tuple[ResidencyAction, ...]
    prefix_cache: PrefixCachePlan
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "lane": self.lane,
            "logical_lane": logical_lane(self.lane),
            "provider_role": self.provider_role,
            "task_kind": self.task_kind,
            "output_mode": self.output_mode,
            "selected_provider_id": self.selected_provider_id,
            "selected_model": self.selected_model,
            "framework": self.framework,
            "runtime_flags": dict(self.runtime_flags),
            "verifier_required": self.verifier_required,
            "verifier_provider_id": self.verifier_provider_id,
            "verifier_model": self.verifier_model,
            "entropy_escalation_threshold": self.entropy_escalation_threshold,
            "suffix_decode_eligible": self.suffix_decode_eligible,
            "explicit_heavy": self.explicit_heavy,
            "context": self.context.to_dict(),
            "phases": [phase.to_dict() for phase in self.phases],
            "residency": [action.to_dict() for action in self.residency],
            "prefix_cache": self.prefix_cache.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "warnings": list(self.warnings),
        }


def build_local_inference_autopilot_plan(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    provider_role: ProviderRole,
    capability_truth: tuple[ProviderCapabilityTruth, ...] | list[ProviderCapabilityTruth],
    source_context: dict[str, Any] | None = None,
    machine_probe: MachineProbe | None = None,
    free_vram_gb: float | None = None,
    eagle3_active: bool = False,
    default_model_tag: str | None = None,
    resident_model_tags: Collection[str] | None = None,
    user_demand_text: str | None = None,
) -> LocalInferenceAutopilotPlan:
    context = compile_context_capsule(user_text=user_text, source_context=source_context)
    capabilities = tuple(capability_truth or ())
    lane = _resolve_lane(
        user_text=user_text,
        task_kind=task_kind,
        output_mode=output_mode,
        source_context=source_context,
        local_available=any(item.locality == "local" and item.availability_state != "blocked" for item in capabilities),
        has_tiny_lane=_lane_model_available(capabilities, max_billions=4.0),
        has_deep_lane=_deep_lane_fits(capabilities, free_vram_gb=free_vram_gb),
    )
    # Demand predicates read the USER'S OWN WORDS. `user_text` can be a composed prompt (user
    # text + "Grounding observations ..." + a JSON dump the runtime authored -- see
    # `observation_prompt`), and numbers inside that scaffolding parse as model-size markers:
    # measured 2026-09-02, a benign post-restart turn's observations made `_largest_parameter_size_b`
    # read 12.0 on one turn and 89.0 on the next, flipping `explicit_heavy` and aborting routing
    # before the adapter (`explicit_heavy_lane_unavailable`) over a heavy request nobody made.
    # `user_demand_text` is the scaffolding-free text the caller holds; None keeps legacy
    # behavior for callers that never composed anything.
    demand_text = user_text if user_demand_text is None else str(user_demand_text)
    explicit_heavy = _explicit_heavy_requested(user_text=demand_text, source_context=source_context)
    requested_heavy_marker = _requested_heavy_marker(user_text=demand_text, source_context=source_context)
    selected = _select_primary_capability(
        capabilities,
        lane=lane,
        provider_role=provider_role,
        explicit_heavy=explicit_heavy,
        requested_heavy_marker=requested_heavy_marker,
        eagle3_active=eagle3_active,
        default_model_tag=default_model_tag,
    )
    risky = _needs_verifier(user_text=user_text, task_kind=task_kind, output_mode=output_mode, source_context=source_context)
    # `resident_model_tags` is measured by the CALLER (best-effort, [] on error) and defaults to
    # None = no residency preference, so the plan builder itself never probes the machine and
    # existing callers/tests rank exactly as before.
    verifier = _select_verifier_capability(
        capabilities, selected=selected, required=risky, resident_model_tags=resident_model_tags,
    )
    warnings = _build_warnings(capabilities=capabilities, selected=selected, explicit_heavy=explicit_heavy)
    evidence_refs = _evidence_refs(capabilities)
    residency = _build_residency(
        capabilities=capabilities,
        selected=selected,
        verifier=verifier,
        explicit_heavy=explicit_heavy,
    )
    framework, runtime_flags = _select_framework_and_flags(
        selected=selected,
        machine_probe=machine_probe,
        eagle3_active=eagle3_active,
    )
    prefix_cache = _build_prefix_cache_plan(context=context, selected=selected)
    suffix_decode_eligible = _suffix_decode_eligible(task_kind)
    phases = _build_phases(
        lane=lane,
        selected=selected,
        verifier_required=risky,
        verifier=verifier,
        task_kind=task_kind,
        output_mode=output_mode,
        framework=framework,
        runtime_flags=runtime_flags,
        suffix_decode_eligible=suffix_decode_eligible,
        machine_probe=machine_probe,
    )

    return LocalInferenceAutopilotPlan(
        schema="vool.local_inference_autopilot.v1",
        lane=lane,
        provider_role=provider_role,
        task_kind=str(task_kind or "unknown"),
        output_mode=str(output_mode or "plain_text"),
        selected_provider_id=selected.provider_id if selected else None,
        selected_model=selected.model_id if selected else None,
        framework=framework,
        runtime_flags=runtime_flags,
        verifier_required=risky,
        verifier_provider_id=verifier.provider_id if verifier else None,
        verifier_model=verifier.model_id if verifier else None,
        entropy_escalation_threshold=_ENTROPY_ESCALATION_THRESHOLD,
        suffix_decode_eligible=suffix_decode_eligible,
        explicit_heavy=explicit_heavy,
        context=context,
        phases=phases,
        residency=residency,
        prefix_cache=prefix_cache,
        evidence_refs=evidence_refs,
        warnings=warnings,
    )


def compile_context_capsule(
    *,
    user_text: str,
    source_context: dict[str, Any] | None = None,
    max_chars: int = 2400,
) -> ContextCapsule:
    context = dict(source_context or {})
    raw_parts: list[str] = [str(user_text or "")]
    stable_parts: list[str] = ["vool.local_inference_autopilot.v1"]
    constraints: list[str] = []
    evidence_refs: list[str] = []
    omitted_private = 0

    for key in ("repo_identity", "repo_map", "memory_capsule", "rules_summary", "tool_schema_hashes"):
        value = _context_value(context.get(key))
        if value:
            stable_parts.append(f"{key}:{value}")

    compressed_lines = [f"Task: {_clip(_sanitize_text(user_text)[0], 600)}"]
    for key, label, limit in (
        ("memory_capsule", "Memory", 520),
        ("repo_map", "Repo", 420),
        ("diff_summary", "Diff", 420),
        ("failing_tests", "Failing tests", 360),
        ("constraints", "Constraints", 360),
    ):
        value = _context_value(context.get(key))
        if not value:
            continue
        clean, redacted = _sanitize_text(value)
        omitted_private += redacted
        raw_parts.append(value)
        clipped = _clip(clean, limit)
        if key == "constraints":
            constraints.extend(_split_constraints(clipped))
        compressed_lines.append(f"{label}: {clipped}")

    for item in _as_tuple(context.get("evidence_refs")):
        clean, redacted = _sanitize_text(item)
        omitted_private += redacted
        if clean:
            evidence_refs.append(_clip(clean, 180))

    stable_text = "\n".join(stable_parts)
    prompt = "\n".join(line for line in compressed_lines if line.strip())
    if len(prompt) > max_chars:
        prompt = prompt[: max(0, max_chars - 1)].rstrip() + "…"
    clean_task, task_redacted = _sanitize_text(user_text)
    omitted_private += task_redacted

    return ContextCapsule(
        schema="vool.context_capsule.v1",
        stable_prefix_hash=hashlib.sha256(stable_text.encode("utf-8")).hexdigest()[:16],
        task_summary=_clip(clean_task, 220),
        compressed_prompt=prompt,
        constraints=tuple(dict.fromkeys(constraints)),
        evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        raw_chars=sum(len(part) for part in raw_parts),
        compressed_chars=len(prompt),
        omitted_private_items=omitted_private,
    )


def build_prefix_cache_plan(
    *,
    stable_prefix_hash: str,
    backend: str,
) -> PrefixCachePlan:
    clean_backend = str(backend or "").strip().lower()
    if clean_backend in {"llama.cpp", "llamacpp", "llama-cpp"}:
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="llama.cpp",
            action="slot_save_restore",
            supported=True,
            reason="llama.cpp can reuse stable prefixes through server slot cache save/restore.",
        )
    if clean_backend in {"mlx", "mlx-lm", "mlx_lm"}:
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="mlx-lm",
            action="cache_prompt",
            supported=True,
            reason="MLX-LM exposes prompt-cache primitives for stable prefixes.",
        )
    if clean_backend == "ollama":
        return PrefixCachePlan(
            stable_prefix_hash=stable_prefix_hash,
            backend="ollama",
            action="preload_keep_alive",
            supported=False,
            reason="Ollama can keep a model resident, but does not expose portable prefix-cache handles.",
        )
    return PrefixCachePlan(
        stable_prefix_hash=stable_prefix_hash,
        backend=clean_backend or "unknown",
        action="none",
        supported=False,
        reason="No backend-specific prefix-cache hook is configured for this lane.",
    )


# --- per-turn complexity routing (fast for trivial input, smart for heavy agentic builds) --------
# Greeting / smalltalk openers -> the tiny (fastest) lane. Trivial fires ONLY when the message is
# essentially just the greeting (opener + optional filler like "there"/"team"), so "hey summarize
# this file" is not swept onto the weakest model.
_TRIVIAL_CHAT_OPENERS = (
    "good morning", "good afternoon", "good evening", "good night", "good day",
    "how are you", "how's it going", "hows it going", "what's up", "whats up",
    "thank you so much", "thanks so much", "thanks a lot", "many thanks", "much appreciated",
    "appreciate it", "no problem", "you rock", "hi", "hii", "hey", "heya", "hiya", "hello",
    "helo", "hullo", "hallo", "heyo", "yo", "sup", "wassup", "howdy", "hola", "gm", "gn", "morning", "greetings",
    "thanks", "thank you", "thx", "ty", "ok", "okay", "kk", "cool", "nice", "great", "awesome",
    "lol", "haha", "hehe", "cheers", "welcome", "gg",
)
_SORTED_TRIVIAL_OPENERS = tuple(sorted(_TRIVIAL_CHAT_OPENERS, key=len, reverse=True))
_TRIVIAL_FILLERS = frozenset(
    {"there", "team", "all", "everyone", "folks", "guys", "friend", "buddy", "mate", "again"}
)
# After the leading greeting, only fillers or other single-word greetings may follow ("ok cool",
# "hey thanks") -- anything else means there is real task content, so it is NOT trivial.
_TRIVIAL_REST_TOKENS = _TRIVIAL_FILLERS | frozenset(o for o in _TRIVIAL_CHAT_OPENERS if " " not in o)

# A heavy build is an IMPERATIVE command to the assistant to produce a software artifact. Anchoring
# on the imperative -- a build verb heading the sentence, after an optional polite/intent prefix --
# is what separates a real build request from a question ("how do i build a website?"), a negation
# ("don't build ..."), a third-party statement ("my friend wants to build ..."), or tooling chatter
# ("npm run build"). Up to two modifier words may sit between the article and the deliverable
# ("a telegram bot", "a full-stack platform"). The deliverable list is curated software artifacts;
# a ".null" target also qualifies. This corrects the classifier under-labeling bare builds -> daily.
_BUILD_PREFIX = (
    r"(?:please|pls|plz|hey|hi|ok|okay|yo|can you|could you|would you|will you|help me|"
    r"let's|lets|i want you to|i'd like you to|i would like you to|i need you to|i want to|"
    r"i need to|i wanna)"
)
_BUILD_VERB = (
    r"(?:build|rebuild|create|make|develop|scaffold|architect|implement|code|write|design|"
    r"generate|deploy|spin up|stand up|put together)"
)
_BUILD_DELIVERABLE = (
    r"(?:websites?|web ?apps?|web ?sites?|webapps?|applications?|landing pages?|dashboards?|"
    r"platforms?|frontends?|front ?ends?|backends?|back ?ends?|full[ -]?stack|pipelines?|"
    r"chat ?bots?|bots?|games?|marketplaces?|online stores?|storefronts?|e-?commerce|crms?|"
    r"portfolios?|browser extensions?|extensions?|plugins?|mobile apps?|sites?|apps?|"
    r"smart contracts?|dapps?|mvps?|prototypes?)"
)
_HEAVY_BUILD_RE = re.compile(
    rf"^(?:{_BUILD_PREFIX}\s+)*{_BUILD_VERB}(?:\s+(?:me|us))?\s+"
    rf"(?:a|an|the|my|our|some|another|one)?\s*(?:[a-z0-9'.-]+\s+){{0,2}}{_BUILD_DELIVERABLE}\b"
)
_HEAVY_NULL_RE = re.compile(
    rf"^(?:{_BUILD_PREFIX}\s+)*{_BUILD_VERB}(?:\s+(?:me|us))?\s+\S*\.null\b"
)


def _message_complexity(user_text: str) -> str:
    """Coarse per-turn complexity from the raw message: "trivial" | "heavy" | "".

    Heuristic layer of the complexity router: a greeting/smalltalk opener routes to the fastest
    lane; an imperative agentic build ("build me a website", a ".null" target) or a long multi-step
    ask routes to the smartest lane; everything else returns "" so the existing task_kind/risk logic
    decides. Biased toward precision on "heavy" (imperative anchor) so questions, negations, and
    tooling chatter about building are NOT escalated to the slow model.
    """
    text = str(user_text or "").strip().lower()
    if not text:
        return ""
    # Keep apostrophes / '.' / '-' so "let's", ".null" and "full-stack" survive; space out the rest.
    haystack = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.'\- ]+", " ", text)).strip()
    words = haystack.split()
    is_heavy_build = bool(_HEAVY_BUILD_RE.match(haystack) or _HEAVY_NULL_RE.match(haystack))
    if not is_heavy_build and len(words) <= 6:
        for opener in _SORTED_TRIVIAL_OPENERS:
            if haystack == opener or haystack.startswith(f"{opener} "):
                if all(word in _TRIVIAL_REST_TOKENS for word in haystack[len(opener):].split()):
                    return "trivial"
                break
    if is_heavy_build:
        return "heavy"
    if len(words) >= 45:
        return "heavy"
    return ""


def _lane_model_available(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    min_billions: float | None = None,
    max_billions: float | None = None,
) -> bool:
    """True if a local, non-blocked model in the given size band exists (guards lane escalation)."""
    for item in capabilities:
        if item.locality != "local" or item.availability_state == "blocked" or item.hardware_fit is False:
            continue
        size_b = model_parameter_billions(item.model_id)
        if min_billions is not None and size_b < min_billions:
            continue
        if max_billions is not None and size_b > max_billions:
            continue
        return True
    return False


def _estimated_model_vram_gb(capability: ProviderCapabilityTruth) -> float:
    """Rough GPU footprint of a model in GB: the measured vram_budget when known, else a Q4-weights
    + small-context KV estimate (~0.6 GB per billion params + ~1.5 GB overhead)."""
    budget = float(getattr(capability, "vram_budget_gb", 0.0) or 0.0)
    if budget > 0.0:
        return budget
    return model_parameter_billions(capability.model_id) * 0.6 + 1.5


def _deep_lane_fits(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    free_vram_gb: float | None,
    min_billions: float = 13.0,
) -> bool:
    """True if a local, non-blocked >=min_billions model exists that fits free VRAM.

    Falls back to 'a big model exists' when free VRAM is unknown (None/<=0), so behavior is unchanged
    unless we can PROVE nothing big fits -- e.g. a 9 GB 14B model on an 8 GB card, where escalating a
    build to the deep lane would only load it (slowly) on CPU. In that case the caller keeps the task
    on the best-fitting model instead.
    """
    big = [
        item
        for item in capabilities
        if item.locality == "local"
        and item.availability_state != "blocked"
        and item.hardware_fit is not False
        and model_parameter_billions(item.model_id) >= min_billions
    ]
    if not big:
        return False
    if free_vram_gb is None or float(free_vram_gb) <= 0.0:
        return True
    return any(_estimated_model_vram_gb(item) <= float(free_vram_gb) for item in big)


def _resolve_lane(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    source_context: dict[str, Any] | None,
    local_available: bool,
    has_tiny_lane: bool = True,
    has_deep_lane: bool = True,
) -> AutopilotLane:
    if not local_available and bool((source_context or {}).get("allow_cloud")):
        return "cloud"
    if not local_available:
        return "human"
    clean_task_kind = str(task_kind or "").strip().lower()
    clean_output = str(output_mode or "").strip().lower()
    if clean_task_kind in _TINY_TASK_KINDS or clean_output == "tool_intent":
        return "tiny"
    complexity = _message_complexity(user_text)
    # Trivial small-talk -> fastest lane, but only when a tiny model actually exists to serve it.
    if complexity == "trivial" and has_tiny_lane:
        return "tiny"
    # A verifier-worthy task or a heavy agentic build wants the smartest (deep) lane -- but only when
    # a deep model actually fits this GPU. Otherwise the best-fitting model on the daily lane serves
    # it, so nothing routes to a model too big to load (e.g. a 14B on an 8 GB card).
    wants_deep = complexity == "heavy" or _needs_verifier(
        user_text=user_text,
        task_kind=task_kind,
        output_mode=output_mode,
        source_context=source_context,
    )
    if wants_deep:
        return "deep" if has_deep_lane else "daily"
    return "daily"


def _needs_verifier(
    *,
    user_text: str,
    task_kind: str,
    output_mode: str,
    source_context: dict[str, Any] | None,
) -> bool:
    clean_task_kind = str(task_kind or "").strip().lower()
    clean_output = str(output_mode or "").strip().lower()
    if clean_task_kind in _DEEP_TASK_KINDS or clean_output == "action_plan":
        return True
    if bool((source_context or {}).get("requires_verifier")):
        return True
    lowered = str(user_text or "").lower()
    return any(term in lowered for term in _RISK_TERMS)


def _select_primary_capability(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    lane: AutopilotLane,
    provider_role: ProviderRole,
    explicit_heavy: bool,
    requested_heavy_marker: str | None,
    eagle3_active: bool = False,
    default_model_tag: str | None = None,
) -> ProviderCapabilityTruth | None:
    if explicit_heavy:
        viable = [
            item
            for item in capabilities
            if item.availability_state != "blocked"
            and item.hardware_fit is not False
            and model_parameter_billions(item.model_id) >= 24.0
            and not _is_fast_nothink_default(item.model_id)
        ]
        if requested_heavy_marker:
            viable = [item for item in viable if requested_heavy_marker in str(item.model_id or "").strip().lower()]
    else:
        viable = [
            item
            for item in capabilities
            if item.availability_state != "blocked"
            and item.hardware_fit is not False
            and (
                # This size cap exists to protect local VRAM: a >=24B model does not fit
                # comfortably on a modest local GPU/CPU, so the tiny/daily lanes exclude it by
                # default. A remote API candidate loads nothing onto this machine's hardware --
                # applying a VRAM-fit filter to it excludes a viable, zero-local-footprint
                # candidate (e.g. a free OpenRouter model) using a rule that has nothing to do
                # with it. Only local-locality candidates are subject to the size cap.
                item.locality != "local"
                or model_parameter_billions(item.model_id) < 24.0
                or _is_fast_nothink_default(item.model_id)
            )
        ]
    if not viable:
        return None
    daily_nonthinking_alternative = lane == "daily" and any(
        _is_reliable_daily_nonthinking_model(item) for item in viable
    )
    ranked = sorted(
        viable,
        key=lambda item: (
            _primary_score(
                item,
                lane=lane,
                provider_role=provider_role,
                explicit_heavy=explicit_heavy,
                eagle3_active=eagle3_active,
                default_model_tag=default_model_tag,
                daily_nonthinking_alternative=daily_nonthinking_alternative,
            ),
            item.provider_id,
        ),
        reverse=True,
    )
    return ranked[0]


def prioritize_daily_residency_capabilities(
    capabilities: tuple[ProviderCapabilityTruth, ...] | list[ProviderCapabilityTruth],
    *,
    default_model_tag: str | None = None,
) -> tuple[ProviderCapabilityTruth, ...]:
    """Put the candidate ordinary Auto would keep resident first, preserving all fallbacks.

    Pre-classification runs before task/lane resolution, but it shares Ollama residency with the
    answer. Calling the first unpaid manifest can load an uncontrolled thinking model, after which
    the correctly selected non-thinking daily model is refused by the memory admission gate. This
    helper reuses the daily lane's exact viability and scoring seam so auxiliary planning and the
    likely ordinary answer agree on one resident model. It is intentionally local-only: remote
    candidate ordering and egress policy remain owned by provider routing.

    No candidate is removed. A sole thinking model stays first, and deep/explicit selection never
    calls this helper.
    """

    ordered = tuple(capabilities or ())
    local_viable = tuple(
        item
        for item in ordered
        if item.locality == "local"
        and item.availability_state != "blocked"
        and item.hardware_fit is not False
        and (
            model_parameter_billions(item.model_id) < 24.0
            or _is_fast_nothink_default(item.model_id)
        )
    )
    if not local_viable:
        return ordered
    selected = _select_primary_capability(
        local_viable,
        lane="daily",
        provider_role="auto",
        explicit_heavy=False,
        requested_heavy_marker=None,
        default_model_tag=default_model_tag,
    )
    if selected is None:
        return ordered
    return (selected, *(item for item in ordered if item.provider_id != selected.provider_id))


def select_daily_residency_model_tag(
    model_tags: tuple[str, ...] | list[str],
    *,
    default_model_tag: str | None = None,
    resident_footprint_gb: Mapping[str, float] | None = None,
    probe: MachineProbe | None = None,
) -> str:
    """Select the installed tag ordinary daily Auto is most likely to keep resident.

    Boot runs before provider benchmark truth is hydrated, but it still knows the exact Ollama
    inventory and (from ``/api/tags``) the resident artifact footprint. Represent those installed
    tags with equal, unknown measurements, apply the router's existing hardware-fit gate, and reuse
    daily routing instead of growing a second model-ranking heuristic in the web runtime. This is
    load-bearing for MoE models: active parameters describe compute, not the 19 GB of weights that
    must remain resident. The helper never invents or downloads a candidate: an empty inventory
    returns ``""`` and the caller retains its configured default.
    """

    seen: set[str] = set()
    candidates: list[ProviderCapabilityTruth] = []
    footprint_by_tag = {
        str(name or "").strip().lower(): max(0.0, float(size_gb or 0.0))
        for name, size_gb in dict(resident_footprint_gb or {}).items()
        if str(name or "").strip()
    }
    for raw_tag in model_tags or ():
        model_tag = str(raw_tag or "").strip()
        key = model_tag.lower()
        if not model_tag or key in seen:
            continue
        seen.add(key)
        metadata = model_metadata(model_tag)
        artifact_size_gb = footprint_by_tag.get(key, 0.0)
        footprint_gb = (
            estimated_local_model_resident_gb(model_tag, artifact_size_gb=artifact_size_gb)
            if probe is not None
            else 0.0
        )
        capability = ProviderCapabilityTruth(
            provider_id=f"ollama-local:{model_tag}",
            model_id=model_tag,
            role_fit=str(metadata.get("role") or "general").strip() or "general",
            context_window=int(metadata.get("max_context") or 4096),
            tool_support=("structured_json",),
            structured_output_support=True,
            tokens_per_second=0.0,
            ram_budget_gb=footprint_gb,
            vram_budget_gb=footprint_gb,
            quantization=str(metadata.get("quantization") or ""),
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=1,
            availability_state="ready",
            measurement_source="installed_inventory",
        )
        if probe is not None:
            rejection = provider_hardware_fit_rejection(capability, probe=probe)
            capability = ProviderCapabilityTruth(
                **{
                    **capability.__dict__,
                    "hardware_fit": (
                        None
                        if rejection in {"hardware_capacity_unknown", "hardware_footprint_unknown"}
                        else not bool(rejection)
                    ),
                    "hardware_fit_reason": rejection,
                }
            )
        candidates.append(capability)
    if not candidates:
        return ""
    ordered = prioritize_daily_residency_capabilities(
        candidates,
        default_model_tag=default_model_tag,
    )
    return str(ordered[0].model_id or "").strip() if ordered else ""


def _select_verifier_capability(
    capabilities: tuple[ProviderCapabilityTruth, ...],
    *,
    selected: ProviderCapabilityTruth | None,
    required: bool,
    resident_model_tags: Collection[str] | None = None,
) -> ProviderCapabilityTruth | None:
    if not required:
        return None
    resident = _normalized_model_tag_set(resident_model_tags)
    viable = [
        item
        for item in capabilities
        if item.availability_state != "blocked"
        and item.hardware_fit is not False
        and (model_parameter_billions(item.model_id) < 24.0 or _is_fast_nothink_default(item.model_id))
        and (
            selected is None
            or (
                item.provider_id != selected.provider_id
                and str(item.model_id or "").strip().lower() != str(selected.model_id or "").strip().lower()
            )
        )
    ]
    if not viable:
        return None
    ranked = sorted(
        viable,
        key=lambda item: (
            _verifier_score(item, selected=selected, resident_model_tags=resident),
            item.provider_id,
        ),
        reverse=True,
    )
    return ranked[0]


def _normalized_model_tag_set(model_tags: Collection[str] | None) -> frozenset[str]:
    return frozenset(
        str(tag or "").strip().lower() for tag in (model_tags or ()) if str(tag or "").strip()
    )


def _primary_score(
    capability: ProviderCapabilityTruth,
    *,
    lane: AutopilotLane,
    provider_role: ProviderRole,
    explicit_heavy: bool,
    eagle3_active: bool = False,
    default_model_tag: str | None = None,
    daily_nonthinking_alternative: bool = False,
) -> float:
    size_b = model_parameter_billions(capability.model_id)
    fast_nothink_default = _is_fast_nothink_default(capability.model_id)
    score = 0.0
    if capability.locality == "local":
        score += 2.0
    else:
        score += 0.4
    if capability.tokens_per_second > 0:
        # Cap at 50 tok/s (divisor 20) — better differentiates 20→40 tok/s range
        score += min(2.5, capability.tokens_per_second / 20.0)
    else:
        score -= 0.25
    if capability.availability_state == "degraded":
        score -= 1.0
    score -= min(2.0, capability.queue_depth / max(1, capability.max_safe_concurrency))
    if provider_role == "queen" and lane != "daily":
        score += 2.0 if capability.role_fit == "queen" else -0.5
    elif provider_role == "drone":
        score += 1.0 if capability.role_fit == "drone" else -1.0

    if provider_role == "drone" and capability.locality != "local" and capability.is_verified_free_cloud_tool_capable:
        # Live re-check (2026-08-04, final-skeptic pass) found the `_role_bonus` free-cloud
        # priority fix in `core/provider_routing.py` provably inert for the actual tool_intent
        # path: `_resolve_lane` routes every tool_intent turn to lane=="tiny", THIS function picks
        # the manifest (not `_role_bonus`), and `_can_prioritize_autopilot_selection` in
        # `memory_first_router.py` unconditionally promotes that pick whenever
        # `allow_paid_fallback` is False -- which it always is for role=="drone"
        # (`_resolve_allow_paid_fallback`). Measured: a local drone-tagged model scored ~5.15 here
        # against ~-2.85 for a verified-free, tool_intent-capable remote manifest under the SAME
        # inputs used to verify `_role_bonus` -- local still won every real unpinned drive despite
        # the `_role_bonus` fix already landing. This mirrors that fix's exact condition
        # (`is_verified_free_cloud_manifest(manifest) and "tool_intent" in capabilities`, computed
        # once in `provider_capability_truth_for_manifest` since this function only ever sees a
        # `ProviderCapabilityTruth`, never the manifest) at the layer that actually decides the
        # live pick. Sized to clear the locality (+2.0 vs +0.4), role-fit ("queen" vs "drone", a
        # 2.0pt swing) and tiny-lane size-band gaps above with room to spare, so free-cloud wins
        # the AUTO default here the same way it now does in `_role_bonus`, instead of losing to a
        # second scoring path that never learned about the operator's stated priority order.
        score += 13.0

    if lane == "tiny":
        if size_b <= 4.0:
            score += 3.0
        elif size_b <= 10.0:
            score += 0.6
        elif size_b >= 13.0:
            score -= 2.0
        if capability.role_fit == "drone":
            score += 0.8
    elif lane == "daily":
        if fast_nothink_default:
            score += 3.4
        elif 4.0 <= size_b <= 10.0:
            score += 2.6
        elif size_b < 4.0:
            score -= 0.8
        elif 13.0 <= size_b <= 16.0:
            score -= 0.6
        else:
            score -= 1.8
        # Break near-ties toward the installed baseline model so the reported "default" model actually
        # serves normal turns (e.g. qwen2.5:7b, not the same-band qwen3:8b that only won on provider_id
        # tiebreak), keeping the per-reply footer consistent with the default. Small (+0.5): it does not
        # rescue a degraded model (-1.0) or beat the nothink bonus (+3.4); it only tips near-equal
        # candidates. The baseline is the model fitted to this host, so preferring it -- even over a
        # larger model on a low-end box -- is the intended outcome.
        if default_model_tag and str(capability.model_id or "").strip().lower() == str(default_model_tag).strip().lower():
            score += 0.5
        if daily_nonthinking_alternative and _is_uncontrolled_local_thinking_model(capability):
            score -= _DAILY_UNCONTROLLED_THINKING_PENALTY
    elif lane == "deep":
        if fast_nothink_default:
            score += 3.2  # MoE nothink: preferred deep fallback when llama.cpp unavailable
        elif 13.0 <= size_b <= 16.0:
            score += 2.8
        elif 8.0 <= size_b < 13.0:
            score += 1.0
        elif 18.0 <= size_b < 24.0:
            score += 0.4
        elif size_b >= 24.0:
            score += 0.2 if explicit_heavy else -2.8
        if capability.role_fit == "queen":
            score += 0.8
        if "code_complex" in {item.lower() for item in capability.tool_support}:
            score += 0.35
        if _is_llamacpp_specialist(capability) and "code_complex" in {item.lower() for item in capability.tool_support}:
            score += 4.4
        if eagle3_active and _is_llamacpp_specialist(capability):
            score += 1.5  # EAGLE-3 confirmed running: 1.4-1.9x speedup bonus
    elif lane == "cloud":
        score += 1.5 if capability.locality == "remote" else 0.2
    elif lane == "human":
        score -= 4.0

    if size_b >= 30.0 and not explicit_heavy and not fast_nothink_default:
        score -= 3.0
    return score


def _verifier_score(
    capability: ProviderCapabilityTruth,
    *,
    selected: ProviderCapabilityTruth | None,
    resident_model_tags: frozenset[str] = frozenset(),
) -> float:
    size_b = model_parameter_billions(capability.model_id)
    score = 0.0
    # A verifier that is ALREADY IN MEMORY verifies with zero new RAM. On a RAM-starved box the
    # size-ranked pick loses to the resource governor's load gate every time (measured live
    # 2026-08-14: primary qwen3:14b resident, picker chose qwen3:8b, the gate refused its ~6.7 GB
    # load with ~5.2 GB free, review reported independent_failed -- while qwen3:4b sat resident
    # from the same turn's classifier call). The bonus outweighs every size/speed bonus a
    # non-resident candidate can accumulate, and it is RELATIVE: with nothing resident, or
    # everything resident, the ranking is exactly what it was before. The distinct-from-primary
    # rule is enforced by the viable filter in _select_verifier_capability, not here.
    if resident_model_tags and str(capability.model_id or "").strip().lower() in resident_model_tags:
        score += 6.0
    if 13.0 <= size_b <= 16.0:
        score += 3.2
    elif 8.0 <= size_b < 13.0:
        score += 1.0
    elif size_b >= 24.0:
        score -= 1.6
    if capability.role_fit == "queen":
        score += 0.8
    if capability.locality == "local":
        score += 1.0
    if "code_complex" in {item.lower() for item in capability.tool_support}:
        score += 0.35
    if selected is not None and capability.provider_id == selected.provider_id:
        score -= 0.45
    if capability.tokens_per_second > 0:
        score += min(1.2, capability.tokens_per_second / 30.0)
    if selected is not None and _is_llamacpp_specialist(selected):
        if 8.0 <= size_b < 13.0:
            score += 2.4
        elif 13.0 <= size_b <= 16.0:
            score -= 1.0
    return score


def _is_llamacpp_specialist(capability: ProviderCapabilityTruth) -> bool:
    provider_id = str(capability.provider_id or "").strip().lower()
    return provider_id.startswith("llamacpp-local:")


def _build_residency(
    *,
    capabilities: tuple[ProviderCapabilityTruth, ...],
    selected: ProviderCapabilityTruth | None,
    verifier: ProviderCapabilityTruth | None,
    explicit_heavy: bool,
) -> tuple[ResidencyAction, ...]:
    actions: list[ResidencyAction] = []
    if selected is not None:
        size_b = model_parameter_billions(selected.model_id)
        if size_b <= 10.0:
            action = "keep_hot"
            reason = "daily lane stays resident to reduce first-token delay"
        elif size_b <= 16.0:
            action = "load_on_demand"
            reason = "deep lane is loaded only when needed"
        elif explicit_heavy:
            action = "load_explicit_only"
            reason = "oversized model was explicitly requested"
        else:
            action = "blocked_by_default"
            reason = "oversized local model is too slow for default UX"
        actions.append(ResidencyAction(selected.provider_id, selected.model_id, action, reason))

    if verifier is not None and (selected is None or verifier.provider_id != selected.provider_id):
        actions.append(
            ResidencyAction(
                verifier.provider_id,
                verifier.model_id,
                "load_for_verification",
                "verifier lane is not kept hot unless a risky output needs review",
            )
        )

    for capability in capabilities:
        size_b = model_parameter_billions(capability.model_id)
        if size_b >= 24.0 and (not explicit_heavy or capability.availability_state == "blocked"):
            reason = (
                "explicit heavy lane is blocked or unhealthy"
                if explicit_heavy and capability.availability_state == "blocked"
                else "24B+ lanes require explicit operator request or measured proof"
            )
            actions.append(
                ResidencyAction(
                    capability.provider_id,
                    capability.model_id,
                    "refuse_default",
                    reason,
                )
            )
    return tuple(_dedupe_residency(actions))


def _build_prefix_cache_plan(
    *,
    context: ContextCapsule,
    selected: ProviderCapabilityTruth | None,
) -> PrefixCachePlan:
    if selected is None:
        return build_prefix_cache_plan(stable_prefix_hash=context.stable_prefix_hash, backend="")
    provider_id = selected.provider_id.lower()
    if "llamacpp" in provider_id or "llama.cpp" in provider_id:
        backend = "llama.cpp"
    elif "mlx" in provider_id:
        backend = "mlx-lm"
    elif "ollama" in provider_id:
        backend = "ollama"
    else:
        backend = provider_id.partition(":")[0]
    return build_prefix_cache_plan(stable_prefix_hash=context.stable_prefix_hash, backend=backend)


def _select_framework_and_flags(
    *,
    selected: ProviderCapabilityTruth | None,
    machine_probe: MachineProbe | None,
    eagle3_active: bool = False,
) -> tuple[AutopilotFramework, dict[str, RuntimeFlagValue]]:
    if selected is None:
        return "unknown", {}

    probe = machine_probe or probe_machine()
    system = platform.system().lower()
    accelerator = str(probe.accelerator or "").strip().lower()
    ram_gb = float(probe.ram_gb or 0.0)
    vram_gb = float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0
    model_size_b = model_parameter_billions(selected.model_id)

    if system == "darwin" and accelerator == "mps":
        if ram_gb >= 32.0:
            framework: AutopilotFramework = "ollama_mlx"
            flags: dict[str, RuntimeFlagValue] = {"OLLAMA_MLX": "1", "num_gpu": 999}
        else:
            framework = "ollama_metal"
            flags = {"num_gpu": 999}
    elif accelerator == "cuda" and vram_gb >= 16.0 and model_size_b >= 60.0:
        framework = "exllamav2"
        flags = {
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
            "batch_size": 2048,
            "ubatch_size": 2048,
        }
    elif accelerator == "cuda" and vram_gb >= 16.0:
        framework = "llama_cpp"
        flags = {
            "flash_attn": True,
            "cache_type_k": "q8_0",
            "cache_type_v": "q8_0",
            "batch_size": 2048,
            "ubatch_size": 2048,
        }
    elif accelerator == "vulkan":
        # Inert lane for non-Apple consumer iGPUs (AMD/Intel) reachable via llama.cpp's Vulkan backend.
        # Selected only when a real "vulkan" accelerator is detected (never on CUDA/mps/CPU boxes), so it
        # is reachability-tested but unbenchmarked until measured tok/s on real hardware (issue #21).
        framework = "vulkan"
        flags = {
            "flash_attn": True,
            "ngl": 999,  # offload all layers to the Vulkan device
            "batch_size": 2048,
            "ubatch_size": 2048,
        }
    else:
        framework = "llama_cpp"
        flags = {
            "flash_attn": True,
            "batch_size": 2048,
            "ubatch_size": 2048,
        }

    if _is_moe_model(selected.model_id):
        flags = {**flags, "ngl": 999, "fit_target": 2048}
    if eagle3_active and _is_llamacpp_specialist(selected):
        flags = {
            **flags,
            "speculative": "draft-eagle3",
            "spec_draft_n_max": 8,
            "spec_draft_p_min": 0.5,
        }
    return framework, flags


def _build_phases(
    *,
    lane: AutopilotLane,
    selected: ProviderCapabilityTruth | None,
    verifier_required: bool,
    verifier: ProviderCapabilityTruth | None,
    task_kind: str,
    output_mode: str,
    framework: AutopilotFramework,
    runtime_flags: dict[str, RuntimeFlagValue],
    suffix_decode_eligible: bool,
    machine_probe: MachineProbe | None,
) -> tuple[AutopilotPhase, ...]:
    selected_label = selected.provider_id if selected else "no provider"
    phases = [
        AutopilotPhase("route", f"Classify request into {lane} lane."),
        AutopilotPhase("retrieve", "Collect only relevant memory, repo, diff, and test facts."),
        AutopilotPhase("compress", "Build bounded context capsule instead of raw context dump."),
        AutopilotPhase("framework", _framework_summary(framework, runtime_flags), "planned" if selected else "blocked"),
        AutopilotPhase("preload", f"Prepare {selected_label}.", "planned" if selected else "blocked"),
        AutopilotPhase("generate", f"Generate with {selected_label}.", "planned" if selected else "blocked"),
    ]
    if selected is not None:
        sysctl_warning = _apple_wired_memory_phase(machine_probe=machine_probe)
        if sysctl_warning is not None:
            phases.append(sysctl_warning)
        if suffix_decode_eligible:
            phases.append(
                AutopilotPhase(
                    "suffix_decoding",
                    "Task is repetitive/agentic; prefer suffix-tree decoding over draft-model speculation when the backend supports it.",
                )
            )
        else:
            eagle_phase = _eagle3_phase(selected.model_id)
            if eagle_phase is not None:
                phases.append(eagle_phase)
    if verifier_required:
        verifier_label = verifier.provider_id if verifier else "no verifier"
        phases.append(
            AutopilotPhase(
                "verify",
                f"Review risky output with {verifier_label}.",
                "planned" if verifier else "blocked",
            )
        )
    if str(task_kind).lower() in {"action_plan", "coding_help_complex"} or str(output_mode).lower() == "action_plan":
        phases.extend(
            [
                AutopilotPhase("test", "Run focused proof for generated changes."),
                AutopilotPhase("repair", "Repair failures and re-run the current proof set."),
            ]
        )
    return tuple(phases)


def _framework_summary(framework: AutopilotFramework, runtime_flags: dict[str, RuntimeFlagValue]) -> str:
    if framework == "unknown":
        return "No inference framework can be selected without a provider lane."
    if not runtime_flags:
        return f"Use {framework} with default runtime flags."
    flag_summary = ", ".join(f"{key}={value}" for key, value in sorted(runtime_flags.items()))
    return f"Use {framework} with {flag_summary}."


def _apple_wired_memory_phase(*, machine_probe: MachineProbe | None) -> AutopilotPhase | None:
    probe = machine_probe or probe_machine()
    if platform.system().lower() != "darwin":
        return None
    if str(probe.accelerator or "").strip().lower() != "mps":
        return None
    current = _read_iogpu_wired_limit_mb()
    if current is None:
        return None
    recommended = int(float(probe.ram_gb or 0.0) * 1024.0 * 0.85)
    if recommended <= 0 or current >= recommended:
        return None
    return AutopilotPhase(
        name="sysctl_warning",
        summary=(
            f"iogpu.wired_limit_mb is low ({current}). "
            f"Recommend: sudo sysctl iogpu.wired_limit_mb={recommended}"
        ),
        status="blocked",
    )


def _read_iogpu_wired_limit_mb() -> int | None:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "iogpu.wired_limit_mb"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    raw = str(result.stdout or "").strip().splitlines()[0:1]
    if not raw:
        return None
    try:
        return int(float(raw[0].strip()))
    except ValueError:
        return None


def _eagle3_phase(model_id: str) -> AutopilotPhase | None:
    if _is_moe_model(model_id):
        return None
    draft_repo = _EAGLE3_DRAFT_MAP.get(str(model_id or "").strip().lower())
    if not draft_repo:
        return None
    return AutopilotPhase(
        name="eagle_candidate",
        summary=(
            f"EAGLE-3 draft available: {draft_repo}. "
            f"Expected {_EAGLE3_SPEEDUP_RANGE[0]}-{_EAGLE3_SPEEDUP_RANGE[1]}x speedup only after a backend proves a real EAGLE draft lane."
        ),
    )


def _suffix_decode_eligible(task_kind: str) -> bool:
    return str(task_kind or "").strip().lower() in _SUFFIX_DECODE_TASK_KINDS


def _is_moe_model(model_id: str) -> bool:
    clean = str(model_id or "").strip().lower()
    metadata = model_metadata(clean)
    architecture = str(metadata.get("architecture") or "").strip().lower()
    if architecture in {"moe", "hybrid_moe"}:
        return True
    if re.search(r"-a\d+(?:\.\d+)?b", clean):
        return True
    return model_active_parameter_billions(clean) != model_parameter_billions(clean)


def _is_fast_nothink_default(model_id: str) -> bool:
    clean = str(model_id or "").strip().lower()
    if clean == "vool-qwen3-30b-a3b:nothink":
        return True
    # Any Ollama custom model with :nothink variant (user-built Modelfiles)
    if ":nothink" in clean:
        return True
    # Metadata flag for GGUF / other backends with thinking disabled at launch
    return bool(model_metadata(clean).get("thinking_disabled", False))


def _is_uncontrolled_local_thinking_model(capability: ProviderCapabilityTruth) -> bool:
    """True when a daily local candidate reasons but has no proven no-think artifact/control.

    A tag ending in ``:nothink`` or bundle metadata ``thinking_disabled`` is a concrete artifact
    property and remains eligible for the existing daily bonus. A generic qwen3/nemotron/R1 tag is
    only a capability claim; runtime flags and prompt switches are not accepted as proof after the
    live qwen3 falsification above.
    """

    if capability.locality != "local" or _is_fast_nothink_default(capability.model_id):
        return False
    from core.agent_runtime.builder.controller import is_thinking_capable_model

    return is_thinking_capable_model(capability.model_id)


def _is_reliable_daily_nonthinking_model(capability: ProviderCapabilityTruth) -> bool:
    """Whether this candidate is a real daily replacement, not merely a tiny utility model."""

    if capability.locality != "local" or _is_uncontrolled_local_thinking_model(capability):
        return False
    size_b = model_parameter_billions(capability.model_id)
    return _is_fast_nothink_default(capability.model_id) or 4.0 <= size_b < 24.0


def _build_warnings(
    *,
    capabilities: tuple[ProviderCapabilityTruth, ...],
    selected: ProviderCapabilityTruth | None,
    explicit_heavy: bool,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if selected is None:
        warnings.append("no_local_or_provider_lane_available")
        if explicit_heavy:
            warnings.append("explicit_heavy_lane_unavailable")
    if capabilities and all(item.tokens_per_second <= 0 for item in capabilities):
        warnings.append("routing_has_no_measured_tokens_per_second")
    for capability in capabilities:
        size_b = model_parameter_billions(capability.model_id)
        if size_b >= 24.0 and not explicit_heavy and not _is_fast_nothink_default(capability.model_id):
            warnings.append(f"{capability.provider_id}:oversized_lane_not_default")
        if capability.availability_state == "degraded":
            warnings.append(f"{capability.provider_id}:degraded")
    return tuple(dict.fromkeys(warnings))


def _evidence_refs(capabilities: tuple[ProviderCapabilityTruth, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for capability in capabilities:
        if capability.tokens_per_second > 0:
            refs.append(f"measured:{capability.provider_id}:tok_s={capability.tokens_per_second:.2f}")
        else:
            refs.append(f"manifest:{capability.provider_id}:unmeasured")
    return tuple(refs)


#: A parameter count stated in billions, in the forms model names actually use: "32b", "550b",
#: "a55b" (active params of an MoE), "3.1-405b", "70 B".
_PARAMETER_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

#: The mixture-of-experts form, "8x22b" = eight experts of 22B. Read as its product, because the
#: bare number in that name is the size of ONE expert: parsing "mixtral-8x22b" as 22B put a ~176B
#: model under a 24B floor.
_MOE_SIZE_RE = re.compile(r"(?<![\d.])(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

#: The floor that decides "heavy". 24 is not a new judgement -- it is the smallest member of the
#: hand-written marker list this replaced, so every model that used to qualify still does.
_HEAVY_PARAMETER_FLOOR_B = 24.0


#: Runtime identifiers that are never size markers: UUIDs, and hex runs of eight or more characters that
#: mix letters and digits (request ids, turn ids, evidence-note ids, hashes). Measured on e4f7ad2a: the
#: composed prompt named `evnote-e50180cf65f5f48b879b`, the size regex read `879b`, and a comparison turn
#: was blocked as an explicit 879B demand nobody made. Model names keep their short size tokens
#: ("405b", "550b-a55b", "8x22b") because those never form an 8+ character mixed hex run.
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_HEX_IDENTIFIER_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", re.IGNORECASE)
#: Blockchain identifiers are never size markers either. Measured on the correction-2 cumulative (served wallet
#: matrix, 2026-09-15): the request "Send 0.002 ETH on ethereum to 0xcb67…230e82b …" sized as an 82B demand because
#: the `0x` prefix keeps the hex run out of `_HEX_IDENTIFIER_RE` (no word boundary after the `x`) while the address's
#: tail `82b` closes the token. A `0x`-prefixed hex string of any length (an EVM address, a transaction hash, calldata)
#: and a long base58 run (a Solana address or signature: 32+ characters of the base58 alphabet, no 0/O/I/l) are
#: removed before sizing. Model names keep their short size tokens ("405b", "8x22b", "qwen3:32b"): none of them is a
#: 0x-prefixed hex string or a 32+ character base58 run.
_HEX_0X_RE = re.compile(r"(?<![0-9a-z])0x[0-9a-f]{4,}", re.IGNORECASE)
_BASE58_RUN_RE = re.compile(r"(?<![0-9A-Za-z])[1-9A-HJ-NP-Za-km-z]{32,}(?![0-9A-Za-z])")


def _without_runtime_identifiers(text: str) -> str:
    cleaned = _HEX_0X_RE.sub(" ", str(text or ""))
    cleaned = _BASE58_RUN_RE.sub(" ", cleaned)
    return _HEX_IDENTIFIER_RE.sub(" ", _UUID_RE.sub(" ", cleaned))


def _largest_parameter_size_b(*sources: str) -> float:
    """The biggest parameter count named anywhere in these strings, in billions, or 0.0.

    An MoE name carries two ("550b" total and "a55b" active); the larger is what the operator
    picked, and either way both clear the floor here. Runtime identifiers are removed first --
    see `_without_runtime_identifiers`.
    """

    best = 0.0
    for source in sources:
        text = _without_runtime_identifiers(str(source or ""))
        for match in _MOE_SIZE_RE.finditer(text):
            try:
                best = max(best, float(match.group(1)) * float(match.group(2)))
            except (TypeError, ValueError):
                continue
        for match in _PARAMETER_SIZE_RE.finditer(text):
            try:
                best = max(best, float(match.group(1)))
            except (TypeError, ValueError):
                continue
    return best


def _explicit_heavy_requested(*, user_text: str, source_context: dict[str, Any] | None) -> bool:
    """Whether THIS turn genuinely asked for a heavy model -- see `explicit_heavy`'s field comment.

    Heaviness used to be a hand-written list of size strings: ("24b", "30b", "32b", "35b", "72b",
    "heavy"). Measured on c6eed761, that list said a local `qwen3:32b` was an explicit heavy
    request while `nemotron-3-ultra-550b-a55b:free`, `llama-3.1-405b`, `deepseek-v3-671b` and
    `mixtral-8x22b` were not -- so a pinned 550B model did NOT earn the policy its consumers exist
    to apply, and could be replaced by a smaller fallback after a failure while a 32B one could not.
    Backwards, and a list that has to be extended every time a model is released.

    Parse the size instead. The floor stays at 24B, the smallest member of the old list, so nothing
    that qualified before stops qualifying.

    Known limit, stated rather than papered over: a frontier model whose name carries no parameter
    count ("claude-opus-5", "gpt-5") is still not recognised as heavy by size, because its name says
    nothing about size. Those need a capability/cost signal rather than a string, which is a
    separate change -- the `autopilot_allow_heavy_model` flag remains the explicit way to say so.
    """

    if bool((source_context or {}).get("autopilot_allow_heavy_model")):
        return True
    requested = str((source_context or {}).get("requested_model") or "")
    text = str(user_text or "")
    if "heavy" in requested.lower() or "heavy" in text.lower():
        return True
    # sized in the text's own case: the size regexes ignore case, and a base58 identifier (a Solana address or
    # signature) is recognisable only with its case intact -- lowercasing it first would turn it back into a size
    return _largest_parameter_size_b(requested, text) >= _HEAVY_PARAMETER_FLOOR_B


def _requested_heavy_marker(*, user_text: str, source_context: dict[str, Any] | None) -> str | None:
    """The size marker that made this a heavy request, for the routing proof, or None."""

    requested = str((source_context or {}).get("requested_model") or "")
    text = str(user_text or "")
    size = _largest_parameter_size_b(requested, text)
    if size < _HEAVY_PARAMETER_FLOOR_B:
        return None
    return f"{size:g}b"


def _sanitize_text(value: Any) -> tuple[str, int]:
    text = str(value or "")
    redactions = 0
    for pattern, replacement in (
        (_SECRET_RE, "<secret-like-token>"),
        (_LONG_TOKEN_RE, "<secret-like-token>"),
        (_MAC_PATH_RE, "<private-path>"),
        (_WIN_PATH_RE, "<private-path>"),
        (_HOME_PATH_RE, "<private-path>"),
    ):
        text, count = pattern.subn(replacement, text)
        redactions += count
    return text, redactions


def _context_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),) if str(value).strip() else tuple()


def _split_constraints(value: str) -> list[str]:
    if not value.strip():
        return []
    if "\n" in value:
        return [_clip(item.strip("-* \t"), 160) for item in value.splitlines() if item.strip()]
    return [_clip(value, 220)]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _dedupe_residency(actions: list[ResidencyAction]) -> list[ResidencyAction]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ResidencyAction] = []
    for action in actions:
        key = (action.provider_id, action.model_id, action.action)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


__all__ = [
    "AutopilotFramework",
    "AutopilotLane",
    "AutopilotPhase",
    "ContextCapsule",
    "LocalInferenceAutopilotPlan",
    "PrefixCachePlan",
    "ResidencyAction",
    "build_local_inference_autopilot_plan",
    "build_prefix_cache_plan",
    "compile_context_capsule",
    "prioritize_daily_residency_capabilities",
    "select_daily_residency_model_tag",
]
