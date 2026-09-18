from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shard_fetch_receipts (
                receipt_id TEXT PRIMARY KEY,
                query_id TEXT,
                shard_id TEXT NOT NULL,
                source_peer_id TEXT NOT NULL,
                source_node_id TEXT,
                manifest_id TEXT,
                content_hash TEXT,
                version INTEGER,
                summary_digest TEXT,
                validation_state TEXT NOT NULL,
                accepted INTEGER NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_shard_fetch_receipts_shard
            ON shard_fetch_receipts(shard_id, created_at DESC)
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_fetch_receipt(
    *,
    shard_id: str,
    source_peer_id: str,
    source_node_id: str | None,
    query_id: str | None,
    manifest_id: str | None,
    content_hash: str | None,
    version: int | None,
    summary_digest: str | None,
    validation_state: str,
    accepted: bool,
    details: dict[str, Any] | None = None,
) -> str:
    _init_table()
    receipt_id = f"receipt-{uuid.uuid4().hex}"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO shard_fetch_receipts (
                receipt_id, query_id, shard_id, source_peer_id, source_node_id,
                manifest_id, content_hash, version, summary_digest,
                validation_state, accepted, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                str(query_id or "").strip() or None,
                str(shard_id or "").strip(),
                str(source_peer_id or "").strip(),
                str(source_node_id or "").strip() or None,
                str(manifest_id or "").strip() or None,
                str(content_hash or "").strip() or None,
                None if version in {None, ""} else int(version),
                str(summary_digest or "").strip() or None,
                str(validation_state or "unknown").strip() or "unknown",
                1 if accepted else 0,
                json.dumps(details or {}, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
        return receipt_id
    finally:
        conn.close()


def latest_receipt_for_shard(shard_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM shard_fetch_receipts
            WHERE shard_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(shard_id or "").strip(),),
        ).fetchone()
        return _row_to_receipt(dict(row)) if row else None
    finally:
        conn.close()


def latest_receipts_for_shards(shard_ids: list[str]) -> dict[str, dict[str, Any]]:
    cleaned = [str(item or "").strip() for item in list(shard_ids or []) if str(item or "").strip()]
    if not cleaned:
        return {}
    _init_table()
    conn = get_connection()
    try:
        placeholders = ", ".join("?" for _ in cleaned)
        rows = conn.execute(
            f"""
            SELECT *
            FROM shard_fetch_receipts
            WHERE shard_id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            tuple(cleaned),
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = _row_to_receipt(dict(row))
        shard_id = str(data.get("shard_id") or "").strip()
        if shard_id and shard_id not in out:
            out[shard_id] = data
    return out


def _row_to_receipt(row: dict[str, Any]) -> dict[str, Any]:
    row["details"] = json.loads(row.pop("details_json") or "{}")
    row["accepted"] = bool(row.get("accepted"))
    return row
