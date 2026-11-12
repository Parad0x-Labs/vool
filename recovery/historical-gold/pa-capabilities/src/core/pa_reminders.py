"""The durable reminder engine (B9, PA lane): fire-at-time, catch-up, honesty.

DELIVERY SEMANTICS (explicit, revised 2026-09-09 after crash-boundary review):

* EXACTLY-ONCE CLAIM, NOT EXACTLY-ONCE NOTIFICATION. The conditional
  ``pending -> firing`` UPDATE guarantees exactly one CLAIMANT per reminder —
  under duplicated ticks, concurrent threads, or a duplicate boot recovery. It
  cannot guarantee exactly-once NOTIFICATION: a crash between a successful
  ``notify()`` and the ``delivered`` commit leaves the row in ``firing`` with
  the notification already shown. Nothing weaker than an idempotent receiver
  can close that window, so the engine does not pretend to.

* AT-LEAST-ONCE, LABELED. On boot, a row found in ``firing`` (a crash window
  survivor) is re-notified with ``possible_duplicate: true`` recorded on the
  row and carried in the payload. Loss without a trace is impossible;
  duplicates are possible and announced.

* NOTIFIER FAILURE IS NEVER SUCCESS. A raising notifier records
  ``delivery_failed`` with the reason — never ``delivered``. Failed deliveries
  are retried on later ticks/boot while ``attempts < max_attempts`` (default 3);
  exhausted rows stay ``delivery_failed`` permanently and are surfaced in
  ``recover()``'s summary as ``permanently_failed``.

* NO NOTIFIER, NO CLAIM. ``tick``/``recover`` called without a delivery channel
  do not claim pending rows at all: firing a reminder with no way to deliver it
  would burn the claim and produce nothing. They report ``notifier_absent``.

* MISSED RUNS ARE REPORTED, NEVER DROPPED. On boot, still-relevant overdue
  pending rows fire; pending rows older than the stale horizon are marked
  ``missed`` and ANNOUNCED. Nothing expires silently.

* REMINDERS ONLY NOTIFY — never execute external actions; acting stays behind
  the durable-approval door (B4).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

STATUS_PENDING = "pending"
STATUS_FIRING = "firing"
STATUS_RECOVERING = "recovering"
STATUS_DELIVERED = "delivered"
STATUS_DELIVERY_FAILED = "delivery_failed"
STATUS_MISSED = "missed"
STATUS_CANCELLED = "cancelled"

#: Legacy alias for pre-revision callers/tests: "fired" meant "claim consumed".
#: Superseded by the delivered/delivery_failed distinction above.
STATUS_FIRED = STATUS_DELIVERED

DEFAULT_STALE_AFTER_SECONDS = 24 * 3600
DEFAULT_MAX_DELIVERY_ATTEMPTS = 3
#: A crash-window survivor claimed by a recovery (firing -> recovering) is left
#: alone by OTHER boots while its claim is fresh; only a claim older than this
#: grace (a recovery that itself crashed) is re-claimed. Bounds duplicate
#: redelivery across overlapping restarts without adding a lost-window.
DEFAULT_INFLIGHT_GRACE_SECONDS = 10.0

NotifyFn = Callable[[dict[str, Any]], None]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_connection():
    from storage.db import get_connection

    return get_connection()


def _row_public(row: dict[str, Any]) -> dict[str, Any]:
    try:
        details = json.loads(str(row.get("details_json") or "{}"))
    except Exception:
        details = {}
    return {
        "reminder_id": str(row.get("reminder_id") or ""),
        "task_id": str(row.get("task_id") or ""),
        "message": str(row.get("message") or ""),
        "due_at_utc": str(row.get("due_at_utc") or ""),
        "status": str(row.get("status") or ""),
        "fired_at": str(row.get("fired_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "attempts": int(row.get("attempts") or 0),
        "details": details if isinstance(details, dict) else {},
    }


def schedule(
    *,
    message: str,
    due_at_utc: str,
    task_id: str = "",
    now_fn: Callable[[], datetime] = _now_utc,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    message = " ".join(str(message or "").split()).strip()
    if not message:
        raise ValueError("a reminder needs a message")
    try:
        due = datetime.fromisoformat(str(due_at_utc or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"due_at_utc must be an ISO datetime: {exc}") from exc
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    reminder_id = str(uuid.uuid4())
    conn = (get_connection_fn or _get_connection)()
    try:
        conn.execute(
            """
            INSERT INTO reminders (reminder_id, task_id, message, due_at_utc, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (reminder_id, str(task_id or ""), message[:1000], due.isoformat(),
             STATUS_PENDING, now_fn().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"reminder_id": reminder_id, "status": STATUS_PENDING, "due_at_utc": due.isoformat()}


def cancel(
    reminder_id: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Cancellation refuses anything not still pending: what was delivered
    cannot be undelivered, and pretending otherwise would hide a shown
    notification."""
    conn = (get_connection_fn or _get_connection)()
    try:
        row = conn.execute(
            "SELECT * FROM reminders WHERE reminder_id = ?", (str(reminder_id or ""),)
        ).fetchone()
        if row is None:
            return {"ok": False, "status": "not_found"}
        row = dict(row)
        status = str(row.get("status") or "")
        if status == STATUS_CANCELLED:
            return {"ok": True, "status": "already_cancelled", "reminder": _row_public(row)}
        if status == STATUS_MISSED:
            return {"ok": True, "status": "already_missed", "reminder": _row_public(row)}
        if status in {STATUS_DELIVERED, STATUS_FIRING, STATUS_DELIVERY_FAILED}:
            label = "already delivered" if status == STATUS_DELIVERED else f"{status} (in flight or exhausted)"
            return {"ok": False, "status": "not_pending", "detail": label, "reminder": _row_public(row)}
        cur = conn.execute(
            "UPDATE reminders SET status = ? WHERE reminder_id = ? AND status = ?",
            (STATUS_CANCELLED, str(reminder_id), STATUS_PENDING),
        )
        conn.commit()
        return {"ok": cur.rowcount > 0, "status": STATUS_CANCELLED if cur.rowcount else "not_pending"}
    finally:
        conn.close()


def list_reminders(
    *,
    include_closed: bool = False,
    get_connection_fn: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    conn = (get_connection_fn or _get_connection)()
    try:
        if include_closed:
            rows = conn.execute(
                "SELECT * FROM reminders ORDER BY due_at_utc ASC LIMIT 500"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = ? ORDER BY due_at_utc ASC LIMIT 500",
                (STATUS_PENDING,),
            ).fetchall()
        return [_row_public(dict(r)) for r in rows]
    finally:
        conn.close()


def _claim(conn: Any, reminder_id: str, *, from_status: str) -> bool:
    """The one claim seam: a conditional UPDATE whose rowcount IS the claim.
    ``from_status`` is pending (first fire) or delivery_failed (bounded retry)."""
    cur = conn.execute(
        "UPDATE reminders SET status = ?, attempts = attempts + 1, fired_at = ? "
        "WHERE reminder_id = ? AND status = ?",
        (STATUS_FIRING, _now_utc().isoformat(), str(reminder_id), from_status),
    )
    return cur.rowcount > 0


def _record(conn: Any, reminder_id: str, *, status: str, details: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE reminders SET status = ?, details_json = ? WHERE reminder_id = ?",
        (status, json.dumps(details, sort_keys=True), str(reminder_id)),
    )


def _attempt_delivery(
    conn: Any,
    row: dict[str, Any],
    *,
    notify: NotifyFn,
    now: datetime,
    lineage: str,
    possible_duplicate: bool,
) -> dict[str, Any]:
    """Deliver one claimed row and record the honest outcome. A raising
    notifier becomes delivery_failed with the reason — never delivered."""
    payload = _row_public(row)
    payload["possible_duplicate"] = possible_duplicate
    payload["delivery_lineage"] = lineage
    reason = ""
    try:
        notify(payload)
    except Exception as exc:
        reason = f"notify_failed: {type(exc).__name__}: {exc}"
    if not reason:
        details = dict(payload.get("details") or {})
        details.update({"delivery_lineage": lineage, "possible_duplicate": possible_duplicate,
                        "delivered_at": now.isoformat()})
        _record(conn, row["reminder_id"], status=STATUS_DELIVERED, details=details)
        return {**payload, "status": STATUS_DELIVERED, "outcome": "delivered"}
    prior = dict(payload.get("details") or {})
    prior.update({"delivery_lineage": lineage, "failure_reason": reason})
    _record(conn, row["reminder_id"], status=STATUS_DELIVERY_FAILED, details=prior)
    return {**payload, "status": STATUS_DELIVERY_FAILED, "outcome": "delivery_failed",
            "failure_reason": reason}


def _sweep_survivors(
    *,
    notify: NotifyFn,
    now: datetime,
    grace_cutoff_iso: str,
    get_connection_fn: Callable[[], Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Claim and redeliver crash-window survivors (firing, or recovering past the
    in-flight grace). Returns (delivered, failed, recovered_meta, in_flight).

    LOCKING LAW: the claim is COMMITTED on its own connection BEFORE any notify
    runs — the notify callback writes runtime events on the SAME database file,
    so a write transaction held across notify deadlocks ('database is locked',
    measured). Delivery outcomes are then recorded with a per-row commit: a
    crash between claim and outcome leaves the durable firing/recovering row
    that this same sweep (or a boot) resolves later."""
    conn = (get_connection_fn or _get_connection)()
    claimed: list[dict[str, Any]] = []
    in_flight: list[dict[str, Any]] = []
    try:
        survivors = conn.execute(
            "SELECT * FROM reminders WHERE status IN (?, ?) ORDER BY due_at_utc ASC LIMIT 100",
            (STATUS_FIRING, STATUS_RECOVERING),
        ).fetchall()
        for row in survivors:
            row = dict(row)
            status = str(row.get("status") or "")
            if status == STATUS_RECOVERING and str(row.get("fired_at") or "") > grace_cutoff_iso:
                in_flight.append({**_row_public(row), "outcome": "skipped_in_flight"})
                continue  # a live boot holds a fresh claim
            cur = conn.execute(
                "UPDATE reminders SET status = ?, fired_at = ? WHERE reminder_id = ? AND status = ?",
                (STATUS_RECOVERING, now.isoformat(), row["reminder_id"], status),
            )
            if cur.rowcount > 0:
                claimed.append(row)
        conn.commit()  # claims durable BEFORE any notify
    finally:
        conn.close()
    delivered: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    recovered_meta: list[dict[str, Any]] = []
    for row in claimed:
        recovered_meta.append({**_row_public(row), "outcome": "redelivered"})
        record_conn = (get_connection_fn or _get_connection)()
        try:
            outcome = _attempt_delivery(
                record_conn, row, notify=notify, now=now,
                lineage="crash_recovery", possible_duplicate=True,
            )
            record_conn.commit()  # per-row commit: never hold a txn across the NEXT notify
            (delivered if outcome["outcome"] == "delivered" else failed).append(outcome)
        finally:
            record_conn.close()
    return delivered, failed, recovered_meta, in_flight


def tick(
    *,
    now: datetime | None = None,
    notify: NotifyFn | None = None,
    max_attempts: int = DEFAULT_MAX_DELIVERY_ATTEMPTS,
    inflight_grace_seconds: float = DEFAULT_INFLIGHT_GRACE_SECONDS,
    now_fn: Callable[[], datetime] = _now_utc,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Fire due pending reminders, retry bounded failures, and sweep crash
    survivors. Without a notifier nothing is claimed (``notifier_absent``):
    there is no honest way to "fire" a reminder with no delivery channel."""
    if notify is None:
        return {"delivered": [], "failed": [], "crash_recovered": [], "in_flight": [],
                "notifier_absent": True}
    now = now or now_fn()
    grace_cutoff = (now - timedelta(seconds=max(0.0, float(inflight_grace_seconds)))).isoformat()

    # Survivor sweep first: a crash straggler must not wait behind fresh work.
    sweep_delivered, sweep_failed, recovered_meta, in_flight = _sweep_survivors(
        notify=notify, now=now, grace_cutoff_iso=grace_cutoff,
        get_connection_fn=get_connection_fn,
    )

    delivered: list[dict[str, Any]] = list(sweep_delivered)
    failed: list[dict[str, Any]] = list(sweep_failed)

    conn = (get_connection_fn or _get_connection)()
    initial_claims: list[tuple[dict[str, Any], str]] = []
    retry_claims: list[tuple[dict[str, Any], str]] = []
    try:
        # Snapshot the retry set BEFORE any claim: a row that fails delivery in
        # this tick's initial pass must not be re-claimed by this same tick —
        # one delivery attempt per row per tick, so failures pace out.
        retry_snapshot = [
            dict(r) for r in conn.execute(
                "SELECT * FROM reminders WHERE status = ? AND attempts < ? ORDER BY due_at_utc ASC LIMIT 100",
                (STATUS_DELIVERY_FAILED, max(1, int(max_attempts))),
            ).fetchall()
        ]
        due_pending = conn.execute(
            "SELECT * FROM reminders WHERE status = ? AND due_at_utc <= ? ORDER BY due_at_utc ASC LIMIT 100",
            (STATUS_PENDING, now.isoformat()),
        ).fetchall()
        for row in due_pending:
            row = dict(row)
            if _claim(conn, row["reminder_id"], from_status=STATUS_PENDING):
                initial_claims.append((row, "initial"))
        conn.commit()  # claims durable BEFORE any notify (locking law, see _sweep_survivors)
        for row in retry_snapshot:
            row = dict(row)
            if _claim(conn, row["reminder_id"], from_status=STATUS_DELIVERY_FAILED):
                retry_claims.append((row, "retry"))
        conn.commit()
    finally:
        conn.close()

    for row, lineage in initial_claims + retry_claims:
        record_conn = (get_connection_fn or _get_connection)()
        try:
            outcome = _attempt_delivery(
                record_conn, row, notify=notify, now=now,
                lineage=lineage, possible_duplicate=False,
            )
            record_conn.commit()  # per-row commit: never hold a txn across the NEXT notify
            (delivered if outcome["outcome"] == "delivered" else failed).append(outcome)
        finally:
            record_conn.close()
    return {
        "delivered": delivered, "failed": failed,
        "crash_recovered": recovered_meta, "in_flight": in_flight,
        "notifier_absent": False,
    }


def recover(
    *,
    now: datetime | None = None,
    notify: NotifyFn | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    max_attempts: int = DEFAULT_MAX_DELIVERY_ATTEMPTS,
    inflight_grace_seconds: float = DEFAULT_INFLIGHT_GRACE_SECONDS,
    now_fn: Callable[[], datetime] = _now_utc,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Boot catch-up after downtime. Handles every non-terminal state:

    * ``firing``/``recovering`` crash survivors → claimed and re-notified with
      ``possible_duplicate: true`` (at-least-once, labeled; the sweep's grace
      window keeps overlapping boots from both delivering);
    * ``pending`` past the stale horizon → ``missed`` + an ANNOUNCEMENT (the
      miss is committed BEFORE the announcement, so a failed announce cannot
      un-miss it);
    * ``delivery_failed`` with attempts remaining → retried by the tick below;
      exhausted rows are reported as ``permanently_failed``;
    * ``pending`` still relevant → fired via the normal claim path.
    """
    now = now or now_fn()
    summary: dict[str, Any] = {
        "delivered": [], "failed": [], "missed": [], "crash_recovered": [],
        "in_flight": [], "permanently_failed": [], "notifier_absent": notify is None,
    }
    grace_cutoff = (now - timedelta(seconds=max(0.0, float(inflight_grace_seconds)))).isoformat()

    if notify is None:
        # Report every non-terminal state honestly; change nothing.
        conn = (get_connection_fn or _get_connection)()
        try:
            survivors = conn.execute(
                "SELECT * FROM reminders WHERE status IN (?, ?)", (STATUS_FIRING, STATUS_RECOVERING)
            ).fetchall()
            summary["crash_recovered"] = [
                {**_row_public(dict(r)), "outcome": "notifier_absent"} for r in survivors
            ]
        finally:
            conn.close()
        fired = tick(now=now, notify=None, now_fn=now_fn, get_connection_fn=get_connection_fn)
        summary["notifier_absent"] = True
        return summary

    sweep_delivered, sweep_failed, recovered_meta, in_flight = _sweep_survivors(
        notify=notify, now=now, grace_cutoff_iso=grace_cutoff,
        get_connection_fn=get_connection_fn,
    )
    summary["delivered"].extend(sweep_delivered)
    summary["failed"].extend(sweep_failed)
    summary["crash_recovered"].extend(recovered_meta)
    summary["in_flight"].extend(in_flight)

    # Missed: commit the miss FIRST, announce AFTER (locking law + durability).
    conn = (get_connection_fn or _get_connection)()
    newly_missed: list[dict[str, Any]] = []
    try:
        cutoff = (now - timedelta(seconds=max(0, int(stale_after_seconds)))).isoformat()
        stale_rows = conn.execute(
            "SELECT * FROM reminders WHERE status = ? AND due_at_utc <= ?",
            (STATUS_PENDING, cutoff),
        ).fetchall()
        for row in stale_rows:
            row = dict(row)
            cur = conn.execute(
                "UPDATE reminders SET status = ?, fired_at = ?, details_json = ? "
                "WHERE reminder_id = ? AND status = ?",
                (STATUS_MISSED, now.isoformat(),
                 json.dumps({"missed": True, "stale_after_seconds": int(stale_after_seconds)}, sort_keys=True),
                 row["reminder_id"], STATUS_PENDING),
            )
            if cur.rowcount > 0:
                newly_missed.append({**_row_public(row), "status": STATUS_MISSED, "late": True})
        exhausted = conn.execute(
            "SELECT * FROM reminders WHERE status = ? AND attempts >= ?",
            (STATUS_DELIVERY_FAILED, max(1, int(max_attempts))),
        ).fetchall()
        summary["permanently_failed"] = [_row_public(dict(r)) for r in exhausted]
        conn.commit()
    finally:
        conn.close()
    summary["missed"].extend(newly_missed)
    for missed_row in newly_missed:
        try:
            notify(missed_row)
        except Exception:
            pass  # the miss is durably recorded on the row

    # Still-relevant overdue pending rows + bounded retries fire normally.
    fired = tick(now=now, notify=notify, max_attempts=max_attempts,
                 inflight_grace_seconds=inflight_grace_seconds,
                 now_fn=now_fn, get_connection_fn=get_connection_fn)
    summary["delivered"].extend(fired.get("delivered") or [])
    summary["failed"].extend(fired.get("failed") or [])
    return summary


class ReminderWorker(threading.Thread):
    """The daemon-side ticker: one thread, one interval, stop()-able.

    The first breath after a restart is RECOVERY (crash windows, missed rows,
    exhausted retries), then the steady tick. ``stats`` is updated in place so
    the daemon surface can observe deliveries without another channel."""

    def __init__(
        self,
        *,
        notify: NotifyFn,
        interval_seconds: float = 30.0,
        max_attempts: int = DEFAULT_MAX_DELIVERY_ATTEMPTS,
        now_fn: Callable[[], datetime] = _now_utc,
        get_connection_fn: Callable[[], Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(daemon=True, name="vool-reminder-worker")
        self.notify = notify
        self.interval_seconds = max(0.2, float(interval_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.now_fn = now_fn
        self.get_connection_fn = get_connection_fn
        self.sleep_fn = sleep_fn
        self._halt = threading.Event()
        self.stats = {
            "started_at": "", "stopped_at": "", "ticks": 0, "delivered": 0,
            "delivery_failures": 0, "crash_recovered": 0, "missed": 0,
            "last_error": "", "last_tick_at": "",
        }
        self._started_once = False
        self._lock = threading.Lock()

    def _bump(self, **kwargs: Any) -> None:
        with self._lock:
            self.stats.update(kwargs)

    def _count(self, key: str, n: int) -> None:
        with self._lock:
            self.stats[key] = int(self.stats.get(key) or 0) + n

    def run(self) -> None:
        self._bump(started_at=_now_utc().isoformat())
        try:
            boot = recover(notify=self.notify, max_attempts=self.max_attempts,
                           now_fn=self.now_fn, get_connection_fn=self.get_connection_fn)
            self._count("delivered", len(boot.get("delivered") or []))
            self._count("crash_recovered", len(boot.get("crash_recovered") or []))
            self._count("missed", len(boot.get("missed") or []))
        except Exception as exc:  # a failed boot recovery must not kill the worker
            self._bump(last_error=f"boot: {type(exc).__name__}: {exc}")
        while not self._halt.is_set():
            self._halt.wait(self.interval_seconds)
            if self._halt.is_set():
                break
            try:
                result = tick(notify=self.notify, max_attempts=self.max_attempts,
                              now_fn=self.now_fn, get_connection_fn=self.get_connection_fn)
                self._count("ticks", 1)
                self._count("delivered", len(result.get("delivered") or []))
                self._count("delivery_failures", len(result.get("failed") or []))
                self._bump(last_tick_at=_now_utc().isoformat())
            except Exception as exc:
                self._bump(last_error=f"tick: {type(exc).__name__}: {exc}")
                continue  # a failed tick must not kill the worker
        self._bump(stopped_at=_now_utc().isoformat())

    def start(self) -> None:  # noqa: D102 — guards double-start explicitly
        if self._started_once:
            raise RuntimeError("ReminderWorker.start() called twice; one worker per process lane")
        self._started_once = True
        super().start()

    def stop(self, *, timeout: float = 5.0) -> bool:
        """Signal shutdown and join. Returns True when the thread exited."""
        self._halt.set()
        if not self.is_alive():
            return True
        self.join(timeout=timeout)
        return not self.is_alive()


__all__ = [
    "DEFAULT_MAX_DELIVERY_ATTEMPTS",
    "DEFAULT_STALE_AFTER_SECONDS",
    "ReminderWorker",
    "STATUS_CANCELLED",
    "STATUS_DELIVERED",
    "STATUS_DELIVERY_FAILED",
    "STATUS_FIRED",
    "STATUS_FIRING",
    "STATUS_RECOVERING",
    "STATUS_MISSED",
    "STATUS_PENDING",
    "cancel",
    "list_reminders",
    "recover",
    "schedule",
    "tick",
]
