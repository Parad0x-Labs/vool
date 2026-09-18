from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_curiosity_topic(
    *,
    session_id: str,
    task_id: str,
    trace_id: str,
    topic: str,
    topic_kind: str,
    reason: str,
    priority: float,
    source_profiles: list[dict[str, Any]],
) -> str:
    topic_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO curiosity_topics (
                topic_id, session_id, originating_task_id, trace_id, topic, topic_kind,
                reason, priority, source_profiles_json, status, created_at, updated_at,
                last_run_at, candidate_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, NULL, NULL)
            """,
            (
                topic_id,
                session_id,
                task_id,
                trace_id,
                topic,
                topic_kind,
                reason,
                float(priority),
                json.dumps(source_profiles, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        return topic_id
    finally:
        conn.close()


def update_curiosity_topic(
    topic_id: str,
    *,
    status: str,
    candidate_id: str | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE curiosity_topics
            SET status = ?, candidate_id = COALESCE(?, candidate_id), last_run_at = ?, updated_at = ?
            WHERE topic_id = ?
            """,
            (status, candidate_id, _utcnow(), _utcnow(), topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def record_curiosity_run(
    *,
    topic_id: str,
    task_id: str,
    trace_id: str,
    query_text: str,
    source_profile_ids: list[str],
    snippets: list[dict[str, Any]],
    candidate_id: str | None,
    outcome: str,
) -> str:
    run_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO curiosity_runs (
                run_id, topic_id, task_id, trace_id, query_text, source_profile_ids_json,
                snippets_json, candidate_id, outcome, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                topic_id,
                task_id,
                trace_id,
                query_text,
                json.dumps(source_profile_ids, sort_keys=True),
                json.dumps(snippets, sort_keys=True),
                candidate_id,
                outcome,
                _utcnow(),
            ),
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def claim_next_queued_curiosity_topic(*, stale_running_after_seconds: int = 900) -> dict[str, Any] | None:
    """Atomically move ONE queued topic to `running` and return it; None when nothing is queued.

    The claim is the UPDATE's own row count under `status = 'queued'`, so two consumers racing
    for the same row cannot both win: the loser's UPDATE matches nothing and it gets None. A
    `running` row older than `stale_running_after_seconds` is an orphan -- its executor died
    mid-topic (process exit, kill) -- and is put back to `queued` first, so a crash never
    strands work and never double-counts it. Highest priority first, then oldest.
    """
    now = _utcnow()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(1, int(stale_running_after_seconds)))
    ).isoformat()
    conn = get_connection()
    try:
        # ONE write transaction for requeue + select + claim: `BEGIN IMMEDIATE` takes the
        # database's write lock up front, so a second claimer cannot interleave between this
        # connection's SELECT and its UPDATE -- it waits at its own BEGIN and then sees the row
        # already `running`. The `AND status = 'queued'` guard on the claim below is the same
        # invariant stated a second time, for a store whose connection runs in autocommit.
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE curiosity_topics
            SET status = 'queued', updated_at = ?
            WHERE status = 'running' AND updated_at < ?
            """,
            (now, cutoff),
        )
        row = conn.execute(
            """
            SELECT *
            FROM curiosity_topics
            WHERE status = 'queued'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        claimed = conn.execute(
            """
            UPDATE curiosity_topics
            SET status = 'running', updated_at = ?, last_run_at = ?
            WHERE topic_id = ? AND status = 'queued'
            """,
            (now, now, str(row["topic_id"])),
        ).rowcount
        conn.commit()
        if claimed != 1:
            return None
        topic = _row_to_topic(dict(row))
        topic["status"] = "running"
        return topic
    finally:
        conn.close()


def count_curiosity_runs(topic_id: str) -> int:
    """How many execution attempts a topic has recorded (every outcome counts)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM curiosity_runs WHERE topic_id = ?", (str(topic_id),)
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def requeue_curiosity_topic(topic_id: str) -> None:
    """Put a claimed topic back to `queued` (cancelled or deadline-cut before it finished)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE curiosity_topics
            SET status = 'queued', updated_at = ?
            WHERE topic_id = ?
            """,
            (_utcnow(), str(topic_id)),
        )
        conn.commit()
    finally:
        conn.close()


def curiosity_queue_counts() -> dict[str, int]:
    """`{status: count}` over every topic -- the queue's state in one read."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM curiosity_topics GROUP BY status"
        ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}
    finally:
        conn.close()


def recent_curiosity_topics(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_topics
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_topics_for_session(session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_topics
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_runs(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM curiosity_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_run(dict(row)) for row in rows]
    finally:
        conn.close()


def recent_curiosity_runs_for_session(session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT runs.*, topics.session_id, topics.topic
            FROM curiosity_runs AS runs
            JOIN curiosity_topics AS topics
              ON topics.topic_id = runs.topic_id
            WHERE topics.session_id = ?
            ORDER BY runs.created_at DESC
            LIMIT ?
            """,
            (normalized, limit),
        ).fetchall()
        return [_row_to_run(dict(row)) for row in rows]
    finally:
        conn.close()


def _row_to_topic(row: dict[str, Any]) -> dict[str, Any]:
    row["source_profiles"] = json.loads(row.pop("source_profiles_json") or "[]")
    return row


def _row_to_run(row: dict[str, Any]) -> dict[str, Any]:
    row["source_profile_ids"] = json.loads(row.pop("source_profile_ids_json") or "[]")
    row["snippets"] = json.loads(row.pop("snippets_json") or "[]")
    return row
