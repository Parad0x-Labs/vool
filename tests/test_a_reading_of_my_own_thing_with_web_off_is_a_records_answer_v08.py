"""A reading of the person's own thing, withheld with Web lookups off, is said as a records answer.

CI #697/#699 (v0.8 line, 94ce6169), the p300 served workflow: "How long is my longest bean row?" (the
saved length retracted) and "What's my heaviest marrow?" (the saved weight deleted) were withheld, and the
notice was rewritten to "Current measured-quantity figures need Web lookups, which are off, so I've left
them out. You can turn them on in Settings, Privacy & Permissions." No web page holds the person's bean
row, so turning the web on would not answer it, and "measured-quantity" is an internal kind name. "How tall
is my tallest sunflower?" already got the records answer; a length and a superlative record now do too.
"""
from __future__ import annotations

import pytest

from core.model_output_guard import records_live_value_notice, unverified_live_value_notice, web_off_live_value_notice
from core.online_asks import reads_an_owned_thing
from core.unsourced_current_claim import RECORDS_WITHDRAWAL_NOTICE


@pytest.mark.parametrize(
    "ask",
    [
        "How long is my longest bean row?",
        "What's my heaviest marrow?",
        "How tall is my tallest sunflower this year?",
        "how old is my oldest apple tree?",
        "what is our biggest pumpkin?",
    ],
)
def test_a_reading_of_an_owned_thing(ask: str) -> None:
    assert reads_an_owned_thing(ask), ask


@pytest.mark.parametrize(
    "ask",
    [
        "how long is the flight to lisbon",
        "what is the weather in my city",
        "how far is my city from paris",
        "can I use a news photo on my blog",
        "how long does it take to boil an egg",
        "what's the biggest city in spain",
    ],
)
def test_not_a_reading_of_an_owned_thing(ask: str) -> None:
    assert not reads_an_owned_thing(ask), ask


def test_the_withheld_notice_becomes_the_records_answer() -> None:
    notice = unverified_live_value_notice(("measured-quantity",), "How long is my longest bean row?")
    assert "not going to state" in notice
    said = records_live_value_notice("Here is what I have.\n\n" + notice)
    assert said == "Here is what I have.\n\n" + RECORDS_WITHDRAWAL_NOTICE
    assert "measured-quantity" not in said and "Web lookups" not in said
    # The web-off line stays for everything else.
    assert "need Web lookups" in web_off_live_value_notice(notice)
