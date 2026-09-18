from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.db import get_connection
from storage.migrations import run_migrations
from storage.useful_output_store import list_useful_outputs, summarize_useful_outputs, sync_useful_outputs


class UsefulOutputStoreTests(unittest.TestCase):
    def test_sync_useful_outputs_captures_structured_durable_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO task_offers (
                        task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                        input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                        priority, deadline_ts, status, created_at, updated_at
                    ) VALUES (
                        'task-1', 'peer-parent', 'capsule-1', 'research', 'analysis',
                        'Summarize the strongest Hive evidence for training',
                        'hash', '[]', '{}', 1, 'high', '2026-03-10T12:00:00+00:00', 'complete',
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:10:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO task_results (
                        result_id, task_id, helper_peer_id, result_type, summary, result_hash, confidence,
                        evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
                    ) VALUES (
                        'result-1', 'task-1', 'peer-helper', 'summary',
                        'Accepted structured result with artifact evidence.', 'hash-r1', 0.92,
                        '[{"artifact_id":"artifact-1"}]', '[]', '[]', 'accepted',
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
                        0.94, 0.91, 0, '2026-03-10T10:07:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO contribution_ledger (
                        entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type, outcome,
                        helpfulness_score, points_awarded, wnull_pending, wnull_released,
                        compute_credits_pending, compute_credits_released, finality_state, finality_depth,
                        finality_target, confirmed_at, finalized_at, parent_host_group_hint_hash,
                        helper_host_group_hint_hash, slashed_flag, fraud_window_end_ts, created_at, updated_at
                    ) VALUES (
                        'ledger-1', 'task-1', 'peer-helper', 'peer-parent', 'assist', 'released',
                        0.94, 12, 0, 0, 0.0, 0.92, 'finalized', 2,
                        2, '2026-03-10T10:08:00+00:00', '2026-03-10T10:09:00+00:00', '',
                        '', 0, '2026-03-10T10:08:00+00:00', '2026-03-10T10:05:00+00:00', '2026-03-10T10:09:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO local_tasks (
                        task_id, session_id, task_class, task_summary, redacted_input_hash,
                        environment_os, environment_shell, environment_runtime, environment_version_hint,
                        plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
                    ) VALUES (
                        'task-1', 'openclaw:test', 'research', 'Explain adaptation blockers', 'hash-local',
                        'macos', 'zsh', 'python', '3.9', 'execute', 'hive_mind', 0.88, 'success', 0,
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:10:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO finalized_responses (
                        parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score, finalization_id, created_at
                    ) VALUES (
                        'task-1', 'raw', 'Finalized response with the real adaptation blocker and next steps.',
                        'success', 0.91, 'fc-test-governed-1', '2026-03-10T10:08:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO hive_topics (
                        topic_id, created_by_agent_id, title, summary, topic_tags_json,
                        status, visibility, evidence_mode, linked_task_id, created_at, updated_at
                    ) VALUES (
                        'topic-1', 'agent:vool', 'Agent Commons: better training signals',
                        'Compare accepted outputs before promotion.', '["agent_commons","design"]',
                        'researching', 'agent_public', 'candidate_only', 'task-1',
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:09:00+00:00'
                    )
                    """
                )
                conn.execute(
                    "ALTER TABLE hive_posts ADD COLUMN moderation_state TEXT"
                )
                conn.execute(
                    """
                    INSERT INTO hive_posts (
                        post_id, topic_id, author_agent_id, post_kind, stance, body,
                        evidence_refs_json, moderation_state, created_at
                    ) VALUES (
                        'post-1', 'topic-1', 'agent:vool', 'result', 'propose',
                        'Approved Hive result with artifact-backed evidence.',
                        '[{"artifact_id":"artifact-2"}]', 'approved',
                        '2026-03-10T10:09:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO hive_commons_promotion_candidates (
                        candidate_id, post_id, topic_id, requested_by_agent_id, score, status, review_state,
                        archive_state, requires_review, promoted_topic_id, support_weight, challenge_weight,
                        cite_weight, comment_count, evidence_depth, downstream_use_count, training_signal_count,
                        reasons_json, metadata_json, created_at, updated_at
                    ) VALUES (
                        'candidate-1', 'post-1', 'topic-1', 'agent:vool', 4.2, 'approved', 'approved',
                        'approved', 1, NULL, 2.0, 0.0, 1.0, 2, 1.1, 1, 1,
                        '["trust_weighted_endorsements","promotion_review_approved"]', '{}',
                        '2026-03-10T10:09:30+00:00', '2026-03-10T10:09:30+00:00'
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            summary = sync_useful_outputs(str(db_path))
            self.assertEqual(summary["training_eligible_count"], 3)
            self.assertEqual(summary["proof_backed_count"], 1)
            self.assertEqual(summary["finalized_task_result_count"], 1)
            self.assertEqual(summary["commons_reviewed_count"], 1)
            self.assertEqual(summary["source_counts"]["task_result"], 1)
            self.assertEqual(summary["source_counts"]["final_response"], 1)
            self.assertEqual(summary["source_counts"]["hive_post"], 1)

            rows = list_useful_outputs(db_path=str(db_path), eligibility_state="eligible", limit=10)
            self.assertEqual(len(rows), 3)
            self.assertTrue(any(row["source_type"] == "task_result" for row in rows))
            self.assertTrue(any("artifact_backed" in row["durability_reasons"] for row in rows))
            task_row = next(row for row in rows if row["source_type"] == "task_result")
            self.assertIn("proof_finalized", task_row["durability_reasons"])
            self.assertTrue(task_row["metadata"]["proof_backed"])
            self.assertEqual(task_row["metadata"]["finality_state"], "finalized")

    def test_pending_task_result_stays_out_of_training_until_proof_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO task_offers (
                        task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                        input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                        priority, deadline_ts, status, created_at, updated_at
                    ) VALUES (
                        'task-proof', 'peer-parent', 'capsule-proof', 'research', 'analysis',
                        'Only confirmed contributions should feed adaptation',
                        'hash-proof', '[]', '{}', 1, 'high', '2026-03-10T12:00:00+00:00', 'complete',
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:10:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO task_results (
                        result_id, task_id, helper_peer_id, result_type, summary, result_hash, confidence,
                        evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
                    ) VALUES (
                        'result-proof', 'task-proof', 'peer-helper', 'summary',
                        'Structured worker result waiting for the fraud window and finality proof.',
                        'hash-proof-r1', 0.9, '[{"artifact_id":"artifact-proof"}]', '[]', '[]', 'accepted',
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
                        'review-proof', 'task-proof', 'peer-helper', 'peer-parent', 'accepted',
                        0.92, 0.9, 0, '2026-03-10T10:07:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO contribution_ledger (
                        entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type, outcome,
                        helpfulness_score, points_awarded, wnull_pending, wnull_released,
                        compute_credits_pending, compute_credits_released, finality_state, finality_depth,
                        finality_target, confirmed_at, finalized_at, parent_host_group_hint_hash,
                        helper_host_group_hint_hash, slashed_flag, fraud_window_end_ts, created_at, updated_at
                    ) VALUES (
                        'ledger-proof', 'task-proof', 'peer-helper', 'peer-parent', 'assist', 'pending',
                        0.92, 11, 0, 0, 0.9, 0.0, 'pending', 0,
                        2, NULL, NULL, '', '', 0, '2026-03-10T12:00:00+00:00',
                        '2026-03-10T10:05:00+00:00', '2026-03-10T10:06:00+00:00'
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            pending_summary = sync_useful_outputs(str(db_path))
            pending_rows = list_useful_outputs(db_path=str(db_path), source_types=["task_result"], limit=10)
            self.assertEqual(pending_summary["training_eligible_count"], 0)
            self.assertEqual(pending_summary["proof_backed_count"], 0)
            self.assertEqual(pending_rows[0]["eligibility_state"], "ineligible")
            self.assertIn("proof_pending", pending_rows[0]["eligibility_reasons"])

            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    UPDATE contribution_ledger
                    SET outcome = 'released',
                        finality_state = 'confirmed',
                        finality_depth = 1,
                        compute_credits_pending = 0.0,
                        compute_credits_released = 0.9,
                        confirmed_at = '2026-03-10T10:08:00+00:00',
                        updated_at = '2026-03-10T10:08:00+00:00'
                    WHERE entry_id = 'ledger-proof'
                    """
                )
                conn.commit()
            finally:
                conn.close()

            confirmed_summary = sync_useful_outputs(str(db_path))
            confirmed_rows = list_useful_outputs(
                db_path=str(db_path),
                source_types=["task_result"],
                eligibility_state="eligible",
                limit=10,
            )
            self.assertEqual(confirmed_summary["training_eligible_count"], 1)
            self.assertEqual(confirmed_summary["proof_backed_count"], 1)
            self.assertEqual(confirmed_rows[0]["metadata"]["finality_state"], "confirmed")
            self.assertTrue(confirmed_rows[0]["metadata"]["proof_backed"])

    def test_summary_counts_ineligible_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO local_tasks (
                        task_id, session_id, task_class, task_summary, redacted_input_hash,
                        environment_os, environment_shell, environment_runtime, environment_version_hint,
                        plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
                    ) VALUES (
                        'task-noise', 'openclaw:test', 'general', 'Generic fallback task', 'hash-local',
                        'macos', 'zsh', 'python', '3.9', 'execute', 'local_only', 0.2, 'unknown', 0,
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:01:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO finalized_responses (
                        parent_task_id, raw_synthesized_text, rendered_persona_text, status_marker, confidence_score, created_at
                    ) VALUES (
                        'task-noise', 'raw', 'I''m here and ready to help. What would you like to work on?',
                        'unknown', 0.1, '2026-03-10T10:02:00+00:00'
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            sync_useful_outputs(str(db_path))
            summary = summarize_useful_outputs(str(db_path))
            self.assertEqual(summary["training_eligible_count"], 0)
            self.assertGreater(summary["ineligible_reasons"].get("low_signal_output", 0), 0)

    def test_commons_posts_require_review_before_becoming_training_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO hive_topics (
                        topic_id, created_by_agent_id, title, summary, topic_tags_json,
                        status, visibility, evidence_mode, linked_task_id, created_at, updated_at
                    ) VALUES (
                        'topic-commons', 'agent:vool', 'Agent Commons: durable research ideas',
                        'Commons posts should stay out of adaptation until reviewed.', '["agent_commons","research"]',
                        'researching', 'agent_public', 'candidate_only', NULL,
                        '2026-03-10T10:00:00+00:00', '2026-03-10T10:00:00+00:00'
                    )
                    """
                )
                # A8 law 11: exercise the commons review gate against REAL
                # moderation evidence (a NULL moderation state stays honestly
                # LEGACY_UNVERIFIED and can never become training-eligible).
                conn.execute(
                    "ALTER TABLE hive_posts ADD COLUMN moderation_state TEXT"
                )
                conn.execute(
                    """
                    INSERT INTO hive_posts (
                        post_id, topic_id, author_agent_id, post_kind, stance, body,
                        evidence_refs_json, created_at
                    ) VALUES (
                        'post-commons', 'topic-commons', 'agent:vool', 'summary', 'propose',
                        'Promote only evidence-backed agent commons posts after review.',
                        '[{"artifact_id":"artifact-commons-reviewed"}]',
                        '2026-03-10T10:05:00+00:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO hive_commons_promotion_candidates (
                        candidate_id, post_id, topic_id, requested_by_agent_id, score, status, review_state,
                        archive_state, requires_review, promoted_topic_id, support_weight, challenge_weight,
                        cite_weight, comment_count, evidence_depth, downstream_use_count, training_signal_count,
                        reasons_json, metadata_json, created_at, updated_at
                    ) VALUES (
                        'candidate-commons', 'post-commons', 'topic-commons', 'agent:vool', 3.1, 'draft', 'pending',
                        'transient', 1, NULL, 1.0, 0.0, 0.5, 1, 1.1, 0, 0,
                        '["evidence_depth"]', '{}', '2026-03-10T10:05:30+00:00', '2026-03-10T10:05:30+00:00'
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            pending_summary = sync_useful_outputs(str(db_path))
            pending_rows = list_useful_outputs(db_path=str(db_path), source_types=["hive_post"], limit=10)
            self.assertEqual(pending_summary["training_eligible_count"], 0)
            self.assertEqual(pending_rows[0]["eligibility_state"], "ineligible")
            self.assertIn("commons_review_pending", pending_rows[0]["eligibility_reasons"])

            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    UPDATE hive_commons_promotion_candidates
                    SET status = 'approved',
                        review_state = 'approved',
                        archive_state = 'approved',
                        updated_at = '2026-03-10T10:06:00+00:00'
                    WHERE candidate_id = 'candidate-commons'
                    """
                )
                # A8 law 11: promotion approval also records the moderation
                # verdict itself (a NULL moderation state stays honestly
                # LEGACY_UNVERIFIED and can never become training-eligible).
                conn.execute(
                    """
                    UPDATE hive_posts SET moderation_state = 'approved'
                    WHERE post_id = 'post-commons'
                    """
                )
                conn.commit()
            finally:
                conn.close()

            approved_summary = sync_useful_outputs(str(db_path))
            approved_rows = list_useful_outputs(
                db_path=str(db_path),
                source_types=["hive_post"],
                eligibility_state="eligible",
                limit=10,
            )
            self.assertEqual(approved_summary["training_eligible_count"], 1)
            self.assertEqual(approved_rows[0]["archive_state"], "approved")
            self.assertIn("promotion_review_approved", approved_rows[0]["durability_reasons"])

            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    UPDATE hive_commons_promotion_candidates
                    SET status = 'rejected',
                        review_state = 'rejected',
                        archive_state = 'transient',
                        updated_at = '2026-03-10T10:07:00+00:00'
                    WHERE candidate_id = 'candidate-commons'
                    """
                )
                conn.commit()
            finally:
                conn.close()

            rejected_summary = sync_useful_outputs(str(db_path))
            rejected_rows = list_useful_outputs(db_path=str(db_path), source_types=["hive_post"], limit=10)
            self.assertEqual(rejected_summary["training_eligible_count"], 0)
            self.assertEqual(rejected_rows[0]["eligibility_state"], "ineligible")
            self.assertIn("commons_review_rejected", rejected_rows[0]["eligibility_reasons"])


if __name__ == "__main__":
    unittest.main()
