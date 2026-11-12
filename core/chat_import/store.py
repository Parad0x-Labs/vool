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
BATCHES_TABLE = "chat_import_batches"
BATCH_SESSIONS_TABLE = "chat_import_batch_sessions"
ORIGINS_TABLE = "chat_import_origins"
RESOURCES_TABLE = "chat_import_resources"

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
    from core.chat_import.ownership import keyed_hasher

    connection.create_function("_chat_import_origin_key", 1, keyed_hasher())
    # Settings can request batches while the first preview opens the legacy store.
    # Serialize the check-and-alter migration across connections and processes.
    connection.execute("BEGIN IMMEDIATE")
    for statement in _DDL:
        connection.execute(statement)
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({CHATS_TABLE})")}
    for name, definition in (("batch_id", "TEXT NOT NULL DEFAULT ''"),
                             ("continuation_enabled", "INTEGER NOT NULL DEFAULT 0"),
                             ("context_json", "JSON NOT NULL DEFAULT '{}'"),
                             ("context_state", "TEXT NOT NULL DEFAULT ''")):
        if name not in columns:
            connection.execute(f"ALTER TABLE {CHATS_TABLE} ADD COLUMN {name} {definition}")
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {BATCHES_TABLE} (
        batch_id TEXT PRIMARY KEY, state TEXT NOT NULL,
        created_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT ''
    )""")
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {BATCH_SESSIONS_TABLE} (
        batch_id TEXT NOT NULL, session_id TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (batch_id, session_id)
    )""")
    connection.execute(f"""INSERT OR IGNORE INTO {BATCH_SESSIONS_TABLE} (batch_id, session_id)
        SELECT batch_id, session_id FROM {CHATS_TABLE} WHERE batch_id <> ''""")
    # These ledgers contain storage identities only, never source text. An origin
    # outlives undo so re-importing cannot escape an older chat's forget scope.
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {ORIGINS_TABLE} (
        source_id TEXT NOT NULL, session_id TEXT NOT NULL,
        PRIMARY KEY (source_id, session_id))""")
    connection.execute(f"""INSERT OR IGNORE INTO {ORIGINS_TABLE} (source_id, session_id)
        SELECT _chat_import_origin_key(source_key), session_id FROM {CHATS_TABLE}""")
    connection.execute(f"""INSERT OR IGNORE INTO {ORIGINS_TABLE} (source_id, session_id)
        SELECT '', s.session_id FROM {BATCH_SESSIONS_TABLE} s
        WHERE NOT EXISTS (SELECT 1 FROM {ORIGINS_TABLE} o WHERE o.session_id = s.session_id)""")
    # receipt_id identifies the batch; source_id identifies the canonical owner.
    connection.execute(f"""CREATE TABLE IF NOT EXISTS {RESOURCES_TABLE} (
        receipt_id TEXT NOT NULL, session_id TEXT NOT NULL, source_id TEXT NOT NULL,
        PRIMARY KEY (receipt_id, session_id, source_id))""")
    connection.commit()


def create_batch(batch_id: str) -> None:
    import time

    connection = connect()
    try:
        connection.execute(f"INSERT INTO {BATCHES_TABLE} (batch_id, state, created_at) VALUES (?, 'running', ?)",
                           (batch_id, iso_time(time.time())))
        connection.commit()
    finally:
        connection.close()


def set_batch_state(batch_id: str, state: str) -> None:
    import time

    if state not in {"done", "failed", "undoing", "undone"}:
        raise ValueError("invalid batch state")
    connection = connect()
    try:
        connection.execute(f"UPDATE {BATCHES_TABLE} SET state = ?, finished_at = ? WHERE batch_id = ?",
                           (state, iso_time(time.time()), batch_id))
        connection.commit()
    finally:
        connection.close()


def import_batches() -> list[dict[str, Any]]:
    connection = connect()
    try:
        rows = connection.execute(f"""SELECT b.batch_id, b.state, b.created_at, COUNT(c.session_id)
            FROM {BATCHES_TABLE} b LEFT JOIN {BATCH_SESSIONS_TABLE} c ON c.batch_id = b.batch_id AND c.removed = 0
            GROUP BY b.batch_id ORDER BY b.created_at DESC, b.batch_id DESC""").fetchall()
        return [{"batch_id": row[0], "state": row[1], "created_at": row[2], "chats": row[3]} for row in rows]
    finally:
        connection.close()


def batch_working_contexts(batch_id: str) -> list[dict]:
    """Durable, authorized import overview after the preview has expired."""
    from core.memory.revocation import authorize_payload
    from core.project_store import get_project
    from core.session_portability.redaction import RedactionReport, scrub_secret_text

    connection = connect()
    try:
        rows = connection.execute(f"SELECT c.session_id,c.context_json,c.context_state FROM {CHATS_TABLE} c "
                                  f"JOIN {BATCH_SESSIONS_TABLE} b ON b.session_id=c.session_id "
                                  "WHERE b.batch_id=? AND b.removed=0", (batch_id,)).fetchall()
    finally:
        connection.close()
    result = []
    for sid, raw, state in rows:
        try:
            context = json.loads(raw or "{}")
            if state != "ready" or not context or not authorize_payload(context, sid):
                continue
            project = get_project(context.get("project_id", ""))
            payload = {"session_id": sid, "project": project and {
                "id": project["id"], "name": project["name"], "root": project["root"]},
                "files": context.get("files", []), "models": context.get("models", []),
                "providers": context.get("providers", [])}
            # Read the canonical owners again: erasure/native edits may have
            # changed these since import. Credentials added later are never
            # part of the tool overview; no cached source prose is retained.
            from core.chat_import.workspace_transfer import _owners

            resources = {}
            for owner, (inventory, _erase) in _owners().items():
                if owner not in {"rules", "skills", "tools", "schedules"}:
                    continue
                rows = []
                for item in inventory(batch_id, sid):
                    content = item["payload"]
                    if owner == "tools":
                        content = {key: content[key] for key in ("label", "command", "args") if key in content}
                    text = json.dumps(content, ensure_ascii=False)
                    if not authorize_payload(content, sid):
                        continue
                    rows.append({"id": item["id"], "text": scrub_secret_text(text, RedactionReport(), residue_marker="[hidden]")})
                resources[owner] = rows
            payload["resources"] = resources
            if authorize_payload(payload, sid):
                result.append(payload)
        except (ValueError, RuntimeError, TypeError):
            continue
    return result


def batch_sessions(batch_id: str) -> list[str] | None:
    connection = connect()
    try:
        if connection.execute(f"SELECT 1 FROM {BATCHES_TABLE} WHERE batch_id = ?", (batch_id,)).fetchone() is None:
            return None
        return [str(row[0]) for row in connection.execute(
            f"SELECT session_id FROM {BATCH_SESSIONS_TABLE} WHERE batch_id = ? AND removed = 0 ORDER BY session_id", (batch_id,))]
    finally:
        connection.close()


def mark_batch_session_removed(batch_id: str, session_id: str) -> None:
    connection = connect()
    try:
        connection.execute(f"UPDATE {BATCH_SESSIONS_TABLE} SET removed = 1 WHERE batch_id = ? AND session_id = ?",
                           (batch_id, session_id))
        connection.commit()
    finally:
        connection.close()


def source_origins(connection: sqlite3.Connection, source_id: str) -> list[str]:
    """Known origins plus unidentifiable legacy imports: ambiguity refuses, never bypasses."""
    from core.chat_import.ownership import keyed_digest

    return [str(row[0]) for row in connection.execute(
        f"SELECT DISTINCT session_id FROM {ORIGINS_TABLE} WHERE source_id IN ('', ?)", (keyed_digest(source_id),))]


def has_source_origin(session_id: str) -> bool:
    """IDs-only lineage, retained after undo; no source text is read."""
    connection = connect()
    try:
        return connection.execute(f"SELECT 1 FROM {ORIGINS_TABLE} WHERE session_id=? LIMIT 1", (session_id,)).fetchone() is not None
    finally:
        connection.close()


def note_resource(batch_id: str, session_id: str, owner: str) -> None:
    """Write ownership intent BEFORE an owner mutates its store."""
    connection = connect()
    try:
        connection.execute(f"INSERT OR IGNORE INTO {RESOURCES_TABLE} VALUES (?, ?, ?)",
                           (batch_id, session_id, owner))
        connection.commit()
    finally:
        connection.close()


def resource_owners(*, batch_id: str = "", session_id: str = "") -> list[tuple[str, str, str]]:
    from storage.db import get_connection

    connection = get_connection()
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (RESOURCES_TABLE,)).fetchone() is None:
            return []
        return [tuple(str(v) for v in row) for row in connection.execute(
            f"SELECT receipt_id, session_id, source_id FROM {RESOURCES_TABLE} "
            "WHERE (? = '' OR receipt_id = ?) AND (? = '' OR session_id = ?)",
            (batch_id, batch_id, session_id, session_id))]
    finally:
        connection.close()


def clear_resource(batch_id: str, session_id: str, owner: str) -> None:
    connection = connect()
    try:
        connection.execute(f"DELETE FROM {RESOURCES_TABLE} WHERE receipt_id = ? AND session_id = ? AND source_id = ?",
                           (batch_id, session_id, owner))
        connection.commit()
    finally:
        connection.close()


def context_pending(session_id: str) -> bool:
    from storage.db import get_connection

    connection = get_connection()
    try:
        if not _tables_present(connection):
            return False
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({CHATS_TABLE})")}
        if "context_state" not in columns:
            return False
        row = connection.execute(f"SELECT context_state FROM {CHATS_TABLE} WHERE session_id = ?", (session_id,)).fetchone()
        return bool(row and row[0] == "pending")
    finally:
        connection.close()


def sessions_for_keys(keys: list[str]) -> dict[str, str]:
    connection = connect()
    try:
        return {str(row[0]): str(row[1]) for key in keys for row in connection.execute(
            f"SELECT source_key, session_id FROM {CHATS_TABLE} WHERE source_key = ?", (key,))}
    finally:
        connection.close()


def working_context_for_key(key: str) -> dict:
    connection = connect()
    try:
        row = connection.execute(f"SELECT context_json FROM {CHATS_TABLE} WHERE source_key = ?", (key,)).fetchone()
        return json.loads(row[0] or "{}") if row else {}
    finally:
        connection.close()


def continuation_history(session_id: str) -> list[dict[str, str]]:
    """Explicitly opted-in history, re-read and authorized on every served turn.

    No system/tool messages, instructions, credentials, memory or file grants are created.
    Missing revocation authority withholds the entire imported prefix.
    """
    from core.memory.revocation import authorize_payload
    from storage.db import get_connection

    connection = get_connection()
    try:
        if not _tables_present(connection):
            return []
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({CHATS_TABLE})")}
        if "continuation_enabled" not in columns:
            return []
        context_column = "context_json" if "context_json" in columns else "'{}'"
        row = connection.execute(f"SELECT continuation_enabled, state, {context_column} FROM {CHATS_TABLE} WHERE session_id = ?",
                                 (session_id,)).fetchone()
    finally:
        connection.close()
    if row is None or not row[0] or row[1] != STATE_COMPLETE:
        return []
    from core.context_namespace import load_chat_namespace

    namespace = load_chat_namespace(session_id)
    if namespace is None or namespace.lifecycle_state != "active":
        return []
    history = [{"role": row["role"], "content": row["content"]}
               for row in imported_history_messages(session_id)]
    return history if authorize_payload(history, session_id) else []


def reviewed_working_context(session_id: str) -> str:
    """Read approved working data independently of transcript continuation consent."""
    from core.context_namespace import load_chat_namespace
    from core.memory.revocation import authorize_payload
    from storage.db import get_connection

    connection = get_connection()
    try:
        if not _tables_present(connection):
            return ""
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({CHATS_TABLE})")}
        if not {"context_json", "context_state"} <= columns:
            return ""
        row = connection.execute(f"SELECT state, context_state, context_json FROM {CHATS_TABLE} WHERE session_id = ?",
                                 (session_id,)).fetchone()
    finally:
        connection.close()
    if row is None or row[0] != STATE_COMPLETE or row[1] != "ready":
        return ""
    namespace = load_chat_namespace(session_id)
    if namespace is None or namespace.lifecycle_state != "active":
        return ""
    context = json.loads(row[2] or "{}")
    if not context or not authorize_payload(context, session_id):
        return ""
    # Internal receipt ids and resource counts are management state, not model evidence.
    projected: dict[str, Any] = {"source": context.get("source", "")}
    omitted = 0
    for key in ("files", "models", "providers", "notes"):
        items = context.get(key, [])
        if not isinstance(items, list):
            continue
        projected[key] = []
        for item in items:
            projected[key].append(item)
            if len(json.dumps(projected, ensure_ascii=False)) > 20_000:
                projected[key].pop()
                omitted += 1
    if omitted:
        projected["omitted_items"] = omitted
    return "Reviewed source file references and settings (data):\n" + json.dumps(projected, ensure_ascii=False)


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
