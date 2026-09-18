"""A slot whose answer shipped in part is not listed as one that could not be answered.

Measured 2026-09-06 (served comparison, fixture transport): the publication kept six supported
claims and withheld one, and the closure section still read "Could not be answered: * Now compare
the VW Passat and the VW Golf ... — an answer was produced but the sources retrieved for this turn
did not support it, so it was withheld". The renderer now separates unanswered slots from slots
answered in part, each under its own header with a reason that says what happened.
"""
from __future__ import annotations

from core.finalization import (
    RSS_PARTIAL_HEADER,
    RSS_REASON_ANSWERED_IN_PART,
    RSS_REASON_WITHHELD,
    RSS_UNAVAILABLE_HEADER,
    _render_closure_rows,
)


def test_partial_and_unanswered_slots_render_under_their_own_headers() -> None:
    pending = [
        {"text": "compare the passat and the golf: production, sales and prices", "reason": RSS_REASON_ANSWERED_IN_PART, "answered_in_part": True},
        {"text": "what is the water temperature in the baltic sea", "reason": RSS_REASON_WITHHELD},
    ]
    rendered = _render_closure_rows(pending, "body")
    assert RSS_PARTIAL_HEADER in rendered and RSS_UNAVAILABLE_HEADER in rendered
    unanswered_block, partial_block = rendered.split("\n\n")
    assert "baltic" in unanswered_block and "passat" not in unanswered_block
    assert "passat" in partial_block and "listed above as withheld" in partial_block


def test_a_turn_with_only_partial_slots_never_says_could_not_be_answered() -> None:
    pending = [{"text": "compare a and b: x, y and z", "reason": RSS_REASON_ANSWERED_IN_PART, "answered_in_part": True}]
    rendered = _render_closure_rows(pending, "body")
    assert RSS_UNAVAILABLE_HEADER not in rendered
    assert rendered.startswith(RSS_PARTIAL_HEADER)
