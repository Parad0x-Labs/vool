"""Reader for an AI code editor's chat store (the Cursor ``state.vscdb`` format).

Format (community-verified by a maintained reader, Cursor builds up to 2026-09):

* the global store is ``~/Library/Application Support/Cursor/User/globalStorage/state.vscdb`` on
  macOS and ``~/.config/Cursor/User/globalStorage/state.vscdb`` on Linux (the Electron
  configuration folder, ``$XDG_CONFIG_HOME`` when it is set);
* table ``cursorDiskKV(key, value)`` holds one ``composerData:<composerId>`` JSON per chat
  (``name``, ``createdAt``/``lastUpdatedAt`` in epoch milliseconds, and
  ``fullConversationHeadersOnly`` -- the active branch, in order) and one
  ``bubbleId:<composerId>:<bubbleId>`` JSON per message (``type`` 1 is the user, 2 the assistant,
  ``text`` the words);
* when the user edits or rewinds, the old bubbles stay behind without a header. Their place cannot
  be recovered, so only bubbles the header list references are read. A chat with no header list
  has no recorded order and is left out rather than guessed at;
* the store can grow to many gigabytes; values are fetched one key at a time, never all at once.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.chat_import.common import Limits, as_text, bump, epoch_millis, iso_seconds, make_message
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult
from core.chat_import.sqlite_snapshot import SnapshotError, snapshot_connection, table_columns

FORMAT = "cursor_state_vscdb"

_COMPOSER_PREFIX = "composerData:"
#: Largest single value parsed (a chat header or one message).
_MAX_VALUE_BYTES = 32 << 20


def store_paths(home: Path, env: dict[str, str], platform: str) -> list[Path]:
    if platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        configured = str(env.get("XDG_CONFIG_HOME") or "").strip()
        base = Path(configured).expanduser() if configured else home / ".config"
    candidate = base / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return [candidate] if candidate.is_file() else []


def _load(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        if len(value) > _MAX_VALUE_BYTES:
            return None
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str) or len(value) > _MAX_VALUE_BYTES:
        return None
    try:
        return json.loads(value)
    except (ValueError, RecursionError):
        return None


def read_stores(paths: list[Path], limits: Limits, *, work_dir: str | os.PathLike[str] | None = None) -> ReadResult:
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    for path in paths:
        try:
            with snapshot_connection(path, max_bytes=limits.max_local_store_bytes, work_dir=work_dir) as connection:
                if not {"key", "value"} <= table_columns(connection, "cursorDiskKV"):
                    problems.append("store_version_unknown")
                    continue
                keys = [
                    row[0]
                    for row in connection.execute(
                        "SELECT key FROM cursorDiskKV WHERE key >= ? AND key < ? ORDER BY key",
                        (_COMPOSER_PREFIX, _COMPOSER_PREFIX[:-1] + ";"),
                    )
                    if isinstance(row[0], str)
                ]
                for key in keys:
                    if len(chats) >= limits.max_conversations:
                        problems.append("too_many_chats")
                        truncated = True
                        break
                    chat, cut = _read_composer(connection, key, limits, problems)
                    truncated = truncated or cut
                    if chat is not None:
                        chats.append(chat)
        except SnapshotError as exc:
            problems.append(exc.code)
        except sqlite3.DatabaseError:
            problems.append("store_unreadable")
    return ReadResult(
        source_format=FORMAT if chats or not problems else "",
        chats=tuple(chats),
        problems=tuple(dict.fromkeys(problems)),
        truncated=truncated,
    )


def _value(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,)).fetchone()
    return _load(row[0]) if row else None


def _has_bubbles(connection: sqlite3.Connection, composer_id: str) -> bool:
    prefix = f"bubbleId:{composer_id}:"
    row = connection.execute(
        "SELECT 1 FROM cursorDiskKV WHERE key >= ? AND key < ? LIMIT 1", (prefix, prefix[:-1] + ";")
    ).fetchone()
    return row is not None


def _read_composer(
    connection: sqlite3.Connection, key: str, limits: Limits, problems: list[str]
) -> tuple[ImportedChat | None, bool]:
    composer = _value(connection, key)
    composer_id = key[len(_COMPOSER_PREFIX):]
    if not isinstance(composer, dict) or not composer_id:
        problems.append("chat_skipped_malformed")
        return None, False
    headers = composer.get("fullConversationHeadersOnly")
    if not isinstance(headers, list) or not headers:
        # A draft that never got a message is not a chat; a chat whose order is not recorded is
        # left out and reported.
        if _has_bubbles(connection, composer_id):
            problems.append("chats_without_order_skipped")
        return None, False

    skipped: dict[str, int] = {}
    messages: list[ImportedMessage] = []
    truncated = False
    for header in headers:
        bubble_id = header.get("bubbleId") if isinstance(header, dict) else None
        if not isinstance(bubble_id, str) or not bubble_id:
            bump(skipped, "malformed")
            continue
        bubble = _value(connection, f"bubbleId:{composer_id}:{bubble_id}")
        if not isinstance(bubble, dict):
            bump(skipped, "missing")
            continue
        kind = bubble.get("type", header.get("type"))
        if kind == 1:
            role = "user"
        elif kind == 2:
            role = "assistant"
        else:
            bump(skipped, "unknown_role")
            continue
        text = bubble.get("text")
        if not isinstance(text, str) or not text.strip():
            bump(skipped, "tool" if bubble.get("toolFormerData") else "empty")
            continue
        if len(messages) >= limits.max_messages_per_chat:
            bump(skipped, "limit")
            continue
        message, cut = make_message(role, text, _bubble_time(bubble), limits)
        truncated = truncated or cut
        messages.append(message)

    return (
        ImportedChat(
            source_format=FORMAT,
            source_chat_id=composer_id[:200],
            title=as_text(composer.get("name")),
            created_at=epoch_millis(composer.get("createdAt")),
            updated_at=epoch_millis(composer.get("lastUpdatedAt")),
            messages=tuple(messages),
            skipped=skipped,
        ),
        truncated,
    )


def _bubble_time(bubble: dict) -> float | None:
    """The message time in the maintained reader's priority order, or None when unrecorded."""
    created = iso_seconds(bubble.get("createdAt"))
    if created is not None:
        return created
    timing = bubble.get("timingInfo") if isinstance(bubble.get("timingInfo"), dict) else {}
    for field in ("clientRpcSendTime", "clientSettleTime", "clientEndTime"):
        value = epoch_millis(timing.get(field))
        if value is not None:
            return value
    return None
