from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_revocations (
                peer_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'all',
                reason TEXT NOT NULL,
                revoked_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_by_peer_id TEXT,
                replacement_peer_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (peer_id, scope)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_identity_revocations_updated ON identity_revocations(updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_key_history (
                history_id TEXT PRIMARY KEY,
                peer_id TEXT NOT NULL,
                key_path TEXT,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_identity_key_history_peer ON identity_key_history(peer_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def record_identity_key(
    *,
    peer_id: str,
    key_path: str | None,
    state: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    _init_tables()
    history_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO identity_key_history (
                history_id, peer_id, key_path, state, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                history_id,
                peer_id,
                key_path,
                state,
                _utcnow(),
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        conn.commit()
        return history_id
    finally:
        conn.close()


def list_identity_key_history(*, limit: int = 200) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM identity_key_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def upsert_identity_revocation(
    *,
    peer_id: str,
    scope: str,
    reason: str,
    revoked_at: str,
    expires_at: str | None,
    revoked_by_peer_id: str | None,
    replacement_peer_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO identity_revocations (
                peer_id, scope, reason, revoked_at, expires_at,
                revoked_by_peer_id, replacement_peer_id, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                peer_id,
                scope,
                reason,
                revoked_at,
                expires_at,
                revoked_by_peer_id,
                replacement_peer_id,
                json.dumps(metadata or {}, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_identity_revocation(peer_id: str, *, scope: str) -> dict[str, Any] | None:
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM identity_revocations
            WHERE peer_id = ? AND scope = ?
            LIMIT 1
            """,
            (peer_id, scope),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data
    finally:
        conn.close()


def list_identity_revocations(*, limit: int = 200) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM identity_revocations
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def delete_identity_revocation(peer_id: str, *, scope: str) -> int:
    _init_tables()
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM identity_revocations WHERE peer_id = ? AND scope = ?",
            (peer_id, scope),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()
