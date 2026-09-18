from __future__ import annotations

import json
import uuid
from typing import Any

from core import audit_logger, policy_engine
from core.daemon.peer_delivery import broadcast_to_recent_peers
from core.discovery_index import (
    peer_trust,
    record_signed_peer_endpoint_observation,
    same_host_group_suspect,
    upsert_peer_minimal,
)
from core.knowledge_registry import register_local_shard
from core.knowledge_transport import build_transport_shard_response, validate_incoming_transport_shard
from network.assist_router import handle_incoming_assist_message
from network.knowledge_models import validate_knowledge_payload
from network.knowledge_router import handle_knowledge_message
from network.presence_router import handle_presence_message
from network.protocol import Protocol, encode_message, peek_message_type, peek_sender_peer_id
from network.rate_limiter import allow as rate_allow
from network.signer import get_local_peer_id as local_peer_id
from retrieval.swarm_query import request_specific_shard
from storage.db import get_connection
from storage.shard_fetch_receipts import record_fetch_receipt


def on_message(daemon: Any, raw: bytes, addr: tuple[str, int]) -> None:
    msg_type = peek_message_type(raw)
    if not msg_type:
        audit_logger.log(
            "incoming_message_rejected",
            target_id=f"{addr[0]}:{addr[1]}",
            target_type="network",
            details={"error": "unable_to_peek_msg_type"},
        )
        return

    assist_types = {
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
    }

    if msg_type in assist_types:
        daemon._refresh_assist_status()
        result = handle_incoming_assist_message(
            raw_bytes=raw,
            source_addr=addr,
            local_capability_ad=daemon.local_capability_ad,
            idle_assist_config=daemon._idle_assist_config(),
            local_current_assignments=daemon._active_assignment_count(),
            parent_trust_lookup=peer_trust,
            same_host_group_lookup=lambda remote_peer_id: same_host_group_suspect(
                daemon.config.local_host_group_hint_hash,
                remote_peer_id,
            ),
        )

        reply_to_self = peek_sender_peer_id(raw) == local_peer_id()
        for msg in result.generated_messages:
            if reply_to_self:
                # Replies to our own messages (zero-peer loopback, e.g. a
                # TASK_ASSIGN answering a self TASK_CLAIM) must be handled
                # in-process: the datagram's source addr is an ephemeral
                # send socket that is already closed.
                on_message(daemon, msg, addr)
                continue
            daemon._send_or_log(
                addr[0],
                int(addr[1]),
                msg,
                message_type="ASSIST_REPLY",
                target_id=f"{addr[0]}:{addr[1]}",
            )

        if not result.ok:
            return

        if msg_type == "TASK_ASSIGN":
            daemon._spawn_limited_worker(
                target=daemon._maybe_execute_local_assignment_from_raw,
                args=(raw, addr),
                name="vool-local-assignment",
                target_id=f"{addr[0]}:{addr[1]}",
            )

        if msg_type == "TASK_RESULT":
            daemon._spawn_limited_worker(
                target=daemon._maybe_auto_review_result_from_raw,
                args=(raw, addr),
                name="vool-local-review",
                target_id=f"{addr[0]}:{addr[1]}",
            )

        return

    try:
        envelope = Protocol.decode_and_validate(raw)
    except Exception as exc:
        audit_logger.log(
            "incoming_message_rejected",
            target_id=f"{addr[0]}:{addr[1]}",
            target_type="network",
            details={"error": str(exc)},
        )
        return

    sender = str(envelope["sender_peer_id"])
    if not rate_allow(sender):
        audit_logger.log(
            "incoming_non_assist_rate_limited",
            target_id=sender,
            target_type="peer",
            details={"msg_type": str(envelope.get("msg_type") or "")},
        )
        return
    upsert_peer_minimal(sender)
    record_signed_peer_endpoint_observation(
        sender,
        addr[0],
        int(addr[1]),
        envelope=envelope,
        source="observed",
    )

    msg_type = str(envelope["msg_type"])
    payload = envelope.get("payload") or {}

    if msg_type == "PING":
        daemon._reply_basic("HEARTBEAT", {}, addr)
        return

    if msg_type == "HEARTBEAT":
        return

    if msg_type in {"HELLO_AD", "PRESENCE_HEARTBEAT"}:
        payload_model = validate_knowledge_payload(msg_type, payload)
        handle_presence_message(msg_type, payload_model)
        return

    if msg_type in {
        "KNOWLEDGE_AD",
        "KNOWLEDGE_WITHDRAW",
        "KNOWLEDGE_FETCH_REQUEST",
        "KNOWLEDGE_FETCH_OFFER",
        "KNOWLEDGE_REPLICA_AD",
        "KNOWLEDGE_REFRESH",
        "KNOWLEDGE_TOMBSTONE",
    }:
        payload_model = validate_knowledge_payload(msg_type, payload)
        result = handle_knowledge_message(msg_type, payload_model)
        for msg in result.generated_messages:
            daemon._send_or_log(addr[0], int(addr[1]), msg, message_type=msg_type, target_id=f"{addr[0]}:{addr[1]}")
        return

    if msg_type == "QUERY_SHARD":
        daemon._handle_query_shard(payload, addr)
        return

    if msg_type == "SHARD_CANDIDATES":
        daemon._handle_shard_candidates(payload, sender)
        return

    if msg_type == "REQUEST_SHARD":
        daemon._handle_request_shard(payload, addr)
        return

    if msg_type == "SHARD_PAYLOAD":
        daemon._handle_shard_payload(payload, sender)
        return

    if msg_type == "REPORT_ABUSE":
        daemon._handle_report_abuse(payload, sender, addr)
        return


