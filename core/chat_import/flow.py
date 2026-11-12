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
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.chat_import import importer, local_sources, readers, store
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
    contexts: dict[str, dict] = field(default_factory=dict)


@dataclass
class _Run:
    run_id: str
    total: int
    state: str = "running"
    done: int = 0
    outcome: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    session_ids: list[str] = field(default_factory=list)
    continuation: bool = False
    working_context: bool = False


@dataclass(frozen=True)
class _Upload:
    content: bytes
    receipt: Path


_LOCK = threading.RLock()
_UPLOADS: dict[str, _Upload] = {}
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
    _drop_orphan_uploads()
    return {"local": local_sources.detect(home=home, env=env, platform=platform), "file": True}


def _drop_orphan_uploads() -> None:
    """Delete picked files no preview refers to any more (left behind by a restart)."""
    with _LOCK:
        known = {upload.receipt for upload in _UPLOADS.values()}
        with contextlib.suppress(OSError):
            for leftover in _import_dir("incoming").iterdir():
                if leftover.is_file() and leftover.suffix == ".zip" and leftover not in known:
                    leftover.unlink()


# --------------------------------------------------------------------------- upload


def _discard_uploads() -> None:
    for upload in _UPLOADS.values():
        with contextlib.suppress(OSError):
            upload.receipt.unlink()
    _UPLOADS.clear()
    with contextlib.suppress(OSError):
        for leftover in _import_dir("incoming").iterdir():
            if leftover.is_file() and leftover.suffix == ".zip":
                leftover.unlink()


def stage_upload(data: bytes) -> dict[str, Any]:
    """Hold the picked archive in memory; persist only an empty, opaque receipt.

    Archives may contain ignored credential/configuration members. Raw upload bytes must
    never enter a runtime data file, including before preview or after a process crash.
    Restart requires choosing the source again; there is no durable raw archive to recover.
    """
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
        path.touch(mode=0o600, exist_ok=False)
        _UPLOADS[upload_id] = _Upload(content=data, receipt=path)
    return {"upload_id": upload_id, "size_bytes": len(data)}


# --------------------------------------------------------------------------- preview


def _chat_row(index: int, chat: ImportedChat, key: str, already: bool, connection=None) -> dict[str, Any]:
    from core.memory.revocation import authorize_payload
    from core.session_portability.redaction import RedactionReport

    title, rows = importer._prepare_rows(chat, RedactionReport())
    hint = importer._scrub(str(chat.context.get("workspace_hint") or ""), RedactionReport())
    payload = {"title": title, "messages": [row[1] for row in rows], "workspace": hint}
    origins = store.source_origins(connection, key) if connection is not None else []
    withheld = not authorize_payload(payload) or any(not authorize_payload(payload, origin) for origin in origins)
    return {
        "ref": str(index),
        "title": "" if withheld else title,
        "created_at": iso_time(chat.created_at or importer._first_time(chat)),
        "updated_at": iso_time(chat.updated_at or importer._last_time(chat) or chat.created_at),
        "message_count": 0 if withheld else len(rows),
        "already_here": already,
        "withheld": withheld,
        "workspace_hint": "" if withheld else hint,
    }


def _publish_preview(result: ReadResult, *, upload_id: str = "") -> dict[str, Any]:
    global _PREVIEW
    # A chat a crashed run left half-finished is finished first, so it is listed as here (and
    # shows in the chat list) instead of blocking its own re-import.
    importer.recover_pending()
    keys = [importer.source_key(chat) for chat in result.chats]
    already = importer.existing_keys(keys)
    preview = _Preview(preview_id=secrets.token_hex(16), result=result, keys=keys, already=already, upload_id=upload_id)
    from core.memory.revocation import writing

    with contextlib.suppress(RuntimeError):
        with writing({}, None):
            pass
    connection = store.connect()
    try:
        rows = [_chat_row(index, chat, key, key in already, connection)
                for index, (chat, key) in enumerate(zip(result.chats, keys, strict=False))]
    finally:
        connection.close()
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
        upload = _UPLOADS.get(str(upload_id or ""))
    if upload is None or not upload.receipt.is_file():
        raise FlowError("upload_missing", 404)
    try:
        result = readers.read_bytes(upload.content, limits=limits)
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


