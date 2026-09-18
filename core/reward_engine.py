from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import audit_logger, fraud_engine, policy_engine, scoreboard_engine
from core.contribution_proof import append_contribution_proof_receipt
from core.credit_ledger import get_escrow_for_task, release_escrow_to_helper
from storage.db import get_connection
from storage.migrations import run_migrations


@dataclass
class RewardComputation:
    score: float
    points_awarded: int
    wnull_pending: int
    outcome: str  # pending | rejected
    reasons: list[str]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _reward_credit_receipt_id(entry_id: str) -> str:
    return f"contribution_release:{entry_id}"


def _parse_dt(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _finality_target_depth() -> int:
    configured = int(policy_engine.get("economics.contribution_finality_target_depth", 2) or 2)
    return max(1, configured)


def _finality_quiet_hours() -> float:
    configured = float(policy_engine.get("economics.contribution_finality_quiet_hours", 6.0) or 6.0)
    return max(0.0, configured)


def _max_abuse_signal(task_id: str, helper_peer_id: str) -> float:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(severity), 0) AS max_sev
            FROM anti_abuse_signals
            WHERE task_id = ?
              AND (peer_id = ? OR related_peer_id = ?)
            """,
            (task_id, helper_peer_id, helper_peer_id),
        ).fetchone()
        return float(row["max_sev"] or 0.0) if row else 0.0
    finally:
        conn.close()


def _has_blocking_task_review(task_id: str, helper_peer_id: str) -> bool:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT outcome, harmful_flag
            FROM task_reviews
            WHERE task_id = ? AND helper_peer_id = ?
            """,
            (task_id, helper_peer_id),
        ).fetchall()
    except Exception:
        return False
    finally:
        conn.close()

    for row in rows:
        outcome = str(row["outcome"] or "").strip().lower()
        harmful = int(row["harmful_flag"] or 0) == 1
        if harmful or outcome in {"rejected", "harmful", "failed"}:
            return True
    return False


