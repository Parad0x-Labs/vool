from __future__ import annotations

import json
import time
from dataclasses import dataclass

from storage.db import get_connection
from storage.migrations import run_migrations


@dataclass(frozen=True)
class MeetWriteLimitReservation:
    allowed: bool
    reason: str
    bucket_key: str
    used_requests: int
    limit_per_minute: int
    window_seconds: int


def reserve_meet_write_rate_limit(
    bucket_key: str,
    limit_per_minute: int,
    *,
    window_seconds: int = 60,
    metadata: dict[str, object] | None = None,
    now_ts: float | None = None,
) -> MeetWriteLimitReservation:
    clean_bucket = str(bucket_key or "").strip()
    limit = max(0, int(limit_per_minute))
    window = max(1, int(window_seconds))
    if limit <= 0:
        return MeetWriteLimitReservation(
            allowed=True,
            reason="limit_disabled",
            bucket_key=clean_bucket,
            used_requests=0,
            limit_per_minute=limit,
            window_seconds=window,
        )
    if not clean_bucket:
        return MeetWriteLimitReservation(
            allowed=False,
            reason="missing_bucket_key",
            bucket_key=clean_bucket,
            used_requests=0,
            limit_per_minute=limit,
            window_seconds=window,
        )

    _init_table()
    event_ts = float(now_ts if now_ts is not None else time.time())
    cutoff = event_ts - float(window)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM meet_write_rate_limit_events WHERE window_seconds = ? AND created_at_epoch < ?",
            (window, cutoff),
        )
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM meet_write_rate_limit_events
            WHERE bucket_key = ?
              AND window_seconds = ?
              AND created_at_epoch >= ?
            """,
            (clean_bucket, window, cutoff),
        ).fetchone()
        used_requests = int(row["total"] or 0) if row else 0
        if used_requests >= limit:
            conn.rollback()
            return MeetWriteLimitReservation(
                allowed=False,
                reason="rate_limit_exceeded",
                bucket_key=clean_bucket,
                used_requests=used_requests,
                limit_per_minute=limit,
                window_seconds=window,
            )
        conn.execute(
            """
            INSERT INTO meet_write_rate_limit_events (
                bucket_key,
                window_seconds,
                metadata_json,
                created_at_epoch,
                created_at
            ) VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                clean_bucket,
                window,
                json.dumps(dict(metadata or {}), sort_keys=True),
                event_ts,
            ),
        )
        conn.commit()
        return MeetWriteLimitReservation(
            allowed=True,
            reason="reserved",
            bucket_key=clean_bucket,
            used_requests=used_requests + 1,
            limit_per_minute=limit,
            window_seconds=window,
        )
    except Exception:
        conn.rollback()
        return MeetWriteLimitReservation(
            allowed=False,
            reason="rate_limit_storage_error",
            bucket_key=clean_bucket,
            used_requests=0,
            limit_per_minute=limit,
            window_seconds=window,
        )
    finally:
        conn.close()


def _init_table() -> None:
    run_migrations()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meet_write_rate_limit_events' LIMIT 1"
        ).fetchone()
        if not row:
            raise RuntimeError("meet_write_rate_limit_events table is missing after migrations.")
    finally:
        conn.close()
