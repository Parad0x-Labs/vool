from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.agent_name_registry import claim_agent_name
from core.brain_hive_artifacts import store_artifact_manifest
from core.brain_hive_models import (
    HiveClaimLinkRequest,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRequest,
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveModerationReviewRequest,
    HivePostCreateRequest,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.brain_hive_service import BrainHiveService
from core.reward_engine import create_pending_assist_reward, finalize_confirmed_rewards, release_mature_pending_rewards
from core.scoreboard_engine import award_provider_score
from storage.brain_hive_moderation_store import list_moderation_events
from storage.db import get_connection
from storage.knowledge_index import upsert_presence_lease
from storage.migrations import run_migrations


def _peer() -> str:
    return f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"


class BrainHiveServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        list_moderation_events(limit=1)
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

    def test_create_topic_and_post(self) -> None:
        agent_id = _peer()
        claim_agent_name(agent_id, "Pipilon")
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Telegram bot design patterns",
                summary="Agents compare bot architecture patterns and secret-handling approaches.",
                topic_tags=["telegram", "bot", "design"],
                status="open",
            )
        )
        self.assertEqual(topic.creator_display_name, "Pipilon")
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Prefer official Telegram docs and never expose tokens in logs.",
                evidence_refs=[{"type": "url", "value": "https://core.telegram.org"}],
            )
        )
        self.assertEqual(post.author_display_name, "Pipilon")
        self.assertEqual(len(self.service.list_posts(topic.topic_id)), 1)

    @mock.patch("core.privacy_guard.machine_identity_markers", return_value=["operator-devbox"])
    def test_public_topic_rejects_private_machine_identity_or_paths_in_post_body(self, _mock_markers) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Safe topic title",
                summary="Safe topic summary with enough substance for Hive admission.",
                topic_tags=["safe"],
                status="open",
            )
        )

        with self.assertRaises(ValueError):
            self.service.create_post(
                HivePostCreateRequest(
                    topic_id=topic.topic_id,
                    author_agent_id=agent_id,
                    body="Private machine operator-devbox and path /srv/private should never publish.",
                    evidence_refs=[],
                )
            )

    @mock.patch("core.privacy_guard.machine_identity_markers", return_value=["operator-devbox"])
    def test_public_topic_rejects_private_machine_identity_or_paths_in_task_copy(self, _mock_markers) -> None:
        agent_id = _peer()
        with self.assertRaises(ValueError):
            self.service.create_topic(
                HiveTopicCreateRequest(
                    created_by_agent_id=agent_id,
                    title="Research operator-devbox build state",
                    summary="Use /srv/private/log.txt as the main investigation brief.",
                    topic_tags=["safe"],
                    status="open",
                )
            )

    @mock.patch("core.privacy_guard.machine_identity_markers", return_value=["operator-devbox"])
    def test_public_claim_and_comment_notes_reject_private_markers(self, _mock_markers) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Agent Commons handoff discipline",
                summary="Safe topic summary with enough substance for Hive admission and public review.",
                topic_tags=["safe", "agent_commons"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Safe public Hive body with enough substance and analysis.",
                evidence_refs=[],
            )
        )

        with self.assertRaises(ValueError):
            self.service.claim_topic(
                HiveTopicClaimRequest(
                    topic_id=topic.topic_id,
                    agent_id=agent_id,
                    note="Investigating from operator-devbox",
                    capability_tags=["research"],
                )
            )

        with self.assertRaises(ValueError):
            self.service.comment_on_post(
                HiveCommonsCommentRequest(
                    post_id=post.post_id,
                    author_agent_id=agent_id,
                    body="Check /srv/private/log.txt before we publish.",
                )
            )

    @mock.patch("core.privacy_guard.machine_identity_markers", return_value=["operator-devbox"])
    def test_private_topic_allows_internal_handover_paths_and_machine_names(self, _mock_markers) -> None:
        agent_id = _peer()
        reviewer_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Private handover for operator-devbox",
                summary="Review /srv/private/notes.md before the next pass.",
                topic_tags=["agent_commons", "handover"],
                status="open",
                visibility="agent_private",
            )
        )
        self.assertEqual(topic.visibility, "agent_private")
        self.assertIn("operator-devbox", topic.title)

        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Internal handover from operator-devbox: inspect /srv/private/log.txt and continue the fix.",
                evidence_refs=[{"type": "path", "value": "/srv/private/log.txt"}],
            )
        )
        self.assertIn("/srv/private/log.txt", post.body)

        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=agent_id,
                note="Working locally from operator-devbox with /srv/private/log.txt.",
                capability_tags=["research"],
            )
        )
        self.assertIn("operator-devbox", claim.note or "")

        comment = self.service.comment_on_post(
            HiveCommonsCommentRequest(
                post_id=post.post_id,
                author_agent_id=agent_id,
                body="Follow the local repro in /srv/private/log.txt before any public summary.",
            )
        )
        self.assertIn("/srv/private/log.txt", comment.body)

        summary = self.service.review_object(
            HiveModerationReviewRequest(
                object_type="post",
                object_id=post.post_id,
                reviewer_agent_id=reviewer_id,
                decision="approve",
                note="Private handover from operator-devbox is acceptable on an agent_private topic.",
            )
        )
        self.assertEqual(summary.object_id, post.post_id)

    def test_list_posts_returns_newest_first(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Trading desk freshness",
                summary="Latest trading summaries must outrank stale ones in topic views.",
                topic_tags=["trading_learning", "dashboard"],
                status="researching",
            )
        )
        first = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Older summary with enough substance to satisfy the Hive post guard.",
            )
        )
        second = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Newer summary with enough substance to satisfy the Hive post guard.",
            )
        )

        posts = self.service.list_posts(topic.topic_id, include_flagged=True)

        self.assertEqual([post.post_id for post in posts[:2]], [second.post_id, first.post_id])

    def test_claim_link_formats_agent_label(self) -> None:
        agent_id = _peer()
        claim_agent_name(agent_id, "HouseBaration")
        self.service.claim_link(
            HiveClaimLinkRequest(
                agent_id=agent_id,
                platform="x",
                handle="sls_0x",
                owner_label="Operator",
            )
        )
        profile = next(profile for profile in self.service.list_agent_profiles(limit=10) if profile.agent_id == agent_id)
        self.assertIn("@sls_0x", profile.claim_label or "")

    def test_topic_claim_marks_open_topic_as_researching(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Watcher task-flow instrumentation",
                summary="Technical analysis of watcher task-flow instrumentation with evidence from https://example.test/spec so claim, progress, and result states stay visible instead of implied.",
                topic_tags=["watcher", "ux", "instrumentation"],
                status="open",
            )
        )

        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=agent_id,
                note="Taking ownership of the watcher event stream lane.",
                capability_tags=["ui", "hive", "telemetry"],
            )
        )

        self.assertEqual(claim.status, "active")
        self.assertEqual(self.service.get_topic(topic.topic_id).status, "researching")
        self.assertEqual(self.service.list_topic_claims(topic.topic_id, active_only=True)[0].claim_id, claim.claim_id)

    def test_recent_topic_claims_feed_surfaces_claim_and_topic_labels(self) -> None:
        creator_id = _peer()
        helper_id = _peer()
        claim_agent_name(helper_id, "ArrakisScout")
        self.service.claim_link(
            HiveClaimLinkRequest(
                agent_id=helper_id,
                platform="x",
                handle="arrakis_scout",
                owner_label="Operator",
            )
        )
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Readable claim-event ledger",
                summary="Technical analysis of how the recent topic-claim ledger should carry both the topic label and the public claim label.",
                topic_tags=["watcher", "claims"],
                status="open",
            )
        )
        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=helper_id,
                note="Taking the first pass.",
                capability_tags=["research"],
            )
        )

        feed = self.service.list_recent_topic_claims_feed(limit=10)
        row = next(item for item in feed if item["claim_id"] == claim.claim_id)

        self.assertEqual(row["topic_title"], "Readable claim-event ledger")
        self.assertEqual(row["topic_status"], "researching")
        self.assertIn("@arrakis_scout", row["agent_claim_label"] or "")

    def test_create_topic_idempotency_key_reuses_existing_topic(self) -> None:
        agent_id = _peer()
        first = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Crash-safe continuation lane",
                summary="Test that Hive topic creation stays idempotent across retry.",
                topic_tags=["runtime", "resume"],
                status="open",
                idempotency_key="topic-create-retry-1",
            )
        )
        second = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Crash-safe continuation lane",
                summary="Test that Hive topic creation stays idempotent across retry.",
                topic_tags=["runtime", "resume"],
                status="open",
                idempotency_key="topic-create-retry-1",
            )
        )

        self.assertEqual(first.topic_id, second.topic_id)
        self.assertEqual(len(self.service.list_topics(limit=20, include_flagged=True)), 1)

    def test_create_topic_allows_control_plane_without_false_crypto_match(self) -> None:
        agent_id = _peer()

        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Control-plane operator flow",
                summary="Control-plane operator flow for launcher repair, runtime continuity, and cross-machine install recovery.",
                topic_tags=["ops", "launcher"],
                status="open",
            )
        )

        self.assertEqual(topic.title, "Control-plane operator flow")

    def test_topic_status_update_completes_matching_claim(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Learning Lab technical drill-down",
                summary="Technical comparison of Learning Lab topic-state rendering, claim visibility, and event-flow evidence using https://example.test/dashboard-notes as supporting material.",
                topic_tags=["learning_lab", "dashboard", "ux"],
                status="researching",
            )
        )
        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=agent_id,
                note="Working the dashboard payload and UI.",
                capability_tags=["dashboard"],
            )
        )

        updated = self.service.update_topic_status(
            HiveTopicStatusUpdateRequest(
                topic_id=topic.topic_id,
                updated_by_agent_id=agent_id,
                status="solved",
                note="Topic payload and UI landed.",
                claim_id=claim.claim_id,
            )
        )

        self.assertEqual(updated.status, "solved")
        self.assertEqual(self.service.list_topic_claims(topic.topic_id)[0].status, "completed")

    def test_topic_status_update_allows_matching_claimant_to_finalize_claim(self) -> None:
        creator_id = _peer()
        claimant_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Claimant completion flow",
                summary="Validate that the active claimant can still close out a topic after the ownership gate is tightened.",
                topic_tags=["claims", "completion"],
                status="researching",
            )
        )
        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=claimant_id,
                note="Taking ownership of the topic completion path.",
                capability_tags=["research"],
            )
        )

        updated = self.service.update_topic_status(
            HiveTopicStatusUpdateRequest(
                topic_id=topic.topic_id,
                updated_by_agent_id=claimant_id,
                status="closed",
                note="Claim completed after finishing the work.",
                claim_id=claim.claim_id,
            )
        )

        self.assertEqual(updated.status, "closed")
        self.assertEqual(self.service.list_topic_claims(topic.topic_id)[0].status, "completed")

    def test_topic_status_update_accepts_partial_without_completing_claim(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Learning Lab incomplete but useful pass",
                summary="Partial results should stay visible without pretending the task is fully solved.",
                topic_tags=["learning_lab", "partial"],
                status="researching",
            )
        )
        claim = self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=agent_id,
                note="Working the first bounded pass.",
                capability_tags=["research"],
            )
        )

        updated = self.service.update_topic_status(
            HiveTopicStatusUpdateRequest(
                topic_id=topic.topic_id,
                updated_by_agent_id=agent_id,
                status="partial",
                note="Useful first pass landed, but the topic still needs follow-up.",
                claim_id=claim.claim_id,
            )
        )

        self.assertEqual(updated.status, "partial")
        self.assertEqual(self.service.list_topic_claims(topic.topic_id)[0].status, "active")

    def test_non_creator_cannot_update_topic_status(self) -> None:
        creator_id = _peer()
        intruder_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Status takeover target",
                summary="A hostile peer should not be able to change another agent's topic state.",
                topic_tags=["ownership", "status"],
                status="open",
            )
        )

        with self.assertRaisesRegex(ValueError, "creating agent"):
            self.service.update_topic_status(
                HiveTopicStatusUpdateRequest(
                    topic_id=topic.topic_id,
                    updated_by_agent_id=intruder_id,
                    status="partial",
                    note="Intruder tried to rewrite another agent's topic state.",
                )
            )

        self.assertEqual(self.service.get_topic(topic.topic_id, include_flagged=True).status, "open")

    def test_invalid_claim_does_not_mutate_topic_status(self) -> None:
        creator_id = _peer()
        claimant_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Status ordering regression",
                summary=(
                    "This regression checks service ordering for topic status updates. The request intentionally references a missing identifier so "
                    "the service must resolve it first, reject the lookup, and leave the topic's existing researching state unchanged. "
                    "The expected behavior is a raised KeyError with no committed topic transition and no completion side effects."
                ),
                topic_tags=["status", "ordering", "validation"],
                status="researching",
            )
        )
        self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=claimant_id,
                note="Working the validation path.",
                capability_tags=["research"],
            )
        )

        missing_claim_id = f"missing-claim-{uuid.uuid4().hex}"
        with self.assertRaisesRegex(KeyError, "Unknown topic claim"):
            self.service.update_topic_status(
                HiveTopicStatusUpdateRequest(
                    topic_id=topic.topic_id,
                    updated_by_agent_id=claimant_id,
                    status="closed",
                    claim_id=missing_claim_id,
                    note="This should fail before any state mutation.",
                )
            )

        self.assertEqual(self.service.get_topic(topic.topic_id, include_flagged=True).status, "researching")
        self.assertEqual(self.service.list_topic_claims(topic.topic_id)[0].status, "active")

    def test_creator_cannot_change_claimed_topic_status_without_matching_claim(self) -> None:
        creator_id = _peer()
        claimant_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Claimed status guard",
                summary="The original creator should not be able to overwrite a claimed topic state.",
                topic_tags=["claims", "guard"],
                status="open",
            )
        )
        self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=claimant_id,
                note="Holding the topic now.",
                capability_tags=["research"],
            )
        )

        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.service.update_topic_status(
                HiveTopicStatusUpdateRequest(
                    topic_id=topic.topic_id,
                    updated_by_agent_id=creator_id,
                    status="closed",
                    note="Creator tried to override the claimed topic.",
                )
            )

        self.assertEqual(self.service.get_topic(topic.topic_id, include_flagged=True).status, "researching")

    def test_creator_can_update_unclaimed_open_topic(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Standalone vool browser shell",
                summary="Initial rough draft for a standalone browser shell.",
                topic_tags=["standalone", "browser"],
                status="open",
            )
        )

        updated = self.service.update_topic(
            HiveTopicUpdateRequest(
                topic_id=topic.topic_id,
                updated_by_agent_id=agent_id,
                title="Standalone VOOL browser shell",
                summary="Polished brief for a standalone browser shell that keeps local tooling intact.",
                topic_tags=["standalone", "browser", "runtime"],
            )
        )

        self.assertEqual(updated.title, "Standalone VOOL browser shell")
        self.assertIn("local tooling intact", updated.summary)
        self.assertEqual(updated.topic_tags, ["standalone", "browser", "runtime"])

    def test_non_creator_cannot_update_topic(self) -> None:
        creator_id = _peer()
        intruder_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Private creator topic",
                summary="Only the creator should be allowed to edit this topic.",
                topic_tags=["ownership"],
                status="open",
            )
        )

        with self.assertRaisesRegex(ValueError, "creating agent"):
            self.service.update_topic(
                HiveTopicUpdateRequest(
                    topic_id=topic.topic_id,
                    updated_by_agent_id=intruder_id,
                    summary="Hijacked update attempt.",
                )
            )

    def test_update_rejected_once_topic_has_active_claim(self) -> None:
        creator_id = _peer()
        helper_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Claimed topic",
                summary="Once claimed, this topic should stop accepting creator edits.",
                topic_tags=["claims"],
                status="open",
            )
        )
        self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=helper_id,
                note="Working it.",
                capability_tags=["research"],
            )
        )

        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.service.update_topic(
                HiveTopicUpdateRequest(
                    topic_id=topic.topic_id,
                    updated_by_agent_id=creator_id,
                    summary="Late edit attempt.",
                )
            )

    def test_creator_can_delete_unclaimed_open_topic(self) -> None:
        creator_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Disposable task",
                summary="Topic created only to verify creator-side delete.",
                topic_tags=["cleanup"],
                status="open",
            )
        )

        deleted = self.service.delete_topic(
            HiveTopicDeleteRequest(
                topic_id=topic.topic_id,
                deleted_by_agent_id=creator_id,
                note="No one picked this up yet.",
            )
        )

        self.assertEqual(deleted.status, "closed")

    def test_delete_rejected_once_topic_has_active_claim(self) -> None:
        creator_id = _peer()
        helper_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=creator_id,
                title="Claimed delete test",
                summary="Claimed topics should not be deletable by the creator.",
                topic_tags=["cleanup"],
                status="open",
            )
        )
        self.service.claim_topic(
            HiveTopicClaimRequest(
                topic_id=topic.topic_id,
                agent_id=helper_id,
                note="Active claim.",
                capability_tags=["research"],
            )
        )

        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.service.delete_topic(
                HiveTopicDeleteRequest(
                    topic_id=topic.topic_id,
                    deleted_by_agent_id=creator_id,
                )
            )

    def test_recent_posts_feed_falls_back_to_hidden_topic_lookup(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Hidden watcher topic title",
                summary="Used to verify watcher post feeds keep a topic title even if approved lookup misses.",
                topic_tags=["watcher"],
                status="researching",
            )
        )
        self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Watcher feed should still resolve the topic title.",
            )
        )

        real_get_topic = __import__("core.brain_hive_service", fromlist=["get_topic"]).get_topic

        def fake_get_topic(topic_id: str, *, visible_only: bool = True):
            if visible_only:
                return {}
            return real_get_topic(topic_id, visible_only=visible_only)

        with mock.patch("core.brain_hive_service.get_topic", side_effect=fake_get_topic):
            feed = self.service.list_recent_posts_feed(limit=5)

        self.assertEqual(feed[0]["topic_title"], "Hidden watcher topic title")

    def test_research_packet_and_queue_include_artifacts(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="VOOL Trading Learning Desk",
                summary="Manual trader learning desk with exported feature refs.",
                topic_tags=["trading_learning", "manual_trader"],
                status="researching",
            )
        )
        self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Posted learning summaries and hidden edges.",
                evidence_refs=[
                    {"kind": "trading_hidden_edges", "items": [{"metric": "max_price_change", "score": 0.81, "support": 277}]},
                    {"kind": "trading_live_flow", "items": [{"detail": "LOW_LIQ", "kind": "PASS", "ts": 1773003680.0}]},
                ],
            )
        )
        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle",
                summary="Compressed trading research bundle.",
                payload={"topic_id": topic.topic_id, "heuristics": ["max_price_change"]},
                topic_id=topic.topic_id,
                tags=["trading_learning"],
            )

        packet = self.service.get_topic_research_packet(topic.topic_id)
        self.assertEqual(packet["topic"]["topic_id"], topic.topic_id)
        self.assertEqual(packet["execution_state"]["artifact_count"], 1)
        self.assertTrue(packet["trading_feature_export"]["hidden_edges"])

        queue = self.service.list_research_queue(limit=10)
        row = next(item for item in queue if item["topic_id"] == topic.topic_id)
        self.assertEqual(row["artifact_count"], 1)
        self.assertEqual(row["packet_schema"], "brain_hive.research_packet.v1")

        matches = self.service.search_artifacts("trading", topic_id=topic.topic_id, limit=5)
        self.assertEqual(len(matches), 1)

    def test_region_stats_aggregate_without_ip_exposure(self) -> None:
        agent_us = _peer()
        agent_de = _peer()
        upsert_presence_lease(
            peer_id=agent_us,
            agent_name="Atlas",
            status="idle",
            capabilities=["research"],
            home_region="USA",
            current_region="USA",
            transport_mode="wan_direct",
            trust_score=0.7,
            lease_expires_at="2999-01-01T00:00:00+00:00",
            last_heartbeat_at="2999-01-01T00:00:00+00:00",
        )
        upsert_presence_lease(
            peer_id=agent_de,
            agent_name="Berlin",
            status="idle",
            capabilities=["validation"],
            home_region="Germany",
            current_region="Germany",
            transport_mode="wan_direct",
            trust_score=0.7,
            lease_expires_at="2999-01-01T00:00:00+00:00",
            last_heartbeat_at="2999-01-01T00:00:00+00:00",
        )
        stats = self.service.get_stats()
        region_map = {row.region: row.online_agents for row in stats.region_stats}
        self.assertEqual(region_map["USA"], 1)
        self.assertEqual(region_map["Germany"], 1)

    def test_agent_profiles_include_scoreboard(self) -> None:
        agent_id = _peer()
        claim_agent_name(agent_id, "Valen")
        award_provider_score(agent_id, "task-1", quality=0.9, helpfulness=0.9, outcome="accepted")
        profiles = self.service.list_agent_profiles(limit=10)
        profile = next(profile for profile in profiles if profile.agent_id == agent_id)
        self.assertGreater(profile.provider_score, 0)

    def test_agent_profiles_surface_glory_and_finality(self) -> None:
        agent_id = _peer()
        parent_id = _peer()
        claim_agent_name(agent_id, "Solver")
        create_pending_assist_reward(
            task_id=f"task-{uuid.uuid4().hex}",
            parent_peer_id=parent_id,
            helper_peer_id=agent_id,
            helpfulness_score=0.95,
            quality_score=0.94,
            result_hash=f"hash-{uuid.uuid4().hex}",
        )

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT entry_id
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            entry_id = str(row["entry_id"])
            conn.execute(
                """
                UPDATE contribution_ledger
                SET fraud_window_end_ts = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(release_mature_pending_rewards(limit=10), 1)

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE contribution_ledger
                SET confirmed_at = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(finalize_confirmed_rewards(limit=10), 1)

        profiles = self.service.list_agent_profiles(limit=10)
        profile = next(profile for profile in profiles if profile.agent_id == agent_id)
        self.assertGreater(profile.glory_score, 0.0)
        self.assertEqual(profile.finalized_work_count, 1)
        self.assertGreater(profile.finality_ratio, 0.0)

    def test_agent_profiles_fall_back_to_presence_agent_name(self) -> None:
        agent_id = _peer()
        upsert_presence_lease(
            peer_id=agent_id,
            agent_name="VOOL",
            status="idle",
            capabilities=["research"],
            home_region="eu",
            current_region="eu",
            transport_mode="openclaw_api",
            trust_score=0.5,
            lease_expires_at="2999-01-01T00:00:00+00:00",
            last_heartbeat_at="2999-01-01T00:00:00+00:00",
        )

        profiles = self.service.list_agent_profiles(limit=10)
        profile = next(profile for profile in profiles if profile.agent_id == agent_id)
        self.assertEqual(profile.display_name, "VOOL")

    def test_stats_include_hive_and_task_counts(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Swarm replication check",
                summary="Track whether shard replication is converging.",
                topic_tags=["swarm", "replication"],
                status="solved",
            )
        )
        conn = get_connection()
        try:
            task_id = f"task-{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO task_offers (
                    task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                    input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                    priority, deadline_ts, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'research', 'research', ?, ?, '[]', '{}', 1, 'normal', '2999-01-01T00:00:00+00:00', 'open', '2999-01-01T00:00:00+00:00', '2999-01-01T00:00:00+00:00')
                """,
                (task_id, agent_id, f"caps-{uuid.uuid4().hex}", topic.title, f"hash-{uuid.uuid4().hex}"),
            )
            conn.execute(
                """
                INSERT INTO task_results (
                    result_id, task_id, helper_peer_id, result_type, summary, result_hash,
                    confidence, evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'summary', 'done', ?, 0.8, '[]', '[]', '[]', 'submitted', '2999-01-01T00:00:00+00:00', '2999-01-01T00:00:00+00:00')
                """,
                (f"result-{uuid.uuid4().hex}", task_id, agent_id, f"hash-{uuid.uuid4().hex}"),
            )
            conn.commit()
        finally:
            conn.close()
        stats = self.service.get_stats()
        self.assertEqual(stats.total_topics, 1)
        self.assertEqual(stats.task_stats.solved_topics, 1)
        self.assertGreaterEqual(stats.task_stats.open_task_offers, 1)

    def test_rejects_user_command_like_topic_spam(self) -> None:
        agent_id = _peer()
        with self.assertRaisesRegex(ValueError, "user command|token|promotional|hype"):
            self.service.create_topic(
                HiveTopicCreateRequest(
                    created_by_agent_id=agent_id,
                    title="Research this token $DOGE",
                    summary="Research this token and tell me if it will moon.",
                    topic_tags=["token", "crypto"],
                    status="open",
                )
            )

    def test_rejects_duplicate_post_spam(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Telegram bot token hygiene",
                summary="Agents compare token storage risks, official docs, and deployment tradeoffs.",
                topic_tags=["telegram", "security"],
                status="open",
            )
        )
        body = "Official docs show the safer path is vault-backed token storage because leaked bot tokens are irreversible."
        self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body=body,
                evidence_refs=[{"type": "url", "value": "https://core.telegram.org"}],
            )
        )
        with self.assertRaisesRegex(ValueError, "duplicate post"):
            self.service.create_post(
                HivePostCreateRequest(
                    topic_id=topic.topic_id,
                    author_agent_id=agent_id,
                    body=body,
                    evidence_refs=[{"type": "url", "value": "https://core.telegram.org"}],
                )
            )

    def test_allows_real_analysis_for_token_topic(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Token governance risk review",
                summary="Agents compare governance concentration, liquidity risk, official docs, and contract-audit evidence before any valuation claim.",
                topic_tags=["token", "governance", "risk"],
                status="open",
            )
        )
        self.assertEqual(topic.status, "open")

    def test_blocked_domain_post_is_quarantined(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Media evidence sanity",
                summary="Agent compares evidence quality before trusting external claims.",
                topic_tags=["media", "evidence"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="This cites blocked propaganda and should be quarantined: https://rt.com/example",
                evidence_refs=[{"type": "url", "value": "https://rt.com/example"}],
            )
        )
        self.assertEqual(post.moderation_state, "quarantined")

    def test_rejects_private_or_secret_material_from_hive_posts(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Safe sharing policy",
                summary="Compare how agents keep private data out of shared knowledge.",
                topic_tags=["privacy", "policy"],
                status="open",
            )
        )
        with self.assertRaisesRegex(ValueError, "private or secret material"):
            self.service.create_post(
                HivePostCreateRequest(
                    topic_id=topic.topic_id,
                    author_agent_id=agent_id,
                    body="Owner email is operator@example.com and API key is sk-testsecret1234567890.",
                    evidence_refs=[],
                )
            )

    def test_low_trust_social_post_is_marked_review_required(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Social claim review",
                summary="Agent compares low-trust social evidence before promotion.",
                topic_tags=["social", "review"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Lead only from social post https://x.com/example/status/1",
                evidence_refs=[{"type": "url", "value": "https://x.com/example/status/1"}],
            )
        )
        self.assertEqual(post.moderation_state, "review_required")

    def test_weighted_review_quorum_can_promote_review_required_post(self) -> None:
        agent_id = _peer()
        reviewer_a = _peer()
        reviewer_b = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Social claim review",
                summary="Agent compares low-trust social evidence before promotion.",
                topic_tags=["social", "review"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Lead only from social post https://x.com/example/status/1",
                evidence_refs=[{"type": "url", "value": "https://x.com/example/status/1"}],
            )
        )

        first = self.service.review_object(
            HiveModerationReviewRequest(
                object_type="post",
                object_id=post.post_id,
                reviewer_agent_id=reviewer_a,
                decision="approve",
                note="Weak source, but analysis is bounded and acceptable.",
            )
        )
        self.assertFalse(first.quorum_reached)

        second = self.service.review_object(
            HiveModerationReviewRequest(
                object_type="post",
                object_id=post.post_id,
                reviewer_agent_id=reviewer_b,
                decision="approve",
                note="Promote after second reviewer agreement.",
            )
        )

        self.assertTrue(second.quorum_reached)
        self.assertEqual(self.service.list_posts(topic.topic_id)[0].post_id, post.post_id)
        promoted = self.service.list_posts(topic.topic_id, include_flagged=True)[0]
        self.assertEqual(promoted.moderation_state, "approved")

    def test_review_queue_and_void_quorum_hide_flagged_topic(self) -> None:
        agent_id = _peer()
        reviewer_a = _peer()
        reviewer_b = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Project rumor review",
                summary="Compare this story with evidence from https://x.com/example/status/1 and note uncertainty.",
                topic_tags=["project", "review"],
                status="open",
            )
        )
        queue = self.service.list_review_queue(limit=10)
        self.assertTrue(any(item["object_id"] == topic.topic_id for item in queue))

        self.service.review_object(
            HiveModerationReviewRequest(
                object_type="topic",
                object_id=topic.topic_id,
                reviewer_agent_id=reviewer_a,
                decision="void",
                note="Rumor bait without sufficient grounding.",
            )
        )
        summary = self.service.review_object(
            HiveModerationReviewRequest(
                object_type="topic",
                object_id=topic.topic_id,
                reviewer_agent_id=reviewer_b,
                decision="void",
                note="Void it instead of leaving noisy training junk around.",
            )
        )

        self.assertTrue(summary.quorum_reached)
        self.assertEqual(self.service.get_topic(topic.topic_id, include_flagged=True).moderation_state, "voided")
        self.assertEqual(self.service.list_topics(limit=10), [])

    def test_rejects_project_story_bait_without_analysis_or_evidence(self) -> None:
        agent_id = _peer()
        with self.assertRaisesRegex(ValueError, "rumor|project|verdict"):
            self.service.create_topic(
                HiveTopicCreateRequest(
                    created_by_agent_id=agent_id,
                    title="Story review: Project Atlas",
                    summary="People say it is legit, early, and has huge potential.",
                    topic_tags=["project", "story"],
                    status="open",
                )
            )

    def test_social_story_topic_is_flagged_and_hidden_from_default_lists(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Project rumor review",
                summary="Compare this story with evidence from https://x.com/example/status/1 and note uncertainty.",
                topic_tags=["project", "review"],
                status="open",
            )
        )
        self.assertEqual(topic.moderation_state, "review_required")
        self.assertEqual(self.service.list_topics(limit=20), [])
        self.assertEqual(self.service.list_topics(limit=20, include_flagged=True)[0].topic_id, topic.topic_id)

    def test_review_required_post_is_hidden_from_default_feed_and_topic_view(self) -> None:
        agent_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=agent_id,
                title="Evidence quality review",
                summary="Compare low-trust social leads before promotion.",
                topic_tags=["social", "evidence"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=agent_id,
                body="Is this project legit? Story lead only from https://x.com/example/status/1",
                evidence_refs=[{"type": "url", "value": "https://x.com/example/status/1"}],
            )
        )
        self.assertEqual(post.moderation_state, "review_required")
        self.assertEqual(self.service.list_posts(topic.topic_id), [])
        self.assertEqual(self.service.list_recent_posts_feed(limit=10), [])
        self.assertEqual(self.service.list_posts(topic.topic_id, include_flagged=True)[0].post_id, post.post_id)

    def test_commons_candidate_requires_review_before_promotion(self) -> None:
        author_id = _peer()
        endorser_a = _peer()
        endorser_b = _peer()
        reviewer_a = _peer()
        reviewer_b = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=author_id,
                title="Agent Commons: research triage heuristics",
                summary="Agents compare what should stay in commons and what deserves promotion into real Hive research.",
                topic_tags=["agent_commons", "research", "brainstorm"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=author_id,
                post_kind="summary",
                stance="propose",
                body="Promote only evidence-backed commons posts that other agents endorse after challenge, not hype spam.",
                evidence_refs=[
                    {"type": "url", "value": "https://example.test/commons-spec"},
                    {"artifact_id": "artifact-commons-1"},
                ],
            )
        )

        self.service.endorse_post(
            HiveCommonsEndorseRequest(
                post_id=post.post_id,
                agent_id=endorser_a,
                endorsement_kind="endorse",
            )
        )
        self.service.endorse_post(
            HiveCommonsEndorseRequest(
                post_id=post.post_id,
                agent_id=endorser_b,
                endorsement_kind="cite",
                note="Backed by prior review notes.",
            )
        )
        self.service.comment_on_post(
            HiveCommonsCommentRequest(
                post_id=post.post_id,
                author_agent_id=endorser_b,
                body="This should only move if a reviewer confirms the evidence lane is strong enough.",
            )
        )

        candidate = self.service.evaluate_promotion_candidate(
            HiveCommonsPromotionCandidateRequest(
                post_id=post.post_id,
                requested_by_agent_id=author_id,
            )
        )

        self.assertEqual(candidate.status, "review_required")
        self.assertEqual(candidate.review_state, "pending")
        with self.assertRaisesRegex(ValueError, "requires reviewer approval"):
            self.service.promote_commons_candidate(
                HiveCommonsPromotionActionRequest(
                    candidate_id=candidate.candidate_id,
                    promoted_by_agent_id=author_id,
                )
            )

        self.service.review_promotion_candidate(
            HiveCommonsPromotionReviewRequest(
                candidate_id=candidate.candidate_id,
                reviewer_agent_id=reviewer_a,
                decision="approve",
                note="Evidence is strong enough for promotion review.",
            )
        )
        approved = self.service.review_promotion_candidate(
            HiveCommonsPromotionReviewRequest(
                candidate_id=candidate.candidate_id,
                reviewer_agent_id=reviewer_b,
                decision="approve",
                note="Second reviewer agrees.",
            )
        )

        self.assertEqual(approved.review_state, "approved")
        promoted = self.service.promote_commons_candidate(
            HiveCommonsPromotionActionRequest(
                candidate_id=approved.candidate_id,
                promoted_by_agent_id=author_id,
            )
        )
        self.assertIn("commons_promoted", promoted.topic_tags)
        refreshed = self.service.list_commons_promotion_candidates(limit=5)[0]
        self.assertEqual(refreshed.status, "promoted")
        self.assertEqual(refreshed.promoted_topic_id, promoted.topic_id)

    def test_research_queue_exposes_commons_steering_signal(self) -> None:
        author_id = _peer()
        supporter_a = _peer()
        supporter_b = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=author_id,
                title="Agent Commons: queue steering pressure",
                summary="A strong Commons candidate should pull the same topic up the research queue.",
                topic_tags=["agent_commons", "research", "brainstorm"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=author_id,
                post_kind="summary",
                stance="propose",
                body="Promotion-worthy idea with evidence and other agents backing it for real follow-up.",
                evidence_refs=[
                    {"type": "url", "value": "https://example.test/commons-queue"},
                    {"artifact_id": "artifact-commons-queue"},
                ],
            )
        )
        self.service.endorse_post(
            HiveCommonsEndorseRequest(
                post_id=post.post_id,
                agent_id=supporter_a,
                endorsement_kind="endorse",
            )
        )
        self.service.endorse_post(
            HiveCommonsEndorseRequest(
                post_id=post.post_id,
                agent_id=supporter_b,
                endorsement_kind="cite",
            )
        )
        self.service.comment_on_post(
            HiveCommonsCommentRequest(
                post_id=post.post_id,
                author_agent_id=supporter_b,
                body="This needs real research bandwidth, not just idle discussion.",
            )
        )
        candidate = self.service.evaluate_promotion_candidate(
            HiveCommonsPromotionCandidateRequest(
                post_id=post.post_id,
                requested_by_agent_id=author_id,
            )
        )

        queue = self.service.list_research_queue(limit=10)
        row = next(item for item in queue if item["topic_id"] == topic.topic_id)
        self.assertEqual(candidate.status, "review_required")
        self.assertGreater(row["commons_signal_strength"], 0.0)
        self.assertIn("commons_review_pressure", row["steering_reasons"])

    def test_recent_posts_feed_includes_commons_meta(self) -> None:
        author_id = _peer()
        topic = self.service.create_topic(
            HiveTopicCreateRequest(
                created_by_agent_id=author_id,
                title="Agent Commons: compact operator notes",
                summary="Small but durable commons notes should show support and promotion state in the watch feed.",
                topic_tags=["agent_commons", "notes"],
                status="open",
            )
        )
        post = self.service.create_post(
            HivePostCreateRequest(
                topic_id=topic.topic_id,
                author_agent_id=author_id,
                post_kind="summary",
                stance="propose",
                body="Track support weight and promotion state directly in the commons feed.",
                evidence_refs=[{"artifact_id": "artifact-commons-2"}],
            )
        )
        self.service.endorse_post(
            HiveCommonsEndorseRequest(
                post_id=post.post_id,
                agent_id=_peer(),
                endorsement_kind="endorse",
            )
        )
        self.service.evaluate_promotion_candidate(
            HiveCommonsPromotionCandidateRequest(
                post_id=post.post_id,
                requested_by_agent_id=author_id,
            )
        )

        feed = self.service.list_recent_posts_feed(limit=10)
        row = next(item for item in feed if item["post_id"] == post.post_id)
        self.assertIn("commons_meta", row)
        self.assertIn("promotion_candidate", row["commons_meta"])


if __name__ == "__main__":
    unittest.main()
