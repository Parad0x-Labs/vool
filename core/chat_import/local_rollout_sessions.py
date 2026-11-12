"""Reader for a terminal coding agent's local session files (the Codex CLI rollout format).

Format (verified from the project's own source, openai/codex at 63ba5581, and its docs; the
exact citations are kept in the mission's FORMATS.md and in docs/CHAT_IMPORT.md):

* the agent's folder is ``$CODEX_HOME`` when set, else ``~/.codex``;
* each session is one append-only JSON-lines file
  ``sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`` (archived ones sit flat in
  ``archived_sessions/``); files older than a week may be zstd-compressed (``.jsonl.zst``);
* every line is ``{"timestamp", "ordinal"?, "type", "payload"}``. ``session_meta`` (first line)
  carries ``id``, ``cwd``, ``cli_version``, ``source``, ``thread_source`` and ``history_mode``;
  ``turn_context`` carries the ``model`` of each turn; ``compacted`` carries the summary text
  that replaced older history (``message``); ``response_item`` carries model items
  (``message`` with ``role`` and ``content`` blocks ``input_text``/``output_text``,
  ``function_call`` with ``name``/``arguments``); ``event_msg`` carries the screen events
  (``user_message``/``agent_message`` with ``message``, ``thread_rolled_back`` with
  ``num_turns``) -- the screen events are only kept for "legacy" history mode sessions;
* text the agent injects into user messages (instruction files, environment blocks, skills,
  summaries) is recognised the way the agent itself recognises it: by its marker tags, by
  ``content_item_kinds`` metadata, by the developer role, and by the summary prefix.

Only user and assistant text becomes messages. The latest summary, the latest plan, the last
model and the working folder are kept as separately reviewed context, never as transcript.
Nothing here writes to the agent's folder; compressed files are read only when a zstd decoder
is installed, otherwise they are left out and reported.
"""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from core.chat_import.common import Limits, as_text, bump, iso_seconds, make_message, read_regular_file
from core.chat_import.model import ImportedChat, ImportedMessage, ReadResult

FORMAT = "codex_rollout_jsonl"

#: Envelope types the reader knows; any other type is counted and left out.
_KNOWN_TYPES = frozenset({
    "session_meta", "response_item", "compacted", "turn_context", "event_msg", "token_usage_record",
    "world_state", "retained_context", "security_risk_score", "inter_agent_communication",
    "inter_agent_communication_metadata", "realtime_item",
})
#: Sessions the agent ran by itself (helpers, reviews, memory upkeep), not the user's chats.
_INTERNAL_THREAD_SOURCES = frozenset({"subagent", "guardian_review", "memory_consolidation"})
_INTERNAL_SOURCES = frozenset({"subagent", "internal"})
#: Marker pairs for text the agent injects into a user message (open, close), compared without case.
_CONTEXT_MARKERS = (
    ("# agents.md instructions", "</instructions>"),
    ("<environment_context>", "</environment_context>"),
    ("<skill>", "</skill>"),
    ("<skills_instructions>", "</skills_instructions>"),
    ("<user_instructions>", "</user_instructions>"),
    ("<user_shell_command>", "</user_shell_command>"),
    ("<turn_aborted>", "</turn_aborted>"),
    ("<subagent_notification>", "</subagent_notification>"),
    ("<goal_context>", "</goal_context>"),
    ("<recommended_plugins>", "</recommended_plugins>"),
)
_CONTEXT_PREFIXES = ("<external_", "<codex_internal_context")
#: Start of the summary the agent writes after compacting history (``summary_prefix.md``).
_SUMMARY_PREFIX = "another language model started to solve this problem"
_ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9A-Za-z_-]+\.jsonl(\.zst)?")
#: Largest single line accepted.
_MAX_LINE_BYTES = 16 << 20


def state_dir(home: Path, env: dict[str, str]) -> Path | None:
    configured = str(env.get("CODEX_HOME") or "").strip()
    root = Path(configured).expanduser() if configured else home / ".codex"
    return root if root.is_dir() and not root.is_symlink() else None


