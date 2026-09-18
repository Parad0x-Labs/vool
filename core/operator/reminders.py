"""Durable local reminders: the store, its state machine, and nothing else.

This is the persistence half of the scheduling vertical. It owns ONE table
(``reminder_requests``, created by storage.migrations) and a strict state
machine:

    scheduled -> dispatching -> delivered
    scheduled -> cancelled
    dispatching -> delivery_uncertain   (only by crash recovery, never inline)
    dispatching -> expired              (a calendar alert that came due after its event started)
    delivered -> scheduled              (snooze: the same row fires again, one generation later)

The same table carries two source kinds. ``reminder`` rows belong to a chat session. ``calendar_alert``
rows belong to an event projection (``source_ref`` is its event key) and are keyed by ``schedule_key``
(event, lead time, start instant), so reconciling an event twice never schedules it twice.

Every transition is a compare-and-swap on the previous status, so two sweepers
(or a sweep racing a cancel) can never both win: one UPDATE row-count decides.
A dispatch that cannot prove its effect does not claim ``delivered`` -- it stays
``delivery_uncertain`` and is reported as exactly that (honest reconciliation:
the store never promises exactly-once delivery, it promises at-most-once
attempt plus a truthful record of the attempt's known outcome).

All clock and storage access is injected, mirroring core/operator/approvals.py,
so handlers and tests stay pure and the served wiring in
core/local_operator_actions.py is the only place real dependencies are bound.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

REMINDER_STATUSES = ("scheduled", "dispatching", "delivered", "cancelled", "delivery_uncertain", "expired")
SOURCE_KINDS = ("reminder", "calendar_alert")

#: The basic repeat rules a standalone reminder may carry (row 20). The next occurrence keeps
#: the SAME local wall time in the reminder's zone, so DST transitions move the UTC instant.
REPEAT_EVERY_CHOICES = ("daily", "weekly")


def _utcnow() -> str:
    from core.time_authority import CLOCK
    return CLOCK.now_utc().isoformat()


def schedule_reminder(
    *,
    session_id: str,
    task_id: str,
    note: str,
    due_at_utc: str,
    tz_name: str,
    due_wall: str = "",
    source_kind: str = "reminder",
    source_ref: str = "",
    schedule_key: str = "",
    payload: dict[str, Any] | None = None,
    repeat_every: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown schedule source kind: {source_kind!r}")
    if repeat_every and repeat_every not in REPEAT_EVERY_CHOICES:
        raise ValueError(f"unknown repeat rule: {repeat_every!r}")
    if repeat_every and source_kind != "reminder":
        raise ValueError("only a standalone reminder may repeat; provider-owned alert schedules own their own repeats")
    now = now_fn()
    reminder_id = str(uuid.uuid4())
    conn = get_connection_fn()
    try:
        conn.execute(
            """
            INSERT INTO reminder_requests (
                reminder_id, session_id, task_id, note, due_at_utc, tz_name, due_wall,
                status, delivery_attempts, delivered_at, last_error, created_at, updated_at,
                source_kind, source_ref, schedule_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', 0, NULL, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, session_id, task_id, note, due_at_utc, tz_name, due_wall, now, now,
             source_kind, str(source_ref or ""), str(schedule_key or ""),
             json.dumps({**(payload or {}), **({"repeat_every": repeat_every} if repeat_every else {})}, sort_keys=True)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"reminder_id": reminder_id, "status": "scheduled", "due_at_utc": due_at_utc, "note": note,
            "repeat_every": repeat_every}


