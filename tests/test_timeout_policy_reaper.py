from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from core.task_state_machine import current_state, transition
from core.timeout_policy import reap_stale_subtasks
from storage.db import get_connection
from storage.migrations import run_migrations


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TimeoutPolicyReaperTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_reaper_marks_stale_assigned_subtask_timed_out(self) -> None:
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=600)
        task_id = f"task-timeout-{uuid.uuid4()}"
        claim_id = str(uuid.uuid4())
        assignment_id = str(uuid.uuid4())
        parent_peer_id = "p" * 16
        helper_peer_id = "h" * 16

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
                    parent_peer_id,
                    str(uuid.uuid4()),
                    "assist",
                    "research",
                    "timeout test",
                    "f" * 64,
                    json.dumps(["research"]),
                    "{}",
                    1,
                    "normal",
                    _iso(now + timedelta(hours=1)),
                    "assigned",
                    _iso(old),
                    _iso(old),
                ),
            )
            conn.execute(
                """
                INSERT INTO task_claims (
                    claim_id, task_id, helper_peer_id, declared_capabilities_json,
                    current_load, host_group_hint_hash, status, claimed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    task_id,
                    helper_peer_id,
                    json.dumps(["research"]),
                    0,
                    None,
                    "pending",
                    _iso(old),
                    _iso(old),
                ),
            )
            conn.execute(
                """
                INSERT INTO task_assignments (
                    assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id,
                    assignment_mode, status, assigned_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment_id,
                    task_id,
                    claim_id,
                    parent_peer_id,
                    helper_peer_id,
                    "single",
                    "active",
                    _iso(old),
                    _iso(old),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        transition(
            entity_type="subtask",
            entity_id=task_id,
            to_state="assigned",
            trace_id=task_id,
            details={"source": "test"},
        )

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE task_state_events SET created_at = ? WHERE entity_type = 'subtask' AND entity_id = ?",
                (_iso(old), task_id),
            )
            conn.commit()
        finally:
            conn.close()

        timed_out = reap_stale_subtasks(limit=50)
        self.assertGreaterEqual(timed_out, 1)
        self.assertEqual(current_state("subtask", task_id), "timed_out")

        conn = get_connection()
        try:
            offer = conn.execute("SELECT status FROM task_offers WHERE task_id = ?", (task_id,)).fetchone()
            assignment = conn.execute(
                "SELECT status FROM task_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            claim = conn.execute("SELECT status FROM task_claims WHERE claim_id = ?", (claim_id,)).fetchone()
        finally:
            conn.close()

        self.assertEqual(str(offer["status"]), "open")
        self.assertEqual(str(assignment["status"]), "timed_out")
        self.assertEqual(str(claim["status"]), "timed_out")


if __name__ == "__main__":
    unittest.main()

