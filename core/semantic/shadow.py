"""SHADOW rung: run the resolver beside the heuristic, record the difference, dispatch nothing.

This is the first rung of the authority ladder. When the mode is SHADOW (and a resolver is
registered and a backend is available), the seam asks the resolver to interpret the turn, compares
its reading to the heuristic reading the runtime is ALREADY going to act on, and records the
comparison. It changes nothing about the turn: there is no execution callback here, no call into
admission, no return value the caller acts on. The turn proceeds on the heuristic exactly as it does
with the resolver switched off.

Modelled on the runtime's existing dual-run precedent (``core.agent_runtime.demand_ownership``'s
``SHADOW_DISAGREEMENTS``): a disagreement is recorded, never dispatched. The value it produces is
evidence — a differential ledger of where a model-first reading and the lexical reading diverge —
which is what gates every rung above this one.

The observation records the request DIGEST, not the text: the turn's text already lives in the
event ledger under its own redaction, and copying it here would duplicate user content into a
second store under a second policy (the same rule ``CanonicalText.to_dict`` follows).
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.semantic.authority_mode import SemanticAuthorityMode, mode_runs_resolver
from core.semantic.canonical_text import CanonicalText
from core.semantic.types import IntentProposal, SemanticResolver

#: Bounded in-process ring of the most recent observations. Bounded so a long-running process cannot
#: grow it without limit; the durable evidence for a real evaluation is the differential harness.
_MAX_OBSERVATIONS = 512
_OBSERVATIONS: deque[SemanticShadowObservation] = deque(maxlen=_MAX_OBSERVATIONS)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class SemanticShadowObservation:
    """One shadow comparison of the resolver reading against the heuristic reading. Dispatches nothing."""

    request_digest: str
    mode: str
    resolver_available: bool
    resolver_clause_count: int
    resolver_operations: tuple[str, ...]
    heuristic_request_count: int
    heuristic_answer_mode: str
    #: The one signal this rung exists to gather: did the model-first decomposition and the lexical
    #: demand-unit reading agree on HOW MANY things were asked? Over/under-split is the multi-intent
    #: failure class in one bit.
    clause_count_agrees: bool
    #: Structurally always False on this rung. Pinned by a test: SHADOW cannot act on a turn.
    dispatched: bool = False
    detail: str = ""
    resolver_operation_list: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_digest": self.request_digest,
            "mode": self.mode,
            "resolver_available": self.resolver_available,
            "resolver_clause_count": self.resolver_clause_count,
            "resolver_operations": list(self.resolver_operations),
            "heuristic_request_count": self.heuristic_request_count,
            "heuristic_answer_mode": self.heuristic_answer_mode,
            "clause_count_agrees": self.clause_count_agrees,
            "dispatched": self.dispatched,
            "detail": self.detail,
        }


def build_observation(
    canonical: CanonicalText,
    proposals: Sequence[IntentProposal],
    *,
    mode: SemanticAuthorityMode,
    resolver_available: bool,
    heuristic_request_count: int,
    heuristic_answer_mode: str,
    detail: str = "",
) -> SemanticShadowObservation:
    """Assemble the typed comparison. Pure: it dispatches nothing and touches no executor.

    ``proposals`` is whatever the resolver returned (possibly empty when it was unavailable or
    abstained). The operation multiset is de-duplicated for the summary field and kept whole in
    ``resolver_operation_list`` so an over-split into repeated operations is still visible.
    """
    operations = tuple(str(p.operation) for p in proposals)
    unique_ops = tuple(dict.fromkeys(operations))
    clause_count = len(proposals)
    return SemanticShadowObservation(
        request_digest=canonical.digest,
        mode=mode.value,
        resolver_available=bool(resolver_available),
        resolver_clause_count=clause_count,
        resolver_operations=unique_ops,
        heuristic_request_count=int(heuristic_request_count),
        heuristic_answer_mode=str(heuristic_answer_mode or ""),
        clause_count_agrees=(clause_count == int(heuristic_request_count)),
        dispatched=False,
        detail=str(detail),
        resolver_operation_list=operations,
    )


def record_observation(observation: SemanticShadowObservation) -> None:
    """Append to the in-process ring. Refuses to record anything marked dispatched — that state is
    impossible on this rung, and recording one would mean the invariant had already been broken."""
    if observation.dispatched:
        raise ValueError("SHADOW observations never dispatch; a dispatched observation is a defect")
    with _LOCK:
        _OBSERVATIONS.append(observation)


def recent_observations() -> tuple[SemanticShadowObservation, ...]:
    with _LOCK:
        return tuple(_OBSERVATIONS)


def clear_observations() -> None:
    with _LOCK:
        _OBSERVATIONS.clear()


def observe_turn_in_shadow(
    canonical: CanonicalText,
    *,
    resolver: SemanticResolver,
    operations: Sequence[str],
    descriptions: Mapping[str, str] | None = None,
    heuristic_request_count: int,
    heuristic_answer_mode: str,
    mode: SemanticAuthorityMode = SemanticAuthorityMode.SHADOW,
    record: bool = True,
) -> SemanticShadowObservation | None:
    """Run the resolver observe-only and record the comparison. Returns the observation (or None).

    Abstain-and-fallback and side-effect-free with respect to the turn: the resolver abstains (it
    returns ``()``) rather than raises on any failure, nothing here calls admission or an executor,
    and the return value is evidence the caller is free to ignore. A resolver failure never grants
    tool or action authority and never means "execute anyway" — the deterministic path stays
    authoritative. In OFF mode this is a no-op.
    """
    if not mode_runs_resolver(mode):
        return None
    try:
        proposals = tuple(resolver.propose(canonical, operations=tuple(operations)))
    except Exception:
        proposals = ()
        observation = build_observation(
            canonical,
            proposals,
            mode=mode,
            resolver_available=False,
            heuristic_request_count=heuristic_request_count,
            heuristic_answer_mode=heuristic_answer_mode,
            detail="resolver raised; treated as unavailable",
        )
        if record:
            record_observation(observation)
        return observation
    observation = build_observation(
        canonical,
        proposals,
        mode=mode,
        resolver_available=True,
        heuristic_request_count=heuristic_request_count,
        heuristic_answer_mode=heuristic_answer_mode,
    )
    if record:
        record_observation(observation)
    return observation


__all__ = [
    "SemanticShadowObservation",
    "build_observation",
    "clear_observations",
    "observe_turn_in_shadow",
    "recent_observations",
    "record_observation",
]
