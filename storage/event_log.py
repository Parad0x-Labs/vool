from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection
from storage.event_hash_chain import append_hashed_event


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log_v2 (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                actor TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                trace_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_log_trace_id ON event_log_v2(trace_id)"
        )
        conn.commit()
    finally:
        conn.close()


def append_event(
    *,
    category: str,
    actor: str,
    target_type: str,
    target_id: str | None,
    payload: dict[str, Any],
    trace_id: str | None = None,
    event_id: str | None = None,
) -> str:
    _init_table()
    event_id = event_id or str(uuid.uuid4())
    trace_id = trace_id or payload.get("trace_id")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO event_log_v2 (
                event_id, category, actor, target_type, target_id, trace_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                category,
                actor,
                target_type,
                target_id,
                trace_id,
                json.dumps(payload, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    append_hashed_event(
        event_id,
        {
            "category": category,
            "actor": actor,
            "target_type": target_type,
            "target_id": target_id,
            "trace_id": trace_id,
            "payload": payload,
        },
    )
    return event_id


def events_for_trace(trace_id: str) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT event_id, category, actor, target_type, target_id, trace_id, payload_json, created_at
            FROM event_log_v2
            WHERE trace_id = ?
            ORDER BY seq ASC
            """,
            (trace_id,),
        ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "category": row["category"],
                "actor": row["actor"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "trace_id": row["trace_id"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()
