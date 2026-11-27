"""A reminder asked for after an amount of time from now is timed by the runtime, not by the model's own sum.

iMac gate27 on 601705f2c (qwen3:8b): "przypomnij mi za 15 minut, żeby zadzwonić do mamy", sent at 04:09:10, was given
due_iso 04:09, a time already past by the approval. The reminder tool takes the wait in minutes (`in_minutes`), and the
runtime adds it to its own clock, the way it works out a named weekday (`day`).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.execution import operator_tools


class _Clock:
    def now_utc(self):
        return datetime(2026, 11, 26, 2, 9, 10, tzinfo=timezone.utc)


@pytest.fixture
def fixed_clock(monkeypatch):
    from core import time_authority

    monkeypatch.setattr(time_authority, "CLOCK", _Clock())
    monkeypatch.setattr("core.user_preferences.owner_timezone", lambda: "UTC")


def test_a_wait_in_minutes_sets_the_due_time_from_the_runtime_clock(fixed_clock):
    intent = operator_tools.build_operator_action_intent("schedule_reminder", {
        "note": "zavolat babičce", "due_iso": "2026-11-26T04:09", "in_minutes": 25,
        "text": "připomeň mi za 25 minut, ať zavolám babičce"})
    assert intent.due_iso == "2026-11-26T02:34"
    assert intent.due_weekday is None


def test_the_wait_is_due_on_the_owners_own_wall_clock(fixed_clock, monkeypatch):
    """Read in the owner's zone like a time they type, so it is stored and shown in it (iMac gate47 on 6ce598256: a
    fresh profile's "40 minutes from now" was shown due in UTC on a +02:00 Mac)."""
    monkeypatch.setattr("core.user_preferences.owner_timezone", lambda: "Europe/Vilnius")
    intent = operator_tools.build_operator_action_intent("schedule_reminder", {
        "note": "išimti duoną", "in_minutes": 40, "text": "po 40 minučių primink išimti duoną"})
    assert intent.due_iso == "2026-11-26T04:49"


@pytest.mark.parametrize("wait", ["", None, 0, -5, 2.5, "soon", True, 10 ** 9])
def test_a_wait_that_is_no_whole_number_of_minutes_leaves_the_typed_time(fixed_clock, wait):
    intent = operator_tools.build_operator_action_intent("schedule_reminder", {
        "note": "water the ferns", "due_iso": "2026-11-27T08:00", "in_minutes": wait})
    assert intent.due_iso == "2026-11-27T08:00"


def test_the_wait_reaches_the_when_reader_as_the_moment_it_names(fixed_clock):
    from core.operator.when import when_text_from_iso

    words = when_text_from_iso(operator_tools._due_after_a_wait({"in_minutes": "90"}) or "")
    assert words.startswith("2026-11-26 03:39"), words


def test_the_reminder_tool_offers_the_wait():
    from core.tool_registry import tool_for_intent

    contract = tool_for_intent("operator.schedule_reminder")
    assert "in_minutes" in contract.input_schema
