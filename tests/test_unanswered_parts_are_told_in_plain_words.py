"""The "Could not be answered" list speaks to the person, not in the ledger's evidence codes.

Measured on the first-day sweep (Nov 21), no model running, "hello whats 2+2 and tell me a joke
please": the answer ended "* and tell me a joke please — dispatched, failed (receipt demand:u2)".
The ledger keeps its own reason (it names the record); the rendered row must not.
"""
from __future__ import annotations

import pytest

from core.conductor.obligation_ledger import (
    RSS_REASON_DISPATCH_UNVERIFIABLE,
    RSS_REASON_DISPATCHED_FAILED,
    RSS_REASON_NO_LANE,
    RSS_REASON_NOT_DISPATCHED,
)
from core.finalization import RSS_REASON_WITHHELD, RSS_UNAVAILABLE_HEADER, _render_closure_rows

LEDGER_REASONS = [
    RSS_REASON_DISPATCHED_FAILED.format(receipt="demand:u2"),
    RSS_REASON_DISPATCHED_FAILED.format(receipt="demand:u2, demand:u3"),
    RSS_REASON_NOT_DISPATCHED,
    RSS_REASON_DISPATCH_UNVERIFIABLE,
    RSS_REASON_NO_LANE,
]
CODE_WORDS = ("receipt", "demand:", "dispatched", "lane")


@pytest.mark.parametrize("reason", LEDGER_REASONS)
def test_every_ledger_reason_renders_in_plain_words(reason: str) -> None:
    rendered = _render_closure_rows([{"text": "tell me a joke please", "reason": reason}], "2 + 2 = 4.")
    assert rendered.startswith(RSS_UNAVAILABLE_HEADER)
    row = rendered.splitlines()[1]
    assert row.startswith("* tell me a joke please — ")
    assert not any(word in row.lower() for word in CODE_WORDS), row


def test_reader_reasons_already_in_plain_words_pass_through() -> None:
    rendered = _render_closure_rows([{"text": "the baltic water temperature", "reason": RSS_REASON_WITHHELD}], "body")
    assert RSS_REASON_WITHHELD in rendered
