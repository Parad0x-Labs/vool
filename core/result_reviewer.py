from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger, policy_engine, scoreboard_engine
from core.capability_tokens import revoke_capability_tokens_for_task
from core.hardware_challenge import evaluate_benchmark_result
from core.parent_orchestrator import continue_parent_orchestration_after_subtask
from core.reward_engine import create_pending_assist_reward
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from core.verdict_engine import review_task_result
from network.assist_models import TaskResult, TaskReview, TaskReward
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection


@dataclass
class ReviewArtifacts:
    review: TaskReview
    outbound_messages: list[bytes]
    points_awarded: int
    wnull_pending: int
    outcome: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _tokenize(text: str) -> set[str]:
    chars = []
    for ch in (text or "").lower():
        chars.append(ch if ch.isalnum() else " ")
    return {t for t in "".join(chars).split() if len(t) > 2}


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True)


def _nonce() -> str:
    return uuid.uuid4().hex


def _load_task_offer(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _load_task_capsule(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT capsule_json FROM task_capsules WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["capsule_json"])
    except Exception:
        return None
    finally:
        conn.close()


def _has_existing_review(task_id: str, helper_peer_id: str, reviewer_peer_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM task_reviews
            WHERE task_id = ?
              AND helper_peer_id = ?
              AND reviewer_peer_id = ?
            LIMIT 1
            """,
            (task_id, helper_peer_id, reviewer_peer_id),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _assignment_exists(task_id: str, helper_peer_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM task_assignments
            WHERE task_id = ?
              AND helper_peer_id = ?
              AND status = 'active'
            LIMIT 1
            """,
            (task_id, helper_peer_id),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _summary_score(summary: str) -> float:
    length = len((summary or "").strip())
    if length <= 32:
        return 0.15
    if length <= 96:
        return 0.45
    if length <= 240:
        return 0.75
    return 1.0


def _overlap_score(result_texts: list[str], target_texts: list[str]) -> float:
    rtoks: set[str] = set()
    ttoks: set[str] = set()

    for txt in result_texts:
        rtoks |= _tokenize(txt)
    for txt in target_texts:
        ttoks |= _tokenize(txt)

    if not rtoks or not ttoks:
        return 0.5

    overlap = len(rtoks & ttoks)
    union = len(rtoks | ttoks)
    return overlap / max(1, union)


def _review_outcome(result: TaskResult, capsule: dict[str, Any] | None, assignment_exists: bool) -> tuple[str, float, float, bool]:
    blocked_flags = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))
    risk_flags = set(result.risk_flags or [])

    harmful = False
    if "scope_violation" in risk_flags:
        harmful = True
    if any(flag in blocked_flags for flag in risk_flags):
        harmful = True
    if not assignment_exists:
        verdict = review_task_result(result, capsule, assignment_exists=False)
        return verdict.outcome, verdict.quality_score, verdict.helpfulness_score, verdict.harmful

    verdict = review_task_result(result, capsule, assignment_exists=True)
    if harmful and verdict.outcome != "harmful":
        return "harmful", min(verdict.quality_score, 0.10), 0.0, True
    return verdict.outcome, verdict.quality_score, verdict.helpfulness_score, verdict.harmful


def _persist_review(
    *,
    result: TaskResult,
    reviewer_peer_id: str,
    outcome: str,
    helpfulness_score: float,
    quality_score: float,
    harmful: bool,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_reviews (
                review_id, task_id, helper_peer_id, reviewer_peer_id, outcome,
                helpfulness_score, quality_score, harmful_flag, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                result.task_id,
                result.helper_agent_id,
                reviewer_peer_id,
                outcome,
                helpfulness_score,
                quality_score,
                1 if harmful else 0,
                _utcnow(),
            ),
        )

        conn.execute(
            """
            UPDATE task_results
            SET status = ?, updated_at = ?
            WHERE task_id = ? AND helper_peer_id = ?
            """,
            (outcome, _utcnow(), result.task_id, result.helper_agent_id),
        )

        # close helper-specific assignment
        conn.execute(
            """
            UPDATE task_assignments
            SET status = 'completed',
                updated_at = ?
            WHERE task_id = ?
              AND helper_peer_id = ?
              AND status = 'active'
            """,
            (_utcnow(), result.task_id, result.helper_agent_id),
        )

        # accepted / partial / harmful closes the task
        if outcome in {"accepted", "partial", "harmful"}:
            conn.execute(
                """
                UPDATE task_offers
                SET status = 'completed',
                    updated_at = ?
                WHERE task_id = ?
                """,
                (_utcnow(), result.task_id),
            )
        else:
            # rejected reopens the task for another helper if needed
            conn.execute(
                """
                UPDATE task_offers
                SET status = 'open',
                    updated_at = ?
                WHERE task_id = ?
                """,
                (_utcnow(), result.task_id),
            )

        conn.commit()
    finally:
        conn.close()


def _build_review_message(review: TaskReview, reviewer_peer_id: str) -> bytes:
    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_REVIEW",
        sender_peer_id=reviewer_peer_id,
        nonce=_nonce(),
        payload=review.model_dump(mode="json"),
    )


