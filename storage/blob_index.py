from __future__ import annotations

from datetime import datetime, timezone

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blob_index (
                blob_hash TEXT PRIMARY KEY,
                total_bytes INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL,
                manifest_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_blob(blob_hash: str, total_bytes: int, chunk_count: int, manifest_id: str) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO blob_index (
                blob_hash, total_bytes, chunk_count, manifest_id, created_at
            ) VALUES (
                ?, ?, ?, ?,
                COALESCE((SELECT created_at FROM blob_index WHERE blob_hash = ?), ?)
            )
            """,
            (blob_hash, total_bytes, chunk_count, manifest_id, blob_hash, _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def get_blob(blob_hash: str) -> dict | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM blob_index WHERE blob_hash = ? LIMIT 1",
            (blob_hash,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
