from __future__ import annotations

import json
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
            CREATE TABLE IF NOT EXISTS knowledge_manifests (
                manifest_id TEXT PRIMARY KEY,
                shard_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                topic_tags_json TEXT NOT NULL,
                summary_digest TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_manifests_shard_id ON knowledge_manifests(shard_id)"
        )
        conn.commit()
    finally:
        conn.close()


def upsert_manifest(
    *,
    manifest_id: str,
    shard_id: str,
    content_hash: str,
    version: int,
    topic_tags: list[str],
    summary_digest: str,
    size_bytes: int,
    metadata: dict[str, Any],
) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_manifests (
                manifest_id, shard_id, content_hash, version, topic_tags_json,
                summary_digest, size_bytes, metadata_json, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT created_at FROM knowledge_manifests WHERE manifest_id = ?), ?),
                ?
            )
            """,
            (
                manifest_id,
                shard_id,
                content_hash,
                int(version),
                json.dumps(topic_tags, sort_keys=True),
                summary_digest,
                int(size_bytes),
                json.dumps(metadata, sort_keys=True),
                manifest_id,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def manifest_for_shard(shard_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM knowledge_manifests
            WHERE shard_id = ?
            ORDER BY version DESC, updated_at DESC
            LIMIT 1
            """,
            (shard_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["topic_tags"] = json.loads(data.pop("topic_tags_json"))
        data["metadata"] = json.loads(data.pop("metadata_json"))
        return data
    finally:
        conn.close()


def all_manifests(limit: int = 500) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_manifests
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["topic_tags"] = json.loads(data.pop("topic_tags_json"))
            data["metadata"] = json.loads(data.pop("metadata_json"))
            out.append(data)
        return out
    finally:
        conn.close()
