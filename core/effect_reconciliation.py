"""A6 effect reconciliation (assembly W001).

Owns exactly one question: DID the authorized effect actually happen?

Authority boundaries:
- A1 ExecutionGate owns "MAY this effect execute?" — this module never computes
  authorization and never calls it.
- A2 semantic_result_seam owns "WHICH semantic result was admitted?" — this
  module only consumes ``semantic_result_id`` as correlation, never mints or
  mutates one.
- No final-byte/transport authority lives here (that is a future A7 contract).

Core law: UNKNOWN is NOT FAILED. If the physical outcome of an authorized,
mutating effect cannot be proven, a durable unresolved record persists and an
identical redispatch stays blocked until truth is established mechanically, via
the provider, or by the typed user channel. THE MODEL IS NOT AN EFFECT-TRUTH
SOURCE.
"""
from __future__ import annotations

import contextvars
import enum
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Receipt/execution status literal for an effect whose physical outcome could
# not be proven. NEVER serialized as status="error"/mode="tool_failed" over a
# possibly-applied effect.
UNKNOWN_OUTCOME_STATUS = "unknown_reconciliation_required"
UNKNOWN_OUTCOME_MODE = "reconciliation_pending"
UNKNOWN_OUTCOME_PROSE = (
    "Outcome unknown — reconciliation required. The change may have already "
    "happened, so retrying it is blocked until its outcome is established."
)

# Intents whose runtime-lane handlers physically mutate but which the legacy
# `_MUTATING_TOOL_INTENTS` set does not name yet. Reservations attach to these
# in addition to `is_mutating_tool_intent`.
_A6_EXTRA_MUTATING_INTENTS = frozenset(
    {
        "machine.write_file",
        "email.send",
        "wallet.spend",
        "web0.publish",
    }
)


def a6_reserved_intent(intent: str) -> bool:
    """Whether this intent's dispatch must reserve its logical effect pre-dispatch."""
    from core.runtime_continuity import is_mutating_tool_intent

    clean = str(intent or "").strip()
    return bool(clean) and (is_mutating_tool_intent(clean) or clean in _A6_EXTRA_MUTATING_INTENTS)


class EffectOutcomeUnknown(RuntimeError):
    """Raised when a handler raised AFTER crossing the dispatch boundary.

    Neither a success nor a failure: reconciliation-required. Never downcast to
    an ordinary retryable failure."""

    def __init__(self, *, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = str(reason or "unknown")
        self.detail = str(detail or "")


class Reconcilability(enum.Enum):
    RECONCILABLE = "reconcilable"          # a resolver can mechanically prove outcome
    NOT_RECONCILABLE = "not_reconcilable"  # inherently unprovable; user resolution only
    UNKNOWN = "unknown"                    # undeclared; treated as not mechanically resolvable


class ResolutionOutcome(enum.Enum):
    APPLIED = "applied"
    FAILED_SAFE_TO_RETRY = "failed_safe_to_retry"
    STILL_UNKNOWN = "still_unknown"


@dataclass(frozen=True)
class EffectResolution:
    outcome: ResolutionOutcome
    source: str  # "mechanical" | "provider" | "user" — never "model"
    evidence: str


# ── in-flight claim ──────────────────────────────────────────────────────────
# Context-local pairing between the executor's reservation and the dispatch
# wrapper's exception boundary: only the claimant that crossed the dispatch
# boundary may classify its outcome as UNKNOWN.

_IN_FLIGHT_EFFECT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "a6_in_flight_effect", default=None
)


def set_in_flight_effect(claim: dict[str, Any]) -> contextvars.Token:
    return _IN_FLIGHT_EFFECT.set(dict(claim))


def clear_in_flight_effect(token: contextvars.Token) -> None:
    _IN_FLIGHT_EFFECT.reset(token)


def peek_in_flight_effect() -> dict[str, Any] | None:
    return _IN_FLIGHT_EFFECT.get()


# ── mechanical reconciler registry ───────────────────────────────────────────

ResolverFn = Callable[[dict[str, Any]], EffectResolution]

_RESOLVERS: dict[str, ResolverFn] = {}
_RECONCILABILITY: dict[str, Reconcilability] = {
    # Arbitrary shell commands have no probe; truthful UNKNOWN until a
    # per-command resolver is registered later.
    "sandbox.run_command": Reconcilability.NOT_RECONCILABLE,
}


