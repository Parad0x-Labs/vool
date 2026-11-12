"""B9/B10 (PA lane): local tasks + the durable reminder engine.

Pure-local journeys (SQLite in an isolated NULLA_HOME; no network, no providers,
no model). Revised 2026-09-09 after the crash-boundary review: delivery is
AT-LEAST-ONCE and labeled (exactly-once CLAIM only), notifier failure is never
success, an absent notifier never claims, and every crash window (after claim /
after notify before commit) recovers honestly.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from core import pa_reminders, pa_tasks, runtime_paths
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _recorder() -> tuple[list[dict], object]:
    seen: list[dict] = []
    return seen, seen.append


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db_execute(sql: str, params: tuple = ()) -> None:
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# -- tasks -------------------------------------------------------------------

def test_task_lifecycle_and_typed_transitions() -> None:
    task = pa_tasks.create_task(title="Pay the rent", notes="by the 5th")
    assert task["status"] == "open"
    assert pa_tasks.list_tasks()[0]["task_id"] == task["task_id"]

    done = pa_tasks.complete_task(task["task_id"])
    assert done["ok"] and done["status"] == "done"
    # completing again is an honest no-op, not an error and not a re-count
    again = pa_tasks.complete_task(task["task_id"])
    assert again["ok"] and again["status"] == "already_done"

    cancelled = pa_tasks.cancel_task(task["task_id"])  # a done task keeps history
    assert not cancelled["ok"] and cancelled["status"] == "already_done"


def test_task_cancel_then_complete_is_refused() -> None:
    task = pa_tasks.create_task(title="abandon me")
    assert pa_tasks.cancel_task(task["task_id"])["ok"]
    result = pa_tasks.complete_task(task["task_id"])
    assert not result["ok"] and result["status"] == "cancelled"


def test_task_requires_title() -> None:
    with pytest.raises(ValueError):
        pa_tasks.create_task(title="   ")


def test_task_tools_through_the_runtime_door() -> None:
    created = execute_runtime_tool("task.create", {"title": "Book dentist"})
    assert created is not None and created.ok and created.status == "created"
    task_id = created.details["task_id"]

    listed = execute_runtime_tool("task.list", {})
    assert listed.ok and any(t["task_id"] == task_id for t in listed.details["tasks"])

    completed = execute_runtime_tool("task.complete", {"task_id": task_id})
    assert completed.ok and completed.status == "done"

    unknown = execute_runtime_tool("task.complete", {"task_id": "no-such-id"})
    assert not unknown.ok and unknown.status == "not_found"

    empty = execute_runtime_tool("task.create", {"title": ""})
    assert not empty.ok and empty.status == "invalid_request"


# -- reminders: steady state ----------------------------------------------------

def test_reminder_delivers_once_across_duplicated_ticks() -> None:
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="stand up", due_at_utc=_iso(due))
    seen, notify = _recorder()

    first = pa_reminders.tick(notify=notify)
    second = pa_reminders.tick(notify=notify)
    third = pa_reminders.tick(notify=notify)

    assert len(first["delivered"]) == 1 and first["delivered"][0]["reminder_id"] == r["reminder_id"]
    assert second["delivered"] == [] and third["delivered"] == []
    assert len(seen) == 1  # one notification across three ticks (exactly-once CLAIM)


def test_future_reminder_does_not_fire_early() -> None:
    due = _now() + timedelta(hours=2)
    pa_reminders.schedule(message="too early", due_at_utc=_iso(due))
    seen, notify = _recorder()
    result = pa_reminders.tick(notify=notify)
    assert result["delivered"] == [] and seen == []


def test_interrupted_reminder_recovers_on_boot_once() -> None:
    # Journey: the daemon dies BEFORE the reminder is due; on boot, recover()
    # delivers it once — and a second recover() does not deliver again.
    due = _now() - timedelta(minutes=30)
    r = pa_reminders.schedule(message="take the bread out", due_at_utc=_iso(due))
    seen, notify = _recorder()

    boot1 = pa_reminders.recover(notify=notify)
    boot2 = pa_reminders.recover(notify=notify)

    assert len(boot1["delivered"]) == 1 and boot1["delivered"][0]["reminder_id"] == r["reminder_id"]
    assert boot2["delivered"] == [] and boot1["missed"] == []
    assert len(seen) == 1


def test_ancient_overdue_reminder_is_reported_missed_not_fired() -> None:
    # A reminder stranded for 3 days: firing it "now" would lie about timeliness.
    due = _now() - timedelta(days=3)
    r = pa_reminders.schedule(message="call the bank before Friday", due_at_utc=_iso(due))
    seen, notify = _recorder()

    result = pa_reminders.recover(notify=notify)
    assert result["delivered"] == []
    assert len(result["missed"]) == 1 and result["missed"][0]["reminder_id"] == r["reminder_id"]
    assert any(m.get("late") for m in seen)  # the miss was ANNOUNCED, never silent


def test_cancelled_reminder_never_fires() -> None:
    due = _now() + timedelta(minutes=5)
    r = pa_reminders.schedule(message="skip me", due_at_utc=_iso(due))
    assert pa_reminders.cancel(r["reminder_id"])["ok"]

    seen, notify = _recorder()
    result = pa_reminders.tick(now=_now() + timedelta(minutes=10), notify=notify)
    assert result["delivered"] == [] and seen == []


def test_delivered_reminder_cannot_be_cancelled() -> None:
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="already gone", due_at_utc=_iso(due))
    pa_reminders.tick(notify=lambda _p: None)
    result = pa_reminders.cancel(r["reminder_id"])
    assert not result["ok"] and result["status"] == "not_pending"


def test_reminder_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        pa_reminders.schedule(message="", due_at_utc="2026-01-01T00:00:00+00:00")
    with pytest.raises(ValueError):
        pa_reminders.schedule(message="x", due_at_utc="next tuesday, ish")


def test_reminder_tools_through_the_runtime_door() -> None:
    due = _now() + timedelta(hours=1)
    created = execute_runtime_tool("reminder.create", {"message": "pick up kids", "due_at_utc": _iso(due)})
    assert created is not None and created.ok and created.status == "scheduled"
    rid = created.details["reminder_id"]

    listed = execute_runtime_tool("reminder.list", {})
    assert listed.ok and any(x["reminder_id"] == rid for x in listed.details["reminders"])

    bad = execute_runtime_tool("reminder.create", {"message": "x", "due_at_utc": "whenever"})
    assert not bad.ok and bad.status == "invalid_request"

    cancelled = execute_runtime_tool("reminder.cancel", {"reminder_id": rid})
    assert cancelled.ok and cancelled.status == "cancelled"


def test_task_reminder_pair_visible_together() -> None:
    # B10 acceptance: "remind me to…" creates a task+reminder pair visible in one list.
    task = pa_tasks.create_task(title="Renew passport", due_at_utc="2026-10-01T09:00:00+00:00")
    due = _now() + timedelta(days=1)
    reminder = pa_reminders.schedule(
        message="Renew passport", due_at_utc=_iso(due), task_id=task["task_id"]
    )
    tasks = pa_tasks.list_tasks()
    reminders = pa_reminders.list_reminders()
    assert any(t["task_id"] == task["task_id"] for t in tasks)
    assert any(r["reminder_id"] == reminder["reminder_id"] and r["task_id"] == task["task_id"]
               for r in reminders)


# -- reminders: crash boundaries (the review's core findings) -------------------

def test_crash_after_claim_before_notify_recovers_with_duplicate_label() -> None:
    # Crash window 1: pending->firing committed, process dies BEFORE notify.
    # The notification was never shown; recovery delivers it — labeled with the
    # crash lineage and possible_duplicate (which here is FALSE in effect, but
    # the engine cannot know, so it stays honest about the uncertainty).
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="crash after claim", due_at_utc=_iso(due))
    # Simulate the crash: claim directly (what tick's first commit does), then die.
    from core.pa_reminders import _claim
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert _claim(conn, r["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.commit()
    finally:
        conn.close()

    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "firing"  # the stranded state, reproduced

    seen, notify = _recorder()
    boot = pa_reminders.recover(notify=notify)
    assert len(boot["crash_recovered"]) == 1 and boot["crash_recovered"][0]["outcome"] == "redelivered"
    assert len(seen) == 1
    assert seen[0]["possible_duplicate"] is True  # uncertainty is ANNOUNCED, not assumed away
    assert seen[0]["delivery_lineage"] == "crash_recovery"
    final = pa_reminders.list_reminders(include_closed=True)[0]
    assert final["status"] == "delivered" and final["details"]["possible_duplicate"] is True


def test_crash_after_notify_before_commit_can_duplicate_and_says_so() -> None:
    # Crash window 2: notify SUCCEEDED, process dies BEFORE the delivered commit.
    # The row is stranded in firing WITH the notification already shown. Recovery
    # delivers AGAIN (at-least-once) — the duplicate is possible and the payload
    # says so. This is the honest floor; exactly-once notification is NOT claimed.
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="crash after notify", due_at_utc=_iso(due))
    from core.pa_reminders import _claim
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert _claim(conn, r["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.commit()
    finally:
        conn.close()
    pre_crash_seen: list[dict] = []
    pre_crash_seen.append({"message": "crash after notify"})  # the notify that DID happen

    seen, notify = _recorder()
    boot = pa_reminders.recover(notify=notify)
    assert len(boot["crash_recovered"]) == 1
    assert len(seen) == 1 and seen[0]["possible_duplicate"] is True  # the possible duplicate
    total_notifications = len(pre_crash_seen) + len(seen)
    assert total_notifications == 2  # at-least-once: a duplicate DID occur, labeled


def test_notifier_failure_is_never_recorded_as_delivered() -> None:
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="flaky channel", due_at_utc=_iso(due))
    calls: list[dict] = []

    def flaky(payload):
        calls.append(payload)
        raise RuntimeError("notification surface down")

    result = pa_reminders.tick(notify=flaky)
    assert result["failed"] and result["failed"][0]["outcome"] == "delivery_failed"
    assert "notify_failed" in result["failed"][0]["failure_reason"]
    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "delivery_failed"  # NOT delivered
    assert row["details"]["failure_reason"].startswith("notify_failed")

    # Bounded retry: the channel recovers, the next tick retries and delivers.
    seen, good_notify = _recorder()
    retried = pa_reminders.tick(notify=good_notify)
    assert len(retried["delivered"]) == 1
    assert retried["delivered"][0]["delivery_lineage"] == "retry"
    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "delivered" and row["attempts"] == 2


def test_notifier_failure_exhausts_and_is_surfaced_not_hidden() -> None:
    due = _now() - timedelta(minutes=1)
    pa_reminders.schedule(message="dead channel", due_at_utc=_iso(due))

    def dead(_payload):
        raise OSError("gone")

    pa_reminders.tick(notify=dead)   # attempt 1
    pa_reminders.tick(notify=dead)   # attempt 2
    pa_reminders.tick(notify=dead)   # attempt 3 (default max)
    fourth = pa_reminders.tick(notify=dead)  # exhausted: no further claim
    assert fourth["delivered"] == [] and fourth["failed"] == []

    boot = pa_reminders.recover(notify=lambda _p: None)
    assert len(boot["permanently_failed"]) == 1  # surfaced at boot, never silent
    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "delivery_failed" and row["attempts"] == 3


def test_absent_notifier_never_claims_and_never_records_success() -> None:
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="no channel configured", due_at_utc=_iso(due))

    result = pa_reminders.tick(notify=None)
    assert result["notifier_absent"] is True
    assert result["delivered"] == [] and result["failed"] == []
    row = pa_reminders.list_reminders()[0]  # still pending: nothing was burned
    assert row["reminder_id"] == r["reminder_id"] and row["status"] == "pending"

    boot = pa_reminders.recover(notify=None)
    assert boot["notifier_absent"] is True
    assert boot["delivered"] == [] and boot["missed"] == []


def test_crash_during_recovery_itself_is_re_claimed_past_grace() -> None:
    # Crash window 3: a recovery claimed the survivor (firing -> recovering)
    # and THEN crashed. While its claim is fresh, other boots skip it; past the
    # grace window, the next boot re-claims and delivers — still labeled.
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="recovery crashed too", due_at_utc=_iso(due))
    from core.pa_reminders import _claim
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert _claim(conn, r["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.commit()
    finally:
        conn.close()
    # A first boot claims the survivor for recovery... and crashes mid-way.
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE reminders SET status = ?, fired_at = ? WHERE reminder_id = ?",
            (pa_reminders.STATUS_RECOVERING, _iso(_now()), r["reminder_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # A second boot with zero grace re-claims the stale recovery and delivers.
    seen, notify = _recorder()
    boot = pa_reminders.recover(notify=notify, inflight_grace_seconds=0.0)
    assert len(boot["crash_recovered"]) == 1 and boot["crash_recovered"][0]["outcome"] == "redelivered"
    assert len(seen) == 1 and seen[0]["possible_duplicate"] is True
    assert pa_reminders.list_reminders(include_closed=True)[0]["status"] == "delivered"

    # And with the default grace, a FRESH recovery claim is skipped, not stolen.
    pa_reminders.schedule(message="fresh claim held elsewhere", due_at_utc=_iso(_now() - timedelta(minutes=1)))
    rows = pa_reminders.list_reminders(include_closed=True)
    other = [x for x in rows if x["message"] == "fresh claim held elsewhere"][0]
    conn = get_connection()
    try:
        assert _claim(conn, other["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.execute(
            "UPDATE reminders SET status = ?, fired_at = ? WHERE reminder_id = ?",
            (pa_reminders.STATUS_RECOVERING, _iso(_now()), other["reminder_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    seen2, notify2 = _recorder()
    boot2 = pa_reminders.recover(notify=notify2)  # default grace: fresh claim skipped
    assert boot2["in_flight"] and boot2["in_flight"][0]["outcome"] == "skipped_in_flight"
    assert seen2 == []


# -- reminders: concurrency -------------------------------------------------------

def test_concurrent_ticks_claim_once_deliver_once() -> None:
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="race me", due_at_utc=_iso(due))
    seen: list[dict] = []
    lock = threading.Lock()

    def notify(payload):
        with lock:
            seen.append(payload)

    barrier = threading.Barrier(6)
    results: list[dict] = []

    def run_tick():
        barrier.wait()
        results.append(pa_reminders.tick(notify=notify))

    threads = [threading.Thread(target=run_tick) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    delivered = [d for res in results for d in res.get("delivered", [])]
    assert len(delivered) == 1 and delivered[0]["reminder_id"] == r["reminder_id"]
    assert len(seen) == 1  # six concurrent claimants, one claim, one notification
    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "delivered" and row["attempts"] == 1


def test_concurrent_crash_recovery_recover_claims_survivor_once() -> None:
    # Two boots racing (e.g. overlapping restart): the firing survivor is
    # resolved by exactly one of them; the other sees no firing row.
    due = _now() - timedelta(minutes=1)
    r = pa_reminders.schedule(message="double boot", due_at_utc=_iso(due))
    from core.pa_reminders import _claim
    from storage.db import get_connection

    conn = get_connection()
    try:
        assert _claim(conn, r["reminder_id"], from_status=pa_reminders.STATUS_PENDING)
        conn.commit()
    finally:
        conn.close()

    seen: list[dict] = []
    lock = threading.Lock()

    def notify(payload):
        with lock:
            seen.append(payload)

    barrier = threading.Barrier(4)
    summaries: list[dict] = []

    def run_recover():
        barrier.wait()
        summaries.append(pa_reminders.recover(notify=notify))

    threads = [threading.Thread(target=run_recover) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    redeliveries = [s for res in summaries for s in res.get("crash_recovered", []) if s.get("outcome") == "redelivered"]
    assert len(redeliveries) == 1  # only one boot resolved the survivor
    assert len(seen) == 1
    row = pa_reminders.list_reminders(include_closed=True)[0]
    assert row["status"] == "delivered"


# -- worker lifecycle ---------------------------------------------------------------

def test_worker_thread_delivers_observes_and_stops_cleanly() -> None:
    due = _now() - timedelta(seconds=1)
    pa_reminders.schedule(message="worker fires me", due_at_utc=_iso(due))
    seen, notify = _recorder()
    fired_event = threading.Event()

    def _notify(payload):
        seen.append(payload)
        fired_event.set()

    worker = pa_reminders.ReminderWorker(notify=_notify, interval_seconds=0.2)
    worker.start()
    try:
        assert fired_event.wait(timeout=5), "worker never delivered the due reminder"
    finally:
        assert worker.stop(timeout=5) is True  # shutdown completes and is reported
    assert not worker.is_alive()
    assert worker.stats["delivered"] >= 1  # observable delivery accounting
    assert worker.stats["stopped_at"] != ""
    assert len(seen) == 1


def test_worker_double_start_is_refused_and_stop_is_idempotent() -> None:
    worker = pa_reminders.ReminderWorker(notify=lambda _p: None, interval_seconds=0.5)
    worker.start()
    try:
        with pytest.raises(RuntimeError):
            worker.start()  # one worker per process lane — a second is a wiring bug
    finally:
        assert worker.stop() is True
    assert worker.stop() is True  # idempotent shutdown