def reply_basic(daemon: Any, msg_type: str, payload: dict[str, Any], addr: tuple[str, int]) -> None:
    raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type=msg_type,
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=payload,
    )
    daemon._send_or_log(addr[0], int(addr[1]), raw, message_type=msg_type, target_id=f"{addr[0]}:{addr[1]}")


def handle_report_abuse(daemon: Any, payload: dict[str, Any], sender: str, addr: tuple[str, int]) -> None:
    from core.fraud_engine import record_signal
    from storage.abuse_gossip_store import allow_reporter_report, mark_report_seen

    report_id = str(payload.get("report_id") or "").strip()
    if not report_id:
        return
    if not mark_report_seen(report_id):
        return
    per_minute_limit = int(policy_engine.get("network.report_abuse_max_reports_per_minute", 8))
    if not allow_reporter_report(sender, per_minute_limit=per_minute_limit):
        audit_logger.log(
            "report_abuse_rate_limited",
            target_id=report_id,
            target_type="anti_abuse",
            details={"reporter_peer_id": sender},
        )
        return

    min_reporter_trust = float(policy_engine.get("network.report_abuse_min_reporter_trust", 0.25))
    reporter_trust = float(peer_trust(sender))
    if reporter_trust < min_reporter_trust:
        audit_logger.log(
            "report_abuse_rejected_low_trust",
            target_id=report_id,
            target_type="anti_abuse",
            details={"reporter_peer_id": sender, "reporter_trust": reporter_trust},
        )
        return

    accused_peer_id = str(payload.get("accused_peer_id") or "").strip() or None
    signal_type = str(payload.get("signal_type") or "reported_abuse").strip()
    severity = float(payload.get("severity") or 0.0)
    task_id = str(payload.get("task_id") or "").strip() or None
    if task_id and not daemon._reporter_related_to_task(sender, task_id):
        audit_logger.log(
            "report_abuse_rejected_unrelated_reporter",
            target_id=report_id,
            target_type="anti_abuse",
            details={"reporter_peer_id": sender, "task_id": task_id},
        )
        return

    details = dict(payload.get("details") or {})
    details["report_id"] = report_id
    details["reporter_peer_id"] = sender
    details["source_addr"] = f"{addr[0]}:{addr[1]}"

    record_signal(
        peer_id=accused_peer_id,
        related_peer_id=sender,
        task_id=task_id,
        signal_type=f"gossip_{signal_type}",
        severity=severity,
        details=details,
    )

    ttl = int(payload.get("ttl") or policy_engine.get("network.report_abuse_gossip_ttl", 2))
    if ttl <= 0:
        return
    next_ttl = max(0, ttl - 1)
    fanout = int(policy_engine.get("network.report_abuse_gossip_fanout", 8))
    forward_payload = dict(payload)
    forward_payload["ttl"] = next_ttl
    forward_raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="REPORT_ABUSE",
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=forward_payload,
    )
    forwarded = broadcast_to_recent_peers(
        forward_raw,
        message_type="REPORT_ABUSE",
        target_id=report_id,
        limit=max(16, fanout * 2),
        fanout=fanout,
        exclude_peer_ids={sender},
        send_attempt=lambda host, port, raw: daemon._send_or_log(
            host,
            int(port),
            raw,
            message_type="REPORT_ABUSE",
            target_id=report_id,
        ),
    )

    audit_logger.log(
        "report_abuse_gossiped",
        target_id=report_id,
        target_type="anti_abuse",
        details={
            "forwarded": forwarded,
            "next_ttl": next_ttl,
            "accused_peer_id": accused_peer_id,
            "signal_type": signal_type,
        },
    )


