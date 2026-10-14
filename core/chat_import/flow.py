"""The one owner of a chat import as the settings screen drives it.

Steps, each started by the user:

1. ``sources()`` -- which local apps are present (existence checks only) plus the file option;
2. ``stage_upload(bytes)`` -- an export zip the user picked, kept owner-only under VOOL's data
   folder and addressed by an opaque id (never a path the browser could choose);
3. ``preview_local(source_id)`` / ``preview_upload(upload_id)`` -- read the chosen source NOW
   (every preview reads again: picking a source is consent for that one read) and list its chats
   with titles, dates and message counts, marking those already here;
4. ``start_import(preview_id, refs)`` -- bring in exactly the previewed chats the user ticked, in a
   background run that reports progress through ``status()``.

Nothing here touches the network. One preview and one run exist at a time; a finished run
discards the uploaded file and the preview. Message contents are never logged.
"""
from __future__ import annotations

import contextlib
import logging
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.chat_import import importer, local_sources, readers
from core.chat_import.common import DEFAULT_LIMITS, Limits
from core.chat_import.model import ImportedChat, ReadResult
from core.chat_import.store import iso_time

_LOG = logging.getLogger(__name__)

#: Largest export zip the upload door accepts (the bytes are held in memory once while staged).
MAX_UPLOAD_BYTES = 512 << 20
#: How long a preview stays valid for an import.
PREVIEW_TTL_SECONDS = 3600
#: Most chats one import run takes (the readers already cap a source at this many).
MAX_IMPORT_CHATS = DEFAULT_LIMITS.max_conversations


class FlowError(Exception):
    """A typed refusal for the route: ``code`` is stable, ``status`` the HTTP status."""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class _Preview:
    preview_id: str
    result: ReadResult
    keys: list[str]
    already: set[str]
    upload_id: str = ""
    created: float = field(default_factory=time.time)


@dataclass
class _Run:
    run_id: str
    total: int
    state: str = "running"
    done: int = 0
    outcome: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    session_ids: list[str] = field(default_factory=list)


_LOCK = threading.RLock()
_UPLOADS: dict[str, Path] = {}
_PREVIEW: _Preview | None = None
_RUN: _Run | None = None
_THREAD: threading.Thread | None = None


def _import_dir(*parts: str) -> Path:
    from core.runtime_paths import data_path

    path = data_path("chat_import", *parts)
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.chmod(0o700)
    return path


def _running() -> bool:
    return _RUN is not None and _RUN.state == "running"


def reset_for_tests() -> None:
    """Drop the preview, uploads and finished run state (tests only)."""
    global _PREVIEW, _RUN, _THREAD
    with _LOCK:
        if _THREAD is not None:
            _THREAD.join(timeout=30)
        _discard_uploads()
        _PREVIEW = None
        _RUN = None
        _THREAD = None


# --------------------------------------------------------------------------- sources


def sources(*, home: Path | None = None, env: dict[str, str] | None = None, platform: str | None = None) -> dict[str, Any]:
    return {"local": local_sources.detect(home=home, env=env, platform=platform), "file": True}


# --------------------------------------------------------------------------- upload


def _discard_uploads() -> None:
    for path in _UPLOADS.values():
        with contextlib.suppress(OSError):
            path.unlink()
    _UPLOADS.clear()
    with contextlib.suppress(OSError):
        for leftover in _import_dir("incoming").iterdir():
            if leftover.is_file() and leftover.suffix == ".zip":
                leftover.unlink()


