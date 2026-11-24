"""A clock hour followed by a closing courtesy ("at 8 pls", "at 8 thanks") is still that hour.

Mac gate on e36daa6a9 (installed app): "btw make a reminder to renew my library books tomorrow evening at 8 pls"
was set for 18:00, the bare evening: "pls" after the 8 read as a noun, so the 8 was not a clock time.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from core.closing_words import CLOSING_COURTESY_WORDS
from core.operator.when import parse_when_expression

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("seed", range(6))
def test_an_hour_ending_in_a_courtesy_keeps_its_hour(seed):
    rng = random.Random(f"courtesy-{seed}")
    closing = rng.choice(CLOSING_COURTESY_WORDS)
    hour = rng.randint(5, 11)
    part, wall = rng.choice([("evening", hour + 12), ("morning", hour)])
    said = f"remind me tomorrow {part} at {hour} {closing}" + rng.choice(["", "!", " :)", "?"])
    resolved = parse_when_expression(said, now_fn=lambda: NOW)
    assert resolved.ok and resolved.due_wall.startswith(f"2026-11-25T{wall:02d}:00"), (said, resolved.due_wall)


@pytest.mark.parametrize("said", ["I live at 9 Elm Street", "we meet at 9 people max"])
def test_a_noun_after_the_number_is_still_not_a_time(said):
    assert not parse_when_expression(said, now_fn=lambda: NOW).due_wall.startswith("2026-11-24T09")
