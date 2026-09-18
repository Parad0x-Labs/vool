from __future__ import annotations

import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from core.brain_hive_artifacts import store_artifact_manifest
from core.brain_hive_research import build_research_queue_entry, build_topic_research_packet, derive_research_questions
from storage.db import get_connection
from storage.migrations import run_migrations


class BrainHiveResearchPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM artifact_manifests")
            conn.commit()
        finally:
            conn.close()

    def test_build_topic_research_packet_extracts_trading_features_and_questions(self) -> None:
        packet = build_topic_research_packet(
            topic={
                "topic_id": "topic-1",
                "title": "VOOL Trading Learning Desk",
                "summary": "Manual trader learning desk.",
                "status": "researching",
                "visibility": "read_public",
                "evidence_mode": "mixed",
                "topic_tags": ["trading_learning", "manual_trader"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[{"claim_id": "claim-1", "agent_id": "peer-1", "status": "active", "capability_tags": ["research"]}],
            posts=[
                {
                    "post_id": "post-1",
                    "post_kind": "analysis",
                    "stance": "support",
                    "body": "Flow and missed mooners posted.",
                    "created_at": "2026-03-09T00:01:00+00:00",
                    "evidence_refs": [
                        {"kind": "trading_hidden_edges", "items": [{"metric": "max_price_change", "score": 0.81, "support": 277}]},
                        {"kind": "trading_missed_mooners", "items": [{"id": "miss-1", "token_mint": "MintA", "ts": 1773000000.0}]},
                        {"kind": "trading_live_flow", "items": [{"detail": "LOW_LIQ", "kind": "PASS", "ts": 1773000100.0}]},
                        {"kind": "task_event", "event_type": "progress_update", "progress_state": "working"},
                    ],
                }
            ],
        )

        self.assertEqual(packet["topic"]["topic_id"], "topic-1")
        self.assertEqual(packet["execution_state"]["active_claim_count"], 1)
        self.assertTrue(packet["trading_feature_export"]["hidden_edges"])
        self.assertTrue(packet["trading_feature_export"]["flow_reason_counts"])
        self.assertTrue(any("Trading Learning Desk" in item for item in packet["derived_research_questions"]))
        self.assertLessEqual(len(packet["derived_research_questions"]), 6)

    def test_build_research_queue_entry_computes_priority(self) -> None:
        row = build_research_queue_entry(
            topic={
                "topic_id": "topic-2",
                "title": "Agent commons: watcher UX",
                "summary": "Improve watcher UX and task flow.",
                "status": "open",
                "topic_tags": ["agent_commons", "design"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
        )

        self.assertEqual(row["topic_id"], "topic-2")
        self.assertGreater(row["research_priority"], 0.4)
        self.assertTrue(row["suggested_questions"])

    def test_build_research_queue_entry_uses_commons_signal_to_raise_priority(self) -> None:
        base = build_research_queue_entry(
            topic={
                "topic_id": "topic-3",
                "title": "Commons to research bridge",
                "summary": "Queue should react when Commons pressure builds around a topic.",
                "status": "open",
                "topic_tags": ["agent_commons", "research"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
        )
        boosted = build_research_queue_entry(
            topic={
                "topic_id": "topic-3",
                "title": "Commons to research bridge",
                "summary": "Queue should react when Commons pressure builds around a topic.",
                "status": "open",
                "topic_tags": ["agent_commons", "research"],
                "created_at": "2026-03-09T00:00:00+00:00",
                "updated_at": "2026-03-09T00:05:00+00:00",
            },
            claims=[],
            posts=[],
            commons_signal={
                "candidate_count": 2,
                "review_required_count": 1,
                "approved_count": 1,
                "promoted_count": 0,
                "top_score": 4.8,
                "support_weight": 3.4,
                "challenge_weight": 0.2,
                "training_signal_count": 2,
                "downstream_use_count": 1,
                "reasons": ["commons_review_pressure", "commons_training_signal"],
            },
        )

        self.assertGreater(boosted["research_priority"], base["research_priority"])
        self.assertGreater(boosted["commons_signal_strength"], 0.0)
        self.assertIn("commons_review_pressure", boosted["steering_reasons"])

    def test_derive_research_questions_strips_runtime_ids_from_regular_topics(self) -> None:
        questions = derive_research_questions(
            topic_row={
                "title": "Watcher UX audit 407552d4-dd85-4e33-ad9f-edf0282657a5",
                "summary": "Improve human-visible task flow cards and queue drill-downs.",
                "topic_tags": ["design", "ux"],
            },
            claim_rows=[],
            evidence_kind_counts=Counter(),
            trading_feature_export={},
        )

        self.assertTrue(questions)
        self.assertFalse(any("407552d4" in item for item in questions))
        self.assertTrue(any(("watcher" in item.lower()) or ("task flow" in item.lower()) for item in questions))

    def test_derive_research_questions_skips_disposable_smoke_topics(self) -> None:
        questions = derive_research_questions(
            topic_row={
                "title": "[VOOL_SMOKE:B:public-hive-task:20260317T231537Z:21f6faf1] Public Hive lifecycle smoke verification",
                "summary": "Verify create, visibility, pickup, and safe cleanup for a disposable public-safe smoke task.",
                "topic_tags": ["vool", "smoke", "public", "hive"],
            },
            claim_rows=[],
            evidence_kind_counts=Counter(),
            trading_feature_export={},
        )

        self.assertEqual(questions, [])

    def test_derive_research_questions_uses_template_path_by_default_without_live_model_call(self) -> None:
        with patch("core.brain_hive_research._derive_questions_via_model", side_effect=AssertionError("live model path should stay off by default")):
            questions = derive_research_questions(
                topic_row={
                    "title": "Agent commons watcher UX",
                    "summary": "Improve watcher drill-down and task flow UX.",
                    "topic_tags": ["agent_commons", "design"],
                },
                claim_rows=[],
                evidence_kind_counts=Counter(),
                trading_feature_export={},
            )

        self.assertTrue(questions)
        self.assertTrue(any("watcher" in item.lower() or "task flow" in item.lower() for item in questions))

    def test_derive_research_questions_uses_model_path_when_explicitly_enabled(self) -> None:
        with patch.dict("os.environ", {"VOOL_BRAIN_HIVE_LIVE_QUESTION_DERIVATION": "1"}, clear=False), patch(
            "core.brain_hive_research._derive_questions_via_model",
            return_value=["Focused query one", "Focused query two"],
        ) as derive_model:
            questions = derive_research_questions(
                topic_row={
                    "title": "Agent commons watcher UX",
                    "summary": "Improve watcher drill-down and task flow UX.",
                    "topic_tags": ["agent_commons", "design"],
                },
                claim_rows=[],
                evidence_kind_counts=Counter(),
                trading_feature_export={},
            )

        derive_model.assert_called_once()
        self.assertEqual(questions, ["Focused query one", "Focused query two"])

    def test_build_topic_research_packet_surfaces_missing_artifact_refs_instead_of_empty_silence(self) -> None:
        packet = build_topic_research_packet(
            topic={
                "topic_id": "topic-missing-artifact",
                "title": "Agent commons watcher UX",
                "summary": "Audit artifact surfacing.",
                "status": "researching",
                "visibility": "read_public",
                "evidence_mode": "candidate_only",
                "topic_tags": ["agent_commons", "design"],
                "created_at": "2026-03-14T00:00:00+00:00",
                "updated_at": "2026-03-14T00:05:00+00:00",
            },
            claims=[],
            posts=[
                {
                    "post_id": "post-artifact-missing",
                    "post_kind": "summary",
                    "stance": "summarize",
                    "body": "Artifact reference should surface even if this node cannot resolve it.",
                    "created_at": "2026-03-14T00:02:00+00:00",
                    "evidence_refs": [
                        {
                            "kind": "research_bundle_artifact",
                            "artifact_id": "artifact-missing-surface",
                            "file_path": "/tmp/definitely-missing-artifact.zst",
                        }
                    ],
                }
            ],
        )

        self.assertEqual(packet["artifacts"], [])
        self.assertEqual(packet["artifact_resolution_status"], "missing")
        self.assertEqual(packet["research_quality_status"], "artifact_missing")
        self.assertEqual(packet["artifact_refs"][0]["artifact_id"], "artifact-missing-surface")
        self.assertFalse(packet["artifact_refs"][0]["exists_public_index"])
        self.assertEqual(
            packet["artifact_refs"][0]["failure_reason"],
            "artifact_not_indexed_on_this_node",
        )

    def test_build_topic_research_packet_reads_latest_bundle_quality_and_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle: watcher UX",
                summary="Research bundle for watcher UX.",
                payload={
                    "topic_id": "topic-grounded",
                    "title": "Agent commons watcher UX",
                    "query_results": [
                        {
                            "query": "watcher ux evidence",
                            "summary": "Focused watcher UX findings from primary docs.",
                            "snippet_count": 2,
                            "source_domains": ["developer.apple.com", "developer.chrome.com"],
                        },
                        {
                            "query": "task flow heuristics",
                            "summary": "Concrete task-flow findings with implementation guidance.",
                            "snippet_count": 1,
                            "source_domains": ["material.io"],
                        },
                    ],
                    "promotion_decisions": [
                        {
                            "candidate_id": "cand-1",
                            "label": "Watcher drill-down card",
                            "gate": {"can_promote": True},
                        }
                    ],
                    "mined_features": {
                        "feature_rows": [{"metric": "watcher_card_latency"}],
                        "heuristic_candidates": [],
                        "script_ideas": [],
                    },
                },
                topic_id="topic-grounded",
                tags=["agent_commons", "design"],
            )

            packet = build_topic_research_packet(
                topic={
                    "topic_id": "topic-grounded",
                    "title": "Agent commons watcher UX",
                    "summary": "Improve watcher drill-down and task flow UX.",
                    "status": "researching",
                    "visibility": "read_public",
                    "evidence_mode": "candidate_only",
                    "topic_tags": ["agent_commons", "design"],
                    "created_at": "2026-03-14T00:00:00+00:00",
                    "updated_at": "2026-03-14T00:05:00+00:00",
                },
                claims=[],
                posts=[],
            )

        self.assertEqual(packet["nonempty_query_count"], 2)
        self.assertEqual(packet["dead_query_count"], 0)
        self.assertEqual(packet["promoted_finding_count"], 1)
        self.assertEqual(packet["mined_feature_count"], 1)
        self.assertEqual(packet["research_quality_status"], "grounded")
        self.assertEqual(packet["artifact_resolution_status"], "none")
        self.assertGreaterEqual(packet["counts"]["source_domain_count"], 3)
        self.assertTrue(any(item["domain"] == "developer.apple.com" for item in packet["source_domains"]))
        self.assertTrue(any(item["domain"] == "material.io" for item in packet["source_domains"]))

    def test_build_topic_research_packet_surfaces_local_only_artifact_and_latest_synthesis_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_path = Path(tmp_dir) / "bundle.json"
            artifact_path.write_text("{}", encoding="utf-8")
            packet = build_topic_research_packet(
                topic={
                    "topic_id": "topic-local-artifact",
                    "title": "Agent commons watcher UX",
                    "summary": "Show public truth even when only local artifact storage exists.",
                    "status": "researching",
                    "visibility": "read_public",
                    "evidence_mode": "candidate_only",
                    "topic_tags": ["agent_commons", "design"],
                    "created_at": "2026-03-14T00:00:00+00:00",
                    "updated_at": "2026-03-14T00:05:00+00:00",
                },
                claims=[],
                posts=[
                    {
                        "post_id": "post-local-artifact",
                        "post_kind": "summary",
                        "stance": "summarize",
                        "body": "One synthesis card should surface the current truth.",
                        "created_at": "2026-03-14T00:03:00+00:00",
                        "evidence_refs": [
                            {
                                "kind": "research_bundle_artifact",
                                "artifact_id": "artifact-local-only",
                                "file_path": str(artifact_path),
                            },
                            {
                                "kind": "research_synthesis_card",
                                "question": "Agent commons watcher UX",
                                "searched": ["watcher ux task flow implementation docs"],
                                "found": ["Watcher state changes should stay visible in the flow."],
                                "source_domains": ["developer.apple.com"],
                                "artifacts": [{"label": "bundle artifact-local-only", "state": "missing"}],
                                "promoted_findings": ["Watcher state changes should stay visible in the flow."],
                                "confidence": "partial",
                                "blockers": ["Artifact indexed only locally."],
                                "state_token": "state-local-1",
                            },
                        ],
                    }
                ],
            )

        self.assertEqual(packet["artifact_resolution_status"], "missing")
        self.assertTrue(packet["artifact_refs"][0]["exists_local"])
        self.assertFalse(packet["artifact_refs"][0]["exists_public_index"])
        self.assertEqual(packet["artifact_refs"][0]["failure_reason"], "artifact_not_indexed_on_this_node")
        self.assertEqual(packet["latest_synthesis_card"]["question"], "Agent commons watcher UX")
        self.assertEqual(packet["latest_synthesis_card"]["confidence"], "partial")
        self.assertEqual(packet["synthesis_card_count"], 1)

    def test_build_topic_research_packet_marks_off_topic_bundle_as_off_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle: watcher UX",
                summary="Off-topic bundle for watcher UX.",
                payload={
                    "topic_id": "topic-off-topic",
                    "title": "Agent commons watcher UX",
                    "query_results": [
                        {
                            "query": "watcher ux task flow implementation docs",
                            "summary": "Skip navigation documentation for Wear OS components and Android for Cars.",
                            "snippet_count": 1,
                            "source_domains": ["developer.android.com"],
                        },
                        {
                            "query": "watcher ux concrete examples constraints",
                            "summary": "Documentation platforms components get started Wear OS.",
                            "snippet_count": 1,
                            "source_domains": ["developer.android.com"],
                        },
                    ],
                    "promotion_decisions": [],
                    "mined_features": {"feature_rows": [], "heuristic_candidates": [], "script_ideas": []},
                },
                topic_id="topic-off-topic",
                tags=["agent_commons", "design"],
            )
            packet = build_topic_research_packet(
                topic={
                    "topic_id": "topic-off-topic",
                    "title": "Agent commons watcher UX",
                    "summary": "Improve watcher drill-down and task flow UX.",
                    "status": "researching",
                    "visibility": "read_public",
                    "evidence_mode": "candidate_only",
                    "topic_tags": ["agent_commons", "design"],
                    "created_at": "2026-03-14T00:00:00+00:00",
                    "updated_at": "2026-03-14T00:05:00+00:00",
                },
                claims=[],
                posts=[],
            )

        self.assertEqual(packet["research_quality_status"], "off_topic")
        self.assertTrue(any("off-topic" in item.lower() for item in packet["research_quality_reasons"]))

    def test_build_topic_research_packet_marks_grounded_without_promoted_findings_as_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)):
            store_artifact_manifest(
                source_kind="research_bundle",
                title="Autonomous research bundle: watcher UX",
                summary="Research bundle for watcher UX without promoted findings.",
                payload={
                    "topic_id": "topic-partial",
                    "title": "Agent commons watcher UX",
                    "query_results": [
                        {
                            "query": "watcher ux task flow implementation docs",
                            "summary": "Focused watcher UX findings from Apple docs.",
                            "snippet_count": 2,
                            "source_domains": ["developer.apple.com"],
                        },
                        {
                            "query": "watcher ux concrete examples constraints",
                            "summary": "Task-flow evidence from Material guidance.",
                            "snippet_count": 2,
                            "source_domains": ["material.io"],
                        },
                    ],
                    "promotion_decisions": [],
                    "mined_features": {"feature_rows": [], "heuristic_candidates": [], "script_ideas": []},
                },
                topic_id="topic-partial",
                tags=["agent_commons", "design"],
            )
            packet = build_topic_research_packet(
                topic={
                    "topic_id": "topic-partial",
                    "title": "Agent commons watcher UX",
                    "summary": "Improve watcher drill-down and task flow UX.",
                    "status": "researching",
                    "visibility": "read_public",
                    "evidence_mode": "candidate_only",
                    "topic_tags": ["agent_commons", "design"],
                    "created_at": "2026-03-14T00:00:00+00:00",
                    "updated_at": "2026-03-14T00:05:00+00:00",
                },
                claims=[],
                posts=[],
            )

        self.assertEqual(packet["research_quality_status"], "partial")
        self.assertTrue(any("No promoted findings" in item for item in packet["research_quality_reasons"]))


if __name__ == "__main__":
    unittest.main()
