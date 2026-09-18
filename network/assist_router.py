from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import audit_logger
from core.capability_tokens import (
    issue_assignment_capability,
    remember_capability_token,
    revoke_capability_tokens_for_task,
)
from core.discovery_index import (
    delivery_targets_for_peer,
    peer_trust,
    record_bootstrap_presence,
    record_signed_peer_endpoint_observation,
    register_capability_ad,
    register_peer_endpoint_candidate,
    upsert_peer_minimal,
)
from core.idle_assist_policy import IdleAssistConfig, should_accept_offer
from core.liquefy_bridge import stream_telemetry_event
from core.task_capsule import TaskCapsule, verify_task_capsule
from core.task_state_machine import current_state, transition
from core.trace_id import ensure_trace
from network import signer
from network.assist_models import (
    CapabilityAd,
    TaskAssign,
    TaskClaim,
    TaskOffer,
    TaskProgress,
    TaskResult,
    TaskReview,
    validate_assist_payload,
)
from network.pow_hashcash import required_pow_difficulty, verify_pow
from network.protocol import (
    BlockFoundPayload,
    BlockPayloadMsg,
    FindBlockPayload,
    FindNodePayload,
    NodeFoundPayload,
    Protocol,
    RequestBlockPayload,
    encode_message,
)
from network.quarantine import note_peer_violation
from network.rate_limiter import allow as rate_allow
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_peer_id() -> str:
    """Resolve the local peer id lazily so signer reloads and test patches take effect."""
    return signer.get_local_peer_id()

def build_find_node_message(target_id: str) -> bytes:
    from network.protocol import FindNodePayload
    payload = FindNodePayload(target_id=target_id).model_dump(mode="json")
    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="FIND_NODE",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )

def build_node_found_message(target_id: str, closest_nodes: list[Any]) -> bytes:
    from network.protocol import NodeFoundEntry, NodeFoundPayload
    entries = []
    for n in closest_nodes:
        entries.append(NodeFoundEntry(peer_id=n.peer_id, ip=n.ip, port=n.port))
    payload = NodeFoundPayload(target_id=target_id, nodes=entries).model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="NODE_FOUND",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )


def build_find_block_message(block_hash: str) -> bytes:
    from network.protocol import FindBlockPayload
    payload = FindBlockPayload(block_hash=block_hash).model_dump(mode="json")
    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="FIND_BLOCK",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )

def build_block_found_message(block_hash: str, hosting_peers: list[Any]) -> bytes:
    from network.protocol import BlockFoundPayload, NodeFoundEntry
    entries = []
    for n in hosting_peers:
        entries.append(NodeFoundEntry(peer_id=n.peer_id, ip=n.ip, port=n.port))
    payload = BlockFoundPayload(block_hash=block_hash, hosting_peers=entries).model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="BLOCK_FOUND",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )

def build_block_payload_message(block_hash: str, data: bytes) -> bytes:
    from network.protocol import BlockPayloadMsg
    payload = BlockPayloadMsg(block_hash=block_hash, byte_hex=data.hex()).model_dump(mode="json")
    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="BLOCK_PAYLOAD",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )

@dataclass
class RouteResult:
    ok: bool
    reason: str
    generated_messages: list[bytes]


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True)


def _nonce() -> str:
    return uuid.uuid4().hex


def _extract_parent_task_ref(capsule: TaskCapsule) -> str | None:
    constraints = capsule.sanitized_context.known_constraints or []
    for item in constraints:
        if not isinstance(item, str):
            continue
        if item.startswith("parent_task_ref:"):
            value = item.split(":", 1)[1].strip()
            return value or None
    return None


def _extract_verification_of_task_id(capsule: TaskCapsule) -> str | None:
    constraints = capsule.sanitized_context.known_constraints or []
    for item in constraints:
        if not isinstance(item, str):
            continue
        if item.startswith("verification_of:"):
            value = item.split(":", 1)[1].strip()
            return value or None
    return None


def build_capability_ad_message(
    *,
    status: str,
    capabilities: list[str],
    compute_class: str,
    supported_models: list[str],
    capacity: int,
    trust_score: float,
    assist_filters: dict[str, Any],
    genesis_nonce: str,
    pow_difficulty: int = 4,
) -> bytes:
    agent_id = local_peer_id()
    payload = CapabilityAd(
        agent_id=agent_id,
        status=status,
        capabilities=capabilities,
        compute_class=compute_class,
        supported_models=supported_models,
        capacity=capacity,
        trust_score=trust_score,
        assist_filters=assist_filters,
        pow_difficulty=int(pow_difficulty),
        genesis_nonce=genesis_nonce,
        timestamp=datetime.now(timezone.utc),
    ).model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="CAPABILITY_AD",
        sender_peer_id=agent_id,
        nonce=_nonce(),
        payload=payload,
    )


