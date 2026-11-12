"""Daemon integration of the reminder runtime (B9 wiring), verified in this
checkout — not asserted. Real module lifecycle (start/idempotent-start/stop),
observable delivery in the runtime-events store under the synthetic
``pa-reminders`` session, boot recovery through the wired worker, and honest
failure observability. Local state only; no OS notifications, no push, no
network.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from core import pa_reminders, runtime_paths
from core.pa_reminder_runtime import (
    REMINDER_SESSION_ID,
    reminder_runtime_status,
    start_reminder_runtime,
    stop_reminder_runtime,
)
from core.runtime_continuity import list_runtime_session_events


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    # NOTE: do NOT touch configure_runtime_continuity_db_path here — the root
    # conftest already binds the runtime-events store to the isolated per-session
    # test DB (runtime_storage_reset re-applies it per test). Resetting it to
    # None repoints event writes at the REAL user data dir, which both leaks the
    # test and contends with any live daemon (measured as a hang).
    yield
    stop_reminder_runtime()
    runtime_paths.configure_runtime_home(None)


def _due(minutes_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _reminder_events() -> list[dict]:
    return list_runtime_session_events(REMINDER_SESSION_ID, limit=50)


def test_worker_delivers_an_observable_runtime_event() -> None:
    pa_reminders.schedule(message="pick up the dry cleaning", due_at_utc=_due())
    result = start_reminder_runtime(interval_seconds=0.2)
    assert result["started"] is True

    deadline = time.monotonic() + 5
    events: list[dict] = []
    while time.monotonic() < deadline:
        events = [e for e in _reminder_events() if e.get("event_type") == "reminder_notification"]
        if events:
            break
        time.sleep(0.05)
    assert events, "the delivered notification never became an observable runtime event"
    event = events[0]
    assert "pick up the dry cleaning" in str(event.get("message") or "")
    # Served rows MERGE details at the top level (continuity's serve shape).
    assert event.get("reminder_id")

    status = reminder_runtime_status()
    assert status["running"] is True and status["stats"]["delivered"] >= 1

    stopped = stop_reminder_runtime()
    assert stopped["stopped"] is True and stopped["was_running"] is True
    assert stopped["stats"]["delivered"] >= 1
    assert reminder_runtime_status() == {"running": False, "stats": {}}


def test_start_is_idempotent_and_reports_the_running_worker() -> None:
    first = start_reminder_runtime(interval_seconds=0.5)
    second = start_reminder_runtime(interval_seconds=0.5)
    assert first["started"] is True
    assert second["started"] is False and second["already_running"] is True
    assert second["status"]["running"] is True
    assert stop_reminder_runtime()["stopped"] is True


def test_stop_without_start_is_safe() -> None:
    result = stop_reminder_runtime()
    assert result == {"stopped": True, "was_running": False, "stats": {}}


def test_boot_recovery_through_the_wired_worker() -> None:
    # The daemon was "down" while a reminder came due: the wired worker's FIRST
    # breath is recover(), so starting it delivers the straggler — observably.
    pa_reminders.schedule(message="water the plants", due_at_utc=_due(minutes_ago=30))
    start_reminder_runtime(interval_seconds=0.2)

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not [
        e for e in _reminder_events() if e.get("event_type") == "reminder_notification"
    ]:
        time.sleep(0.05)
    delivered = [e for e in _reminder_events() if e.get("event_type") == "reminder_notification"]
    assert delivered and "water the plants" in str(delivered[0].get("message") or "")
    rows = pa_reminders.list_reminders(include_closed=True)
    assert rows[0]["status"] == "delivered"
    stop_reminder_runtime()


def test_crash_survivor_recovery_is_observable_and_labeled() -> None:
    # A crash between claim and delivery commit left a row in firing; the wired
    # worker's boot recovery redelivers it — the event carries the duplicate caveat.
    r = pa_reminders.schedule(message="crash straggler", due_at_utc=_due())
    from core.pa_reminders import _claim
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert _claim(conn, r["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.commit()
    finally:
        conn.close()

    start_reminder_runtime(interval_seconds=0.2)
    deadline = time.monotonic() + 5
    events: list[dict] = []
    while time.monotonic() < deadline:
        events = [e for e in _reminder_events() if e.get("event_type") == "reminder_notification"]
        if events:
            break
        time.sleep(0.05)
    assert events
    assert events[0].get("possible_duplicate") is True
    assert events[0].get("delivery_lineage") == "crash_recovery"
    assert "may have been shown before" in str(events[0].get("message") or "")
    stop_reminder_runtime()


def test_missed_reminder_is_announced_as_an_event() -> None:
    pa_reminders.schedule(message="three days stale", due_at_utc=_due(minutes_ago=3 * 24 * 60))
    start_reminder_runtime(interval_seconds=0.2)
    deadline = time.monotonic() + 5
    missed: list[dict] = []
    while time.monotonic() < deadline:
        missed = [e for e in _reminder_events() if e.get("event_type") == "reminder_missed"]
        if missed:
            break
        time.sleep(0.05)
    assert missed and "three days stale" in str(missed[0].get("message") or "")
    rows = pa_reminders.list_reminders(include_closed=True)
    assert rows[0]["status"] == "missed"
    stop_reminder_runtime()


def test_notifier_failure_is_visible_in_status_not_recorded_delivered() -> None:
    # The event store refuses (simulated): the wired notifier raises, the engine
    # records delivery_failed (NOT delivered), the worker surfaces the failure
    # count, and the bounded retry does not spin the CPU.
    import core.pa_reminder_runtime as rt

    pa_reminders.schedule(message="store is down", due_at_utc=_due())

    def refusing(_payload):
        raise RuntimeError("event store refused")

    from core.pa_reminders import ReminderWorker

    worker = ReminderWorker(notify=refusing, interval_seconds=0.2, max_attempts=2)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and worker.stats["delivery_failures"] < 2:
            time.sleep(0.05)
        assert worker.stats["delivery_failures"] >= 1
        rows = pa_reminders.list_reminders(include_closed=True)
        assert rows[0]["status"] == "delivery_failed"  # never 'delivered'
        assert rows[0]["attempts"] == 2  # bounded (max_attempts=2), then stops
    finally:
        assert worker.stop() is True
    assert rt.reminder_runtime_status() == {"running": False, "stats": {}}
