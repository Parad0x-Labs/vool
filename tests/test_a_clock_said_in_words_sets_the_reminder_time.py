"""A clock said in words sets the reminder's time, and leaves the reminder's text.

Gate 68 on a35e0e475 (packaged copy, qwen3:8b): "set a reminder for 9 january at half past seven in the
morning to book the boiler service" was set for 09:00 on 9 January, and its text kept "half past seven in
the morning". The parser knew "7:30 in the morning" but no clock said in words. Clock words are read as the
digits people mean; a word that is not part of a clock ("walked past seven shops") stays a word.

LABELLED: injected clock (Wednesday 28 Oct 2026, 10:00 Europe/Athens), isolated home; no live provider.
"""
from __future__ import annotations

import uuid

import pytest

from core.operator.when import normalize_time_words
from tests.test_reminder_times_people_say import _pending, _run, clock

__all__ = ["clock"]  # fixture this module uses

pytestmark = [pytest.mark.pa_beta]


@pytest.mark.parametrize("text, due_prefix, subject", [
    # 07:30 EET on 9 Jan 2027 is 05:30 UTC.
    ("set a reminder for 9 january at half past seven in the morning to book the boiler service",
     "2027-01-09T05:30", "book the boiler service"),
    ("remind me tomorrow at quarter past eight to water the ferns", "2026-10-29T06:15", "water the ferns"),
    ("remind me tomorrow at quarter to nine in the evening to lock the shed", "2026-10-29T18:45", "lock the shed"),
    ("remind me on friday at ten past six pm to call the garage", "2026-10-30T16:10", "call the garage"),
    ("remind me tomorrow at seven o'clock to feed the cat", "2026-10-29T05:00", "feed the cat"),
    ("remind me tomorrow at twenty to twelve to ring the school", "2026-10-29T09:40", "ring the school"),
])
def test_a_clock_said_in_words_is_the_time_it_says(clock, text, due_prefix, subject):
    session = f"worded-{uuid.uuid4().hex[:6]}"
    result = _run(text, session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["due_at_utc"].startswith(due_prefix), (row["due_at_utc"], row["note"])
    assert subject in row["note"], row["note"]
    for word in ("past", "quarter", "o'clock", "half", " to twelve"):
        assert word not in row["note"].replace(subject, ""), row["note"]


@pytest.mark.parametrize("text", [
    "I walked past seven shops",
    "half an hour from now",
    "tell Tom we lost ten to nine",
    "the meeting ran a quarter past nine hours",
])
def test_words_that_are_not_a_clock_stay_words(text):
    normalized = normalize_time_words(text)
    assert not any(ch.isdigit() for ch in normalized.replace("in half an hour", "")), normalized


def test_a_score_in_the_reminder_text_is_not_its_time(clock):
    session = f"score-{uuid.uuid4().hex[:6]}"
    result = _run("remind me tomorrow at 6pm to tell Tom we lost ten to nine", session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["due_at_utc"].startswith("2026-10-29T16:00"), (row["due_at_utc"], row["note"])
    assert "ten to nine" in row["note"], row["note"]
