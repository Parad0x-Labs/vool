"""A reminder delay said as an amount "from now" is read like "in N minutes".

iMac gate35 on 584863ad5: "set a reminder for 25 minutes from now to call the plumber" was answered "I could not read a
date or time in that" and nothing was set. Wordings are fresh.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.operator.when import normalize_time_words, parse_when_expression

NOW = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(("said", "minutes"), [
    ("ping me 40 minutes from now about the laundry", 40),
    ("set a reminder for 3 hours from now to feed the cat", 180),
    ("remind me in ten minutes from now to check the oven", 10),
    ("an hour from now remind me to stretch", 60),
    ("remind me after 2 days from now to water the fern", 2 * 24 * 60),
    # iMac gate38: "half an hour from now" was set an hour later with "half" as the reminder's text.
    ("put a reminder half an hour from now to stir the soup", 30),
    ("an hour and a half from now tell me to leave", 90),
    # iMac gate40: "a quarter of an hour from now" was set an hour later with "a quarter of" in the reminder's text.
    ("nudge me a quarter of an hour from now to take the bread out", 15),
    ("three quarters of an hour from now remind me to call the garage", 45),
    ("remind me in a quarter of an hour to check the kettle", 15),
    ("in three quarters of an hour tell me to move the car", 45),
])
def test_an_amount_from_now_is_a_delay(said: str, minutes: int) -> None:
    when = parse_when_expression(normalize_time_words(said), now_fn=lambda: NOW)
    assert when.ok, when.problem
    due = datetime.fromisoformat(when.due_at_utc)
    assert abs((due - NOW) - timedelta(minutes=minutes)) < timedelta(seconds=1)


def test_from_now_without_an_amount_is_left_as_it_was() -> None:
    assert normalize_time_words("from now on call me Sam") == "from now on call me Sam"


def test_half_an_hour_from_now_leaves_no_half_behind() -> None:
    assert "half" not in normalize_time_words("remind me half an hour from now to stir the soup").replace("in half an hour", "")


def test_a_quarter_of_an_hour_from_now_leaves_no_quarter_behind() -> None:
    said = normalize_time_words("nudge me a quarter of an hour from now to take the bread out")
    assert said.count("quarter") == 1 and "in a quarter of an hour" in said


def test_a_part_of_the_hour_with_more_after_it_is_asked_about() -> None:
    when = parse_when_expression(normalize_time_words("remind me in a quarter of an hour and a half to stir"),
                                 now_fn=lambda: NOW)
    assert not when.ok and "minutes" in when.problem
