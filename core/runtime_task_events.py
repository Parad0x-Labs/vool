from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable
from typing import Any

from core.runtime_continuity import (
    append_runtime_event,
    configure_runtime_continuity_db_path,
    list_recent_runtime_session_events,  # noqa: F401
    list_runtime_session_events,  # noqa: F401
    list_runtime_sessions,  # noqa: F401
    reset_runtime_continuity_state,
)

RuntimeEventSink = Callable[[dict[str, Any]], None]

_SINKS: dict[str, RuntimeEventSink] = {}
_LOCK = threading.RLock()


def _execution_truth():
    """Imported lazily: `core.execution_truth` reads `core.runtime_continuity`, which imports this."""
    from core import execution_truth

    return execution_truth


def new_runtime_event_stream_id() -> str:
    return f"runtime-stream-{uuid.uuid4().hex}"


def register_runtime_event_sink(stream_id: str, sink: RuntimeEventSink) -> None:
    key = str(stream_id or "").strip()
    if not key:
        return
    with _LOCK:
        _SINKS[key] = sink


def unregister_runtime_event_sink(stream_id: str) -> None:
    key = str(stream_id or "").strip()
    if not key:
        return
    with _LOCK:
        _SINKS.pop(key, None)


def configure_runtime_event_store(db_path: str | None) -> None:
    configure_runtime_continuity_db_path(db_path)


def emit_runtime_event(
    source_context: dict[str, Any] | None,
    *,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    normalized_event_type = str(event_type or "status").strip() or "status"
    raw_message = str(message or "")
    payload = {
        "event_type": normalized_event_type,
        "message": raw_message if normalized_event_type == "model_output_chunk" else raw_message.strip(),
    }
    payload.update(dict(details or {}))
    # Tag every event with the client turn id (sent per turn in the /api/chat body, also used for
    # cancel) so the Activity panel can scope the ledger timeline to the CURRENT turn -- additive,
    # never overrides a detail a caller already set.
    client_turn_id = str((source_context or {}).get("cancel_turn_id") or "").strip()
    if client_turn_id:
        payload.setdefault("client_turn_id", client_turn_id)
    stream_id = str((source_context or {}).get("runtime_event_stream_id") or "").strip()
    session_id = str((source_context or {}).get("runtime_session_id") or (source_context or {}).get("session_id") or "").strip()
    # Stamp the canonical turn identity on EVERY event, from the one resolver, before anything is
    # stored or streamed. Previously an event carried a turn id only when `cancel_turn_id` happened
    # to be in the source context, so 554 of 809 turn receipts landed with no identity at all and
    # the stores could not be joined to each other -- the fragmentation that let each consumer
    # report a different reality. Additive: a caller that already named its turn keeps its value.
    turn_key = _execution_truth().resolve_turn_key(source_context, payload)
    if turn_key:
        payload.setdefault("turn_key", turn_key)
    sink: RuntimeEventSink | None = None
    if session_id:
        payload = append_runtime_event(
            session_id=session_id,
            event_type=payload["event_type"],
            message=payload["message"],
            details={key: value for key, value in payload.items() if key not in {"event_type", "message"}},
        )
        # An execution becomes an authoritative fact HERE, at the one function every runtime event
        # passes through, rather than at the sixteen call sites that emit `tool_executed` -- which is
        # exactly where the shapes diverged (the live-data lane's thin event carried no receipt, so
        # its 128 executions existed in no receipt store at all). A new tool call site is recorded by
        # existing. Never raises: recording must not be able to fail a working turn.
        with contextlib.suppress(Exception):
            _execution_truth().note_runtime_event(
                source_context,
                event_type=payload["event_type"],
                session_id=session_id,
                details={key: value for key, value in payload.items() if key not in {"event_type", "message"}},
            )
    if stream_id:
        with _LOCK:
            sink = _SINKS.get(stream_id)
    if sink is not None:
        try:
            sink(dict(payload))
        except Exception:
            return


def reset_runtime_event_state() -> None:
    with _LOCK:
        _SINKS.clear()
    reset_runtime_continuity_state()
