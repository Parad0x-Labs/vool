"""pa_beta_gate -- dates written in words and event lengths, read the way people type them.

Found by the starter-skills review on 2026-10-13: "Thursday 5 November 18:30" booked next
Thursday, "5 November at 18:30" booked tomorrow, "1.5 hours" became 5 hours and "18:30 to
20:00" became the 30-minute default. Every case here goes through the served seam (parse +
dispatch) against the disposable CalDAV service, or through the reminder store.

LABELLED: injected clock (Tuesday 2026-10-13 19:00 UTC = 22:00 Europe/Athens), isolated home,
disposable local CalDAV service; no live provider.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 13, 19, 0, tzinfo=timezone.utc)
HOME_CAL = "/calendars/home/"
# 5 November 2026 18:30 in Europe/Athens (EET, UTC+2 after the October change).
NOV5_1830 = datetime(2026, 11, 5, 16, 30, tzinfo=timezone.utc)


def _pin_clock(monkeypatch):
    from core import local_operator_actions
    from core.time_authority import CLOCK

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    monkeypatch.setattr(CLOCK, "now_utc", lambda: T0)
    monkeypatch.setattr(CLOCK, "now_for_timezone", lambda name: T0.astimezone(ZoneInfo(name)))


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    _pin_clock(monkeypatch)
    server, state, base_url, _port = start_caldav_fixture(calendars={HOME_CAL: "Home"})
    monkeypatch.setenv("VOOL_CALENDAR_PROVIDER", "caldav")
    monkeypatch.setenv("VOOL_CALENDAR_URL", base_url)
    monkeypatch.setenv("VOOL_CALENDAR_ID", HOME_CAL)
    monkeypatch.setenv("VOOL_CALENDAR_TIMEOUT", "10")
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def reminders(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    _pin_clock(monkeypatch)


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"operator parser missed the request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _events(state):
    return state.snapshot()[HOME_CAL]


@pytest.mark.parametrize(
    "text",
    [
        'propose "Dinner" Thursday 5 November 18:30 to 20:00',
        'propose "Dinner" 5 November at 18:30 for 1.5 hours',
        'propose "Dinner" on November 5th at 18:30 for 1h30',
        'propose "Dinner" on 5 Nov 2026 at 18:30 for 90 minutes',
        'propose "Dinner" on 5 November from 18:30 until 20:00',
        'propose "Dinner" on the 5th of November, 6:30pm to 8pm',
        'propose "Dinner" on 2026-11-05 18:30 for 1.5 hours',  # the ISO form that already worked
    ],
)
def test_a_date_in_words_and_its_length_propose_the_asked_event(calendar, text):
    proposal = _run(text, session_id=f"p-{uuid.uuid4().hex[:6]}")
    assert proposal.status == "approval_required", proposal.response_text
    assert datetime.fromisoformat(proposal.details["start_utc"]) == NOV5_1830, proposal.details
    assert datetime.fromisoformat(proposal.details["end_utc"]) == NOV5_1830 + timedelta(minutes=90), proposal.details
    assert "Duration: 90 minutes" in proposal.response_text
    assert _events(calendar) == {}, "a proposal is not an event"


def test_the_approved_event_lands_on_the_asked_day_and_length(calendar):
    session = "approve-words"
    proposal = _run('propose "Dinner" Thursday 5 November 18:30 to 20:00', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    approval = _run(f"approve calendar {proposal.details['action_id']}", session_id=session)
    assert approval.ok and approval.details["verified"] is True, approval.response_text
    event = _events(calendar)[approval.details["uid"]]
    assert event["start_utc"].startswith("2026-11-05T16:30"), event
    # Stored in the user's zone: 20:00 Europe/Athens is 18:00 UTC, 90 minutes after the start.
    assert re.search(r"(?m)^DTEND;TZID=Europe/Athens:20261105T200000\r?$", event["ics"]), event["ics"]


@pytest.mark.parametrize(
    "text, words",
    [
        ('propose "Dinner" Friday 5 November 18:30', "is not Friday"),       # weekday and date disagree
        ('propose "Dinner" tomorrow 5 November 18:30', "is not tomorrow"),   # day word and date disagree
        ('propose "Dinner" 31 November at 18:30', "not a real date"),
        ('propose "Dinner" 5 November', "Which time on 5 November"),
        ('propose "Dinner" in May 2027 at 18:30', "Which day in May 2027"),  # a month with no day is asked, not dropped
    ],
)
def test_a_date_that_cannot_be_placed_is_asked_about_and_nothing_is_proposed(calendar, text, words):
    result = _run(text, session_id=f"n-{uuid.uuid4().hex[:6]}")
    assert not result.ok and result.status == "invalid_request", result.response_text
    assert words in result.response_text, result.response_text
    assert "action_id" not in result.details
    assert _events(calendar) == {}


def test_a_reminder_on_a_date_in_words_is_due_that_day_with_its_subject(reminders):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    for text in (
        "remind me on 5 November at 18:30 to call the bank",
        "remind me to call the bank on Thursday 5 November at 18:30",
        "remind me to call the bank on November 5th at 6:30pm",
    ):
        session = f"r-{uuid.uuid4().hex[:6]}"
        result = _run(text, session_id=session)
        assert result.ok, result.response_text
        [row] = list_reminders(session_id=session, get_connection_fn=get_connection)
        assert row["due_at_utc"].startswith("2026-11-05T16:30"), (text, row["due_at_utc"])
        assert row["note"] == "call the bank", (text, row["note"])


def test_a_date_already_passed_this_year_is_read_as_said_and_a_reminder_refuses_it(reminders):
    """With no year, a date is this year's: never rolled into next year by a guess."""
    from core.operator.reminders import list_reminders
    from core.operator.when import has_passed, parse_when_expression
    from storage.db import get_connection

    reading = parse_when_expression("5 March at 10:00 Europe/Athens", now_fn=lambda: T0)
    assert reading.ok and reading.due_at_utc.startswith("2026-03-05T08:00"), reading
    assert has_passed(reading)

    result = _run("remind me on 5 March at 10:00 to renew the passport", session_id="past")
    assert not result.ok and result.details.get("reason") == "due_time_passed", result.response_text
    assert list_reminders(session_id="past", get_connection_fn=get_connection) == []

    with_year = _run("remind me on 5 March 2027 at 10:00 to renew the passport", session_id="next-year")
    assert with_year.ok, with_year.response_text
    [row] = list_reminders(session_id="next-year", get_connection_fn=get_connection)
    assert row["due_at_utc"].startswith("2027-03-05T08:00"), row["due_at_utc"]


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("for 1.5 hours", 90),
        ("for 0.25h", 15),
        ("for 1h30", 90),
        ("for 1 hour 30 minutes", 90),
        ("for 2 hours and 15 mins", 135),
        ("for 90 minutes", 90),
        ("for 45m", 45),
        ("a half-hour", 30),
        ("on 5 May", None),          # a date is not a length
        ("at 18:30", None),
    ],
)
def test_an_explicit_duration_is_read_exactly(text, minutes):
    from core.operator.calendar_provider import parse_duration_minutes

    assert parse_duration_minutes(text) == minutes


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("18:30 to 20:00", 90),
        ("from 18:30 until 20:00", 90),
        ("18:30-20:00", 90),
        ("6pm to 8pm", 120),
        ("22:00 to 01:00", 180),          # runs past midnight
        ("18:30 to 20:00 for 2 hours", 120),  # an explicit duration wins over the range
        ("at 18:30 with 5 people", None),
        ("18:30 to 5 people", None),      # not a clock time after "to"
        ("18:30 to 25:00", None),
    ],
)
def test_a_clock_range_is_the_event_length_but_not_an_availability_length(text, minutes):
    from core.operator.calendar_provider import parse_duration_minutes, parse_event_length_minutes

    assert parse_event_length_minutes(text) == minutes
    if "for " not in text:
        # In "check Tuesday 14:00 to 16:00 for a slot" a range is the window to search, not the slot's length.
        assert parse_duration_minutes(text) is None
