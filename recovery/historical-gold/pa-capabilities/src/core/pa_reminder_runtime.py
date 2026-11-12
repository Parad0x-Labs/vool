"""Daemon lifecycle + observable delivery for the reminder engine (B9 wiring).

One owner process-wide: ``start_reminder_runtime()`` at daemon boot (after
migrations), ``stop_reminder_runtime()`` at shutdown. Delivery is OBSERVABLE by
construction: every notification the worker delivers is appended as a runtime
event under the stable synthetic session ``pa-reminders`` —

* desktop: the events store (``list_runtime_session_events("pa-reminders")``);
* mobile companion: ``notifications.poll`` with ``session="pa-reminders"`` (a
  canonical id passes verbatim through ``_read_session_id``);
* in-process: ``reminder_runtime_status()`` exposes the worker's counters
  (delivered, failures, crash recoveries, misses, last error).

What this wiring does NOT claim: OS-level notifications (the mobile app's
notification layer is separately PARTIAL per its own README) and any external
channel (KAS-owned). The event rows are the delivery record of truth.
"""
from __future__ import annotations

import threading
from typing import Any

#: The stable synthetic session under which reminder notifications are recorded.
#: Deliberately NOT a chat session: reminders are daemon-origin, not turn-origin.
REMINDER_SESSION_ID = "pa-reminders"

_EVENT_REMINDER = "reminder_notification"
_EVENT_MISSED = "reminder_missed"
_EVENT_STARTED = "reminder_runtime_started"
_EVENT_STOPPED = "reminder_runtime_stopped"

_LOCK = threading.Lock()
_WORKER: Any = None  # ReminderWorker | None


def _emit_runtime_event(event_type: str, message: str, details: dict[str, Any]) -> None:
    from core.runtime_task_events import emit_runtime_event

    emit_runtime_event(
        {"runtime_session_id": REMINDER_SESSION_ID},
        event_type=event_type,
        message=message,
        details=details,
    )


def notify_runtime_event(payload: dict[str, Any]) -> None:
    """The worker's notify callback: one durable, observable runtime event per
    delivered notification. Raises only if the event store itself refuses (the
    engine records that as delivery_failed — correct: an unobservable delivery
    is not a successful one)."""
    message = str(payload.get("message") or "").strip()
    due = str(payload.get("due_at_utc") or "")
    notes: list[str] = []
    if payload.get("possible_duplicate"):
        notes.append("may have been shown before (recovered after a restart)")
    lineage = str(payload.get("delivery_lineage") or "")
    if lineage == "crash_recovery":
        notes.append("recovered after a restart")
    elif lineage == "retry":
        notes.append("delivered after a retry")
    suffix = f" ({'; '.join(notes)})" if notes else ""
    event_type = _EVENT_REMINDER
    if payload.get("late") or str(payload.get("status")) == "missed":
        event_type = _EVENT_MISSED
        suffix = suffix or " (missed while the app was not running)"
    _emit_runtime_event(
        event_type,
        f"Reminder: {message} — due {due}{suffix}".strip(),
        {
            "reminder_id": str(payload.get("reminder_id") or ""),
            "task_id": str(payload.get("task_id") or ""),
            "due_at_utc": due,
            "possible_duplicate": bool(payload.get("possible_duplicate")),
            "delivery_lineage": lineage,
            "status": str(payload.get("status") or ""),
        },
    )


def start_reminder_runtime(
    *,
    interval_seconds: float = 30.0,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Start the one reminder worker for this process. Idempotent: a second
    call reports the running worker instead of starting another. Requires the
    ``reminders`` table (run_migrations) — a missing table surfaces in the
    worker's own stats (boot error), never a crashed daemon."""
    global _WORKER
    from core.pa_reminders import ReminderWorker

    # NOTE: never call reminder_runtime_status() inside `with _LOCK:` — it takes
    # the same non-reentrant lock (measured as a hard deadlock on the idempotent
    # second start). Capture the worker under the lock; build the reply outside.
    with _LOCK:
        existing = _WORKER
        if existing is not None and existing.is_alive():
            worker = None
        else:
            worker = ReminderWorker(
                notify=notify_runtime_event,
                interval_seconds=interval_seconds,
                max_attempts=max_attempts,
            )
            _WORKER = worker
    if worker is None:
        return {"started": False, "already_running": True,
                "status": reminder_runtime_status()}
    worker.start()
    try:
        _emit_runtime_event(
            _EVENT_STARTED,
            "Reminder runtime started.",
            {"interval_seconds": interval_seconds, "max_attempts": max_attempts},
        )
    except Exception:
        pass  # the marker is observability sugar, never a boot dependency
    return {"started": True, "already_running": False, "status": reminder_runtime_status()}


def stop_reminder_runtime(*, timeout: float = 5.0) -> dict[str, Any]:
    """Stop the worker and report its final counters. Idempotent; safe when
    never started."""
    global _WORKER
    with _LOCK:
        worker = _WORKER
        _WORKER = None
    if worker is None:
        return {"stopped": True, "was_running": False, "stats": {}}
    stopped = worker.stop(timeout=timeout)
    stats = dict(worker.stats)
    try:
        _emit_runtime_event(_EVENT_STOPPED, "Reminder runtime stopped.", {"clean": bool(stopped)})
    except Exception:
        pass
    return {"stopped": bool(stopped), "was_running": True, "stats": stats}


def reminder_runtime_status() -> dict[str, Any]:
    """Observable in-process truth about the worker (counters + liveness)."""
    with _LOCK:
        worker = _WORKER
    if worker is None:
        return {"running": False, "stats": {}}
    return {
        "running": worker.is_alive(),
        "stats": dict(worker.stats),
        "interval_seconds": worker.interval_seconds,
        "max_attempts": worker.max_attempts,
    }


__all__ = [
    "REMINDER_SESSION_ID",
    "notify_runtime_event",
    "reminder_runtime_status",
    "start_reminder_runtime",
    "stop_reminder_runtime",
]
