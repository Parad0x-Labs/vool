from __future__ import annotations

import unittest

from core.fraud_engine import assess_assist_reward
from core.task_state_machine import current_state, transition
from core.trace_id import ensure_trace
from storage.migrations import run_migrations


class FraudAndTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_self_farm_triggers_rejection(self) -> None:
        assessment = assess_assist_reward(
            task_id="task-fraud-1",
            parent_peer_id="same-peer",
            helper_peer_id="same-peer",
            parent_host_group_hint_hash=None,
            helper_host_group_hint_hash=None,
        )
        self.assertTrue(assessment.reject_reward)
        self.assertIn("self_farm_same_peer", assessment.reasons)

    def test_helper_timeout_transition_is_allowed(self) -> None:
        task_id = "task-timeout-1"
        ensure_trace(task_id, trace_id=task_id)
        if current_state("subtask", task_id) is None:
            transition(entity_type="subtask", entity_id=task_id, to_state="assigned", trace_id=task_id, details={"source": "test"})
        transition(entity_type="subtask", entity_id=task_id, to_state="timed_out", trace_id=task_id, details={"reason": "helper_timeout"})
        self.assertEqual(current_state("subtask", task_id), "timed_out")


if __name__ == "__main__":
    unittest.main()