def review_context(preview_id: str, ref: str, *, root: str, instructions: str = "",
                   private: bool = False, old_root: str = "", memories: str = "") -> dict[str, Any]:
    from core.chat_import import source_context
    from core.cloud_escalation_policy import available_import_models

    with _LOCK:
        preview = _PREVIEW
        if _running():
            raise FlowError("busy", 409)
        if preview is None or preview.preview_id != preview_id:
            raise FlowError("preview_missing", 404)
        if time.time() - preview.created > PREVIEW_TTL_SECONDS:
            raise FlowError("preview_expired", 410)
        if not ref.isdigit() or int(ref) >= len(preview.result.chats):
            raise FlowError("bad_selection")
        chat = preview.result.chats[int(ref)]
    try:
        plan = source_context.review(chat, root=root, additional_instructions=instructions, private=private, old_root=old_root, additional_memories=memories)
        from core.memory.revocation import authorize_payload, writing

        # A fresh empty authority is initialized through the public writer,
        # before the preview can disclose context that a prior forget withdrew.
        with writing({}, None):
            payload = {key: value for key, value in plan.items() if key != "root_identity"}
            connection = store.connect()
            try:
                origins = store.source_origins(connection, importer.source_key(chat))
            finally:
                connection.close()
            if not authorize_payload(payload) or any(not authorize_payload(payload, origin) for origin in origins):
                raise FlowError("context_withheld", 409)
    except (OSError, ValueError):
        raise FlowError("context_unavailable") from None
    with _LOCK:
        if _PREVIEW is not preview or _running():
            raise FlowError("preview_missing", 409)
        reviewed = {"plan_id": secrets.token_hex(16), "plan": plan}
        candidate = {**preview.contexts, ref: reviewed}
        import json

        if len(json.dumps(candidate, ensure_ascii=False).encode()) > 32 << 20:
            raise FlowError("too_large", 413)
        preview.contexts[ref] = reviewed
    return {"plan_id": reviewed["plan_id"], "ref": ref, "context": plan,
            "local_models": available_import_models()}


def start_import(preview_id: str, refs: list[str], *, continuation: bool = False,
                 contexts: dict | None = None) -> dict[str, Any]:
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
        if not isinstance(continuation, bool):
            raise FlowError("bad_selection")
        if contexts is None:
            contexts = {}
        if not isinstance(contexts, dict) or set(contexts) - set(refs):
            raise FlowError("bad_context")
        from core.chat_import import workspace_transfer
        from core.chat_import.ownership import keyed_digest

        selected_contexts = {}
        for ref, request in contexts.items():
            reviewed = preview.contexts.get(ref)
            if (not isinstance(request, dict) or set(request) != {"plan_id", "options"} or reviewed is None
                    or request["plan_id"] != reviewed["plan_id"]):
                raise FlowError("bad_context")
            try:
                options = workspace_transfer.selection(reviewed["plan"], request["options"])
            except ValueError:
                raise FlowError("bad_context") from None
            base_key = preview.keys[int(ref)]
            review_key = keyed_digest(["working-context", base_key,
                                       workspace_transfer.admission_payload(reviewed["plan"], options),
                                       {key: value for key, value in options.items() if key != "working_copy"}])
            previous = store.working_context_for_key(base_key)
            if base_key in preview.already and previous.get("review_key") != review_key:
                if not options["working_copy"]:
                    raise FlowError("context_copy_required", 409)
                key = review_key
            else:
                key = base_key
            existing = store.sessions_for_keys([key])
            if key in existing and store.context_pending(existing[key]):
                raise FlowError("context_incomplete", 409)
            selected_contexts[base_key] = {**reviewed, "options": options, "key": key, "review_key": review_key}
        run = _Run(run_id=secrets.token_hex(16), total=len(chosen), continuation=continuation, working_context=bool(selected_contexts))
        store.create_batch(run.run_id)
        _RUN = run
        _THREAD = threading.Thread(target=_run_import, args=(run, chosen, preview, continuation, selected_contexts), name="chat-import", daemon=True)
        _THREAD.start()
    return status()


