"""SHADOW observation v2: what one resolver run beside the deterministic reading recorded.

Replaces the v1 record (a request digest, a mode and one count-agreement bit) with the evidence a
promotion decision actually needs and NOTHING a leak could ride on:

* identity: ticket, turn, session, request digest -- never the text;
* versions: resolver, provider/model, prompt, reply schema, catalog, policy -- so a differential
  can be attributed to what produced it;
* cost and failure: latency, and a typed failure (timeout, queue full, backend error, parse
  error, cancelled, abstained, not prepared, none);
* the graph differential: per-axis verdicts and strict whole-turn correctness of the resolver's
  graph against the heuristic graph, plus both graph digests and the resolver graph's text-free
  structural summary.

Structurally always observe-only (``dispatched`` is False and the ring refuses anything else). The
builder takes graphs and enums; it has no parameter a prompt or a reply could be passed through, and
``assert_text_free`` is the sanitization proof a test drives with secret-bearing strings.
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

OBSERVATION_SCHEMA = "vool.semantic_shadow_observation.v2"
SHADOW_OBSERVATION_EVENT = "semantic_shadow_observation"

FAILURE_NONE = "none"
FAILURE_TIMEOUT = "timeout"
FAILURE_QUEUE_FULL = "queue_full"
FAILURE_BACKEND_ERROR = "backend_error"
FAILURE_PARSE_ERROR = "parse_error"
FAILURE_CANCELLED = "cancelled"
FAILURE_ABSTAINED = "abstained"
FAILURE_NOT_PREPARED = "not_prepared"
FAILURE_TYPES = frozenset({
    FAILURE_NONE, FAILURE_TIMEOUT, FAILURE_QUEUE_FULL, FAILURE_BACKEND_ERROR, FAILURE_PARSE_ERROR,
    FAILURE_CANCELLED, FAILURE_ABSTAINED, FAILURE_NOT_PREPARED,
})

_MAX_OBSERVATIONS = 512
_OBSERVATIONS: deque[ShadowObservationV2] = deque(maxlen=_MAX_OBSERVATIONS)
_LOCK = threading.Lock()


@dataclass(frozen=True)
class ShadowObservationV2:
    ticket_id: str
    turn_id: str
    session_id: str
    request_digest: str
    mode: str
    resolver_name: str = ""
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    schema_version: str = ""
    catalog_version: str = ""
    policy_version: str = ""
    latency_ms: float = 0.0
    failure_type: str = FAILURE_NONE
    resolver_graph_digest: str = ""
    heuristic_graph_digest: str = ""
    differential: dict[str, Any] | None = None
    resolver_summary: dict[str, Any] = field(default_factory=dict)
    note_kinds: dict[str, int] = field(default_factory=dict)
    late: bool = False
    dispatched: bool = False
    schema: str = OBSERVATION_SCHEMA

    def __post_init__(self) -> None:
        if self.failure_type not in FAILURE_TYPES:
            raise ValueError(f"unknown shadow failure type {self.failure_type!r}")
        if self.dispatched:
            raise ValueError("SHADOW observations never dispatch; a dispatched observation is a defect")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ticket_id": self.ticket_id,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "request_digest": self.request_digest,
            "mode": self.mode,
            "resolver_name": self.resolver_name,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "catalog_version": self.catalog_version,
            "policy_version": self.policy_version,
            "latency_ms": round(float(self.latency_ms), 3),
            "failure_type": self.failure_type,
            "resolver_graph_digest": self.resolver_graph_digest,
            "heuristic_graph_digest": self.heuristic_graph_digest,
            "differential": dict(self.differential) if self.differential is not None else None,
            "resolver_summary": dict(self.resolver_summary),
            "note_kinds": dict(self.note_kinds),
            "late": bool(self.late),
            "dispatched": False,
        }


def build_observation(
    *,
    ticket_id: str,
    turn_id: str,
    session_id: str,
    request_digest: str,
    mode: str,
    resolver_name: str = "",
    provider: str = "",
    model: str = "",
    prompt_version: str = "",
    schema_version: str = "",
    catalog_version: str = "",
    policy_version: str = "",
    latency_ms: float = 0.0,
    failure_type: str = FAILURE_NONE,
    resolver_graph: Any = None,
    heuristic_graph: Any = None,
    notes: Iterable[str] = (),
    late: bool = False,
) -> ShadowObservationV2:
    """Assemble the record from GRAPHS and enums. There is no parameter for prompt or reply text;
    the graphs contribute only their digests, a structural summary and a per-axis differential."""
    from core.semantic.graph_builder import graph_summary
    from core.semantic.graph_serialization import graph_digest

    differential = None
    if resolver_graph is not None and heuristic_graph is not None:
        from core.semantic.graph_diff import compare_graphs

        differential = compare_graphs(heuristic_graph, resolver_graph).to_dict()
    note_kinds: dict[str, int] = {}
    for note in notes:
        kind = str(note).split(":", 1)[0]
        note_kinds[kind] = note_kinds.get(kind, 0) + 1
    return ShadowObservationV2(
        ticket_id=str(ticket_id), turn_id=str(turn_id), session_id=str(session_id),
        request_digest=str(request_digest), mode=str(mode), resolver_name=str(resolver_name),
        provider=str(provider), model=str(model), prompt_version=str(prompt_version),
        schema_version=str(schema_version), catalog_version=str(catalog_version),
        policy_version=str(policy_version), latency_ms=float(latency_ms), failure_type=str(failure_type),
        resolver_graph_digest=graph_digest(resolver_graph) if resolver_graph is not None else "",
        heuristic_graph_digest=graph_digest(heuristic_graph) if heuristic_graph is not None else "",
        differential=differential,
        resolver_summary=graph_summary(resolver_graph) if resolver_graph is not None else {},
        note_kinds=note_kinds, late=bool(late),
    )


def assert_text_free(payload: Mapping[str, Any], forbidden: Iterable[str]) -> None:
    """Raise if any forbidden string (user text, a prompt, a reply, a key) appears in ``payload``."""
    import json

    rendered = json.dumps(payload, sort_keys=True, default=str).casefold()
    for needle in forbidden:
        probe = str(needle or "").casefold()
        if probe and probe in rendered:
            raise AssertionError(f"shadow observation leaks text: {probe[:24]!r}...")


def record_observation(observation: ShadowObservationV2) -> None:
    if observation.dispatched:
        raise ValueError("SHADOW observations never dispatch")
    with _LOCK:
        _OBSERVATIONS.append(observation)


def recent_observations() -> tuple[ShadowObservationV2, ...]:
    with _LOCK:
        return tuple(_OBSERVATIONS)


def clear_observations() -> None:
    with _LOCK:
        _OBSERVATIONS.clear()


__all__ = [
    "FAILURE_ABSTAINED",
    "FAILURE_BACKEND_ERROR",
    "FAILURE_CANCELLED",
    "FAILURE_NONE",
    "FAILURE_NOT_PREPARED",
    "FAILURE_PARSE_ERROR",
    "FAILURE_QUEUE_FULL",
    "FAILURE_TIMEOUT",
    "FAILURE_TYPES",
    "OBSERVATION_SCHEMA",
    "SHADOW_OBSERVATION_EVENT",
    "ShadowObservationV2",
    "assert_text_free",
    "build_observation",
    "clear_observations",
    "recent_observations",
    "record_observation",
]
