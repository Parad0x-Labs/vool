from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core import audit_logger
from core.semantic_judge import evaluate_semantic_agreement
from core.task_capsule import TaskCapsule, build_task_capsule
from core.verdict_engine import evaluate_consensus
from network.assist_models import RewardHint, TaskOffer
from network.signer import get_local_peer_id as local_peer_id
from retrieval.swarm_query import broadcast_task_offer
from storage.db import get_connection


@dataclass
class CandidateScore:
    result_id: str
    helper_peer_id: str
    score: float
    confidence: float
    helpfulness_score: float
    quality_score: float
    review_outcome: str
    summary: str
    result_hash: str | None


@dataclass
class ConsensusDecision:
    task_id: str
    action: str  # winner_selected | verification_requested | insufficient_data | already_resolved
    winner_result_id: str | None = None
    winner_helper_peer_id: str | None = None
    verification_task_id: str | None = None
    verdict: str = "insufficient_evidence"
    reason: str = ""


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _tokenize(text: str) -> set[str]:
    chars = []
    for ch in (text or "").lower():
        chars.append(ch if ch.isalnum() else " ")
    return {t for t in "".join(chars).split() if len(t) > 2}


def _similarity(a: str, b: str) -> float:
    # Phase 30: Use LLM Semantic Consensus Judge instead of string equality
    return float(evaluate_semantic_agreement(a, b))