def _task_review_confirmation_summary(task_id: str, helper_peer_id: str, *, parent_peer_id: str = "") -> dict[str, object]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT reviewer_peer_id, outcome, harmful_flag
            FROM task_reviews
            WHERE task_id = ? AND helper_peer_id = ?
            """,
            (task_id, helper_peer_id),
        ).fetchall()
    except Exception:
        return {
            "reviewer_count": 0,
            "positive_review_count": 0,
            "negative_review_count": 0,
            "external_positive_review_count": 0,
            "parent_positive_review": False,
            "review_support_score": 0.0,
            "review_quorum_reached": False,
        }
    finally:
        conn.close()

    positive_reviewers: set[str] = set()
    negative_reviewers: set[str] = set()
    parent_positive_review = False
    for row in rows:
        reviewer_peer_id = str(row["reviewer_peer_id"] or "").strip()
        outcome = str(row["outcome"] or "").strip().lower()
        harmful = int(row["harmful_flag"] or 0) == 1
        if harmful or outcome in {"rejected", "harmful", "failed"}:
            if reviewer_peer_id:
                negative_reviewers.add(reviewer_peer_id)
            continue
        if outcome in {"accepted", "approved", "reviewed", "partial"}:
            if reviewer_peer_id:
                positive_reviewers.add(reviewer_peer_id)
            if reviewer_peer_id and reviewer_peer_id == str(parent_peer_id or "").strip():
                parent_positive_review = True
    reviewer_ids = {item for item in positive_reviewers | negative_reviewers if item}
    positive_count = len(positive_reviewers)
    negative_count = len(negative_reviewers)
    denominator = positive_count + negative_count
    review_support_score = round(positive_count / max(1, denominator), 4) if denominator else 0.0
    external_positive_review_count = len(
        {item for item in positive_reviewers if item and item != str(parent_peer_id or "").strip()}
    )
    return {
        "reviewer_count": len(reviewer_ids),
        "positive_review_count": positive_count,
        "negative_review_count": negative_count,
        "external_positive_review_count": external_positive_review_count,
        "parent_positive_review": parent_positive_review,
        "review_support_score": review_support_score,
        "review_quorum_reached": positive_count > 0 and positive_count >= negative_count,
    }


def _trust_for_peer(peer_id: str) -> float:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT trust_score FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return float(row["trust_score"]) if row else 0.50
    finally:
        conn.close()


def _host_group_hint_for_peer(peer_id: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT host_group_hint_hash FROM agent_capabilities WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return str(row["host_group_hint_hash"]) if row and row["host_group_hint_hash"] else None
    finally:
        conn.close()


def _task_reward_hint(task_id: str) -> tuple[int, int]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT reward_hint_json FROM task_offers WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if not row:
            return 10, 5
        data = json.loads(row["reward_hint_json"] or "{}")
        return int(data.get("points", 10)), int(data.get("wnull_pending", 5))
    except Exception:
        return 10, 5
    finally:
        conn.close()


def _fraud_window_hours(task_complexity: float) -> int:
    # 0.0 -> 6h, 1.0 -> 48h
    return round(6 + (42 * _clamp(task_complexity)))


def _calc_assist_score(
    *,
    helpfulness_score: float,
    quality_score: float,
    parent_trust: float,
    helper_trust: float,
    task_complexity: float,
    timeliness: float,
    novelty: float,
    validator_confirmation: float,
    duplication_penalty: float,
    pair_farming_penalty: float,
    harmful_penalty: float,
) -> float:
    raw = (
        (0.30 * _clamp(helpfulness_score))
        + (0.20 * _clamp(quality_score))
        + (0.15 * _clamp(parent_trust))
        + (0.10 * _clamp(helper_trust))
        + (0.10 * _clamp(task_complexity))
        + (0.05 * _clamp(timeliness))
        + (0.05 * _clamp(novelty))
        + (0.05 * _clamp(validator_confirmation))
        - (0.25 * _clamp(duplication_penalty))
        - (0.35 * _clamp(pair_farming_penalty))
        - (0.60 * _clamp(harmful_penalty))
    )
    return _clamp(raw)


def create_pending_assist_reward(
    *,
    task_id: str,
    parent_peer_id: str,
    helper_peer_id: str,
    helpfulness_score: float,
    quality_score: float,
    task_complexity: float = 0.50,
    timeliness: float = 1.0,
    novelty: float = 0.50,
    validator_confirmation: float = 0.0,
    harmful: bool = False,
    result_hash: str | None = None,
) -> RewardComputation:
    parent_trust = _trust_for_peer(parent_peer_id)
    helper_trust = _trust_for_peer(helper_peer_id)

    parent_host = _host_group_hint_for_peer(parent_peer_id)
    helper_host = _host_group_hint_for_peer(helper_peer_id)

    assessment = fraud_engine.assess_assist_reward(
        task_id=task_id,
        parent_peer_id=parent_peer_id,
        helper_peer_id=helper_peer_id,
        parent_host_group_hint_hash=parent_host,
        helper_host_group_hint_hash=helper_host,
        result_hash=result_hash,
    )

    duplication_penalty = 1.0 if "duplicate_result" in assessment.reasons else 0.0
    pair_penalty = assessment.pair_penalty
    harmful_penalty = 1.0 if harmful else 0.0

    score = _calc_assist_score(
        helpfulness_score=helpfulness_score,
        quality_score=quality_score,
        parent_trust=parent_trust,
        helper_trust=helper_trust,
        task_complexity=task_complexity,
        timeliness=timeliness,
        novelty=novelty,
        validator_confirmation=validator_confirmation,
        duplication_penalty=duplication_penalty,
        pair_farming_penalty=pair_penalty,
        harmful_penalty=harmful_penalty,
    )

    base_points, _base_wnull = _task_reward_hint(task_id)

    if assessment.reject_reward or harmful or score <= 0.0:
        points_awarded = 0
        wnull_pending = 0
        compute_credits_pending = 0.0
        outcome = "rejected"
    else:
        points_awarded = math.floor(base_points * score)
        wnull_pending = 0  # Frozen: scoreboard-first economy
        # Credits are held as pending until the fraud window clears.
        compute_credits_pending = float(score)
        outcome = "pending"

    entry_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    fraud_window_end = now + timedelta(hours=_fraud_window_hours(task_complexity))
    finality_target = _finality_target_depth()
    finality_state = "pending" if outcome == "pending" else "rejected"

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO contribution_ledger (
                entry_id, task_id, helper_peer_id, parent_peer_id,
                contribution_type, outcome, helpfulness_score,
                points_awarded, wnull_pending, wnull_released,
                compute_credits_pending, compute_credits_released,
                finality_state, finality_depth, finality_target,
                confirmed_at, finalized_at,
                parent_host_group_hint_hash, helper_host_group_hint_hash,
                slashed_flag, fraud_window_end_ts, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?, NULL, NULL, ?, ?, 0, ?, ?, ?)
            """,
            (
                entry_id,
                task_id,
                helper_peer_id,
                parent_peer_id,
                "assist",
                outcome,
                float(helpfulness_score),
                points_awarded,
                wnull_pending,
                compute_credits_pending,
                finality_state,
                0,
                finality_target,
                parent_host,
                helper_host,
                fraud_window_end.isoformat(),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "reward_created",
        target_id=task_id,
        target_type="task",
        details={
            "entry_id": entry_id,
            "outcome": outcome,
            "finality_state": finality_state,
            "score": score,
            "points_awarded": points_awarded,
            "wnull_pending": wnull_pending,
            "reasons": assessment.reasons,
        },
    )
    append_contribution_proof_receipt(
        entry_id=entry_id,
        task_id=task_id,
        helper_peer_id=helper_peer_id,
        parent_peer_id=parent_peer_id,
        stage=finality_state,
        outcome=outcome,
        finality_state=finality_state,
        finality_depth=0,
        finality_target=finality_target,
        compute_credits=compute_credits_pending,
        points_awarded=points_awarded,
        challenge_reason=",".join(assessment.reasons) if outcome == "rejected" else "",
        evidence={
            "score": round(score, 4),
            "reasons": list(assessment.reasons),
            "fraud_window_end_ts": fraud_window_end.isoformat(),
            "review_confirmation": _task_review_confirmation_summary(
                task_id,
                helper_peer_id,
                parent_peer_id=parent_peer_id,
            ),
        },
        created_at=now.isoformat(),
    )

    # Phase 20: Award scoreboard points instead of tokens
    if outcome == "pending":
        scoreboard_engine.award_provider_score(
            peer_id=helper_peer_id,
            task_id=task_id,
            quality=score,
            helpfulness=helpfulness_score,
            outcome="accepted" if score >= 0.70 else "partial",
        )

    return RewardComputation(
        score=score,
        points_awarded=points_awarded,
        wnull_pending=wnull_pending,
        outcome=outcome,
        reasons=assessment.reasons,
    )


