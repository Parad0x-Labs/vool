from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.adaptation_dataset import (
    _hive_post_examples,
    _task_result_examples,
    build_adaptation_corpus,
    curate_adaptation_rows,
    score_adaptation_rows,
)


class AdaptationDatasetSchemaCompatibilityTests(unittest.TestCase):
    def test_hive_post_examples_tolerate_missing_moderation_state(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE hive_topics (
                topic_id TEXT PRIMARY KEY,
                created_by_agent_id TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                topic_tags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'open',
                visibility TEXT NOT NULL DEFAULT 'agent_public',
                evidence_mode TEXT NOT NULL DEFAULT 'candidate_only',
                linked_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE hive_posts (
                post_id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                author_agent_id TEXT NOT NULL,
                post_kind TEXT NOT NULL DEFAULT 'analysis',
                stance TEXT NOT NULL DEFAULT 'propose',
                body TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO hive_topics (
                topic_id, created_by_agent_id, title, summary, topic_tags_json,
                status, visibility, evidence_mode, linked_task_id, created_at, updated_at
            ) VALUES (
                'topic-old', 'agent:test', 'Legacy Hive Topic', 'Older schema without moderation columns', '[]',
                'researching', 'agent_public', 'candidate_only', NULL, '2026-03-10T10:00:00+00:00', '2026-03-10T10:00:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO hive_posts (
                post_id, topic_id, author_agent_id, post_kind, stance, body, evidence_refs_json, created_at
            ) VALUES (
                'post-old', 'topic-old', 'agent:test', 'analysis', 'propose',
                'Use the trace rail so operators can see claim, query, artifact, and result flow.',
                '[]', '2026-03-10T10:05:00+00:00'
            )
            """
        )
        conn.commit()
        with mock.patch("core.adaptation_dataset.get_connection", return_value=conn):
            rows = _hive_post_examples(limit=8, filters={})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "hive_post")
        self.assertIn("Legacy Hive Topic", rows[0].instruction)
        conn.close()

    def test_build_adaptation_corpus_keeps_imported_jsonl_intact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir) / "imported.jsonl"
            corpus_path.write_text(
                '{"instruction":"Explain trace rails","output":"They expose claim, queries, artifacts, and status.","source":"imported","metadata":{"ts":"2026-03-10T10:00:00+00:00"}}\n',
                encoding="utf-8",
            )
            with mock.patch(
                "core.adaptation_dataset.get_adaptation_corpus",
                return_value={
                    "corpus_id": "corpus-imported",
                    "source_config": {"imported": True},
                    "filters": {},
                    "output_path": str(corpus_path),
                },
            ), mock.patch("core.adaptation_dataset.update_corpus_build") as update_build:
                result = build_adaptation_corpus("corpus-imported")
        self.assertEqual(result.example_count, 1)
        self.assertEqual(result.output_path, str(corpus_path))
        update_build.assert_called_once()

    def test_curate_rows_drops_low_signal_and_collapses_duplicate_task_lists(self) -> None:
        rows = [
            {
                "instruction": "pull Hive tasks",
                "output": "Available Hive tasks right now (3 total): - [researching] A - [researching] B - [researching] C",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:00:00+00:00"},
            },
            {
                "instruction": "what are the tasks available for Hive mind?",
                "output": "Available Hive tasks right now (3 total): - [researching] A - [researching] B - [researching] C",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:01:00+00:00"},
            },
            {
                "instruction": "lets check hive tasks",
                "output": "I won't fake it: the model returned an invalid tool payload with no intent name.",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:02:00+00:00"},
            },
            {
                "instruction": "lets go with #7d33994f",
                "output": "Started Hive research on `Agent Commons` (#7d33994f). Claim `72eb93bb` is active. The first bounded research pass already ran and posted its result. Bounded queries run: 3. Artifacts packed: 2.",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:03:00+00:00", "share_scope": "hive_mind"},
            },
        ]
        curated = curate_adaptation_rows(rows)
        self.assertEqual(len(curated.rows), 1)
        outputs = [row["output"] for row in curated.rows]
        self.assertTrue(any("Started Hive research on" in item for item in outputs))
        self.assertGreaterEqual(curated.details["dropped_low_signal"], 1)

    def test_score_rows_penalizes_low_signal_conversation_only_corpus(self) -> None:
        rows = [
            {
                "instruction": "pull Hive tasks",
                "output": "Available Hive tasks right now (3 total): - [researching] A - [researching] B - [researching] C",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:00:00+00:00"},
            },
            {
                "instruction": "ok",
                "output": "I'm here and ready to help. What would you like to work on?",
                "source": "conversation",
                "metadata": {"ts": "2026-03-10T10:01:00+00:00"},
            },
        ]
        scored = score_adaptation_rows(rows)
        self.assertLess(float(scored["quality_score"]), 0.68)

    def test_curate_and_score_reward_proof_backed_and_commons_reviewed_signal(self) -> None:
        rows = [
            {
                "instruction": "Task type: research\nTask summary: Validate the strongest solver output.",
                "output": "Accepted solver result with evidence, trace references, and a bounded conclusion that survived review.",
                "source": "task_result",
                "metadata": {
                    "status": "accepted",
                    "confidence": 0.93,
                    "quality_score": 0.9,
                    "helpfulness_score": 0.91,
                    "reviewer_count": 2,
                    "review_support_score": 1.0,
                    "eligibility_state": "eligible",
                    "archive_state": "candidate",
                    "finality_state": "finalized",
                    "proof_backed": True,
                    "durability_reasons": ["artifact_backed", "proof_finalized", "proof_backed"],
                    "created_at": "2026-03-10T10:00:00+00:00",
                },
            },
            {
                "instruction": "Hive topic: Agent Commons\nTopic summary: Promote useful research.\nWrite the next useful Hive contribution.",
                "output": "Reviewed Commons summary with downstream reuse and clear evidence for the next training pass.",
                "source": "hive_post",
                "metadata": {
                    "moderation_state": "approved",
                    "post_kind": "summary",
                    "promotion_review_state": "approved",
                    "promotion_status": "approved",
                    "support_weight": 2.4,
                    "challenge_weight": 0.0,
                    "downstream_use_count": 2,
                    "training_signal_count": 1,
                    "eligibility_state": "eligible",
                    "archive_state": "approved",
                    "created_at": "2026-03-10T10:05:00+00:00",
                },
            },
            {
                "instruction": "Task type: research\nTask summary: Draft something before confirmation.",
                "output": "Accepted-looking result that is still waiting for the fraud window to clear.",
                "source": "task_result",
                "metadata": {
                    "status": "accepted",
                    "confidence": 0.89,
                    "quality_score": 0.86,
                    "helpfulness_score": 0.85,
                    "reviewer_count": 1,
                    "review_support_score": 1.0,
                    "eligibility_state": "ineligible",
                    "archive_state": "transient",
                    "finality_state": "pending",
                    "proof_backed": False,
                    "created_at": "2026-03-10T10:06:00+00:00",
                },
            },
        ]
        curated = curate_adaptation_rows(rows)
        self.assertEqual(curated.details["proof_backed_examples"], 1)
        self.assertEqual(curated.details["finalized_examples"], 1)
        self.assertEqual(curated.details["commons_reviewed_examples"], 1)
        scored = score_adaptation_rows(curated.rows)
        self.assertGreater(float(scored["quality_score"]), 0.7)
        self.assertEqual(scored["details"]["proof_backed_examples"], 1)
        self.assertEqual(scored["details"]["finalized_examples"], 1)
        self.assertEqual(scored["details"]["commons_reviewed_examples"], 1)

    def test_task_result_examples_include_review_metadata(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE task_offers (
                task_id TEXT PRIMARY KEY,
                parent_peer_id TEXT NOT NULL,
                capsule_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                subtask_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                input_capsule_hash TEXT NOT NULL,
                required_capabilities_json TEXT NOT NULL,
                reward_hint_json TEXT NOT NULL DEFAULT '{}',
                max_helpers INTEGER NOT NULL DEFAULT 1,
                priority TEXT NOT NULL DEFAULT 'normal',
                deadline_ts TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE task_results (
                result_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                helper_peer_id TEXT NOT NULL,
                result_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                result_hash TEXT,
                confidence REAL NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                abstract_steps_json TEXT NOT NULL DEFAULT '[]',
                risk_flags_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'submitted',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE task_reviews (
                review_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                helper_peer_id TEXT NOT NULL,
                reviewer_peer_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                helpfulness_score REAL NOT NULL,
                quality_score REAL NOT NULL,
                harmful_flag INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json,
                max_helpers, priority, deadline_ts, status, created_at, updated_at
            ) VALUES (
                'task-1', 'peer-a', 'capsule-1', 'research', 'analysis', 'Summarize trace rail improvements',
                'hash', '[]', '{}', 1, 'normal', '2026-03-10T10:00:00+00:00', 'complete',
                '2026-03-10T10:00:00+00:00', '2026-03-10T10:10:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_results (
                result_id, task_id, helper_peer_id, result_type, summary, confidence,
                evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
            ) VALUES (
                'result-1', 'task-1', 'peer-helper', 'summary',
                'Claim the task, run bounded queries, and post the result with artifacts.',
                0.91, '[]', '[]', '[]', 'accepted',
                '2026-03-10T10:05:00+00:00', '2026-03-10T10:06:00+00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO task_reviews (
                review_id, task_id, helper_peer_id, reviewer_peer_id, outcome,
                helpfulness_score, quality_score, harmful_flag, created_at
            ) VALUES (
                'review-1', 'task-1', 'peer-helper', 'peer-reviewer', 'accepted',
                0.9, 0.88, 0, '2026-03-10T10:07:00+00:00'
            )
            """
        )
        conn.commit()
        with mock.patch("core.adaptation_dataset.get_connection", return_value=conn):
            rows = _task_result_examples(limit=8, filters={})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "task_result")
        self.assertEqual(rows[0].metadata["status"], "accepted")
        self.assertAlmostEqual(rows[0].metadata["quality_score"], 0.88)
        conn.close()


if __name__ == "__main__":
    unittest.main()
