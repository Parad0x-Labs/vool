"""pa_beta_gate -- durable recurring-reminder recovery (correction repair 1; rows 20-22).

The delivered-to-next-occurrence gap must be repairable by a later sweep: a transient storage
failure or a crash between delivery and successor creation cannot silently end a repeating
reminder. Exactly one successor per occurrence (unique schedule key), no redelivery of the
already delivered notification, failures visible in the sweep outcome.

LABELLED: real reminder store and dispatcher (isolated home), injected clock and delivery fn,
injected one-shot storage failure. No live provider.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)  # Saturday; occurrence was 17:00 UTC


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _dispatcher(clock):
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from storage.db import get_connection

    return ReminderDispatcher(get_connection_fn=get_connection, sleep_fn=lambda _s: None, now_fn=clock)


def _rows():
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id="repair", include_delivered=True, get_connection_fn=get_connection)


def _seed(clock, monkeypatch, *, due="2026-10-24 17:00"):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    text = f'remind me to stretch on {due.replace(" ", " at ")} every day'
    intent = parse_operator_action_intent(text)
    assert intent is not None
    result = dispatch_operator_action(intent, task_id="t-repair", session_id="repair")
    assert result.ok, result.response_text
    return result.details["reminder_id"]


def test_storage_failure_between_delivery_and_successor_is_repaired_by_a_later_sweep(home, monkeypatch):
    """ORIGINAL (the review probe): a reschedule failure during the delivering sweep leaves the
    parent delivered with zero children and a VISIBLE failure; a fresh dispatcher sweep repairs
    exactly one successor, redelivers nothing, and reports zero errors once repaired."""
    clock = Clock(T0, monkeypatch)
    parent = _seed(clock, monkeypatch)

    from core.operator import reminders as operator_reminders

    original = operator_reminders.reschedule_repeat
    monkeypatch.setattr(operator_reminders, "reschedule_repeat",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("injected storage failure")))
    failed_sweep = _dispatcher(clock).sweep_once()
    assert failed_sweep["delivered"] == 1, failed_sweep
    assert failed_sweep.get("repeat_repair_failed", 0) >= 1, "the failure is visible in the sweep outcome"
    assert [row for row in _rows() if row["status"] == "scheduled"] == []

    monkeypatch.setattr(operator_reminders, "reschedule_repeat", original)
    repaired = _dispatcher(clock).sweep_once()  # fresh dispatcher, same durable store
    assert repaired.get("repeat_repaired", 0) == 1 and repaired["delivered"] == 0, repaired
    assert repaired["errors"] == 0, repaired
    [successor] = [row for row in _rows() if row["status"] == "scheduled"]
    from zoneinfo import ZoneInfo

    successor_local = datetime.fromisoformat(successor["due_at_utc"]).astimezone(ZoneInfo("Europe/Athens"))
    assert (successor_local.hour, successor_local.minute) == (17, 0), successor["due_at_utc"]  # wall time kept across the Oct 25 DST change
    # No redelivery of the parent: its notification fired exactly once.
    delivered = []
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from storage.db import get_connection

    redelivery = ReminderDispatcher(get_connection_fn=get_connection,
                                    deliver_fn=lambda row: delivered.append(row["reminder_id"]) or {"ok": True},
                                    sleep_fn=lambda _s: None, now_fn=clock).sweep_once()
    assert redelivery["delivered"] == 0 and delivered == [], (redelivery, delivered)


def test_completion_before_due_and_cancellation_of_a_series(home, monkeypatch):
    """NOVEL: completing a SCHEDULED occurrence early keeps the series going from the NEXT period
    (no duplicate at the same time); cancelling the next occurrence stops it for good; editing a
    pending occurrence rewords it without touching the schedule."""
    clock = Clock(T0, monkeypatch)
    future = T0 + timedelta(days=2, hours=1)
    _seed(clock, monkeypatch, due=future.strftime("%Y-%m-%d %H:%M"))
    [pending] = [row for row in _rows() if row["status"] == "scheduled"]
    original_due = pending["due_at_utc"]

    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    edited = dispatch_operator_action(
        parse_operator_action_intent(f'edit reminder {pending["reminder_id"][:8]} to say "stretch and hydrate"'),
        task_id="t2", session_id="repair")
    assert edited.ok, edited.response_text

    completed = dispatch_operator_action(
        parse_operator_action_intent(f'complete reminder {pending["reminder_id"][:8]}'),
        task_id="t3", session_id="repair")
    assert completed.ok and "repeating" in completed.response_text, completed.response_text

    outcome = _dispatcher(clock).sweep_once()
    assert outcome.get("repeat_repaired", 0) == 1, outcome
    [successor] = [row for row in _rows() if row["status"] == "scheduled"]
    assert successor["note"] == "stretch and hydrate", "the series carries the edited wording"
    assert successor["due_at_utc"] > original_due, (successor["due_at_utc"], original_due)

    cancelled = dispatch_operator_action(
        parse_operator_action_intent(f'cancel reminder {successor["reminder_id"][:8]}'),
        task_id="t4", session_id="repair")
    assert cancelled.ok, cancelled.response_text
    again = _dispatcher(clock).sweep_once()
    assert again.get("repeat_repaired", 0) == 0 and again.get("repeat_repair_failed", 0) == 0, again
    assert [row for row in _rows() if row["status"] == "scheduled"] == [], "a cancelled successor ends the series"


def test_concurrent_sweepers_create_exactly_one_successor(home, monkeypatch):
    """CONTROL: two sweepers racing the same delivered parent produce one successor, and a
    repaired gap is not repaired twice."""
    clock = Clock(T0, monkeypatch)
    parent = _seed(clock, monkeypatch)
    outcome = _dispatcher(clock).sweep_once()
    assert outcome["delivered"] == 1 and outcome.get("repeat_scheduled", 0) == 1, outcome

    # Simulate the crash window: remove the successor, leave the parent delivered.
    from storage.db import get_connection

    conn = get_connection()
    conn.execute("DELETE FROM reminder_requests WHERE schedule_key = ?", (f"repeat:{parent}",))
    conn.commit()
    conn.close()

    first, second = _dispatcher(clock).sweep_once(), _dispatcher(clock).sweep_once()
    successors = [row for row in _rows() if row["status"] == "scheduled"]
    assert len(successors) == 1, (first, second, successors)
    assert first.get("repeat_repaired", 0) + second.get("repeat_repaired", 0) == 1
