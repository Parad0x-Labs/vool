from __future__ import annotations

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
            CREATE TABLE IF NOT EXISTS knowledge_possession_challenges (
                challenge_id TEXT PRIMARY KEY,
                shard_id TEXT NOT NULL,
                holder_peer_id TEXT NOT NULL,
                requester_peer_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                manifest_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                expected_chunk_hash TEXT NOT NULL,
                nonce TEXT NOT NULL,
                status TEXT NOT NULL,
                verification_note TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_possession_holder ON knowledge_possession_challenges(holder_peer_id, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_possession_requester ON knowledge_possession_challenges(requester_peer_id, created_at)"
        )
        conn.commit()
    finally:
        conn.close()


def insert_challenge(record: dict[str, Any]) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_possession_challenges (
                challenge_id, shard_id, holder_peer_id, requester_peer_id, content_hash, manifest_id,
                chunk_index, expected_chunk_hash, nonce, status, verification_note, created_at, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["challenge_id"],
                record["shard_id"],
                record["holder_peer_id"],
                record["requester_peer_id"],
                record["content_hash"],
                record["manifest_id"],
                int(record["chunk_index"]),
                record["expected_chunk_hash"],
                record["nonce"],
                record["status"],
                record.get("verification_note"),
                record["created_at"],
                record["expires_at"],
                record.get("updated_at") or _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_challenge(challenge_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM knowledge_possession_challenges WHERE challenge_id = ? LIMIT 1",
            (challenge_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_challenge_status(challenge_id: str, *, status: str, verification_note: str | None = None) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE knowledge_possession_challenges
            SET status = ?, verification_note = ?, updated_at = ?
            WHERE challenge_id = ?
            """,
            (status, verification_note, _utcnow(), challenge_id),
        )
        conn.commit()
    finally:
        conn.close()