def register_effect_resolver(intent: str, resolver: ResolverFn, *,
                             reconcilability: Reconcilability = Reconcilability.RECONCILABLE) -> None:
    _RESOLVERS[str(intent)] = resolver
    _RECONCILABILITY[str(intent)] = reconcilability


def reconcilability_for_intent(intent: str) -> Reconcilability:
    return _RECONCILABILITY.get(str(intent or "").strip(), Reconcilability.UNKNOWN)


def reconcile_unresolved_effect(row: dict[str, Any]) -> EffectResolution:
    """Narrow typed mechanical reconciliation entry point (EffectReconciler.resolve).

    Only mechanical evidence is consulted here. Provider-query and user
    resolutions go through resolve_unresolved_effect with their own source.
    An intent with no resolver stays STILL_UNKNOWN — that still forbids blind
    retry. No pretending.
    """
    intent = str(row.get("tool_name") or "").strip()
    resolver = _RESOLVERS.get(intent)
    if resolver is None:
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence=f"no mechanical resolver registered for `{intent}`",
        )
    try:
        resolution = resolver(row)
    except Exception as exc:  # a failing probe proves nothing; stay truthful
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence=f"resolver error: {type(exc).__name__}: {exc}",
        )
    if not isinstance(resolution, EffectResolution):
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence="resolver returned a non-typed result",
        )
    return resolution


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_file_mechanical_resolver(row: dict[str, Any]) -> EffectResolution:
    """Prove workspace/machine write_file outcome from disk state alone.

    APPLIED: target exists and hashes byte-for-byte to the intended content.
    FAILED_SAFE_TO_RETRY: target provably absent — the atomic write either
    completed fully or not at all, so absence proves the mutation never landed.
    Exists but differs: someone/something else wrote it — STILL_UNKNOWN.
    """

    evidence_map = row.get("expected_evidence") if isinstance(row.get("expected_evidence"), dict) else {}
    target_text = str(evidence_map.get("path") or row.get("resource_identity") or "")
    expected_hash = str(evidence_map.get("content_sha256") or "")
    if not target_text or not expected_hash:
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence="no canonical path / expected content hash recorded on the reservation",
        )
    target = Path(target_text)
    if not target.exists():
        return EffectResolution(
            outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY,
            source="mechanical",
            evidence=f"target provably absent after dispatch: {target}",
        )
    try:
        actual_hash = _sha256_bytes(target.read_bytes())
    except OSError as exc:
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence=f"target unreadable: {type(exc).__name__}: {exc}",
        )
    if actual_hash == expected_hash:
        return EffectResolution(
            outcome=ResolutionOutcome.APPLIED,
            source="mechanical",
            evidence=f"target content hash matches intended write: {target} sha256={actual_hash[:16]}…",
        )
    return EffectResolution(
        outcome=ResolutionOutcome.STILL_UNKNOWN,
        source="mechanical",
        evidence=f"target exists with different content than intended ({target}); cannot attribute",
    )


register_effect_resolver("workspace.write_file", _write_file_mechanical_resolver)
register_effect_resolver("machine.write_file", _write_file_mechanical_resolver)


def build_write_file_expected_evidence(*, canonical_path: str, content: str) -> dict[str, str]:
    """Evidence captured at reserve time so reconciliation can compare later."""
    return {
        "path": str(canonical_path),
        "content_sha256": _sha256_bytes(str(content).encode("utf-8")),
    }


def apply_resolution_outcome(resolution: EffectResolution, logical_effect_id: str) -> dict[str, Any]:
    """Apply a mechanical/provider resolution to the durable store (typed sources only)."""
    from core.runtime_continuity import resolve_unresolved_effect

    mapping = {
        ResolutionOutcome.APPLIED: "CONFIRMED_APPLIED",
        ResolutionOutcome.FAILED_SAFE_TO_RETRY: "CONFIRMED_FAILED_SAFE_TO_RETRY",
    }
    literal = mapping.get(resolution.outcome)
    if literal is None:
        return {"outcome": "still_unknown"}
    return resolve_unresolved_effect(
        logical_effect_id=logical_effect_id,
        resolution=literal,
        source=resolution.source,
        evidence=resolution.evidence,
        resolved_by=f"effect_reconciler:{'mechanical'}",
    )


def json_dumps_compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
