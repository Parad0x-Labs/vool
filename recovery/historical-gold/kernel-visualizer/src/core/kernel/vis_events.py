"""Optional, fully-gated observer side-channel for the trace visualizer.

This module is OFF unless the operator sets the env var ``VOOL_VIS_EVENTS`` to a
writable path. When OFF it is a strict no-op: zero imports pulled into the hot
path, zero exceptions, zero behavior change. When ON it appends one JSON line
per effect *attempt* to an append-only file separate from the canonical journal
(the journal remains the single source of truth; this file is a display stream
only, never read by any kernel law).

The write is best-effort: any failure to append swallows the error so a display
stream can never affect a turn's correctness, durability, or record.
"""
from __future__ import annotations

import json
import os
import time


def enabled() -> bool:
    return bool(os.environ.get("VOOL_VIS_EVENTS", ""))


def emit(event: dict) -> None:
    """Append one display event, best-effort. No-ops when disabled."""
    path = os.environ.get("VOOL_VIS_EVENTS", "")
    if not path:
        return
    payload = dict(event)
    payload.setdefault("_ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    payload.setdefault("_kind", "effect")
    try:
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        # A display stream must never take down or alter a turn.
        pass


def effect_started(effect_id: str, args_hash: str, turn_index: int | None = None) -> None:
    emit({"_kind": "effect_started", "effect_id": effect_id,
          "args_hash": args_hash, "turn_index": turn_index})
