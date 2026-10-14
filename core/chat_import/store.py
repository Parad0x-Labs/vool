"""Where imported chats live in VOOL: history the user can read, apart from every memory store.

A chat brought in from another app is shown in the sidebar and can be read like any chat, but
it is not something VOOL learned. So its messages are NOT written to the conversation log: that
log feeds recall, fact capture, learned heuristics, session summaries, context retrieval and the
adaptation dataset, and it is trimmed by size, which would let a large import push the user's own
VOOL chats out. Imported messages live in two tables of the runtime database instead:

* ``chat_import_chats`` -- one row per imported chat: its new VOOL id, the digest that recognises
  the source chat again (``source_key``), its original times and the import state;
* ``chat_import_messages`` -- its messages, user and assistant only, as inert text.

No memory writer and no recall reader reads these tables. The chat's title lives in the normal
chat metadata (so rename works), its existence in the chat namespace (so delete, archive and
projects work), and ``list_conversation_sessions`` and ``/api/chat/history`` read these tables to
show the original times and the messages. Deleting the chat deletes its rows; a forget sweeps
``chat_import_messages`` like the other chat-scoped tables (``core.memory.carriers``).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

_LOG = logging.getLogger(__name__)

CHATS_TABLE = "chat_import_chats"
MESSAGES_TABLE = "chat_import_messages"

STATE_PREPARED = "PREPARED"
STATE_COMPLETE = "COMPLETE"

_DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS {CHATS_TABLE} (
        session_id TEXT PRIMARY KEY,
        source_key TEXT NOT NULL UNIQUE,
        source_format TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL,
        pending_title TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT '',
        imported_at TEXT NOT NULL DEFAULT ''
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {MESSAGES_TABLE} (
        session_id TEXT NOT NULL,
        event_sequence INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        text TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT '',
        attachment_names_json JSON NOT NULL DEFAULT '[]',
        PRIMARY KEY (session_id, event_sequence)
    )
    """,
)

#: Most imported messages one history read returns (the newest ones).
HISTORY_MESSAGES_MAX = 2000


def iso_time(epoch: float | None) -> str:
    """UTC ISO 8601 text in the same shape the chat stores use, or '' when unknown."""
    if epoch is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def connect() -> sqlite3.Connection:
    from storage.db import get_connection

    connection = get_connection()
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    for statement in _DDL:
        connection.execute(statement)
    connection.commit()


def _tables_present(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)", (CHATS_TABLE, MESSAGES_TABLE)
    ).fetchall()
    return len(rows) == 2


def imported_session_overlays(session_ids: set[str]) -> dict[str, dict[str, Any]]:
    """For each imported chat among ``session_ids``: its original times and message counts.

    A read fault returns {} -- the sidebar then shows the chat with its import time, which is
    a cosmetic loss, never a wrong chat.
    """
    if not session_ids:
        return {}
    from storage.db import get_connection

    try:
        connection = get_connection()
    except Exception:
        return {}
    try:
        if not _tables_present(connection):
            return {}
        rows = connection.execute(
            f"""
            SELECT c.session_id, c.created_at, c.updated_at,
                   COUNT(m.event_sequence), COALESCE(SUM(m.role = 'user'), 0), MAX(m.created_at)
            FROM {CHATS_TABLE} c LEFT JOIN {MESSAGES_TABLE} m ON m.session_id = c.session_id
            GROUP BY c.session_id
            """
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        _LOG.warning("imported chat overlay unavailable: %s", type(exc).__name__)
        return {}
    finally:
        connection.close()
    overlays: dict[str, dict[str, Any]] = {}
    for session_id, created_at, updated_at, messages, user_messages, last_message in rows:
        if session_id not in session_ids:
            continue
        overlays[str(session_id)] = {
            "created_at": str(created_at or ""),
            "updated_at": max(str(updated_at or ""), str(last_message or "")),
            "message_count": int(messages or 0),
            "user_message_count": int(user_messages or 0),
        }
    return overlays


def imported_history_messages(session_id: str, *, limit: int = HISTORY_MESSAGES_MAX) -> list[dict[str, Any]]:
    """The imported messages of one chat, oldest first, as the transcript reader serves them.

    Every row is marked ``imported`` so the page can show it as history and keep it out of
    what it sends to a model.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return []
    from storage.db import get_connection

    connection = get_connection()
    try:
        if not _tables_present(connection):
            return []
        rows = connection.execute(
            f"""
            SELECT role, text, created_at, attachment_names_json FROM (
                SELECT role, text, created_at, attachment_names_json, event_sequence
                FROM {MESSAGES_TABLE} WHERE session_id = ?
                ORDER BY event_sequence DESC LIMIT ?
            ) ORDER BY event_sequence ASC
            """,
            (sid, max(1, int(limit))),
        ).fetchall()
    finally:
        connection.close()
    messages: list[dict[str, Any]] = []
    for role, text, created_at, names_json in rows:
        row: dict[str, Any] = {"role": str(role), "content": str(text), "imported": True}
        if created_at:
            row["ts"] = str(created_at)
        try:
            names = json.loads(names_json or "[]")
        except ValueError:
            names = []
        if isinstance(names, list) and names:
            row["imported_attachment_names"] = [str(name) for name in names if isinstance(name, str)]
        messages.append(row)
    return messages


def delete_imported_chat(session_id: str) -> bool:
    """Remove one imported chat's rows. True when anything was removed."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    from storage.db import get_connection

    connection = get_connection()
    try:
        if not _tables_present(connection):
            return False
        removed = connection.execute(f"DELETE FROM {MESSAGES_TABLE} WHERE session_id = ?", (sid,)).rowcount
        removed += connection.execute(f"DELETE FROM {CHATS_TABLE} WHERE session_id = ?", (sid,)).rowcount
        connection.commit()
        return removed > 0
    finally:
        connection.close()
