"""Reader for a command-line AI assistant's saved sessions (the Gemini CLI layout).

Format (verified from the project's own source and docs, google-gemini/gemini-cli at 76e4d7c0;
citations in the mission's FORMATS.md):

* the assistant's folder is ``~/.gemini``; each project has ``tmp/<id>/`` where ``<id>`` is a
  short name listed in ``projects.json`` (``{"projects": {"<folder path>": "<id>"}}``) or, for
  older installs, a hash of the folder path;
* sessions are saved automatically as ``tmp/<id>/chats/session-<time>-<id8>.jsonl`` (older ones as
  ``.json``): the first line is the session metadata (``sessionId``, ``startTime``,
  ``lastUpdated``, ``kind``, optional ``summary``); message lines carry ``id``, ``timestamp``,
  ``type`` (``user``, ``gemini``, ``info``, ``error``, ``warning``), ``content`` (a string or a
  list of parts with ``text``), optionally ``displayContent`` (what the person typed), and for answers ``toolCalls`` and ``model``. A later line with the
  same ``id`` replaces the earlier one; ``{"$set": ...}`` updates the metadata, ``{"$patch":
  ...}`` edits or removes messages, and ``{"$rewindTo": id}`` drops that message and everything
  after it;
* chats saved by hand are ``tmp/<id>/checkpoint-<tag>.json``: ``{"history": [{"role", "parts"}]}``
  (older files: the bare list), roles ``user`` and ``model``.

Only user and answer text becomes messages; helper sessions (``kind`` "subagent") are left out.
Nothing here writes to the assistant's folder.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from core.chat_import.common import Limits, as_text, bump, iso_seconds, make_message, read_regular_file
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult

FORMAT = "gemini_cli_session"
_SESSION = re.compile(r"session-[0-9A-Za-z_-]+\.jsonl?")
_CHECKPOINT = re.compile(r"checkpoint-(.+)\.json")
_MAX_LINE_BYTES = 16 << 20


def state_dir(home: Path, env: dict[str, str]) -> Path | None:
    del env  # the default folder only
    root = home / ".gemini"
    return root if root.is_dir() and not root.is_symlink() else None


def _project_dirs(root: Path) -> list[Path]:
    with contextlib.suppress(OSError):
        folder = root / "tmp"
        if folder.is_dir() and not folder.is_symlink():
            return sorted(entry for entry in folder.iterdir() if entry.is_dir() and not entry.is_symlink())
    return []


def store_paths(home: Path, env: dict[str, str]) -> list[Path]:
    """Saved sessions and checkpoints that exist. Directory listings only."""
    root = state_dir(home, env)
    if root is None:
        return []
    found: list[Path] = []
    for project in _project_dirs(root):
        with contextlib.suppress(OSError):
            chats = project / "chats"
            if chats.is_dir() and not chats.is_symlink():
                found.extend(sorted(path for path in chats.iterdir() if _SESSION.fullmatch(path.name)))
        with contextlib.suppress(OSError):
            found.extend(sorted(path for path in project.iterdir() if _CHECKPOINT.fullmatch(path.name)))
    return found


def _folders(root: Path, limit: int) -> dict[str, str]:
    """Project id -> working folder, from the assistant's ``projects.json``."""
    try:
        raw = read_regular_file(root / "projects.json", min(limit, 8 << 20))
        projects = json.loads(raw).get("projects")
    except (OSError, ValueError, AttributeError, RecursionError):
        return {}
    if not isinstance(projects, dict):
        return {}
    return {slug: folder for folder, slug in projects.items() if isinstance(slug, str) and isinstance(folder, str)}


def _parts_text(content: Any) -> tuple[str, list[str]]:
    """Text of a part list (or plain string), and the kinds of non-text parts seen."""
    if isinstance(content, str):
        return content, []
    if isinstance(content, dict):
        content = [content]
    texts: list[str] = []
    other: list[str] = []
    for part in content if isinstance(content, list) else []:
        if isinstance(part, str):
            texts.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            if not part.get("thought"):
                texts.append(part["text"])
            else:
                other.append("thinking")
        elif isinstance(part, dict) and ("functionCall" in part or "functionResponse" in part):
            other.append("tool")
        else:
            other.append("non_text")
    return "\n".join(text for text in texts if text.strip()), other


