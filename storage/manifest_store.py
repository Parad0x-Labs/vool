from __future__ import annotations

import json
from datetime import datetime, timezone

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manifest_store (
                manifest_id TEXT PRIMARY KEY,
                blob_hash TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def save_manifest(manifest_id: str, blob_hash: str, manifest: dict) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO manifest_store (
                manifest_id, blob_hash, manifest_json, created_at
            ) VALUES (
                ?, ?, ?,
                COALESCE((SELECT created_at FROM manifest_store WHERE manifest_id = ?), ?)
            )
            """,
            (
                manifest_id,
                blob_hash,
                json.dumps(manifest, sort_keys=True),
                manifest_id,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def load_manifest(manifest_id: str) -> dict | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT manifest_json FROM manifest_store WHERE manifest_id = ? LIMIT 1",
            (manifest_id,),
        ).fetchone()
        return json.loads(row["manifest_json"]) if row else None
    finally:
        conn.close()
