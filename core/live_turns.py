"""The live-turn registry: which turns have a worker actually executing right now.

ONE registry, two questions. It has always answered "can this turn be cancelled" (a
``threading.Event`` per live turn, keyed by ``(session_id, turn_id)``, consumed by the router's
``_cancel_check``). It now also answers "is this checkpoint's worker still alive", because both
questions have the same correct answer source and answering them separately is how they drift.

**Why it moved here.** It used to live in ``core/web/api/turn_cancel.py`` (which remains as a thin
façade, so every existing import and its tests keep working). Two things outside the web layer need
it now -- the stale-checkpoint sweep in ``core.runtime_continuity`` and the agent in
``apps.vool_agent`` -- and a low-level module reaching up into ``core.web.api`` to ask whether work
is running reads exactly backwards. Nothing in this module imports anything from the project, so it
can sit under all of them.

**Lifetime is the worker's, not the HTTP stream's.** This is the DISPATCHER phase-3 repair. The
entry used to be dropped in the streaming generator's ``finally``, i.e. when the CLIENT went away.
Once a chat can keep running in the background (phase 2), the client going away is ordinary, and
whether a turn stayed cancellable came down to where that generator happened to be suspended when
the browser disconnected:

* blocked in ``event_queue.get()`` -- the usual case for a long turn -- ``close()`` cannot be
  delivered, the entry survives, cancel works;
* parked at a ``yield`` between events -- ``close()`` lands promptly, the entry is dropped, and
  ``/api/chat/cancel`` answers ``not_found`` for a turn that is still burning tokens.

A non-deterministic answer to "can I stop this" is not a working cancel. So registration is now
owned end to end by the worker: registered before its thread starts, released in that thread's own
``finally``, on every exit path -- completion, exception, cancellation. The stream may come and go
as many times as the user navigates; it no longer has a vote.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class _LiveTurn:
    """One executing turn. `checkpoint_id` is empty until the runtime has created/resumed one."""

    cancel_event: threading.Event = field(default_factory=threading.Event)
    checkpoint_id: str = ""


_LOCK = threading.Lock()
_TURNS: dict[tuple[str, str], _LiveTurn] = {}


def _key(session_id: str, turn_id: str) -> tuple[str, str]:
    return (str(session_id or "").strip(), str(turn_id or "").strip())


def register_turn(session_id: str, turn_id: str) -> threading.Event:
    """Register a live turn and return its cancel Event (to place on its source_context).

    Call this BEFORE starting the worker, so a cancel that races the very first instruction still
    finds something to set.
    """
    turn = _LiveTurn()
    with _LOCK:
        _TURNS[_key(session_id, turn_id)] = turn
    return turn.cancel_event


def unregister_turn(session_id: str, turn_id: str) -> None:
    """Release a turn once its WORKER has genuinely finished -- not when its stream closed.

    Idempotent: a double release is a no-op, so the worker's `finally` and any belt-and-braces
    caller cannot double-count.
    """
    with _LOCK:
        _TURNS.pop(_key(session_id, turn_id), None)


def note_checkpoint(session_id: str, turn_id: str, checkpoint_id: str) -> None:
    """Record which durable checkpoint this live turn is executing.

    Stamped by the agent once ``prepare_runtime_checkpoint`` has created or resumed one. This is
    what lets the stale sweep tell "quiet but alive" from "gone". A turn whose entry is already
    released (it finished while this was being called) is not resurrected.
    """
    clean = str(checkpoint_id or "").strip()
    if not clean:
        return
    with _LOCK:
        turn = _TURNS.get(_key(session_id, turn_id))
        if turn is not None:
            turn.checkpoint_id = clean


def request_cancel(session_id: str, turn_id: str) -> str:
    """Cancel one specific in-flight turn. Idempotent:

    - ``"cancelled"``: a worker is live for this turn (Event set now, or already set)
    - ``"not_found"``: no live worker (it finished, or never started).
    """
    with _LOCK:
        turn = _TURNS.get(_key(session_id, turn_id))
    if turn is None:
        return "not_found"
    turn.cancel_event.set()
    return "cancelled"


def is_checkpoint_live(checkpoint_id: str) -> bool:
    """True when a worker is currently executing this checkpoint."""
    clean = str(checkpoint_id or "").strip()
    if not clean:
        return False
    with _LOCK:
        return any(turn.checkpoint_id == clean for turn in _TURNS.values())


def live_checkpoint_ids() -> frozenset[str]:
    """Every checkpoint with a live worker. The stale sweep's exemption set."""
    with _LOCK:
        return frozenset(turn.checkpoint_id for turn in _TURNS.values() if turn.checkpoint_id)


def live_session_ids() -> frozenset[str]:
    """Sessions with at least one live worker (diagnostics; the sweep exempts by checkpoint)."""
    with _LOCK:
        return frozenset(session_id for session_id, _ in _TURNS if session_id)


def active_turn_count() -> int:
    """Live turns currently registered (tests / diagnostics)."""
    with _LOCK:
        return len(_TURNS)


def reset_live_turns() -> None:
    """Drop every entry. Tests only -- a process with real work in flight must never call this."""
    with _LOCK:
        _TURNS.clear()
