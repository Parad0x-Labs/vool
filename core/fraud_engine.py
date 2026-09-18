from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import audit_logger, scoreboard_engine
from core.contribution_proof import append_contribution_proof_receipt
from core.liquefy_bridge import stream_telemetry_event
from storage.db import get_connection


@dataclass
class FraudAssessment:
    reject_reward: bool
    risk_score: float
    pair_penalty: float
    reasons: list[str]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def record_signal(
    *,
    peer_id: str | None,
    related_peer_id: str | None,
    task_id: str | None,
    signal_type: str,
    severity: float,
    details: dict,
) -> None:
    severity = max(0.0, min(1.0, severity))

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO anti_abuse_signals (
                signal_id, peer_id, related_peer_id, task_id,
                signal_type, severity, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                peer_id,
                related_peer_id,
                task_id,
                signal_type,
                severity,
                json.dumps(details, sort_keys=True),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "anti_abuse_signal",
        target_id=task_id or peer_id,
        target_type="task" if task_id else "peer",
        details={
            "signal_type": signal_type,
            "severity": severity,
            "peer_id": peer_id,
            "related_peer_id": related_peer_id,
        },
    )

    stream_telemetry_event(
        event_type="ANTI_ABUSE_SIGNAL",
        target_id=task_id or peer_id or "unknown",
        details={
            "signal_type": signal_type,
            "severity": severity,
            "peer_id": peer_id,
            "related_peer_id": related_peer_id,
        },
    )


def _pair_count_rolling(parent_peer_id: str, helper_peer_id: str, hours: int = 24) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM contribution_ledger
            WHERE created_at >= ?
              AND (
                    (parent_peer_id = ? AND helper_peer_id = ?)
                 OR (parent_peer_id = ? AND helper_peer_id = ?)
              )
            """,
            (since, parent_peer_id, helper_peer_id, helper_peer_id, parent_peer_id),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _counterparty_diversity(peer_id: str, days: int = 7) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT CASE
                WHEN parent_peer_id = ? THEN helper_peer_id
                ELSE parent_peer_id
            END) AS cnt
            FROM contribution_ledger
            WHERE created_at >= ?
              AND (parent_peer_id = ? OR helper_peer_id = ?)
            """,
            (peer_id, since, peer_id, peer_id),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _duplicate_result_count(task_id: str, result_hash: str | None) -> int:
    if not result_hash:
        return 0
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM task_results
            WHERE task_id = ? AND result_hash = ?
            """,
            (task_id, result_hash),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def assess_assist_reward(
    *,
    task_id: str,
    parent_peer_id: str,
    helper_peer_id: str,
    parent_host_group_hint_hash: str | None,
    helper_host_group_hint_hash: str | None,
    result_hash: str | None = None,
) -> FraudAssessment:
    reasons: list[str] = []
    risk = 0.0
    pair_penalty = 0.0
    reject = False

    # 1) Direct self-farm
    if parent_peer_id == helper_peer_id:
        reject = True
        risk = 1.0
        reasons.append("self_farm_same_peer")
        record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="self_farm",
            severity=1.0,
            details={"mode": "same_peer"},
        )
        return FraudAssessment(reject_reward=reject, risk_score=risk, pair_penalty=1.0, reasons=reasons)

    # 2) Same host-group (best-effort same-box signal)
    if (
        parent_host_group_hint_hash
        and helper_host_group_hint_hash
        and parent_host_group_hint_hash == helper_host_group_hint_hash
    ):
        reject = True
        risk = max(risk, 0.95)
        reasons.append("same_host_group")
        record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="same_machine_cluster",
            severity=0.95,
            details={},
        )

    # 3) Pair farming
    pair_count = _pair_count_rolling(parent_peer_id, helper_peer_id, hours=24)
    if pair_count >= 12:
        pair_penalty = 0.85
        risk = max(risk, 0.85)
        reasons.append("heavy_pair_repetition")
    elif pair_count >= 8:
        pair_penalty = 0.50
        risk = max(risk, 0.60)
        reasons.append("pair_repetition")
    elif pair_count >= 4:
        pair_penalty = 0.20
        risk = max(risk, 0.30)
        reasons.append("light_pair_repetition")

    if pair_penalty > 0:
        record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="pair_farm",
            severity=min(1.0, pair_penalty),
            details={"pair_count_24h": pair_count},
        )

    # 4) Low-diversity ring behavior
    helper_div = _counterparty_diversity(helper_peer_id, days=7)
    parent_div = _counterparty_diversity(parent_peer_id, days=7)

    if pair_count >= 8 and helper_div <= 2 and parent_div <= 2:
        risk = max(risk, 0.90)
        reject = True
        reasons.append("closed_loop_ring")
        record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="ring_pattern",
            severity=0.90,
            details={
                "pair_count_24h": pair_count,
                "helper_diversity_7d": helper_div,
                "parent_diversity_7d": parent_div,
            },
        )

    # 5) Duplicate result spam
    dup_count = _duplicate_result_count(task_id, result_hash)
    if dup_count >= 2:
        risk = max(risk, 0.70)
        reasons.append("duplicate_result")
        record_signal(
            peer_id=helper_peer_id,
            related_peer_id=parent_peer_id,
            task_id=task_id,
            signal_type="duplicate",
            severity=0.70,
            details={"duplicate_count": dup_count},
        )

    # Hard reject threshold
    if risk >= 0.90:
        reject = True

    return FraudAssessment(
        reject_reward=reject,
        risk_score=max(0.0, min(1.0, risk)),
        pair_penalty=max(0.0, min(1.0, pair_penalty)),
        reasons=reasons,
    )


def can_release_pending_entry(entry_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT entry_id, task_id, helper_peer_id, outcome, slashed_flag, fraud_window_end_ts
            FROM contribution_ledger
            WHERE entry_id = ?
            LIMIT 1
            """,
            (entry_id,),
        ).fetchone()
        if not row:
            return False

        if row["outcome"] != "pending":
            return False
        if int(row["slashed_flag"]) == 1:
            return False

        fraud_end = _parse_dt(row["fraud_window_end_ts"])
        if not fraud_end or fraud_end > datetime.now(timezone.utc):
            return False

        sig = conn.execute(
            """
            SELECT COALESCE(MAX(severity), 0) AS max_sev
            FROM anti_abuse_signals
            WHERE task_id = ?
              AND (peer_id = ? OR related_peer_id = ?)
            """,
            (row["task_id"], row["helper_peer_id"], row["helper_peer_id"]),
        ).fetchone()

        max_sev = float(sig["max_sev"]) if sig else 0.0
        return max_sev < 0.90
    finally:
        conn.close()


