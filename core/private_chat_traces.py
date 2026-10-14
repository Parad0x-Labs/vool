"""Private chat: a chat that was private from its first message leaves no record that it existed.

A chat whose first message turned private chat on (core.private_chat) is given no namespace row
(core.context_namespace), so it is in no chat list. But a turn of it still ran through VOOL's run
machinery, which keys its working records by the chat's id: the runtime session, its events and
attempts, the task row, execution facts, the context-generation counter, the provenance and
provider-call manifests, the semantic admission ids, the per-chat receipt ledgers. Each holds ids,
states and digests, never a word of the chat, but together they say "this chat existed, at these
times, with this many turns". Some of them the turn needs while it runs; none is needed after it.

This module is the ONE owner of that cleanup. At the end of every private request of a chat that
has nothing saved (:func:`erase_if_unsaved`), and at startup for a chat still private after a
restart (:func:`sweep_unsaved_private_chats`), it deletes those rows (the runtime database zeroes
deleted content: ``PRAGMA secure_delete``), checkpoints the WAL so no frame keeps them, and removes
the chat's receipt ledger files. The private switch itself (``private_chat_sessions``) stays while
the chat is private, by design.

Money is never erased. When the chat has a spend record (a money liability or a model spend
reservation), every money table keeps its rows, and so do the chat's task rows and provider-call
manifests, which are what such a receipt is traced back through (the MONEY RULE: every spend keeps
its receipt).

A chat that was saved before it went private keeps its records: it exists, and its id is in its
own chat list row. Nothing here touches such a chat.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

from storage.db import get_connection

__all__ = ["chat_is_unsaved", "erase_chat_traces", "erase_if_unsaved", "sweep_unsaved_private_chats"]

_log = logging.getLogger(__name__)

#: (table, column, how the column names the chat). "eq": the column is the chat id; "in": the
#: chat id is part of the column's text (a composed id, or a JSON document).
_RUN_RECORDS: tuple[tuple[str, str, str], ...] = (
    ("runtime_session_events", "session_id", "eq"),
    ("runtime_sessions", "session_id", "eq"),
    ("runtime_attempts", "session_id", "eq"),
    ("runtime_checkpoints", "session_id", "eq"),
    ("notification_items", "session_id", "eq"),
    ("session_hive_watch_state", "session_id", "eq"),
    ("execution_facts", "session_id", "eq"),
    ("turn_context_generations", "session_id", "eq"),
    ("semantic_admissions", "sr_id", "in"),
)
#: Kept when the chat has a money record: a model spend reservation is traced back to its task.
_MONEY_LINKED: tuple[tuple[str, str, str], ...] = (
    ("local_tasks", "session_id", "eq"),
)


def _clean(session_id: Any) -> str:
    return str(session_id or "").strip()


def chat_is_unsaved(session_id: str) -> bool:
    """True when nothing of the chat is saved: no namespace row, no transcript row, no sidebar
    entry. Any fault answers False, so a chat that may exist is never erased."""
    sid = _clean(session_id)
    if not sid:
        return False
    try:
        from core.context_namespace import _load_saved_namespace
        from core.memory.entries import _load_session_meta_raw, recent_conversation_events

        if _load_saved_namespace(sid) is not None:
            return False
        if sid in _load_session_meta_raw():
            return False
        return not recent_conversation_events(sid, limit=1)
    except Exception:
        _log.warning("could not tell whether a private chat has saved records; leaving them", exc_info=True)
        return False


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _has_money(conn: sqlite3.Connection, sid: str, tables: set[str]) -> bool:
    if "effect_budget_money_liabilities" in tables and conn.execute(
        "SELECT 1 FROM effect_budget_money_liabilities WHERE session_id = ? LIMIT 1", (sid,)
    ).fetchone():
        return True
    return {"model_spend_reservations", "local_tasks"} <= tables and conn.execute(
        "SELECT 1 FROM model_spend_reservations WHERE task_id IN "
        "(SELECT task_id FROM local_tasks WHERE session_id = ?) LIMIT 1", (sid,)
    ).fetchone() is not None


def _delete(conn: sqlite3.Connection, table: str, column: str, how: str, sid: str) -> int:
    where = f'"{column}" = ?' if how == "eq" else f'instr(CAST("{column}" AS TEXT), ?) > 0'
    return conn.execute(f'DELETE FROM "{table}" WHERE {where}', (sid,)).rowcount or 0


def _ledger_files(sid: str) -> list[Path]:
    """The chat's own receipt ledger files: each is named by a digest of the chat id."""
    files: list[Path] = []
    with contextlib.suppress(Exception):
        from core.honesty_receipt import _ledger_path

        ledger = _ledger_path(sid)
        files += [ledger, ledger.with_suffix(".lock"), *ledger.parent.glob(ledger.name + ".*.tmp")]
    with contextlib.suppress(Exception):
        from core.evidence_kernel.receipts import _ledger_dir, ledger_path

        root = _ledger_dir()
        if root.is_dir():
            for kind in [path.name for path in root.iterdir() if path.is_dir()]:
                ledger = ledger_path(kind, sid)
                files += [ledger, ledger.with_suffix(".head.json"), ledger.with_suffix(".tmp")]
    return files


def erase_chat_traces(session_id: str) -> dict[str, Any]:
    """Delete every run record that names the chat and remove its receipt ledgers.

    Callers decide that the chat is unsaved and private (:func:`erase_if_unsaved`). Returns what
    was removed and what was kept for money. Raises on a store fault.
    """
    sid = _clean(session_id)
    if not sid:
        raise ValueError("a chat id is required")
    removed: dict[str, int] = {}
    conn = get_connection()
    try:
        tables = _tables(conn)
        money = _has_money(conn, sid, tables)
        wanted = _RUN_RECORDS + (() if money else _MONEY_LINKED)
        for table, column, how in wanted:
            if table not in tables:
                continue
            try:
                count = _delete(conn, table, column, how, sid)
            except sqlite3.IntegrityError:
                # A row something else still points at stays; it is reported, never forced.
                _log.warning("private chat record in %s is still referenced; kept", table)
                continue
            if count:
                removed[table] = count
        conn.commit()
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    files = 0
    for path in _ledger_files(sid):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
            files += 1
    if files:
        removed["receipt_files"] = files
    return {"removed": removed, "kept_for_money": [t for t, _c, _h in _MONEY_LINKED] if money else []}


def erase_if_unsaved(session_id: str) -> dict[str, Any] | None:
    """Erase the chat's run records when nothing of it is saved. Never raises."""
    sid = _clean(session_id)
    if not sid or not chat_is_unsaved(sid):
        return None
    try:
        return erase_chat_traces(sid)
    except Exception:
        _log.warning("a private chat's run records could not be erased", exc_info=True)
        return None


def sweep_unsaved_private_chats() -> int:
    """At startup: a private chat with nothing saved whose last turn did not reach its end (VOOL
    stopped mid-turn) still has that turn's records. Erase them. Returns how many chats."""
    try:
        conn = get_connection()
        try:
            if "private_chat_sessions" not in _tables(conn):
                return 0
            chats = [str(row[0]) for row in conn.execute("SELECT session_id FROM private_chat_sessions")]
        finally:
            conn.close()
    except Exception:
        _log.warning("private chat sweep could not read the switch store", exc_info=True)
        return 0
    return sum(1 for sid in chats if erase_if_unsaved(sid) is not None)