def _store_task_offer(offer: TaskOffer, capsule: TaskCapsule) -> None:
    # A8 write fence (ERASE-dominance): a task brief carrying governed
    # WITHHELD/ERASED bytes is refused at the durable boundary.
    from core.finalization import writer_may_publish_public_text

    if not writer_may_publish_public_text(str(offer.summary or "")):
        raise ValueError("task offer refused: summary carries governed WITHHELD/ERASED bytes")
    trace = ensure_trace(offer.task_id, trace_id=offer.task_id)
    parent_task_ref = _extract_parent_task_ref(capsule)
    verification_of_task_id = _extract_verification_of_task_id(capsule)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json,
                max_helpers, priority, deadline_ts, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM task_offers WHERE task_id = ?), ?
            ), ?)
            """,
            (
                offer.task_id,
                offer.parent_agent_id,
                offer.capsule_id,
                offer.task_type,
                offer.subtask_type,
                offer.summary,
                capsule.capsule_hash,
                _json(offer.required_capabilities),
                _json(offer.reward_hint.model_dump()),
                offer.max_helpers,
                offer.priority,
                offer.deadline_ts.isoformat(),
                "open",
                offer.task_id,
                _utcnow(),
                _utcnow(),
            ),
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO task_capsules (
                capsule_id, task_id, parent_peer_id, capsule_hash, capsule_json,
                parent_task_ref, verification_of_task_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM task_capsules WHERE capsule_id = ?), ?
            ), ?)
            """,
            (
                capsule.capsule_id,
                offer.task_id,
                offer.parent_agent_id,
                capsule.capsule_hash,
                json.dumps(capsule.model_dump(mode="json"), sort_keys=True),
                parent_task_ref,
                verification_of_task_id,
                capsule.capsule_id,
                _utcnow(),
                _utcnow(),
            ),
        )

        conn.commit()
    finally:
        conn.close()

    transition(
        entity_type="subtask",
        entity_id=offer.task_id,
        to_state="offered",
        details={"parent_peer_id": offer.parent_agent_id, "task_type": offer.task_type},
        trace_id=trace.trace_id,
    )


def load_task_capsule_for_task(task_id: str) -> TaskCapsule | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT capsule_json
            FROM task_capsules
            WHERE task_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if not row:
            return None

        raw = json.loads(row["capsule_json"])
        return verify_task_capsule(raw)
    finally:
        conn.close()


def _store_task_claim(claim: TaskClaim) -> None:
    trace = ensure_trace(claim.task_id, trace_id=claim.task_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_claims (
                claim_id, task_id, helper_peer_id, declared_capabilities_json,
                current_load, host_group_hint_hash, status, claimed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT claimed_at FROM task_claims WHERE claim_id = ?), ?
            ), ?)
            """,
            (
                claim.claim_id,
                claim.task_id,
                claim.helper_agent_id,
                _json(claim.declared_capabilities),
                claim.current_load,
                claim.host_group_hint_hash,
                "pending",
                claim.claim_id,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stream_telemetry_event("TASK_CLAIM", claim.task_id, {"helper": claim.helper_agent_id})
    transition(
        entity_type="subtask",
        entity_id=claim.task_id,
        to_state="claimed",
        details={"helper_peer_id": claim.helper_agent_id},
        trace_id=trace.trace_id,
    )


def _store_task_assignment(assign: TaskAssign) -> None:
    trace = ensure_trace(assign.task_id, trace_id=assign.task_id)
    capability_token = dict(assign.capability_token or {})
    capability_token_id = str(capability_token.get("token_id") or "").strip() or None
    lease_expires_at = str(capability_token.get("expires_at") or "").strip() or None
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_assignments (
                assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id,
                assignment_mode, status, capability_token_id, lease_expires_at,
                last_progress_state, last_progress_note, assigned_at, updated_at,
                progress_updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT status FROM task_assignments WHERE assignment_id = ?), 'active'
            ), ?, ?, COALESCE((SELECT last_progress_state FROM task_assignments WHERE assignment_id = ?), ''),
               COALESCE((SELECT last_progress_note FROM task_assignments WHERE assignment_id = ?), ''),
               COALESCE((SELECT assigned_at FROM task_assignments WHERE assignment_id = ?), ?), ?,
               COALESCE((SELECT progress_updated_at FROM task_assignments WHERE assignment_id = ?), NULL),
               COALESCE((SELECT completed_at FROM task_assignments WHERE assignment_id = ?), NULL))
            """,
            (
                assign.assignment_id,
                assign.task_id,
                assign.claim_id,
                assign.parent_agent_id,
                assign.helper_agent_id,
                assign.assignment_mode,
                assign.assignment_id,
                capability_token_id,
                lease_expires_at,
                assign.assignment_id,
                assign.assignment_id,
                assign.assignment_id,
                _utcnow(),
                _utcnow(),
                assign.assignment_id,
                assign.assignment_id,
            ),
        )
        conn.execute(
            "UPDATE task_claims SET status = 'accepted', updated_at = ? WHERE claim_id = ?",
            (_utcnow(), assign.claim_id),
        )
        conn.execute(
            "UPDATE task_offers SET status = 'assigned', updated_at = ? WHERE task_id = ?",
            (_utcnow(), assign.task_id),
        )
        conn.commit()
    finally:
        conn.close()

    if capability_token:
        remember_capability_token(capability_token, status="active")

    stream_telemetry_event("TASK_ASSIGN", assign.task_id, {"helper": assign.helper_agent_id})
    transition(
        entity_type="subtask",
        entity_id=assign.task_id,
        to_state="assigned",
        details={"helper_peer_id": assign.helper_agent_id, "assignment_mode": assign.assignment_mode},
        trace_id=trace.trace_id,
    )