def store_paths(home: Path, env: dict[str, str]) -> list[Path]:
    """Session files that exist, newest folders first. Directory listings only."""
    root = state_dir(home, env)
    if root is None:
        return []
    found: list[Path] = []

    def listing(folder: Path) -> list[Path]:
        with contextlib.suppress(OSError):
            if folder.is_dir() and not folder.is_symlink():
                return sorted(folder.iterdir(), reverse=True)
        return []

    for year in listing(root / "sessions"):
        if not re.fullmatch(r"\d{4}", year.name):
            continue
        for month in listing(year):
            if not re.fullmatch(r"\d{2}", month.name):
                continue
            for day in listing(month):
                if not re.fullmatch(r"\d{2}", day.name):
                    continue
                found.extend(path for path in listing(day) if _ROLLOUT_NAME.fullmatch(path.name))
    found.extend(path for path in listing(root / "archived_sessions") if _ROLLOUT_NAME.fullmatch(path.name))
    return found


def _zstd_decoder(limit: int):
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        zstandard = None
    if zstandard is not None:
        decompressor = zstandard.ZstdDecompressor()
        return lambda blob: decompressor.decompress(blob, max_output_size=limit)
    try:
        from compression import zstd  # type: ignore[import-not-found]
    except ImportError:
        return None
    return zstd.decompress


def _titles(root: Path, limit: int) -> dict[str, str]:
    """Thread names the user gave, from ``session_index.jsonl`` (last entry per id wins)."""
    titles: dict[str, str] = {}
    try:
        raw = read_regular_file(root / "session_index.jsonl", min(limit, 32 << 20))
    except (OSError, ValueError):
        return titles
    for line in raw.splitlines():
        with contextlib.suppress(ValueError, RecursionError):
            entry = json.loads(line)
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("thread_name"), str):
                titles[entry["id"]] = entry["thread_name"]
    return titles


def read_stores(paths: list[Path], limits: Limits, *, decoder: Any = "auto") -> ReadResult:
    chats: list[ImportedChat] = []
    problems: list[str] = []
    truncated = False
    seen: set[str] = set()
    budget = limits.max_local_store_bytes
    decode = _zstd_decoder(limits.max_local_store_bytes) if decoder == "auto" else decoder
    titles_by_root: dict[Path, dict[str, str]] = {}
    for count, path in enumerate(paths):
        if count >= limits.max_local_files or len(chats) >= limits.max_conversations:
            problems.append("too_many_chats")
            truncated = True
            break
        compressed = path.name.endswith(".zst")
        if compressed and decode is None:
            problems.append("rows_compressed_skipped")
            continue
        try:
            raw = read_regular_file(path, budget)
        except ValueError:
            problems.append("store_too_large")
            break
        except OSError:
            problems.append("files_skipped")
            continue
        budget -= len(raw)
        if compressed:
            try:
                raw = decode(raw)
            except Exception:
                problems.append("rows_unreadable_skipped")
                continue
            if len(raw) > budget:
                problems.append("store_too_large")
                break
            budget -= len(raw)
        lines = _lines(raw, problems)
        if lines is None:
            continue
        root = path.parents[1] if path.parent.name == "archived_sessions" else path.parents[4]
        if root not in titles_by_root:
            titles_by_root[root] = _titles(root, limits.max_local_store_bytes)
        chat, cut = _chat_from_lines(lines, limits, problems, titles_by_root[root])
        if chat is None:
            continue
        if chat.source_chat_id in seen:
            # The same thread live and archived: the live (listed first) copy wins.
            continue
        seen.add(chat.source_chat_id)
        truncated = truncated or cut
        chats.append(ImportedChat(
            source_format=chat.source_format, source_chat_id=chat.source_chat_id, title=chat.title,
            created_at=chat.created_at, updated_at=chat.updated_at, messages=chat.messages,
            skipped=chat.skipped, context={**chat.context, "profile_root": str(root)}))
    source_format = FORMAT if chats or not problems else ""
    return ReadResult(source_format=source_format, chats=tuple(chats), problems=tuple(dict.fromkeys(problems)),
                      truncated=truncated)


