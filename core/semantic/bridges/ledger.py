"""RequestGraph <-> the obligation ledger (``core.conductor.obligation_ledger``).

The turn door mints one demand obligation per requested slot and the publication sweep later needs
the frozen slot set back. Today the sweep re-lexes the frozen request text; with the graph persisted
in the set's snapshot the sweep READS the ids it needs. The obligation rows this bridge produces are
byte-identical to the ones the door mints today (a test pins that over the corpus), so wiring it
changes no id, no text and no slice.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.semantic.canonical_text import CanonicalText
from core.semantic.graph_serialization import GraphSerializationError, graph_from_dict, graph_to_dict
from core.semantic.request_graph import RequestGraph

#: The snapshot key the serialized graph rides under, beside the ledger's own ``request_text``.
SNAPSHOT_GRAPH_KEY = "request_graph"

#: How ``frozen_slot_ids`` obtained the set: read from the persisted graph, re-derived from the
#: request text (bounded legacy fallback), or nothing was available.
SOURCE_GRAPH = "graph"
SOURCE_LEGACY_REPARSE = "legacy_reparse"
SOURCE_UNREADABLE = "unreadable"
SOURCE_NONE = "none"


def demand_obligation_rows(
    graph: RequestGraph, *, attempt_id: str, request_text: str, units: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """The obligation rows the turn door mints, built from the graph's slots.

    Exactly today's shape: the ``prose`` row first, then one ``demand`` row per slot with
    ``unit_id == SlotId`` and the slice id the splitter recorded (``units`` maps unit id -> the
    ``DemandUnit``; a slot with no unit keeps an empty slice id).
    """
    rows: list[dict[str, Any]] = [
        {"obligation_id": f"ob:{attempt_id}:answer", "text": str(request_text or "")[:240], "kind": "prose"},
    ]
    requests = {r.id: r for r in graph.requests}
    for slot in graph.slots:
        unit = (units or {}).get(str(slot.id))
        text = str(getattr(unit, "text", "") or requests[slot.request_id].source_text)
        slice_id = str(getattr(unit, "slice_id", "") or "")
        rows.append({
            "obligation_id": f"ob:{attempt_id}:demand:{slot.id}",
            "text": text,
            "kind": "demand",
            "unit_id": str(slot.id),
            "slice_id": slice_id,
        })
    return rows


def snapshot_graph_payload(graph: RequestGraph) -> dict[str, Any]:
    """The graph as the ledger persists it (text included -- the snapshot already stores the request)."""
    return graph_to_dict(graph, include_text=True)


def snapshot_has_graph(snapshot: Mapping[str, Any] | None) -> bool:
    """Whether the snapshot CARRIES a graph payload at all (readable or not)."""
    return isinstance(snapshot, Mapping) and isinstance(snapshot.get(SNAPSHOT_GRAPH_KEY), Mapping)


def graph_from_snapshot(snapshot: Mapping[str, Any] | None) -> RequestGraph | None:
    """The persisted graph, or None when the snapshot predates it OR it cannot be read. Callers that
    must tell those apart ask ``snapshot_has_graph`` first (``frozen_slot_ids`` does)."""
    if not snapshot_has_graph(snapshot):
        return None
    payload = snapshot[SNAPSHOT_GRAPH_KEY]  # type: ignore[index]
    try:
        return graph_from_dict(payload)
    except GraphSerializationError:
        text = str(snapshot.get("request_text") or "")  # type: ignore[union-attr]
        if not text:
            return None
        try:
            return graph_from_dict(payload, canonical=CanonicalText.of(text))
        except GraphSerializationError:
            return None


def frozen_slot_ids(snapshot: Mapping[str, Any] | None) -> tuple[frozenset[str], str]:
    """The turn's frozen SlotId set and where it came from.

    Reads the persisted graph first. A snapshot written before the graph existed (no graph key)
    falls back to the bounded legacy re-derivation from ``request_text`` (the same call the door
    used to mint the set) and SAYS so. A snapshot that carries a graph nobody can read is
    UNREADABLE: no re-derivation, an empty set, and the source names the condition.
    """
    graph = graph_from_snapshot(snapshot)
    if graph is not None:
        return frozenset(str(s) for s in graph.slot_ids), SOURCE_GRAPH
    if snapshot_has_graph(snapshot):
        # A graph IS there and cannot be read (unsupported version, corruption). Re-deriving the
        # slot set from text would silently REPLACE a frozen historical universe; refuse instead.
        return frozenset(), SOURCE_UNREADABLE
    text = str((snapshot or {}).get("request_text") or "") if isinstance(snapshot, Mapping) else ""
    if not text:
        return frozenset(), SOURCE_NONE
    from core.agent_runtime.answer_coverage import demand_units

    return frozenset(str(u.unit_id) for u in demand_units(text)), SOURCE_LEGACY_REPARSE


__all__ = [
    "SNAPSHOT_GRAPH_KEY",
    "SOURCE_GRAPH",
    "SOURCE_LEGACY_REPARSE",
    "SOURCE_NONE",
    "SOURCE_UNREADABLE",
    "demand_obligation_rows",
    "frozen_slot_ids",
    "graph_from_snapshot",
    "snapshot_graph_payload",
    "snapshot_has_graph",
]