def _build_reward_notice_message(
    *,
    task_id: str,
    helper_peer_id: str,
    reviewer_peer_id: str,
    points_awarded: int,
    wnull_pending: int,
) -> bytes:
    reward = TaskReward(
        task_id=task_id,
        helper_agent_id=helper_peer_id,
        points_awarded=points_awarded,
        wnull_pending=wnull_pending,
        slashed=False,
        timestamp=datetime.now(timezone.utc),
    )
    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_REWARD",
        sender_peer_id=reviewer_peer_id,
        nonce=_nonce(),
        payload=reward.model_dump(mode="json"),
    )


def auto_review_task_result(
    result: TaskResult | dict[str, Any],
    *,
    reviewer_peer_id: str | None = None,
    emit_reward_notice: bool = True,
) -> ReviewArtifacts | None:
    """
    Parent-local review authority.
    - validates + scores result
    - stores TASK_REVIEW locally
    - creates pending reward locally
    - emits TASK_REVIEW (+ optional TASK_REWARD notice)
    """
    reviewer_peer_id = reviewer_peer_id or local_peer_id()
    task_result = result if isinstance(result, TaskResult) else TaskResult.model_validate(result)
    trace = ensure_trace(task_result.task_id)

    # Phase 30: Capability-Aware Hardware Benchmarking Intercept
    if task_result.task_id.startswith("benchmark-"):
        evaluate_benchmark_result(task_result.task_id, task_result.helper_agent_id)
        return None  # Benchmarks do not get standard points or semantic reviews

    offer = _load_task_offer(task_result.task_id)
    if not offer:
        return None

    # only the parent node should auto-review
    if offer["parent_peer_id"] != reviewer_peer_id:
        return None

    if _has_existing_review(task_result.task_id, task_result.helper_agent_id, reviewer_peer_id):
        return None

    capsule = _load_task_capsule(task_result.task_id)
    assignment_exists = _assignment_exists(task_result.task_id, task_result.helper_agent_id)

    outcome, quality_score, helpfulness_score, harmful = _review_outcome(
        task_result,
        capsule,
        assignment_exists,
    )

    _persist_review(
        result=task_result,
        reviewer_peer_id=reviewer_peer_id,
        outcome=outcome,
        helpfulness_score=helpfulness_score,
        quality_score=quality_score,
        harmful=harmful,
    )
    revoke_capability_tokens_for_task(
        task_result.task_id,
        helper_peer_id=task_result.helper_agent_id,
        reason=f"review:{outcome}",
    )

    review = TaskReview(
        task_id=task_result.task_id,
        helper_agent_id=task_result.helper_agent_id,
        reviewer_agent_id=reviewer_peer_id,
        outcome=outcome,
        helpfulness_score=_clamp(helpfulness_score),
        quality_score=_clamp(quality_score),
        harmful=harmful,
        timestamp=datetime.now(timezone.utc),
    )

    points_awarded = 0
    wnull_pending = 0

    if outcome in {"accepted", "partial"} and not harmful:
        reward = create_pending_assist_reward(
            task_id=task_result.task_id,
            parent_peer_id=reviewer_peer_id,
            helper_peer_id=task_result.helper_agent_id,
            helpfulness_score=helpfulness_score,
            quality_score=quality_score,
            task_complexity=0.50,
            timeliness=1.0,
            novelty=0.50,
            validator_confirmation=0.0,
            harmful=False,
            result_hash=task_result.result_hash,
        )
        points_awarded = reward.points_awarded
        wnull_pending = reward.wnull_pending

    outbound = [_build_review_message(review, reviewer_peer_id)]

    if emit_reward_notice and (points_awarded > 0 or wnull_pending > 0):
        outbound.append(
            _build_reward_notice_message(
                task_id=task_result.task_id,
                helper_peer_id=task_result.helper_agent_id,
                reviewer_peer_id=reviewer_peer_id,
                points_awarded=points_awarded,
                wnull_pending=wnull_pending,
            )
        )

    audit_logger.log(
        "task_result_auto_reviewed",
        target_id=task_result.task_id,
        target_type="task",
        details={
            "helper_peer_id": task_result.helper_agent_id,
            "outcome": outcome,
            "quality_score": round(quality_score, 4),
            "helpfulness_score": round(helpfulness_score, 4),
            "harmful": harmful,
            "points_awarded": points_awarded,
            "wnull_pending": wnull_pending,
        },
        trace_id=trace.trace_id,
    )
    transition(
        entity_type="subtask",
        entity_id=task_result.task_id,
        to_state="completed",
        details={"review_outcome": outcome, "helper_peer_id": task_result.helper_agent_id},
        trace_id=trace.trace_id,
    )

    continue_parent_orchestration_after_subtask(task_result.task_id)

    # Phase 20: Award validator score to the reviewer
    scoreboard_engine.award_validator_score(
        peer_id=reviewer_peer_id,
        task_id=task_result.task_id,
        review_correct=True,  # Assumed correct for parent-local reviews
    )

    return ReviewArtifacts(
        review=review,
        outbound_messages=outbound,
        points_awarded=points_awarded,
        wnull_pending=wnull_pending,
        outcome=outcome,
    )
