from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.vool_daemon import DaemonConfig, VoolDaemon
from core.capability_tokens import issue_assignment_capability, load_capability_token
from core.task_capsule import build_task_capsule
from core.task_state_machine import current_state, transition
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeshSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "capability_tokens",
                "task_progress_events",
                "task_assignments",
                "task_claims",
                "task_capsules",
                "task_offers",
                "task_state_events",
                "finalized_responses",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()
        self.daemon = VoolDaemon(DaemonConfig(capacity=2))

    def _seed_offer(self, task_id: str):
        capsule = build_task_capsule(
            parent_agent_id=get_local_peer_id(),
            task_id=task_id,
            task_type="research",
            subtask_type="mesh-reconcile-test",
            summary="Reconcile mesh task",
            sanitized_context={
                "problem_class": "research",
                "environment_tags": {"runtime": "test"},
                "abstract_inputs": ["pending claim"],
                "known_constraints": ["No raw execution."],
            },
            allowed_operations=["reason", "research", "summarize"],
            deadline_ts=datetime.now(timezone.utc) + timedelta(hours=1),
            reward_hint={"points": 2, "wnull_pending": 0},
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
                    json.dumps({"points": 2, "wnull_pending": 0}),
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
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    capsule.capsule_id,
                    task_id,
                    get_local_peer_id(),
                    capsule.capsule_hash,
                    json.dumps(capsule.model_dump(mode="json"), sort_keys=True),
                    f"parent-{uuid.uuid4()}",
                    _now_iso(),
                    _now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return capsule

    @pytest.mark.xfail(reason="Pre-existing: task capsule signing not configured in CI")
    def test_reconcile_requeues_blocked_assignment_and_assigns_next_pending_claim(self) -> None:
        task_id = f"task-{uuid.uuid4()}"
        capsule = self._seed_offer(task_id)
        helper_one = "helper-one-123456"
        helper_two = "helper-two-654321"
        claim_one = str(uuid.uuid4())
        claim_two = str(uuid.uuid4())
        assignment_id = str(uuid.uuid4())
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()

        token = issue_assignment_capability(
            task_id=task_id,
            parent_peer_id=get_local_peer_id(),
            helper_peer_id=helper_one,
            capsule=capsule,
            assignment_mode="single",
            lease_seconds=600,
        )

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO task_claims (
                    claim_id, task_id, helper_peer_id, declared_capabilities_json,
                    current_load, host_group_hint_hash, status, claimed_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, NULL, 'accepted', ?, ?)
                """,
                (claim_one, task_id, helper_one, json.dumps(["research"]), old_time, old_time),
            )
            conn.execute(
                """
                INSERT INTO task_claims (
                    claim_id, task_id, helper_peer_id, declared_capabilities_json,
                    current_load, host_group_hint_hash, status, claimed_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, NULL, 'pending', ?, ?)
                """,
                (claim_two, task_id, helper_two, json.dumps(["research"]), old_time, old_time),
            )
            conn.execute(
                """
                INSERT INTO task_assignments (
                    assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id,
                    assignment_mode, status, capability_token_id, lease_expires_at,
                    last_progress_state, last_progress_note, assigned_at, updated_at,
                    progress_updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, 'single', 'blocked', ?, ?, 'blocked', 'worker failed', ?, ?, ?, NULL)
                """,
                (
                    assignment_id,
                    task_id,
                    claim_one,
                    get_local_peer_id(),
                    helper_one,
                    str(token["token_id"]),
                    str(token["expires_at"]),
                    old_time,
                    old_time,
                    old_time,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        transition(entity_type="subtask", entity_id=task_id, to_state="assigned", trace_id=task_id, details={"source": "test"})

        def _endpoint(peer_id: str):
            if peer_id == helper_two:
                return ("127.0.0.1", 49001)
            return None

        with patch("core.agent_runtime.daemon.reap_stale_subtasks", return_value=0), patch(
            "core.agent_runtime.daemon.expire_stale_capability_tokens",
            return_value=0,
        ), patch(
            "core.agent_runtime.daemon.continue_parent_orchestration",
            return_value=SimpleNamespace(action="no_action"),
        ), patch("core.agent_runtime.daemon.endpoint_for_peer", side_effect=_endpoint), patch.object(
            self.daemon,
            "_send_or_log",
            return_value=True,
        ) as send_mock:
            self.daemon._reconcile_mesh_state()

        conn = get_connection()
        try:
            old_assignment = conn.execute(
                "SELECT status FROM task_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            new_assignment = conn.execute(
                """
                SELECT helper_peer_id, status, capability_token_id
                FROM task_assignments
                WHERE task_id = ? AND helper_peer_id = ? AND assignment_id != ?
                ORDER BY assigned_at DESC
                LIMIT 1
                """,
                (task_id, helper_two, assignment_id),
            ).fetchone()
            old_claim = conn.execute(
                "SELECT status FROM task_claims WHERE claim_id = ?",
                (claim_one,),
            ).fetchone()
            offer = conn.execute(
                "SELECT status FROM task_offers WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(str(old_assignment["status"]), "timed_out")
        self.assertEqual(str(old_claim["status"]), "timed_out")
        self.assertIsNotNone(new_assignment)
        self.assertEqual(str(new_assignment["helper_peer_id"]), helper_two)
        self.assertEqual(str(new_assignment["status"]), "active")
        self.assertTrue(str(new_assignment["capability_token_id"]))
        self.assertEqual(str(offer["status"]), "assigned")
        self.assertEqual(current_state("subtask", task_id), "assigned")
        remembered = load_capability_token(str(token["token_id"]))
        self.assertEqual(str((remembered or {}).get("status")), "revoked")
        message_types = [str(call.kwargs.get("message_type")) for call in send_mock.call_args_list]
        self.assertIn("TASK_ASSIGN", message_types)


if __name__ == "__main__":
    unittest.main()
