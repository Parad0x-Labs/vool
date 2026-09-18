from __future__ import annotations

import unittest
import uuid

from core.brain_hive_dashboard import build_dashboard_snapshot
from core.brain_hive_models import HiveTopicCreateRequest
from core.brain_hive_service import BrainHiveService
from storage.db import get_connection
from storage.migrations import run_migrations


class BrainHiveDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "hive_commons_promotion_reviews",
                "hive_commons_promotion_candidates",
                "hive_post_comments",
                "hive_post_endorsements",
                "hive_posts",
                "hive_topics",
                "presence_leases",
                "agent_names",
                "scoreboard",
                "contribution_ledger",
                "contribution_proof_receipts",
                "compute_credit_ledger",
                "public_hive_write_quota_events",
                "adaptation_eval_runs",
                "adaptation_jobs",
                "adaptation_corpora",
                "adaptation_loop_state",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()
        self.service = BrainHiveService()

    def test_dashboard_snapshot_includes_proof_and_research_queue(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        entry_id = f"entry-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="World brain dashboard topic",
                summary="Technical audit of watcher proof-of-useful-work rendering with evidence from https://example.test/watcher-proof so research pressure and finalized solver history stay visible.",
                topic_tags=["watcher", "research"],
                status="open",
            )
        )

        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO contribution_ledger (
                    entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type, outcome,
                    helpfulness_score, points_awarded, wnull_pending, wnull_released,
                    compute_credits_pending, compute_credits_released,
                    finality_state, finality_depth, finality_target, confirmed_at, finalized_at,
                    slashed_flag, fraud_window_end_ts, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'assist', 'released', ?, ?, 0, 0, 0, ?, 'finalized', 2, 2, ?, ?, 0, ?, ?, ?)
                """,
                (
                    entry_id,
                    task_id,
                    agent_id,
                    "peer-parent",
                    0.9,
                    9,
                    0.9,
                    "2026-03-10T10:05:00+00:00",
                    "2026-03-10T16:05:00+00:00",
                    "2026-03-10T12:05:00+00:00",
                    "2026-03-10T10:00:00+00:00",
                    "2026-03-10T16:05:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO contribution_proof_receipts (
                    receipt_id, entry_id, task_id, helper_peer_id, parent_peer_id,
                    stage, outcome, finality_state, finality_depth, finality_target,
                    compute_credits, points_awarded, challenge_reason,
                    previous_receipt_id, previous_receipt_hash, receipt_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'finalized', 'released', 'finalized', 2, 2, ?, ?, '', '', '', '', '{}', ?)
                """,
                (
                    f"proof-{uuid.uuid4().hex}",
                    entry_id,
                    task_id,
                    agent_id,
                    "peer-parent",
                    0.9,
                    9,
                    "2026-03-10T16:05:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        snapshot = build_dashboard_snapshot(self.service, topic_limit=8, post_limit=8, agent_limit=8)

        self.assertEqual(snapshot["proof_of_useful_work"]["finalized_count"], 1)
        self.assertTrue(snapshot["proof_of_useful_work"]["recent_receipts"])
        self.assertIn("adaptation_proof", snapshot)
        self.assertTrue(snapshot["research_queue"])
        self.assertEqual(snapshot["research_queue"][0]["topic_id"], topic.topic_id)

    def test_dashboard_snapshot_topics_hide_closed_cleanup_artifacts(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        active = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Active public task",
                summary="Open research work should stay visible on the dashboard topic strip.",
                topic_tags=["research"],
                status="researching",
            )
        )
        _closed = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="[VOOL_SMOKE] Closed cleanup artifact",
                summary="Disposable smoke cleanup should not stay in the default public dashboard topic list.",
                topic_tags=["smoke"],
                status="closed",
            )
        )

        snapshot = build_dashboard_snapshot(self.service, topic_limit=8, post_limit=8, agent_limit=8)
        titles = [str(item.get("title") or "") for item in snapshot["topics"]]

        self.assertIn("Active public task", titles)
        self.assertNotIn("[VOOL_SMOKE] Closed cleanup artifact", titles)
        self.assertEqual(snapshot["topics"][0]["topic_id"], active.topic_id)

    def test_dashboard_snapshot_keeps_partial_and_needs_improvement_topics_visible(self) -> None:
        agent_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        partial = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Partial bounded pass",
                summary="Partially solved work should stay visible so agents can continue it.",
                topic_tags=["research"],
                status="partial",
            )
        )
        needs_improvement = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Needs another pass",
                summary="Send-back work should still appear in the dashboard instead of vanishing.",
                topic_tags=["research"],
                status="needs_improvement",
            )
        )

        snapshot = build_dashboard_snapshot(self.service, topic_limit=8, post_limit=8, agent_limit=8)
        topic_ids = {str(item.get("topic_id") or "") for item in snapshot["topics"]}

        self.assertIn(partial.topic_id, topic_ids)
        self.assertIn(needs_improvement.topic_id, topic_ids)


if __name__ == "__main__":
    unittest.main()
