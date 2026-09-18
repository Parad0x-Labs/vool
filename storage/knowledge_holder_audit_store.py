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
            CREATE TABLE IF NOT EXISTS knowledge_holder_audits (
                audit_id TEXT PRIMARY KEY,
                shard_id TEXT NOT NULL,
                holder_peer_id TEXT NOT NULL,
                requester_peer_id TEXT NOT NULL,
                trigger_reason TEXT NOT NULL,
                status TEXT NOT NULL,
                challenge_id TEXT,
                note TEXT,
                freshness_age_seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_holder_audits_holder ON knowledge_holder_audits(holder_peer_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_holder_audits_shard ON knowledge_holder_audits(shard_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def create_holder_audit(
    *,
    shard_id: str,
    holder_peer_id: str,
    requester_peer_id: str,
    trigger_reason: str,
    status: str,
    challenge_id: str | None = None,
    note: str | None = None,
    freshness_age_seconds: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    _init_table()
    audit_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO knowledge_holder_audits (
                audit_id, shard_id, holder_peer_id, requester_peer_id, trigger_reason,
                status, challenge_id, note, freshness_age_seconds, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                shard_id,
                holder_peer_id,
                requester_peer_id,
                trigger_reason,
                status,
                challenge_id,
                note,
                int(freshness_age_seconds),
                now,
                now,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        conn.commit()
        return audit_id
    finally:
        conn.close()


def update_holder_audit(
    audit_id: str,
    *,
    status: str,
    challenge_id: str | None = None,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _init_table()
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT metadata_json, challenge_id, note FROM knowledge_holder_audits WHERE audit_id = ? LIMIT 1",
            (audit_id,),
        ).fetchone()
        existing_metadata = json.loads(str(existing["metadata_json"]) or "{}") if existing else {}
        if metadata:
            existing_metadata.update(metadata)
        conn.execute(
            """
            UPDATE knowledge_holder_audits
            SET status = ?,
                challenge_id = COALESCE(?, challenge_id),
                note = COALESCE(?, note),
                updated_at = ?,
                metadata_json = ?
            WHERE audit_id = ?
            """,
            (
                status,
                challenge_id,
                note,
                _utcnow(),
                json.dumps(existing_metadata, sort_keys=True),
                audit_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def audits_for_holder(holder_peer_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_holder_audits
            WHERE holder_peer_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (holder_peer_id, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def latest_audit_for_challenge(challenge_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM knowledge_holder_audits
            WHERE challenge_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (challenge_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data
    finally:
        conn.close()
