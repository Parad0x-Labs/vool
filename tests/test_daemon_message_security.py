from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.vool_daemon import DaemonConfig, VoolDaemon
from core.capability_tokens import issue_assignment_capability
from core.task_capsule import build_task_capsule
from network.assist_models import TaskResult
from network.protocol import Protocol, encode_message
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DaemonMessageSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM nonce_cache")
            conn.execute("DELETE FROM capability_tokens")
            conn.execute("DELETE FROM task_progress_events")
            conn.execute("DELETE FROM task_assignments")
            conn.execute("DELETE FROM task_claims")
            conn.execute("DELETE FROM task_capsules")
            conn.execute("DELETE FROM task_offers")
            conn.commit()
        finally:
            conn.close()
        self.daemon = VoolDaemon(DaemonConfig())

    def _seed_capsule(self, task_id: str):
        capsule = build_task_capsule(
            parent_agent_id=get_local_peer_id(),
            task_id=task_id,
            task_type="research",
            subtask_type="daemon-security-test",
            summary="Test daemon assignment execution",
            sanitized_context={
                "problem_class": "research",
                "environment_tags": {"runtime": "test"},
                "abstract_inputs": ["safe signal"],
                "known_constraints": ["No raw execution."],
            },
            allowed_operations=["reason", "research", "summarize"],
            deadline_ts=datetime.now(timezone.utc) + timedelta(hours=1),
            reward_hint={"points": 1, "wnull_pending": 0},
        )
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO task_offers (
                    task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                    input_capsule_hash, required_capabilities_json, reward_hint_json,
                    max_helpers, priority, deadline_ts, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    get_local_peer_id(),
                    capsule.capsule_id,
                    capsule.task_type,
                    capsule.subtask_type,
                    capsule.summary,
                    capsule.capsule_hash,
                    json.dumps(["research"]),
                    json.dumps({"points": 1, "wnull_pending": 0}),
                    1,
                    "normal",
                    capsule.deadline_ts.isoformat(),
                    "assigned",
                    _now_iso(),
                    _now_iso(),
                ),
            )
            conn.execute(
                """
                INSERT INTO task_capsules (
                    capsule_id, task_id, parent_peer_id, capsule_hash, capsule_json,
                    parent_task_ref, verification_of_task_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    capsule.capsule_id,
                    task_id,
                    get_local_peer_id(),
                    capsule.capsule_hash,
                    json.dumps(capsule.model_dump(mode="json"), sort_keys=True),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return capsule

    def test_auto_review_rejects_unsigned_payload(self) -> None:
        raw = json.dumps(
            {
                "msg_type": "TASK_RESULT",
                "payload": {
                    "result_id": str(uuid.uuid4()),
                    "task_id": str(uuid.uuid4()),
                    "helper_agent_id": get_local_peer_id(),
                    "result_type": "research_summary",
                    "summary": "unsigned result",
                    "confidence": 0.9,
                    "evidence": [],
                    "abstract_steps": [],
                    "risk_flags": [],
                    "result_hash": "a" * 64,
                    "timestamp": _now_iso(),
                },
            }
        ).encode("utf-8")

        with patch("core.agent_runtime.daemon.auto_review_task_result") as review_mock:
            self.daemon._maybe_auto_review_result_from_raw(raw, ("127.0.0.1", 49152))
            review_mock.assert_not_called()

    def test_auto_review_accepts_verified_signed_payload(self) -> None:
        peer_id = get_local_peer_id()
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_RESULT",
            sender_peer_id=peer_id,
            nonce=uuid.uuid4().hex,
            payload={
                "result_id": str(uuid.uuid4()),
                "task_id": str(uuid.uuid4()),
                "helper_agent_id": peer_id,
                "result_type": "research_summary",
                "summary": "verified result",
                "confidence": 0.9,
                "evidence": [],
                "abstract_steps": [],
                "risk_flags": [],
                "result_hash": "b" * 64,
                "timestamp": _now_iso(),
            },
        )

        with patch("core.agent_runtime.daemon.auto_review_task_result", return_value=None) as review_mock:
            self.daemon._maybe_auto_review_result_from_raw(raw, ("127.0.0.1", 49152))
            review_mock.assert_called_once()

    def test_decode_verified_assist_envelope_allows_replay_revalidation(self) -> None:
        peer_id = get_local_peer_id()
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_RESULT",
            sender_peer_id=peer_id,
            nonce=uuid.uuid4().hex,
            payload={
                "result_id": str(uuid.uuid4()),
                "task_id": str(uuid.uuid4()),
                "helper_agent_id": peer_id,
                "result_type": "research_summary",
                "summary": "verified result replay path",
                "confidence": 0.9,
                "evidence": [],
                "abstract_steps": [],
                "risk_flags": [],
                "result_hash": "c" * 64,
                "timestamp": _now_iso(),
            },
        )

        # First decode stores nonce in replay cache.
        Protocol.decode_and_validate(raw)

        # Second decode should still pass for local post-processing via replay-safe verification.
        envelope = self.daemon._decode_verified_assist_envelope(raw, expected_msg_type="TASK_RESULT")
        self.assertIsNotNone(envelope)
        self.assertEqual(str((envelope or {}).get("msg_type")), "TASK_RESULT")

    def test_assignment_execution_rejects_unsigned_payload(self) -> None:
        raw = json.dumps(
            {
                "msg_type": "TASK_ASSIGN",
                "payload": {
                    "assignment_id": str(uuid.uuid4()),
                    "task_id": str(uuid.uuid4()),
                    "claim_id": str(uuid.uuid4()),
                    "parent_agent_id": get_local_peer_id(),
                    "helper_agent_id": get_local_peer_id(),
                    "assignment_mode": "single",
                    "timestamp": _now_iso(),
                },
            }
        ).encode("utf-8")
        with patch("core.agent_runtime.daemon.load_task_capsule_for_task") as load_mock:
            self.daemon._maybe_execute_local_assignment_from_raw(raw, ("127.0.0.1", 49152))
            load_mock.assert_not_called()

    @pytest.mark.xfail(reason="Pre-existing: task capsule signing not configured in CI")
    def test_assignment_execution_rejects_verified_payload_without_capability_token(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        self._seed_capsule(task_id)
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_ASSIGN",
            sender_peer_id=get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "assignment_id": str(uuid.uuid4()),
                "task_id": task_id,
                "claim_id": str(uuid.uuid4()),
                "parent_agent_id": get_local_peer_id(),
                "helper_agent_id": get_local_peer_id(),
                "assignment_mode": "single",
                "timestamp": _now_iso(),
            },
        )
        with patch("core.agent_runtime.daemon.peer_trust", return_value=0.9), patch(
            "core.agent_runtime.daemon.run_task_capsule"
        ) as run_mock:
            self.daemon._maybe_execute_local_assignment_from_raw(raw, ("127.0.0.1", 49152))
            run_mock.assert_not_called()

    @pytest.mark.xfail(reason="Pre-existing: task capsule signing not configured in CI")
    def test_assignment_execution_accepts_signed_capability_and_emits_progress(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = self._seed_capsule(task_id)
        assignment_id = str(uuid.uuid4())
        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=get_local_peer_id(),
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )
        raw = encode_message(
            msg_id=str(uuid.uuid4()),
            msg_type="TASK_ASSIGN",
            sender_peer_id=get_local_peer_id(),
            nonce=uuid.uuid4().hex,
            payload={
                "assignment_id": assignment_id,
                "task_id": task_id,
                "claim_id": str(uuid.uuid4()),
                "parent_agent_id": get_local_peer_id(),
                "helper_agent_id": get_local_peer_id(),
                "assignment_mode": "single",
                "capability_token": token,
                "timestamp": _now_iso(),
            },
        )
        worker_outcome = SimpleNamespace(
            result=TaskResult(
                result_id=str(uuid.uuid4()),
                task_id=task_id,
                helper_agent_id=get_local_peer_id(),
                result_type="research_summary",
                summary="completed helper result",
                confidence=0.92,
                evidence=[],
                abstract_steps=[],
                risk_flags=[],
                result_hash="d" * 64,
                timestamp=datetime.now(timezone.utc),
            ),
            accepted_scope=True,
        )

        with patch("core.agent_runtime.daemon.peer_trust", return_value=0.9), patch(
            "core.agent_runtime.daemon.run_task_capsule",
            return_value=worker_outcome,
        ) as run_mock, patch.object(self.daemon, "_send_or_log", return_value=True) as send_mock:
            self.daemon._maybe_execute_local_assignment_from_raw(raw, ("127.0.0.1", 49152))

        run_mock.assert_called_once()
        message_types = [str(call.kwargs.get("message_type")) for call in send_mock.call_args_list]
        self.assertGreaterEqual(message_types.count("TASK_PROGRESS"), 2)
        self.assertIn("TASK_RESULT", message_types)


if __name__ == "__main__":
    unittest.main()
