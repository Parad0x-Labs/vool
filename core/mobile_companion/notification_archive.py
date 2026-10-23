"""Durable, device-scoped inbox projection; never a payment or approval authority."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager


@contextmanager
def connection():
    from core.runtime_paths import active_data_dir

    path = active_data_dir() / "mobile_companion" / "notification_archive.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600) if hasattr(os, "fchmod") else os.chmod(path, 0o600)
    os.close(descriptor)
    conn = sqlite3.connect(path, timeout=5)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS notification_archive (
            device_id TEXT NOT NULL, source_seq INTEGER NOT NULL, source_id TEXT NOT NULL,
            event_json TEXT NOT NULL, PRIMARY KEY(device_id,source_seq), UNIQUE(device_id,source_id))""")
        yield conn
        conn.commit()
    finally:
        conn.close()


def append(device_id, events):
    if not events:
        return
    with connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO notification_archive VALUES (?,?,?,?)",
            [
                (device_id, int(event["source_seq"]), event["source_id"], json.dumps(event, sort_keys=True))
                for event in events
            ],
        )


def page(device_id, *, before=0, limit=200):
    with connection() as conn:
        rows = conn.execute(
            """SELECT source_seq,event_json FROM notification_archive
            WHERE device_id=? AND (?=0 OR source_seq<?) ORDER BY source_seq DESC LIMIT ?""",
            (device_id, before, before, limit + 1),
        ).fetchall()
    selected = rows[:limit]
    has_more = len(rows) > limit
    events = [dict(json.loads(row[1]), seq=row[0], record_seq=row[0]) for row in reversed(selected)]
    # An archived prompt is not evidence that approval is still pending. The
    # canonical inbox retains closed prompts, but its outcome can be unknown.
    pending = [event for event in events if event["event_type"] == "task_pending_approval"]
    if pending:
        from storage.db import get_connection

        conn = get_connection()
        try:
            placeholders = ",".join("?" for _ in pending)
            rows = conn.execute(
                f"SELECT notification_id,superseded_at,payload_json FROM notification_items WHERE notification_id IN ({placeholders})",
                [event["source_id"] for event in pending],
            ).fetchall()
            from core.mode_permission_policy import approval_is_pending

            closed = {
                row[0]
                for row in rows
                if row[1]
                or (
                    json.loads(row[2]).get("approval_id") and not approval_is_pending(json.loads(row[2])["approval_id"])
                )
            }
        finally:
            conn.close()
        for event in pending:
            if event["source_id"] in closed:
                event.update(
                    approval_state="closed_outcome_unknown",
                    message="An action is no longer waiting. Check the result on your computer.",
                )
    return events, selected[-1][0] if has_more else 0
