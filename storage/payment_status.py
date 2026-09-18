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
            CREATE TABLE IF NOT EXISTS payment_status_markers (
                task_or_transfer_id TEXT PRIMARY KEY,
                payer_peer_id TEXT NOT NULL,
                payee_peer_id TEXT NOT NULL,
                status TEXT NOT NULL,
                receipt_reference TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_payment_status_updated_at ON payment_status_markers(updated_at)"
        )
        conn.commit()
    finally:
        conn.close()


def upsert_payment_status(
    *,
    task_or_transfer_id: str,
    payer_peer_id: str,
    payee_peer_id: str,
    status: str,
    receipt_reference: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    _init_table()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO payment_status_markers (
                task_or_transfer_id, payer_peer_id, payee_peer_id, status,
                receipt_reference, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_or_transfer_id,
                payer_peer_id,
                payee_peer_id,
                status,
                receipt_reference,
                json.dumps(metadata or {}, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_payment_status(task_or_transfer_id: str) -> dict[str, Any] | None:
    _init_table()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM payment_status_markers
            WHERE task_or_transfer_id = ?
            LIMIT 1
            """,
            (task_or_transfer_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        return data
    finally:
        conn.close()


def list_payment_status(limit: int = 256) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM payment_status_markers
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