def slash_entry(entry_id: str, reason: str, severity: float = 1.0) -> None:
    severity = max(0.0, min(1.0, severity))

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT task_id, helper_peer_id, parent_peer_id, compute_credits_released
            FROM contribution_ledger
            WHERE entry_id = ?
            LIMIT 1
            """,
            (entry_id,),
        ).fetchone()
        if not row:
            return

        conn.execute(
            """
            UPDATE contribution_ledger
            SET outcome = 'slashed',
                finality_state = 'slashed',
                finality_depth = 0,
                slashed_flag = 1,
                wnull_pending = 0,
                compute_credits_pending = 0,
                updated_at = ?
            WHERE entry_id = ?
            """,
            (_utcnow(), entry_id),
        )
        released_credits = max(0.0, float(row["compute_credits_released"] or 0.0))
        if released_credits > 0.0:
            conn.execute(
                """
                INSERT INTO compute_credit_ledger (
                    peer_id, amount, reason, receipt_id, settlement_mode, timestamp
                )
                SELECT ?, ?, ?, ?, 'simulated', ?
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM compute_credit_ledger
                    WHERE receipt_id = ?
                )
                """,
                (
                    row["helper_peer_id"],
                    -released_credits,
                    f"contribution_slash:{row['task_id']}",
                    f"contribution_slash:{entry_id}",
                    _utcnow(),
                    f"contribution_slash:{entry_id}",
                ),
            )
        conn.commit()

        append_contribution_proof_receipt(
            entry_id=entry_id,
            task_id=str(row["task_id"] or ""),
            helper_peer_id=str(row["helper_peer_id"] or ""),
            parent_peer_id=str(row["parent_peer_id"] or ""),
            stage="slashed",
            outcome="slashed",
            finality_state="slashed",
            finality_depth=0,
            finality_target=0,
            compute_credits=released_credits,
            challenge_reason=str(reason or "").strip(),
            evidence={"severity": severity, "released_credits_clawed_back": released_credits > 0.0},
        )

        record_signal(
            peer_id=row["helper_peer_id"],
            related_peer_id=row["parent_peer_id"],
            task_id=row["task_id"],
            signal_type="slashed_reward",
            severity=severity,
            details={"reason": reason, "entry_id": entry_id},
        )

        # Phase 20: Scoreboard slash
        scoreboard_engine.slash_score(
            peer_id=row["helper_peer_id"],
            score_type="provider",
            amount=scoreboard_engine.PROVIDER_SLASH_PENALTY,
            reason=reason,
            related_task_id=row["task_id"],
        )
    finally:
        conn.close()
