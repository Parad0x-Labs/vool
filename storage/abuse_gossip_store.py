from __future__ import annotations

from datetime import datetime, timezone

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS abuse_gossip_seen (
                report_id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                seen_count INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS abuse_gossip_reporters (
                reporter_peer_id TEXT NOT NULL,
                minute_bucket TEXT NOT NULL,
                report_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (reporter_peer_id, minute_bucket)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def mark_report_seen(report_id: str) -> bool:
    """
    Returns True only on first observation of a report ID.
    Subsequent observations update counters and return False.
    """
    _init_table()
    now = _utcnow()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO abuse_gossip_seen (
                report_id, first_seen_at, last_seen_at, seen_count
            ) VALUES (?, ?, ?, 1)
            """,
            (report_id, now, now),
        )
        if int(cur.rowcount or 0) > 0:
            conn.commit()
            return True
        conn.execute(
            """
            UPDATE abuse_gossip_seen
            SET seen_count = seen_count + 1,
                last_seen_at = ?
            WHERE report_id = ?
            """,
            (now, report_id),
        )
        conn.commit()
        return False
    finally:
        conn.close()


def allow_reporter_report(reporter_peer_id: str, *, per_minute_limit: int = 10) -> bool:
    _init_table()
    reporter = str(reporter_peer_id or "").strip()
    if not reporter:
        return False
    now = datetime.now(timezone.utc)
    bucket = now.strftime("%Y-%m-%dT%H:%M")
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT report_count
            FROM abuse_gossip_reporters
            WHERE reporter_peer_id = ? AND minute_bucket = ?
            LIMIT 1
            """,
            (reporter, bucket),
        ).fetchone()
        current = int(row["report_count"]) if row else 0
        if current >= max(1, int(per_minute_limit)):
            return False
        if row:
            conn.execute(
                """
                UPDATE abuse_gossip_reporters
                SET report_count = report_count + 1
                WHERE reporter_peer_id = ? AND minute_bucket = ?
                """,
                (reporter, bucket),
            )
        else:
            conn.execute(
                """
                INSERT INTO abuse_gossip_reporters (
                    reporter_peer_id, minute_bucket, report_count
                ) VALUES (?, ?, 1)
                """,
                (reporter, bucket),
            )
        conn.commit()
        return True
    finally:
        conn.close()
