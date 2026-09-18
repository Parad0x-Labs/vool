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
            CREATE TABLE IF NOT EXISTS knowledge_holders (
                shard_id TEXT NOT NULL,
                holder_peer_id TEXT NOT NULL,
                home_region TEXT NOT NULL DEFAULT 'global',
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                freshness_ts TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                access_mode TEXT NOT NULL DEFAULT 'public',
                fetch_route_json TEXT NOT NULL DEFAULT '{}',
                trust_weight REAL NOT NULL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'advertised',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (shard_id, holder_peer_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_holders_holder_peer ON knowledge_holders(holder_peer_id)"
        )
        _ensure_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_holders_region ON knowledge_holders(home_region, status, updated_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_columns(conn: Any) -> None:
    columns = _table_columns(conn, "knowledge_holders")
    if "home_region" not in columns:
        conn.execute("ALTER TABLE knowledge_holders ADD COLUMN home_region TEXT NOT NULL DEFAULT 'global'")
    if "last_proved_at" not in columns:
        conn.execute("ALTER TABLE knowledge_holders ADD COLUMN last_proved_at TEXT")
    if "successful_audits" not in columns:
        conn.execute("ALTER TABLE knowledge_holders ADD COLUMN successful_audits INTEGER NOT NULL DEFAULT 0")
    if "failed_audits" not in columns:
        conn.execute("ALTER TABLE knowledge_holders ADD COLUMN failed_audits INTEGER NOT NULL DEFAULT 0")
    if "audit_state" not in columns:
        conn.execute("ALTER TABLE knowledge_holders ADD COLUMN audit_state TEXT NOT NULL DEFAULT 'unverified'")


def upsert_holder(
    *,
    shard_id: str,
    holder_peer_id: str,
    home_region: str,
    content_hash: str,
    version: int,
    freshness_ts: str,
    expires_at: str,
    access_mode: str,
    fetch_route: dict[str, Any],
    trust_weight: float,
    status: str = "active",
    source: str = "advertised",
) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_holders (
                shard_id, holder_peer_id, home_region, content_hash, version, freshness_ts,
                expires_at, access_mode, fetch_route_json, trust_weight, status, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shard_id,
                holder_peer_id,
                home_region or "global",
                content_hash,
                int(version),
                freshness_ts,
                expires_at,
                access_mode,
                json.dumps(fetch_route, sort_keys=True),
                float(trust_weight),
                status,
                source,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def mark_holder_withdrawn(shard_id: str, holder_peer_id: str, status: str = "withdrawn") -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE knowledge_holders
            SET status = ?, updated_at = ?
            WHERE shard_id = ? AND holder_peer_id = ?
            """,
            (status, _utcnow(), shard_id, holder_peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def holders_for_shard(shard_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        where = "WHERE shard_id = ?"
        params: list[Any] = [shard_id]
        if active_only:
            where += " AND status = 'active'"
        rows = conn.execute(
            f"""
            SELECT *
            FROM knowledge_holders
            {where}
            ORDER BY trust_weight DESC, version DESC, freshness_ts DESC
            """,
            tuple(params),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["fetch_route"] = json.loads(data.pop("fetch_route_json"))
            data["home_region"] = data.get("home_region") or "global"
            out.append(data)
        return out
    finally:
        conn.close()


def all_holders(limit: int = 1000) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM knowledge_holders
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["fetch_route"] = json.loads(data.pop("fetch_route_json"))
            data["home_region"] = data.get("home_region") or "global"
            out.append(data)
        return out
    finally:
        conn.close()


def prune_expired_holders(now_iso: str) -> int:
    _init_table()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE knowledge_holders
            SET status = 'expired', updated_at = ?
            WHERE expires_at < ? AND status = 'active'
            """,
            (_utcnow(), now_iso),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def mark_holder_audit_result(
    *,
    shard_id: str,
    holder_peer_id: str,
    passed: bool,
    proved_at: str | None = None,
) -> None:
    _init_table()
    conn = get_connection()
    try:
        if passed:
            conn.execute(
                """
                UPDATE knowledge_holders
                SET last_proved_at = ?,
                    successful_audits = COALESCE(successful_audits, 0) + 1,
                    audit_state = 'verified',
                    updated_at = ?
                WHERE shard_id = ? AND holder_peer_id = ?
                """,
                (proved_at or _utcnow(), _utcnow(), shard_id, holder_peer_id),
            )
        else:
            conn.execute(
                """
                UPDATE knowledge_holders
                SET failed_audits = COALESCE(failed_audits, 0) + 1,
                    audit_state = 'suspect',
                    updated_at = ?
                WHERE shard_id = ? AND holder_peer_id = ?
                """,
                (_utcnow(), shard_id, holder_peer_id),
            )
        conn.commit()
    finally:
        conn.close()
