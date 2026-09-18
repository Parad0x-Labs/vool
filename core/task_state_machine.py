from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.trace_id import ensure_trace, trace_for_task
from storage.db import get_connection


@dataclass(frozen=True)
class TaskStateRecord:
    entity_type: str
    entity_id: str
    state: str
    trace_id: str


_TRANSITIONS = {
    None: {"created", "offered", "claimed", "assigned", "running", "completed", "cancelled"},
    "created": {"offered", "completed", "finalized", "cancelled"},
    "offered": {"claimed", "timed_out", "cancelled"},
    "claimed": {"assigned", "timed_out", "cancelled"},
    "assigned": {"running", "timed_out", "cancelled"},
    "running": {"completed", "timed_out", "disputed", "cancelled"},
    "completed": {"finalized", "disputed"},
    "timed_out": {"offered", "cancelled", "finalized"},
    "disputed": {"finalized", "cancelled"},
    "finalized": set(),
    "cancelled": set(),
}
_TABLE_READY = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_state_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_state_entity ON task_state_events(entity_type, entity_id, seq)"
        )
        conn.commit()
        _TABLE_READY = True
    finally:
        conn.close()


def current_state(entity_type: str, entity_id: str) -> str | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT to_state
            FROM task_state_events
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY seq DESC
            LIMIT 1
            """,
            (entity_type, entity_id),
        ).fetchone()
        return str(row["to_state"]) if row else None
    finally:
        conn.close()


def transition(
    *,
    entity_type: str,
    entity_id: str,
    to_state: str,
    details: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> TaskStateRecord:
    _init_table()
    previous = current_state(entity_type, entity_id)
    if previous == to_state:
        trace = trace_for_task(entity_id) or ensure_trace(entity_id, trace_id=trace_id or entity_id)
        return TaskStateRecord(entity_type=entity_type, entity_id=entity_id, state=to_state, trace_id=trace.trace_id)
    allowed = _TRANSITIONS.get(previous, set())
    if to_state not in allowed:
        raise ValueError(f"Illegal task state transition for {entity_type}:{entity_id}: {previous!r} -> {to_state!r}")

    trace = trace_for_task(entity_id) or ensure_trace(entity_id, trace_id=trace_id or entity_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_state_events (
                entity_type, entity_id, from_state, to_state, trace_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                entity_id,
                previous,
                to_state,
                trace.trace_id,
                json.dumps(details or {}, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return TaskStateRecord(entity_type=entity_type, entity_id=entity_id, state=to_state, trace_id=trace.trace_id)
