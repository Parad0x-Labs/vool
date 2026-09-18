"""
core/web0_mesh_registry.py
==========================
Web0 worker registry backed by SQLite.

Workers POST to /v1/workers/announce on boot and periodically re-announce
(TTL=300s).  Rows survive restarts; expired rows are filtered on read and
pruned by evict_expired().
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection

_WORKER_TTL_SECONDS = 300  # workers must re-announce within 5 min


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: tuple) -> dict[str, Any]:
    (
        worker_id,
        provider_ids_json,
        top_tps,
        top_tier,
        context_window,
        tools_json,
        price_per_token_usdc,
        privacy_mode,
        announced_at,
        expires_at,
        _updated_at,
    ) = row
    return {
        "worker_id": worker_id,
        "provider_ids": json.loads(provider_ids_json or "[]"),
        "top_tps": top_tps,
        "top_tier": top_tier,
        "context_window": context_window,
        "tools": json.loads(tools_json or "[]"),
        "price_per_token_usdc": price_per_token_usdc,
        "privacy_mode": privacy_mode,
        "announced_at": announced_at,
        "expires_at": expires_at,
        "active": expires_at > time.time(),
    }


def announce_worker(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert a worker announcement. Returns {ok, worker_id, expires_at}."""
    worker_id = str(payload.get("worker_id") or "").strip()
    if not worker_id:
        return {"ok": False, "error": "worker_id required"}

    now = time.time()
    expires_at = now + _WORKER_TTL_SECONDS

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO web0_workers
            (worker_id, provider_ids_json, top_tps, top_tier, context_window,
             tools_json, price_per_token_usdc, privacy_mode, announced_at, expires_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker_id) DO UPDATE SET
            provider_ids_json     = excluded.provider_ids_json,
            top_tps               = excluded.top_tps,
            top_tier              = excluded.top_tier,
            context_window        = excluded.context_window,
            tools_json            = excluded.tools_json,
            price_per_token_usdc  = excluded.price_per_token_usdc,
            privacy_mode          = excluded.privacy_mode,
            announced_at          = excluded.announced_at,
            expires_at            = excluded.expires_at,
            updated_at            = excluded.updated_at
        """,
        (
            worker_id,
            json.dumps([str(p) for p in list(payload.get("provider_ids") or [])]),
            float(payload.get("top_tps") or 0.0),
            str(payload.get("top_tier") or "drone"),
            int(payload.get("context_window") or 32768),
            json.dumps([str(t) for t in list(payload.get("tools") or [])]),
            float(payload.get("price_per_token_usdc") or 0.000001),
            str(payload.get("privacy_mode") or "plain"),
            float(payload.get("announced_at") or now),
            expires_at,
            _utcnow(),
        ),
    )
    conn.commit()

    return {
        "ok": True,
        "worker_id": worker_id,
        "expires_at": expires_at,
        "ttl_seconds": _WORKER_TTL_SECONDS,
    }


def list_workers(*, active_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
    """Return workers sorted by TPS descending."""
    conn = get_connection()
    if active_only:
        rows = conn.execute(
            "SELECT * FROM web0_workers WHERE expires_at > ? ORDER BY top_tps DESC LIMIT ?",
            (time.time(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM web0_workers ORDER BY top_tps DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_worker(worker_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web0_workers WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def evict_expired() -> int:
    """Delete rows whose TTL has lapsed. Returns count removed."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM web0_workers WHERE expires_at <= ?",
        (time.time(),),
    )
    conn.commit()
    return cur.rowcount


__all__ = [
    "announce_worker",
    "evict_expired",
    "get_worker",
    "list_workers",
]
