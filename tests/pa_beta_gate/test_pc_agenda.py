"""pa_beta_gate -- agenda, bounded search and multi-calendar availability (product rows 5-6).

Driven at the served-turn seam: parse_operator_action_intent + dispatch_operator_action, with two
loopback CalDAV fixture accounts chosen in Settings (the settings routes' own authority), so the
agenda reads exactly what the alert schedule would.

LABELLED: disposable loopback CalDAV fixtures; injected clock; no live provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 Vilnius
WORK_CAL = "/calendars/work/"
PERSONAL_CAL = "/calendars/personal/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    return prepared


@pytest.fixture
def servers(home):
    """Two accounts on two servers, both selected: work (with a location+URL event) and personal."""
    server_a, state_a, base_a, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    server_b, state_b, base_b, _ = start_caldav_fixture(calendars={PERSONAL_CAL: "Personal"})
    state_a.seed_event(WORK_CAL, "standup@fixture", summary="Stand-up", start=T0 + timedelta(hours=2), minutes=30)
    state_a.seed_event(WORK_CAL, "review@fixture", summary="Design review",
                       start=T0 + timedelta(days=1, hours=3), minutes=60,
                       location="Room 5", url="https://join.fixture.test/review")
    state_b.seed_event(PERSONAL_CAL, "dentist@fixture", summary="Dentist", start=T0 + timedelta(hours=4), minutes=60)
    from core.operator import calendar_accounts

    for base, label, cal in ((base_a, "Work account", WORK_CAL), (base_b, "Personal account", PERSONAL_CAL)):
        account = calendar_accounts.add_account(provider="caldav", base_url=base, label=label)
        assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
        assert calendar_accounts.select_calendar(account["account_id"], cal, selected=True)["ok"]
        calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    yield state_a, state_b
    server_a.shutdown(); server_a.server_close()
    server_b.shutdown(); server_b.server_close()


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed an agenda/calendar request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_today_agenda_spans_both_selected_calendars(servers, monkeypatch):
    """ORIGINAL: 'what do I have today' reads every opted-in selected calendar, in local time,
    with calendar attribution, location and the https join link; a cancelled event is marked."""
    state_a, state_b = servers
    Clock(T0, monkeypatch)
    result = _run("What do I have on my calendar today?", session_id="agenda-today")
    assert result.ok, result.response_text
    text = result.response_text
    assert "Stand-up" in text and "Dentist" in text, text
    assert "Work" in text and "Personal" in text, text
    assert "12:30" in text, text  # 09:00 UTC stand-up = 12:30 Vilnius
    assert "Room 5" not in text, "tomorrow's event is outside today's window"
    assert text.count("join.fixture.test") == 0  # the join link is on tomorrow's event, not today's
    assert result.details["zone"] == "Europe/Athens"
    assert {row["calendar"] for row in result.details["events"]} == {"Work", "Personal"}


def test_week_agenda_date_range_and_bounded_search(servers, monkeypatch):
    """NOVEL: 'this week' covers Mon-Sun including tomorrow's design review with its location and
    https join link; a bounded title search finds one event and reports its window; an unreadable
    range is a typed question, and a word with no match is an honest no-match."""
    state_a, state_b = servers
    Clock(T0, monkeypatch)
    week = _run("show me my agenda for this week", session_id="agenda-week")
    assert week.ok, week.response_text
    assert "Design review" in week.response_text and "Room 5" in week.response_text, week.response_text
    assert "https://join.fixture.test/review" in week.response_text, week.response_text

    search = _run('search my calendar for "dentist"', session_id="agenda-search")
    assert search.ok and "Dentist" in search.response_text, search.response_text
    assert search.details["events"] and search.details["events"][0]["calendar"] == "Personal"
    assert not search.details["truncated"]

    missing = _run('search my calendar for "q4 board offsite"', session_id="agenda-search-2")
    assert missing.ok and "No event matching" in missing.response_text, missing.response_text

    unreadable = _run("show my agenda", session_id="agenda-ask")
    assert not unreadable.ok and "Which days" in unreadable.response_text, unreadable.response_text

    date_range = _run("agenda from 2026-09-22 to 2026-09-22", session_id="agenda-range")
    assert date_range.ok and "Design review" in date_range.response_text and "Stand-up" not in date_range.response_text, date_range.response_text


def test_availability_counts_busy_across_both_calendars(servers, monkeypatch):
    """Row 6: a free-slot check on the work calendar counts the personal calendar's Dentist as
    busy, and says which other calendars were read."""
    state_a, state_b = servers
    Clock(T0, monkeypatch)
    result = _run("Check today for a free 30-minute slot", session_id="avail-multi")
    assert result.ok, result.response_text
    options = result.details["options"]
    assert options, result.response_text
    busy_start, busy_end = T0 + timedelta(hours=4), T0 + timedelta(hours=5)  # Dentist, personal calendar
    for option in options:
        start = datetime.fromisoformat(option["start_utc"])
        end = datetime.fromisoformat(option["end_utc"])
        standup = (T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30))
        assert not (start < standup[1] and standup[0] < end), f"option overlaps stand-up: {option}"
        assert not (start < busy_end and busy_start < end), f"option overlaps the personal calendar's Dentist: {option}"
    assert "other chosen calendars" in result.response_text and "Dentist" in result.response_text, result.response_text
    assert result.details["other_calendar_count"] == 1
