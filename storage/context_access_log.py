from __future__ import annotations

import json
import uuid
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
            CREATE TABLE IF NOT EXISTS context_access_log (
                log_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                total_context_budget INTEGER NOT NULL,
                bootstrap_tokens_used INTEGER NOT NULL DEFAULT 0,
                relevant_tokens_used INTEGER NOT NULL DEFAULT 0,
                cold_tokens_used INTEGER NOT NULL DEFAULT 0,
                retrieval_confidence TEXT NOT NULL DEFAULT 'low',
                swarm_metadata_consulted INTEGER NOT NULL DEFAULT 0,
                cold_archive_opened INTEGER NOT NULL DEFAULT 0,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_access_log_created ON context_access_log(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_context_access_log_task ON context_access_log(task_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def record_context_access(report: dict[str, Any]) -> str:
    _init_table()
    log_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO context_access_log (
                log_id, task_id, trace_id, total_context_budget,
                bootstrap_tokens_used, relevant_tokens_used, cold_tokens_used,
                retrieval_confidence, swarm_metadata_consulted, cold_archive_opened,
                report_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                str(report.get("task_id") or ""),
                str(report.get("trace_id") or ""),
                int(report.get("total_context_budget") or 0),
                int(report.get("bootstrap_tokens_used") or 0),
                int(report.get("relevant_tokens_used") or 0),
                int(report.get("cold_tokens_used") or 0),
                str(report.get("retrieval_confidence") or "low"),
                1 if report.get("swarm_metadata_consulted") else 0,
                1 if report.get("cold_archive_opened") else 0,
                json.dumps(report, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
        return log_id
    finally:
        conn.close()


def recent_context_access(limit: int = 20) -> list[dict[str, Any]]:
    _init_table()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM context_access_log
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["report"] = json.loads(data.pop("report_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def context_access_summary(limit: int = 200) -> dict[str, Any]:
    rows = recent_context_access(limit=limit)
    total = len(rows)
    if total == 0:
        return {
            "entries": 0,
            "avg_total_budget": 0.0,
            "avg_tokens_used": 0.0,
            "avg_bootstrap_tokens": 0.0,
            "avg_relevant_tokens": 0.0,
            "avg_cold_tokens": 0.0,
            "swarm_consult_rate": 0.0,
            "cold_open_rate": 0.0,
            "retrieval_confidence_breakdown": {},
            "recent": [],
        }

    confidence_breakdown: dict[str, int] = {}
    for row in rows:
        confidence = str(row.get("retrieval_confidence") or "low")
        confidence_breakdown[confidence] = confidence_breakdown.get(confidence, 0) + 1

    return {
        "entries": total,
        "avg_total_budget": sum(int(row.get("total_context_budget") or 0) for row in rows) / total,
        "avg_tokens_used": sum(
            int(row.get("bootstrap_tokens_used") or 0)
            + int(row.get("relevant_tokens_used") or 0)
            + int(row.get("cold_tokens_used") or 0)
            for row in rows
        ) / total,
        "avg_bootstrap_tokens": sum(int(row.get("bootstrap_tokens_used") or 0) for row in rows) / total,
        "avg_relevant_tokens": sum(int(row.get("relevant_tokens_used") or 0) for row in rows) / total,
        "avg_cold_tokens": sum(int(row.get("cold_tokens_used") or 0) for row in rows) / total,
        "swarm_consult_rate": sum(int(row.get("swarm_metadata_consulted") or 0) for row in rows) / total,
        "cold_open_rate": sum(int(row.get("cold_archive_opened") or 0) for row in rows) / total,
        "retrieval_confidence_breakdown": confidence_breakdown,
        "recent": [
            {
                "task_id": row.get("task_id"),
                "trace_id": row.get("trace_id"),
                "created_at": row.get("created_at"),
                "retrieval_confidence": row.get("retrieval_confidence"),
                "tokens_used": int(row.get("bootstrap_tokens_used") or 0)
                + int(row.get("relevant_tokens_used") or 0)
                + int(row.get("cold_tokens_used") or 0),
            }
            for row in rows[:10]
        ],
    }
