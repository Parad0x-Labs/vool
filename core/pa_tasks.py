"""The person's own to-do list: one local table, durable rows, typed transitions.

Distinct from ``local_tasks``: that table is the runtime's INTERNAL per-turn audit ledger and must
not be mixed with anything the person creates. This store is the list the person keeps in VOOL by
saying "add milk to my to-do list", "tick off milk" and "what's on my to-do list" (the chat side is
core/todo_list_turn.py). Local only: nothing here syncs or leaves the computer.

Ported from the PA capabilities lane (build/pa-capabilities-20260909 @ 53383844, kept in
recovery/historical-gold/pa-capabilities). Changes on the port:

* The table is created on first use (``ensure_table``), like the other self-owned stores in
  storage/, so the store-version ledger is not bumped for one owner-managed table.
* The list reads oldest first, so the numbers a person sees stay put while new items are added.
* ``remove_task`` deletes the row: "take it off my list" means it is gone, not kept as cancelled.
* ``find_open_tasks`` matches the person's words to open items (exact, then contained, then all
  words), so the chat side can refuse an ambiguous tick-off instead of guessing.

Like a reminder, an item is an object the person made and manages. A chat "forget" does not erase it
(core/memory/run_history.py keeps such tables out of run-history erasure); the table carries no
chat id, and removing an item is how the person deletes it.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

TITLE_MAX = 500
OPEN_LIMIT = 500

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS user_tasks (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open',
        due_at_utc TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_user_tasks_status ON user_tasks(status, created_at ASC)",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection():
    from storage.db import get_connection

    return get_connection()


def ensure_table(conn: Any) -> None:
    """Create the table on this connection's database if it is not there yet (idempotent DDL;
    cheap enough to run on every open, so a store deleted and made again never loses it)."""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()


def _open(get_connection_fn: Callable[[], Any] | None) -> Any:
    conn = (get_connection_fn or _get_connection)()
    try:
        ensure_table(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _row_public(row: Any) -> dict[str, Any]:
    row = dict(row)
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


def clean_title(title: str) -> str:
    return " ".join(str(title or "").split()).strip()[:TITLE_MAX]


def create_task(
    *,
    title: str,
    notes: str = "",
    due_at_utc: str = "",
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    title = clean_title(title)
    if not title:
        raise ValueError("a task needs a title")
    task_id = str(uuid.uuid4())
    now = now_fn()
    conn = _open(get_connection_fn)
    try:
        conn.execute(
            """
            INSERT INTO user_tasks (task_id, title, notes, status, due_at_utc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, title, str(notes or "")[:5000], STATUS_OPEN, str(due_at_utc or ""), now, now),
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
    """Open items oldest first (the order the person added them). With ``include_done``, the done
    items follow, most recently ticked off first."""
    conn = _open(get_connection_fn)
    try:
        rows = conn.execute(
            "SELECT * FROM user_tasks WHERE status = ? ORDER BY created_at ASC, rowid ASC LIMIT ?",
            (STATUS_OPEN, OPEN_LIMIT),
        ).fetchall()
        result = [_row_public(r) for r in rows]
        if include_done:
            done = conn.execute(
                "SELECT * FROM user_tasks WHERE status = ? ORDER BY completed_at DESC, rowid DESC LIMIT ?",
                (STATUS_DONE, OPEN_LIMIT),
            ).fetchall()
            result.extend(_row_public(r) for r in done)
        return result
    finally:
        conn.close()


