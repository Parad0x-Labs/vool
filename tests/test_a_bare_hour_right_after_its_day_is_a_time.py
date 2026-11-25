"""A bare hour said right after the day it falls on is a clock time, whatever order follows it.

iMac gate on 7ce02be05 (app from source, qwen3:8b): "and on saturday at 10 remind me to water the balcony plants" was
asked "Which time on saturday?": the word after the 10 read as a noun (an address or a count), so the 10 was not a time.
Which side of noon a bare hour lands on stays the existing rule (7 to 11 morning, 1 to 6 afternoon, a part word wins).
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from core.operator.when import parse_when_expression

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)  # a Tuesday
DAYS = {"wednesday": 25, "thursday": 26, "friday": 27, "saturday": 28, "sunday": 29}
ORDERS = ("remind me to water the balcony plants", "water the plants", "ring grandma about the cake",
          "nudge me to pick up the dry cleaning", "call the plumber pls", "bring the cake")
LEADS = ("", "and ", "oh and ", "also ")


def _wall(hour: int) -> int:
    return hour if hour >= 7 else hour + 12


@pytest.mark.parametrize("seed", range(12))
def test_a_bare_hour_after_its_day_keeps_its_hour(seed):
    rng = random.Random(f"day-hour-{seed}")
    day = rng.choice(list(DAYS))
    hour = rng.randint(1, 11)
    said = f"{rng.choice(LEADS)}{rng.choice(['on ', ''])}{day} at {hour} {rng.choice(ORDERS)}"
    resolved = parse_when_expression(said, now_fn=lambda: NOW)
    assert resolved.ok, (said, resolved.problem)
    assert resolved.due_wall.startswith(f"2026-11-{DAYS[day]}T{_wall(hour):02d}:00"), (said, resolved.due_wall)


@pytest.mark.parametrize("said", [
    "remind me to book the Friday at 10 workshop on monday at 9",
    "remind me to call the Monday at 3 caller back tomorrow at 10",
    "I live at 9 Elm Street",
])
def test_an_event_named_by_its_day_or_an_address_is_still_not_the_time(said):
    resolved = parse_when_expression(said, now_fn=lambda: NOW)
    assert not resolved.due_wall.startswith(("2026-11-27T10", "2026-11-30T15", "2026-11-24T09")), (said, resolved)