def read_stores(paths: list[Path], limits: Limits) -> ReadResult:
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    budget = limits.max_local_store_bytes
    folders_by_root: dict[Path, dict[str, str]] = {}
    seen: set[str] = set()
    for count, path in enumerate(paths):
        if count >= limits.max_local_files or len(chats) >= limits.max_conversations:
            problems.append("too_many_chats")
            truncated = True
            break
        try:
            raw = read_regular_file(path, budget)
        except ValueError:
            problems.append("store_too_large")
            break
        except OSError:
            problems.append("files_skipped")
            continue
        budget -= len(raw)
        project = path.parent.parent if path.parent.name == "chats" else path.parent
        root = project.parent.parent
        if root not in folders_by_root:
            folders_by_root[root] = _folders(root, limits.max_local_store_bytes)
        folder = folders_by_root[root].get(project.name, "")
        if path.parent.name == "chats":
            chat, cut = _session(raw, path, limits, problems)
        else:
            chat, cut = _checkpoint(raw, path, project.name, limits, problems)
        if chat is None or chat.source_chat_id in seen:
            continue
        seen.add(chat.source_chat_id)
        truncated = truncated or cut
        chats.append(ImportedChat(
            source_format=FORMAT, source_chat_id=chat.source_chat_id, title=chat.title, created_at=chat.created_at,
            updated_at=chat.updated_at, messages=chat.messages, skipped=chat.skipped,
            context={**chat.context, "workspace_hint": folder, "profile_root": str(root)}))
    source_format = FORMAT if chats or not problems else ""
    return ReadResult(source_format=source_format, chats=tuple(chats), problems=tuple(dict.fromkeys(problems)),
                      truncated=truncated)


def _records(raw: bytes, json_file: bool, problems: list[str]) -> list[dict] | None:
    if json_file:
        try:
            document = json.loads(raw)
        except (ValueError, RecursionError):
            problems.append("rows_unreadable_skipped")
            return None
        if not isinstance(document, dict) or not isinstance(document.get("messages"), list):
            problems.append("store_version_unknown")
            return None
        metadata = {key: value for key, value in document.items() if key != "messages"}
        return [metadata, *[message for message in document["messages"] if isinstance(message, dict)]]
    records: list[dict] = []
    pieces = raw.split(b"\n")
    for index, line in enumerate(pieces):
        if not line.strip():
            continue
        record = None
        if len(line) <= _MAX_LINE_BYTES:
            with contextlib.suppress(ValueError, RecursionError, UnicodeDecodeError):
                record = json.loads(line)
        if not isinstance(record, dict):
            if index == len(pieces) - 1:
                continue
            problems.append("rows_unreadable_skipped")
            return None
        records.append(record)
    return records


