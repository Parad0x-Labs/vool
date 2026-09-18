from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from core import audit_logger, policy_engine
from core.capability_tokens import consume_capability_token, verify_assignment_capability
from core.daemon.peer_delivery import send_or_log as _send_or_log_via_transport
from core.daemon.peer_delivery import send_to_peer_or_log
from core.liquefy_bridge import execution_payload_within_size_limit
from network.assist_router import build_task_progress_message
from network.protocol import Envelope, Protocol, encode_message, validate_payload, verify_signature
from network.signer import get_local_peer_id as local_peer_id


def maybe_auto_review_result_from_raw(
    daemon: Any,
    raw: bytes,
    fallback_addr: tuple[str, int],
    *,
    hooks: Any,
) -> None:
    envelope = daemon._decode_verified_assist_envelope(raw, expected_msg_type="TASK_RESULT")
    if not envelope:
        return
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        return
    sender_peer_id = str(envelope.get("sender_peer_id") or "")
    helper_peer_id = str(payload.get("helper_agent_id", ""))
    if sender_peer_id and helper_peer_id and sender_peer_id != helper_peer_id:
        audit_logger.log(
            "task_result_rejected_sender_mismatch",
            target_id=str(payload.get("task_id", "")),
            target_type="task",
            details={"sender_peer_id": sender_peer_id, "helper_peer_id": helper_peer_id},
        )
        return

    artifacts = hooks.auto_review_task_result(
        payload,
        reviewer_peer_id=local_peer_id(),
        emit_reward_notice=True,
    )
    if not artifacts:
        return

    for msg in artifacts.outbound_messages:
        send_to_peer_or_log(
            helper_peer_id,
            msg,
            message_type="TASK_REVIEW",
            target_id=str(payload.get("task_id", "")),
            include_candidates=True,
            fallback_addr=fallback_addr,
            send_attempt=lambda host, port, raw: daemon._send_or_log(
                host,
                int(port),
                raw,
                message_type="TASK_REVIEW",
                target_id=str(payload.get("task_id", "")),
            ),
        )

    audit_logger.log(
        "task_result_review_reply_sent",
        target_id=str(payload.get("task_id", "")),
        target_type="task",
        details={
            "helper_peer_id": helper_peer_id,
            "review_outcome": artifacts.outcome,
            "points_awarded": artifacts.points_awarded,
            "wnull_pending": artifacts.wnull_pending,
        },
    )


