"""A calendar approval card says what will be written, with when (CUT 42).

Gate 49 on the installed app, cases 2 and 3: the card read only "add an event to your calendar · Affects: No
specific path reported" while the call carried the event's title and time. The card now names the event and the day
and time the call's own handler reads from the same arguments. Reading the card also showed the handler's quoted
title stopping at an apostrophe ("Parents' evening" written as "Parents"); a quoted value now closes on the mark it
opened with.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ._pc_calendar_rig import prepare_home

pytestmark = [pytest.mark.pa_beta]

# Friday 27 Nov 2026, 13:00 UTC.
NOW = datetime(2026, 11, 27, 13, 0, tzinfo=timezone.utc)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    prepare_home(tmp_path, monkeypatch, zone="UTC")
    from core import local_operator_actions
    from core.time_authority import CLOCK

    monkeypatch.setitem(vars(CLOCK), "now_utc", lambda: NOW)
    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: NOW.astimezone(ZoneInfo("UTC")))
    yield tmp_path
    monkeypatch.undo()
    time.tzset()


def test_a_local_event_card_names_the_event_and_when(home):
    from core.approval_action_phrases import approval_target_name, localized_action_words

    call = {"title": "Parents' evening", "start_iso": "2026-12-03T17:45", "day": "Thursday", "duration_minutes": 60}
    assert approval_target_name("operator.schedule_calendar_event", call) == "“Parents' evening”, Thu 03 Dec 2026 at 17:45"
    assert localized_action_words("operator.schedule_calendar_event", "en", call) == (
        "add an event to your calendar for “Parents' evening”, Thu 03 Dec 2026 at 17:45")


def test_a_proposed_event_card_says_when_from_the_request(home):
    from core.approval_action_phrases import localized_action_words

    words = localized_action_words("operator.propose_calendar_event", "en",
                                   {"text": "pop the boiler service into my calendar on Monday at 8:15am"})
    assert words.startswith("add an event to your calendar for "), words
    assert "Mon 30 Nov 2026 at 08:15" in words, words


def test_a_card_whose_time_cannot_be_read_says_only_what_it_knows(home):
    from core.approval_action_phrases import localized_action_words

    assert localized_action_words("operator.propose_calendar_event", "en", {"text": "put something on my calendar"}) == (
        "add an event to your calendar")


def test_a_quoted_title_keeps_its_apostrophe():
    from core.operator.parser import _extract_quoted_values

    assert _extract_quoted_values('schedule a meeting "Parents\' evening" on 2026-12-03T17:45 for 60m') == ["Parents' evening"]
    assert _extract_quoted_values("rename 'Q1 plan' to \"Q1 plan (old)\"") == ["Q1 plan", "Q1 plan (old)"]
    assert _extract_quoted_values("it's \"Notes\"") == ["Notes"]
