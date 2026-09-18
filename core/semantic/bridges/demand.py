"""RequestGraph slots <-> the demand ledger's records and dispositions.

Two vocabularies already describe how a requested slot ended: ``DemandRecord.terminal_state``
(``core.agent_runtime.demand_ownership``) and the obligation ledger's demand dispositions
(``core.conductor.obligation_ledger``). This bridge maps BOTH onto ``TerminalDisposition`` so the
slot-conservation law (``core.semantic.slot_reconciliation``) can be checked over the graph's frozen
SlotIds regardless of which layer produced the outcome. The maps are TOTAL over their source
vocabularies (a test pins that), and an unknown state is UNVERIFIED -- never silently ANSWERED.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from core.semantic.request_graph import RequestGraph, SlotId, TerminalDisposition
from core.semantic.slot_reconciliation import SlotResult

#: DemandRecord.terminal_state -> TerminalDisposition.
DEMAND_STATE_TO_DISPOSITION: dict[str, TerminalDisposition] = {
    "executed": TerminalDisposition.ANSWERED,
    "failed": TerminalDisposition.FAILED,
    "pending_approval": TerminalDisposition.BLOCKED,
    "not_attempted": TerminalDisposition.UNKNOWN,
    "refused": TerminalDisposition.DENIED,
}

#: Obligation-ledger demand disposition -> TerminalDisposition.
LEDGER_STATE_TO_DISPOSITION: dict[str, TerminalDisposition] = {
    "satisfied": TerminalDisposition.ANSWERED,
    "failed": TerminalDisposition.FAILED,
    "gated": TerminalDisposition.BLOCKED,
    "unsupported": TerminalDisposition.BLOCKED,
    "superseded": TerminalDisposition.SUPERSEDED,
    "cancelled": TerminalDisposition.CANCELLED,
    "unanswered": TerminalDisposition.UNKNOWN,
    "indeterminate": TerminalDisposition.UNVERIFIED,
    "partial": TerminalDisposition.UNVERIFIED,
}

#: Ledger states that are NOT terminal: a slot still in one of these has no result yet.
LEDGER_OPEN_STATES = frozenset({"planned", "absent"})


def slot_id_for_unit(unit_id: str) -> SlotId:
    """The lexical producer mints SlotId == DemandUnit.unit_id, so this is the identity."""
    return SlotId(str(unit_id))


def slot_results_from_demand_records(
    records: Iterable[Any],
    graph: RequestGraph,
    *,
    answers: Mapping[str, str] | None = None,
) -> tuple[SlotResult, ...]:
    """One ``SlotResult`` per (record, member unit) -- duplicates INCLUDED, bound to the graph.

    ``answers`` maps a unit id to the actual answer content/evidence its slot received. A record
    that says ``executed`` without such content is UNVERIFIED, never ANSWERED: the request text is
    a question, not an answer, and an execution label is not content. Two records naming one unit
    yield two results so ``reconcile`` can refuse the duplicate; nothing is de-duplicated here.
    """
    from core.semantic.graph_serialization import graph_digest

    digest = graph_digest(graph)
    turn_id = str(graph.turn_id or "")
    content_by_unit = dict(answers or {})
    out: list[SlotResult] = []
    for record in records:
        state = str(getattr(record, "terminal_state", "") or "")
        disposition = DEMAND_STATE_TO_DISPOSITION.get(state, TerminalDisposition.UNVERIFIED)
        units = tuple(getattr(record, "member_unit_ids", ()) or ()) or (str(getattr(record, "demand_id", "")),)
        reasons = "; ".join(str(r) for r in (getattr(record, "refusal_reasons", ()) or ()))
        for unit in units:
            sid = slot_id_for_unit(unit)
            content = str(content_by_unit.get(str(unit), "") or "").strip()
            effective = disposition
            if effective is TerminalDisposition.ANSWERED and not content:
                effective = TerminalDisposition.UNVERIFIED
            out.append(SlotResult(
                slot_id=sid, disposition=effective, content=content if effective is TerminalDisposition.ANSWERED else "",
                detail=reasons or f"demand_record:{state}", turn_id=turn_id, graph_digest=digest,
            ))
    return tuple(out)


def slot_results_from_ledger(
    rows: Iterable[Mapping[str, Any]],
    *,
    answered_content: Mapping[str, str] | None = None,
    graph: RequestGraph | None = None,
) -> tuple[SlotResult, ...]:
    """One ``SlotResult`` per demand obligation row (``obligation_ledger.demand_obligations``).

    A row still in an open state yields NO result -- the slot has not terminated, and reconciliation
    reports it as missing, which is the law working. ``answered_content`` supplies, per unit id, the
    EVIDENCE that the served bytes answer the slot (the boundary derives it from the final payload;
    the ledger row does not know it). With ``graph`` every result is bound to that graph's turn and
    digest, which is what lets ``reconcile`` refuse a result from another turn.
    """
    content_by_unit = dict(answered_content or {})
    digest = ""
    turn_id = ""
    if graph is not None:
        from core.semantic.graph_serialization import graph_digest

        digest, turn_id = graph_digest(graph), str(graph.turn_id or "")
    out: list[SlotResult] = []
    for row in rows:
        unit_id = str(row.get("unit_id") or "")
        if not unit_id:
            continue
        state = str(row.get("state") or "")
        if state in LEDGER_OPEN_STATES:
            continue
        disposition = LEDGER_STATE_TO_DISPOSITION.get(state, TerminalDisposition.UNVERIFIED)
        content = content_by_unit.get(unit_id, "") if disposition is TerminalDisposition.ANSWERED else ""
        if disposition is TerminalDisposition.ANSWERED and not content:
            # The ledger says satisfied but nobody supplied the served text: that is an unverified
            # answer at this seam, not an answered one. ANSWERED means content.
            disposition = TerminalDisposition.UNVERIFIED
        out.append(SlotResult(
            slot_id=slot_id_for_unit(unit_id), disposition=disposition, content=content,
            detail=f"ledger:{state}:{row.get('evidence_source') or ''}", turn_id=turn_id, graph_digest=digest,
        ))
    return tuple(out)


__all__ = [
    "DEMAND_STATE_TO_DISPOSITION",
    "LEDGER_OPEN_STATES",
    "LEDGER_STATE_TO_DISPOSITION",
    "slot_id_for_unit",
    "slot_results_from_demand_records",
    "slot_results_from_ledger",
]
