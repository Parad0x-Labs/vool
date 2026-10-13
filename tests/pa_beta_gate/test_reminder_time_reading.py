"""pa_beta_gate -- reading a reminder's time and subject the way people say them.

A clock time already passed today, a past date and "yesterday" are refused instead of firing at
once or rolling into the future; a bare 24-hour time after a day word is read; a when-clause that
leads the subject ("tomorrow at 9am to call the bank") is not the subject.

LABELLED: injected clock (both the UTC and the zoned door), isolated home; no live provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)  # Saturday 10:00 in Europe/Athens (EEST)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    from core.time_authority import CLOCK

    fixed = Clock(T0, monkeypatch)
    monkeypatch.setattr(CLOCK, "now_for_timezone", lambda name: fixed.now.astimezone(ZoneInfo(name)))
    return fixed


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a reminder request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending(session_id: str):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session_id, get_connection_fn=get_connection)


@pytest.mark.parametrize(
    "text",
    [
        "remind me today at 8am to call the bank",          # earlier today
        "remind me yesterday at 9am to call the bank",      # a past day word
        "remind me on 2026-10-01 at 09:00 to call the bank",  # a past date
    ],
)
def test_a_time_already_passed_is_refused_and_nothing_is_stored(clock, text):
    result = _run(text, session_id="passed")
    assert not result.ok and result.status == "invalid_request", result.response_text
    assert result.details.get("reason") == "due_time_passed", result.details
    assert "already passed" in result.response_text
    assert _pending("passed") == []


def test_future_times_are_unchanged_controls(clock):
    later_today = _run("remind me today at 11am to call the bank", session_id="ctl")
    assert later_today.ok, later_today.response_text
    relative = _run("remind me in 20 minutes to check the oven", session_id="ctl")
    assert relative.ok, relative.response_text
    due = sorted(row["due_at_utc"][:16] for row in _pending("ctl"))
    assert due == ["2026-10-24T07:20", "2026-10-24T08:00"], due


def test_a_bare_24_hour_time_after_a_day_word_is_read(clock):
    result = _run("remind me tomorrow 15:00 to call the bank", session_id="h24")
    assert result.ok, result.response_text
    [row] = _pending("h24")
    assert row["due_at_utc"].startswith("2026-10-25T13:00"), row["due_at_utc"]  # 15:00 EET after the change
    assert row["note"] == "call the bank", row["note"]


@pytest.mark.parametrize(
    "text, subject",
    [
        ("remind me tomorrow at 9am to call the bank", "call the bank"),
        ("remind me at 5pm to take my pills", "take my pills"),
        ("remind me in 2 hours to check the oven", "check the oven"),
        ("remind me to call mom at 5pm", "call mom"),  # trailing when-clause, as before
    ],
)
def test_the_subject_is_the_thing_to_do_not_the_schedule(clock, text, subject):
    session = f"subj-{uuid.uuid4().hex[:6]}"
    result = _run(text, session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["note"] == subject, row["note"]


def test_moving_a_reminder_into_the_past_is_refused(clock):
    created = _run("remind me tomorrow at 9am to call the bank", session_id="mv")
    assert created.ok, created.response_text
    [row] = _pending("mv")
    moved = _run(f"move reminder {row['reminder_id'][:8]} to today at 8am", session_id="mv")
    assert not moved.ok and moved.details.get("reason") == "due_time_passed", moved.response_text
    [after] = _pending("mv")
    assert after["due_at_utc"] == row["due_at_utc"]


def test_the_parser_still_reads_a_past_time_for_callers_that_need_one():
    """A calendar search or move may name a past time; only reminder callers refuse it."""
    from core.operator.when import has_passed, parse_when_expression

    reading = parse_when_expression("yesterday at 9:00 Europe/Athens", now_fn=lambda: T0)
    assert reading.ok and reading.due_at_utc.startswith("2026-10-23T06:00"), reading
    assert has_passed(reading)
