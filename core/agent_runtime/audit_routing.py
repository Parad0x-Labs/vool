"""Which model answers an audit, and why — as state the operator can read back.

A live audit ran every one of its calls on `nvidia/nemotron-3-ultra-550b-a55b:free` and nothing in
the product said so, or said why. Traced, the selection was not a bug in the ranking: the audit
asked for candidates with `enforce_hardware_fit=True`, the local models did not fit the free memory
on that box, and the one surviving manifest was a verified-zero-price OpenRouter model. The ranking
did exactly what it was told. The defect is that it was never told what the OPERATOR had chosen, and
the choice it made left no record.

Three modes, and the audit and every helper subcall inside it obey the same one:

* **manual** — the operator pinned a concrete model. That model runs nomination, the adversarial
  challenge, proof-artifact generation and synthesis. No other cloud model may answer any part of
  it. A pin that cannot be resolved is a refusal naming the model, never a substitution.
* **auto** — the runtime chooses, which is what "auto" means. Every choice is recorded with the
  reason it was made, so the answer to "why did this run on that?" is a lookup rather than an
  inference.
* **local-only** — no cloud model call is permitted at all. Enforced by removing every non-local
  manifest from the candidate list before the first call rather than by checking at each call site,
  because a filter someone must remember to apply is a filter that will eventually be forgotten.

The mode is read from STATE — the pin the composer sends, the runtime's local-only flag — never from
the operator's sentence. Prose is how the incident's permission bug happened; a routing mode that
could be talked into changing would be the same mistake in a new place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MANUAL = "manual"
AUTO = "auto"
LOCAL_ONLY = "local_only"

# Why a manifest was not eligible, in the words the receipt prints.
_LOCAL_ONLY_REFUSAL = "local-only routing is on, so no cloud model may be called for this audit"


@dataclass(frozen=True)
class AuditRouting:
    """The routing contract for one audit turn and every subcall inside it."""

    mode: str = AUTO
    requested_model: str = ""
    reason: str = ""
    # Whether an explicit fallback policy was authorized for this turn. Off by default: a pinned
    # model that fails is a refusal, not an invitation to answer as someone else.
    fallback_authorized: bool = False

    @property
    def cloud_permitted(self) -> bool:
        return self.mode != LOCAL_ONLY

    @property
    def pinned(self) -> bool:
        return self.mode == MANUAL and bool(self.requested_model)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "requested_model": self.requested_model,
            "reason": self.reason,
            "cloud_permitted": self.cloud_permitted,
            "fallback_authorized": self.fallback_authorized,
        }


@dataclass
class RoutingAttribution:
    """One recorded model choice: which step ran where, under which mode, and why.

    `auto` is allowed to route. It is not allowed to route invisibly — this is the row that makes a
    choice attributable in Activity.
    """

    step: str
    provider_id: str
    model_name: str
    mode: str
    reason: str
    cloud: bool = False
    fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "mode": self.mode,
            "reason": self.reason,
            "cloud": self.cloud,
            "fallback": self.fallback,
        }


@dataclass
class RoutingLedger:
    """Every model choice this turn made, in order."""

    routing: AuditRouting = field(default_factory=AuditRouting)
    rows: list[RoutingAttribution] = field(default_factory=list)

    def record(
        self,
        *,
        step: str,
        manifest: Any,
        reason: str = "",
        fallback: bool = False,
    ) -> RoutingAttribution:
        row = RoutingAttribution(
            step=str(step or ""),
            provider_id=str(getattr(manifest, "provider_id", "") or ""),
            model_name=str(getattr(manifest, "model_name", "") or ""),
            mode=self.routing.mode,
            reason=str(reason or self.routing.reason or ""),
            cloud=manifest_is_cloud(manifest),
            fallback=bool(fallback),
        )
        self.rows.append(row)
        return row

    @property
    def cloud_calls(self) -> int:
        return sum(1 for row in self.rows if row.cloud)

    @property
    def distinct_models(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.rows:
            label = f"{row.provider_id}:{row.model_name}".strip(":")
            if label and label not in seen:
                seen.append(label)
        return tuple(seen)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.routing.as_dict(),
            "attributions": [row.as_dict() for row in self.rows],
            "cloud_calls": self.cloud_calls,
            "models_used": list(self.distinct_models),
        }


def manifest_is_cloud(manifest: Any) -> bool:
    """Whether calling this manifest leaves the machine.

    Asks the cost classifier rather than the model name: `free_local` is the one class that is
    definitionally on this box, and everything else — including a manifest whose class could not be
    determined — is treated as remote. Failing closed is the only safe direction for a mode whose
    whole promise is that nothing left the machine.
    """
    try:
        from core.model_selection_policy import provider_cost_class

        return provider_cost_class(manifest) != "free_local"
    except Exception:
        return True


def _local_only_flagged(source_context: dict[str, Any] | None) -> bool:
    ctx = source_context if isinstance(source_context, dict) else {}
    for key in ("local_only_mode", "local_only", "cloud_disabled"):
        if bool(ctx.get(key)):
            return True
    try:
        from core.policy_engine import local_only_mode

        return bool(local_only_mode())
    except Exception:
        return False


def resolve_routing_mode(source_context: dict[str, Any] | None) -> AuditRouting:
    """The mode this audit and every subcall inside it must obey.

    Local-only is checked FIRST. A concrete pin cannot override it: a machine configured to make no
    cloud calls does not start making them because a picker was left on a cloud model, and silently
    honouring the pin there would break the one guarantee the mode exists to give.
    """
    ctx = source_context if isinstance(source_context, dict) else {}
    from core.agent_runtime.builder.pinned_generation import requested_model_id

    if _local_only_flagged(ctx):
        # The pin is CARRIED, not discarded. If it names a cloud model the turn is refused by name
        # rather than answered by a local stand-in: substituting silently would break the pin's
        # promise to keep the mode's, and the operator can only fix a conflict they are shown.
        return AuditRouting(
            mode=LOCAL_ONLY,
            requested_model=requested_model_id(ctx),
            reason="local-only routing is enabled for this runtime",
        )

    pinned = requested_model_id(ctx)
    if pinned:
        return AuditRouting(
            mode=MANUAL,
            requested_model=pinned,
            reason=f"the operator pinned `{pinned}` for this chat",
        )
    return AuditRouting(
        mode=AUTO,
        requested_model="",
        reason="the model picker is on auto, so the runtime ranked the available providers",
    )


def _ranked_candidates(agent: Any) -> list[Any]:
    from core.local_ollama_inventory import is_text_generation_ollama_model
    from core.model_selection_policy import is_verified_free_cloud_manifest, provider_cost_class
    from core.provider_routing import rank_provider_candidates

    ranked = rank_provider_candidates(
        agent.memory_router.registry,
        task_kind="normalization_assist",
        output_mode="plain_text",
        role="queen",
        allow_paid_fallback=False,
        swarm_size=4,
        min_trust=0.45,
        enforce_hardware_fit=True,
    )
    return [
        manifest
        for manifest in ranked
        if is_text_generation_ollama_model(manifest.model_name)
        and (
            provider_cost_class(manifest) != "paid_cloud"
            or is_verified_free_cloud_manifest(manifest)
        )
    ]


def select_audit_manifests(
    agent: Any, source_context: dict[str, Any] | None, routing: AuditRouting
) -> tuple[list[Any], str]:
    """(manifests to try in order, refusal reason).

    The list IS the enforcement. Under a pin it holds exactly one manifest; under local-only it
    holds only manifests that run on this machine. A caller cannot reach a model the mode forbids,
    because no such model is in the list it was handed.
    """
    if routing.mode == MANUAL:
        from core.agent_runtime.builder.pinned_generation import resolve_pinned_manifest

        manifest = resolve_pinned_manifest(agent, source_context)
        if manifest is None:
            return [], "that model is not a provider this runtime can reach"
        return [manifest], ""

    try:
        usable = _ranked_candidates(agent)
    except Exception as exc:
        return [], f"provider ranking failed ({type(exc).__name__})"

    if routing.mode == LOCAL_ONLY:
        if routing.requested_model:
            from core.agent_runtime.builder.pinned_generation import resolve_pinned_manifest

            pinned = resolve_pinned_manifest(agent, source_context)
            if pinned is not None and not manifest_is_cloud(pinned):
                return [pinned], ""
            return [], (
                f"{_LOCAL_ONLY_REFUSAL} — and `{routing.requested_model}` is not a model that runs "
                "on this machine"
            )
        local = [manifest for manifest in usable if not manifest_is_cloud(manifest)]
        if not local:
            return [], (
                f"{_LOCAL_ONLY_REFUSAL}, and no local model on this machine is currently loadable"
            )
        return local[:4], ""

    if not usable:
        return [], "no ranked text-capable provider is available"
    # Up to four, not two. Driven live 2026-08-01: the ranking put qwen3:14b first (which failed
    # model_load_gated_low_memory on a box with 0.7 GB free) and the free cloud model second (which
    # returned nothing usable) — and qwen3:8b, the one model that fits this box and has nominated
    # correct citations before, was ranked third and never got its chance. Gated locals fail in
    # ~4s, so a longer list costs little.
    return usable[:4], ""


def routing_refusal_sentence(routing: AuditRouting, reason: str) -> str:
    """Why this audit has no model to run on, naming the mode that decided it."""
    if routing.mode == MANUAL:
        return (
            f"the pinned model `{routing.requested_model}` could not be resolved to a provider "
            f"({reason}). This audit will not be answered by a different model than the one "
            "selected."
        )
    if routing.mode == LOCAL_ONLY:
        return f"no model was available for this audit ({reason})"
    return f"no model was available for this audit ({reason})"


__all__ = [
    "AUTO",
    "LOCAL_ONLY",
    "MANUAL",
    "AuditRouting",
    "RoutingAttribution",
    "RoutingLedger",
    "manifest_is_cloud",
    "resolve_routing_mode",
    "routing_refusal_sentence",
    "select_audit_manifests",
]