def _store_task_progress(progress: TaskProgress) -> None:
    trace = ensure_trace(progress.task_id, trace_id=progress.task_id)
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_progress_events (
                event_id, assignment_id, task_id, helper_peer_id,
                progress_state, progress_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                progress.assignment_id,
                progress.task_id,
                progress.helper_agent_id,
                progress.progress_state,
                progress.progress_note,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE task_assignments
            SET last_progress_state = ?,
                last_progress_note = ?,
                progress_updated_at = ?,
                updated_at = ?,
                status = CASE
                    WHEN ? = 'done' THEN 'completed'
                    WHEN ? = 'blocked' THEN 'blocked'
                    ELSE status
                END,
                completed_at = CASE
                    WHEN ? = 'done' THEN COALESCE(completed_at, ?)
                    ELSE completed_at
                END
            WHERE assignment_id = ?
            """,
            (
                progress.progress_state,
                progress.progress_note,
                now,
                now,
                progress.progress_state,
                progress.progress_state,
                progress.progress_state,
                now,
                progress.assignment_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stream_telemetry_event(
        "TASK_PROGRESS",
        progress.task_id,
        {"helper": progress.helper_agent_id, "state": progress.progress_state},
    )
    if progress.progress_state in {"started", "working"}:
        state = current_state("subtask", progress.task_id)
        if state == "assigned":
            transition(
                entity_type="subtask",
                entity_id=progress.task_id,
                to_state="running",
                details={"helper_peer_id": progress.helper_agent_id, "assignment_id": progress.assignment_id},
                trace_id=trace.trace_id,
            )
    if progress.progress_state == "blocked":
        revoke_capability_tokens_for_task(
            progress.task_id,
            helper_peer_id=progress.helper_agent_id,
            reason="progress_blocked",
        )


def _store_task_result(result: TaskResult) -> None:
    # A8 write fence (ERASE-dominance): a task result summary carrying
    # governed WITHHELD/ERASED bytes is refused at the durable boundary.
    from core.finalization import writer_may_publish_public_text

    if not writer_may_publish_public_text(str(result.summary or "")):
        raise ValueError("task result refused: summary carries governed WITHHELD/ERASED bytes")
    trace = ensure_trace(result.task_id, trace_id=result.task_id)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_results (
                result_id, task_id, helper_peer_id, result_type, summary, result_hash,
                confidence, evidence_json, abstract_steps_json, risk_flags_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted',
                      COALESCE((SELECT created_at FROM task_results WHERE result_id = ?), ?), ?)
            """,
            (
                result.result_id,
                result.task_id,
                result.helper_agent_id,
                result.result_type,
                result.summary,
                result.result_hash,
                result.confidence,
                _json(result.evidence),
                _json(result.abstract_steps),
                _json(result.risk_flags),
                result.result_id,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    stream_telemetry_event("TASK_RESULT", result.task_id, {"helper": result.helper_agent_id, "confidence": result.confidence})
    state = current_state("subtask", result.task_id)
    if state in {None, "assigned"}:
        transition(
            entity_type="subtask",
            entity_id=result.task_id,
            to_state="running",
            details={"helper_peer_id": result.helper_agent_id, "result_id": result.result_id},
            trace_id=trace.trace_id,
        )
        state = "running"
    if state == "running":
        transition(
            entity_type="subtask",
            entity_id=result.task_id,
            to_state="completed",
            details={"helper_peer_id": result.helper_agent_id, "result_id": result.result_id},
            trace_id=trace.trace_id,
        )


def _store_task_review(review: TaskReview) -> None:
    trace = ensure_trace(review.task_id, trace_id=review.task_id)
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
                review.task_id,
                review.helper_agent_id,
                review.reviewer_agent_id,
                review.outcome,
                review.helpfulness_score,
                review.quality_score,
                1 if review.harmful else 0,
                _utcnow(),
            ),
        )
        conn.execute(
            """
            UPDATE task_results
            SET status = ?, updated_at = ?
            WHERE task_id = ? AND helper_peer_id = ?
            """,
            (review.outcome, _utcnow(), review.task_id, review.helper_agent_id),
        )
        conn.execute(
            """
            UPDATE task_offers
            SET status = CASE
                WHEN ? IN ('accepted', 'partial') THEN 'completed'
                WHEN ? = 'harmful' THEN 'completed'
                ELSE status
            END,
            updated_at = ?
            WHERE task_id = ?
            """,
            (review.outcome, review.outcome, _utcnow(), review.task_id),
        )
        conn.commit()
    finally:
        conn.close()

    stream_telemetry_event("TASK_REVIEW", review.task_id, {"reviewer": review.reviewer_agent_id, "outcome": review.outcome})
    transition(
        entity_type="subtask",
        entity_id=review.task_id,
        to_state="completed",
        details={"outcome": review.outcome, "reviewer_peer_id": review.reviewer_agent_id},
        trace_id=trace.trace_id,
    )


