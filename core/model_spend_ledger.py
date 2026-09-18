from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from storage.db import get_connection

# Reservations written to record money that was ALREADY spent (a retry the provider billed under
# a reservation that had closed). They are charges, not fresh calls, so they must not consume a
# slot in the daily call cap — otherwise a single retried turn would eat two of the user's calls.
SUPPLEMENTAL_SUBTASK_ID = "paid_retry_charge"


@dataclass(frozen=True)
class SpendLimits:
    per_call_usd: float
    per_task_usd: float
    daily_usd: float
    monthly_usd: float
    # Optional cap on the NUMBER of paid calls admitted per UTC day, enforced inside the same
    # transaction that reserves the dollars (see reserve_spend). ``None`` leaves the count
    # ungated, which is what every caller that only cares about dollars wants.
    daily_call_cap: int | None = None


@dataclass(frozen=True)
class SpendReservation:
    reservation_id: str
    model_call_id: str
    task_id: str
    subtask_id: str
    model_id: str
    reserved_usd: float
    actual_usd: float
    status: str


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_spend_reservations (
                reservation_id TEXT PRIMARY KEY,
                model_call_id TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                subtask_id TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL,
                reserved_usd REAL NOT NULL,
                actual_usd REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                billing_ambiguous INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_model_spend_task ON model_spend_reservations(task_id, created_ts);
            CREATE INDEX IF NOT EXISTS idx_model_spend_status ON model_spend_reservations(status, created_ts);
            """
        )
        conn.commit()
    finally:
        conn.close()


def reserve_spend(
    *,
    model_call_id: str,
    task_id: str,
    subtask_id: str,
    model_id: str,
    maximum_usd: float,
    limits: SpendLimits,
    now: float | None = None,
) -> SpendReservation:
    amount = round(max(0.0, float(maximum_usd)), 8)
    if not model_call_id or not task_id or amount <= 0:
        raise ValueError("model_call_id, task_id, and a positive reservation are required")
    if amount > max(0.0, float(limits.per_call_usd)):
        raise PermissionError("per_call_spend_cap_exceeded")
    _init_table()
    ts = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    day_start = ts - (ts % 86400)
    month_start = datetime.fromtimestamp(ts, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task_total = _committed_and_reserved(conn, "task_id = ?", (task_id,))
        day_total = _committed_and_reserved(conn, "created_ts >= ?", (day_start,))
        month_total = _committed_and_reserved(conn, "created_ts >= ?", (month_start,))
        checks = (
            (task_total + amount, limits.per_task_usd, "per_task_spend_cap_exceeded"),
            (day_total + amount, limits.daily_usd, "daily_spend_cap_exceeded"),
            (month_total + amount, limits.monthly_usd, "monthly_spend_cap_exceeded"),
        )
        for projected, cap, reason in checks:
            if cap <= 0 or projected > float(cap) + 1e-9:
                raise PermissionError(reason)
        # The call-count cap is checked HERE, inside the BEGIN IMMEDIATE that reserves the
        # dollars, and the row inserted below is the increment. Previously the count was read
        # (cloud_escalation_policy.used_today) and written (record_escalation) on either side of
        # this transaction, so concurrent turns all read the same pre-call count and every one of
        # them passed a cap they should not have. Counting the ledger's own rows makes the check
        # and its increment the same atomic act as reserving the money.
        call_cap = limits.daily_call_cap
        if call_cap is not None:
            cap_int = int(call_cap)
            if cap_int <= 0 or _calls_today(conn, day_start) + 1 > cap_int:
                raise PermissionError("daily_call_cap_exceeded")
        reservation = SpendReservation(
            reservation_id=f"spend-reservation-{uuid.uuid4().hex}",
            model_call_id=model_call_id,
            task_id=task_id,
            subtask_id=subtask_id,
            model_id=model_id,
            reserved_usd=amount,
            actual_usd=0.0,
            status="reserved",
        )
        now_iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO model_spend_reservations
            (reservation_id, model_call_id, task_id, subtask_id, model_id, reserved_usd,
             actual_usd, status, billing_ambiguous, created_ts, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'reserved', 0, ?, ?, ?)""",
            (
                reservation.reservation_id, model_call_id, task_id, subtask_id, model_id,
                amount, ts, now_iso, now_iso,
            ),
        )
        conn.commit()
        return reservation
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def settle_spend(model_call_id: str, *, actual_usd: float, billing_ambiguous: bool = False) -> SpendReservation:
    _init_table()
    actual = round(max(0.0, float(actual_usd)), 8)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM model_spend_reservations WHERE model_call_id = ?", (model_call_id,)).fetchone()
        if row is None:
            raise KeyError(model_call_id)
        if row["status"] not in {"reserved", "billing_ambiguous"}:
            raise ValueError("reservation_already_finalized")
        status = "billing_ambiguous" if billing_ambiguous else ("cap_breached" if actual > float(row["reserved_usd"]) else "settled")
        conn.execute(
            """UPDATE model_spend_reservations SET actual_usd = ?, reserved_usd = 0,
            status = ?, billing_ambiguous = ?, updated_at = ? WHERE model_call_id = ?""",
            (actual, status, int(billing_ambiguous), datetime.now(timezone.utc).isoformat(), model_call_id),
        )
        conn.commit()
        return SpendReservation(
            reservation_id=row["reservation_id"], model_call_id=model_call_id, task_id=row["task_id"],
            subtask_id=row["subtask_id"], model_id=row["model_id"], reserved_usd=0.0,
            actual_usd=actual, status=status,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_spend(model_call_id: str, *, reason: str = "released") -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE model_spend_reservations SET reserved_usd = 0, status = ?, updated_at = ?
            WHERE model_call_id = ? AND status = 'reserved'""",
            (reason, datetime.now(timezone.utc).isoformat(), model_call_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_spend_reservation(model_call_id: str) -> SpendReservation | None:
    """Read one authoritative reservation row by call id, or ``None`` when absent."""
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM model_spend_reservations WHERE model_call_id = ?",
            (str(model_call_id or ""),),
        ).fetchone()
        if row is None:
            return None
        return SpendReservation(
            reservation_id=str(row["reservation_id"] or ""),
            model_call_id=str(row["model_call_id"] or ""),
            task_id=str(row["task_id"] or ""),
            subtask_id=str(row["subtask_id"] or ""),
            model_id=str(row["model_id"] or ""),
            reserved_usd=float(row["reserved_usd"] or 0.0),
            actual_usd=float(row["actual_usd"] or 0.0),
            status=str(row["status"] or ""),
        )
    finally:
        conn.close()


def _calls_today(conn, day_start: float) -> int:
    """Paid calls already admitted today, counted from the ledger's own rows.

    Every admitted call leaves exactly one row, whatever became of it afterwards: a reservation
    that was released because the provider failed still counts, because the cap bounds attempts
    on the user's key rather than successes. That matches what the previous counter did — it was
    incremented at authorization time and never decremented — so this changes when the count is
    read, not what it counts.
    """
    row = conn.execute(
        """SELECT COUNT(*) FROM model_spend_reservations
        WHERE created_ts >= ? AND subtask_id <> ?""",
        (day_start, SUPPLEMENTAL_SUBTASK_ID),
    ).fetchone()
    return int(row[0] or 0)


def calls_today(*, now: float | None = None) -> int:
    """Paid calls admitted so far in the current UTC day, read from the ledger."""
    _init_table()
    ts = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    conn = get_connection()
    try:
        return _calls_today(conn, ts - (ts % 86400))
    finally:
        conn.close()


def _committed_and_reserved(conn, where: str, params: tuple[object, ...]) -> float:
    row = conn.execute(
        f"""SELECT COALESCE(SUM(actual_usd + CASE WHEN status = 'reserved' THEN reserved_usd ELSE 0 END), 0)
        FROM model_spend_reservations WHERE {where}""",
        params,
    ).fetchone()
    return float(row[0] or 0.0)


__all__ = [
    "SUPPLEMENTAL_SUBTASK_ID",
    "SpendLimits",
    "SpendReservation",
    "calls_today",
    "get_spend_reservation",
    "release_spend",
    "reserve_spend",
    "settle_spend",
]
