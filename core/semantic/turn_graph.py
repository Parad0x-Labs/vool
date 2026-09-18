"""The turn's RequestGraph, as it rides the turn context: a TEXT-FREE projection under one key.

The full graph is persisted in the obligation set's snapshot (beside the request text the ledger
already stores). What rides ``source_context`` is a JSON-safe projection -- digest, shape, the
frozen slot ids, a structural summary, the producer and its version -- because the context dict is
copied across pool threads and serialized into receipts and events, and neither user text nor a
frozen dataclass belongs there. The projection is what the resolution receipt reads.

Written only by the turn door (``core.agent_runtime.agent``), read anywhere. Like ``turn_request``,
the key is server-written and stripped from every inbound body (``core.request_trust``); a reader
that knows its turn id asks for that turn's projection and gets None for any other.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from core.semantic.graph_builder import graph_summary
from core.semantic.graph_serialization import graph_digest
from core.semantic.request_graph import RequestGraph

#: The source_context key the projection rides under.
REQUEST_GRAPH_KEY = "request_graph"
PROJECTION_SCHEMA = "vool.request_graph_projection.v1"


def graph_projection(graph: RequestGraph, *, producer: str, producer_version: str) -> dict[str, Any]:
    """A text-free, JSON-safe view of ``graph`` for the turn context and the receipt."""
    return {
        "schema": PROJECTION_SCHEMA,
        "turn_id": str(graph.turn_id or ""),
        "producer": str(producer),
        "producer_version": str(producer_version),
        "digest": graph_digest(graph),
        "canonical_digest": graph.canonical.digest,
        "shape": graph.shape.value,
        "slot_ids": [str(s) for s in (s.id for s in graph.slots)],
        "request_ids": [str(r.id) for r in graph.requests],
        "summary": graph_summary(graph),
        "failed": "",
    }


def failed_projection(reason: str, *, turn_id: str = "") -> dict[str, Any]:
    """What the context carries when the producer faulted: a visible failure, not an absence."""
    return {
        "schema": PROJECTION_SCHEMA,
        "turn_id": str(turn_id or ""),
        "producer": "",
        "producer_version": "",
        "digest": "",
        "canonical_digest": "",
        "shape": "unknown",
        "slot_ids": [],
        "request_ids": [],
        "summary": {},
        "failed": str(reason or "unknown"),
    }


def publish_request_graph(source_context: MutableMapping[str, Any] | None, projection: Mapping[str, Any] | None) -> None:
    """Write the projection onto the turn context. Never raises; a non-dict context is left alone."""
    if not isinstance(source_context, MutableMapping) or not isinstance(projection, Mapping):
        return
    try:
        source_context[REQUEST_GRAPH_KEY] = dict(projection)
    except Exception:
        return


def current_graph_projection(
    source_context: Mapping[str, Any] | None, *, turn_id: str | None = None
) -> dict[str, Any] | None:
    """The projection the door wrote for this turn, or None when no door wrote one.

    With ``turn_id`` the projection must name that turn: a projection copied from another turn's
    context (or forged -- the key is stripped at ingress, but a schema tag is not provenance) reads
    as absent rather than as this turn's graph."""
    if not isinstance(source_context, Mapping):
        return None
    value = source_context.get(REQUEST_GRAPH_KEY)
    if not (isinstance(value, Mapping) and value.get("schema") == PROJECTION_SCHEMA):
        return None
    if turn_id and str(value.get("turn_id") or "") != str(turn_id):
        return None
    return dict(value)


__all__ = [
    "PROJECTION_SCHEMA",
    "REQUEST_GRAPH_KEY",
    "current_graph_projection",
    "failed_projection",
    "graph_projection",
    "publish_request_graph",
]