def has_any_task(*, get_connection_fn: Callable[[], Any] | None = None) -> bool:
    """True once the person has ever kept an item here (open, done or cancelled)."""
    conn = _open(get_connection_fn)
    try:
        return conn.execute("SELECT 1 FROM user_tasks LIMIT 1").fetchone() is not None
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
    """Idempotent completion: completing a done task reports already_done; a cancelled task
    cannot be completed. The UPDATE is a compare-and-swap on ``open``, so two ticks of the same
    item record one completion."""
    conn = _open(get_connection_fn)
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
        changed = conn.execute(
            "UPDATE user_tasks SET status = ?, completed_at = ?, updated_at = ? WHERE task_id = ? AND status = ?",
            (STATUS_DONE, now, now, str(task_id), STATUS_OPEN),
        ).rowcount
        conn.commit()
        fresh = _load(task_id, conn) or row
        if not changed:
            return {"ok": True, "status": "already_done", "task": _row_public(fresh)}
        return {"ok": True, "status": "done", "task": _row_public(fresh)}
    finally:
        conn.close()


def cancel_task(
    task_id: str,
    *,
    now_fn: Callable[[], str] = _now_iso,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Cancellation is final for an open task; done tasks keep their history."""
    conn = _open(get_connection_fn)
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
            "UPDATE user_tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
            (STATUS_CANCELLED, now_fn(), str(task_id), STATUS_OPEN),
        )
        conn.commit()
        return {"ok": True, "status": "cancelled", "task": _row_public(_load(task_id, conn) or row)}
    finally:
        conn.close()


def remove_task(
    task_id: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Delete one item. Removing an item that is already gone reports not_found."""
    conn = _open(get_connection_fn)
    try:
        row = _load(task_id, conn)
        if row is None:
            return {"ok": False, "status": "not_found"}
        conn.execute("DELETE FROM user_tasks WHERE task_id = ?", (str(task_id),))
        conn.commit()
        return {"ok": True, "status": "removed", "task": _row_public(row)}
    finally:
        conn.close()


def get_task(
    task_id: str,
    *,
    get_connection_fn: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    conn = _open(get_connection_fn)
    try:
        row = _load(task_id, conn)
        return _row_public(row) if row else None
    finally:
        conn.close()


_MATCH_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_MATCH_STOP = frozenset({"the", "a", "an", "my", "to", "of", "and", "for", "on", "in", "at", "with", "about"})


def _match_key(text: str) -> str:
    return " ".join(_MATCH_WORD_RE.findall(str(text or "").lower().replace("’", "'")))


def _match_words(text: str) -> set[str]:
    words = set()
    for word in _MATCH_WORD_RE.findall(str(text or "").lower().replace("’", "'")):
        if word in _MATCH_STOP:
            continue
        words.add(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
    return words


def find_open_tasks(
    words: str,
    *,
    tasks: list[dict[str, Any]] | None = None,
    get_connection_fn: Callable[[], Any] | None = None,
) -> list[dict[str, Any]]:
    """Open items the person's words name, best tier only.

    Tier 1: the same words as the title. Tier 2: the words appear inside the title, or the title
    inside the words ("milk" -> "buy milk"; "buy the milk today" -> "buy milk" only if no exact).
    Tier 3: every distinctive word of the ask is in the title. More than one hit in the best tier is
    returned whole, so the caller can ask which one instead of picking.
    """
    key = _match_key(words)
    if not key:
        return []
    pool = tasks if tasks is not None else list_tasks(get_connection_fn=get_connection_fn)
    pool = [t for t in pool if t.get("status") == STATUS_OPEN]
    exact = [t for t in pool if _match_key(t["title"]) == key]
    if exact:
        return exact
    padded = f" {key} "
    contained = [t for t in pool if padded in f" {_match_key(t['title'])} "]
    if contained:
        return contained
    containing = [t for t in pool if f" {_match_key(t['title'])} " in padded and _match_key(t["title"])]
    if containing:
        return containing
    wanted = _match_words(words)
    if not wanted:
        return []
    return [t for t in pool if wanted <= _match_words(t["title"])]


__all__ = [
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_OPEN",
    "cancel_task",
    "clean_title",
    "complete_task",
    "create_task",
    "ensure_table",
    "find_open_tasks",
    "get_task",
    "has_any_task",
    "list_tasks",
    "remove_task",
]
