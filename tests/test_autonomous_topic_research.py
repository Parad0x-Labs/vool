from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.autonomous_topic_research import pick_autonomous_research_signal, research_topic_from_signal
from storage.db import get_connection
from storage.migrations import run_migrations


class AutonomousTopicResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM artifact_manifests")
            conn.execute("DELETE FROM runtime_session_events")
            conn.execute("DELETE FROM runtime_sessions")
            conn.commit()
        finally:
            conn.close()

    def test_pick_autonomous_research_signal_prefers_unclaimed_high_priority_topic(self) -> None:
        signal = pick_autonomous_research_signal(
            [
                {"topic_id": "topic-1", "research_priority": 0.55, "active_claim_count": 1, "claims": [{"agent_id": "peer-other"}], "artifact_count": 0},
                {"topic_id": "topic-2", "research_priority": 0.82, "active_claim_count": 0, "claims": [], "artifact_count": 0},
            ],
            local_peer_id="peer-local",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["topic_id"], "topic-2")

    def test_pick_autonomous_research_signal_prefers_unclaimed_topic_when_priority_tied(self) -> None:
        signal = pick_autonomous_research_signal(
            [
                {
                    "topic_id": "topic-claimed",
                    "research_priority": 0.82,
                    "active_claim_count": 1,
                    "claims": [{"agent_id": "peer-other", "status": "active"}],
                    "artifact_count": 0,
                },
                {
                    "topic_id": "topic-open",
                    "research_priority": 0.82,
                    "active_claim_count": 0,
                    "claims": [],
                    "artifact_count": 0,
                },
            ],
            local_peer_id="peer-local",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["topic_id"], "topic-open")

    def test_pick_autonomous_research_signal_keeps_foreign_claimed_topic_if_it_is_only_viable_choice(self) -> None:
        signal = pick_autonomous_research_signal(
            [
                {
                    "topic_id": "topic-claimed",
                    "research_priority": 0.61,
                    "active_claim_count": 1,
                    "claims": [{"agent_id": "peer-other", "status": "active"}],
                    "artifact_count": 1,
                }
            ],
            local_peer_id="peer-local",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal["topic_id"], "topic-claimed")

    def test_research_topic_from_signal_claims_packs_and_submits(self) -> None:
        bridge = mock.Mock()
        bridge.enabled.return_value = True
        bridge.get_public_research_packet.return_value = {
            "packet_schema": "brain_hive.research_packet.v1",
            "topic": {
                "topic_id": "topic-123",
                "title": "VOOL Trading Learning Desk",
                "summary": "Manual trader desk",
                "status": "open",
                "topic_tags": ["trading_learning", "manual_trader"],
            },
            "claims": [],
            "counts": {"post_count": 1, "claim_count": 0, "active_claim_count": 0, "evidence_count": 4, "source_domain_count": 2},
            "execution_state": {"execution_state": "open"},
            "derived_research_questions": [
                "Best way to research topic: VOOL Trading Learning Desk",
                "Which exported trading features best explain misses or hidden edges?",
            ],
            "trading_feature_export": {
                "hidden_edges": [{"metric": "max_price_change", "score": 0.82, "support": 277}],
                "flow_reason_counts": [{"reason": "LOW_LIQ", "count": 6}],
                "pattern_health": {"total_patterns": 209},
            },
        }
        bridge.claim_public_topic.return_value = {"ok": True, "claim_id": "claim-1", "topic_id": "topic-123"}
        bridge.post_public_topic_progress.return_value = {"ok": True, "post_id": "post-progress-1"}
        bridge.submit_public_topic_result.return_value = {"ok": True, "post_id": "post-result-1", "topic_id": "topic-123"}

        curiosity = mock.Mock()
        curiosity.run_external_topic.side_effect = [
            {
                "topic_id": "curiosity-1",
                "candidate_id": "candidate-1",
                "cached": False,
                "summary": "Bounded curiosity notes for query one.",
                "snippets": [{"summary": "note"}],
            },
            {
                "topic_id": "curiosity-2",
                "candidate_id": "candidate-2",
                "cached": False,
                "summary": "Bounded curiosity notes for query two.",
                "snippets": [{"summary": "note"}],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)), mock.patch(
            "core.autonomous_topic_research.get_local_peer_id",
            return_value="peer-local-1234567890",
        ), mock.patch(
            "core.autonomous_topic_research.get_candidate_by_id",
            side_effect=lambda candidate_id: {"candidate_id": candidate_id, "normalized_output": f"summary for {candidate_id}"},
        ):
            result = research_topic_from_signal(
                {"topic_id": "topic-123"},
                public_hive_bridge=bridge,
                curiosity=curiosity,
                session_id="auto-research:topic-123",
                auto_claim=True,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.claim_id, "claim-1")
        self.assertEqual(len(result.artifact_ids), 2)
        self.assertEqual(len(result.candidate_ids), 2)
        bridge.claim_public_topic.assert_called_once()
        bridge.post_public_topic_progress.assert_called_once()
        bridge.submit_public_topic_result.assert_called_once()

    def test_research_topic_from_signal_allows_parallel_lane_when_foreign_claim_exists(self) -> None:
        bridge = mock.Mock()
        bridge.enabled.return_value = True
        bridge.get_public_research_packet.return_value = {
            "packet_schema": "brain_hive.research_packet.v1",
            "topic": {
                "topic_id": "topic-parallel",
                "title": "Improving UX Heuristics",
                "summary": "Parallel research should be allowed.",
                "status": "researching",
                "topic_tags": ["ux", "heuristics"],
            },
            "claims": [
                {
                    "claim_id": "foreign-claim-1",
                    "agent_id": "peer-other",
                    "status": "active",
                }
            ],
            "counts": {"post_count": 2, "claim_count": 1, "active_claim_count": 1, "evidence_count": 3, "source_domain_count": 1},
            "execution_state": {"execution_state": "researching"},
            "derived_research_questions": [
                "Which human interaction heuristics compress best for reuse?",
                "How should UX learning artifacts be preserved for rapid retrieval?",
            ],
            "trading_feature_export": {},
        }
        bridge.claim_public_topic.return_value = {"ok": True, "claim_id": "claim-parallel", "topic_id": "topic-parallel"}
        bridge.post_public_topic_progress.return_value = {"ok": True, "post_id": "post-progress-parallel"}
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "post_id": "post-result-parallel",
            "topic_id": "topic-parallel",
        }

        curiosity = mock.Mock()
        curiosity.run_external_topic.side_effect = [
            {
                "topic_id": "curiosity-parallel-1",
                "candidate_id": "candidate-parallel-1",
                "cached": False,
                "summary": "Parallel note one.",
                "snippets": [{"summary": "note"}],
            },
            {
                "topic_id": "curiosity-parallel-2",
                "candidate_id": "candidate-parallel-2",
                "cached": False,
                "summary": "Parallel note two.",
                "snippets": [{"summary": "note"}],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)), mock.patch(
            "core.autonomous_topic_research.get_local_peer_id",
            return_value="peer-local-1234567890",
        ), mock.patch(
            "core.autonomous_topic_research.get_candidate_by_id",
            side_effect=lambda candidate_id: {"candidate_id": candidate_id, "normalized_output": f"summary for {candidate_id}"},
        ):
            result = research_topic_from_signal(
                {"topic_id": "topic-parallel"},
                public_hive_bridge=bridge,
                curiosity=curiosity,
                session_id="auto-research:topic-parallel",
                auto_claim=True,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed_parallel")
        self.assertEqual(result.claim_id, "claim-parallel")
        self.assertTrue(result.details["parallel_lane"])
        self.assertEqual(len(result.details["foreign_active_claims"]), 1)
        bridge.claim_public_topic.assert_called_once()
        claim_kwargs = bridge.claim_public_topic.call_args.kwargs
        self.assertIn("Parallel autonomous research lane joined", claim_kwargs["note"])
        progress_kwargs = bridge.post_public_topic_progress.call_args.kwargs
        self.assertIn("Parallel autonomous research started", progress_kwargs["body"])
        self.assertTrue(progress_kwargs["evidence_refs"][0]["parallel_lane"])

    def test_research_topic_from_signal_downgrades_weak_runs_into_visible_summary_status(self) -> None:
        bridge = mock.Mock()
        bridge.enabled.return_value = True
        bridge.get_public_research_packet.return_value = {
            "packet_schema": "brain_hive.research_packet.v1",
            "topic": {
                "topic_id": "topic-weak",
                "title": "Agent commons watcher UX",
                "summary": "Audit whether weak research gets marked honestly.",
                "status": "researching",
                "topic_tags": ["agent_commons", "design"],
                "evidence_mode": "candidate_only",
            },
            "claims": [],
            "counts": {"post_count": 0, "claim_count": 0, "active_claim_count": 0, "evidence_count": 0, "source_domain_count": 0},
            "execution_state": {"execution_state": "open"},
            "derived_research_questions": [
                "watcher ux docs",
                "task flow evidence gaps",
            ],
            "trading_feature_export": {},
        }
        bridge.claim_public_topic.return_value = {"ok": True, "claim_id": "claim-weak", "topic_id": "topic-weak"}
        bridge.post_public_topic_progress.return_value = {"ok": True, "post_id": "post-progress-weak"}
        bridge.submit_public_topic_result.return_value = {"ok": True, "post_id": "post-result-weak", "topic_id": "topic-weak"}

        curiosity = mock.Mock()
        curiosity.run_external_topic.side_effect = [
            {
                "topic_id": "curiosity-weak-1",
                "candidate_id": "candidate-weak-1",
                "cached": False,
                "summary": "Generic watcher UX note without grounded domains.",
                "snippets": [],
            },
            {
                "topic_id": "curiosity-weak-2",
                "candidate_id": "",
                "cached": False,
                "summary": "",
                "snippets": [],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)), mock.patch(
            "core.autonomous_topic_research.get_local_peer_id",
            return_value="peer-local-1234567890",
        ), mock.patch(
            "core.autonomous_topic_research.get_candidate_by_id",
            side_effect=lambda candidate_id: {"candidate_id": candidate_id, "normalized_output": f"summary for {candidate_id}"},
        ):
            result = research_topic_from_signal(
                {"topic_id": "topic-weak"},
                public_hive_bridge=bridge,
                curiosity=curiosity,
                session_id="auto-research:topic-weak",
                auto_claim=True,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.result_status, "researching")
        self.assertEqual(result.details["quality_summary"]["research_quality_status"], "insufficient_evidence")
        submit_kwargs = bridge.submit_public_topic_result.call_args.kwargs
        self.assertEqual(submit_kwargs["post_kind"], "summary")
        self.assertEqual(submit_kwargs["result_status"], "researching")
        self.assertIn("Research synthesis card", submit_kwargs["body"])
        self.assertIn("Confidence: insufficient_evidence", submit_kwargs["body"])
        self.assertIn("Blockers:", submit_kwargs["body"])

    def test_research_topic_from_signal_promotes_grounded_general_findings_and_posts_single_synthesis_card(self) -> None:
        bridge = mock.Mock()
        bridge.enabled.return_value = True
        bridge.get_public_research_packet.return_value = {
            "packet_schema": "brain_hive.research_packet.v1",
            "topic": {
                "topic_id": "topic-grounded-general",
                "title": "Agent commons watcher UX",
                "summary": "Need grounded watcher and task-flow guidance.",
                "status": "researching",
                "topic_tags": ["agent_commons", "design"],
                "evidence_mode": "read_public",
            },
            "claims": [],
            "counts": {"post_count": 1, "claim_count": 0, "active_claim_count": 0, "evidence_count": 1, "source_domain_count": 0},
            "execution_state": {"execution_state": "open"},
            "derived_research_questions": [
                "watcher ux task flow implementation docs",
                "watcher ux concrete examples constraints",
            ],
            "trading_feature_export": {},
        }
        bridge.claim_public_topic.return_value = {"ok": True, "claim_id": "claim-grounded", "topic_id": "topic-grounded-general"}
        bridge.post_public_topic_progress.return_value = {"ok": True, "post_id": "post-progress-grounded"}
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "post_id": "post-result-grounded",
            "topic_id": "topic-grounded-general",
        }

        curiosity = mock.Mock()
        curiosity.run_external_topic.side_effect = [
            {
                "topic_id": "curiosity-good-1",
                "candidate_id": "candidate-good-1",
                "cached": False,
                "summary": "Watcher drill-down cards should preserve task state and reveal recent transitions clearly.",
                "snippets": [
                    {"summary": "Watcher drill-down cards should preserve task state.", "origin_domain": "developer.apple.com"},
                    {"summary": "Recent transitions should stay visible.", "origin_domain": "developer.chrome.com"},
                ],
            },
            {
                "topic_id": "curiosity-good-2",
                "candidate_id": "candidate-good-2",
                "cached": False,
                "summary": "Task-flow UX should keep active claims visible and expose blockers without hiding the queue context.",
                "snippets": [
                    {"summary": "Active claims stay visible in the queue.", "origin_domain": "material.io"},
                ],
            },
        ]

        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)), mock.patch(
            "core.autonomous_topic_research.get_local_peer_id",
            return_value="peer-local-1234567890",
        ), mock.patch(
            "core.autonomous_topic_research.get_candidate_by_id",
            side_effect=lambda candidate_id: {"candidate_id": candidate_id, "normalized_output": f"summary for {candidate_id}"},
        ):
            result = research_topic_from_signal(
                {"topic_id": "topic-grounded-general"},
                public_hive_bridge=bridge,
                curiosity=curiosity,
                session_id="auto-research:topic-grounded-general",
                auto_claim=True,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.result_status, "solved")
        self.assertEqual(result.details["quality_summary"]["research_quality_status"], "grounded")
        self.assertGreaterEqual(result.details["quality_summary"]["promoted_finding_count"], 1)
        submit_kwargs = bridge.submit_public_topic_result.call_args.kwargs
        self.assertEqual(submit_kwargs["post_kind"], "verdict")
        self.assertEqual(submit_kwargs["result_status"], "solved")
        self.assertIn("Research synthesis card", submit_kwargs["body"])
        self.assertIn("Question: Agent commons watcher UX", submit_kwargs["body"])
        self.assertIn("Promoted findings:", submit_kwargs["body"])
        self.assertNotIn("claim-grounded", submit_kwargs["idempotency_key"])
        synthesis_refs = [item for item in submit_kwargs["evidence_refs"] if item.get("kind") == "research_synthesis_card"]
        self.assertEqual(len(synthesis_refs), 1)
        self.assertEqual(synthesis_refs[0]["confidence"], "grounded")

    def test_research_topic_from_signal_skips_external_research_for_disposable_smoke_topic(self) -> None:
        bridge = mock.Mock()
        bridge.enabled.return_value = True
        bridge.get_public_research_packet.return_value = {
            "packet_schema": "brain_hive.research_packet.v1",
            "topic": {
                "topic_id": "topic-smoke",
                "title": "[VOOL_SMOKE:B:public-hive-task:20260317T231537Z:21f6faf1] Public Hive lifecycle smoke verification",
                "summary": "Verify create, visibility, pickup, and safe cleanup for a disposable public-safe smoke task.",
                "status": "researching",
                "topic_tags": ["vool", "smoke", "public", "hive"],
                "evidence_mode": "candidate_only",
            },
            "claims": [],
            "counts": {"post_count": 0, "claim_count": 0, "active_claim_count": 0, "evidence_count": 0, "source_domain_count": 0},
            "execution_state": {"execution_state": "open"},
            "derived_research_questions": [],
            "trading_feature_export": {},
        }
        bridge.claim_public_topic.return_value = {"ok": True, "claim_id": "claim-smoke", "topic_id": "topic-smoke"}
        bridge.post_public_topic_progress.return_value = {"ok": True, "post_id": "post-progress-smoke"}
        bridge.submit_public_topic_result.return_value = {"ok": True, "post_id": "post-result-smoke", "topic_id": "topic-smoke"}

        curiosity = mock.Mock()

        with tempfile.TemporaryDirectory() as tmp_dir, mock.patch("core.liquefy_bridge._VOOL_VAULT", Path(tmp_dir)), mock.patch(
            "core.autonomous_topic_research.get_local_peer_id",
            return_value="peer-local-1234567890",
        ):
            result = research_topic_from_signal(
                {"topic_id": "topic-smoke"},
                public_hive_bridge=bridge,
                curiosity=curiosity,
                session_id="auto-research:topic-smoke",
                auto_claim=True,
            )

        curiosity.run_external_topic.assert_not_called()
        self.assertTrue(result.ok)
        self.assertEqual(result.result_status, "researching")
        self.assertEqual(result.details["quality_summary"]["research_quality_status"], "insufficient_evidence")
        self.assertIn(
            "Disposable smoke topic detected; external research skipped by policy.",
            result.details["quality_summary"]["research_quality_reasons"],
        )
        submit_kwargs = bridge.submit_public_topic_result.call_args.kwargs
        self.assertIn("Confidence: insufficient_evidence", submit_kwargs["body"])
        self.assertIn("external research skipped by policy", submit_kwargs["body"])


if __name__ == "__main__":
    unittest.main()
