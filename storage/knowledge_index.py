from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection
from storage.replica_table import all_holders


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_leases (
                peer_id TEXT PRIMARY KEY,
                agent_name TEXT,
                status TEXT NOT NULL,
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                home_region TEXT NOT NULL DEFAULT 'global',
                current_region TEXT,
                transport_mode TEXT NOT NULL DEFAULT 'lan_only',
                trust_score REAL NOT NULL DEFAULT 0.5,
                lease_expires_at TEXT NOT NULL,
                last_heartbeat_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_presence_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_leases_region ON presence_leases(home_region, current_region, lease_expires_at)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_tombstones (
                shard_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS index_deltas (
                delta_id TEXT PRIMARY KEY,
                peer_id TEXT,
                delta_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_presence_columns(conn: Any) -> None:
    columns = _table_columns(conn, "presence_leases")
    if "home_region" not in columns:
        conn.execute("ALTER TABLE presence_leases ADD COLUMN home_region TEXT NOT NULL DEFAULT 'global'")
    if "current_region" not in columns:
        conn.execute("ALTER TABLE presence_leases ADD COLUMN current_region TEXT")
    conn.commit()


def upsert_presence_lease(
    *,
    peer_id: str,
    agent_name: str | None,
    status: str,
    capabilities: list[str],
    home_region: str,
    current_region: str | None,
    transport_mode: str,
    trust_score: float,
    lease_expires_at: str,
    last_heartbeat_at: str,
) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO presence_leases (
                peer_id, agent_name, status, capabilities_json, home_region,
                current_region, transport_mode, trust_score, lease_expires_at,
                last_heartbeat_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                peer_id,
                agent_name,
                status,
                json.dumps(capabilities, sort_keys=True),
                home_region or "global",
                current_region or home_region or "global",
                transport_mode,
                float(trust_score),
                lease_expires_at,
                last_heartbeat_at,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def active_presence(now_iso: str | None = None, limit: int = 256) -> list[dict[str, Any]]:
    _init_tables()
    now_iso = now_iso or _utcnow()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM presence_leases
            WHERE status != 'offline'
              AND lease_expires_at > ?
            ORDER BY last_heartbeat_at DESC
            LIMIT ?
            """,
            (now_iso, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["capabilities"] = json.loads(data.pop("capabilities_json"))
            data["home_region"] = data.get("home_region") or "global"
            data["current_region"] = data.get("current_region") or data["home_region"]
            out.append(data)
        return out
    finally:
        conn.close()


def presence_for_peer(peer_id: str, *, now_iso: str | None = None) -> dict[str, Any] | None:
    _init_tables()
    now_iso = now_iso or _utcnow()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM presence_leases
            WHERE peer_id = ? AND lease_expires_at >= ?
            LIMIT 1
            """,
            (peer_id, now_iso),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["capabilities"] = json.loads(data.pop("capabilities_json"))
        data["home_region"] = data.get("home_region") or "global"
        data["current_region"] = data.get("current_region") or data["home_region"]
        return data
    finally:
        conn.close()


def prune_expired_presence(now_iso: str | None = None) -> int:
    _init_tables()
    now_iso = now_iso or _utcnow()
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM presence_leases WHERE lease_expires_at < ?",
            (now_iso,),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def withdraw_presence_lease(peer_id: str, *, withdrawn_at: str | None = None) -> None:
    _init_tables()
    withdrawn_at = withdrawn_at or _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE presence_leases
            SET status = 'offline',
                lease_expires_at = ?,
                updated_at = ?
            WHERE peer_id = ?
            """,
            (withdrawn_at, _utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_index_delta(delta_id: str, delta_type: str, payload: dict[str, Any], peer_id: str | None = None) -> None:
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO index_deltas (
                delta_id, peer_id, delta_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (delta_id, peer_id, delta_type, json.dumps(payload, sort_keys=True), _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def list_index_deltas(*, since_created_at: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        if since_created_at:
            rows = conn.execute(
                """
                SELECT *
                FROM index_deltas
                WHERE created_at > ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (since_created_at, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM (
                    SELECT *
                    FROM index_deltas
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC
                """,
                (limit,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def latest_index_cursor() -> str | None:
    _init_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT created_at
            FROM index_deltas
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        return str(row["created_at"]) if row and row["created_at"] else None
    finally:
        conn.close()


def add_tombstone(shard_id: str, content_hash: str, version: int, reason: str) -> None:
    """Record a knowledge withdrawal tombstone.

    A8 STRICT digest law: the tombstone never retains the withdrawn shard's
    content_hash — the erasure mechanism itself must not be an unsalted
    confirmation oracle. The hash argument is accepted for call-site
    compatibility and discarded.
    """
    _init_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_tombstones (
                shard_id, content_hash, version, reason, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (shard_id, "", int(version), reason, _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def swarm_knowledge_index(limit: int = 1000) -> list[dict[str, Any]]:
    _init_tables()
    holders = all_holders(limit=limit)
    grouped: dict[str, dict[str, Any]] = {}
    for row in holders:
        item = grouped.setdefault(
            row["shard_id"],
            {
                "shard_id": row["shard_id"],
                "content_hash": row["content_hash"],
                "version": row["version"],
                "latest_freshness": row["freshness_ts"],
                "replication_count": 0,
                "holders": [],
            },
        )
        item["replication_count"] += 1
        item["holders"].append(
            {
                "peer_id": row["holder_peer_id"],
                "trust_weight": row["trust_weight"],
                "fetch_route": row["fetch_route"],
                "status": row["status"],
            }
        )
        if row["version"] > item["version"]:
            item["version"] = row["version"]
        if row["freshness_ts"] > item["latest_freshness"]:
            item["latest_freshness"] = row["freshness_ts"]
    return sorted(grouped.values(), key=lambda item: (item["replication_count"], item["latest_freshness"]), reverse=True)
