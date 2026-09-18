from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core import audit_logger, policy_engine
from core.capacity_predictor import predict_local_override_necessity
from core.consensus_validator import decide_consensus_for_task
from core.finalizer import finalize_parent_response
from core.local_worker_pool import resolve_local_worker_capacity
from core.task_capsule import resign_task_capsule
from core.task_decomposer import broadcast_decomposed_subtasks, decompose_task, should_decompose
from core.task_reassembler import check_and_reassemble as reassemble_parent_task
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from storage.db import get_connection


@dataclass
class ParentOrchestrationResult:
    parent_task_id: str
    action: str  # decomposed | waiting | reassembled | finalized | verification_requested | no_action
    subtasks_known: int = 0
    subtasks_broadcasted: int = 0
    waiting_subtasks: int = 0
    completed_subtasks: int = 0
    verification_requested: int = 0
    finalized: bool = False
    confidence: float = 0.0
    reason: str = ""


def _parent_ref_from_capsule_json(capsule_json: str) -> str | None:
    try:
        data = json.loads(capsule_json)
    except Exception:
        return None

    ctx = data.get("sanitized_context") or {}
    constraints = ctx.get("known_constraints") or []
    for item in constraints:
        if not isinstance(item, str):
            continue
        if item.startswith("parent_task_ref:"):
            return item.split(":", 1)[1].strip() or None
    return None


def _matching_parent(parent_ref: str, parent_task_id: str) -> bool:
    # Prefer exact task-id match to avoid accidental prefix collisions between unrelated IDs.
    if parent_ref == parent_task_id:
        return True
    # Legacy tolerance for old short refs only.
    if len(parent_ref) < 16 or len(parent_task_id) < 16:
        return parent_task_id.startswith(parent_ref) or parent_ref.startswith(parent_task_id)
    return False


def _subtask_ids_for_parent(parent_task_id: str) -> list[str]:
    marker = f"parent_task_ref:{parent_task_id}"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT task_id, capsule_json, parent_task_ref
            FROM task_capsules
            WHERE parent_task_ref = ?
            ORDER BY updated_at DESC
            """,
            (parent_task_id,),
        ).fetchall()
        # Legacy fallback path for older rows that predate explicit parent_task_ref.
        if not rows:
            rows = conn.execute(
                """
                SELECT task_id, capsule_json, parent_task_ref
                FROM task_capsules
                WHERE capsule_json LIKE ?
                ORDER BY updated_at DESC
                LIMIT 200
                """,
                (f"%{marker}%",),
            ).fetchall()
    finally:
        conn.close()

    out: list[str] = []
    for row in rows:
        parent_ref = str(row["parent_task_ref"] or "").strip() or _parent_ref_from_capsule_json(row["capsule_json"])
        if not parent_ref:
            continue
        if _matching_parent(parent_ref, parent_task_id):
            out.append(str(row["task_id"]))
    return sorted(set(out))


def _subtask_state(task_id: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                COALESCE((SELECT status FROM task_offers WHERE task_id = ? LIMIT 1), 'unknown') AS offer_status,
                (SELECT COUNT(*) FROM task_results WHERE task_id = ?) AS result_count,
                COALESCE(SUM(CASE WHEN outcome = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted_count,
                COALESCE(SUM(CASE WHEN outcome = 'partial' THEN 1 ELSE 0 END), 0) AS partial_count,
                COALESCE(SUM(CASE WHEN outcome = 'harmful' THEN 1 ELSE 0 END), 0) AS harmful_count,
                COALESCE(SUM(CASE WHEN outcome = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count
            FROM task_reviews
            WHERE task_id = ?
            """,
            (task_id, task_id, task_id),
        ).fetchone()

        return {
            "offer_status": str(row["offer_status"]) if row else "unknown",
            "result_count": int(row["result_count"]) if row else 0,
            "accepted_count": int(row["accepted_count"]) if row else 0,
            "partial_count": int(row["partial_count"]) if row else 0,
            "harmful_count": int(row["harmful_count"]) if row else 0,
            "rejected_count": int(row["rejected_count"]) if row else 0,
        }
    finally:
        conn.close()


def _resolved_subtask_width() -> int:
    configured = max(1, int(policy_engine.get("orchestration.max_subtasks_per_parent", 3)))
    hard_cap = max(1, int(policy_engine.get("orchestration.max_subtasks_hard_cap", 10)))
    target = min(configured, hard_cap)

    if bool(policy_engine.get("orchestration.local_worker_auto_detect", True)):
        pool_hard_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", hard_cap)))
        policy_target = int(policy_engine.get("orchestration.local_worker_pool_target", 0) or 0)
        requested = policy_target if policy_target > 0 else None
        auto_width, _recommended = resolve_local_worker_capacity(
            requested=requested,
            hard_cap=pool_hard_cap,
        )
        target = max(target, min(auto_width, hard_cap))

    return max(1, min(target, hard_cap))


