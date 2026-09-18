"""pa_beta_gate -- standalone reminders: complete, edit, repeat, and their controls (rows 20-22).

One delivery surface (the reminder schedule and its dispatcher), driven through the chat seam;
occurrence identity is one row per occurrence; wall-clock repeat across the DST boundary keeps
the local time and moves the UTC instant.

LABELLED: injected clock, isolated home; no live provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._pc_calendar_rig import Clock, prepare_home, sweep

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)  # Saturday 10:00 Vilnius (EEST, UTC+3)
VILNIUS = ZoneInfo("Europe/Berlin")


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a reminder request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending(session_id: str):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session_id, get_connection_fn=get_connection)


def test_repeating_reminder_reschedules_across_dst(home, monkeypatch):
    """ORIGINAL: a daily 17:00 reminder scheduled for the day BEFORE the October DST change
    delivers and schedules exactly one next occurrence: still 17:00 local, now EET, so the UTC
    instant moves from 14:00 to 15:00. Cancelling the next occurrence stops the series."""
    clock = Clock(T0, monkeypatch)
    session = "rem-repeat"
    result = _run("remind me to stretch on 2026-10-24 at 17:00 every day", session_id=session)
    assert result.ok, result.response_text
    assert "repeats every day" in result.response_text, result.response_text
    [row] = _pending(session)
    assert row["due_at_utc"].startswith("2026-10-24T14:00"), row["due_at_utc"]  # 17:00 EEST

    clock.advance(hours=10)  # past 17:00 local on Oct 24
    outcome = sweep(clock)
    assert outcome["delivered"] >= 1 and outcome.get("repeat_scheduled", 0) == 1, outcome
    [nxt] = _pending(session)
    assert nxt["due_at_utc"].startswith("2026-10-25T15:00"), nxt["due_at_utc"]  # 17:00 EET after the change

    stopped = _run(f'cancel reminder {nxt["reminder_id"][:8]}', session_id=session)
    assert stopped.ok, stopped.response_text
    assert _pending(session) == []


def test_complete_edit_and_controls(home, monkeypatch):
    """NOVEL: completing a DELIVERED occurrence records done-ness and says the repeating series
    continues; editing rewords a pending occurrence without moving its time; completing an
    unknown id is a typed refusal; an ambiguous completion asks instead of guessing."""
    clock = Clock(T0, monkeypatch)
    session = "rem-controls"
    _run("remind me to water the plants on 2026-10-24 at 11:00 every week", session_id=session)
    [first] = _pending(session)
    assert first["due_at_utc"].startswith("2026-10-24T08:00"), first["due_at_utc"]

    edited = _run(f'edit reminder {first["reminder_id"][:8]} to say "water the ferns"', session_id=session)
    assert edited.ok, edited.response_text
    [after_edit] = _pending(session)
    assert after_edit["note"] == "water the ferns" and after_edit["due_at_utc"] == first["due_at_utc"]

    clock.advance(hours=4)  # past 11:00 local
    sweep(clock)
    done = _run(f'complete reminder {first["reminder_id"][:8]}', session_id=session)
    assert done.ok and "completed" in done.response_text.lower(), done.response_text
    assert "repeating" in done.response_text, done.response_text  # the series truth is stated

    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    history = list_reminders(session_id=session, include_delivered=True, get_connection_fn=get_connection)
    assert any(row["status"] == "completed" for row in history), history
    [next_occurrence] = _pending(session)  # the series continues: exactly one new occurrence

    unknown = _run("complete reminder deadbeef", session_id=session)
    assert not unknown.ok, unknown.response_text

    # Two live completable rows and no id is a question, not a guess.
    _run("remind me to call back on 2026-10-26 at 09:00", session_id=session)
    ambiguous = _run("complete reminder", session_id=session)
    assert not ambiguous.ok and "unambiguously" in ambiguous.response_text, ambiguous.response_text
