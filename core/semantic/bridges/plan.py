"""RequestGraph -> the planner's clauses and the dependency law.

``ProposedClause`` (``core.conductor.planner``) carries forward-compatibility fields for exactly this
envelope -- spans, the canonical representation, provenance -- and ``IntentProposal`` gained the
stable ids. This bridge fills them from a graph so a planner or admission consumer speaks ids
instead of re-parsing text.

The dependency law lives here too: an ADMITTED prerequisite is not a SUCCESSFUL one. A VALUE
dependency releases only when the prerequisite slot terminated ANSWERED; a dependent whose
prerequisite ended any other way is BLOCKED, and the edge is neither removed nor rewired.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.semantic.request_graph import DependencyKind, RequestGraph, RequestId, TerminalDisposition
from core.semantic.resolver import proposals_from_graph
from core.semantic.slot_reconciliation import SlotResult


def proposed_clauses_from_graph(graph: RequestGraph) -> tuple[Any, ...]:
    """One ``ProposedClause`` per live request, spans and provenance filled, positions derived."""
    from core.conductor.planner import ProposedClause

    clauses: list[Any] = []
    for proposal in proposals_from_graph(graph):
        request = next(r for r in graph.requests if r.id == proposal.request_id)
        clauses.append(ProposedClause(
            index=proposal.index,
            request=proposal.request_text,
            operation=proposal.operation,
            depends_on=proposal.depends_on,
            spans=proposal.spans,
            canonical_representation=graph.canonical.representation if proposal.spans else "",
            origin="request_graph",
            unresolved_reason=(
                next((s.reason for s in graph.slots_for(request.id) if s.reason), "")
                if request.unresolved else ""
            ),
            request_id=str(proposal.request_id),
            slot_ids=tuple(str(sid) for sid in proposal.slot_ids),
        ))
    return tuple(clauses)


def released_value_dependencies(graph: RequestGraph, results: Mapping[str, SlotResult]) -> dict[str, bool]:
    """Per VALUE edge: whether its prerequisite slot terminated ANSWERED (with content)."""
    out: dict[str, bool] = {}
    for edge in graph.dependencies:
        if edge.kind is not DependencyKind.VALUE:
            continue
        prerequisite = results.get(str(edge.value_ref)) if edge.value_ref else None
        out[str(edge.id)] = bool(
            prerequisite is not None
            and prerequisite.disposition is TerminalDisposition.ANSWERED
            and str(prerequisite.content or "").strip()
        )
    return out


def blocked_requests(graph: RequestGraph, results: Mapping[str, SlotResult]) -> frozenset[RequestId]:
    """Dependents that may not release: a VALUE prerequisite not ANSWERED, a CONDITIONAL/ORDERING
    prerequisite with no result yet. The edges stay; only the release is withheld."""
    released = released_value_dependencies(graph, results)
    blocked: set[RequestId] = set()
    slots_by_request = {r.id: [s.id for s in graph.slots_for(r.id)] for r in graph.requests}
    for edge in graph.dependencies:
        if edge.kind is DependencyKind.VALUE:
            if not released.get(str(edge.id), False):
                blocked.add(edge.from_request)
            continue
        prerequisite_slots = slots_by_request.get(edge.to_request, [])
        if not prerequisite_slots or any(str(sid) not in results for sid in prerequisite_slots):
            blocked.add(edge.from_request)
            continue
        if edge.kind is DependencyKind.CONDITIONAL and not all(
            results[str(sid)].disposition is TerminalDisposition.ANSWERED for sid in prerequisite_slots
        ):
            blocked.add(edge.from_request)
    return frozenset(blocked)


__all__ = ["blocked_requests", "proposed_clauses_from_graph", "released_value_dependencies"]
