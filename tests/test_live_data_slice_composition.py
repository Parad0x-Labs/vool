"""Slice answers must ride out with the lane that ends the turn (live defect 2026-08-29).

The reported turn's USD→EUR conversion was computed and recorded by the
currency slice lane, then silently dropped when the typed live-data lane ended
the turn with its markets table. This family pins the composition contract:
answers for clauses the plan cannot serve are composed into the reply, in the
order the user wrote them; clauses the plan serves are never answered twice.
"""

from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.answer_coverage import (
    COVERAGE_CONTEXT_KEY,
    FAMILY_CURRENCY,
    FAMILY_MARKET_QUOTE,
    record_slice_answer,
)

_MARKET_TABLE = "Markets\n\nAsset    Price    24h Change\nBitcoin    USD 77,668.00    -3.86%"


def _context_with_recorded(*families: str, response: str = "10.01 EUR is 1,000 RUB.") -> dict:
    context: dict = {}
    for family in families:
        record_slice_answer(
            context,
            text="1000 rub to eur",
            family=family,
            response=response,
            reason=f"{family}_slice_answers",
        )
    return context


def test_recorded_currency_answer_is_prepended_to_the_market_table():
    context = _context_with_recorded(FAMILY_CURRENCY)
    composed = VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, context)
    assert "10.01 EUR" in composed
    assert composed.index("10.01 EUR") < composed.index("Bitcoin"), (
        "the recorded conversion must precede the table (user's clause order)"
    )
    assert "Bitcoin" in composed, "the plan's own answer must remain intact"


def test_market_family_answers_are_not_double_served():
    context = _context_with_recorded(FAMILY_MARKET_QUOTE, response="Bitcoin is $77,668.")
    composed = VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, context)
    assert "recorded twice" not in composed
    assert "$77,668" not in composed, (
        "a clause the plan serves must not be answered twice"
    )


def test_two_recorded_answers_keep_their_record_order():
    context: dict = {}
    record_slice_answer(
        context,
        text="what time is it",
        family="assistant_identity",
        response="It is 04:13.",
        reason="identity_slice_answers",
    )
    record_slice_answer(
        context,
        text="1000 rub to eur",
        family=FAMILY_CURRENCY,
        response="10.01 EUR is 1,000 RUB.",
        reason="currency_slice_answers",
    )
    composed = VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, context)
    assert composed.index("04:13") < composed.index("10.01 EUR")


def test_identical_responses_are_not_duplicated():
    context = _context_with_recorded(FAMILY_CURRENCY, FAMILY_CURRENCY)
    composed = VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, context)
    assert composed.count("10.01 EUR") == 1


def test_no_coverage_record_leaves_the_table_untouched():
    assert VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, {}) == _MARKET_TABLE
    assert VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, None) == _MARKET_TABLE


def test_empty_rendered_reply_stays_empty_no_ghost_answers():
    # If the plan produced nothing, prepending recorded slices would invent a
    # reply shape the fail-closed lane never intended; that path handles
    # slices itself via the unavailable result.
    context = _context_with_recorded(FAMILY_CURRENCY)
    assert VoolAgent._prepend_unserved_slice_answers("", context) == ""


def test_recorded_answer_without_response_text_is_skipped():
    context = _context_with_recorded(FAMILY_CURRENCY, response="   ")
    assert VoolAgent._prepend_unserved_slice_answers(_MARKET_TABLE, context) == _MARKET_TABLE
