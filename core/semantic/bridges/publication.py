"""RequestGraph slots -> what the rendered answer visibly accounts for.

The renderer owns layout. This bridge only turns what it reports -- which units the served bytes
answered (the sweep's evidence), which units were rendered as an explicit "could not be answered"
row -- into ``PublishedSlot`` rows keyed by the graph's SlotIds, so publication conservation
(``core.semantic.publication_conservation``) can verify every terminal slot is visible in prose,
tables, JSON, literal and artifact outputs alike.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from core.semantic.publication_conservation import PublishedSlot
from core.semantic.request_graph import TerminalDisposition
from core.semantic.slot_reconciliation import SlotResult


def published_slots(
    results: Iterable[SlotResult],
    *,
    answered_units: Mapping[str, str] | None = None,
    rendered_units: Mapping[str, str] | None = None,
) -> tuple[PublishedSlot, ...]:
    """One ``PublishedSlot`` per terminal result.

    ``answered_units`` maps unit id -> the served text that answered it (visible when non-empty);
    ``rendered_units`` maps unit id -> the disclosure row the renderer emitted for a slot that did
    not get an answer. A terminal slot in neither map is emitted with ``visible=False`` so the
    conservation check reports it as hidden rather than silently passing it.
    """
    answered = dict(answered_units or {})
    rendered = dict(rendered_units or {})
    out: list[PublishedSlot] = []
    for result in results:
        sid = str(result.slot_id)
        if result.disposition is TerminalDisposition.ANSWERED and str(answered.get(sid, "") or "").strip():
            out.append(PublishedSlot(slot_id=result.slot_id, visible=True, rendering=str(answered[sid])))
        elif str(rendered.get(sid, "") or "").strip():
            out.append(PublishedSlot(slot_id=result.slot_id, visible=True, rendering=str(rendered[sid])))
        else:
            out.append(PublishedSlot(slot_id=result.slot_id, visible=False, rendering=""))
    return tuple(out)


__all__ = ["published_slots"]
