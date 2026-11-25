"""The time in a named place is a runtime fact, asked in any language.

iMac gate23 on 97d3db22e (qwen3:8b, Web lookups off): "Wie spät ist es gerade in Sydney?" never reached the date and
time lane, whose readers know English clock phrasings only; the turn's judge picked the web, and the web-off line was
the whole answer. The web question now says the clock is not the internet, and the turn's facts carry the time in a
place the message names, so the model answers any wording from the clock.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.parametrize("ask, place, wall", [
    ("Quelle heure est-il à Tokyo en ce moment ?", "Tokyo", "07:40"),
    ("Hoe laat is het nu in New York?", "New York", "17:40"),
    ("Która jest teraz godzina w Sydney?", "Sydney", "09:40"),
])
def test_the_time_in_a_named_place_is_a_fact_of_the_turn(ask, place, wall):
    from core.prompt_normalizer import _named_place_times_text

    line = _named_place_times_text(ask, datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc))
    assert place in line and wall in line, line


def test_a_message_naming_no_place_adds_nothing():
    from core.prompt_normalizer import _named_place_times_text

    assert _named_place_times_text("could you sum up my notes?", datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc)) == ""


def test_the_web_question_says_the_clock_is_not_the_internet():
    from core.intent_arbiter import _web_ask_prompt

    prompt = _web_ask_prompt("Zeit in Kairo?")
    assert "the computer's clock gives it" in prompt


@pytest.mark.parametrize("said", ["we are off over the easter weekend", "my friend Sofia has an oral exam", "wake me at 7"])
def test_a_place_name_that_is_an_everyday_word_adds_nothing(said):
    from core.prompt_normalizer import _named_place_times_text

    assert _named_place_times_text(said, datetime(2026, 11, 25, 22, 40, tzinfo=timezone.utc)) == ""
