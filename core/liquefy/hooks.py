"""Event-store hooks: the smallest real producers→liquefy wiring.

Law: the liquefy log is an additive, compressed, searchable PROJECTION of real
VOOL logs — never their authority. A producer's own journal (Blackbox journal,
receipts, bug reports, activity ledger) stays the original recovery path; a
projection failure must never break the producer, and a projection entry must
still resolve for every reference the producer retained (dossier §36.12/§36.15).

Default state: ON (Aug-28 scratch-proof recommendation, adopted by the dossier:
structured journal default-ON), root under ``data_path("liquefy_logs")``,
overridable with ``VOOL_LIQUEFY_LOGS_HOME``; ``VOOL_LIQUEFY_LOGS=0`` disables.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from core.liquefy.store import LiquefyLogStore

_STORE: LiquefyLogStore | None = None
_STORE_LOCK = threading.RLock()
_DISABLED = False
_FAILURES = 0


def enabled() -> bool:
    return not _DISABLED and os.environ.get("VOOL_LIQUEFY_LOGS", "1") not in ("0", "false", "no")


def default_root() -> str:
    override = str(os.environ.get("VOOL_LIQUEFY_LOGS_HOME") or "").strip()
    if override:
        return override
    from core.runtime_paths import data_path

    return str(data_path("liquefy_logs"))


def get_default_store() -> LiquefyLogStore | None:
    """Process-wide projection store; None when disabled or unwritable."""
    global _STORE, _DISABLED
    if not enabled():
        return None
    with _STORE_LOCK:
        if _STORE is None:
            try:
                _STORE = LiquefyLogStore(default_root())
            except OSError:
                _DISABLED = True
                return None
        return _STORE


def reset_default_store() -> None:
    global _STORE, _DISABLED, _FAILURES
    with _STORE_LOCK:
        _STORE = None
        _DISABLED = False
        _FAILURES = 0


def record_event(
    *,
    kind: str,
    source: str,
    payload: dict[str, Any],
    ref: dict[str, Any] | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    ts: str | None = None,
) -> int | None:
    """Ingest one event into the projection. Never raises: the producer's own
    log remains the authority; failures are counted, not propagated."""
    global _FAILURES
    store = get_default_store()
    if store is None:
        return None
    try:
        return store.append(
            [
                {
                    "kind": kind,
                    "source": source,
                    "payload": payload,
                    "ref": ref,
                    "session_id": session_id,
                    "trace_id": trace_id,
                    "ts": ts,
                }
            ]
        )[0]
    except Exception:
        _FAILURES += 1
        return None


def blackbox_entry_to_event(entry: dict[str, Any]) -> dict[str, Any]:
    """The one journal-entry → projection-event mapping (pure).

    ``record_blackbox_entry`` delivers it through the live sink; the operator
    rebuild reads the SAME mapping over the authoritative journal so a rebuilt
    projection is event-for-event the one the sink would have built.
    """
    ref = {
        "event_id": entry.get("effect_id") or entry.get("entry_hash"),
        "turn_id": entry.get("turn_id"),
        "blackbox_seq": entry.get("seq"),
        "effect_id": entry.get("effect_id"),
    }
    return {
        "kind": str(entry.get("kind") or "blackbox_entry"),
        "source": "blackbox",
        "payload": entry,
        "ref": {k: v for k, v in ref.items() if v is not None},
        "session_id": str(entry.get("session_id") or "") or None,
        "trace_id": str(entry.get("trace_id") or "") or None,
        "ts": entry.get("ts"),
    }


def record_blackbox_entry(entry: dict[str, Any]) -> int | None:
    """Adapter for Blackbox journal entries (post-append hook in BlackboxStore).

    The entry is journaled wholesale minus nothing: journal lines are compact
    (before/after BYTES live in the blob store, not the line), so the compressed
    projection carries the full effect truth and the original Blackbox journal
    stays byte-for-byte untouched."""
    return record_event(**blackbox_entry_to_event(entry))


def retrieve_reference(
    *,
    event_id: str | None = None,
    turn_id: str | None = None,
    seq: int | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    requester: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a consumer-retained reference against the projection. Cold
    retrievals verify both hashes before serving and write a restore receipt."""
    store = get_default_store()
    if store is None:
        return None
    try:
        if seq is not None:
            return store.read(int(seq), requester=requester)
        hits = store.find(
            event_id=event_id,
            turn_id=turn_id,
            session_id=session_id,
            trace_id=trace_id,
            limit=1,
        )
        return hits[0] if hits else None
    except Exception:
        return None


def failure_count() -> int:
    return _FAILURES


__all__ = [
    "blackbox_entry_to_event",
    "default_root",
    "enabled",
    "failure_count",
    "get_default_store",
    "record_blackbox_entry",
    "record_event",
    "reset_default_store",
    "retrieve_reference",
]
