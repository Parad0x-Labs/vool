"""
storage/task_offer_store.py
===========================
Thin SQLite wrapper over the existing `task_offers` table (defined in migrations.py).
Provides the read/write operations needed by the task market routes and the
Web0 worker bid/claim/complete loop.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _summary_servable(text: str) -> bool:
    """A8 serve-time gate over task-offer briefs: a governed WITHHELD/ERASED
    payload (or a bound derivative quote inside the brief) never serves.
    Hosted-store failure fails closed; a proven-absent store is legacy."""
    from core.finalization import writer_may_persist_text

    return writer_may_persist_text(str(text or ""))


def list_open_task_offers(*, limit: int = 50) -> list[dict[str, Any]]:
    """Return open task offers ordered by priority + deadline."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, parent_peer_id, task_type, subtask_type, summary,
                   required_capabilities_json, reward_hint_json,
                   max_helpers, priority, deadline_ts, status, created_at
            FROM task_offers
            WHERE status = 'open'
            ORDER BY
                CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END,
                deadline_ts ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        d = dict(row)
        for key in ("required_capabilities_json", "reward_hint_json"):
            raw = d.pop(key, None)
            parsed_key = key.removesuffix("_json")
            try:
                d[parsed_key] = json.loads(raw) if raw else {}
            except Exception:
                d[parsed_key] = {}
        # A8 serve gate: an offer whose brief carries governed bytes is
        # filtered from the served queue (the offer IS its brief).
        if not _summary_servable(str(d.get("summary") or "")):
            continue
        result.append(d)
    return result


def claim_task_offer(task_id: str, helper_peer_id: str) -> bool:
    """
    Atomically mark an open offer as claimed by helper_peer_id.
    The claimant identity is persisted in ``claimed_by`` so that only the
    actual claimer can later collect the escrow payout (see
    :func:`get_task_offer_claimed_by`).
    Returns True if the offer was successfully claimed (was 'open').
    Returns False if already claimed/completed or not found.
    """
    now = _utcnow_iso()
    claimant = str(helper_peer_id or "")
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE task_offers
            SET status = 'claimed', claimed_by = ?, updated_at = ?
            WHERE task_id = ? AND status = 'open'
            """,
            (claimant, now, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_task_offer_claimed_by(task_id: str) -> str | None:
    """
    Return the peer id recorded as the claimant for ``task_id``.

    Returns ``None`` when the offer does not exist or predates the
    ``claimed_by`` column (legacy rows store an empty string); the empty
    string is also normalized to ``None`` so callers can treat "unknown
    claimant" uniformly.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT claimed_by FROM task_offers WHERE task_id = ?", (task_id,)
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    claimed_by = str(row["claimed_by"] or "").strip()
    return claimed_by or None


def complete_task_offer(task_id: str, result_hash: str) -> bool:
    """Mark a claimed offer as completed."""
    now = _utcnow_iso()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE task_offers
            SET status = 'completed', updated_at = ?
            WHERE task_id = ? AND status = 'claimed'
            """,
            (now, task_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_task_offer(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    for key in ("required_capabilities_json", "reward_hint_json"):
        raw = d.pop(key, None)
        parsed_key = key.removesuffix("_json")
        try:
            d[parsed_key] = json.loads(raw) if raw else {}
        except Exception:
            d[parsed_key] = {}
    # A8 serve gate (denormalized-column law): a governed brief is suppressed
    # at serve time — the id-addressed read keeps its shape, never its bytes.
    if not _summary_servable(str(d.get("summary") or "")):
        d["summary"] = ""
    return d


__all__ = [
    "claim_task_offer",
    "complete_task_offer",
    "get_task_offer",
    "get_task_offer_claimed_by",
    "list_open_task_offers",
]
