"""Durable, device-scoped inbox projection; never a payment or approval authority."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager


def archive_path():
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "mobile_companion" / "notification_archive.sqlite3"


@contextmanager
def connection():
    path = archive_path()
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


def forget(token, *, chat_id, carries):
    """Erase the archived phone notifications that carry a forgotten text: rows of this chat, and rows that name no
    chat (the same derivative law as every other forget store). Returns ``(erased rows of this chat, verified)``;
    ``None`` for ``verified`` when no archive exists on this computer.

    Each row is a copy the phone's inbox reads back (``detail`` is the notification title, which can be a reminder's
    own words), so a "forget" said in the chat must reach it. The wallet's ledger and receipts are not here."""
    path = archive_path()
    if not path.exists():
        return 0, None

    def scope(event):
        return str(event.get("session_id") or "") if isinstance(event, dict) else ""

    with connection() as conn:
        conn.execute("PRAGMA secure_delete = ON")  # an erased row's bytes are zeroed, not left in a free page
        conn.execute("BEGIN IMMEDIATE")
        counted = 0
        for rowid, raw in conn.execute("SELECT rowid, event_json FROM notification_archive").fetchall():
            event = json.loads(raw)
            if scope(event) not in {"", chat_id} or not carries(event, token):
                continue
            conn.execute("DELETE FROM notification_archive WHERE rowid = ?", (rowid,))
            counted += 1 if scope(event) == chat_id else 0
        conn.commit()
        retained = any(
            scope(event) in {"", chat_id} and carries(event, token)
            for event in (json.loads(raw) for (raw,) in conn.execute("SELECT event_json FROM notification_archive"))
        )
    return counted, not retained

