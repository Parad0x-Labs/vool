"""Other AI apps' chat stores on this computer: what is offered, and how each is read.

``detect`` only checks whether a known store exists (``stat`` and directory listings, never a
file's content), so an app that is not installed is simply not offered. ``read`` opens a store
only when the user picked that source and asked for the preview; every read copies the store
into a private temporary folder first (``core.chat_import.sqlite_snapshot``) and never writes to
the other app's files.

Each source is described to the user by what it is ("A code editor on this computer"), never
by a product name.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.chat_import import local_agent_sessions_db, local_editor_kv, local_transcript_tree
from core.chat_import.common import DEFAULT_LIMITS, Limits
from core.chat_import.model import ReadResult


@dataclass(frozen=True)
class LocalSource:
    """One kind of local chat store.

    ``id`` is the stable value the screen sends back; ``family`` groups formats that hold the
    same chats (for recognising a chat already brought in); ``kind`` picks the plain
    description the screen shows.
    """

    id: str
    family: str
    kind: str
    paths: Callable[[Path, dict[str, str], str], list[Path]]
    reader: Callable[[Path, dict[str, str], str, Limits, str | None], ReadResult]


def _hermes_paths(home: Path, env: dict[str, str], platform: str) -> list[Path]:
    return local_agent_sessions_db.store_paths(home, env)


def _hermes_read(home: Path, env: dict[str, str], platform: str, limits: Limits, work_dir: str | None) -> ReadResult:
    return local_agent_sessions_db.read_stores(local_agent_sessions_db.store_paths(home, env), limits, work_dir=work_dir)


def _openclaw_paths(home: Path, env: dict[str, str], platform: str) -> list[Path]:
    databases, transcripts = local_transcript_tree.store_paths(home, env)
    return databases + transcripts


def _openclaw_read(home: Path, env: dict[str, str], platform: str, limits: Limits, work_dir: str | None) -> ReadResult:
    databases, transcripts = local_transcript_tree.store_paths(home, env)
    return local_transcript_tree.read_stores(databases, transcripts, limits, work_dir=work_dir)


def _cursor_paths(home: Path, env: dict[str, str], platform: str) -> list[Path]:
    return local_editor_kv.store_paths(home, env, platform)


def _cursor_read(home: Path, env: dict[str, str], platform: str, limits: Limits, work_dir: str | None) -> ReadResult:
    return local_editor_kv.read_stores(local_editor_kv.store_paths(home, env, platform), limits, work_dir=work_dir)


SOURCES: tuple[LocalSource, ...] = (
    LocalSource(id="local_code_editor", family="cursor", kind="code_editor", paths=_cursor_paths, reader=_cursor_read),
    LocalSource(id="local_agent", family="hermes", kind="ai_agent", paths=_hermes_paths, reader=_hermes_read),
    LocalSource(id="local_assistant", family="openclaw", kind="assistant", paths=_openclaw_paths, reader=_openclaw_read),
)

_BY_ID = {source.id: source for source in SOURCES}

#: Platforms whose store locations were verified (macOS and Linux).
SUPPORTED_PLATFORMS = ("darwin", "linux")


def _platform_key(platform: str | None) -> str:
    value = platform or sys.platform
    return "darwin" if value == "darwin" else ("linux" if value.startswith("linux") else value)


def _home(home: Path | None) -> Path:
    return Path(home) if home is not None else Path.home()


def source_by_id(source_id: str) -> LocalSource | None:
    return _BY_ID.get(str(source_id or ""))


def detect(*, home: Path | None = None, env: dict[str, str] | None = None, platform: str | None = None) -> list[dict]:
    """The local sources present on this computer, as ``{id, kind, last_changed}``.

    Existence and modification-time checks only: no store is opened or read here.
    """
    key = _platform_key(platform)
    if key not in SUPPORTED_PLATFORMS:
        return []
    root = _home(home)
    environment = dict(os.environ if env is None else env)
    found: list[dict] = []
    for source in SOURCES:
        try:
            paths = source.paths(root, environment, key)
        except OSError:
            continue
        if not paths:
            continue
        newest = 0.0
        for path in paths:
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
        found.append(
            {
                "id": source.id,
                "kind": source.kind,
                "last_changed": datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else "",
            }
        )
    return found


def read(
    source_id: str,
    *,
    home: Path | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
    work_dir: str | None = None,
) -> ReadResult:
    """Read one local source the user picked. Unknown ids and missing stores are refusals."""
    source = source_by_id(source_id)
    key = _platform_key(platform)
    if source is None or key not in SUPPORTED_PLATFORMS:
        return ReadResult(source_format="", chats=(), problems=("store_missing",), truncated=False)
    root = _home(home)
    environment = dict(os.environ if env is None else env)
    if not source.paths(root, environment, key):
        return ReadResult(source_format="", chats=(), problems=("store_missing",), truncated=False)
    return source.reader(root, environment, key, limits, work_dir)
