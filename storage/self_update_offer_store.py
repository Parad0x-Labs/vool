"""
storage/self_update_offer_store.py
==================================
Per-session marker for "an update offer was shown in this chat session," so a bare
'yes'/'no' can be understood as responding to it, and so the offer isn't re-shown on
every turn. Dedicated + isolated (not the shared hive interaction state) so it can't
race with hive nudges.

Holds only (session_id -> offered target_version + when). Authorizes nothing; the
actual update still goes through the verified, OS-independent updater.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

DEFAULT_OFFER_TTL_SECONDS = 2700  # 45 min: a fresh offer, not resurrected scrollback


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _ensure_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_update_offer (
                session_id TEXT PRIMARY KEY,
                target_version TEXT NOT NULL,
                offered_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def mark_offered(session_id: str, target_version: str) -> None:
    sid = str(session_id or "").strip()
    ver = str(target_version or "").strip()
    if not sid or not ver:
        return
    _ensure_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO self_update_offer (session_id, target_version, offered_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                target_version = excluded.target_version,
                offered_at = excluded.offered_at
            """,
            (sid, ver, _utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def load_offered(session_id: str, *, ttl_seconds: int = DEFAULT_OFFER_TTL_SECONDS) -> dict[str, Any] | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    _ensure_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id, target_version, offered_at FROM self_update_offer WHERE session_id = ? LIMIT 1",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    raw = str(data.get("offered_at") or "")
    try:
        offered = datetime.fromisoformat(raw)
        if offered.tzinfo is None:
            offered = offered.replace(tzinfo=timezone.utc)
    except Exception:
        clear_offered(sid)
        return None
    age = (_utcnow() - offered).total_seconds()
    if age >= max(0, int(ttl_seconds)) or age < -60:
        clear_offered(sid)
        return None
    return {"session_id": sid, "target_version": str(data.get("target_version") or "")}


def clear_offered(session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    _ensure_table()
    conn = get_connection()
    try:
        conn.execute("DELETE FROM self_update_offer WHERE session_id = ?", (sid,))
        conn.commit()
    finally:
        conn.close()


__all__ = ["DEFAULT_OFFER_TTL_SECONDS", "clear_offered", "load_offered", "mark_offered"]
