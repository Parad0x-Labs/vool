from __future__ import annotations

import unittest
import uuid

from core import brain_hive_write_support
from core.brain_hive_models import HivePostCreateRequest, HiveTopicCreateRequest
from core.brain_hive_moderation import ModerationDecision
from core.brain_hive_service import BrainHiveService
from storage.db import get_connection
from storage.migrations import run_migrations


def _peer() -> str:
    return f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"


class BrainHiveWriteSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "hive_idempotency_keys",
                "hive_moderation_events",
                "hive_moderation_reviews",
                "hive_post_comments",
                "hive_post_endorsements",
                "hive_posts",
                "hive_topics",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()
        self.service = BrainHiveService()

    def test_topic_and_post_guard_helpers_follow_visibility(self) -> None:
        agent_id = _peer()
        public_topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Public Agent Commons lane",
                summary="Safe public commons lane with enough detail for review.",
                topic_tags=["agent_commons"],
                status="open",
                visibility="agent_public",
            )
        )
        private_topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Private Agent Commons lane",
                summary="Internal lane with enough detail for private review.",
                topic_tags=["agent_commons"],
                status="open",
                visibility="agent_private",
            )
        )
        public_post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=public_topic.topic_id,
                author_agent_id=agent_id,
                body="Public commons post with enough substance and evidence context.",
                evidence_refs=[],
            )
        )
        private_post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=private_topic.topic_id,
                author_agent_id=agent_id,
                body="Private commons post with enough substance and evidence context.",
                evidence_refs=[],
            )
        )

        self.assertTrue(brain_hive_write_support.topic_requires_public_guard(public_topic.topic_id))
        self.assertFalse(brain_hive_write_support.topic_requires_public_guard(private_topic.topic_id))
        self.assertTrue(
            brain_hive_write_support.post_requires_public_guard(
                brain_hive_write_support.load_post_row(public_post.post_id)
            )
        )
        self.assertFalse(
            brain_hive_write_support.post_requires_public_guard(
                brain_hive_write_support.load_post_row(private_post.post_id)
            )
        )

    def test_load_post_row_parses_json_fields(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Evidence-rich public lane",
                summary="Safe public lane for evidence parsing coverage.",
                topic_tags=["agent_commons"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Post body with enough substance and an evidence ref.",
                evidence_refs=[{"type": "url", "value": "https://example.com/evidence"}],
            )
        )

        row = brain_hive_write_support.load_post_row(post.post_id)

        self.assertEqual(row["post_id"], post.post_id)
        self.assertEqual(row["evidence_refs"], [{"type": "url", "value": "https://example.com/evidence"}])
        self.assertIsInstance(row["moderation_reasons"], list)

    def test_forced_review_decision_marks_reason_and_floor(self) -> None:
        decision = brain_hive_write_support.forced_review_decision(
            ModerationDecision(
                state="approved",
                score=0.1,
                reasons=["existing"],
                metadata={"source": "test"},
            )
        )

        self.assertEqual(decision.state, "review_required")
        self.assertGreaterEqual(decision.score, 0.35)
        self.assertIn("existing", decision.reasons)
        self.assertIn("scoped write grant forces review", decision.reasons)
        self.assertTrue(decision.metadata["forced_review_required"])
        self.assertEqual(decision.metadata["source"], "test")

    def test_idempotent_result_roundtrip_validates_model(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Receipt roundtrip lane",
                summary="Topic used to prove Hive idempotent receipt roundtrip coverage.",
                topic_tags=["agent_commons"],
                status="open",
            )
        )

        brain_hive_write_support.store_idempotent_result(
            "brain-hive-write-support-roundtrip",
            "hive.test.roundtrip",
            topic,
        )
        cached = brain_hive_write_support.cached_result(
            "brain-hive-write-support-roundtrip",
            type(topic),
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached.topic_id, topic.topic_id)
        self.assertEqual(cached.title, topic.title)


if __name__ == "__main__":
    unittest.main()
