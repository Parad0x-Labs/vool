"""Reminder parsing, listing and delivery share injected UTC even after wall time moves on."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2001, 1, 15, 7, tzinfo=timezone.utc)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions, time_authority

    class LaterWallClock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2040, 1, 15, 18, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    # Keep the system-clock seam later than every requested date, independently of
    # today's date. Inject ONLY UTC; zoned callers must derive from that authority.
    monkeypatch.setattr(time_authority, "datetime", LaterWallClock)
    monkeypatch.setattr(local_operator_actions, "datetime", LaterWallClock)
    return Clock(T0, monkeypatch)


def _run(text):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, text
    return dispatch_operator_action(intent, task_id="clock-task", session_id="clock-session")


@pytest.mark.parametrize("text", [
    "remind me on 2001-01-15 at 11:00 to water the plants",
    "remind me today at 11am to water the plants",
    "remind me in 2 hours to water the plants",
])
def test_reminder_workflow_uses_injected_utc_after_wall_time_has_passed(clock, text):
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    created = _run(text)
    assert created.ok, created.response_text
    [row] = list_reminders(session_id="clock-session", get_connection_fn=get_connection)
    assert row["due_at_utc"] == "2001-01-15T09:00:00+00:00"
    assert row["created_at"] == T0.isoformat()
    assert row["note"] == "water the plants"
    assert "OVERDUE" not in _run("list reminders").response_text

    refused = _run(f"move reminder {row['reminder_id'][:8]} to today at 8am")
    assert not refused.ok and refused.details["reason"] == "due_time_passed"
    [unchanged] = list_reminders(session_id="clock-session", get_connection_fn=get_connection)
    assert unchanged["due_at_utc"] == row["due_at_utc"]

    # Exercise the default production clock wiring, not a separately injected sweep.
    dispatcher = ReminderDispatcher(get_connection_fn=get_connection, sleep_fn=lambda _: None)
    assert dispatcher.sweep_once()["delivered"] == 0
    clock.advance(hours=3)
    assert "OVERDUE" in _run("list reminders").response_text
    assert dispatcher.sweep_once()["delivered"] == 1
    assert dispatcher.sweep_once()["delivered"] == 0
    [delivered] = list_reminders(session_id="clock-session", include_delivered=True, get_connection_fn=get_connection)
    assert delivered["status"] == "delivered"
    assert delivered["delivered_at"] == clock().replace("Z", "+00:00")


def test_default_parser_resolves_the_current_clock_at_call_time(clock):
    from core.operator.when import parse_when_expression

    parsed = parse_when_expression("in 20 minutes")
    assert parsed.ok, parsed
    assert parsed.due_at_utc == (T0 + timedelta(minutes=20)).isoformat()
    assert parsed.details["read_at_utc"] == T0.isoformat()


def test_calendar_draft_timestamp_uses_the_same_utc_clock(clock):
    from core.local_operator_actions import _render_ics

    draft = _render_ics(title="Water plants", start_iso="2001-01-15T09:00:00+00:00",
                        end_iso="2001-01-15T09:30:00+00:00")
    assert "DTSTAMP:20010115T070000Z" in draft