def _task_offer_for(task_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def load_task_offer_payload(task_id: str) -> tuple[dict[str, Any], list[str]] | None:
    conn = get_connection()
    try:
        offer_row = conn.execute(
            "SELECT * FROM task_offers WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        capsule_row = conn.execute(
            "SELECT capsule_json FROM task_capsules WHERE task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
        if not offer_row or not capsule_row:
            return None
        required_capabilities = json.loads(str(offer_row["required_capabilities_json"] or "[]"))
        reward_hint = json.loads(str(offer_row["reward_hint_json"] or "{}"))
        payload = TaskOffer(
            task_id=str(offer_row["task_id"]),
            parent_agent_id=str(offer_row["parent_peer_id"]),
            capsule_id=str(offer_row["capsule_id"]),
            task_type=str(offer_row["task_type"]),
            subtask_type=str(offer_row["subtask_type"]),
            summary=str(offer_row["summary"]),
            required_capabilities=list(required_capabilities),
            max_helpers=int(offer_row["max_helpers"]),
            priority=str(offer_row["priority"]),
            reward_hint=reward_hint,
            capsule=json.loads(str(capsule_row["capsule_json"] or "{}")),
            deadline_ts=datetime.fromisoformat(str(offer_row["deadline_ts"]).replace("Z", "+00:00")),
        ).model_dump(mode="json")
        return payload, list(required_capabilities)
    finally:
        conn.close()


def _pick_best_claim(task_id: str, parent_peer_id: str) -> tuple[str, str] | None:
    """
    Returns (claim_id, helper_peer_id) for the best pending claim.
    Very simple v1: trust + capability fit handled upstream in discovery.

    Bid order keeps ``trust_score`` as the DOMINANT term. A proof-of-settlement
    tiebreak is folded in *after* trust_score: among helpers of equal trust, the
    one with the larger REAL-settled reward total (payouts whose settlement_mode
    is 'mainnet'/'devnet') wins. The tiebreak is the SUM of those settled
    amounts (COALESCE to 0) — NOT a ratio — so a helper with only 'simulated'
    rewards scores 0 exactly like an idle helper and is never reordered relative
    to it. When no real receipts exist (the current state, and CI) every helper
    scores 0 here and the original current_load / claimed_at ordering is
    preserved byte-for-byte.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.claim_id, c.helper_peer_id, c.current_load,
                   p.trust_score,
                   (
                     SELECT COALESCE(SUM(l.amount), 0)
                     FROM compute_credit_ledger l
                     WHERE l.peer_id = c.helper_peer_id
                       AND l.amount > 0
                       AND l.settlement_mode IN ('mainnet', 'devnet')
                   ) AS settled_amount
            FROM task_claims c
            LEFT JOIN peers p ON p.peer_id = c.helper_peer_id
            WHERE c.task_id = ? AND c.status = 'pending'
            ORDER BY COALESCE(p.trust_score, 0.5) DESC,
                     settled_amount DESC,
                     c.current_load ASC,
                     c.claimed_at ASC
            LIMIT 1
            """,
            (task_id,),
        ).fetchall()

        if not rows:
            return None

        row = rows[0]
        return row["claim_id"], row["helper_peer_id"]
    finally:
        conn.close()


def pick_best_claim_for_task(task_id: str, parent_peer_id: str) -> tuple[str, str] | None:
    return _pick_best_claim(task_id, parent_peer_id)


def persist_task_assignment(assign: TaskAssign) -> None:
    _store_task_assignment(assign)


def persist_task_progress(progress: TaskProgress) -> None:
    _store_task_progress(progress)


def prepare_task_assignment(
    *,
    task_id: str,
    claim_id: str,
    parent_agent_id: str,
    helper_agent_id: str,
    assignment_mode: str = "single",
    lease_seconds: int = 900,
) -> TaskAssign | None:
    capsule = load_task_capsule_for_task(task_id)
    if not capsule:
        return None
    return TaskAssign(
        assignment_id=str(uuid.uuid4()),
        task_id=task_id,
        claim_id=claim_id,
        parent_agent_id=parent_agent_id,
        helper_agent_id=helper_agent_id,
        assignment_mode=assignment_mode,
        capability_token=issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=parent_agent_id,
            helper_peer_id=helper_agent_id,
            capsule=capsule,
            assignment_mode=assignment_mode,
            lease_seconds=max(60, int(lease_seconds)),
        ),
        timestamp=datetime.now(timezone.utc),
    )


