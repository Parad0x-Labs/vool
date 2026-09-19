from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path, project_path

MEMORY_FILE = "MEMORY.md"
CONVERSATION_LOG_FILE = "conversation_log.jsonl"
MEMORY_ENTRIES_FILE = "memory_entries.jsonl"
SESSION_SUMMARIES_FILE = "session_summaries.jsonl"
USER_HEURISTICS_FILE = "user_heuristics.jsonl"
DENSE_OPERATOR_PROFILE_FILE = "operator_dense_profile.json"
CHAT_SESSION_META_FILE = "chat_session_meta.json"

MAX_CONVERSATION_LOG_BYTES = 8 * 1024 * 1024
MAX_MEMORY_INDEX_BYTES = 2 * 1024 * 1024
MAX_SESSION_SUMMARY_BYTES = 2 * 1024 * 1024
MAX_USER_HEURISTICS_BYTES = 512 * 1024
MAX_DENSE_OPERATOR_PROFILE_BYTES = 256 * 1024
_JSONL_LOCK = threading.RLock()


def memory_path() -> Path:
    return data_path(MEMORY_FILE)


def conversation_log_path() -> Path:
    return data_path(CONVERSATION_LOG_FILE)


def memory_entries_path() -> Path:
    return data_path(MEMORY_ENTRIES_FILE)


def session_summaries_path() -> Path:
    return data_path(SESSION_SUMMARIES_FILE)


def user_heuristics_path() -> Path:
    return data_path(USER_HEURISTICS_FILE)


def operator_dense_profile_path() -> Path:
    return data_path(DENSE_OPERATOR_PROFILE_FILE)


def chat_session_meta_path() -> Path:
    return data_path(CHAT_SESSION_META_FILE)


def ensure_memory_files(*, ensure_policy_table: callable) -> None:
    path = memory_path()
    if not path.exists():
        path.write_text(default_memory_template(), encoding="utf-8")
    for extra_path in (
        conversation_log_path(),
        memory_entries_path(),
        session_summaries_path(),
        user_heuristics_path(),
        operator_dense_profile_path(),
    ):
        if not extra_path.exists():
            if extra_path.suffix == ".json":
                extra_path.write_text("{}", encoding="utf-8")
            else:
                extra_path.write_text("", encoding="utf-8")
    ensure_policy_table()


def default_memory_template() -> str:
    template_path = project_path("MEMORY.md")
    if template_path.exists():
        text = template_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            return _ensure_memory_sections(text)
    return _ensure_memory_sections(
        "# VOOL Persistent Memory\n\n"
        "## Identity\n\n"
        "- **My name**: VOOL\n"
        "- **Owner's name**: unknown\n\n"
        "## Privacy Pact\n\n"
        "- Not set yet.\n\n"
        "## Learned Knowledge\n\n"
        "<!-- New memories append below -->\n"
    )


def _ensure_memory_sections(text: str) -> str:
    normalized = str(text or "").rstrip() + "\n"
    additions: list[str] = []
    if "## Identity" not in normalized:
        additions.append("## Identity\n\n- **My name**: VOOL\n- **Owner's name**: unknown")
    if "## Privacy Pact" not in normalized:
        additions.append("## Privacy Pact\n\n- Not set yet.")
    if "## Learned Knowledge" not in normalized:
        additions.append("## Learned Knowledge\n\n<!-- New memories append below -->")
    if not additions:
        return normalized
    return normalized.rstrip() + "\n\n" + "\n\n".join(additions).rstrip() + "\n"


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with _JSONL_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_sequenced_jsonl(
    path: Path,
    payload: dict[str, object],
    *,
    namespace_field: str = "session_id",
) -> dict[str, object]:
    """Append one immutable event with a per-namespace sequence."""
    with _JSONL_LOCK:
        namespace = str(payload.get(namespace_field) or "").strip()
        rows = load_jsonl(path)
        sequence = (
            max(
                (
                    int(row.get("event_sequence") or 0)
                    for row in rows
                    if str(row.get(namespace_field) or "").strip()
                    == namespace
                ),
                default=0,
            )
            + 1
        )
        event = {
            **payload,
            "event_id": str(
                payload.get("event_id")
                or f"event-{uuid.uuid4().hex}"
            ),
            "event_sequence": sequence,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event


def load_jsonl(path: Path) -> list[dict[str, object]]:
    with _JSONL_LOCK:
        if not path.exists():
            return []
        rows: list[dict[str, object]] = []
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
        return rows


def write_text_atomic(path: Path, text: str) -> None:
    """Durable atomic text replace: temp + fsync + os.replace + directory fsync.

    A concurrent reader (or a crash) never observes a torn write — the previous
    bytes or the new bytes are the only possible states. This is the publication
    barrier every memory rewrite crosses; the erasure law depends on it (a row
    removal and its tombstone must land as ONE replace, and a mirror rewrite
    must never leave a half-written projection).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    # The rename is durable only once the directory entry is on disk too.
    with contextlib.suppress(OSError):
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def rewrite_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with _JSONL_LOCK:
        content = "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in rows
            if isinstance(row, dict)
        ).rstrip()
        write_text_atomic(path, (content + "\n") if content else "")


def trim_jsonl_file(
    path: Path,
    *,
    max_bytes: int,
    always_keep: Callable[[dict[str, Any]], bool] | None = None,
) -> None:
    try:
        if path.stat().st_size <= max_bytes:
            return
    except Exception:
        return
    rows = load_jsonl(path)
    if len(rows) <= 2:
        return
    if always_keep is None:
        keep = rows[len(rows) // 2 :]
    else:
        pinned = [row for row in rows if always_keep(row)]
        rest = [row for row in rows if not always_keep(row)]
        # Durable erasure evidence (tombstones) never ages out with the facts
        # it guards: an evicted tombstone would let a stale mirror line
        # resurrect the very fact the trim was supposed to keep forgotten.
        keep = [*pinned, *rest[len(rest) // 2 :]]
    rewrite_jsonl(path, keep)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
