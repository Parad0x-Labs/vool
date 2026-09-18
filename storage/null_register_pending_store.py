"""
storage/null_register_pending_store.py
======================================
Isolated persistence for a *pending* in-chat ``.null`` registration.

This is deliberately a DEDICATED table, not the shared hive
``interaction_mode`` / ``interaction_payload`` fields. Money-flow state must never
race with, or be clobbered by, the hive nudge system, and a pending spend
confirmation must never leak into unrelated interaction state.

The row records only three things per session:
  * which ``.null`` name the user asked to register,
  * the previewed on-chain cost (lamports) at offer time, and
  * when the offer was staged (for a short TTL).

It authorizes NOTHING on its own. A staged row is merely a note that an offer was
shown; execution still requires the trusted spend gate AND a live OS-consent
(Windows Hello) prompt in :mod:`core.null_register_execute`. A stale or missing
row simply means "no pending offer" — it can never cause a spend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

# Default lifetime of a staged offer. A "yes" that arrives after this window does
# NOT execute — the user is asked to start the registration again. Short by design:
# a spend confirmation should be fresh, not resurrected from an old scrollback.
DEFAULT_PENDING_TTL_SECONDS = 600  # 10 minutes


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ensure_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS null_register_pending (
                session_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                cost_lamports INTEGER NOT NULL DEFAULT 0,
                owner_pubkey TEXT NOT NULL DEFAULT '',
                staged_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def stage_pending_registration(
    session_id: str,
    *,
    name: str,
    cost_lamports: int,
    owner_pubkey: str,
) -> None:
    """Record (or overwrite) the pending registration offer for a session.

    Overwrites any prior offer for the same session, so a fresh
    ``register other.null`` request always supersedes an older one.
    """
    sid = str(session_id or "").strip()
    clean_name = str(name or "").strip()
    if not sid or not clean_name:
        return
    _ensure_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO null_register_pending (session_id, name, cost_lamports, owner_pubkey, staged_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                name = excluded.name,
                cost_lamports = excluded.cost_lamports,
                owner_pubkey = excluded.owner_pubkey,
                staged_at = excluded.staged_at
            """,
            (sid, clean_name, max(0, int(cost_lamports)), str(owner_pubkey or ""), _utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def load_pending_registration(
    session_id: str,
    *,
    ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS,
) -> dict[str, Any] | None:
    """Return the fresh pending offer for a session, or None.

    Returns None (and clears the row) when the offer is older than ``ttl_seconds``,
    when the timestamp is unparseable, or when it is implausibly in the future
    (clock skew / tampering). Fail-safe: any ambiguity resolves to "no pending
    offer", which can only *prevent* a spend, never cause one.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id, name, cost_lamports, owner_pubkey, staged_at "
            "FROM null_register_pending WHERE session_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)

    staged_raw = str(data.get("staged_at") or "")
    try:
        staged = datetime.fromisoformat(staged_raw)
        if staged.tzinfo is None:
            staged = staged.replace(tzinfo=timezone.utc)
    except Exception:
        clear_pending_registration(sid)
        return None

    age_seconds = (_utcnow() - staged).total_seconds()
    # Expired, or a timestamp implausibly in the future (>60s skew) -> drop it.
    if age_seconds >= max(0, int(ttl_seconds)) or age_seconds < -60:
        clear_pending_registration(sid)
        return None

    return {
        "session_id": sid,
        "name": str(data.get("name") or ""),
        "cost_lamports": int(data.get("cost_lamports") or 0),
        "owner_pubkey": str(data.get("owner_pubkey") or ""),
        "staged_at": staged_raw,
    }


def clear_pending_registration(session_id: str) -> None:
    """Remove any pending offer for a session (idempotent)."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    _ensure_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM null_register_pending WHERE session_id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "DEFAULT_PENDING_TTL_SECONDS",
    "clear_pending_registration",
    "load_pending_registration",
    "stage_pending_registration",
]
