"""Web-facing façade over the live-turn registry (`core.live_turns`).

The registry itself moved down to `core/live_turns.py` in DISPATCHER phase 3, because the stale
checkpoint sweep (`core.runtime_continuity`) and the agent (`apps.vool_agent`) both need to ask it
whether a worker is still alive, and a low-level module reaching up into `core.web.api` to find out
reads backwards. Nothing about the contract changed: still keyed by `(session_id, turn_id)` -- NOT
session alone -- so cancelling one in-flight turn never touches a queued or overlapping turn, and
the Event this hands out is the same one the router's `_cancel_check` consumes, so setting it stops
the in-flight model call / tool loop rather than only dropping the HTTP stream.

What DID change is lifetime. Entries used to be released in the streaming generator's `finally`,
i.e. when the client went away. They are now released by the worker itself, on every exit path, so
a turn stays cancellable for exactly as long as it is actually running -- see `core.live_turns` for
the race that made the old ownership give non-deterministic answers.

This module stays as the import path the web layer and its tests already use.
"""
from __future__ import annotations

from core.live_turns import (
    active_turn_count,
    is_checkpoint_live,
    live_checkpoint_ids,
    live_session_ids,
    note_checkpoint,
    register_turn,
    request_cancel,
    reset_live_turns,
    unregister_turn,
)

__all__ = [
    "active_turn_count",
    "is_checkpoint_live",
    "live_checkpoint_ids",
    "live_session_ids",
    "note_checkpoint",
    "register_turn",
    "request_cancel",
    "reset_live_turns",
    "unregister_turn",
]
