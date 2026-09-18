"""Capture of what VOOL actually sends to a model, for debugging a turn end to end.

Opt-in: arm it with `VOOL_DEBUG_PROMPT=1`. Off by default, and one env read when off.

Why this exists: a question like "what's your name?" was answered with a stale name even though every
inspectable source -- persona row, memory excerpt, grounding facts, display name -- reported the
current one. Grepping the assembly code could not settle it, because the assembled context and the
final wire payload are not the same thing. This records the payload the provider actually receives,
so "what did the model see?" becomes a measurement instead of an argument.

Why it stays OPT-IN, despite the cost of that: capture used to require `VOOL_DEBUG_PROMPT=1` and
the daemon never exported it, so when an audit turn failed on three provider lanes the one surface
that could have shown the offending request was empty and the cause was recovered by hand-tracing.
That is a real cost, and default-on was implemented to fix it — then reverted, measured, on
2026-07-29.

The reason: this file writes the FULL outbound prompt to disk, and `core/secret_redaction.py` is
label-driven, not entropy-driven. Probed directly, a 12-word BIP-39 mnemonic written as prose and an
`aws_secret: ...` without the `_access_key` suffix both reach disk verbatim, while the same secrets
behind a recognised label are masked. Default-on would convert an opt-in exposure into an
on-by-default one on a machine that holds live keypairs, which is not a trade a debug convenience
earns. `core/turn_trace.py` already prints the exact command to arm it at the moment you need it,
which is the cheap half of the benefit without the standing exposure.

Revisit only when redaction is shape/entropy-aware rather than label-driven.

Cost, measured on this machine by replaying the 29 real records the previous run left in
`~/.vool_runtime/logs/prompt_debug.jsonl` (payload JSON: mean 33 KB, median 47 KB, max 49 KB)
through `dump_outbound_prompt` itself: **2.39 ms mean / 3.73 ms max per model call**, of which
2.11 ms is the secret redactor walking every string. Rotation adds 0.086 ms amortized (11 ms, once
per ~128 captures). Opted out it is 0.0002 ms -- one env read. A model call costs hundreds of ms to
seconds, so capture is well under 1% of a turn: not material on the hot path.

Disk is bounded: see `_maybe_rotate`. Steady state is 4-8 MB, never more.

Output: one JSON object per model call, appended to `$VOOL_HOME/logs/prompt_debug.jsonl`.

    python -m core.turn_trace                 # the joined timeline
    tail -1 ~/.vool_runtime/logs/prompt_debug.jsonl | python -m json.tool
    VOOL_DEBUG_PROMPT=1 <run vool>           # arm capture for the next turns

Secrets are masked with the shared redactor before anything is written, and the file is kept
owner-only (0600) -- through rotation too -- because a prompt carries whatever the user pasted
into chat.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENV_FLAG = "VOOL_DEBUG_PROMPT"
_TRUTHY = {"1", "true", "yes", "on"}
_MAX_CONTENT = 24000  # per message; a prompt with a big file read should not produce a 10 MB line
_ROTATE_BYTES = 8 * 1024 * 1024       # rotate past ~8MB ...
_ROTATE_KEEP_BYTES = 4 * 1024 * 1024  # ... keeping the newest ~4MB of records

# With capture armed, two provider lanes racing through one turn both write here — the mux and the
# local/remote race are genuinely concurrent. The lock covers append AND rotation together: rotation
# is read-modify-replace, and an append landing between its read and its os.replace would be
# silently discarded.
_WRITE_LOCK = threading.Lock()


def prompt_debug_enabled() -> bool:
    """Opt-in. Unset means OFF -- see the module docstring for why that stayed true.

    Only an explicitly truthy value arms capture, so an empty or whitespace `VOOL_DEBUG_PROMPT=`
    fails closed rather than silently recording every prompt to disk.
    """
    return str(os.environ.get(_ENV_FLAG, "")).strip().lower() in _TRUTHY


def _log_path() -> Path:
    from core.runtime_paths import active_data_dir

    directory = Path(active_data_dir()).parent / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(directory, 0o700)
    return directory / "prompt_debug.jsonl"


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_CONTENT:
        return value[:_MAX_CONTENT] + f"…[clipped {len(value) - _MAX_CONTENT} chars]"
    return value


def _redacted(value: Any) -> Any:
    from core.secret_redaction import redact_secrets

    if isinstance(value, str):
        return _clip(redact_secrets(value))
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redacted(item) for item in value]
    return value


def dump_outbound_prompt(
    payload: dict[str, Any],
    *,
    lane: str = "",
    provider_id: str = "",
    model: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Append the outbound payload for one model call. Never raises, never blocks a turn."""
    if not prompt_debug_enabled():
        return
    try:
        messages = payload.get("messages")
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lane": lane,
            "provider_id": provider_id,
            "model": model or payload.get("model", ""),
            "message_count": len(messages) if isinstance(messages, list) else 0,
            # A quick shape summary makes the common question ("which roles, how big?") answerable
            # without reading the whole record.
            "roles": [str(m.get("role", "")) for m in messages] if isinstance(messages, list) else [],
            "total_chars": sum(len(str(m.get("content", ""))) for m in messages) if isinstance(messages, list) else 0,
            "payload": _redacted(payload),
        }
        if extra:
            record["extra"] = _redacted(dict(extra))
        path = _log_path()
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _WRITE_LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
            _maybe_rotate(path)
            # AFTER rotation, not before, and on every write rather than only on create. Rotation
            # replaces the inode, so chmod-then-rotate left the live file at the umask default
            # (0644 here) from the rotating call until whenever the next model call happened to land
            # -- a file of raw prompts, readable by every account on the box, for an unbounded window.
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
    except Exception:
        return  # debugging must never break a turn


