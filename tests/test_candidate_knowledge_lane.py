from __future__ import annotations

import unittest

from core.candidate_knowledge_lane import (
    build_task_hash,
    get_exact_candidate,
    invalidate_candidate,
    recent_candidates,
    record_candidate_output,
)
from storage.db import get_connection
from storage.migrations import run_migrations


class CandidateKnowledgeLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.execute("DELETE FROM learning_shards")
            conn.commit()
        finally:
            conn.close()

    def test_candidate_output_is_stored_separately_from_canonical_memory(self) -> None:
        task_hash = build_task_hash(normalized_input="harden local passwords", task_class="security_hardening", output_mode="action_plan")
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id="task-1",
            trace_id="task-1",
            task_class="security_hardening",
            task_kind="action_plan",
            output_mode="action_plan",
            provider_name="local-qwen-http",
            model_name="qwen",
            raw_output='{"summary":"Protect secrets","steps":["rotate credentials"]}',
            normalized_output="Protect secrets\n- rotate credentials",
            structured_output={"summary": "Protect secrets", "steps": ["rotate credentials"]},
            confidence=0.72,
            trust_score=0.79,
            validation_state="valid",
            metadata={"source": "provider"},
            provenance={"license_name": "Apache-2.0"},
        )
        candidate = get_exact_candidate(task_hash, output_mode="action_plan")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["candidate_id"], candidate_id)
        self.assertEqual(candidate["promotion_state"], "candidate")

        conn = get_connection()
        try:
            canonical_count = int((conn.execute("SELECT COUNT(*) AS cnt FROM learning_shards").fetchone() or {"cnt": 0})["cnt"])
        finally:
            conn.close()
        self.assertEqual(canonical_count, 0)

    def test_invalidate_candidate_marks_candidate_invalid(self) -> None:
        task_hash = build_task_hash(normalized_input="check topology", task_class="system_design", output_mode="summary_block")
        candidate_id = record_candidate_output(
            task_hash=task_hash,
            task_id="task-2",
            trace_id="task-2",
            task_class="system_design",
            task_kind="summarization",
            output_mode="summary_block",
            provider_name="local-qwen-http",
            model_name="qwen",
            raw_output="Topology summary",
            normalized_output="Topology summary",
            structured_output=None,
            confidence=0.5,
            trust_score=0.55,
            validation_state="valid",
        )
        invalidate_candidate(candidate_id, reason="superseded")
        fresh = recent_candidates(limit=10)
        self.assertFalse(any(row["candidate_id"] == candidate_id for row in fresh))


if __name__ == "__main__":
    unittest.main()
