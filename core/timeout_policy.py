from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from core.task_state_machine import transition
from storage.db import get_connection


@dataclass(frozen=True)
class TimeoutPolicy:
    offer_seconds: int = 120
    assignment_seconds: int = 300
    result_seconds: int = 600
    transfer_seconds: int = 180
    review_seconds: int = 120


DEFAULT_TIMEOUT_POLICY = TimeoutPolicy()


def timeout_for(state: str) -> int:
    return {
        "offered": DEFAULT_TIMEOUT_POLICY.offer_seconds,
        "assigned": DEFAULT_TIMEOUT_POLICY.assignment_seconds,
        "running": DEFAULT_TIMEOUT_POLICY.result_seconds,
        "transfer": DEFAULT_TIMEOUT_POLICY.transfer_seconds,
        "review": DEFAULT_TIMEOUT_POLICY.review_seconds,
    }.get(state, DEFAULT_TIMEOUT_POLICY.result_seconds)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reap_stale_subtasks(limit: int = 200) -> int:
    """
    Marks stale offered/claimed/assigned/running subtasks as timed_out and
    re-opens task offers where appropriate so work can continue.
    """
    safe_limit = max(1, int(limit))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT e.entity_id, e.to_state, e.created_at
            FROM task_state_events e
            JOIN (
                SELECT entity_id, MAX(seq) AS max_seq
                FROM task_state_events
                WHERE entity_type = 'subtask'
                GROUP BY entity_id
            ) latest
              ON latest.entity_id = e.entity_id AND latest.max_seq = e.seq
            WHERE e.entity_type = 'subtask'
              AND e.to_state IN ('offered', 'claimed', 'assigned', 'running')
            ORDER BY e.created_at ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    finally:
        conn.close()

    now_dt = datetime.now(timezone.utc)
    timed_out = 0
    for row in rows:
        task_id = str(row["entity_id"] or "")
        state = str(row["to_state"] or "")
        created_dt = _parse_iso(str(row["created_at"] or ""))
        if not task_id or not state or created_dt is None:
            continue
        age_seconds = (now_dt - created_dt).total_seconds()
        if age_seconds < float(timeout_for(state)):
            continue
        try:
            transition(
                entity_type="subtask",
                entity_id=task_id,
                to_state="timed_out",
                trace_id=task_id,
                details={
                    "reason": "timeout_reaper",
                    "previous_state": state,
                    "age_seconds": int(max(0.0, age_seconds)),
                },
            )
        except Exception:
            # State may have advanced concurrently.
            continue

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE task_assignments
                SET status = 'timed_out',
                    updated_at = ?
                WHERE task_id = ?
                  AND status = 'active'
                """,
                (_now_iso(), task_id),
            )
            conn.execute(
                """
                UPDATE task_claims
                SET status = 'timed_out',
                    updated_at = ?
                WHERE task_id = ?
                  AND status = 'pending'
                """,
                (_now_iso(), task_id),
            )
            conn.execute(
                """
                UPDATE task_offers
                SET status = CASE
                    WHEN status IN ('assigned', 'claimed') THEN 'open'
                    ELSE status
                END,
                updated_at = ?
                WHERE task_id = ?
                """,
                (_now_iso(), task_id),
            )
            conn.commit()
        finally:
            conn.close()
        timed_out += 1

    return timed_out