def release_mature_pending_rewards(limit: int = 100) -> int:
    released = 0
    run_migrations()

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT entry_id
            FROM contribution_ledger
            WHERE outcome = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        entry_id = row["entry_id"]

        if not fraud_engine.can_release_pending_entry(entry_id):
            continue

        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT entry_id, task_id, helper_peer_id, parent_peer_id, outcome,
                       points_awarded, wnull_pending, compute_credits_pending, finality_target
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
            if not current or str(current["outcome"] or "") != "pending":
                conn.rollback()
                continue
            pending_amount = int(current["wnull_pending"] or 0)
            score_amount = max(0.0, float(current["compute_credits_pending"] or 0.0))
            finality_target = max(1, int(current["finality_target"] or _finality_target_depth()))
            next_finality_state = "finalized" if finality_target <= 1 else "confirmed"
            next_finality_depth = finality_target if next_finality_state == "finalized" else 1
            reward_receipt_id = _reward_credit_receipt_id(entry_id)
            task_id_str = str(current["task_id"] or "")
            helper_peer = str(current["helper_peer_id"] or "")

            escrow = get_escrow_for_task(task_id_str)
            if escrow and escrow["remaining"] > 0 and score_amount > 0:
                payout = round(score_amount * escrow["total_escrowed"], 4)
                credit_amount = min(payout, escrow["remaining"])
                release_escrow_to_helper(
                    task_id_str,
                    helper_peer,
                    credit_amount,
                    receipt_id=reward_receipt_id,
                )
            else:
                credit_amount = score_amount
                if credit_amount > 0.0:
                    existing_credit = conn.execute(
                        "SELECT 1 FROM compute_credit_ledger WHERE receipt_id = ? LIMIT 1",
                        (reward_receipt_id,),
                    ).fetchone()
                    if not existing_credit:
                        conn.execute(
                            """
                            INSERT INTO compute_credit_ledger (
                                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                            ) VALUES (?, ?, ?, ?, 'simulated', ?)
                            """,
                            (helper_peer, credit_amount, f"confirmed_contribution:{task_id_str}", reward_receipt_id, _utcnow()),
                        )
            conn.execute(
                """
                UPDATE contribution_ledger
                SET outcome = 'released',
                    wnull_released = ?,
                    wnull_pending = 0,
                    compute_credits_released = ?,
                    compute_credits_pending = 0,
                    finality_state = ?,
                    finality_depth = ?,
                    confirmed_at = COALESCE(confirmed_at, ?),
                    finalized_at = CASE WHEN ? = 'finalized' THEN COALESCE(finalized_at, ?) ELSE finalized_at END,
                    updated_at = ?
                WHERE entry_id = ?
                """,
                (
                    pending_amount,
                    credit_amount,
                    next_finality_state,
                    next_finality_depth,
                    _utcnow(),
                    next_finality_state,
                    _utcnow(),
                    _utcnow(),
                    entry_id,
                ),
            )
            conn.commit()
            released += 1
            review_confirmation = _task_review_confirmation_summary(
                str(current["task_id"] or ""),
                str(current["helper_peer_id"] or ""),
                parent_peer_id=str(current["parent_peer_id"] or ""),
            )
            audit_logger.log(
                "reward_confirmed",
                target_id=str(current["task_id"] or entry_id),
                target_type="task",
                details={
                    "entry_id": entry_id,
                    "helper_peer_id": str(current["helper_peer_id"] or ""),
                    "finality_state": next_finality_state,
                    "finality_depth": next_finality_depth,
                    "compute_credits_released": credit_amount,
                    "review_confirmation": review_confirmation,
                },
            )
            append_contribution_proof_receipt(
                entry_id=entry_id,
                task_id=str(current["task_id"] or ""),
                helper_peer_id=str(current["helper_peer_id"] or ""),
                parent_peer_id=str(current["parent_peer_id"] or ""),
                stage=next_finality_state,
                outcome="released",
                finality_state=next_finality_state,
                finality_depth=next_finality_depth,
                finality_target=finality_target,
                compute_credits=credit_amount,
                points_awarded=int(current["points_awarded"] or 0),
                evidence={
                    "released_from_pending": True,
                    "review_confirmation": review_confirmation,
                },
            )
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    return released


