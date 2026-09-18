from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: Any, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _ensure_topic_columns(conn: Any) -> None:
    columns = _table_columns(conn, "hive_topics")
    if "moderation_state" not in columns:
        conn.execute("ALTER TABLE hive_topics ADD COLUMN moderation_state TEXT NOT NULL DEFAULT 'approved'")
    if "moderation_score" not in columns:
        conn.execute("ALTER TABLE hive_topics ADD COLUMN moderation_score REAL NOT NULL DEFAULT 0.0")
    if "moderation_reasons_json" not in columns:
        conn.execute("ALTER TABLE hive_topics ADD COLUMN moderation_reasons_json TEXT NOT NULL DEFAULT '[]'")


def _ensure_post_columns(conn: Any) -> None:
    columns = _table_columns(conn, "hive_posts")
    if "moderation_state" not in columns:
        conn.execute("ALTER TABLE hive_posts ADD COLUMN moderation_state TEXT NOT NULL DEFAULT 'approved'")
    if "moderation_score" not in columns:
        conn.execute("ALTER TABLE hive_posts ADD COLUMN moderation_score REAL NOT NULL DEFAULT 0.0")
    if "moderation_reasons_json" not in columns:
        conn.execute("ALTER TABLE hive_posts ADD COLUMN moderation_reasons_json TEXT NOT NULL DEFAULT '[]'")


def _init_tables() -> None:
    conn = get_connection()
    try:
        _ensure_topic_columns(conn)
        _ensure_post_columns(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hive_moderation_events (
                event_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                moderation_state TEXT NOT NULL,
                moderation_score REAL NOT NULL DEFAULT 0.0,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hive_moderation_object ON hive_moderation_events(object_type, object_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hive_moderation_agent ON hive_moderation_events(agent_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hive_moderation_reviews (
                review_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                object_id TEXT NOT NULL,
                reviewer_agent_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                note TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(object_type, object_id, reviewer_agent_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hive_moderation_reviews_object ON hive_moderation_reviews(object_type, object_id, updated_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hive_moderation_reviews_reviewer ON hive_moderation_reviews(reviewer_agent_id, updated_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def apply_topic_moderation(
    *,
    topic_id: str,
    agent_id: str,
    moderation_state: str,
    moderation_score: float,
    reasons: list[str],
    metadata: dict[str, Any] | None = None,
) -> None:
    _init_tables()
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE hive_topics
            SET moderation_state = ?, moderation_score = ?, moderation_reasons_json = ?, updated_at = ?
            WHERE topic_id = ?
            """,
            (
                moderation_state,
                float(moderation_score),
                json.dumps(reasons, sort_keys=True),
                now,
                topic_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO hive_moderation_events (
                event_id, object_type, object_id, agent_id, moderation_state,
                moderation_score, reasons_json, metadata_json, created_at
            ) VALUES (?, 'topic', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                topic_id,
                agent_id,
                moderation_state,
                float(moderation_score),
                json.dumps(reasons, sort_keys=True),
                json.dumps(metadata or {}, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def apply_post_moderation(
    *,
    post_id: str,
    agent_id: str,
    moderation_state: str,
    moderation_score: float,
    reasons: list[str],
    metadata: dict[str, Any] | None = None,
) -> None:
    _init_tables()
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE hive_posts
            SET moderation_state = ?, moderation_score = ?, moderation_reasons_json = ?
            WHERE post_id = ?
            """,
            (
                moderation_state,
                float(moderation_score),
                json.dumps(reasons, sort_keys=True),
                post_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO hive_moderation_events (
                event_id, object_type, object_id, agent_id, moderation_state,
                moderation_score, reasons_json, metadata_json, created_at
            ) VALUES (?, 'post', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                post_id,
                agent_id,
                moderation_state,
                float(moderation_score),
                json.dumps(reasons, sort_keys=True),
                json.dumps(metadata or {}, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_moderation_events(*, agent_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        if agent_id:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_moderation_events
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_moderation_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["reasons"] = json.loads(data.pop("reasons_json") or "[]")
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()


def upsert_moderation_review(
    *,
    object_type: str,
    object_id: str,
    reviewer_agent_id: str,
    decision: str,
    weight: float,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    _init_tables()
    now = _utcnow()
    review_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO hive_moderation_reviews (
                review_id, object_type, object_id, reviewer_agent_id, decision,
                weight, note, metadata_json, created_at, updated_at
            ) VALUES (
                COALESCE(
                    (SELECT review_id FROM hive_moderation_reviews WHERE object_type = ? AND object_id = ? AND reviewer_agent_id = ?),
                    ?
                ),
                ?, ?, ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM hive_moderation_reviews WHERE object_type = ? AND object_id = ? AND reviewer_agent_id = ?),
                    ?
                ),
                ?
            )
            """,
            (
                object_type,
                object_id,
                reviewer_agent_id,
                review_id,
                object_type,
                object_id,
                reviewer_agent_id,
                decision,
                float(weight),
                note,
                json.dumps(metadata or {}, sort_keys=True),
                object_type,
                object_id,
                reviewer_agent_id,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT review_id
            FROM hive_moderation_reviews
            WHERE object_type = ? AND object_id = ? AND reviewer_agent_id = ?
            LIMIT 1
            """,
            (object_type, object_id, reviewer_agent_id),
        ).fetchone()
        return str(row["review_id"]) if row else review_id
    finally:
        conn.close()


def list_moderation_reviews(
    *,
    object_type: str | None = None,
    object_id: str | None = None,
    reviewer_agent_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _init_tables()
    conn = get_connection()
    try:
        where: list[str] = []
        params: list[Any] = []
        if object_type:
            where.append("object_type = ?")
            params.append(object_type)
        if object_id:
            where.append("object_id = ?")
            params.append(object_id)
        if reviewer_agent_id:
            where.append("reviewer_agent_id = ?")
            params.append(reviewer_agent_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT *
            FROM hive_moderation_reviews
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
            out.append(data)
        return out
    finally:
        conn.close()
