"""The user-facing local task store (B10, PA lane).

Distinct from ``local_tasks`` — that table is the runtime's INTERNAL per-turn
audit/classification ledger and must not be conflated with anything the user
creates. This store is the user's own todo list: one authority, durable rows,
typed transitions. Local-only by design; KAS sync connectors come later under
the KAS boundary law and are NOT built here.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection():
    from storage.db import get_connection

    return get_connection()


def _row_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(row.get("task_id") or ""),
        "title": str(row.get("title") or ""),
        "notes": str(row.get("notes") or ""),
        "status": str(row.get("status") or ""),
        "due_at_utc": str(row.get("due_at_utc") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
    }


def create_task(
    *,
    title: str,
    notes: str = "",
    due_at_utc: str = "",
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    title = " ".join(str(title or "").split()).strip()
    if not title:
        raise ValueError("a task needs a title")
    task_id = str(uuid.uuid4())
    now = now_fn()
    conn = (get_connection_fn or _get_connection)()
    try:
        conn.execute(
            """
            INSERT INTO user_tasks (task_id, title, notes, status, due_at_utc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, title[:500], str(notes or "")[:5000], STATUS_OPEN, str(due_at_utc or ""), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"task_id": task_id, "title": title, "status": STATUS_OPEN}


def list_tasks(
    *,
    include_done: bool = False,
    get_connection_fn: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    conn = (get_connection_fn or _get_connection)()
    try:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM user_tasks ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM user_tasks WHERE status = ? ORDER BY created_at DESC LIMIT 500",
                (STATUS_OPEN,),
            ).fetchall()
        return [_row_public(dict(r)) for r in rows]
    finally:
        conn.close()


def _load(task_id: str, conn: Any) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM user_tasks WHERE task_id = ?", (str(task_id or ""),)).fetchone()
    return dict(row) if row else None


def complete_task(
    task_id: str,
    *,
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Idempotent completion: completing a done task reports already_done; a
    cancelled task cannot be completed."""
    conn = (get_connection_fn or _get_connection)()
    try:
        row = _load(task_id, conn)
        if row is None:
            return {"ok": False, "status": "not_found"}
        status = str(row.get("status") or "")
        if status == STATUS_DONE:
            return {"ok": True, "status": "already_done", "task": _row_public(row)}
        if status == STATUS_CANCELLED:
            return {"ok": False, "status": "cancelled", "task": _row_public(row)}
        now = now_fn()
        conn.execute(
            "UPDATE user_tasks SET status = ?, completed_at = ?, updated_at = ? WHERE task_id = ?",
            (STATUS_DONE, now, now, str(task_id)),
        )
        conn.commit()
        return {"ok": True, "status": "done", "task": _row_public(_load(task_id, conn) or row)}
    finally:
        conn.close()


def cancel_task(
    task_id: str,
    *,
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Cancellation is final for an open task; done tasks keep their history."""
    conn = (get_connection_fn or _get_connection)()
    try:
        row = _load(task_id, conn)
        if row is None:
            return {"ok": False, "status": "not_found"}
        status = str(row.get("status") or "")
        if status == STATUS_DONE:
            return {"ok": False, "status": "already_done", "task": _row_public(row)}
        if status == STATUS_CANCELLED:
            return {"ok": True, "status": "already_cancelled", "task": _row_public(row)}
        conn.execute(
            "UPDATE user_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (STATUS_CANCELLED, now_fn(), str(task_id)),
        )
        conn.commit()
        return {"ok": True, "status": "cancelled", "task": _row_public(_load(task_id, conn) or row)}
    finally:
        conn.close()


def get_task(
    task_id: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    conn = (get_connection_fn or _get_connection)()
    try:
        row = _load(task_id, conn)
        return _row_public(row) if row else None
    finally:
        conn.close()


__all__ = [
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_OPEN",
    "cancel_task",
    "complete_task",
    "create_task",
    "get_task",
    "list_tasks",
]
