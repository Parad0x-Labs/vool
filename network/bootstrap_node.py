from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from storage.db import get_connection


@dataclass(frozen=True)
class BootstrapPeer:
    peer_id: str
    host: str
    port: int
    transport_mode: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bootstrap_registry (
                peer_id TEXT PRIMARY KEY,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                transport_mode TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_bootstrap_peer(peer_id: str, host: str, port: int, transport_mode: str) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO bootstrap_registry (
                peer_id, host, port, transport_mode, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (peer_id, host, int(port), transport_mode, _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def list_bootstrap_peers(limit: int = 32) -> list[BootstrapPeer]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT peer_id, host, port, transport_mode FROM bootstrap_registry ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            BootstrapPeer(peer_id=row["peer_id"], host=row["host"], port=int(row["port"]), transport_mode=row["transport_mode"])
            for row in rows
        ]
    finally:
        conn.close()