def _lines(raw: bytes, problems: list[str]) -> list[dict] | None:
    """Every record of one file. A damaged complete line leaves the whole file out, because the
    lost line could be a message; only a last line still being written may be let go."""
    records: list[dict] = []
    pieces = raw.split(b"\n")
    for index, line in enumerate(pieces):
        if not line.strip():
            continue
        last_unfinished = index == len(pieces) - 1
        record = None
        if len(line) <= _MAX_LINE_BYTES:
            with contextlib.suppress(ValueError, RecursionError, UnicodeDecodeError):
                record = json.loads(line)
        if not isinstance(record, dict):
            if last_unfinished:
                continue
            problems.append("rows_unreadable_skipped")
            return None
        records.append(record)
    if not records or records[0].get("type") != "session_meta" or not isinstance(records[0].get("payload"), dict):
        problems.append("files_skipped")
        return None
    return records


def is_injected_context(text: str) -> bool:
    folded = text.strip().lower()
    if any(folded.startswith(start) and folded.endswith(end) for start, end in _CONTEXT_MARKERS):
        return True
    return folded.startswith(_CONTEXT_PREFIXES) or folded.startswith(_SUMMARY_PREFIX)


def _item_text(item: dict, role: str) -> tuple[str | None, str]:
    content = item.get("content")
    if not isinstance(content, list):
        return None, "empty"
    metadata = item.get("internal_chat_message_metadata_passthrough")
    kinds = metadata.get("content_item_kinds") if isinstance(metadata, dict) else None
    texts: list[str] = []
    reasons: set[str] = set()
    for index, block in enumerate(content):
        kind = block.get("type") if isinstance(block, dict) else None
        text = block.get("text") if isinstance(block, dict) else None
        if kind not in ("input_text", "output_text") or not isinstance(text, str):
            reasons.add("non_text")
            continue
        label = kinds[index] if isinstance(kinds, list) and index < len(kinds) and isinstance(kinds[index], str) else None
        if role == "user" and ((label is not None and not label.startswith("user.")) or is_injected_context(text)):
            reasons.add("context")
            continue
        if text.strip():
            texts.append(text)
    if texts:
        return "\n\n".join(texts), ""
    for reason in ("context", "non_text"):
        if reason in reasons:
            return None, reason
    return None, "empty"


def _plan_text(arguments: Any) -> str:
    if not isinstance(arguments, str) or len(arguments) > 200_000:
        return ""
    try:
        parsed = json.loads(arguments)
    except (ValueError, RecursionError):
        return ""
    steps = parsed.get("plan") if isinstance(parsed, dict) else None
    if not isinstance(steps, list):
        return ""
    lines = [f"[{as_text(step.get('status')) or 'pending'}] {as_text(step.get('step')).strip()}"
             for step in steps[:100] if isinstance(step, dict) and as_text(step.get("step")).strip()]
    explanation = as_text(parsed.get("explanation")).strip()
    return "\n".join(([explanation] if explanation else []) + lines)


