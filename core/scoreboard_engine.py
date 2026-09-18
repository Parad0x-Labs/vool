from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import audit_logger
from storage.db import get_connection

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

# Provider deltas
PROVIDER_ACCEPTED_MULTIPLIER = 10.0   # quality × 10
PROVIDER_PARTIAL_MULTIPLIER  =  5.0   # quality × 5
PROVIDER_SLASH_PENALTY       = 20.0
PROVIDER_HARMFUL_PENALTY     = 30.0

# Validator deltas
VALIDATOR_CORRECT_REVIEW     =  8.0
VALIDATOR_INCORRECT_REVIEW   = -4.0

# Trust deltas
TRUST_ACCEPTED_BONUS         =  0.5
TRUST_PARTIAL_BONUS          =  0.1
TRUST_CORRECT_REVIEW_BONUS   =  0.3
TRUST_INCORRECT_REVIEW_PENALTY = -0.2
TRUST_SLASH_PENALTY          = -2.0
TRUST_HARMFUL_PENALTY        = -3.0

# Tier thresholds (provider score)
TIERS = [
    (1000, "Elite"),
    ( 200, "Trusted Provider"),
    (  50, "Contributor"),
    (   0, "Newcomer"),
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: Any, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _finality_state_expression() -> str:
    return """
    CASE
        WHEN finality_state IS NOT NULL AND TRIM(finality_state) != '' THEN LOWER(finality_state)
        WHEN outcome = 'pending' THEN 'pending'
        WHEN outcome = 'released' THEN 'confirmed'
        WHEN outcome = 'slashed' THEN 'slashed'
        WHEN outcome IN ('rejected', 'harmful', 'failed') THEN 'rejected'
        ELSE 'pending'
    END
    """


def _compute_glory_score(
    *,
    provider: float,
    validator: float,
    trust: float,
    pending_work_count: int,
    confirmed_work_count: int,
    finalized_work_count: int,
    rejected_work_count: int,
    slashed_work_count: int,
    released_compute_credits: float,
) -> float:
    score = (
        (finalized_work_count * 20.0)
        + (confirmed_work_count * 8.0)
        + max(0.0, released_compute_credits) * 10.0
        + max(0.0, provider) * 0.20
        + max(0.0, validator) * 0.10
        + max(0.0, trust) * 2.0
        + max(0, pending_work_count) * 0.25
        - max(0, rejected_work_count) * 6.0
        - max(0, slashed_work_count) * 12.0
    )
    return round(max(0.0, score), 3)


# ---------------------------------------------------------------------------
# Core write operations
# ---------------------------------------------------------------------------

def _insert_delta(
    *,
    peer_id: str,
    score_type: str,
    delta: float,
    reason: str,
    related_task_id: str | None = None,
    related_peer_id: str | None = None,
    season: int = 1,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scoreboard (
                entry_id, peer_id, score_type, delta,
                reason, related_task_id, related_peer_id,
                season, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                peer_id,
                score_type,
                delta,
                reason,
                related_task_id,
                related_peer_id,
                season,
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public scoring API
# ---------------------------------------------------------------------------

def zero_out_provider(peer_id: str, reason: str, related_task_id: str | None = None) -> None:
    """
    Phase 28 Anti-Cheat: Annihilates a provider's score and blacklists their trust.
    Called when caught returning fraudulent results during a spot-check.
    """
    current_board = get_peer_scoreboard(peer_id)
    if current_board.provider > 0:
        slash_score(peer_id, "provider", current_board.provider, f"zeroed:{reason}", related_task_id)

    # Devastating trust penalty ensuring they can never re-enter the active pool
    slash_score(peer_id, "trust", 999.0, f"blacklist:{reason}", related_task_id)



def award_provider_score(
    peer_id: str,
    task_id: str,
    quality: float,
    helpfulness: float,
    outcome: str,
) -> float:
    """
    Awards Provider Score based on review outcome.
    Returns the delta applied.
    """
    quality = max(0.0, min(1.0, quality))

    if outcome == "accepted":
        delta = PROVIDER_ACCEPTED_MULTIPLIER * quality
        trust_delta = TRUST_ACCEPTED_BONUS
    elif outcome == "partial":
        delta = PROVIDER_PARTIAL_MULTIPLIER * quality
        trust_delta = TRUST_PARTIAL_BONUS
    else:
        return 0.0

    _insert_delta(
        peer_id=peer_id,
        score_type="provider",
        delta=delta,
        reason=f"work_{outcome}",
        related_task_id=task_id,
    )

    _insert_delta(
        peer_id=peer_id,
        score_type="trust",
        delta=trust_delta,
        reason=f"work_{outcome}_trust",
        related_task_id=task_id,
    )

    audit_logger.log(
        "scoreboard_provider_awarded",
        target_id=peer_id,
        target_type="peer",
        details={"task_id": task_id, "delta": delta, "outcome": outcome},
    )

    return delta


def award_validator_score(
    peer_id: str,
    task_id: str,
    review_correct: bool,
) -> float:
    """
    Awards Validator Score for review participation.
    Returns the delta applied.
    """
    if review_correct:
        delta = VALIDATOR_CORRECT_REVIEW
        trust_delta = TRUST_CORRECT_REVIEW_BONUS
        reason = "correct_review"
    else:
        delta = VALIDATOR_INCORRECT_REVIEW
        trust_delta = TRUST_INCORRECT_REVIEW_PENALTY
        reason = "incorrect_review"

    _insert_delta(
        peer_id=peer_id,
        score_type="validator",
        delta=delta,
        reason=reason,
        related_task_id=task_id,
    )

    _insert_delta(
        peer_id=peer_id,
        score_type="trust",
        delta=trust_delta,
        reason=f"{reason}_trust",
        related_task_id=task_id,
    )

    audit_logger.log(
        "scoreboard_validator_awarded",
        target_id=peer_id,
        target_type="peer",
        details={"task_id": task_id, "delta": delta, "correct": review_correct},
    )

    return delta


def slash_score(
    peer_id: str,
    score_type: str,
    amount: float,
    reason: str,
    related_task_id: str | None = None,
) -> None:
    """
    Applies a negative delta (slash) to the specified score type.
    """
    _insert_delta(
        peer_id=peer_id,
        score_type=score_type,
        delta=-abs(amount),
        reason=f"slashed:{reason}",
        related_task_id=related_task_id,
    )

    # Also hit trust
    trust_penalty = TRUST_SLASH_PENALTY if "harmful" not in reason else TRUST_HARMFUL_PENALTY
    _insert_delta(
        peer_id=peer_id,
        score_type="trust",
        delta=trust_penalty,
        reason=f"slashed_trust:{reason}",
        related_task_id=related_task_id,
    )

    audit_logger.log(
        "scoreboard_slashed",
        target_id=peer_id,
        target_type="peer",
        details={"score_type": score_type, "amount": amount, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Read / query operations
# ---------------------------------------------------------------------------

@dataclass
class PeerScoreboard:
    provider: float
    validator: float
    trust: float
    tier: str
    glory_score: float = 0.0
    pending_work_count: int = 0
    confirmed_work_count: int = 0
    finalized_work_count: int = 0
    rejected_work_count: int = 0
    slashed_work_count: int = 0
    finality_ratio: float = 0.0


def get_peer_scoreboard(peer_id: str, season: int | None = None, db_path: str | Path | None = None) -> PeerScoreboard:
    """
    Aggregate all score deltas for a peer and compute their tier.
    """
    conn = get_connection(db_path) if db_path is not None else get_connection()
    try:
        where = "WHERE peer_id = ?"
        params: list = [peer_id]
        if season is not None:
            where += " AND season = ?"
            params.append(season)

        scores: dict[str, float] = {"provider": 0.0, "validator": 0.0, "trust": 0.0}
        if _table_exists(conn, "scoreboard"):
            rows = conn.execute(
                f"""
                SELECT score_type, COALESCE(SUM(delta), 0) AS total
                FROM scoreboard
                {where}
                GROUP BY score_type
                """,
                tuple(params),
            ).fetchall()
            for row in rows:
                scores[row["score_type"]] = float(row["total"])

        # Determine tier from provider score
        tier = "Newcomer"
        for threshold, label in TIERS:
            if scores["provider"] >= threshold:
                tier = label
                break

        pending_work_count = 0
        confirmed_work_count = 0
        finalized_work_count = 0
        rejected_work_count = 0
        slashed_work_count = 0
        released_compute_credits = 0.0
        if _table_exists(conn, "contribution_ledger"):
            finality_state = _finality_state_expression()
            contribution = conn.execute(
                f"""
                SELECT
                    SUM(CASE WHEN {finality_state} = 'pending' THEN 1 ELSE 0 END) AS pending_work_count,
                    SUM(CASE WHEN {finality_state} = 'confirmed' THEN 1 ELSE 0 END) AS confirmed_work_count,
                    SUM(CASE WHEN {finality_state} = 'finalized' THEN 1 ELSE 0 END) AS finalized_work_count,
                    SUM(CASE WHEN {finality_state} = 'rejected' THEN 1 ELSE 0 END) AS rejected_work_count,
                    SUM(CASE WHEN {finality_state} = 'slashed' THEN 1 ELSE 0 END) AS slashed_work_count,
                    COALESCE(SUM(compute_credits_released), 0) AS released_compute_credits
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                """,
                (peer_id,),
            ).fetchone()
            if contribution:
                pending_work_count = int(contribution["pending_work_count"] or 0)
                confirmed_work_count = int(contribution["confirmed_work_count"] or 0)
                finalized_work_count = int(contribution["finalized_work_count"] or 0)
                rejected_work_count = int(contribution["rejected_work_count"] or 0)
                slashed_work_count = int(contribution["slashed_work_count"] or 0)
                released_compute_credits = float(contribution["released_compute_credits"] or 0.0)

        finality_denominator = finalized_work_count + confirmed_work_count + rejected_work_count + slashed_work_count
        finality_ratio = round(finalized_work_count / max(1, finality_denominator), 4)
        glory_score = _compute_glory_score(
            provider=scores["provider"],
            validator=scores["validator"],
            trust=scores["trust"],
            pending_work_count=pending_work_count,
            confirmed_work_count=confirmed_work_count,
            finalized_work_count=finalized_work_count,
            rejected_work_count=rejected_work_count,
            slashed_work_count=slashed_work_count,
            released_compute_credits=released_compute_credits,
        )

        return PeerScoreboard(
            provider=scores["provider"],
            validator=scores["validator"],
            trust=scores["trust"],
            tier=tier,
            glory_score=glory_score,
            pending_work_count=pending_work_count,
            confirmed_work_count=confirmed_work_count,
            finalized_work_count=finalized_work_count,
            rejected_work_count=rejected_work_count,
            slashed_work_count=slashed_work_count,
            finality_ratio=finality_ratio,
        )
    finally:
        conn.close()


def get_season_leaderboard(
    score_type: str = "provider",
    season: int = 1,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Returns the top-N peers for a given score type and season.
    """
    conn = get_connection(db_path) if db_path is not None else get_connection()
    try:
        if not _table_exists(conn, "scoreboard"):
            return []
        rows = conn.execute(
            """
            SELECT peer_id, SUM(delta) AS total
            FROM scoreboard
            WHERE score_type = ? AND season = ?
            GROUP BY peer_id
            ORDER BY total DESC
            LIMIT ?
            """,
            (score_type, season, limit),
        ).fetchall()

        return [{"peer_id": r["peer_id"], "score": float(r["total"])} for r in rows]
    finally:
        conn.close()


def get_glory_leaderboard(limit: int = 20, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = get_connection(db_path) if db_path is not None else get_connection()
    try:
        peer_ids: list[str] = []
        if _table_exists(conn, "contribution_ledger"):
            rows = conn.execute(
                """
                SELECT DISTINCT helper_peer_id AS peer_id
                FROM contribution_ledger
                WHERE helper_peer_id IS NOT NULL AND TRIM(helper_peer_id) != ''
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(limit * 8, 64),),
            ).fetchall()
            peer_ids.extend(str(row["peer_id"]) for row in rows)
        if _table_exists(conn, "scoreboard"):
            scoreboard_rows = conn.execute(
                """
                SELECT DISTINCT peer_id
                FROM scoreboard
                WHERE peer_id IS NOT NULL AND TRIM(peer_id) != ''
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(limit * 8, 64),),
            ).fetchall()
            peer_ids.extend(str(row["peer_id"]) for row in scoreboard_rows)
    finally:
        conn.close()

    seen: set[str] = set()
    leaderboard: list[dict[str, Any]] = []
    for peer_id in peer_ids:
        if not peer_id or peer_id in seen:
            continue
        seen.add(peer_id)
        board = get_peer_scoreboard(peer_id, db_path=db_path)
        leaderboard.append(
            {
                "peer_id": peer_id,
                "glory_score": board.glory_score,
                "provider_score": board.provider,
                "validator_score": board.validator,
                "trust_score": board.trust,
                "tier": board.tier,
                "pending_work_count": board.pending_work_count,
                "confirmed_work_count": board.confirmed_work_count,
                "finalized_work_count": board.finalized_work_count,
                "rejected_work_count": board.rejected_work_count,
                "slashed_work_count": board.slashed_work_count,
                "finality_ratio": board.finality_ratio,
            }
        )
    leaderboard.sort(
        key=lambda row: (
            float(row.get("glory_score") or 0.0),
            int(row.get("finalized_work_count") or 0),
            float(row.get("provider_score") or 0.0),
            float(row.get("trust_score") or 0.0),
        ),
        reverse=True,
    )
    return leaderboard[:limit]