def reporter_related_to_task(reporter_peer_id: str, task_id: str) -> bool:
    reporter = str(reporter_peer_id or "").strip()
    task = str(task_id or "").strip()
    if not reporter or not task:
        return False
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM (
                SELECT parent_peer_id AS peer_id FROM task_offers WHERE task_id = ?
                UNION ALL
                SELECT helper_peer_id AS peer_id FROM task_claims WHERE task_id = ?
                UNION ALL
                SELECT helper_peer_id AS peer_id FROM task_assignments WHERE task_id = ?
                UNION ALL
                SELECT helper_peer_id AS peer_id FROM task_results WHERE task_id = ?
                UNION ALL
                SELECT reviewer_peer_id AS peer_id FROM task_reviews WHERE task_id = ?
            ) participants
            WHERE peer_id = ?
            LIMIT 1
            """,
            (task, task, task, task, task, reporter),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def handle_query_shard(daemon: Any, payload: dict[str, Any], addr: tuple[str, int]) -> None:
    problem_class = str(payload.get("problem_class", "unknown"))
    query_id = str(payload.get("query_id", ""))
    max_candidates = int(payload.get("max_candidates", 3))
    max_candidates = max(1, min(5, max_candidates))

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM learning_shards
            WHERE problem_class = ?
              AND quarantine_status = 'active'
            ORDER BY trust_score DESC, quality_score DESC, updated_at DESC
            LIMIT ?
            """,
            (problem_class, max_candidates),
        ).fetchall()
    finally:
        conn.close()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        candidates.append(
            {
                "shard_id": row["shard_id"],
                "problem_class": row["problem_class"],
                "summary": row["summary"][:512],
                "trust_score": float(row["trust_score"]),
                "quality_score": float(row["quality_score"]),
                "freshness_ts": row["freshness_ts"],
                "risk_flags": json.loads(row["risk_flags_json"]),
            }
        )

    raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="SHARD_CANDIDATES",
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload={"query_id": query_id, "candidates": candidates},
    )
    daemon._send_or_log(addr[0], int(addr[1]), raw, message_type="SHARD_CANDIDATES", target_id=query_id or "unknown")


def handle_shard_candidates(daemon: Any, payload: dict[str, Any], sender_peer_id: str) -> None:
    query_id = str(payload.get("query_id", ""))
    raw_candidates = payload.get("candidates") or []
    if not isinstance(raw_candidates, list):
        return

    safe = []
    blocked = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        risk_flags = set(item.get("risk_flags") or [])
        if any(flag in blocked for flag in risk_flags):
            continue
        safe.append(item)

    safe.sort(
        key=lambda candidate: (
            float(candidate.get("trust_score", 0.0)),
            float(candidate.get("quality_score", 0.0)),
        ),
        reverse=True,
    )

    for item in safe[: max(1, min(daemon.config.auto_request_shards_per_response, 3))]:
        shard_id = item.get("shard_id")
        if not shard_id:
            continue
        request_specific_shard(peer_id=sender_peer_id, query_id=query_id, shard_id=str(shard_id))