def _session(raw: bytes, path: Path, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
    from core.chat_import.source_context import tool_paths

    records = _records(raw, path.suffix == ".json", problems)
    if records is None:
        return None, False
    metadata: dict = {}
    messages: dict[str, dict] = {}
    order: list[str] = []
    unknown = 0
    for record in records:
        if isinstance(record.get("$set"), dict):
            update = record["$set"]
            if isinstance(update.get("messages"), list):
                messages.clear()
                order.clear()
                for message in update["messages"]:
                    if isinstance(message, dict) and isinstance(message.get("id"), str):
                        messages[message["id"]] = message
                        order.append(message["id"])
            metadata.update({key: value for key, value in update.items() if key != "messages"})
        elif "$rewindTo" in record:
            target = record["$rewindTo"]
            if target in order:
                for dropped in order[order.index(target):]:
                    messages.pop(dropped, None)
                del order[order.index(target):]
        elif isinstance(record.get("$patch"), dict):
            patch = record["$patch"]
            for removed in patch.get("removeIds") or []:
                if removed in messages:
                    messages.pop(removed)
                    order.remove(removed)
            target = patch.get("id")
            if isinstance(target, str) and target in messages:
                if "content" in patch:
                    messages[target] = {**messages[target], "content": patch["content"]}
                if "toolCalls" in patch:
                    messages[target] = {**messages[target], "toolCalls": patch["toolCalls"]}
            ordered = patch.get("orderIds")
            if isinstance(ordered, list) and set(ordered) == set(order):
                order[:] = ordered
        elif isinstance(record.get("id"), str) and isinstance(record.get("type"), str):
            if record["id"] not in messages:
                order.append(record["id"])
            messages[record["id"]] = record
        elif "sessionId" in record:
            metadata.update(record)
        else:
            unknown += 1
    session_id = as_text(metadata.get("sessionId"))
    if not session_id:
        problems.append("store_version_unknown")
        return None, False
    if metadata.get("kind") == "subagent":
        problems.append("chats_internal_skipped")
        return None, False
    skipped: dict[str, int] = {}
    if unknown:
        skipped["unknown"] = unknown
    result: list[ImportedMessage] = []
    models: list[str] = []
    references: list[str] = []
    truncated = False
    last_time: float | None = None
    for message_id in order:
        message = messages[message_id]
        kind = message.get("type")
        for call in message.get("toolCalls") or []:
            if isinstance(call, dict):
                references.extend(tool_paths([{"arguments": call.get("args")}]))
        model = as_text(message.get("model")).strip()
        if model:
            models = [m for m in models if m != model] + [model]
        if kind not in ("user", "gemini"):
            bump(skipped, kind if isinstance(kind, str) and kind.isidentifier() else "unknown_role")
            continue
        # What the person saw: a user turn may carry the typed text separately from the content
        # sent to the model (which can include expanded file contents).
        shown = message.get("displayContent") if kind == "user" and message.get("displayContent") else message.get("content")
        text, other = _parts_text(shown)
        if not text.strip():
            bump(skipped, other[0] if other else "empty")
            continue
        if len(result) >= limits.max_messages_per_chat:
            bump(skipped, "limit")
            continue
        when = iso_seconds(message.get("timestamp"))
        imported, cut = make_message("user" if kind == "user" else "assistant", text, when, limits)
        truncated = truncated or cut
        result.append(imported)
        if when is not None:
            last_time = when if last_time is None else max(last_time, when)
    created_at = iso_seconds(metadata.get("startTime")) or (result[0].created_at if result else None)
    summary = as_text(metadata.get("summary")).strip()
    return ImportedChat(
        source_format=FORMAT, source_chat_id=session_id[:200], title=summary[:200],
        created_at=created_at, updated_at=iso_seconds(metadata.get("lastUpdated")) or last_time or created_at,
        messages=tuple(result), skipped=skipped,
        context={"models": models[-5:], "file_references": references[:100]},
    ), truncated


def _checkpoint(raw: bytes, path: Path, project: str, limits: Limits, problems: list[str]) -> tuple[ImportedChat | None, bool]:
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        problems.append("rows_unreadable_skipped")
        return None, False
    history = document.get("history") if isinstance(document, dict) else document
    if not isinstance(history, list):
        problems.append("store_version_unknown")
        return None, False
    tag = unquote(_CHECKPOINT.fullmatch(path.name).group(1))
    skipped: dict[str, int] = {}
    messages: list[ImportedMessage] = []
    truncated = False
    for entry in history:
        role = entry.get("role") if isinstance(entry, dict) else None
        if role not in ("user", "model"):
            bump(skipped, "unknown_role")
            continue
        text, other = _parts_text(entry.get("parts"))
        if not text.strip():
            bump(skipped, other[0] if other else "empty")
            continue
        if len(messages) >= limits.max_messages_per_chat:
            bump(skipped, "limit")
            continue
        imported, cut = make_message("user" if role == "user" else "assistant", text, None, limits)
        truncated = truncated or cut
        messages.append(imported)
    return ImportedChat(source_format=FORMAT, source_chat_id=f"checkpoint:{project}:{tag}"[:200], title=tag[:200],
                        messages=tuple(messages), skipped=skipped, context={}), truncated