def stage_upload(data: bytes) -> dict[str, Any]:
    """Keep one picked export file for a preview. Replaces any earlier upload and preview."""
    global _PREVIEW
    if not data:
        raise FlowError("empty_file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise FlowError("file_too_large", 413)
    with _LOCK:
        if _running():
            raise FlowError("busy", 409)
        _discard_uploads()
        _PREVIEW = None
        upload_id = secrets.token_hex(16)
        path = _import_dir("incoming") / f"{upload_id}.zip"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        _UPLOADS[upload_id] = path
    return {"upload_id": upload_id, "size_bytes": len(data)}


# --------------------------------------------------------------------------- preview


def _chat_row(index: int, chat: ImportedChat, key: str, already: bool) -> dict[str, Any]:
    from core.session_portability.redaction import RedactionReport

    title, rows = importer._prepare_rows(chat, RedactionReport())
    return {
        "ref": str(index),
        "title": title,
        "created_at": iso_time(chat.created_at or importer._first_time(chat)),
        "updated_at": iso_time(chat.updated_at or importer._last_time(chat) or chat.created_at),
        "message_count": len(rows),
        "already_here": already,
    }


def _publish_preview(result: ReadResult, *, upload_id: str = "") -> dict[str, Any]:
    global _PREVIEW
    # A chat a crashed run left half-finished is finished first, so it is listed as here (and
    # shows in the chat list) instead of blocking its own re-import.
    importer.recover_pending()
    keys = [importer.source_key(chat) for chat in result.chats]
    already = importer.existing_keys(keys)
    preview = _Preview(preview_id=secrets.token_hex(16), result=result, keys=keys, already=already, upload_id=upload_id)
    rows = [_chat_row(index, chat, key, key in already) for index, (chat, key) in enumerate(zip(result.chats, keys, strict=False))]
    with _LOCK:
        _PREVIEW = preview
    return {
        "preview_id": preview.preview_id,
        "readable": bool(result.source_format),
        "chats": rows,
        "problems": list(result.problems),
        "truncated": result.truncated,
        "total": len(rows),
        "already_here": sum(1 for row in rows if row["already_here"]),
    }


def preview_upload(upload_id: str, *, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    with _LOCK:
        if _running():
            raise FlowError("busy", 409)
        path = _UPLOADS.get(str(upload_id or ""))
    if path is None or not path.is_file():
        raise FlowError("upload_missing", 404)
    try:
        result = readers.read(path, limits=limits)
    except Exception:
        _LOG.warning("chat import: the export file could not be read")
        result = ReadResult(source_format="", chats=(), problems=("file_unreadable",), truncated=False)
    return _publish_preview(result, upload_id=str(upload_id))


def preview_local(
    source_id: str,
    *,
    home: Path | None = None,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    if local_sources.source_by_id(source_id) is None:
        raise FlowError("unknown_source")
    with _LOCK:
        if _running():
            raise FlowError("busy", 409)
    work_dir = _import_dir("work")
    try:
        result = local_sources.read(source_id, home=home, env=env, platform=platform, limits=limits, work_dir=str(work_dir))
    except Exception:
        _LOG.warning("chat import: a local chat store could not be read")
        result = ReadResult(source_format="", chats=(), problems=("store_unreadable",), truncated=False)
    finally:
        # Every snapshot removes itself; anything a crashed read left behind goes now.
        for leftover in work_dir.iterdir():
            shutil.rmtree(leftover, ignore_errors=True)
    return _publish_preview(result)


# --------------------------------------------------------------------------- import


def start_import(preview_id: str, refs: list[str]) -> dict[str, Any]:
    global _RUN, _THREAD
    with _LOCK:
        if _running():
            raise FlowError("busy", 409)
        preview = _PREVIEW
        if preview is None or preview.preview_id != str(preview_id or ""):
            raise FlowError("preview_missing", 404)
        if time.time() - preview.created > PREVIEW_TTL_SECONDS:
            raise FlowError("preview_expired", 410)
        if not isinstance(refs, list) or not refs:
            raise FlowError("nothing_selected")
        chosen: list[ImportedChat] = []
        seen: set[int] = set()
        for ref in refs:
            if not isinstance(ref, str) or not ref.isdigit():
                raise FlowError("bad_selection")
            index = int(ref)
            if index >= len(preview.result.chats) or index in seen:
                raise FlowError("bad_selection")
            seen.add(index)
            chosen.append(preview.result.chats[index])
        if len(chosen) > MAX_IMPORT_CHATS:
            raise FlowError("bad_selection")
        run = _Run(run_id=secrets.token_hex(8), total=len(chosen))
        _RUN = run
        _THREAD = threading.Thread(target=_run_import, args=(run, chosen, preview), name="chat-import", daemon=True)
        _THREAD.start()
    return status()


def _run_import(run: _Run, chosen: list[ImportedChat], preview: _Preview) -> None:
    global _PREVIEW

    def progress(done: int, total: int) -> None:
        with _LOCK:
            run.done = done

    try:
        outcome = importer.import_chats(chosen, progress=progress)
        with _LOCK:
            run.outcome = outcome.as_dict()
            run.session_ids = list(outcome.session_ids)
            run.done = run.total
            run.state = "done"
    except Exception as exc:
        _LOG.warning("chat import stopped: %s", type(exc).__name__)
        with _LOCK:
            run.state = "failed"
            run.error = "store_failed"
    finally:
        with _LOCK:
            if _PREVIEW is preview:
                _PREVIEW = None
            if preview.upload_id:
                path = _UPLOADS.pop(preview.upload_id, None)
                if path is not None:
                    with contextlib.suppress(OSError):
                        path.unlink()


def status() -> dict[str, Any]:
    with _LOCK:
        run = _RUN
        if run is None:
            return {"state": "idle"}
        return {
            "state": run.state,
            "run_id": run.run_id,
            "total": run.total,
            "done": run.done,
            "outcome": dict(run.outcome),
            "error": run.error,
            "first_session_id": run.session_ids[0] if run.session_ids else "",
        }


def discard() -> dict[str, Any]:
    """Forget the preview and delete the uploaded file (never touches a running import)."""
    global _PREVIEW
    with _LOCK:
        if _running():
            raise FlowError("busy", 409)
        _PREVIEW = None
        _discard_uploads()
    return {"discarded": True}
