from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from storage.db import get_connection

_TABLE_READY = False


@dataclass(frozen=True)
class TraceRecord:
    task_id: str
    trace_id: str
    parent_trace_id: str | None
    span_id: str


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
            CREATE TABLE IF NOT EXISTS task_trace_index (
                task_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_trace_id TEXT,
                span_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_trace_trace_id ON task_trace_index(trace_id)"
        )
        conn.commit()
        _TABLE_READY = True
    finally:
        conn.close()


def ensure_trace(task_id: str, *, parent_trace_id: str | None = None, trace_id: str | None = None) -> TraceRecord:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT task_id, trace_id, parent_trace_id, span_id FROM task_trace_index WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if row:
            return TraceRecord(
                task_id=row["task_id"],
                trace_id=row["trace_id"],
                parent_trace_id=row["parent_trace_id"],
                span_id=row["span_id"],
            )

        resolved_trace_id = trace_id or parent_trace_id or task_id
        record = TraceRecord(
            task_id=task_id,
            trace_id=resolved_trace_id,
            parent_trace_id=parent_trace_id,
            span_id=task_id,
        )
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO task_trace_index (
                task_id, trace_id, parent_trace_id, span_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (record.task_id, record.trace_id, record.parent_trace_id, record.span_id, now, now),
        )
        conn.commit()
        return record
    finally:
        conn.close()


def trace_for_task(task_id: str) -> TraceRecord | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT task_id, trace_id, parent_trace_id, span_id FROM task_trace_index WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return TraceRecord(
            task_id=row["task_id"],
            trace_id=row["trace_id"],
            parent_trace_id=row["parent_trace_id"],
            span_id=row["span_id"],
        )
    finally:
        conn.close()


def tasks_for_trace(trace_id: str) -> list[TraceRecord]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, trace_id, parent_trace_id, span_id
            FROM task_trace_index
            WHERE trace_id = ?
            ORDER BY created_at ASC
            """,
            (trace_id,),
        ).fetchall()
        return [
            TraceRecord(
                task_id=row["task_id"],
                trace_id=row["trace_id"],
                parent_trace_id=row["parent_trace_id"],
                span_id=row["span_id"],
            )
            for row in rows
        ]
    finally:
        conn.close()
