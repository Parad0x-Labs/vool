"""Reader for an AI agent's local sessions database (the Hermes Agent ``state.db`` format).

Format (verified from the project's source and developer docs, schema versions 1 to 31):

* ``$HERMES_HOME/state.db`` (default ``~/.hermes/state.db`` on macOS and Linux) and one more
  database per named profile at ``~/.hermes/profiles/<name>/state.db``;
* tables ``sessions(id, source, parent_session_id, started_at, ended_at, title, hidden, ...)``,
  ``messages(id, session_id, role, content, timestamp, active, compacted, message_uid, ...)``
  and ``schema_version``;
* context compression splits a session: the new session points to the old one through
  ``parent_session_id``, so one conversation is a chain of sessions, read here as one chat;
* in-place compaction archives old rows (``active=0``) and re-inserts the kept context, so the
  visible history is ``active=1 OR compacted=1`` de-duplicated on ``message_uid``;
* times are epoch seconds (float); roles are OpenAI chat roles. Only ``user`` and ``assistant``
  rows with text in ``content`` become messages: tool rows, system rows, tool calls and reasoning
  never do.

A database with a newer schema version than the last one verified is skipped, never guessed at.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.chat_import.common import Limits, bump, epoch_seconds, make_message
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult
from core.chat_import.sqlite_snapshot import SnapshotError, snapshot_connection, table_columns, table_names

FORMAT = "hermes_state_db"

#: Highest schema version whose layout was verified.
VERIFIED_SCHEMA_VERSION = 31

#: Sessions the agent runs on its own (delegated helpers, scheduled jobs), not the user's chats.
_INTERNAL_SOURCES = frozenset({"subagent", "cron"})


def store_paths(home: Path, env: dict[str, str]) -> list[Path]:
    """The databases that exist, default home first, then each profile. Existence checks only."""
    configured = str(env.get("HERMES_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else home / ".hermes"
    found: list[Path] = []
    candidates = [root / "state.db"]
    profiles = home / ".hermes" / "profiles"
    with contextlib.suppress(OSError):
        candidates.extend(sorted(entry / "state.db" for entry in profiles.iterdir() if entry.is_dir()))
    for candidate in candidates:
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


def _schema_version(connection: sqlite3.Connection) -> int | None:
    columns = table_columns(connection, "schema_version")
    if "version" in columns:
        column = "version"
    elif len(columns) == 1:
        column = next(iter(columns))
    else:
        return None
    try:
        row = connection.execute(f'SELECT MAX("{column}") FROM schema_version').fetchone()
    except sqlite3.DatabaseError:
        return None
    value = row[0] if row else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _profile_name(path: Path) -> str:
    parent = path.parent
    if parent.parent.name == "profiles":
        return parent.name
    return "default"


def read_stores(paths: list[Path], limits: Limits, *, work_dir: str | os.PathLike[str] | None = None) -> ReadResult:
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    for path in paths:
        try:
            with snapshot_connection(path, max_bytes=limits.max_local_store_bytes, work_dir=work_dir) as connection:
                store_chats, store_problems, store_truncated = _read_connection(
                    connection, _profile_name(path), limits, limits.max_conversations - len(chats)
                )
        except SnapshotError as exc:
            problems.append(exc.code)
            continue
        except sqlite3.DatabaseError:
            problems.append("store_unreadable")
            continue
        chats.extend(store_chats)
        problems.extend(store_problems)
        truncated = truncated or store_truncated
    return ReadResult(
        source_format=FORMAT if chats or not problems else "",
        chats=tuple(chats),
        problems=tuple(dict.fromkeys(problems)),
        truncated=truncated,
    )


def _read_connection(
    connection: sqlite3.Connection, profile: str, limits: Limits, room: int
) -> tuple[list[ImportedChat], list[str], bool]:
    tables = table_names(connection)
    session_columns = table_columns(connection, "sessions")
    message_columns = table_columns(connection, "messages")
    if (
        not {"sessions", "messages", "schema_version"} <= tables
        or not {"message_uid", "active"} <= message_columns
        or not {"id", "started_at"} <= session_columns
        or not {"session_id", "role", "content", "timestamp"} <= message_columns
    ):
        return [], ["store_version_unknown"], False
    version = _schema_version(connection)
    if version is None or version > VERIFIED_SCHEMA_VERSION:
        return [], ["store_version_unknown"], False

    wanted = ["id", "source", "parent_session_id", "started_at", "ended_at", "title", "hidden"]
    selected = [column for column in wanted if column in session_columns]
    rows = connection.execute(f"SELECT {', '.join(selected)} FROM sessions").fetchall()
    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = dict(zip(selected, row, strict=False))
        if isinstance(record.get("id"), str) and record["id"]:
            sessions[record["id"]] = record

    problems: list[str] = []
    # Sessions the agent ran by itself are left out before chains are built, so a delegated
    # helper that points at the user's session never joins (or hides) the user's chat.
    internal = [
        session_id
        for session_id, record in sessions.items()
        if str(record.get("source") or "") in _INTERNAL_SOURCES or record.get("hidden") in (1, True)
    ]
    for session_id in internal:
        del sessions[session_id]
    if internal:
        problems.append("chats_internal_skipped")
    chains: dict[str, list[dict[str, Any]]] = {}
    for session_id, record in sessions.items():
        root = _chain_root(session_id, sessions)
        chains.setdefault(root, []).append(record)

    chats: list[ImportedChat] = []
    truncated = False
    ordered_roots = sorted(chains, key=lambda root: epoch_seconds(sessions[root].get("started_at")) or 0.0)
    for root in ordered_roots:
        chain = sorted(chains[root], key=lambda record: epoch_seconds(record.get("started_at")) or 0.0)
        if len(chats) >= room:
            problems.append("too_many_chats")
            truncated = True
            break
        chat, cut = _read_chain(connection, root, chain, profile, message_columns, limits)
        truncated = truncated or cut
        if chat is not None:
            chats.append(chat)
    return chats, problems, truncated


def _chain_root(session_id: str, sessions: dict[str, dict[str, Any]]) -> str:
    seen = {session_id}
    current = session_id
    while True:
        parent = sessions.get(current, {}).get("parent_session_id")
        if not isinstance(parent, str) or parent not in sessions or parent in seen:
            return current
        seen.add(parent)
        current = parent


def _read_chain(
    connection: sqlite3.Connection,
    root: str,
    chain: list[dict[str, Any]],
    profile: str,
    message_columns: set[str],
    limits: Limits,
) -> tuple[ImportedChat | None, bool]:
    visible = "(active = 1 OR compacted = 1)" if "compacted" in message_columns else "active = 1"
    extra = [column for column in ("tool_calls", "id") if column in message_columns]
    select = ["role", "content", "timestamp", "message_uid", *extra]
    order = "timestamp, id" if "id" in message_columns else "timestamp"
    skipped: dict[str, int] = {}
    messages: list[ImportedMessage] = []
    seen_uids: set[str] = set()
    seen_plain: set[tuple] = set()
    truncated = False
    last_time: float | None = None
    for record in chain:
        cursor = connection.execute(
            f"SELECT {', '.join(select)} FROM messages WHERE session_id = ? AND {visible} ORDER BY {order}",
            (record["id"],),
        )
        for row in cursor:
            values = dict(zip(select, row, strict=False))
            uid = values.get("message_uid")
            key = (values.get("role"), values.get("content"), values.get("timestamp"))
            if isinstance(uid, str) and uid:
                if uid in seen_uids:
                    continue
                seen_uids.add(uid)
            elif key in seen_plain:
                continue
            seen_plain.add(key)
            role = values.get("role")
            if role not in ("user", "assistant"):
                bump(skipped, role if isinstance(role, str) and role.isidentifier() else "unknown_role")
                continue
            content = values.get("content")
            if not isinstance(content, str) or not content.strip():
                bump(skipped, "tool" if values.get("tool_calls") else "empty")
                continue
            if len(messages) >= limits.max_messages_per_chat:
                bump(skipped, "limit")
                continue
            created = epoch_seconds(values.get("timestamp"))
            message, cut = make_message(role, content, created, limits)
            truncated = truncated or cut
            messages.append(message)
            if created is not None:
                last_time = created if last_time is None else max(last_time, created)
    titles = [str(record.get("title") or "").strip() for record in chain]
    title = next((value for value in reversed(titles) if value), "")
    ended = [epoch_seconds(record.get("ended_at")) for record in chain]
    updated_candidates = [value for value in [*ended, last_time] if value is not None]
    return (
        ImportedChat(
            source_format=FORMAT,
            source_chat_id=f"{profile}/{root}"[:200],
            title=title,
            created_at=epoch_seconds(chain[0].get("started_at")),
            updated_at=max(updated_candidates) if updated_candidates else None,
            messages=tuple(messages),
            skipped=skipped,
        ),
        truncated,
    )
