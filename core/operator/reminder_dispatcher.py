"""The due-reminder dispatcher: the delivery half of the scheduling vertical.

One background thread, started only by the served process (apps.vool_api_server), that
repeatedly:

1. demotes crash-interrupted dispatches to ``delivery_uncertain`` (honest reconciliation --
   unknown outcomes are REPORTED, never re-fired and never claimed delivered);
2. claims due reminders with a CAS (``scheduled`` -> ``dispatching``) so two sweeps can
   never both deliver; cancellations after a claim cannot promise to stop its effect;
3. fires the delivery effect and records the PROVEN outcome.

THE EFFECT, stated exactly: a delivered chat reminder is (a) marked ``delivered`` with a receipt
row in ``reminder_requests``, (b) recorded in the audit log, (c) appended to the owning
session's conversation log as a distinct reminder-delivery event, so the next chat view (and
``GET /api/reminders``) shows it, and (d) recorded as a typed item in the notification centre the
bell reads. A due calendar alert (source kind ``calendar_alert``) has no owning chat: it is recorded
in the notification centre only. An alert that comes due after its event already started is marked
``expired`` and collected into one catch-up notice. None of this is an OS push notification or an
external message; the native notification bridge reads the centre separately.

Only an explicit no-effect receipt permits retry. Exceptions and crashes leave an
uncertain outcome instead of replaying a potentially delivered reminder. Exactly-once
delivery is never promised.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core import audit_logger
from core.operator import reminders as operator_reminders
from core.operator.when import format_due_time

logger = logging.getLogger(__name__)

DEFAULT_SWEEP_INTERVAL_SECONDS = 15.0
DEFAULT_STALE_DISPATCH_SECONDS = 120.0
MAX_DELIVER_PER_SWEEP = 25


class ReminderDispatcher:
    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
        stale_dispatch_seconds: float = DEFAULT_STALE_DISPATCH_SECONDS,
        get_connection_fn: Callable[[], Any],
        deliver_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        sleep_fn: Callable[[float], None],
        now_fn: Callable[[], str] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._interval = max(1.0, float(interval_seconds))
        self._stale_seconds = float(stale_dispatch_seconds)
        self._get_connection_fn = get_connection_fn
        self._deliver_fn = deliver_fn or self._deliver_default
        self._sleep_fn = sleep_fn
        from core.time_authority import CLOCK
        self._now_fn = now_fn or (lambda: CLOCK.now_utc().isoformat())
        self._monotonic_fn = monotonic_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- one sweep, separately callable so boot recovery and tests use the SAME code ----

    def sweep_once(self) -> dict[str, Any]:
        """One pass: reconcile stale dispatches, repair recurring gaps, then claim+deliver.

        The recurring-gap repair is what makes a repeating reminder durable: any delivered or
        completed occurrence whose successor row is missing (a storage failure or crash in the
        window after delivery) gets its successor created here, at most once (unique schedule
        key). Returns counters; never raises (a sweep error is logged and retried).
        """
        outcome: dict[str, Any] = {"uncertain": 0, "delivered": 0, "retry_scheduled": 0, "cancel_raced": 0, "errors": 0, "expired": 0,
                                   "repeat_repaired": 0, "repeat_repair_failed": 0}
        expired_rows: list[dict[str, Any]] = []
        try:
            stale = operator_reminders.demote_stale_dispatching(
                older_than_seconds=self._stale_seconds,
                now_fn=self._now_fn,
                get_connection_fn=self._get_connection_fn,
            )
            outcome["uncertain"] = len(stale)
            for row in stale:
                audit_logger.log(
                    "reminder_delivery_uncertain",
                    target_id=str(row.get("reminder_id")),
                    target_type="reminder",
                    details={"note": str(row.get("note") or "")[:200], "due_at_utc": row.get("due_at_utc")},
                )
                try:
                    self._deliver_fn({**row, "delivery_status": "delivery_uncertain"})
                except Exception:
                    logger.exception("reminder uncertain-outcome notice failed for %s", row.get("reminder_id"))
        except Exception:
            logger.exception("reminder staleness reconciliation failed")
            outcome["errors"] += 1

        try:
            repair = operator_reminders.repair_repeating_gaps(
                now_fn=self._now_fn, get_connection_fn=self._get_connection_fn,
            )
            outcome["repeat_repaired"] += repair["repaired"]
            outcome["repeat_repair_failed"] += repair["failed"]
            for failure in repair["failures"]:
                audit_logger.log(
                    "reminder_repeat_repair_failed",
                    target_id=failure["reminder_id"],
                    target_type="reminder",
                    details={"reason": failure["reason"]},
                )
        except Exception:
            logger.exception("recurring reminder gap repair failed")
            outcome["errors"] += 1

        try:
            due_rows = operator_reminders.due_reminders(
                now_utc=self._now_fn(),
                limit=MAX_DELIVER_PER_SWEEP,
                get_connection_fn=self._get_connection_fn,
            )
        except Exception:
            logger.exception("reminder due-query failed")
            return outcome

        for row in due_rows:
            reminder_id = str(row.get("reminder_id") or "")
            try:
                if not operator_reminders.claim_for_dispatch(
                    reminder_id, get_connection_fn=self._get_connection_fn,
                    now_fn=self._now_fn, due_before_utc=self._now_fn(),
                ):
                    # Someone else claimed it, or a cancel won the race. Either way this
                    # sweep does not deliver it.
                    outcome["cancel_raced"] += 1
                    continue
            except Exception:
                logger.exception("reminder claim failed for %s", reminder_id)
                outcome["errors"] += 1
                continue

            refreshed = operator_reminders.load_reminder(reminder_id, get_connection_fn=self._get_connection_fn)
            if refreshed is None or str(refreshed.get("status")) != "dispatching":
                outcome["cancel_raced"] += 1
                continue
            try:
                receipt = self._deliver_fn(refreshed)
                ok = bool(receipt.get("ok"))
            except Exception as exc:
                logger.exception("reminder delivery failed for %s", reminder_id)
                operator_reminders.mark_delivery_uncertain(
                    reminder_id, error=str(exc), get_connection_fn=self._get_connection_fn,
                    now_fn=self._now_fn,
                )
                outcome["uncertain"] += 1
                continue
            if not ok and receipt.get("effect_state") == "expired":
                # A calendar alert whose event already started (or left the calendar) is never shown late as if
                # it were current. Its outcome is recorded; one catch-up notice collects the ones the user missed.
                if operator_reminders.mark_expired(
                    reminder_id, reason=str(receipt.get("reason") or "expired"),
                    get_connection_fn=self._get_connection_fn, now_fn=self._now_fn,
                ):
                    outcome["expired"] += 1
                    if receipt.get("catch_up"):
                        expired_rows.append(refreshed)
                continue
            if not ok:
                # Only an explicit no-effect receipt permits a retry. A generic failure
                # cannot establish whether the append happened before acknowledgement failed.
                retryable = receipt.get("effect_state") == "not_applied"
                transition = operator_reminders.mark_delivery_failed if retryable else operator_reminders.mark_delivery_uncertain
                transition(
                    reminder_id, error=str(receipt.get("reason") or "delivery_refused"),
                    get_connection_fn=self._get_connection_fn, now_fn=self._now_fn,
                )
                outcome["retry_scheduled" if retryable else "uncertain"] += 1
                continue
            delivered = operator_reminders.mark_delivered(
                reminder_id,
                delivery_receipt=receipt,
                get_connection_fn=self._get_connection_fn,
                now_fn=self._now_fn,
            )
            if not delivered:
                # The row left ``dispatching`` between claim and completion (a cancel won).
                # The effect already happened; record it honestly in the audit log.
                audit_logger.log(
                    "reminder_delivered_but_cancelled_midflight",
                    target_id=reminder_id,
                    target_type="reminder",
                    details={"note": str(refreshed.get("note") or "")[:200]},
                )
            else:
                outcome["delivered"] += 1
                # A delivered repeating reminder schedules its OWN next occurrence (one row per
                # occurrence). A failure here is recorded, never hidden; the delivery itself
                # already happened and is not undone. The gap repair below recreates any
                # successor this window lost, so this failure is recoverable, not final.
                try:
                    repeat = operator_reminders.reschedule_repeat(
                        reminder_id, get_connection_fn=self._get_connection_fn, now_fn=self._now_fn,
                    )
                except Exception as exc:
                    outcome["repeat_repair_failed"] = outcome.get("repeat_repair_failed", 0) + 1
                    audit_logger.log(
                        "reminder_repeat_reschedule_failed",
                        target_id=reminder_id,
                        target_type="reminder",
                        details={"error": type(exc).__name__},
                    )
                else:
                    if repeat.get("ok") and repeat.get("status") == "scheduled":
                        outcome["repeat_scheduled"] = outcome.get("repeat_scheduled", 0) + 1
            audit_logger.log(
                "reminder_delivered",
                target_id=reminder_id,
                target_type="reminder",
                details={
                    "note": str(refreshed.get("note") or "")[:200],
                    "due_at_utc": refreshed.get("due_at_utc"),
                    "overdue": bool(str(refreshed.get("due_at_utc") or "") < self._now_fn().replace("+00:00", "")) or None,
                },
            )
        if expired_rows:
            try:
                from core.operator import notification_center

                notification_center.record_catch_up(expired_rows, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)
            except Exception:
                logger.exception("calendar catch-up notice failed")
                outcome["errors"] += 1
        return outcome

    def _deliver_default(self, row: dict[str, Any]) -> dict[str, Any]:
        return deliver_scheduled_item(row, now_fn=self._now_fn, get_connection_fn=self._get_connection_fn)

    # -- thread lifecycle ---------------------------------------------------------------

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vool-reminder-dispatcher", daemon=True)
        self._thread.start()
        logger.info("Reminder dispatcher started (interval=%ss)", self._interval)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        # First sweep immediately: reminders that came due while the process was down are
        # delivered late (with their overdue state visible), never silently dropped.
        while not self._stop.is_set():
            try:
                self.sweep_once()
            except Exception:
                logger.exception("reminder sweep failed")
            self._stop.wait(self._interval)


def _deliver_to_session_log(row: dict[str, Any]) -> dict[str, Any]:
    """Append a typed reminder artifact through the existing transcript authority.

    Artifact identity allows idle chat refresh without replacing a live turn,
    and keeps background delivery out of dialogue and preference mining.
    """
    session_id = str(row.get("session_id") or "")
    if not session_id:
        return {"ok": False, "reason": "reminder has no owning session", "effect_state": "not_applied"}
    note = str(row.get("note") or "")
    due_wall = format_due_time(
        str(row.get("due_at_utc") or ""), str(row.get("tz_name") or ""),
        str(row.get("due_wall") or ""),
    )
    status = str(row.get("delivery_status") or "delivered")
    if status == "delivery_uncertain":
        text = (
            f"⏰ Reminder (delivery uncertain — an attempt was interrupted): {note} "
            f"(was due {due_wall}). Check whether you already saw this."
        )
    else:
        text = f"⏰ Reminder: {note} (due {due_wall})"
    from core.persistent_memory import append_assistant_artifact_event

    written = append_assistant_artifact_event(
        session_id=session_id,
        text=text,
        artifact_kind="reminder_delivery",
        artifact={"reminder_id": str(row.get("reminder_id") or ""), "status": status},
    )
    if not written:
        return {"ok": False, "reason": "session reminder was not appended", "effect_state": "not_applied"}
    return {"ok": True, "effect": "session_conversation_log", "text": text, "reminder_id": str(row.get("reminder_id") or "")}


def _decode_payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def deliver_scheduled_item(row: dict[str, Any], *, now_fn: Callable[[], str], get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    """The default effect for one claimed schedule row, by its source kind."""
    if str(row.get("source_kind") or "reminder") == "calendar_alert":
        return _deliver_calendar_alert(row, now_fn=now_fn, get_connection_fn=get_connection_fn)
    receipt = _deliver_to_session_log(row)
    if receipt.get("ok"):
        _record_reminder_in_notification_centre(row, receipt, now_fn=now_fn, get_connection_fn=get_connection_fn)
    return receipt


def _record_reminder_in_notification_centre(row: dict[str, Any], receipt: dict[str, Any], *, now_fn: Callable[[], str],
                                            get_connection_fn: Callable[[], Any]) -> None:
    """The bell's copy of a delivered chat reminder. The conversation artifact is the reminder's primary effect, so
    a failure here is recorded on the receipt, never turned into a failed or repeated delivery."""
    from core.operator import notification_center

    reminder_id = str(row.get("reminder_id") or "")
    status = str(row.get("delivery_status") or "delivered")
    generation = "uncertain" if status == "delivery_uncertain" else str(int(row.get("fire_generation") or 0))
    note = str(row.get("note") or "")
    try:
        item = notification_center.record_item(
            dedupe_key=f"reminder:{reminder_id}:{generation}",
            source_kind="reminder",
            title=f"Reminder: {note}",
            body=str(receipt.get("text") or ""),
            payload={
                "reminder_id": reminder_id,
                "due_at_utc": str(row.get("due_at_utc") or ""),
                "due_local": format_due_time(str(row.get("due_at_utc") or ""), str(row.get("tz_name") or ""), str(row.get("due_wall") or "")),
                "delivery_status": status,
            },
            schedule_id=reminder_id,
            session_id=str(row.get("session_id") or ""),
            now_fn=now_fn,
            get_connection_fn=get_connection_fn,
        )
        if item.get("created"):
            notification_center.supersede_earlier_items(reminder_id, keep_notification_id=item["notification_id"],
                                                        now_fn=now_fn, get_connection_fn=get_connection_fn)
            notification_center.record_delivery(item["notification_id"], channel="session_log", state="recorded",
                                                now_fn=now_fn, get_connection_fn=get_connection_fn)
            notification_center.record_delivery(item["notification_id"], channel="in_app", state="recorded",
                                                now_fn=now_fn, get_connection_fn=get_connection_fn)
        receipt["notification_id"] = item["notification_id"]
    except Exception as exc:
        logger.exception("reminder %s reached its chat but not the notification centre", reminder_id)
        receipt["notification_error"] = type(exc).__name__


def _deliver_calendar_alert(row: dict[str, Any], *, now_fn: Callable[[], str], get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    """Record a due calendar alert in the notification centre, or refuse to show it as current."""
    from core.operator import notification_center

    reminder_id = str(row.get("reminder_id") or "")
    event_key = str(row.get("source_ref") or "")
    payload = _decode_payload(row.get("payload_json"))
    now = _parse_instant(now_fn())
    start = _parse_instant(payload.get("start_utc"))
    if now is None or start is None:
        return {"ok": False, "effect_state": "expired", "reason": "the alert has no readable event start", "catch_up": False}
    conn = get_connection_fn()
    try:
        projection = conn.execute("SELECT state FROM calendar_event_projections WHERE event_key = ?", (event_key,)).fetchone()
    finally:
        conn.close()
    state = str(projection["state"]) if projection is not None else "unknown"
    if state != "active":
        return {"ok": False, "effect_state": "expired", "reason": f"the event is {state} since the alert was scheduled", "catch_up": False}
    preferences = notification_center.load_preferences(get_connection_fn=get_connection_fn)
    if now > start + timedelta(minutes=int(preferences.get("late_grace_minutes") or 0)):
        return {"ok": False, "effect_state": "expired", "reason": "the event started before the alert could be shown", "catch_up": True}
    due = _parse_instant(row.get("due_at_utc"))
    late = bool(due is not None and now - due > timedelta(minutes=1))
    local_start = _parse_instant(payload.get("start_local"))
    body = ("Started" if start <= now else "Starts") + (f" at {local_start.strftime('%H:%M')}" if local_start else "")
    if payload.get("calendar_name"):
        body += f" · {payload['calendar_name']}"
    if payload.get("location"):
        body += f" · {payload['location']}"
    item = notification_center.record_item(
        dedupe_key=f"calendar_alert:{reminder_id}:{int(row.get('fire_generation') or 0)}",
        source_kind="calendar_alert",
        title=str(payload.get("title") or row.get("note") or "(untitled event)"),
        body=body,
        payload={**payload, "late": late, "snooze_count": int(row.get("snooze_count") or 0)},
        schedule_id=reminder_id,
        event_key=event_key,
        now_fn=now_fn,
        get_connection_fn=get_connection_fn,
    )
    if item.get("created"):
        notification_center.supersede_earlier_items(reminder_id, keep_notification_id=item["notification_id"],
                                                    now_fn=now_fn, get_connection_fn=get_connection_fn)
        notification_center.record_delivery(item["notification_id"], channel="in_app", state="recorded",
                                            now_fn=now_fn, get_connection_fn=get_connection_fn)
    return {"ok": True, "effect": "notification_centre", "notification_id": item["notification_id"], "late": late,
            "reminder_id": reminder_id}


def reminder_store_snapshot() -> dict[str, Any]:
    from storage.db import get_connection

    return operator_reminders.sweep_snapshot(get_connection_fn=get_connection)


def reminders_for_session(session_id: str, *, include_delivered: bool = True) -> list[dict[str, Any]]:
    from storage.db import get_connection

    rows = operator_reminders.list_reminders(
        session_id=session_id, include_delivered=include_delivered, get_connection_fn=get_connection
    )
    for row in rows:
        if row.get("delivery_receipt_json"):
            try:
                row["delivery_receipt"] = json.loads(str(row["delivery_receipt_json"]))
            except json.JSONDecodeError:
                pass
    return rows


_DEFAULT_DISPATCHER: ReminderDispatcher | None = None
_DISPATCHER_LOCK = threading.Lock()


def start_default_dispatcher() -> ReminderDispatcher | None:
    """Start the one process-wide dispatcher. Called by the served boot; safe to call twice."""
    global _DEFAULT_DISPATCHER
    import time

    with _DISPATCHER_LOCK:
        if _DEFAULT_DISPATCHER is not None and _DEFAULT_DISPATCHER._thread is not None and _DEFAULT_DISPATCHER._thread.is_alive():
            return _DEFAULT_DISPATCHER
        from storage.db import get_connection

        _DEFAULT_DISPATCHER = ReminderDispatcher(
            get_connection_fn=get_connection,
            sleep_fn=time.sleep,
        )
        _DEFAULT_DISPATCHER.start()
        return _DEFAULT_DISPATCHER


def stop_default_dispatcher() -> None:
    global _DEFAULT_DISPATCHER
    with _DISPATCHER_LOCK:
        if _DEFAULT_DISPATCHER is not None:
            _DEFAULT_DISPATCHER.stop()
        _DEFAULT_DISPATCHER = None
