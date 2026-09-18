from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_media_evidence(
    *,
    task_id: str,
    trace_id: str,
    source_kind: str,
    source_domain: str,
    media_kind: str,
    reference: str,
    credibility_score: float,
    blocked: bool,
    metadata: dict[str, Any],
) -> str:
    entry_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO media_evidence_log (
                entry_id, task_id, trace_id, source_kind, source_domain, media_kind,
                reference, credibility_score, blocked, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                task_id,
                trace_id,
                source_kind,
                source_domain,
                media_kind,
                reference,
                float(credibility_score),
                int(bool(blocked)),
                json.dumps(metadata, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
        return entry_id
    finally:
        conn.close()


def recent_media_evidence(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM media_evidence_log
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_entry(dict(row)) for row in rows]
    finally:
        conn.close()


def _row_to_entry(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["blocked"] = bool(row["blocked"])
    return row