def _maybe_rotate(path: Path) -> None:
    """Keep the newest ~4MB of captures so an always-on daemon cannot fill the disk.

    Same shape as ``core.routing_decision_log._maybe_rotate`` -- size trigger, keep the newest tail,
    atomic ``os.replace`` -- but the tail is budgeted in BYTES, not rows. A routing row is ~200 bytes
    and near-constant, so a fixed 1000-row keep bounds that file at ~200KB. A prompt record measured
    on this machine ranges 1.6KB to 49.6KB (a 31x spread, and a many-message turn can go higher
    still, since ``_MAX_CONTENT`` clips per message rather than per record), so a row count would
    bound nothing: 1000 rows at 47KB is 47MB.

    The 8MB trigger is deliberately 2x the 4MB keep budget. If they were close, every append past the
    line would re-read and re-write the whole file -- 11 ms measured -- instead of once per ~128
    captures (0.086 ms amortized). Bytes, not text: ``ensure_ascii=False`` writes non-ASCII raw, so a
    character budget would let a CJK prompt log occupy 3x its budget and rotate on every single call.
    """
    try:
        if path.stat().st_size <= _ROTATE_BYTES:
            return
        rows = [row for row in path.read_bytes().split(b"\n") if row]
        kept: list[bytes] = []
        total = 0
        for row in reversed(rows):
            total += len(row) + 1
            if total > _ROTATE_KEEP_BYTES and kept:
                break
            kept.append(row)
        kept.reverse()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(b"\n".join(kept) + b"\n")
        # The temp file holds the same prompts as the live one and is created at the umask default,
        # so it is tightened BEFORE it exists under any other name -- otherwise half the capture sits
        # world-readable next to the real file for the duration of the copy, and `os.replace` then
        # carries that mode onto the live path.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        pass  # debugging must never break a turn


def find_in_last_prompt(needle: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Search the most recent captured prompts for a string -- the "who told it that?" helper.

    Returns one entry per hit: the message index, its role, and the surrounding text.
    """
    hits: list[dict[str, Any]] = []
    try:
        path = _log_path()
        if not path.exists():
            return hits
        lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        for raw in reversed(lines[-limit:]):
            try:
                record = json.loads(raw)
            except Exception:
                continue
            messages = (record.get("payload") or {}).get("messages") or []
            for index, message in enumerate(messages):
                content = str(message.get("content") or "")
                position = content.lower().find(str(needle).lower())
                if position >= 0:
                    hits.append(
                        {
                            "ts": record.get("ts"),
                            "model": record.get("model"),
                            "message_index": index,
                            "role": message.get("role"),
                            "context": content[max(0, position - 120) : position + 160],
                        }
                    )
    except Exception:
        return hits
    return hits


__all__ = ["dump_outbound_prompt", "find_in_last_prompt", "prompt_debug_enabled"]
