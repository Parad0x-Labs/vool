"""Publication conservation: every terminal SlotID is visibly represented in the rendered answer."""
from __future__ import annotations

import pytest

from core.semantic.canonical_text import CanonicalText
from core.semantic.publication_conservation import (
    PublicationConservationError,
    PublishedSlot,
    require_published,
    verify_publication,
)
from core.semantic.request_graph import (
    InterpretationState,
    Request,
    RequestGraph,
    Slot,
    TerminalDisposition,
)
from core.semantic.slot_reconciliation import SlotResult, reconcile


def _graph() -> RequestGraph:
    c = CanonicalText.of("price of gold and price of silver")
    return RequestGraph(
        turn_id="t",  # type: ignore[arg-type]
        canonical=c,
        requests=(Request(id="r0", source_text=c.text, slot_ids=("slot-gold", "slot-silver"),  # type: ignore[arg-type]
                          state=InterpretationState.RESOLVED),),
        slots=(Slot(id="slot-gold", request_id="r0"), Slot(id="slot-silver", request_id="r0")),  # type: ignore[arg-type]
    )


def _conserved_reconciliation():
    # gold answered, silver blocked (retrieval forbidden) — both are terminal, both must be visible.
    return reconcile(_graph(), (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="$2400/oz"),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.BLOCKED, detail="retrieval forbidden"),  # type: ignore[arg-type]
    ))


def test_every_terminal_slot_visible_is_conserved() -> None:
    rec = _conserved_reconciliation()
    published = (
        PublishedSlot("slot-gold", visible=True, rendering="Gold: $2400/oz"),  # type: ignore[arg-type]
        PublishedSlot("slot-silver", visible=True, rendering="Silver: unavailable (retrieval off)"),  # type: ignore[arg-type]
    )
    verdict = require_published(rec, published)
    assert verdict.conserved and verdict.violations() == ()


def test_a_blocked_slot_omitted_from_the_answer_is_refused() -> None:
    """The exact Astra defect: a non-answered slot silently vanishes from publication."""
    rec = _conserved_reconciliation()
    published = (PublishedSlot("slot-gold", visible=True, rendering="Gold: $2400/oz"),)  # type: ignore[arg-type]
    verdict = verify_publication(rec, published)
    assert not verdict.conserved and verdict.omitted == ("slot-silver",)
    with pytest.raises(PublicationConservationError, match="omitted"):
        require_published(rec, published)


def test_a_slot_rendered_but_hidden_is_refused() -> None:
    rec = _conserved_reconciliation()
    published = (
        PublishedSlot("slot-gold", visible=True, rendering="Gold: $2400/oz"),  # type: ignore[arg-type]
        PublishedSlot("slot-silver", visible=False, rendering=""),  # emitted structurally, not shown  # type: ignore[arg-type]
    )
    verdict = verify_publication(rec, published)
    assert not verdict.conserved and verdict.hidden == ("slot-silver",)
    with pytest.raises(PublicationConservationError, match="not visible"):
        require_published(rec, published)


def test_rendered_terminal_slots_conserve_publication() -> None:
    rec = _conserved_reconciliation()
    published = (
        PublishedSlot("slot-gold", visible=True, rendering="Gold: $2400/oz"),  # type: ignore[arg-type]
        PublishedSlot("slot-silver", visible=True, rendering="Silver: unavailable"),  # type: ignore[arg-type]
    )
    assert verify_publication(rec, published).conserved


def test_publishing_an_unconserved_terminal_set_is_refused_up_front() -> None:
    # silver never terminated -> reconciliation not conserved -> cannot publish.
    rec = reconcile(_graph(), (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="$2400/oz"),))  # type: ignore[arg-type]
    assert not rec.conserved
    with pytest.raises(PublicationConservationError, match="unconserved terminal set"):
        require_published(rec, (PublishedSlot("slot-gold", visible=True),))  # type: ignore[arg-type]