def _run_import(run: _Run, chosen: list[ImportedChat], preview: _Preview, continuation: bool,
                contexts: dict | None = None) -> None:
    global _PREVIEW

    def progress(done: int, total: int) -> None:
        with _LOCK:
            run.done = done

    try:
        from core.chat_import import workspace_transfer

        contexts = contexts or {}
        outcome = importer.import_chats(chosen, progress=progress, batch_id=run.run_id, continuation=continuation,
                                        admission_payloads={key: workspace_transfer.admission_payload(item["plan"], item["options"])
                                                            for key, item in contexts.items()},
                                        key_variants={key: item["key"] for key, item in contexts.items()})
        sessions = store.sessions_for_keys([item["key"] for item in contexts.values()])
        for chat in chosen:
            context = contexts.get(importer.source_key(chat))
            if context and sessions.get(context["key"]) in outcome.session_ids:
                workspace_transfer.apply(context["plan"], context["options"], batch_id=run.run_id,
                                         session_id=sessions[context["key"]], source=chat.source_format,
                                         continuation=continuation, review_key=context["review_key"])
        store.set_batch_state(run.run_id, "done")
        with _LOCK:
            run.outcome = outcome.as_dict()
            run.session_ids = list(outcome.session_ids)
            run.done = run.total
            run.state = "done"
    except Exception as exc:
        with contextlib.suppress(Exception):
            store.set_batch_state(run.run_id, "failed")
        _LOG.warning("chat import stopped: %s", type(exc).__name__)
        with _LOCK:
            run.state = "failed"
            run.error = "store_failed"
    finally:
        with _LOCK:
            if _PREVIEW is preview:
                _PREVIEW = None
            if preview.upload_id:
                upload = _UPLOADS.pop(preview.upload_id, None)
                if upload is not None:
                    with contextlib.suppress(OSError):
                        upload.receipt.unlink()


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
            "continuation": run.continuation,
            "working_context": run.working_context,
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


def undo(batch_id: str) -> dict[str, Any]:
    """Remove only chats created by this batch, through the canonical deletion authority.

    Later VOOL turns make an imported chat new work. Refuse the batch instead of deleting
    those turns. A partial deletion fault leaves a durable retryable ledger entry.
    """
    from contextlib import ExitStack

    from core.context_namespace import load_chat_namespace
    from core.memory.entries import delete_conversation_session
    from core.persistent_memory import recent_conversation_events
    from core.routing_decision_log import purge_session_routing_telemetry
    from core.session_turn_gate import hold_session_turn_gate
    from storage.dialogue_memory import recent_dialogue_turns

    with _LOCK, ExitStack() as gates:
        if _running():
            raise FlowError("busy", 409)
        sessions = store.batch_sessions(str(batch_id or ""))
        if sessions is None:
            raise FlowError("batch_missing", 404)
        from core.project_store import _load, hold_project_context

        projects = sorted(pid for pid, row in _load().items()
                          if any(g.get("batch_id") == batch_id for g in row.get("local_context_imports", [])))
        for project in projects:
            gates.enter_context(hold_project_context(project))
        # The stable order prevents deadlocks between overlapping turn/deletion requests.
        for session_id in sessions:
            gates.enter_context(hold_session_turn_gate(session_id))
        for session_id in sessions:
            if (recent_conversation_events(session_id, limit=1)
                    or recent_dialogue_turns(session_id, limit=1)):
                raise FlowError("batch_in_use", 409)
        from core.chat_import import workspace_transfer

        try:
            workspace_transfer.assert_undo_possible(batch_id, sessions)
        except ValueError:
            raise FlowError("batch_in_use", 409) from None
        store.set_batch_state(batch_id, "undoing")
        removed = 0
        try:
            for session_id in sessions:
                workspace_transfer.purge(batch_id, session_id)
                delete_conversation_session(session_id)
                namespace = load_chat_namespace(session_id)
                if (store.imported_history_messages(session_id)
                        or (namespace is not None and namespace.lifecycle_state != "deleted")):
                    raise RuntimeError("import deletion incomplete")
                telemetry = purge_session_routing_telemetry(session_id)
                if (not telemetry.ok or telemetry.shadow_rows_unattributable
                        or telemetry.jsonl_rows_unattributable):
                    raise RuntimeError("import deletion could not be verified")
                store.mark_batch_session_removed(batch_id, session_id)
                removed += 1
        except Exception:
            store.set_batch_state(batch_id, "failed")
            raise FlowError("undo_failed", 500) from None
        store.set_batch_state(batch_id, "undone")
        return {"batch_id": batch_id, "removed": removed, "state": "undone"}