def build_task_assign_message(assign: TaskAssign) -> bytes:
    payload = assign.model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_ASSIGN",
        sender_peer_id=assign.parent_agent_id,
        nonce=_nonce(),
        payload=payload,
    )


def build_task_claim_message(*, task_id: str, declared_capabilities: list[str], current_load: int, host_group_hint_hash: str | None = None) -> bytes:
    helper_id = local_peer_id()
    payload = TaskClaim(
        claim_id=str(uuid.uuid4()),
        task_id=task_id,
        helper_agent_id=helper_id,
        declared_capabilities=declared_capabilities,
        current_load=current_load,
        host_group_hint_hash=host_group_hint_hash,
        timestamp=datetime.now(timezone.utc),
    ).model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_CLAIM",
        sender_peer_id=helper_id,
        nonce=_nonce(),
        payload=payload,
    )


def build_task_progress_message(
    *,
    assignment_id: str,
    task_id: str,
    helper_agent_id: str,
    progress_state: str,
    progress_note: str,
) -> bytes:
    payload = TaskProgress(
        assignment_id=assignment_id,
        task_id=task_id,
        helper_agent_id=helper_agent_id,
        progress_state=progress_state,
        progress_note=progress_note[:256],
        timestamp=datetime.now(timezone.utc),
    ).model_dump(mode="json")

    return encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_PROGRESS",
        sender_peer_id=helper_agent_id,
        nonce=_nonce(),
        payload=payload,
    )


def _validate_mesh_payload(msg_type: str, payload_dict: dict[str, Any]) -> Any:
    if msg_type == "FIND_NODE":
        return FindNodePayload.model_validate(payload_dict)
    if msg_type == "NODE_FOUND":
        return NodeFoundPayload.model_validate(payload_dict)
    if msg_type == "FIND_BLOCK":
        return FindBlockPayload.model_validate(payload_dict)
    if msg_type == "BLOCK_FOUND":
        return BlockFoundPayload.model_validate(payload_dict)
    if msg_type == "REQUEST_BLOCK":
        return RequestBlockPayload.model_validate(payload_dict)
    if msg_type == "BLOCK_PAYLOAD":
        return BlockPayloadMsg.model_validate(payload_dict)
    raise ValueError(f"Unsupported mesh payload type: {msg_type}")