def _finalize_parent_task(parent_task_id: str, *, confidence: float, complete: bool) -> None:
    trace = ensure_trace(parent_task_id, trace_id=parent_task_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE local_tasks
            SET outcome = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            ("success" if complete else "partial", parent_task_id),
        )
        conn.commit()
    except Exception as e:
        audit_logger.log(
            "parent_task_finalize_error",
            target_id=parent_task_id,
            target_type="task",
            details={
                "error": str(e),
            },
        )
    finally:
        conn.close()

    audit_logger.log(
        "parent_task_finalized",
        target_id=parent_task_id,
        target_type="task",
        details={
            "confidence": round(confidence, 4),
            "complete": complete,
        },
        trace_id=trace.trace_id,
    )
    transition(
        entity_type="local_task",
        entity_id=parent_task_id,
        to_state="finalized",
        details={"confidence": round(confidence, 4), "complete": complete},
        trace_id=trace.trace_id,
    )


def orchestrate_parent_task(
    *,
    parent_task_id: str,
    user_input: str | None,
    classification: dict[str, Any] | None,
    environment_tags: dict[str, str] | None = None,
    exclude_host_group_hint_hash: str | None = None,
    bid_multiplier: float = 1.0,
) -> ParentOrchestrationResult:
    """
    Entry point when a parent task is first created.
    Decides whether to decompose immediately or keep it local.
    """
    subtasks = _subtask_ids_for_parent(parent_task_id)
    if subtasks:
        return continue_parent_orchestration(parent_task_id, exclude_host_group_hint_hash=exclude_host_group_hint_hash)

    if not user_input or not classification:
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="no_action",
            reason="No input/classification provided.",
        )

    if not should_decompose(user_input, classification):
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="no_action",
            reason="Task does not justify decomposition.",
        )

    if predict_local_override_necessity():
        audit_logger.log(
            "orchestration_local_override",
            target_id=parent_task_id,
            target_type="task",
            details={"reason": "swarm_empty_fallback"}
        )
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="no_action",
            reason="Swarm is empty. Bypassing decomposition for local execution.",
        )

    decomposed = decompose_task(
        parent_task_id=parent_task_id,
        user_input=user_input,
        classification=classification,
        environment_tags=environment_tags or {},
        deadline_minutes=20,
        max_subtasks=_resolved_subtask_width(),
        bid_multiplier=bid_multiplier,
    )

    if not decomposed:
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="no_action",
            reason="Decomposer returned no subtasks.",
        )

    # Phase 26: Anti-Freeloader Credit System / Phase 29: Priority Ticket DEX
    total_cost = sum(sub.offer.reward_hint.points for sub in decomposed)
    if total_cost > 0:
        from core.credit_ledger import reserve_swarm_dispatch_budget
        from network.signer import get_local_peer_id as local_peer_id

        budget = reserve_swarm_dispatch_budget(
            local_peer_id(),
            float(total_cost),
            f"dispatch_task:{parent_task_id}",
            receipt_id=f"dispatch:{parent_task_id}",
            metadata={"parent_task_id": parent_task_id, "subtasks": len(decomposed)},
        )
        if not budget.allowed:
            audit_logger.log(
                "task_dispatch_blocked_budget_exhausted",
                target_id=parent_task_id,
                target_type="task",
                details={
                    "required_credits": total_cost,
                    "reason": budget.reason,
                    "free_tier_points_used": budget.free_tier_points_used,
                    "free_tier_points_limit": budget.free_tier_points_limit,
                },
            )
            return ParentOrchestrationResult(
                parent_task_id=parent_task_id,
                action="no_action",
                reason="Swarm dispatch blocked: no credits and free-tier budget is exhausted.",
            )

        if budget.mode == "free_tier":
            audit_logger.log(
                "task_dispatch_downgraded_to_free_tier",
                target_id=parent_task_id,
                target_type="task",
                details={
                    "required_credits": total_cost,
                    "reason": budget.reason,
                    "free_tier_points_used": budget.free_tier_points_used,
                    "free_tier_points_limit": budget.free_tier_points_limit,
                },
            )

            for sub in decomposed:
                sub.offer.reward_hint.points = 0
                sub.offer.priority = "background"
                sub.capsule.learning_allowed = True
                # The capsule was hashed + signed at build time; mutating it
                # invalidates both, so helpers would reject the offer.
                sub.capsule = resign_task_capsule(sub.capsule)
                sub.offer.capsule = sub.capsule.model_dump(mode="json")

    sent = broadcast_decomposed_subtasks(
        decomposed,
        exclude_host_group_hint_hash=exclude_host_group_hint_hash,
    )

    audit_logger.log(
        "parent_task_decomposed",
        target_id=parent_task_id,
        target_type="task",
        details={
            "subtasks": len(decomposed),
            "offers_sent": sent,
        },
    )

    return ParentOrchestrationResult(
        parent_task_id=parent_task_id,
        action="decomposed",
        subtasks_known=len(decomposed),
        subtasks_broadcasted=sent,
        reason="Parent task was decomposed and subtasks were broadcast.",
    )


