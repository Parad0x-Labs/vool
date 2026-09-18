from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.knowledge_possession_challenge import (
    issue_knowledge_possession_challenge,
    verify_knowledge_possession_response,
)
from core.meet_and_greet_models import KnowledgeChallengeIssueRequest, KnowledgeChallengeVerifyRequest
from network.signer import get_local_peer_id
from storage.knowledge_holder_audit_store import (
    audits_for_holder,
    create_holder_audit,
    latest_audit_for_challenge,
    update_holder_audit,
)
from storage.replica_table import all_holders, mark_holder_audit_result


@dataclass(frozen=True)
class HolderFreshnessPolicy:
    stale_after_seconds: int = 1800
    max_success_age_seconds: int = 3600
    max_sample_size: int = 8
    suspicious_failure_threshold: int = 2


@dataclass(frozen=True)
class HolderAuditDecision:
    due: bool
    reason: str
    freshness_age_seconds: int


def assess_holder_audit_need(
    holder: dict[str, Any],
    *,
    policy: HolderFreshnessPolicy | None = None,
    now: datetime | None = None,
) -> HolderAuditDecision:
    cfg = policy or HolderFreshnessPolicy()
    active_now = now or datetime.now(timezone.utc)
    freshness_ts = _parse_dt(str(holder.get("freshness_ts") or ""), active_now)
    freshness_age_seconds = max(0, int((active_now - freshness_ts).total_seconds()))
    last_proved_at = str(holder.get("last_proved_at") or "")
    if not last_proved_at:
        return HolderAuditDecision(True, "never_proved", freshness_age_seconds)
    proved_dt = _parse_dt(last_proved_at, active_now)
    proof_age = max(0, int((active_now - proved_dt).total_seconds()))
    if freshness_age_seconds >= cfg.stale_after_seconds:
        return HolderAuditDecision(True, "freshness_stale", freshness_age_seconds)
    if proof_age >= cfg.max_success_age_seconds:
        return HolderAuditDecision(True, "proof_too_old", freshness_age_seconds)
    if int(holder.get("failed_audits") or 0) >= cfg.suspicious_failure_threshold:
        return HolderAuditDecision(True, "repeated_failures", freshness_age_seconds)
    return HolderAuditDecision(False, "fresh_enough", freshness_age_seconds)


def select_holders_for_sampling(
    *,
    policy: HolderFreshnessPolicy | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    cfg = policy or HolderFreshnessPolicy()
    rows = [row for row in all_holders(limit=2000) if str(row.get("status") or "") == "active"]
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        decision = assess_holder_audit_need(row, policy=cfg, now=now)
        if not decision.due:
            continue
        risk = int(row.get("failed_audits") or 0)
        ranked.append((risk, decision.freshness_age_seconds, row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for _, _, row in ranked[: limit or cfg.max_sample_size]]
    return selected


def start_sampling_audit(
    *,
    shard_id: str,
    holder_peer_id: str,
    requester_peer_id: str | None = None,
    trigger_reason: str = "scheduled_sampling",
    policy: HolderFreshnessPolicy | None = None,
) -> dict[str, Any]:
    cfg = policy or HolderFreshnessPolicy()
    requester = requester_peer_id or get_local_peer_id()
    holder = _holder_row(shard_id, holder_peer_id)
    if not holder:
        raise ValueError("Unknown active holder for sampling audit.")
    decision = assess_holder_audit_need(holder, policy=cfg)
    audit_id = create_holder_audit(
        shard_id=shard_id,
        holder_peer_id=holder_peer_id,
        requester_peer_id=requester,
        trigger_reason=trigger_reason if decision.due else "manual_check",
        status="pending",
        freshness_age_seconds=decision.freshness_age_seconds,
        metadata={"decision_reason": decision.reason},
    )
    challenge = issue_knowledge_possession_challenge(
        KnowledgeChallengeIssueRequest(
            shard_id=shard_id,
            holder_peer_id=holder_peer_id,
            requester_peer_id=requester,
        )
    )
    update_holder_audit(
        audit_id,
        status="challenge_issued",
        challenge_id=challenge.challenge_id,
        note=f"issued challenge for {decision.reason}",
    )
    return {"audit_id": audit_id, "challenge_id": challenge.challenge_id, "decision_reason": decision.reason}


def finalize_sampling_audit(
    *,
    verify_request: KnowledgeChallengeVerifyRequest,
) -> dict[str, Any]:
    challenge = verify_knowledge_possession_response(verify_request)
    audit = latest_audit_for_challenge(challenge.challenge_id)
    if not audit:
        raise ValueError("No sampling audit is attached to this challenge.")
    passed = challenge.status == "passed"
    mark_holder_audit_result(
        shard_id=challenge.shard_id,
        holder_peer_id=challenge.holder_peer_id,
        passed=passed,
        proved_at=datetime.now(timezone.utc).isoformat() if passed else None,
    )
    update_holder_audit(
        str(audit["audit_id"]),
        status="passed" if passed else "failed",
        note=challenge.verification_note,
        metadata={"challenge_status": challenge.status},
    )
    return {
        "audit_id": str(audit["audit_id"]),
        "challenge_id": challenge.challenge_id,
        "status": "passed" if passed else "failed",
    }


def holder_audit_snapshot(*, holder_peer_id: str) -> dict[str, Any]:
    rows = audits_for_holder(holder_peer_id, limit=50)
    return {
        "holder_peer_id": holder_peer_id,
        "audit_count": len(rows),
        "recent_audits": rows,
    }


def _holder_row(shard_id: str, holder_peer_id: str) -> dict[str, Any] | None:
    for row in all_holders(limit=2000):
        if str(row.get("shard_id") or "") == shard_id and str(row.get("holder_peer_id") or "") == holder_peer_id:
            return row
    return None


def _parse_dt(raw: str, fallback: datetime) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return fallback
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