def handle_incoming_assist_message(
    *,
    raw_bytes: bytes,
    source_addr: tuple[str, int] | None = None,
    local_capability_ad: CapabilityAd | None = None,
    idle_assist_config: IdleAssistConfig | None = None,
    local_current_assignments: int = 0,
    parent_trust_lookup: Callable[[str], float] | None = None,
    same_host_group_lookup: Callable[[str], bool] | None = None,
) -> RouteResult:
    """
    Validates a network envelope, routes assist messages, stores state,
    and optionally generates response messages (like TASK_CLAIM / TASK_ASSIGN).
    """
    generated: list[bytes] = []

    try:
        envelope_dict = Protocol.decode_and_validate(raw_bytes)
        # Assuming decode_and_validate returns dict, not object because of .model_dump()
        sender = envelope_dict["sender_peer_id"]
        msg_type = envelope_dict["msg_type"]
        payload_dict = envelope_dict["payload"]
    except Exception as e:
        return RouteResult(False, f"Envelope rejected: {e}", generated)

    if source_addr:
        record_signed_peer_endpoint_observation(
            sender,
            source_addr[0],
            int(source_addr[1]),
            envelope=envelope_dict,
            source="observed",
        )

    if not rate_allow(sender):
        return RouteResult(False, "Rate limited.", generated)

    if msg_type not in {
        "CAPABILITY_AD",
        "TASK_OFFER",
        "TASK_CLAIM",
        "TASK_ASSIGN",
        "TASK_PROGRESS",
        "TASK_RESULT",
        "TASK_REVIEW",
        "TASK_REWARD",
        "TASK_CANCEL",
        "FIND_NODE",
        "NODE_FOUND",
        "FIND_BLOCK",
        "BLOCK_FOUND",
        "REQUEST_BLOCK",
        "BLOCK_PAYLOAD",
        "CREDIT_OFFER",
        "CREDIT_TRANSFER",
    }:
        return RouteResult(False, f"Not an assist message: {msg_type}", generated)

    try:
        if msg_type == "TASK_CANCEL":
            payload_model = payload_dict
        elif msg_type in {"FIND_NODE", "NODE_FOUND", "FIND_BLOCK", "BLOCK_FOUND", "REQUEST_BLOCK", "BLOCK_PAYLOAD"}:
            payload_model = _validate_mesh_payload(msg_type, payload_dict)
        else:
            payload_model = validate_assist_payload(msg_type, payload_dict)
    except Exception as e:
        note_peer_violation(sender, f"assist_payload_invalid:{msg_type}")
        return RouteResult(False, f"Assist payload invalid: {e}", generated)

    upsert_peer_minimal(sender)

    # Phase 23: DHT integration
    from network.dht import get_routing_table
    table = get_routing_table()
    if source_addr:
        table.add_node(sender, source_addr[0], int(source_addr[1]), source="observed")

    if msg_type == "FIND_NODE":
        target = payload_model.target_id
        closest = table.find_closest_peers(target, count=10, verified_only=True)
        generated.append(build_node_found_message(target, closest))
        return RouteResult(True, f"DHT FIND_NODE processed. Found {len(closest)} peers.", generated)

    if msg_type == "NODE_FOUND":
        nodes = payload_model.nodes
        for n in nodes:
            table.add_node(n.peer_id, n.ip, n.port, source="dht")
            register_peer_endpoint_candidate(n.peer_id, n.ip, n.port, source="dht")
        return RouteResult(True, f"DHT NODE_FOUND processed. Integrated {len(nodes)} peers.", generated)

    # Phase 24: Petabyte Data Layer (CAS Block Routing)
    if msg_type == "FIND_BLOCK":
        block_hash = payload_model.block_hash
        from core.liquefy_cas import get_chunk
        chunk_data = get_chunk(block_hash)

        if chunk_data:
            # We have it; advertise a reachable endpoint when known.
            from network.dht import DHTNode
            local_id = local_peer_id()
            targets = delivery_targets_for_peer(local_id, verified_limit=1, include_candidates=False)
            if targets:
                host = targets[0].host
                port = int(targets[0].port)
            else:
                host = "127.0.0.1"
                port = 49152
            self_entry = DHTNode(peer_id=local_id, ip=host, port=port, last_seen=0)
            generated.append(build_block_found_message(block_hash, [self_entry]))
            return RouteResult(True, "FIND_BLOCK processed. We have the block, returning BLOCK_FOUND.", generated)
        else:
            # We don't have it, route to closest peers in DHT
            closest = table.find_closest_peers(block_hash[:64], count=10, verified_only=True) # approximate distance by block hash
            generated.append(build_block_found_message(block_hash, closest))
            return RouteResult(True, f"FIND_BLOCK processed. Routed to {len(closest)} closer peers.", generated)

    if msg_type == "REQUEST_BLOCK":
        block_hash = payload_model.block_hash
        from core.liquefy_cas import get_chunk
        chunk_data = get_chunk(block_hash)
        if chunk_data:
            generated.append(build_block_payload_message(block_hash, chunk_data))

            # Phase 24: Reward Provider Score for serving data
            from core.scoreboard_engine import award_provider_score
            award_provider_score(
                peer_id=local_peer_id(),
                task_id=block_hash,
                quality=1.0,
                helpfulness=1.0,
                outcome="accepted",
            )

            return RouteResult(True, "REQUEST_BLOCK processed. Serving 2MB payload.", generated)
        return RouteResult(False, "REQUEST_BLOCK failed. Block not found locally.", generated)

    if msg_type == "BLOCK_FOUND":
        hosting_peers = getattr(payload_model, "hosting_peers", [])
        for peer in hosting_peers:
            register_peer_endpoint_candidate(peer.peer_id, peer.ip, peer.port, source="block_found")
        return RouteResult(True, f"BLOCK_FOUND processed. Observed {len(hosting_peers)} hosting peers.", generated)

    if msg_type == "BLOCK_PAYLOAD":
        block_hash = payload_model.block_hash
        byte_data = bytes.fromhex(payload_model.byte_hex)
        from core.liquefy_cas import store_chunk
        saved_hash = store_chunk(byte_data)
        if saved_hash == block_hash:
            return RouteResult(True, "BLOCK_PAYLOAD processed and saved to CAS.", generated)
        return RouteResult(False, "BLOCK_PAYLOAD hash mismatch! Rejected.", generated)


    if msg_type == "CAPABILITY_AD":
        ad = payload_model

        # Identity binding: a peer may only advertise capabilities for itself.
        # Without this a peer could publish an ad on behalf of any agent_id and
        # (via the trust fields) self-promote or defame a third party.
        if ad.agent_id != sender:
            note_peer_violation(sender, "capability_ad_agent_id_mismatch")
            return RouteResult(
                False,
                "CAPABILITY_AD rejected: agent_id does not match envelope sender.",
                generated,
            )

        # Phase 30: Sybil Resistance at Genesis
        required_difficulty = required_pow_difficulty(default=4)
        if not verify_pow(ad.agent_id, ad.genesis_nonce, target_difficulty=required_difficulty):
            note_peer_violation(sender, "sybil_invalid_pow")
            return RouteResult(False, "CAPABILITY_AD rejected: Invalid Proof-of-Work nonce.", generated)

        register_capability_ad(ad)
        # Trust is owned by the local reputation engine. Seed the routing-read
        # trust column from our locally-held trust for this peer, never from the
        # peer's self-declared ad.trust_score.
        record_bootstrap_presence(
            peer_id=ad.agent_id,
            status=ad.status,
            capabilities=ad.capabilities,
            capacity=ad.capacity,
            trust_score=peer_trust(ad.agent_id),
            host_group_hint_hash=ad.assist_filters.host_group_hint_hash,
        )
        return RouteResult(True, "Capability ad stored.", generated)

    if msg_type == "TASK_OFFER":
        offer = payload_model
        capsule_raw = payload_model.capsule
        if not isinstance(capsule_raw, dict):
            note_peer_violation(sender, "task_offer_missing_capsule")
            return RouteResult(False, "TASK_OFFER must include embedded 'capsule'.", generated)

        try:
            capsule = verify_task_capsule(capsule_raw)
        except Exception as e:
            note_peer_violation(sender, "task_capsule_invalid")
            return RouteResult(False, f"Task capsule invalid: {e}", generated)

        _store_task_offer(offer, capsule)

        # Auto-decide whether we want to help
        if local_capability_ad and idle_assist_config:
            parent_trust = float(parent_trust_lookup(sender)) if parent_trust_lookup else 0.50
            same_host_suspect = bool(same_host_group_lookup(sender)) if same_host_group_lookup else False

            decision = should_accept_offer(
                config=idle_assist_config,
                capability_ad=local_capability_ad,
                offer=offer,
                capsule=capsule,
                parent_trust=parent_trust,
                current_assignments=local_current_assignments,
                same_host_group_suspect=same_host_suspect,
            )

            if decision.accept:
                # Phase 27: Dynamic Bidding Order Book.
                # Push to priority queue rather than immediately returning a claim message.
                from core.order_book import global_order_book
                global_order_book.push(raw_bytes, source_addr or ("0.0.0.0", 0), payload_dict)
                return RouteResult(True, "Offer stored and pushed to OrderBookQueue for evaluation.", generated)

            return RouteResult(True, f"Offer stored; local policy declined: {decision.reason}", generated)

        return RouteResult(True, "Offer stored.", generated)

    if msg_type == "TASK_CLAIM":
        claim = payload_model
        _store_task_claim(claim)

        # If we are the parent, we can choose the best claim and assign.
        offer = _task_offer_for(claim.task_id)
        if offer and offer["parent_peer_id"] == local_peer_id():
            conn = get_connection()
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM task_assignments WHERE task_id = ?", (claim.task_id,)).fetchone()
                active_count = int(row["c"]) if row else 0
            finally:
                conn.close()

            if active_count < offer["max_helpers"]:
                best = _pick_best_claim(claim.task_id, offer["parent_peer_id"])
                if best:
                    claim_id, helper_peer_id = best

                    assign_obj = prepare_task_assignment(
                        task_id=claim.task_id,
                        claim_id=claim_id,
                        parent_agent_id=offer["parent_peer_id"],
                        helper_agent_id=helper_peer_id,
                        assignment_mode="verification" if active_count > 0 else "single",
                    )
                    if not assign_obj:
                        return RouteResult(False, "Claim stored; assignment preparation failed.", generated)

                    # Store it locally so parent updates task status to 'assigned' and claim to 'accepted'
                    _store_task_assignment(assign_obj)
                    assign_msg_bytes = build_task_assign_message(assign_obj)
                    generated.append(assign_msg_bytes)

                    # Phase 28: Spot-check check on first assignment
                    if active_count == 0:
                        import random

                        from core.discovery_index import get_spot_check_probability
                        prob = get_spot_check_probability(helper_peer_id)

                        if random.random() < prob:
                            # Re-open the offer to catch a 2nd helper for redundant validation
                            conn = get_connection()
                            try:
                                conn.execute("UPDATE task_offers SET max_helpers = 2, status = 'open' WHERE task_id = ?", (claim.task_id,))
                                conn.commit()
                            finally:
                                conn.close()

                            audit_logger.log(
                                "spot_check_triggered",
                                target_id=claim.task_id,
                                target_type="task",
                                details={"target_helper": helper_peer_id, "probability": prob}
                            )

                    return RouteResult(True, "Claim stored; assignment generated.", generated)

        return RouteResult(True, "Claim stored.", generated)

    if msg_type == "TASK_ASSIGN":
        assign = payload_model
        _store_task_assignment(assign)
        return RouteResult(True, "Assignment stored.", generated)

    if msg_type == "TASK_PROGRESS":
        progress = payload_model
        _store_task_progress(progress)
        return RouteResult(True, "Task progress stored.", generated)

    if msg_type == "TASK_RESULT":
        result = payload_model
        _store_task_result(result)
        return RouteResult(True, "Task result stored.", generated)

    if msg_type == "TASK_REVIEW":
        review = payload_model
        _store_task_review(review)
        return RouteResult(True, "Task review stored.", generated)

    if msg_type == "TASK_REWARD":
        # In v1 we trust local ledger as source of truth, so remote TASK_REWARD is informational only.
        audit_logger.log(
            "remote_task_reward_seen",
            target_id=payload_dict.get("task_id"),
            target_type="task",
            details={"from": sender},
        )
        return RouteResult(True, "Remote reward notice observed.", generated)

    if msg_type == "TASK_CANCEL":
        payload = payload_dict
        task_id = str(payload.get("task_id", ""))
        if task_id:
            conn = get_connection()
            try:
                conn.execute(
                    "UPDATE task_offers SET status = 'cancelled', updated_at = ? WHERE task_id = ?",
                    (_utcnow(), task_id),
                )
                conn.commit()
            finally:
                conn.close()
        return RouteResult(True, "Task cancelled.", generated)

    # Phase 29: Credit Market DEX message routing
    if msg_type == "CREDIT_OFFER":
        from core.credit_dex import global_credit_market
        global_credit_market.push(payload_dict)
        return RouteResult(True, "Credit offer stored in DEX queue.", generated)

    if msg_type == "CREDIT_TRANSFER":
        transfer = payload_model
        # Informational only — award NOTHING. A CREDIT_TRANSFER is a peer's signed CLAIM that
        # they sent credits, but it carries no proof of a real debit or on-chain settlement.
        # Minting local credits on that claim would let any peer credit this node by simply
        # asserting a transfer (the forgery seam removed in core/dna_payment_bridge). The mesh
        # credit economy is a parked research feature; the claim is recorded, not acted on.
        if transfer.buyer_peer_id == local_peer_id():
            audit_logger.log(
                "credit_transfer_observed",
                target_id=transfer.transfer_id,
                target_type="dex",
                details={
                    "seller": transfer.seller_peer_id,
                    "buyer": transfer.buyer_peer_id,
                    "amount": transfer.credits_transferred,
                    "awarded": False,
                    "reason": "unverified_claim_informational_only",
                },
                trace_id=transfer.transfer_id,
            )
            return RouteResult(True, "Credit transfer claim observed; no credits awarded (unverified).", generated)

        return RouteResult(True, "Credit transfer observed.", generated)

    return RouteResult(False, "Unhandled assist message.", generated)
