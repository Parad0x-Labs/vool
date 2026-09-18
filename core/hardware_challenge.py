from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core import audit_logger
from core.challenge_engine import issue_challenge
from core.discovery_index import delivery_endpoints_for_peer
from core.task_capsule import build_task_capsule
from network.assist_models import RewardHint, TaskOffer
from network.dht import get_routing_table
from network.protocol import encode_message
from network.signer import get_local_peer_id
from network.transport import send_message
from storage.db import get_connection

_PENDING_BENCHMARKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

COMPUTE_CLASS_TIMEOUTS_MS = {
    "gpu_elite": 500,
    "gpu_basic": 2500,
    "cpu_advanced": 6000,
    "cpu_basic": 15000,
}


def _peer_compute_class(peer_id: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT compute_class
            FROM agent_capabilities
            WHERE peer_id = ?
            LIMIT 1
            """,
            (peer_id,),
        ).fetchone()
        return str(row["compute_class"]) if row and row["compute_class"] else "cpu_basic"
    finally:
        conn.close()


def initiate_random_hardware_challenge() -> None:
    table = get_routing_table()
    target = next((node for node in table.get_all_nodes() if node.peer_id != get_local_peer_id()), None)
    if not target:
        return

    compute_class = _peer_compute_class(target.peer_id)
    task_id = f"benchmark-{uuid.uuid4().hex[:12]}"
    issue_challenge(target.peer_id, "hardware_probe", {"task_id": task_id, "expected_class": compute_class})

    capsule = build_task_capsule(
        parent_agent_id=get_local_peer_id(),
        task_id=task_id,
        task_type="validation",
        subtask_type="hardware_probe",
        summary="Return a validation response immediately for hardware spot-check timing.",
        sanitized_context={
            "problem_class": "hardware_probe",
            "environment_tags": {"challenge": "benchmark"},
            "abstract_inputs": [compute_class, "latency-sensitive validation reply"],
            "known_constraints": ["no execution", "no shell", "respond immediately", "benchmark_probe"],
        },
        allowed_operations=["reason", "validate", "summarize"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=1),
        reward_hint={"points": 0, "wnull_pending": 0},
    )
    offer = TaskOffer(
        task_id=task_id,
        parent_agent_id=get_local_peer_id(),
        capsule_id=capsule.capsule_id,
        task_type="validation",
        subtask_type="hardware_probe",
        summary="Benchmark spot-check for capability timing.",
        required_capabilities=["validation"],
        max_helpers=1,
        priority="low",
        reward_hint=RewardHint(points=0, wnull_pending=0),
        capsule=capsule.model_dump(mode="json"),
        deadline_ts=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    with _LOCK:
        _PENDING_BENCHMARKS[task_id] = {
            "start_time": time.time(),
            "target_peer": target.peer_id,
            "compute_class": compute_class,
        }

    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_OFFER",
        sender_peer_id=get_local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=offer.model_dump(mode="json"),
    )
    sent = False
    attempts = 0
    for host, port in delivery_endpoints_for_peer(
        target.peer_id,
        verified_limit=4,
        include_candidates=True,
        candidate_limit=1,
    ):
        attempts += 1
        if send_message(host, int(port), msg):
            sent = True
            break
    if not sent:
        attempts += 1
        send_message(target.ip, int(target.port), msg)
    audit_logger.log(
        "hardware_challenge_dispatched",
        target_id=target.peer_id,
        target_type="peer",
        details={"benchmark_id": task_id, "expected_class": compute_class, "endpoint_attempts": attempts},
        trace_id=task_id,
    )


def evaluate_benchmark_result(task_id: str, responder_peer_id: str) -> None:
    with _LOCK:
        benchmark = _PENDING_BENCHMARKS.pop(task_id, None)
    if not benchmark or benchmark["target_peer"] != responder_peer_id:
        return

    duration_ms = (time.time() - benchmark["start_time"]) * 1000.0
    expected_class = benchmark["compute_class"]
    cutoff_ms = COMPUTE_CLASS_TIMEOUTS_MS.get(expected_class, 15000)
    passed = duration_ms <= cutoff_ms
    audit_logger.log(
        "hardware_challenge_passed" if passed else "hardware_challenge_failed",
        target_id=responder_peer_id,
        target_type="peer",
        details={
            "benchmark_id": task_id,
            "duration_ms": int(duration_ms),
            "cutoff_ms": cutoff_ms,
            "compute_class": expected_class,
        },
        trace_id=task_id,
    )
    if not passed:
        from core.fraud_engine import record_signal
        from core.scoreboard_engine import slash_score

        record_signal(
            peer_id=responder_peer_id,
            related_peer_id=None,
            task_id=task_id,
            signal_type="fake_hardware_spoofing",
            severity=1.0,
            details={"expected": expected_class, "allowed_ms": cutoff_ms, "actual_ms": duration_ms},
        )
        slash_score(
            peer_id=responder_peer_id,
            score_type="provider",
            amount=50.0,
            reason="hardware_benchmark_timeout",
            related_task_id=task_id,
        )