def maybe_execute_local_assignment_from_raw(
    daemon: Any,
    raw: bytes,
    fallback_addr: tuple[str, int],
    *,
    hooks: Any,
) -> None:
    # Edition floor (goal §4/§8): in VOOL School the hive lane does not exist —
    # a remote peer's capsule is refused before decode, whatever preferences say.
    from core.product_edition import edition_allows

    hive_ok, _hive_reason = edition_allows("hive_task_intake")
    if not hive_ok:
        return
    envelope = daemon._decode_verified_assist_envelope(raw, expected_msg_type="TASK_ASSIGN")
    if not envelope:
        return
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        return
    sender_peer_id = str(envelope.get("sender_peer_id") or "")
    parent_peer_id = str(payload.get("parent_agent_id", ""))
    if sender_peer_id and parent_peer_id and sender_peer_id != parent_peer_id:
        audit_logger.log(
            "task_assign_rejected_sender_mismatch",
            target_id=str(payload.get("task_id", "")),
            target_type="task",
            details={"sender_peer_id": sender_peer_id, "parent_peer_id": parent_peer_id},
        )
        return

    if payload.get("helper_agent_id") != local_peer_id():
        return

    task_id = str(payload.get("task_id", ""))
    assignment_id = str(payload.get("assignment_id", ""))
    if not task_id or not parent_peer_id:
        return

    capsule = hooks.load_task_capsule_for_task(task_id)
    if not capsule:
        audit_logger.log(
            "assignment_execution_skipped",
            target_id=task_id,
            target_type="task",
            details={"reason": "capsule_not_found"},
        )
        return

    if not assignment_id:
        audit_logger.log(
            "assignment_execution_skipped",
            target_id=task_id,
            target_type="task",
            details={"reason": "missing_assignment_id"},
        )
        return

    parent_trust = float(hooks.peer_trust(parent_peer_id))
    min_parent_trust = max(
        0.0,
        min(1.0, float(policy_engine.get("assist_mesh.min_parent_trust_for_assignment_execution", 0.25))),
    )
    if parent_trust < min_parent_trust:
        audit_logger.log(
            "assignment_execution_skipped",
            target_id=task_id,
            target_type="task",
            details={"reason": "parent_trust_too_low", "parent_trust": parent_trust, "threshold": min_parent_trust},
        )
        return

    capability_token = payload.get("capability_token")
    if bool(policy_engine.get("assist_mesh.require_assignment_capability_token", True)):
        capability_decision = verify_assignment_capability(
            capability_token if isinstance(capability_token, dict) else None,
            task_id=task_id,
            parent_peer_id=parent_peer_id,
            helper_peer_id=local_peer_id(),
            capsule=capsule,
        )
        if not capability_decision.ok:
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": capability_decision.reason},
            )
            return

        # Atomic verify+consume: only the caller that flips the single-use token
        # active -> used may execute. A concurrent execution loses the CAS and is skipped,
        # closing the TOCTOU window between verify (status == active) and consume.
        capability_token_id = str((capability_token or {}).get("token_id") or "").strip()
        if capability_token_id and not consume_capability_token(capability_token_id):
            audit_logger.log(
                "assignment_execution_skipped",
                target_id=task_id,
                target_type="task",
                details={"reason": "capability_token_already_consumed"},
            )
            return

    if not execution_payload_within_size_limit(
        {"task_id": task_id, "assignment_id": assignment_id},
        {"assignment": payload, "capability_token": capability_token or {}},
    ):
        audit_logger.log(
            "assignment_execution_skipped",
            target_id=task_id,
            target_type="task",
            details={"reason": "liquefy_execution_safety_rejected"},
        )
        return

    try:
        started_progress = build_task_progress_message(
            assignment_id=assignment_id,
            task_id=task_id,
            helper_agent_id=local_peer_id(),
            progress_state="started",
            progress_note="lease verified; helper execution started",
        )
        send_to_peer_or_log(
            parent_peer_id,
            started_progress,
            message_type="TASK_PROGRESS",
            target_id=task_id,
            include_candidates=True,
            fallback_addr=fallback_addr,
            send_attempt=lambda host, port, raw: daemon._send_or_log(
                host,
                int(port),
                raw,
                message_type="TASK_PROGRESS",
                target_id=task_id,
            ),
        )

        worker_outcome = hooks.run_task_capsule(capsule, helper_agent_id=local_peer_id())
    except Exception as exc:
        blocked_progress = build_task_progress_message(
            assignment_id=assignment_id,
            task_id=task_id,
            helper_agent_id=local_peer_id(),
            progress_state="blocked",
            progress_note=f"helper execution failed: {str(exc)[:180]}",
        )
        send_to_peer_or_log(
            parent_peer_id,
            blocked_progress,
            message_type="TASK_PROGRESS",
            target_id=task_id,
            include_candidates=True,
            fallback_addr=fallback_addr,
            send_attempt=lambda host, port, raw: daemon._send_or_log(
                host,
                int(port),
                raw,
                message_type="TASK_PROGRESS",
                target_id=task_id,
            ),
        )
        audit_logger.log(
            "assignment_execution_failed",
            target_id=task_id,
            target_type="task",
            details={"error": str(exc)},
        )
        return

    done_progress = build_task_progress_message(
        assignment_id=assignment_id,
        task_id=task_id,
        helper_agent_id=local_peer_id(),
        progress_state="done",
        progress_note="helper execution finished; sending result",
    )
    send_to_peer_or_log(
        parent_peer_id,
        done_progress,
        message_type="TASK_PROGRESS",
        target_id=task_id,
        include_candidates=True,
        fallback_addr=fallback_addr,
        send_attempt=lambda host, port, raw: daemon._send_or_log(
            host,
            int(port),
            raw,
            message_type="TASK_PROGRESS",
            target_id=task_id,
        ),
    )

    raw_result = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_RESULT",
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=worker_outcome.result.model_dump(mode="json"),
    )
    send_to_peer_or_log(
        parent_peer_id,
        raw_result,
        message_type="TASK_RESULT",
        target_id=task_id,
        include_candidates=True,
        fallback_addr=fallback_addr,
        send_attempt=lambda host, port, raw: daemon._send_or_log(
            host,
            int(port),
            raw,
            message_type="TASK_RESULT",
            target_id=task_id,
        ),
    )

    audit_logger.log(
        "assignment_executed_locally",
        target_id=task_id,
        target_type="task",
        details={
            "sent_to_parent": True,
            "parent_peer_id": parent_peer_id,
        },
    )