def handle_request_shard(daemon: Any, payload: dict[str, Any], addr: tuple[str, int]) -> None:
    query_id = str(payload.get("query_id", ""))
    shard_id = str(payload.get("shard_id", ""))

    if not shard_id:
        return

    response_payload = build_transport_shard_response(query_id=query_id, shard_id=shard_id)
    if not response_payload:
        return

    raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="SHARD_PAYLOAD",
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=response_payload,
    )
    daemon._send_or_log(addr[0], int(addr[1]), raw, message_type="SHARD_PAYLOAD", target_id=shard_id)


def handle_shard_payload(_daemon: Any, payload: dict[str, Any], sender_peer_id: str) -> None:
    shard = payload.get("shard")
    if not isinstance(shard, dict):
        return

    risk_flags = shard.get("risk_flags") or []
    blocked = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))
    if any(flag in blocked for flag in risk_flags):
        return

    validation = validate_incoming_transport_shard(payload)
    receipt_id = record_fetch_receipt(
        shard_id=str(shard.get("shard_id") or "").strip(),
        source_peer_id=sender_peer_id,
        source_node_id=str(shard.get("source_node_id") or "").strip() or None,
        query_id=str(payload.get("query_id") or "").strip() or None,
        manifest_id=str(payload.get("manifest_id") or "").strip() or None,
        content_hash=str(payload.get("content_hash") or "").strip() or None,
        version=payload.get("version"),
        summary_digest=str(payload.get("summary_digest") or "").strip() or None,
        validation_state=str(validation.get("validation_state") or "validation_failed"),
        accepted=bool(validation.get("accepted")),
        details=dict(validation.get("details") or {}),
    )
    if not bool(validation.get("accepted")):
        audit_logger.log(
            "peer_shard_rejected",
            target_id=str(shard.get("shard_id") or "unknown"),
            target_type="shard",
            details={
                "source_peer": sender_peer_id,
                "validation_state": validation.get("validation_state"),
                "receipt_id": receipt_id,
                "errors": list((validation.get("details") or {}).get("errors") or []),
            },
        )
        return

    conn = get_connection()
    try:
        incoming_trust_cap = float(policy_engine.get("shards.max_incoming_trust_score", 0.60))
        conn.execute(
            """
            INSERT OR REPLACE INTO learning_shards (
                shard_id, schema_version, problem_class, problem_signature,
                summary, resolution_pattern_json, environment_tags_json,
                source_type, source_node_id, quality_score, trust_score,
                local_validation_count, local_failure_count,
                quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                signature, origin_task_id, origin_session_id, share_scope,
                restricted_terms_json, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, 'peer_received', ?, ?, ?, 0, 0,
                'active', ?, ?, ?, ?, '', '', 'public_knowledge', '[]',
                COALESCE((SELECT created_at FROM learning_shards WHERE shard_id = ?), CURRENT_TIMESTAMP),
                CURRENT_TIMESTAMP
            )
            """,
            (
                shard["shard_id"],
                int(shard["schema_version"]),
                shard["problem_class"],
                shard["problem_signature"],
                shard["summary"],
                json.dumps(shard["resolution_pattern"], sort_keys=True),
                json.dumps(shard["environment_tags"], sort_keys=True),
                str(shard.get("source_node_id") or sender_peer_id),
                max(0.0, min(1.0, float(shard["quality_score"]))),
                min(max(0.0, incoming_trust_cap), max(0.0, float(shard["trust_score"]))),
                json.dumps(risk_flags, sort_keys=True),
                shard["freshness_ts"],
                shard.get("expires_ts"),
                shard["signature"],
                shard["shard_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "peer_shard_cached",
        target_id=shard["shard_id"],
        target_type="shard",
        details={
            "source_peer": sender_peer_id,
            "receipt_id": receipt_id,
            "validation_state": validation.get("validation_state"),
        },
    )
    manifest = register_local_shard(str(shard["shard_id"]))
    if not manifest:
        audit_logger.log(
            "peer_shard_kept_candidate_only",
            target_id=shard["shard_id"],
            target_type="shard",
            details={"source_peer": sender_peer_id, "reason": "shareability_gate_blocked"},
        )