def _load_task_results(task_id: str) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                tr.result_id,
                tr.task_id,
                tr.helper_peer_id,
                tr.summary,
                tr.result_hash,
                tr.confidence,
                tr.evidence_json,
                tr.abstract_steps_json,
                tr.risk_flags_json,
                tr.status,
                rv.outcome AS review_outcome,
                rv.helpfulness_score,
                rv.quality_score,
                rv.harmful_flag
            FROM task_results tr
            LEFT JOIN (
                SELECT r1.*
                FROM task_reviews r1
                JOIN (
                    SELECT task_id, helper_peer_id, MAX(created_at) AS max_created_at
                    FROM task_reviews
                    GROUP BY task_id, helper_peer_id
                ) latest
                  ON latest.task_id = r1.task_id
                 AND latest.helper_peer_id = r1.helper_peer_id
                 AND latest.max_created_at = r1.created_at
            ) rv
              ON rv.task_id = tr.task_id
             AND rv.helper_peer_id = tr.helper_peer_id
            WHERE tr.task_id = ?
            ORDER BY tr.created_at ASC
            """,
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _task_capsule_json(task_id: str) -> dict[str, Any] | None:
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


def _task_offer_row(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _has_accepted_review(task_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM task_reviews
            WHERE task_id = ?
              AND outcome = 'accepted'
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _verification_exists(task_id: str) -> bool:
    marker = f"verification_of:{task_id}"
    rows = []
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM task_capsules
            WHERE verification_of_task_id = ?
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if row:
            return True
        rows = conn.execute(
            """
            SELECT capsule_json
            FROM task_capsules
            WHERE capsule_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT 200
            """,
            (f"%{marker}%",),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    for row in rows:
        try:
            data = json.loads(row["capsule_json"])
        except Exception:
            continue
        ctx = data.get("sanitized_context") or {}
        constraints = ctx.get("known_constraints") or []
        if marker in constraints:
            return True
    return False


def _score_candidates(rows: list[dict[str, Any]]) -> list[CandidateScore]:
    usable = [r for r in rows if not int(r.get("harmful_flag") or 0)]
    if len(usable) < 2:
        return []

    scored: list[CandidateScore] = []

    for row in usable:
        review_outcome = str(row.get("review_outcome") or "")
        review_weight = {
            "accepted": 1.0,
            "partial": 0.70,
            "rejected": 0.20,
            "harmful": 0.0,
            "": 0.40,
        }.get(review_outcome, 0.40)

        confidence = _clamp(float(row.get("confidence") or 0.0))
        helpfulness = _clamp(float(row.get("helpfulness_score") or 0.5))
        quality = _clamp(float(row.get("quality_score") or 0.5))

        others = [u for u in usable if u["result_id"] != row["result_id"]]
        if others:
            agreement = sum(_similarity(str(row.get("summary") or ""), str(o.get("summary") or "")) for o in others) / len(others)
        else:
            agreement = 0.5

        score = _clamp(
            (0.30 * review_weight)
            + (0.25 * confidence)
            + (0.20 * helpfulness)
            + (0.15 * quality)
            + (0.10 * agreement)
        )

        scored.append(
            CandidateScore(
                result_id=str(row["result_id"]),
                helper_peer_id=str(row["helper_peer_id"]),
                score=score,
                confidence=confidence,
                helpfulness_score=helpfulness,
                quality_score=quality,
                review_outcome=review_outcome,
                summary=str(row.get("summary") or ""),
                result_hash=row.get("result_hash"),
            )
        )

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored


def _build_verification_offer(task_id: str) -> tuple[str, TaskOffer, TaskCapsule] | None:
    offer = _task_offer_row(task_id)
    capsule = _task_capsule_json(task_id)
    if not offer or not capsule:
        return None

    ctx = capsule.get("sanitized_context") or {}
    summary = str(capsule.get("summary") or "Independent verification requested.")
    new_task_id = str(uuid.uuid4())

    verification_capsule = build_task_capsule(
        parent_agent_id=local_peer_id(),
        task_id=new_task_id,
        task_type="validation",
        subtask_type="consensus_verification",
        summary=f"Independently verify the strongest answer for this sanitized subtask: {summary[:220]}",
        sanitized_context={
            "problem_class": str(ctx.get("problem_class") or "unknown"),
            "environment_tags": ctx.get("environment_tags") or {},
            "abstract_inputs": list(ctx.get("abstract_inputs") or [])[:6],
            "known_constraints": [*list(ctx.get("known_constraints") or [])[:6], f"verification_of:{task_id}", "independent reviewer required", "no execution"],
        },
        allowed_operations=["reason", "compare", "rank", "summarize", "validate"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=20),
        reward_hint={"points": 12, "wnull_pending": 6},
    )

    verification_offer = TaskOffer(
        task_id=new_task_id,
        parent_agent_id=local_peer_id(),
        capsule_id=verification_capsule.capsule_id,
        task_type="validation",
        subtask_type="consensus_verification",
        summary="Independently verify which helper answer is strongest for a sanitized subtask.",
        required_capabilities=["validation", "ranking"],
        max_helpers=1,
        priority="high",
        reward_hint=RewardHint(points=12, wnull_pending=6),
        capsule=verification_capsule.model_dump(mode="json"),
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=20),
    )

    return new_task_id, verification_offer, verification_capsule


def decide_consensus_for_task(task_id: str, *, exclude_host_group_hint_hash: str | None = None) -> ConsensusDecision:
    if _has_accepted_review(task_id):
        return ConsensusDecision(
            task_id=task_id,
            action="already_resolved",
            verdict="accepted",
            reason="Task already has an accepted review.",
        )

    rows = _load_task_results(task_id)
    if len(rows) < 2:
        return ConsensusDecision(
            task_id=task_id,
            action="insufficient_data",
            verdict="insufficient_evidence",
            reason="Fewer than two task results available.",
        )

    scored = _score_candidates(rows)
    if len(scored) < 2:
        return ConsensusDecision(
            task_id=task_id,
            action="insufficient_data",
            verdict="insufficient_evidence",
            reason="Not enough usable non-harmful results.",
        )

    best = scored[0]
    second = scored[1]
    margin = best.score - second.score
    usable_rows = [r for r in rows if not int(r.get("harmful_flag") or 0)]
    verdict = evaluate_consensus(
        [
            {
                "result_id": row["result_id"],
                "task_id": row["task_id"],
                "helper_agent_id": row["helper_peer_id"],
                "result_type": "validation",
                "summary": row["summary"],
                "confidence": row["confidence"],
                "evidence": json.loads(row.get("evidence_json") or "[]"),
                "abstract_steps": json.loads(row.get("abstract_steps_json") or "[]"),
                "risk_flags": json.loads(row.get("risk_flags_json") or "[]"),
                "result_hash": row.get("result_hash"),
                "timestamp": datetime.now(timezone.utc),
            }
            for row in usable_rows
        ]
    )

    if verdict.verdict in {"accepted", "accepted_with_conflict"} and best.score >= 0.62 and margin >= 0.05:
        # Phase 28: Anti-Cheat penalty check
        # If one node scored highly and the other scored abysmally, the loser was caught cheating
        if margin >= 0.40 and second.score < 0.30:
            from core.scoreboard_engine import zero_out_provider
            zero_out_provider(second.helper_peer_id, "spot_check_failed_catastrophically", task_id)
            audit_logger.log(
                "anti_cheat_triggered",
                target_id=task_id,
                target_type="task",
                details={"cheater": second.helper_peer_id, "margin": round(margin, 4)}
            )

        audit_logger.log(
            "consensus_winner_selected",
            target_id=task_id,
            target_type="task",
            details={
                "winner_result_id": best.result_id,
                "winner_helper_peer_id": best.helper_peer_id,
                "best_score": round(best.score, 4),
                "margin": round(margin, 4),
                "verdict": verdict.verdict,
            },
            trace_id=task_id,
        )
        return ConsensusDecision(
            task_id=task_id,
            action="winner_selected",
            winner_result_id=best.result_id,
            winner_helper_peer_id=best.helper_peer_id,
            verdict=verdict.verdict,
            reason="Winner selected using evidence-weighted verdicting." if verdict.verdict == "accepted" else "Winner selected with soft conflict noted.",
        )

    # Phase 28: Spot-check success! Both nodes returned strong, agreeing answers
    if verdict.verdict == "accepted" and best.score >= 0.70 and margin < 0.12:
        audit_logger.log(
            "spot_check_success",
            target_id=task_id,
            target_type="task",
            details={
                "winner_result_id": best.result_id,
                "winner_helper_peer_id": best.helper_peer_id,
                "second_helper_peer_id": second.helper_peer_id,
            },
            trace_id=task_id,
        )
        return ConsensusDecision(
            task_id=task_id,
            action="winner_selected",
            winner_result_id=best.result_id,
            winner_helper_peer_id=best.helper_peer_id,
            verdict=verdict.verdict,
            reason="Spot check clear consensus. Both helpers provided excellent matching results.",
        )

    if _verification_exists(task_id):
        return ConsensusDecision(
            task_id=task_id,
            action="verification_requested",
            verdict=verdict.verdict,
            reason="Verification task already exists.",
        )

    built = _build_verification_offer(task_id)
    if not built:
        return ConsensusDecision(
            task_id=task_id,
            action="insufficient_data",
            verdict=verdict.verdict,
            reason="Could not build verification task.",
        )

    verification_task_id, verification_offer, verification_capsule = built
    # Persist the verification task locally (task_offers + task_capsules with
    # verification_of_task_id, plus trace + "offered" transition) so it is a real,
    # inspectable job that the idempotency guard above can see — not just an outbound
    # broadcast. Best-effort: a local persist failure still lets the request go out.
    try:
        from network.assist_router import _store_task_offer

        _store_task_offer(verification_offer, verification_capsule)
    except Exception as exc:
        audit_logger.log(
            "consensus_verification_persist_failed",
            target_id=task_id,
            target_type="task",
            details={"verification_task_id": verification_task_id, "error": str(exc)},
            trace_id=task_id,
        )
    broadcast_task_offer(
        offer_payload=verification_offer.model_dump(mode="json"),
        required_capabilities=["validation", "ranking"],
        exclude_host_group_hint_hash=exclude_host_group_hint_hash,
        limit=8,
    )

    audit_logger.log(
        "consensus_verification_requested",
        target_id=task_id,
        target_type="task",
        details={
            "verification_task_id": verification_task_id,
            "best_score": round(best.score, 4),
            "second_score": round(second.score, 4),
            "margin": round(margin, 4),
            "verdict": verdict.verdict,
        },
        trace_id=task_id,
    )

    return ConsensusDecision(
        task_id=task_id,
        action="verification_requested",
        verification_task_id=verification_task_id,
        verdict=verdict.verdict,
        reason="Conflict or insufficient evidence detected; independent verification was requested.",
    )