def decode_verified_assist_envelope(raw: bytes, *, expected_msg_type: str) -> dict[str, Any] | None:
    try:
        envelope = Protocol.decode_and_validate(raw)
    except Exception as exc:
        error_text = str(exc)
        if "Replay detected" not in error_text:
            audit_logger.log(
                "assist_envelope_rejected",
                target_id=expected_msg_type,
                target_type="network",
                details={"error": error_text},
            )
            return None
        try:
            envelope_model = Envelope.model_validate(json.loads(raw.decode("utf-8")))
            if not verify_signature(envelope_model):
                raise ValueError("Invalid signature.")
            validate_payload(envelope_model)
            envelope = envelope_model.model_dump()
            audit_logger.log(
                "assist_envelope_replay_revalidated",
                target_id=expected_msg_type,
                target_type="network",
                details={},
            )
        except Exception as replay_exc:
            audit_logger.log(
                "assist_envelope_rejected",
                target_id=expected_msg_type,
                target_type="network",
                details={"error": f"{error_text}; replay_revalidation={replay_exc}"},
            )
            return None
    msg_type = str(envelope.get("msg_type") or "")
    if msg_type != expected_msg_type:
        audit_logger.log(
            "assist_envelope_type_mismatch",
            target_id=expected_msg_type,
            target_type="network",
            details={"actual_msg_type": msg_type},
        )
        return None
    payload = envelope.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    return envelope


def spawn_limited_worker(
    daemon: Any,
    *,
    target: Any,
    args: tuple[Any, ...],
    name: str,
    target_id: str,
) -> bool:
    if not daemon._local_worker_sem.acquire(blocking=False):
        audit_logger.log(
            "local_worker_capacity_exhausted",
            target_id=target_id,
            target_type="daemon",
            details={"worker_limit": int(daemon._local_worker_limit), "worker_name": name},
        )
        return False

    def _runner() -> None:
        try:
            target(*args)
        finally:
            daemon._local_worker_sem.release()

    try:
        threading.Thread(target=_runner, name=name, daemon=True).start()
    except Exception as exc:
        # Thread.start() failed before _runner could run, so its finally-block
        # release will never fire. Release the permit here or capacity leaks
        # permanently (bleeds toward 0 on repeated spawn failures).
        daemon._local_worker_sem.release()
        audit_logger.log(
            "local_worker_spawn_failed",
            target_id=target_id,
            target_type="daemon",
            details={"worker_name": name, "error": str(exc)},
        )
        return False
    return True


def send_or_log(host: str, port: int, payload: bytes, *, message_type: str, target_id: str) -> bool:
    return _send_or_log_via_transport(
        host,
        int(port),
        payload,
        message_type=message_type,
        target_id=target_id,
    )