def finalize_confirmed_rewards(limit: int = 100) -> int:
    finalized = 0
    run_migrations()
    quiet_hours = _finality_quiet_hours()
    threshold = datetime.now(timezone.utc) - timedelta(hours=quiet_hours)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT entry_id, task_id, helper_peer_id, finality_target, confirmed_at
            FROM contribution_ledger
            WHERE outcome = 'released'
              AND finality_state = 'confirmed'
            ORDER BY confirmed_at ASC, created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        entry_id = str(row["entry_id"] or "")
        task_id = str(row["task_id"] or "")
        helper_peer_id = str(row["helper_peer_id"] or "")
        confirmed_at = _parse_dt(row["confirmed_at"])
        if confirmed_at is None or confirmed_at > threshold:
            continue

        if _has_blocking_task_review(task_id, helper_peer_id) or _max_abuse_signal(task_id, helper_peer_id) >= 0.90:
            fraud_engine.slash_entry(entry_id, reason="finality_rejected", severity=1.0)
            continue

        conn = get_connection()
        try:
            now = _utcnow()
            current = conn.execute(
                """
                SELECT outcome, finality_state, finality_target, parent_peer_id
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
            if not current:
                continue
            if str(current["outcome"] or "") != "released" or str(current["finality_state"] or "") != "confirmed":
                continue
            target_depth = max(1, int(current["finality_target"] or row["finality_target"] or _finality_target_depth()))
            conn.execute(
                """
                UPDATE contribution_ledger
                SET finality_state = 'finalized',
                    finality_depth = ?,
                    finalized_at = COALESCE(finalized_at, ?),
                    updated_at = ?
                WHERE entry_id = ?
                """,
                (target_depth, now, now, entry_id),
            )
            conn.commit()
            finalized += 1
            review_confirmation = _task_review_confirmation_summary(
                task_id,
                helper_peer_id,
                parent_peer_id=str(current["parent_peer_id"] or ""),
            )
            audit_logger.log(
                "reward_finalized",
                target_id=task_id or entry_id,
                target_type="task",
                details={
                    "entry_id": entry_id,
                    "helper_peer_id": helper_peer_id,
                    "finality_depth": target_depth,
                    "review_confirmation": review_confirmation,
                },
            )
            append_contribution_proof_receipt(
                entry_id=entry_id,
                task_id=task_id,
                helper_peer_id=helper_peer_id,
                parent_peer_id=str(current["parent_peer_id"] or ""),
                stage="finalized",
                outcome="released",
                finality_state="finalized",
                finality_depth=target_depth,
                finality_target=target_depth,
                evidence={
                    "quiet_hours": quiet_hours,
                    "review_confirmation": review_confirmation,
                },
            )
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    return finalized
