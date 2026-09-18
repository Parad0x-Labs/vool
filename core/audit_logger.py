from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from storage.db import active_default_db_path, execute_query, get_connection
from storage.event_log import append_event

# --- Schema setup once-flag (task #21) -------------------------------------
# The audit_log DDL (CREATE TABLE / CREATE INDEX + commit) is idempotent but
# previously ran on every single log() call (~182 call-sites). Guard it behind
# a per-process once-flag so the schema setup runs a single time per process
# (re-running only if the active default DB path changes, which happens under
# test isolation). Double-checked locking keeps it correct on first use and
# across threads.
_SCHEMA_LOCK = threading.Lock()
_schema_ready_for_path: str | None = None


def _create_audit_log_schema() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type)"
        )
        conn.commit()
    finally:
        conn.close()


def _ensure_audit_log_table() -> None:
    """Run the audit_log schema setup once per process (per active DB path).

    Thread-safe via double-checked locking: the common case is a cheap string
    comparison with no lock; the DDL only executes the first time (or after the
    default DB path is reconfigured, e.g. test isolation).
    """
    global _schema_ready_for_path
    active_path = active_default_db_path()
    if _schema_ready_for_path == active_path:
        return
    with _SCHEMA_LOCK:
        if _schema_ready_for_path == active_path:
            return
        _create_audit_log_schema()
        _schema_ready_for_path = active_path


def reset_schema_ready_flag() -> None:
    """Drop the cached schema once-flag (forces re-setup on next log).

    Exposed for tests / runtime resets that swap the underlying DB out from
    under the process.
    """
    global _schema_ready_for_path
    with _SCHEMA_LOCK:
        _schema_ready_for_path = None


# --- Peer ip:port / peer-id redaction (tasks #28 / #30) --------------------
# Raw peer ip:port and long peer ids must not land in the durable audit_log or
# stdout. We salt-hash ip:port to a short, stable token and truncate long peer
# ids. Everything is local + deterministic *per process*: the salt is generated
# once at import, never leaves the process, and is not persisted, so a token
# cannot be reproduced or correlated across restarts/peers while the same input
# still maps to the same token within a run (useful for grouping log lines).
_ADDR_SALT = os.urandom(32)
_ADDR_TOKEN_PREFIX = "addr:"
_ADDR_TOKEN_HEX_LEN = 12  # 48 bits of the digest — short but collision-safe here

# IPv4 / bracketed-IPv6 / hostname followed by :port. Anchored to word-ish
# boundaries so compound target_id strings ("a:1 (public b:2)") redact each
# address independently without mangling surrounding text.
_ADDR_RE = re.compile(
    r"(?<![\w.:\-])"
    r"(?:\[[0-9A-Fa-f:]+\]|\d{1,3}(?:\.\d{1,3}){3}|[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?)"
    r":\d{1,5}"
    r"(?![\w.])"
)

# Keys inside a details dict whose values are peer ids and should be truncated.
_PEER_ID_KEYS = frozenset(
    {
        "peer_id",
        "helper_peer_id",
        "parent_peer_id",
        "reviewer_peer_id",
        "requester_peer_id",
        "holder_peer_id",
        "sender_peer_id",
        "target_peer_id",
        "from_peer_id",
        "to_peer_id",
    }
)

# Keys inside a details dict whose values hold a raw network address. Only these
# get ip:port salt-hashing — applying the address regex to arbitrary free-text
# detail values would risk redacting unrelated ``word:number`` strings.
_ADDR_KEYS = frozenset(
    {
        "addr",
        "address",
        "peer_addr",
        "peer_address",
        "endpoint",
        "peer_endpoint",
        "remote_addr",
        "remote_address",
        "ip_port",
        "host_port",
    }
)

_PEER_ID_KEEP = 12  # leading chars kept verbatim from a long peer id


def redact_addr(value: str) -> str:
    """Salt-hash a raw ``ip:port`` (or ``host:port``) to a short stable token.

    Returns ``addr:<12 hex chars>``. Deterministic per process; the raw address
    is never recoverable from the token.
    """
    digest = hashlib.sha256(_ADDR_SALT + str(value).encode("utf-8")).hexdigest()
    return _ADDR_TOKEN_PREFIX + digest[:_ADDR_TOKEN_HEX_LEN]


def redact_addrs_in_text(text: str) -> str:
    """Replace every ``ip:port`` substring in ``text`` with a redacted token."""
    return _ADDR_RE.sub(lambda m: redact_addr(m.group(0)), text)


def truncate_peer_id(value: str, *, keep: int = _PEER_ID_KEEP) -> str:
    """Truncate a long peer id for hot-path prints / durable storage.

    Short ids pass through unchanged. Longer ids keep a deterministic ``keep``
    char prefix plus a length marker so distinct ids stay distinguishable in
    logs without persisting the full identifier: ``<prefix>…(<len>)``.
    """
    text = str(value)
    if len(text) <= keep:
        return text
    return f"{text[:keep]}…({len(text)})"


def _redact_target_id(target_id: str | None) -> str | None:
    if target_id is None:
        return None
    return redact_addrs_in_text(str(target_id))


def _redact_details(details: dict | None) -> dict:
    if not details:
        return {}
    redacted: dict = {}
    for key, raw in details.items():
        if isinstance(raw, str):
            if key in _PEER_ID_KEYS:
                redacted[key] = truncate_peer_id(raw)
            elif key in _ADDR_KEYS:
                redacted[key] = redact_addrs_in_text(raw)
            else:
                redacted[key] = raw
        else:
            redacted[key] = raw
    return redacted


def log(
    event_type: str,
    target_id: str | None = None,
    details: dict | None = None,
    *,
    actor: str = "system",
    target_type: str = "generic",
    trace_id: str | None = None,
) -> None:
    event_id = str(uuid.uuid4())
    safe_target_id = _redact_target_id(target_id)
    payload = _redact_details(details)
    _ensure_audit_log_table()
    execute_query(
        """
        INSERT INTO audit_log (
            event_id, event_type, actor, target_type, target_id, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            actor,
            target_type,
            safe_target_id,
            json.dumps(payload, sort_keys=True),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    append_event(
        category=event_type,
        actor=actor,
        target_type=target_type,
        target_id=safe_target_id,
        payload=payload,
        trace_id=trace_id,
        event_id=event_id,
    )
