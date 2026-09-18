from __future__ import annotations

import unittest
import uuid

from core import brain_hive_topic_post_frontdoor
from core.agent_name_registry import claim_agent_name
from core.brain_hive_models import HivePostCreateRequest, HiveTopicCreateRequest
from core.brain_hive_service import BrainHiveService
from storage.db import get_connection
from storage.migrations import run_migrations


def _peer() -> str:
    return f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"


class BrainHiveTopicPostFrontdoorTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "hive_moderation_events",
                "hive_moderation_reviews",
                "hive_write_grants",
                "hive_claim_links",
                "hive_topic_claims",
                "hive_commons_promotion_reviews",
                "hive_commons_promotion_candidates",
                "hive_post_comments",
                "hive_post_endorsements",
                "hive_posts",
                "hive_topics",
                "artifact_manifests",
                "presence_leases",
                "agent_names",
                "compute_credit_ledger",
                "contribution_proof_receipts",
                "contribution_ledger",
                "anti_abuse_signals",
                "scoreboard",
                "task_offers",
                "task_results",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()
        self.service = BrainHiveService()

    def test_frontdoor_create_and_list_keep_display_fields(self) -> None:
        agent_id = _peer()
        claim_agent_name(agent_id, "Pipilon")

        topic = brain_hive_topic_post_frontdoor.create_topic_record(
            self.service,
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Topic frontdoor",
                summary="Create/list topic behavior should stay behind the extracted frontdoor seam.",
                topic_tags=["frontdoor"],
                status="open",
            ),
        )
        post = brain_hive_topic_post_frontdoor.create_post_record(
            self.service,
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Frontdoor-created post should preserve display fields and list ordering.",
            ),
        )

        topics = brain_hive_topic_post_frontdoor.list_topic_records(self.service)
        posts = brain_hive_topic_post_frontdoor.list_post_records(self.service, topic.topic_id)

        self.assertEqual(topic.creator_display_name, "Pipilon")
        self.assertEqual(post.author_display_name, "Pipilon")
        self.assertEqual(topics[0].creator_display_name, "Pipilon")
        self.assertEqual(posts[0].author_display_name, "Pipilon")

    def test_frontdoor_create_topic_reuses_idempotency_key(self) -> None:
        agent_id = _peer()

        first = brain_hive_topic_post_frontdoor.create_topic_record(
            self.service,
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Idempotent topic",
                summary="The extracted frontdoor must keep the idempotent topic-create contract intact.",
                topic_tags=["frontdoor"],
                status="open",
                idempotency_key="frontdoor-topic-retry-1",
            ),
        )
        second = brain_hive_topic_post_frontdoor.create_topic_record(
            self.service,
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Changed title ignored",
                summary="Changed summary should not matter when the same idempotency key is reused.",
                topic_tags=["different"],
                status="solved",
                idempotency_key="frontdoor-topic-retry-1",
            ),
        )

        self.assertEqual(first.topic_id, second.topic_id)
        self.assertEqual(first.title, second.title)


if __name__ == "__main__":
    unittest.main()
