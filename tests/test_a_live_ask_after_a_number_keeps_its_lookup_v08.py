"""A pointer back ("of it", "is that") after a message with a number stays a lookup when it asks for a present reading.

Independent review of the job 19 commits (C1): any ask of twelve words or fewer with "is/of/does it" counted as pointing
back at the chat when the previous user message held any digit, so "what is the share price of it now?" after "i bought
20 shares of Vexa Corp last week" became a direct answer with no tools, with Web lookups on or off, and the model gave a
live price from nothing. All names, places and figures are invented.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.online_asks import asks_about_a_chat_referent

FLATS = "i'm comparing two flats: one on Birch Row for 950 a month and one on Kell Lane for 1100 a month"


def _context(earlier: str, asked: str) -> dict:
    return {"conversation_history": [{"role": "user", "content": earlier}, {"role": "assistant", "content": "ok"},
                                     {"role": "user", "content": asked}]}


@pytest.mark.parametrize(
    ("earlier", "asked"),
    [
        ("i bought 20 shares of Vexa Corp last week", "what is the share price of it now?"),
        ("the pharmacy at 12 Kell Lane", "is it open on sundays?"),
        ("there's a lamp I like, model 4040 from Ostra", "how much does it cost today?"),
        (FLATS, "is that one still available?"),
        (FLATS, "is the second one available now?"),
    ],
)
def test_a_present_reading_is_not_the_chats(earlier, asked):
    assert not asks_about_a_chat_referent(asked, _context(earlier, asked))


@pytest.mark.parametrize(
    ("earlier", "asked"),
    [
        ("i bought 20 shares of Vexa Corp last week", "what is the price of it?"),  # a count, not a price, was given
        ("the pharmacy at 12 Kell Lane", "what does it cost?"),  # an address number is no amount
    ],
)
def test_a_digit_that_is_no_amount_is_nothing_to_point_back_at(earlier, asked):
    assert not asks_about_a_chat_referent(asked, _context(earlier, asked))


def test_the_share_price_after_a_number_is_not_held_to_the_chat():
    earlier, asked = "i bought 20 shares of Vexa Corp last week", "what is the share price of it now?"
    need = requirements_for(asked, source_context=_context(earlier, asked))
    assert "user_stipulated_frame" not in need.reason_codes


@pytest.mark.parametrize("asked", ["how much is the second one?", "what does that one cost?", "and the cheaper one?"])
def test_a_pointer_back_at_amounts_just_given_is_still_the_chats(asked):
    assert asks_about_a_chat_referent(asked, _context(FLATS, asked))
    assert asks_about_a_chat_referent(asked, _context("the blue kettle is $40 and the red one is $55", asked))