def _chat_from_lines(records: list[dict], limits: Limits, problems: list[str],
                     titles: dict[str, str]) -> tuple[ImportedChat | None, bool]:
    from core.chat_import.source_context import tool_paths

    meta = records[0]["payload"]
    thread_id = as_text(meta.get("id"))
    if not thread_id or not as_text(meta.get("cli_version")):
        problems.append("store_version_unknown")
        return None, False
    if as_text(meta.get("thread_source")) in _INTERNAL_THREAD_SOURCES or (
            isinstance(meta.get("source"), str) and meta["source"] in _INTERNAL_SOURCES) or isinstance(meta.get("source"), dict):
        problems.append("chats_internal_skipped")
        return None, False
    legacy = as_text(meta.get("history_mode") or "legacy") == "legacy"
    has_events = legacy and any(record.get("type") == "event_msg" and isinstance(record.get("payload"), dict)
                                and record["payload"].get("type") in ("user_message", "agent_message")
                                for record in records)
    skipped: dict[str, int] = {}
    entries: list[tuple[str, str, float | None]] = []  # role, text, time
    models: list[str] = []
    summary = ""
    plan = ""
    references: list[str] = []
    for record in records[1:]:
        kind = record.get("type")
        payload = record.get("payload")
        when = iso_seconds(record.get("timestamp"))
        if kind not in _KNOWN_TYPES or not isinstance(payload, dict):
            bump(skipped, "unknown")
            continue
        if kind == "turn_context":
            model = as_text(payload.get("model")).strip()
            if model:
                models = [m for m in models if m != model] + [model]
        elif kind == "compacted":
            summary = as_text(payload.get("message")).strip() or summary
        elif kind == "event_msg":
            event = as_text(payload.get("type"))
            if has_events and event in ("user_message", "agent_message"):
                text = as_text(payload.get("message"))
                if event == "user_message" and is_injected_context(text):
                    bump(skipped, "context")
                elif text.strip():
                    entries.append(("user" if event == "user_message" else "assistant", text, when))
                else:
                    bump(skipped, "empty")
            elif event == "thread_rolled_back":
                turns = payload.get("num_turns")
                if isinstance(turns, int) and not isinstance(turns, bool) and turns > 0:
                    # Drop the last ``turns`` user turns and everything after them, as the agent does.
                    starts = [i for i, entry in enumerate(entries) if entry[0] == "user"]
                    cut = starts[-turns] if len(starts) >= turns else 0
                    bump(skipped, "rolled_back", len(entries) - cut)
                    del entries[cut:]
        elif kind == "response_item":
            item_type = as_text(payload.get("type"))
            if item_type == "function_call":
                if as_text(payload.get("name")) == "update_plan":
                    plan = _plan_text(payload.get("arguments")) or plan
                else:
                    references.extend(tool_paths([{"arguments": payload.get("arguments")}]))
                continue
            if item_type != "message" or has_events:
                continue
            role = as_text(payload.get("role"))
            metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
            if role not in ("user", "assistant"):
                bump(skipped, role if role.isidentifier() else "unknown_role")
                continue
            if metadata.get("compaction_output") or metadata.get("inherited_user_message"):
                bump(skipped, "context")
                continue
            text, reason = _item_text(payload, role)
            if text is None:
                bump(skipped, reason)
                continue
            entries.append((role, text, when))
    messages: list[ImportedMessage] = []
    truncated = False
    last_time: float | None = None
    for role, text, when in entries:
        if len(messages) >= limits.max_messages_per_chat:
            bump(skipped, "limit")
            continue
        message, cut = make_message(role, text, when, limits)
        truncated = truncated or cut
        messages.append(message)
        if when is not None:
            last_time = when if last_time is None else max(last_time, when)
    notes = []
    if summary:
        notes.append({"text": summary, "source": "conversation_summary", "scope": "chat"})
    if plan:
        notes.append({"text": plan, "source": "plan", "scope": "chat"})
    git = records[0].get("payload", {}).get("git")
    branch = as_text(git.get("branch")) if isinstance(git, dict) else ""
    created_at = iso_seconds(meta.get("timestamp")) or (messages[0].created_at if messages else None)
    return (
        ImportedChat(
            source_format=FORMAT,
            source_chat_id=thread_id[:200],
            title=as_text(titles.get(thread_id)),
            created_at=created_at,
            updated_at=last_time or created_at,
            messages=tuple(messages),
            skipped=skipped,
            context={"workspace_hint": as_text(meta.get("cwd")), "models": models[-5:], "notes": notes,
                     "file_references": references[:100], **({"branch": branch[:200]} if branch else {})},
        ),
        truncated,
    )
