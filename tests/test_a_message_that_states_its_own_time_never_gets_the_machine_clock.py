"""A message that states a clock reading of its own asks for a time worked out from it, never the machine's clock.

iMac gate52 on a6a5f29b7: "a train leaves at 9:40 and the ride is 2 hours 35 minutes, what time does it get in?" was
answered "Current time is 18:56 EET." by the clock lane. The reading is found by its shape (hours:minutes, or an hour
with a 12-hour marker), in any language. All wordings here are invented for this contract.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.agent_runtime.fast_paths_utility import date_time_fast_path

PIN = datetime(2026, 3, 4, 15, 21, tzinfo=timezone.utc)


def _clock(text: str) -> str | None:
    return date_time_fast_path(None, text, source_surface="api", now_utc=PIN)


@pytest.mark.parametrize("text", [
    "the bus goes at 7:15 and takes 50 minutes, what time do I arrive?",
    "if the film starts at 20:30 and runs 2h 10m, what time is it over?",
    "autobusas išvyksta 6:45, kelionė trunka 3 valandas, kiek valandų atvyks?",
    "der Zug fährt um 14:05 ab und braucht 95 Minuten, wann ist er da?",
    "my shift starts at 11pm and lasts 8 hours, what time does it end?",
    "what time is it 4 hours after 9 a.m.?",
])
def test_a_stated_clock_reading_is_not_answered_from_the_machine_clock(text):
    assert _clock(text) is None


@pytest.mark.parametrize("text", ["what time is it?", "what's the time right now", "current time"])
def test_a_plain_clock_ask_is_still_the_runtimes(text):
    answer = _clock(text) or ""
    assert answer and ":" in answer


def test_a_time_mentioned_after_a_plain_clock_question_leaves_it_to_the_clock():
    assert (_clock("what time is it? the plumber said he comes at 4:30") or "").startswith("Current time is ")


def test_numbers_that_are_no_clock_reading_do_not_decline_a_clock_ask():
    assert _clock("what time is it now? I have 2 hours and 3.50 euros left") is not None
