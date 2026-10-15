"""Reader for a personal AI assistant's local transcripts (the OpenClaw format).

Format (verified from the project's source and reference docs, package version 2026.10.3):

* the state folder is ``~/.openclaw`` on macOS and Linux (``~/.clawdbot`` on installs from
  before the rename). VOOL reads no OpenClaw environment setting to find another folder: it
  looks only in the default places, in the home folder it was given;
* since release 2026.8.1 each agent keeps its transcripts in
  ``agents/<agentId>/agent/openclaw-agent.sqlite``, table
  ``transcript_events(session_id, seq, event_json, event_zstd, ...)``; exactly one of
  ``event_json`` and ``event_zstd`` is set, and the zstd rows can only be read when a zstd
  decoder is installed -- otherwise the sessions holding them are left out and reported (a
  session with any unreadable row is never shown partly, since its branch could be wrong);
* before that release, ``agents/<agentId>/sessions/<sessionId>.jsonl`` holds the same events one
  per line (``*.jsonl.deleted.<time>`` files are deleted transcripts and are not read);
* events form an append-only tree: a ``session`` header (version 3 or 4), then entries with
  ``id``/``parentId``. The chat the user sees is the path from the latest entry up to the root;
  a ``reset`` entry hides what came before it except from ``firstKeptEntryId``;
* ``message`` entries carry ``message.role`` (``user``, ``assistant``, ``toolResult``, ``custom``)
  and ``content`` (a string or blocks: ``text``, ``thinking``, ``toolCall``, ``image``). Only text
  blocks of user and assistant messages become text. Injected runtime-context messages are not
  the user's words and are left out.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.chat_import.common import Limits, as_text, bump, epoch_millis, iso_seconds, make_message
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult
from core.chat_import.sqlite_snapshot import SnapshotError, snapshot_connection, table_columns

FORMAT_DB = "openclaw_agent_db"
FORMAT_JSONL = "openclaw_jsonl"

#: Header versions whose entry layout the current reader code reads natively.
VERIFIED_HEADER_VERSIONS = frozenset({3, 4})

_DB_NAME = "openclaw-agent.sqlite"
_CONTEXT_MARKERS = ("<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>>", "OpenClaw runtime context:")
#: Largest single transcript line or decompressed event accepted.
_MAX_EVENT_BYTES = 8 << 20


def state_dirs(home: Path, env: dict[str, str]) -> list[Path]:
    del env  # the default folders only; no third-party environment setting moves them
    roots = [home / ".openclaw", home / ".clawdbot"]
    return [root for root in roots if root.is_dir()]


def _agent_dirs(root: Path) -> list[Path]:
    try:
        return sorted(entry for entry in (root / "agents").iterdir() if entry.is_dir())
    except OSError:
        return []


def store_paths(home: Path, env: dict[str, str]) -> tuple[list[Path], list[Path]]:
    """(agent databases, legacy transcript files) that exist. Listing and existence checks only."""
    databases: list[Path] = []
    transcripts: list[Path] = []
    for root in state_dirs(home, env):
        for agent in _agent_dirs(root):
            database = agent / "agent" / _DB_NAME
            if database.is_file():
                databases.append(database)
            with contextlib.suppress(OSError):
                transcripts.extend(
                    sorted(entry for entry in (agent / "sessions").iterdir() if entry.suffix == ".jsonl" and entry.is_file())
                )
    return databases, transcripts


def _zstd_decoder():
    """A function that decompresses one zstd frame, or None when no decoder is installed."""
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        zstandard = None
    if zstandard is not None:
        decompressor = zstandard.ZstdDecompressor()
        return lambda blob: decompressor.decompress(blob, max_output_size=_MAX_EVENT_BYTES)
    try:
        from compression import zstd  # type: ignore[import-not-found]
    except ImportError:
        return None
    return zstd.decompress


def read_stores(
    databases: list[Path],
    transcripts: list[Path],
    limits: Limits,
    *,
    work_dir: str | os.PathLike[str] | None = None,
    decoder: Any = "auto",
) -> ReadResult:
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    seen: set[str] = set()
    decode = _zstd_decoder() if decoder == "auto" else decoder
    for database in databases:
        agent = database.parent.parent.name
        try:
            with snapshot_connection(database, max_bytes=limits.max_local_store_bytes, work_dir=work_dir) as connection:
                columns = table_columns(connection, "transcript_events")
                if not {"session_id", "seq", "event_json", "event_zstd"} <= columns:
                    problems.append("store_version_unknown")
                    continue
                for session_id, events, skipped_rows in _sessions_from_db(connection, decode, problems):
                    if len(chats) >= limits.max_conversations:
                        problems.append("too_many_chats")
                        truncated = True
                        break
                    key = f"{agent}/{session_id}"
                    seen.add(key)
                    if skipped_rows:
                        # A missing row can be the one that links the shown branch together;
                        # the walk would then show a thrown-away answer as the current one. The
                        # session is left out (and reported) rather than shown wrong.
                        continue
                    chat, cut = _chat_from_events(events, key, FORMAT_DB, limits, problems)
                    truncated = truncated or cut
                    if chat is not None:
                        chats.append(chat)
        except SnapshotError as exc:
            problems.append(exc.code)
        except sqlite3.DatabaseError:
            problems.append("store_unreadable")
    budget = limits.max_local_store_bytes
    for count, transcript in enumerate(transcripts):
        if count >= limits.max_local_files or len(chats) >= limits.max_conversations:
            problems.append("too_many_chats")
            truncated = True
            break
        key = f"{transcript.parent.parent.name}/{transcript.stem}"
        if key in seen:
            continue
        try:
            size = transcript.stat().st_size
        except OSError:
            problems.append("files_skipped")
            continue
        if size > budget:
            problems.append("store_too_large")
            break
        budget -= size
        events = _events_from_jsonl(transcript, problems)
        if events is None:
            continue
        chat, cut = _chat_from_events(events, key, FORMAT_JSONL, limits, problems)
        seen.add(key)
        truncated = truncated or cut
        if chat is not None:
            chats.append(chat)
    formats = {chat.source_format for chat in chats}
    source_format = FORMAT_DB if FORMAT_DB in formats else (FORMAT_JSONL if chats or not problems else "")
    return ReadResult(source_format=source_format, chats=tuple(chats), problems=tuple(dict.fromkeys(problems)), truncated=truncated)


def _sessions_from_db(connection: sqlite3.Connection, decode: Any, problems: list[str]) -> Iterable[tuple[str, list[dict], int]]:
    """(session id, events in sequence order, rows left out) for every session in the table."""
    cursor = connection.execute(
        "SELECT session_id, event_json, event_zstd FROM transcript_events ORDER BY session_id, seq"
    )
    current: str | None = None
    events: list[dict] = []
    skipped_rows = 0
    for session_id, event_json, event_zstd in cursor:
        if session_id != current:
            if current is not None:
                yield current, events, skipped_rows
            current, events, skipped_rows = str(session_id), [], 0
        raw: Any = event_json
        if raw is None and event_zstd is not None:
            if decode is None:
                problems.append("rows_compressed_skipped")
                skipped_rows += 1
                continue
            try:
                raw = decode(bytes(event_zstd))
            except Exception:
                problems.append("rows_unreadable_skipped")
                skipped_rows += 1
                continue
        event = _parse_event(raw)
        if event is None:
            problems.append("rows_unreadable_skipped")
            skipped_rows += 1
            continue
        events.append(event)
    if current is not None:
        yield current, events, skipped_rows


def _parse_event(raw: Any) -> dict | None:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        if len(raw) > _MAX_EVENT_BYTES:
            return None
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or len(raw) > _MAX_EVENT_BYTES:
        return None
    try:
        event = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return event if isinstance(event, dict) else None


def _events_from_jsonl(path: Path, problems: list[str]) -> list[dict] | None:
    events: list[dict] = []
    bad = 0
    try:
        with path.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = _parse_event(line)
                if event is None:
                    # Only a last line the app was still writing (no line end yet) is let go;
                    # any other unreadable line could be a link of the shown branch.
                    if line.endswith(b"\n"):
                        bad += 1
                    continue
                events.append(event)
    except OSError:
        problems.append("files_skipped")
        return None
    if bad:
        problems.append("rows_unreadable_skipped")
        return None
    if not events or events[0].get("type") != "session":
        problems.append("files_skipped")
        return None
    return events


def _chat_from_events(
    events: list[dict], key: str, source_format: str, limits: Limits, problems: list[str]
) -> tuple[ImportedChat | None, bool]:
    header = next((event for event in events if event.get("type") == "session"), None)
    if header is not None and header.get("version") not in VERIFIED_HEADER_VERSIONS:
        problems.append("store_version_unknown")
        return None, False

    by_id: dict[str, dict] = {}
    last_id: str | None = None
    title = ""
    for event in events:
        entry_id = event.get("id")
        if event.get("type") != "session" and isinstance(entry_id, str) and entry_id:
            by_id[entry_id] = event
            last_id = entry_id
        if event.get("type") == "session_info" and isinstance(event.get("name"), str) and event["name"].strip():
            title = event["name"]

    path: list[dict] = []
    seen: set[str] = set()
    node: Any = last_id
    while isinstance(node, str) and node in by_id and node not in seen and len(path) <= 4 * limits.max_messages_per_chat + 1024:
        seen.add(node)
        path.append(by_id[node])
        node = by_id[node].get("parentId")
    path.reverse()

    visible: list[dict] = []
    for entry in path:
        if entry.get("type") == "reset":
            keep = entry.get("firstKeptEntryId")
            index = next((i for i, kept in enumerate(visible) if kept.get("id") == keep), None)
            visible = visible[index:] if isinstance(keep, str) and index is not None else []
            continue
        visible.append(entry)

    skipped: dict[str, int] = {}
    hidden = sum(1 for event in by_id.values() if event.get("type") == "message") - sum(
        1 for entry in visible if entry.get("type") == "message"
    )
    if hidden > 0:
        skipped["hidden_branch"] = hidden
    messages: list[ImportedMessage] = []
    truncated = False
    last_time: float | None = None
    for entry in visible:
        kind = entry.get("type")
        if kind == "custom_message":
            bump(skipped, "context")
            continue
        if kind != "message":
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            bump(skipped, "malformed")
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            bump(skipped, "tool" if role == "toolResult" else (role if isinstance(role, str) and role.isidentifier() else "unknown_role"))
            continue
        text, reason = _message_text(message)
        if text is None:
            bump(skipped, reason)
            continue
        if role == "user" and (
            "runtimeContext" in message or "runtimeContextCarrier" in message or any(marker in text for marker in _CONTEXT_MARKERS)
        ):
            bump(skipped, "context")
            continue
        if len(messages) >= limits.max_messages_per_chat:
            bump(skipped, "limit")
            continue
        created = epoch_millis(message.get("timestamp")) or iso_seconds(entry.get("timestamp"))
        imported, cut = make_message(role, text, created, limits)
        truncated = truncated or cut
        messages.append(imported)
        if created is not None:
            last_time = created if last_time is None else max(last_time, created)

    created_at = iso_seconds(header.get("timestamp")) if header else None
    if created_at is None and messages:
        created_at = messages[0].created_at
    return (
        ImportedChat(
            source_format=source_format,
            source_chat_id=key[:200],
            title=as_text(title),
            created_at=created_at,
            updated_at=last_time or created_at,
            messages=tuple(messages),
            skipped=skipped,
        ),
        truncated,
    )


def _message_text(message: dict) -> tuple[str | None, str]:
    content = message.get("content")
    if isinstance(content, str):
        return (content, "") if content.strip() else (None, "empty")
    if not isinstance(content, list):
        return None, "empty"
    texts: list[str] = []
    kinds: set[str] = set()
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text" and isinstance(block.get("text"), str):
            if block["text"].strip():
                texts.append(block["text"])
        elif kind == "thinking":
            kinds.add("thinking")
        elif kind == "toolCall":
            kinds.add("tool")
        else:
            kinds.add("non_text")
    if texts:
        return "\n\n".join(texts), ""
    for reason in ("tool", "thinking", "non_text"):
        if reason in kinds:
            return None, reason
    return None, "empty"