def continue_parent_orchestration_after_subtask(subtask_task_id: str) -> ParentOrchestrationResult | None:
    """
    Called after a subtask review.
    Finds the parent and continues orchestration.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT capsule_json
            FROM task_capsules
            WHERE task_id = ?
            LIMIT 1
            """,
            (subtask_task_id,),
        ).fetchone()
        if not row:
            return None
        parent_ref = _parent_ref_from_capsule_json(row["capsule_json"])
    finally:
        conn.close()

    if not parent_ref:
        return None

    return continue_parent_orchestration(parent_ref)


def continue_parent_orchestration(
    parent_task_id: str,
    *,
    exclude_host_group_hint_hash: str | None = None,
) -> ParentOrchestrationResult:
    """
    Parent lifecycle after subtasks exist:
    - checks conflicts
    - requests verification if needed
    - tries reassembly
    - decides whether to wait or finalize
    """
    subtasks = _subtask_ids_for_parent(parent_task_id)
    if not subtasks:
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="no_action",
            reason="No subtasks are known for parent.",
        )

    waiting = 0
    completed = 0
    verification_requested = 0

    # 1) handle possible conflicts per subtask
    for subtask_id in subtasks:
        state = _subtask_state(subtask_id)

        resolved = state["accepted_count"] > 0
        conflicting = state["result_count"] >= 2 and not resolved and state["harmful_count"] == 0

        if conflicting:
            decision = decide_consensus_for_task(
                subtask_id,
                exclude_host_group_hint_hash=exclude_host_group_hint_hash,
            )
            if decision.action == "verification_requested":
                verification_requested += 1

        # recompute state after possible consensus action is okay to leave approximate in v1
        if state["offer_status"] in {"open", "assigned"} and state["accepted_count"] == 0 and state["partial_count"] == 0:
            waiting += 1
        if state["accepted_count"] > 0 or state["partial_count"] > 0 or state["harmful_count"] > 0:
            completed += 1

    if verification_requested > 0:
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="verification_requested",
            subtasks_known=len(subtasks),
            waiting_subtasks=waiting,
            completed_subtasks=completed,
            verification_requested=verification_requested,
            reason="Conflicting helper answers triggered independent verification.",
        )

    # 2) try to reassemble current progress
    reassembled = reassemble_parent_task(parent_task_id)
    if not reassembled:
        action = "waiting" if waiting > 0 else "no_action"
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action=action,
            subtasks_known=len(subtasks),
            waiting_subtasks=waiting,
            completed_subtasks=completed,
            reason="Waiting for enough accepted/partial subtask output to reassemble.",
        )

    # 3) decide whether to finalize
    complete = (
        reassembled.is_complete
        and completed >= max(1, len(subtasks))
        and waiting == 0
    )

    if complete:
        final_confidence = max(0.0, min(1.0, float(reassembled.confidence)))
        _finalize_parent_task(
            parent_task_id,
            confidence=final_confidence,
            complete=True,
        )
        finalized = finalize_parent_response(parent_task_id)
        if finalized:
            audit_logger.log(
                "parent_response_materialized",
                target_id=parent_task_id,
                target_type="task",
                details={
                    "status": finalized.status,
                    "confidence": round(finalized.confidence, 4),
                    "completeness_score": round(finalized.completeness_score, 4),
                },
            )
        return ParentOrchestrationResult(
            parent_task_id=parent_task_id,
            action="finalized",
            subtasks_known=len(subtasks),
            waiting_subtasks=waiting,
            completed_subtasks=completed,
            finalized=True,
            confidence=final_confidence,
            reason="Parent task reached completion threshold and was finalized.",
        )

    return ParentOrchestrationResult(
        parent_task_id=parent_task_id,
        action="reassembled" if completed > 0 else "waiting",
        subtasks_known=len(subtasks),
        waiting_subtasks=waiting,
        completed_subtasks=completed,
        confidence=0.5,
        reason="Parent task was reassembled from current subtask progress but is not final yet.",
    )
