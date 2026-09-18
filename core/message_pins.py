"""Per-chat pinned messages: save an answer in a long chat so you can find it again.

A pin is a snapshot of one message (role + text + when it was said) belonging to one chat session.
Pins live in ``<data>/message_pins.json`` keyed by session id, are owner-local, and never leave the
machine. A pin's id is derived from its (role, text) so pinning the same message twice is idempotent
(one pin, not two) and the UI can tell at a glance whether a message is already pinned. Storing the
text itself (not a transcript offset) means a pin still resolves after the log is trimmed.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from core.memory.files import utcnow
from core.runtime_paths import data_path

_PINS_FILE = "message_pins.json"
_PINS_LOCK = threading.Lock()
_TEXT_MAX = 20000        # a pinned snapshot is capped so one giant paste can't bloat the store
_PINS_PER_CHAT_MAX = 200  # a soft ceiling per chat; oldest drop out beyond it


def pins_path() -> Path:
    return data_path(_PINS_FILE)


def _pin_id(role: str, text: str) -> str:
    return hashlib.sha256((str(role) + "\x00" + str(text)).encode("utf-8")).hexdigest()[:16]


def _load() -> dict[str, list[dict[str, Any]]]:
    path = pins_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for sid, pins in data.items():
        if isinstance(pins, list):
            out[str(sid)] = [p for p in pins if isinstance(p, dict) and p.get("id") and p.get("text")]
    return out


def _save(store: dict[str, list[dict[str, Any]]]) -> None:
    path = pins_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({k: v for k, v in store.items() if v}, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):  # last-good backup
        path.with_name(path.name + ".bak").write_text(payload, encoding="utf-8")


def list_pins(session_id: str) -> list[dict[str, Any]]:
    """Pinned messages for one chat, newest-pinned first. Each: {id, role, text, ts, pinned_at}."""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    pins = list(_load().get(sid, []))
    pins.sort(key=lambda p: str(p.get("pinned_at") or ""), reverse=True)
    return pins


def list_servable_pins(session_id: str) -> list[dict[str, Any]]:
    """A8-gated pin serving: pins whose governing payload is WITHHELD/ERASED
    are suppressed — never served as verbatim bytes. Lineage (finalization_id)
    is authoritative; the content-hash verdict covers legacy unlineaged pins.
    Availability is re-verified per call (no read-once-trust)."""
    out: list[dict[str, Any]] = []
    for pin in list_pins(session_id):
        try:
            from core.finalization import (
                AVAILABILITY_ERASED,
                AVAILABILITY_WITHHELD,
                payload_availability_for_finalization_id,
                payload_availability_for_text,
            )

            fid = str(pin.get("finalization_id") or "").strip()
            verdict = (
                payload_availability_for_finalization_id(fid)
                if fid
                else payload_availability_for_text(str(pin.get("text") or ""))
            )
            if verdict in (AVAILABILITY_WITHHELD, AVAILABILITY_ERASED):
                continue
        except Exception:
            # PASS-002 fail-closed: when the governing store cannot be read,
            # uncertainty must never resolve into disclosure of governed
            # bytes. The pin is suppressed until the store answers again.
            continue
        out.append(pin)
    return out


def is_pinned(session_id: str, role: str, text: str) -> bool:
    pid = _pin_id(role, text)
    return any(p.get("id") == pid for p in _load().get(str(session_id or "").strip(), []))


def pin_message(session_id: str, role: str, text: str, ts: str = "") -> tuple[bool, dict[str, Any]]:
    """Pin one message to a chat (idempotent by content). Returns (True, pin) or (False, {} ) on bad input."""
    sid = str(session_id or "").strip()
    clean_text = str(text or "").strip()[:_TEXT_MAX]
    clean_role = "assistant" if str(role) == "assistant" else "user"
    if not sid or not clean_text:
        return False, {}
    pid = _pin_id(clean_role, clean_text)
    with _PINS_LOCK:
        store = _load()
        pins = store.get(sid, [])
        existing = next((p for p in pins if p.get("id") == pid), None)
        if existing:
            return True, existing  # already pinned -> no duplicate
        # A8 pass-001 payload lineage: an assistant pin carries the governing
        # finalization id (content-addressed lookup; '' when no committed A7
        # truth matches — legacy pins are honestly unlineaged, never stamped
        # with a fabricated id).
        finalization_id = ""
        if clean_role == "assistant":
            try:
                from core.finalization import get_finalization_by_content

                binding = get_finalization_by_content(clean_text)
                finalization_id = str((binding or {}).get("finalization_id") or "")
            except Exception:
                finalization_id = ""
        pin = {
            "id": pid,
            "role": clean_role,
            "text": clean_text,
            "ts": str(ts or ""),
            "pinned_at": utcnow(),
            "finalization_id": finalization_id,
        }
        pins.append(pin)
        if len(pins) > _PINS_PER_CHAT_MAX:  # drop the oldest-pinned beyond the ceiling
            pins.sort(key=lambda p: str(p.get("pinned_at") or ""))
            pins = pins[-_PINS_PER_CHAT_MAX:]
        store[sid] = pins
        _save(store)
    return True, pin


def unpin_message(session_id: str, pin_id: str) -> bool:
    """Remove one pin by id. Returns True if a pin was removed."""
    sid = str(session_id or "").strip()
    pid = str(pin_id or "").strip()
    if not sid or not pid:
        return False
    with _PINS_LOCK:
        store = _load()
        pins = store.get(sid, [])
        kept = [p for p in pins if p.get("id") != pid]
        if len(kept) == len(pins):
            return False
        store[sid] = kept
        _save(store)
    return True


def drop_session_pins(session_id: str) -> bool:
    """Forget all pins for a chat (called when the chat itself is deleted). True if any were removed."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _PINS_LOCK:
        store = _load()
        if sid not in store:
            return False
        del store[sid]
        _save(store)
    return True


__all__ = [
    "drop_session_pins",
    "is_pinned",
    "list_pins",
    "list_servable_pins",
    "pin_message",
    "pins_path",
    "unpin_message",
]
