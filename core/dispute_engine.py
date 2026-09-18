from __future__ import annotations

from dataclasses import dataclass

from core import audit_logger, scoreboard_engine
from core.appeal_queue import enqueue_appeal
from core.review_quorum import evaluate_quorum


@dataclass(frozen=True)
class DisputeResolution:
    appeal_id: str
    status: str
    reviewer_penalty_applied: bool


def open_dispute(task_id: str, appellant_peer_id: str, reason: str, evidence: dict) -> str:
    appeal_id = enqueue_appeal(task_id, appellant_peer_id, reason, evidence)
    audit_logger.log(
        "dispute_opened",
        target_id=task_id,
        target_type="task",
        details={"appeal_id": appeal_id, "appellant_peer_id": appellant_peer_id},
        trace_id=task_id,
    )
    return appeal_id


def resolve_dispute(appeal_id: str, task_id: str, reviewer_peer_id: str, votes: list[bool]) -> DisputeResolution:
    decision = evaluate_quorum(votes)
    reviewer_penalty_applied = False
    if not decision.accepted:
        scoreboard_engine.award_validator_score(reviewer_peer_id, task_id, review_correct=False)
        reviewer_penalty_applied = True
    audit_logger.log(
        "dispute_resolved",
        target_id=task_id,
        target_type="task",
        details={
            "appeal_id": appeal_id,
            "accepted": decision.accepted,
            "approvals": decision.approvals,
            "rejections": decision.rejections,
        },
        trace_id=task_id,
    )
    return DisputeResolution(
        appeal_id=appeal_id,
        status="upheld" if decision.accepted else "rejected",
        reviewer_penalty_applied=reviewer_penalty_applied,
    )
