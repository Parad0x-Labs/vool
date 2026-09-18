from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appeal_queue (
                appeal_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                appellant_peer_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def enqueue_appeal(task_id: str, appellant_peer_id: str, reason: str, evidence: dict) -> str:
    _init_table()
    appeal_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO appeal_queue (
                appeal_id, task_id, appellant_peer_id, reason, evidence_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (appeal_id, task_id, appellant_peer_id, reason, json.dumps(evidence, sort_keys=True), _utcnow(), _utcnow()),
        )
        conn.commit()
        return appeal_id
    finally:
        conn.close()


def list_pending_appeals() -> list[dict]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM appeal_queue WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
