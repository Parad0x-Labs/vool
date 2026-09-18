from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine
from network import quarantine
from storage.db import get_connection


@dataclass
class Outcome:
    status: str
    is_success: bool
    is_durable: bool
    harmful_flag: bool
    confidence_before: float
    confidence_after: float


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def evaluate_outcome(task: Any, plan: Any, gate_decision: Any, execution_result: Any) -> Outcome:
    mode = _get(gate_decision, "mode", "advice_only")
    confidence_before = float(_get(plan, "confidence", 0.0))

    harmful = bool(_get(execution_result, "harmful", False))
    has_error = bool(_get(execution_result, "error", False))

    if mode == "blocked":
        status = "blocked"
        success = False
        durable = False
        confidence_after = max(0.0, confidence_before - 0.15)
    elif harmful:
        status = "harmful"
        success = False
        durable = False
        confidence_after = max(0.0, confidence_before - 0.35)
    elif has_error:
        status = "failed"
        success = False
        durable = False
        confidence_after = max(0.0, confidence_before - 0.20)
    elif confidence_before >= 0.75:
        status = "success"
        success = True
        durable = True
        confidence_after = min(1.0, confidence_before + 0.05)
    else:
        status = "partial"
        success = False
        durable = False
        confidence_after = min(1.0, confidence_before + 0.02)

    return Outcome(
        status=status,
        is_success=success,
        is_durable=durable,
        harmful_flag=harmful,
        confidence_before=confidence_before,
        confidence_after=confidence_after,
    )


def _top_candidates(evidence: dict[str, Any]) -> list[dict]:
    out: list[dict] = []
    for label in ["candidates", "local_candidates", "swarm_candidates"]:
        items = evidence.get(label) or []
        if items:
            out.append(items[0])
    return out


def _update_task_row(task: Any, outcome: Outcome) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE local_tasks
            SET outcome = ?,
                confidence = ?,
                harmful_flag = ?,
                updated_at = ?
            WHERE task_id = ?
            """,
            (
                outcome.status,
                outcome.confidence_after,
                1 if outcome.harmful_flag else 0,
                _utcnow(),
                _get(task, "task_id"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _bump_shard(conn, candidate: dict, task_id: str, outcome: Outcome) -> None:
    shard_id = candidate.get("shard_id")
    if not shard_id:
        return

    row = conn.execute(
        """
        SELECT trust_score, local_validation_count, local_failure_count
        FROM learning_shards
        WHERE shard_id = ?
        LIMIT 1
        """,
        (shard_id,),
    ).fetchone()

    if not row:
        return

    trust = float(row["trust_score"])
    ok = int(row["local_validation_count"])
    fail = int(row["local_failure_count"])

    if outcome.is_success:
        ok += 1
        trust += float(policy_engine.get("trust.successful_validation_boost", 0.08))
    else:
        fail += 1
        trust -= float(policy_engine.get("trust.failed_validation_penalty", 0.12))

    if outcome.harmful_flag:
        trust -= float(policy_engine.get("trust.harmful_outcome_penalty", 0.30))

    trust = max(0.0, min(1.0, trust))
    status = "quarantined" if outcome.harmful_flag else "active"

    conn.execute(
        """
        UPDATE learning_shards
        SET trust_score = ?,
            local_validation_count = ?,
            local_failure_count = ?,
            quarantine_status = CASE
                WHEN ? = 'quarantined' THEN 'quarantined'
                ELSE quarantine_status
            END,
            updated_at = ?
        WHERE shard_id = ?
        """,
        (trust, ok, fail, status, _utcnow(), shard_id),
    )

    conn.execute(
        """
        INSERT INTO shard_feedback (
            feedback_id, shard_id, task_id, peer_id, outcome,
            confidence_before, confidence_after, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            shard_id,
            task_id,
            candidate.get("source_node_id"),
            outcome.status,
            outcome.confidence_before,
            outcome.confidence_after,
            None,
            _utcnow(),
        ),
    )

    if outcome.harmful_flag:
        audit_logger.log(
            "shard_quarantined",
            target_id=shard_id,
            target_type="shard",
            details={"reason": "harmful_outcome"},
        )


def _bump_peer(conn, candidate: dict, outcome: Outcome) -> None:
    peer_id = candidate.get("source_node_id")
    if not peer_id:
        return

    row = conn.execute(
        "SELECT 1 FROM peers WHERE peer_id = ? LIMIT 1",
        (peer_id,),
    ).fetchone()

    if not row:
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO peers (
                peer_id, display_alias, trust_score, successful_shards,
                failed_shards, strike_count, status, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, 0, 0, 0, 'active', ?, ?, ?)
            """,
            (
                peer_id,
                None,
                float(policy_engine.get("trust.initial_peer_trust", 0.50)),
                now,
                now,
                now,
            ),
        )

    row = conn.execute(
        """
        SELECT trust_score, successful_shards, failed_shards, strike_count, status
        FROM peers
        WHERE peer_id = ?
        LIMIT 1
        """,
        (peer_id,),
    ).fetchone()

    trust = float(row["trust_score"])
    success_count = int(row["successful_shards"])
    fail_count = int(row["failed_shards"])
    strike_count = int(row["strike_count"])
    row["status"]

    if outcome.is_success:
        success_count += 1
        trust += 0.02
    else:
        fail_count += 1
        trust -= 0.04

    if outcome.harmful_flag:
        strike_count += 1
        trust -= float(policy_engine.get("trust.harmful_outcome_penalty", 0.30))

    trust = max(0.0, min(1.0, trust))

    conn.execute(
        """
        UPDATE peers
        SET trust_score = ?,
            successful_shards = ?,
            failed_shards = ?,
            strike_count = ?,
            last_seen_at = ?,
            updated_at = ?
        WHERE peer_id = ?
        """,
        (
            trust,
            success_count,
            fail_count,
            strike_count,
            _utcnow(),
            _utcnow(),
            peer_id,
        ),
    )

    if outcome.harmful_flag or strike_count >= int(policy_engine.get("network.max_failed_messages_before_quarantine", 3)):
        quarantine.quarantine_peer(peer_id, "harmful_or_repeated_bad_shards")


def apply(task: Any, evidence: dict[str, Any], outcome: Outcome) -> None:
    _update_task_row(task, outcome)

    task_id = _get(task, "task_id")
    candidates = _top_candidates(evidence)

    conn = get_connection()
    try:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            _bump_shard(conn, candidate, task_id, outcome)
            _bump_peer(conn, candidate, outcome)

        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "feedback_applied",
        target_id=task_id,
        target_type="task",
        details={
            "outcome": outcome.status,
            "harmful": outcome.harmful_flag,
            "used_candidates": len(candidates),
        },
    )
