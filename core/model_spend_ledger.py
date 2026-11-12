from __future__ import annotations

import math
import sqlite3
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
    # Who this call is spent for, when one of the owner's devices has its own monthly limit (a
    # paired phone: ``"phone:<device_id>"``). Its month's total is checked against
    # ``spender_monthly_usd`` in the same transaction, on top of the caps above.
    spender: str = ""
    spender_monthly_usd: float | None = None


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
    # True when this settlement named an invocation the ledger had already settled: nothing was
    # written, and the row is returned as it stands.
    replayed: bool = False


def _finite(cap: object) -> bool:
    try:
        return math.isfinite(float(cap))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


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
            CREATE TABLE IF NOT EXISTS model_spend_invocations (
                model_call_id TEXT NOT NULL,
                invocation_id TEXT NOT NULL,
                ledger_call_id TEXT NOT NULL,
                actual_usd REAL,
                flagged INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (model_call_id, invocation_id)
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(model_spend_reservations)").fetchall()}
        if "spender" not in columns:
            try:
                conn.execute("ALTER TABLE model_spend_reservations ADD COLUMN spender TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as exc:  # another process added it first
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_spend_spender ON model_spend_reservations(spender, created_ts)"
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
    if not _finite(limits.per_call_usd) or amount > max(0.0, float(limits.per_call_usd)):
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
            # A cap that is not a finite number refuses: NaN compares false against any total and inf has no ceiling.
            if not _finite(cap) or cap <= 0 or projected > float(cap) + 1e-9:
                raise PermissionError(reason)
        spender = str(limits.spender or "")
        if limits.spender_monthly_usd is not None:
            spender_cap = float(limits.spender_monthly_usd)
            spender_total = _committed_and_reserved(
                conn, "spender = ? AND created_ts >= ?", (spender, month_start)
            ) if spender else 0.0
            if not spender or not _finite(spender_cap) or spender_cap <= 0 or spender_total + amount > spender_cap + 1e-9:
                raise PermissionError("device_monthly_spend_cap_exceeded")
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
             actual_usd, status, billing_ambiguous, created_ts, created_at, updated_at, spender)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'reserved', 0, ?, ?, ?, ?)""",
            (
                reservation.reservation_id, model_call_id, task_id, subtask_id, model_id,
                amount, ts, now_iso, now_iso, spender,
            ),
        )
        conn.commit()
        return reservation
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def settle_spend(
    model_call_id: str,
    *,
    actual_usd: float | None,
    billing_ambiguous: bool = False,
    invocation_id: str = "",
    add_to_ambiguous: bool = False,
) -> SpendReservation:
    """Close (or keep holding) a reservation at what the call cost.

    ``invocation_id`` names the physical provider response being settled. The ledger records it in
    the same transaction as the money, so settling that response again returns the row unchanged
    (``replayed``) instead of charging it twice, across restarts and concurrent callers.

    ``add_to_ambiguous`` settles against the row as it stands inside the transaction: on a row whose
    earlier attempt left its outcome unknown, ``actual_usd`` is added to the charges already known
    and the hold is kept, so a settlement racing a failed attempt can neither lose a charge nor
    clear the hold.
    """
    _init_table()
    actual = None if actual_usd is None else round(max(0.0, float(actual_usd)), 8)
    billing_ambiguous = billing_ambiguous or actual is None
    invocation = str(invocation_id or "")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if invocation:
            replay = _replayed_row(conn, model_call_id, invocation)
            if replay is not None:
                conn.rollback()
                return replay
        row = conn.execute("SELECT * FROM model_spend_reservations WHERE model_call_id = ?", (model_call_id,)).fetchone()
        if row is None:
            raise KeyError(model_call_id)
        if row["status"] not in {"reserved", "billing_ambiguous"}:
            raise ValueError("reservation_already_finalized")
        if add_to_ambiguous and row["status"] == "billing_ambiguous":
            # Decided here, inside the transaction: the row's unknown attempt stays held and its
            # known charges stay counted, whatever the caller read before.
            billing_ambiguous = True
            if actual is not None:
                actual = round(actual + float(row["actual_usd"]), 8)
        if actual is None:
            actual = float(row["actual_usd"])
        # Unknown billing remains a liability, not a zero-dollar settlement. Only
        # explicit reconciliation may clear this hold with a known total.
        held = float(row["reserved_usd"]) if billing_ambiguous else 0.0
        status = "billing_ambiguous" if billing_ambiguous else ("cap_breached" if actual > float(row["reserved_usd"]) else "settled")
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE model_spend_reservations SET actual_usd = ?, reserved_usd = ?,
            status = ?, billing_ambiguous = ?, updated_at = ? WHERE model_call_id = ?""",
            (actual, held, status, int(billing_ambiguous), now_iso, model_call_id),
        )
        if invocation:
            _record_invocation(conn, model_call_id, invocation, ledger_call_id=model_call_id, usd=actual_usd, flagged=False)
        conn.commit()
        return SpendReservation(
            reservation_id=row["reservation_id"], model_call_id=model_call_id, task_id=row["task_id"],
            subtask_id=row["subtask_id"], model_id=row["model_id"], reserved_usd=held,
            actual_usd=actual, status=status,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_supplemental_charge(
    model_call_id: str,
    *,
    invocation_id: str,
    task_id: str,
    model_id: str,
    actual_usd: float | None,
    hold_usd: float = 0.0,
) -> SpendReservation | None:
    """Record a provider response that arrived after its reservation closed (a paid retry).

    The caps exist to refuse spend that has not happened yet; this money already has, so the row is
    admitted regardless of them, and recording it is what makes the NEXT reservation see the larger
    total. A known amount is booked as settled; an unknown one holds ``hold_usd`` as an ambiguous
    liability rather than reading as free.

    The row is keyed on the invocation, in one transaction with its invocation record, so the same
    response replayed returns the existing row (``replayed``) and books nothing. A settlement that
    names no invocation cannot be told apart from a replay: it is recorded under a fresh id and
    FLAGGED (see :func:`unverified_supplemental_charges`), never dropped. Returns ``None`` when there
    is nothing to record (a known zero, or an unknown outcome with nothing to hold).
    """
    _init_table()
    actual = None if actual_usd is None else round(max(0.0, float(actual_usd)), 8)
    hold = round(max(0.0, float(hold_usd or 0.0)), 8) if actual is None else 0.0
    if (actual is not None and actual <= 0) or (actual is None and hold <= 0):
        return None
    invocation = str(invocation_id or "")
    flagged = not invocation
    if flagged:
        invocation = f"unidentified-{uuid.uuid4().hex}"
    supplemental_id = f"{model_call_id}#{invocation}"
    status = "billing_ambiguous" if actual is None else "settled"
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        replay = _replayed_row(conn, model_call_id, invocation)
        if replay is not None:
            conn.rollback()
            return replay
        ts = datetime.now(timezone.utc).timestamp()
        now_iso = datetime.fromtimestamp(ts, timezone.utc).isoformat()
        reservation_id = f"spend-reservation-{uuid.uuid4().hex}"
        # A retry's charge counts against the same device limit as the call it retried.
        parent = conn.execute(
            "SELECT spender FROM model_spend_reservations WHERE model_call_id = ?", (model_call_id,)
        ).fetchone()
        conn.execute(
            """INSERT INTO model_spend_reservations
            (reservation_id, model_call_id, task_id, subtask_id, model_id, reserved_usd,
             actual_usd, status, billing_ambiguous, created_ts, created_at, updated_at, spender)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reservation_id, supplemental_id, task_id or "unknown_task", SUPPLEMENTAL_SUBTASK_ID, model_id,
                hold, actual or 0.0, status, int(actual is None), ts, now_iso, now_iso,
                str(parent["spender"] or "") if parent is not None else "",
            ),
        )
        _record_invocation(conn, model_call_id, invocation, ledger_call_id=supplemental_id, usd=actual, flagged=flagged)
        conn.commit()
        return SpendReservation(
            reservation_id=reservation_id, model_call_id=supplemental_id, task_id=task_id or "unknown_task",
            subtask_id=SUPPLEMENTAL_SUBTASK_ID, model_id=model_id, reserved_usd=hold,
            actual_usd=actual or 0.0, status=status,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _replayed_row(conn, model_call_id: str, invocation_id: str) -> SpendReservation | None:
    """The ledger row an already-settled invocation wrote, marked replayed; else ``None``."""
    seen = conn.execute(
        "SELECT ledger_call_id FROM model_spend_invocations WHERE model_call_id = ? AND invocation_id = ?",
        (model_call_id, invocation_id),
    ).fetchone()
    if seen is None:
        return None
    row = conn.execute(
        "SELECT * FROM model_spend_reservations WHERE model_call_id = ?", (seen["ledger_call_id"],)
    ).fetchone()
    if row is None:
        # The invocation is on record but its row is gone: the response WAS settled once, so a
        # replay still books nothing. Report it as an empty settled row.
        return SpendReservation(
            reservation_id="", model_call_id=str(seen["ledger_call_id"]), task_id="", subtask_id="",
            model_id="", reserved_usd=0.0, actual_usd=0.0, status="settled", replayed=True,
        )
    return SpendReservation(
        reservation_id=str(row["reservation_id"] or ""), model_call_id=str(row["model_call_id"] or ""),
        task_id=str(row["task_id"] or ""), subtask_id=str(row["subtask_id"] or ""),
        model_id=str(row["model_id"] or ""), reserved_usd=float(row["reserved_usd"] or 0.0),
        actual_usd=float(row["actual_usd"] or 0.0), status=str(row["status"] or ""), replayed=True,
    )


def _record_invocation(conn, model_call_id: str, invocation_id: str, *, ledger_call_id: str, usd: float | None, flagged: bool) -> None:
    conn.execute(
        """INSERT INTO model_spend_invocations
        (model_call_id, invocation_id, ledger_call_id, actual_usd, flagged, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (model_call_id, invocation_id, ledger_call_id, usd, int(flagged), datetime.now(timezone.utc).isoformat()),
    )


def unverified_supplemental_charges() -> list[dict[str, object]]:
    """Charges booked on a closed reservation by a settlement that named no invocation.

    Each one is real money recorded against the caps that may also be a replay of an earlier
    response; they are listed so a person can reconcile them against the provider's bill.
    """
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT model_call_id, invocation_id, ledger_call_id, actual_usd, created_at
            FROM model_spend_invocations WHERE flagged = 1 ORDER BY created_at"""
        ).fetchall()
        return [dict(row) for row in rows]
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


def unresolved_spend_today(*, now: float | None = None) -> float:
    """Unpriced billing still held against today's caps, separate from known charges."""
    _init_table()
    ts = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(reserved_usd), 0) FROM model_spend_reservations "
            "WHERE status = 'billing_ambiguous' AND created_ts >= ?",
            (ts - ts % 86400,),
        ).fetchone()
        return float(row[0])
    finally:
        conn.close()


def spender_month_total(spender: str, *, now: float | None = None) -> float:
    """Known charges plus open holds booked for ``spender`` in the current UTC month."""
    if not spender:
        return 0.0
    _init_table()
    ts = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    month_start = datetime.fromtimestamp(ts, timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    conn = get_connection()
    try:
        return _committed_and_reserved(conn, "spender = ? AND created_ts >= ?", (str(spender), month_start))
    finally:
        conn.close()


def _committed_and_reserved(conn, where: str, params: tuple[object, ...]) -> float:
    row = conn.execute(
        f"""SELECT COALESCE(SUM(actual_usd + CASE WHEN status IN ('reserved', 'billing_ambiguous') THEN reserved_usd ELSE 0 END), 0)
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
    "record_supplemental_charge",
    "release_spend",
    "reserve_spend",
    "settle_spend",
    "spender_month_total",
    "unresolved_spend_today",
    "unverified_supplemental_charges",
]
