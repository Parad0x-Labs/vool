"""Slot conservation: ID-set equality (not count), uniqueness, and ANSWERED-means-content."""
from __future__ import annotations

import pytest

from core.semantic.canonical_text import CanonicalText
from core.semantic.request_graph import (
    InterpretationState,
    Request,
    RequestGraph,
    Slot,
    TerminalDisposition,
)
from core.semantic.slot_reconciliation import (
    SlotConservationError,
    SlotResult,
    reconcile,
    require_conserved,
)


def _graph() -> RequestGraph:
    c = CanonicalText.of("price of gold and price of silver")
    return RequestGraph(
        turn_id="t",  # type: ignore[arg-type]
        canonical=c,
        requests=(Request(id="r0", source_text=c.text, slot_ids=("slot-gold", "slot-silver"),  # type: ignore[arg-type]
                          state=InterpretationState.RESOLVED),),
        slots=(
            Slot(id="slot-gold", request_id="r0"),  # type: ignore[arg-type]
            Slot(id="slot-silver", request_id="r0"),  # type: ignore[arg-type]
        ),
    )


def test_every_slot_terminated_once_with_content_is_conserved() -> None:
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="$2400/oz"),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.ANSWERED, content="$30/oz"),  # type: ignore[arg-type]
    )
    rec = require_conserved(g, results)
    assert rec.conserved and rec.violations() == ()


def test_non_answered_dispositions_do_not_require_content() -> None:
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="$2400/oz"),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.BLOCKED, detail="retrieval forbidden"),  # type: ignore[arg-type]
    )
    assert reconcile(g, results).conserved


def test_a_missing_slot_is_not_conserved_and_fails_closed() -> None:
    g = _graph()
    results = (SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="$2400/oz"),)  # type: ignore[arg-type]
    rec = reconcile(g, results)
    assert not rec.conserved and rec.missing == ("slot-silver",)
    with pytest.raises(SlotConservationError, match="missing terminal result"):
        require_conserved(g, results)


def test_a_result_for_an_unknown_slot_is_invented() -> None:
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="x"),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.ANSWERED, content="y"),  # type: ignore[arg-type]
        SlotResult("slot-platinum", TerminalDisposition.ANSWERED, content="z"),  # type: ignore[arg-type]
    )
    rec = reconcile(g, results)
    assert not rec.conserved and rec.invented == ("slot-platinum",)


def test_a_slot_terminated_twice_is_duplicated() -> None:
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="x"),  # type: ignore[arg-type]
        SlotResult("slot-gold", TerminalDisposition.FAILED),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.ANSWERED, content="y"),  # type: ignore[arg-type]
    )
    rec = reconcile(g, results)
    assert not rec.conserved and rec.duplicated == ("slot-gold",)


def test_answered_without_content_is_refused() -> None:
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="x"),  # type: ignore[arg-type]
        SlotResult("slot-silver", TerminalDisposition.ANSWERED, content="   "),  # type: ignore[arg-type]
    )
    rec = reconcile(g, results)
    assert not rec.conserved and rec.answered_without_content == ("slot-silver",)
    with pytest.raises(SlotConservationError, match="ANSWERED without content"):
        require_conserved(g, results)


def test_right_count_wrong_ids_is_not_conserved() -> None:
    """The core distinction: count equality is INSUFFICIENT. Two results for two slots, but one id
    is wrong -> one missing + one invented, not conserved."""
    g = _graph()
    results = (
        SlotResult("slot-gold", TerminalDisposition.ANSWERED, content="x"),  # type: ignore[arg-type]
        SlotResult("slot-WRONG", TerminalDisposition.ANSWERED, content="y"),  # type: ignore[arg-type]
    )
    rec = reconcile(g, results)
    assert len(results) == len(g.slot_ids)          # counts match
    assert not rec.conserved                         # but ID sets do not
    assert rec.missing == ("slot-silver",) and rec.invented == ("slot-WRONG",)
