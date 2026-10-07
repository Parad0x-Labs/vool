"""A reply that follows the format the person spelled out is not reshaped into a table.

Measured on the live agent-team comparison (2026-10-07, counted run t1_shop team): each agent replied with the
requested report line first and one finding line, and the automatic presentation repair ("Present the answer as a
table") replaced every reply; the report lines were lost and all three agents ended unverified. The selector now
stands down when the request states its own reply shape, and for an agent's turn.
"""
from __future__ import annotations

import pytest

from core.presentation_selection import select_presentation

REQUEST = ("Review the three Python files in this folder: pricing.py, stock.py and orders.py. For each file, find the "
           "single most important bug and report it as `file:line — what is wrong — the fix` (one line per file).")
REPLY = (
    "stock.py:17 — can_reserve uses > instead of >= — compare with >=\n"
    "orders.py:25 — a partial cancel never releases stock — release the cancelled quantity\n"
    "pricing.py:35 — the member coupon is applied twice — apply it once"
)


def _context(request: str, **extra):
    return {"conversation_history": [{"role": "user", "content": request}], **extra}


def test_a_reply_in_the_requested_format_is_not_elected_for_a_table():
    record = select_presentation(REPLY, _context(REQUEST))
    assert not record.get("gap_detected"), record
    assert record.get("disabled_by") == "request_format", record


def test_an_agent_turn_is_never_reshaped():
    record = select_presentation(REPLY, _context("Read stock.py and report the bug.", turn_author="agent"))
    assert not record.get("gap_detected") and record.get("disabled_by") == "agent_turn", record


@pytest.mark.parametrize("request_text,states_format", [
    ("Report it as `file:line — what is wrong`.", True),
    ("Begin your reply with one line `RESULT: {...}`.", True),
    ("List the bugs, one line per file.", True),
    ("What are the most important bugs in these files?", False),
], ids=["report-as-template", "begin-reply-with", "one-line-per", "plain-question"])
def test_only_a_request_that_states_its_shape_stands_the_selector_down(request_text, states_format):
    record = select_presentation(REPLY, _context(request_text))
    assert (record.get("disabled_by") == "request_format") is states_format, record
