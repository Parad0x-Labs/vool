from __future__ import annotations

import json
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
            CREATE TABLE IF NOT EXISTS meet_nodes (
                node_id TEXT PRIMARY KEY,
                base_url TEXT NOT NULL,
                region TEXT NOT NULL DEFAULT 'global',
                role TEXT NOT NULL DEFAULT 'seed',
                platform_hint TEXT NOT NULL DEFAULT 'unknown',
                priority INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_meet_nodes_status_priority ON meet_nodes(status, priority, updated_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meet_sync_state (
                remote_node_id TEXT PRIMARY KEY,
                last_snapshot_cursor TEXT,
                last_delta_cursor TEXT,
                last_sync_at TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_meet_node(
    *,
    node_id: str,
    base_url: str,
    region: str,
    role: str,
    platform_hint: str,
    priority: int,
    status: str = "active",
    metadata: dict[str, Any] | None = None,
    last_seen_at: str | None = None,
) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO meet_nodes (
                node_id, base_url, region, role, platform_hint, priority,
                status, metadata_json, last_seen_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT created_at FROM meet_nodes WHERE node_id = ?), ?),
                ?
            )
            """,
            (
                node_id,
                base_url.rstrip("/"),
                region,
                role,
                platform_hint,
                int(priority),
                status,
                json.dumps(metadata or {}, sort_keys=True),
                last_seen_at,
                node_id,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_meet_nodes(*, active_only: bool = True, limit: int = 256) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        if active_only:
            rows = conn.execute(
                """
                SELECT *
                FROM meet_nodes
                WHERE status = 'active'
                ORDER BY priority ASC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM meet_nodes
                ORDER BY priority ASC, updated_at DESC
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


def get_meet_node(node_id: str) -> dict[str, Any] | None:
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM meet_nodes
            WHERE node_id = ?
            LIMIT 1
            """,
            (node_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data
    finally:
        conn.close()


def upsert_sync_state(
    *,
    remote_node_id: str,
    last_snapshot_cursor: str | None = None,
    last_delta_cursor: str | None = None,
    last_sync_at: str | None = None,
    last_error: str | None = None,
) -> None:
    _init_tables()
    existing = get_sync_state(remote_node_id) or {}
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO meet_sync_state (
                remote_node_id, last_snapshot_cursor, last_delta_cursor,
                last_sync_at, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                remote_node_id,
                last_snapshot_cursor if last_snapshot_cursor is not None else existing.get("last_snapshot_cursor"),
                last_delta_cursor if last_delta_cursor is not None else existing.get("last_delta_cursor"),
                last_sync_at if last_sync_at is not None else existing.get("last_sync_at"),
                last_error,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_sync_state(remote_node_id: str) -> dict[str, Any] | None:
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM meet_sync_state
            WHERE remote_node_id = ?
            LIMIT 1
            """,
            (remote_node_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_sync_state(limit: int = 256) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM meet_sync_state
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