def list_reminders(
    *,
    session_id: str,
    include_delivered: bool = False,
    get_connection_fn: Callable[[], Any],
) -> list[dict[str, Any]]:
    conn = get_connection_fn()
    try:
        if include_delivered:
            rows = conn.execute(
                """
                SELECT * FROM reminder_requests
                WHERE session_id = ? AND status != 'cancelled'
                ORDER BY due_at_utc ASC, created_at ASC
                """,
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM reminder_requests
                WHERE session_id = ? AND status IN ('scheduled', 'dispatching', 'delivery_uncertain')
                ORDER BY due_at_utc ASC, created_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_reminder(
    reminder_id: str,
    *,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any] | None:
    conn = get_connection_fn()
    try:
        row = conn.execute(
            "SELECT * FROM reminder_requests WHERE reminder_id = ? LIMIT 1", (reminder_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def cancel_reminder(
    reminder_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Cancel a scheduled effect. An in-flight or unknown effect cannot be promised away."""
    conn = get_connection_fn()
    try:
        row = conn.execute(
            "SELECT status FROM reminder_requests WHERE reminder_id = ?", (reminder_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "status": "missing", "reason": "no reminder with that id"}
        current = str(row["status"])
        if current == "delivered":
            return {"ok": False, "status": "already_delivered", "reason": "the reminder was already delivered"}
        if current == "cancelled":
            return {"ok": True, "status": "cancelled", "reason": "already cancelled"}
        if current in {"dispatching", "delivery_uncertain"}:
            return {"ok": False, "status": current, "reason": "delivery has started or its outcome is uncertain; cancellation cannot guarantee it was prevented"}
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET status = 'cancelled', updated_at = ?
            WHERE reminder_id = ? AND status = 'scheduled'
            """,
            (now_fn(), reminder_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "status": "race_lost", "reason": f"status changed concurrently to {current}"}
        return {"ok": True, "status": "cancelled"}
    finally:
        conn.close()


def move_reminder(
    reminder_id: str,
    *,
    due_at_utc: str,
    tz_name: str = "",
    due_wall: str = "",
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET due_at_utc = ?, tz_name = ?, due_wall = ?, status = 'scheduled',
                delivery_attempts = 0, delivered_at = NULL, last_error = NULL, updated_at = ?
            WHERE reminder_id = ? AND status IN ('scheduled', 'delivery_uncertain')
            """,
            (due_at_utc, tz_name, due_wall, now_fn(), reminder_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "status": "not_movable", "reason": "reminder is cancelled, delivered, or currently dispatching"}
        return {"ok": True, "status": "scheduled", "due_at_utc": due_at_utc}
    finally:
        conn.close()


def complete_reminder(
    reminder_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Mark a reminder done: a durable record that it was carried out, not a deletion.

    A repeating reminder's CURRENT occurrence is completed; the next occurrence, if one was
    already scheduled by delivery, is untouched (cancelling that row is how the series stops).
    """
    conn = get_connection_fn()
    try:
        row = conn.execute(
            "SELECT status, note FROM reminder_requests WHERE reminder_id = ?", (reminder_id,)
        ).fetchone()
        if row is None:
            return {"ok": False, "status": "missing", "reason": "no reminder with that id"}
        current = str(row["status"])
        if current == "cancelled":
            return {"ok": False, "status": "cancelled", "reason": "that reminder was cancelled, not completed"}
        if current == "completed":
            return {"ok": True, "status": "completed", "reason": "already completed"}
        if current == "dispatching":
            return {"ok": False, "status": "dispatching", "reason": "delivery has started; complete it after it arrives"}
        cursor = conn.execute(
            "UPDATE reminder_requests SET status = 'completed', updated_at = ? WHERE reminder_id = ? AND status = ?",
            (now_fn(), reminder_id, current),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"ok": False, "status": "race_lost", "reason": f"status changed concurrently to {current}"}
        return {"ok": True, "status": "completed", "note": str(row["note"] or "")}
    finally:
        conn.close()


def edit_reminder(
    reminder_id: str,
    *,
    note: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Edit what a pending reminder says. Time changes belong to move_reminder."""
    wanted = str(note or "").strip()
    if not wanted:
        return {"ok": False, "status": "invalid_request", "reason": "the new reminder text is empty"}
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            "UPDATE reminder_requests SET note = ?, updated_at = ? WHERE reminder_id = ? AND status = 'scheduled'",
            (wanted, now_fn(), reminder_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            row = conn.execute("SELECT status FROM reminder_requests WHERE reminder_id = ?", (reminder_id,)).fetchone()
            reason = "no reminder with that id" if row is None else f"that reminder is {row['status']}, not editable"
            return {"ok": False, "status": "not_editable", "reason": reason}
        return {"ok": True, "status": "scheduled", "note": wanted}
    finally:
        conn.close()


def next_repeat_occurrence(row: Mapping[str, Any], *, now_fn: Callable[[], str] = _utcnow,
                          after_utc: datetime | None = None) -> dict[str, Any] | None:
    """The next occurrence of a repeating reminder, or None when it does not repeat.

    The wall clock stays fixed in the reminder's zone (09:00 stays 09:00 across a DST change);
    the UTC instant therefore moves. A wall time that does not exist on the next date (a
    spring-forward gap) resolves to that zone's later instant rather than being invented.
    """
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        return None
    repeat = str(payload.get("repeat_every") or "")
    if repeat not in REPEAT_EVERY_CHOICES:
        return None
    tz_name = str(row.get("tz_name") or "UTC")
    zone = ZoneInfo(tz_name)
    wall = str(row.get("due_wall") or "")
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", wall)
    if match:
        local = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                         int(match.group(4)), int(match.group(5)), tzinfo=zone)
    else:
        try:
            local = datetime.fromisoformat(str(row.get("due_at_utc") or "").replace("Z", "+00:00")).astimezone(zone)
        except ValueError:
            return None
    step = timedelta(days=1 if repeat == "daily" else 7)
    now_local = datetime.fromisoformat(now_fn().replace("Z", "+00:00")).astimezone(zone)
    floor = now_local.astimezone(timezone.utc)
    if after_utc is not None:
        # An occurrence completed BEFORE its due time still happened: its successor is one period
        # after the occurrence itself, not one period after now.
        floor = max(floor, after_utc)
    candidate = local
    # Step from the stored wall time until the occurrence is in the future; this also repairs a
    # stored occurrence that is somehow still in the past without firing a burst of catch-ups.
    while candidate.astimezone(timezone.utc) <= floor:
        candidate = candidate + step
    return {"due_at_utc": candidate.astimezone(timezone.utc).isoformat(),
            "tz_name": tz_name,
            "due_wall": candidate.strftime("%Y-%m-%d %H:%M"),
            "repeat_every": repeat}


def reschedule_repeat(reminder_id: str, *, now_fn: Callable[[], str] = _utcnow,
                      get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    """After delivery, schedule a repeating reminder's next occurrence as its OWN row.

    Occurrence identity is one row per occurrence; the previous row stays delivered, so a
    delivered occurrence is never mutated and the next one can be cancelled on its own.
    """
    row = load_reminder(reminder_id, get_connection_fn=get_connection_fn)
    if row is None or str(row.get("status")) not in {"delivered", "completed"}:
        return {"ok": False, "status": "not_repeating", "reason": "no delivered or completed reminder with that id"}
    if str(row.get("source_kind")) != "reminder":
        return {"ok": False, "status": "not_repeating", "reason": "provider-owned schedules manage their own repeats"}
    existing = get_connection_fn()
    try:
        clash = existing.execute(
            "SELECT 1 FROM reminder_requests WHERE source_kind = 'reminder' AND schedule_key = ? LIMIT 1",
            (f"repeat:{reminder_id}",),
        ).fetchone()
    finally:
        existing.close()
    if clash is not None:
        return {"ok": True, "status": "already_scheduled"}
    try:
        after_utc = None
        if str(row.get("status")) == "completed" and row.get("due_at_utc"):
            after_utc = datetime.fromisoformat(str(row["due_at_utc"]).replace("Z", "+00:00"))
        nxt = next_repeat_occurrence(row, now_fn=now_fn, after_utc=after_utc)
    except ValueError:
        nxt = None
    if nxt is None:
        return {"ok": False, "status": "not_repeating", "reason": "the reminder does not repeat"}
    try:
        record = schedule_reminder(
            session_id=str(row.get("session_id") or ""),
            task_id=str(row.get("task_id") or ""),
            note=str(row.get("note") or ""),
            due_at_utc=nxt["due_at_utc"], tz_name=nxt["tz_name"], due_wall=nxt["due_wall"],
            source_kind="reminder", schedule_key=f"repeat:{reminder_id}",
            payload={"repeat_of": reminder_id}, repeat_every=nxt["repeat_every"],
            now_fn=now_fn, get_connection_fn=get_connection_fn,
        )
    except sqlite3.IntegrityError:
        # The unique schedule-key index decided a concurrent sweeper won: exactly one successor.
        return {"ok": True, "status": "already_scheduled"}
    return {"ok": True, "status": "scheduled", "next": record}


def repair_repeating_gaps(*, now_fn: Callable[[], str] = _utcnow,
                          get_connection_fn: Callable[[], Any]) -> dict[str, Any]:
    """Repair delivered/completed repeating occurrences whose successor is missing.

    The window between a delivery and its successor's insert can fail (storage error, crash).
    A repeating occurrence with NO row keyed ``repeat:<id>`` -- whatever that row's status,
    so a cancelled successor still ends the series intentionally -- gets its successor created
    here, idempotently, by any later sweep. Failures are counted, never hidden.
    """
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            """
            SELECT reminder_id, status, payload_json FROM reminder_requests
            WHERE source_kind = 'reminder' AND status IN ('delivered', 'completed')
            """
        ).fetchall()
    finally:
        conn.close()
    repaired, failed = 0, 0
    failures: list[dict[str, str]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if str(payload.get("repeat_every") or "") not in REPEAT_EVERY_CHOICES:
            continue
        reminder_id = str(row["reminder_id"])
        check = get_connection_fn()
        try:
            exists = check.execute(
                "SELECT 1 FROM reminder_requests WHERE schedule_key = ? LIMIT 1", (f"repeat:{reminder_id}",),
            ).fetchone()
        finally:
            check.close()
        if exists is not None:
            continue
        outcome = reschedule_repeat(reminder_id, now_fn=now_fn, get_connection_fn=get_connection_fn)
        if outcome.get("ok") and outcome.get("status") in {"scheduled", "already_scheduled"}:
            repaired += 1
        else:
            failed += 1
            failures.append({"reminder_id": reminder_id, "reason": str(outcome.get("reason") or outcome.get("status"))})
    return {"repaired": repaired, "failed": failed, "failures": failures}


def due_reminders(
    *,
    now_utc: str,
    limit: int = 50,
    get_connection_fn: Callable[[], Any],
) -> list[dict[str, Any]]:
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM reminder_requests
            WHERE status = 'scheduled' AND due_at_utc <= ?
            ORDER BY due_at_utc ASC
            LIMIT ?
            """,
            (now_utc, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def claim_for_dispatch(
    reminder_id: str,
    *,
    now_fn: Callable[[], str] = _utcnow,
    due_before_utc: str | None = None,
    get_connection_fn: Callable[[], Any],
) -> bool:
    """CAS scheduled -> dispatching. Exactly one caller wins; the loser sees False."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET status = 'dispatching', delivery_attempts = delivery_attempts + 1, updated_at = ?
            WHERE reminder_id = ? AND status = 'scheduled'
                AND (? IS NULL OR due_at_utc <= ?)
            """,
            (now_fn(), reminder_id, due_before_utc, due_before_utc),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_delivered(
    reminder_id: str,
    *,
    delivery_receipt: dict[str, Any],
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> bool:
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET status = 'delivered', delivered_at = ?, last_error = NULL,
                delivery_receipt_json = ?, updated_at = ?
            WHERE reminder_id = ? AND status = 'dispatching'
            """,
            (now_fn(), json.dumps(delivery_receipt, sort_keys=True), now_fn(), reminder_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_delivery_failed(
    reminder_id: str,
    *,
    error: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> bool:
    """A delivery attempt with an explicit no-effect receipt. An exception alone is
    insufficient evidence. The reminder goes back to ``scheduled`` so the
    next sweep retries; the attempt count and error are kept for the record."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET status = 'scheduled', last_error = ?, updated_at = ?
            WHERE reminder_id = ? AND status = 'dispatching'
            """,
            (str(error)[:500], now_fn(), reminder_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def mark_delivery_uncertain(
    reminder_id: str,
    *,
    error: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> bool:
    """An exception is not evidence that an effect did not happen. Do not replay it."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            "UPDATE reminder_requests SET status = 'delivery_uncertain', last_error = ?, updated_at = ? "
            "WHERE reminder_id = ? AND status = 'dispatching'",
            (str(error)[:500], now_fn(), reminder_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def reconcile_event_alerts(
    *,
    event_key: str,
    desired: list[dict[str, Any]],
    note: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Make an event's pending alerts exactly ``desired`` (entries: schedule_key, due_at_utc, tz_name, payload).

    One write transaction. A missing alert is inserted (the unique schedule key refuses a concurrent duplicate).
    A pending alert that is still desired keeps its due time (a snooze stays) and takes the refreshed card. A
    cancelled one that is desired again is re-armed one generation later. A pending alert that is no longer
    desired is cancelled as superseded. Delivered, expired and uncertain alerts are history and are never touched.
    """
    now = now_fn()
    wanted = {str(entry["schedule_key"]): entry for entry in desired}
    scheduled = 0
    superseded_ids: list[str] = []
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = {
            str(row["schedule_key"]): dict(row)
            for row in conn.execute(
                "SELECT reminder_id, schedule_key, status FROM reminder_requests WHERE source_kind = 'calendar_alert' AND source_ref = ?",
                (event_key,),
            ).fetchall()
        }
        for key, entry in wanted.items():
            payload_json = json.dumps(entry.get("payload") or {}, sort_keys=True)
            row = existing.get(key)
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO reminder_requests (
                        reminder_id, session_id, task_id, note, due_at_utc, tz_name, due_wall, status, delivery_attempts,
                        created_at, updated_at, source_kind, source_ref, schedule_key, payload_json
                    ) VALUES (?, '', '', ?, ?, ?, '', 'scheduled', 0, ?, ?, 'calendar_alert', ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), str(note or "")[:300], str(entry["due_at_utc"]), str(entry.get("tz_name") or ""),
                     now, now, event_key, key, payload_json),
                )
                scheduled += int(cursor.rowcount or 0)
            elif row["status"] == "scheduled":
                conn.execute(
                    "UPDATE reminder_requests SET note = ?, payload_json = ?, updated_at = ? WHERE reminder_id = ? AND status = 'scheduled'",
                    (str(note or "")[:300], payload_json, now, row["reminder_id"]),
                )
            elif row["status"] == "cancelled":
                cursor = conn.execute(
                    """
                    UPDATE reminder_requests
                    SET status = 'scheduled', due_at_utc = ?, note = ?, payload_json = ?, last_error = NULL,
                        fire_generation = fire_generation + 1, updated_at = ?
                    WHERE reminder_id = ? AND status = 'cancelled'
                    """,
                    (str(entry["due_at_utc"]), str(note or "")[:300], payload_json, now, row["reminder_id"]),
                )
                scheduled += int(cursor.rowcount or 0)
        for key, row in existing.items():
            if key in wanted or row["status"] != "scheduled":
                continue
            cursor = conn.execute(
                "UPDATE reminder_requests SET status = 'cancelled', last_error = ?, updated_at = ? WHERE reminder_id = ? AND status = 'scheduled'",
                ("superseded: the event, its alert policy or its alerts setting changed", now, row["reminder_id"]),
            )
            if cursor.rowcount:
                superseded_ids.append(str(row["reminder_id"]))
        conn.commit()
    finally:
        conn.close()
    return {"scheduled": scheduled, "superseded": len(superseded_ids), "superseded_ids": superseded_ids}


def cancel_calendar_alert_rows(
    *,
    event_keys: list[str],
    reason: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> list[str]:
    """Cancel the pending calendar alerts of these events. Returns the cancelled schedule ids."""
    keys = [str(key) for key in event_keys if str(key or "")]
    if not keys:
        return []
    conn = get_connection_fn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        marks = ",".join("?" for _ in keys)
        ids = [
            str(row["reminder_id"])
            for row in conn.execute(
                f"SELECT reminder_id FROM reminder_requests WHERE source_kind = 'calendar_alert' AND status = 'scheduled' AND source_ref IN ({marks})",
                keys,
            ).fetchall()
        ]
        if ids:
            id_marks = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE reminder_requests SET status = 'cancelled', last_error = ?, updated_at = ? WHERE status = 'scheduled' AND reminder_id IN ({id_marks})",
                [str(reason or "cancelled")[:300], now_fn(), *ids],
            )
        conn.commit()
    finally:
        conn.close()
    return ids


def snooze_schedule(
    reminder_id: str,
    *,
    until_utc: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Delivered -> scheduled at ``until_utc``, one fire generation later. Only something already shown snoozes."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            """
            UPDATE reminder_requests
            SET status = 'scheduled', due_at_utc = ?, snooze_count = snooze_count + 1, fire_generation = fire_generation + 1,
                delivered_at = NULL, last_error = NULL, updated_at = ?
            WHERE reminder_id = ? AND status = 'delivered'
            """,
            (until_utc, now_fn(), reminder_id),
        )
        conn.commit()
        if cursor.rowcount == 1:
            return {"ok": True, "reminder_id": reminder_id, "due_at_utc": until_utc}
        row = conn.execute("SELECT status FROM reminder_requests WHERE reminder_id = ?", (reminder_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {"ok": False, "reason": "unknown_schedule"}
    status = str(row["status"])
    if status == "scheduled":
        return {"ok": False, "reason": "already_waiting", "detail": "it is already waiting to be shown again"}
    return {"ok": False, "reason": f"not_snoozable_{status}", "detail": "only an alert or reminder that was shown can be snoozed"}


def mark_expired(
    reminder_id: str,
    *,
    reason: str,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> bool:
    """A claimed calendar alert that must not be shown as current (its event already started or left the calendar)."""
    conn = get_connection_fn()
    try:
        cursor = conn.execute(
            "UPDATE reminder_requests SET status = 'expired', last_error = ?, updated_at = ? WHERE reminder_id = ? AND status = 'dispatching'",
            (str(reason or "expired")[:300], now_fn(), reminder_id),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def demote_stale_dispatching(
    *,
    older_than_seconds: float,
    now_fn: Callable[[], str] = _utcnow,
    get_connection_fn: Callable[[], Any],
) -> list[dict[str, Any]]:
    """Crash recovery: a row still ``dispatching`` after the staleness window means the
    process died between the CAS and the delivery. The outcome is UNKNOWN -- it is demoted
    to ``delivery_uncertain``, never re-fired silently and never claimed delivered. The
    sweep reports these so the operator can reconcile by looking at the delivery surface."""
    from datetime import datetime, timedelta, timezone

    try:
        cutoff = (datetime.fromisoformat(now_fn()).astimezone(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat()
    except Exception:
        return []
    conn = get_connection_fn()
    try:
        # Select and transition under one write transaction: concurrent sweepers cannot
        # both report the same stale claim or overwrite a newly completed delivery.
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM reminder_requests WHERE status = 'dispatching' AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        stale = [dict(row) for row in rows]
        if stale:
            conn.execute(
                """
                UPDATE reminder_requests
                SET status = 'delivery_uncertain', last_error = 'dispatch interrupted; outcome unknown',
                    updated_at = ?
                WHERE status = 'dispatching' AND updated_at < ?
                """,
                (now_fn(), cutoff),
            )
        conn.commit()
        return stale
    finally:
        conn.close()


def sweep_snapshot(
    *,
    get_connection_fn: Callable[[], Any],
) -> dict[str, Any]:
    """Counters for the served surface: what the vertical currently holds."""
    conn = get_connection_fn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM reminder_requests GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}
    finally:
        conn.close()
