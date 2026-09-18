from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.control_plane_workspace as control_plane_workspace
from core.control_plane import metrics_views as control_plane_metrics_views
from core.control_plane import policies as control_plane_policies
from core.control_plane import queue_views as control_plane_queue_views
from core.control_plane import runtime_views as control_plane_runtime_views
from core.control_plane import schemas as control_plane_schemas
from core.control_plane import templates as control_plane_templates
from core.control_plane_workspace import collect_control_plane_status, sync_control_plane_workspace
from storage.db import get_connection
from storage.migrations import run_migrations


class ControlPlaneWorkspaceTests(unittest.TestCase):
    def test_control_plane_workspace_facade_reuses_extracted_libraries(self) -> None:
        self.assertEqual(control_plane_workspace._schema_library(), control_plane_schemas.schema_library())
        self.assertEqual(control_plane_workspace._control_plane_policy_text(), control_plane_policies.control_plane_policy_text())
        self.assertEqual(
            control_plane_workspace._template_library(),
            control_plane_templates.template_library(spawn_policy_fn=control_plane_workspace._spawn_policy),
        )

    def test_control_plane_workspace_facade_reuses_extracted_read_views(self) -> None:
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = None

        self.assertEqual(
            control_plane_workspace._load_open_task_offers(conn, limit=5),
            control_plane_queue_views.load_open_task_offers(
                conn,
                limit=5,
                table_exists_fn=control_plane_workspace._table_exists,
            ),
        )

    def test_control_plane_workspace_facade_reuses_extracted_metrics_views(self) -> None:
        conn = mock.Mock()
        conn.execute.return_value.fetchone.return_value = None
        conn.execute.return_value.fetchall.return_value = []

        with mock.patch("core.control_plane_workspace._utcnow", return_value="2026-03-21T00:00:00+00:00"), mock.patch(
            "core.control_plane_workspace._utc_day_bucket",
            return_value="2026-03-21",
        ):
            self.assertEqual(
                control_plane_workspace._load_swarm_budget_summary(conn),
                control_plane_metrics_views.load_swarm_budget_summary(
                    conn,
                    table_exists_fn=control_plane_workspace._table_exists,
                    utc_day_bucket_fn=control_plane_workspace._utc_day_bucket,
                    utcnow_fn=control_plane_workspace._utcnow,
                    policy_getter=control_plane_workspace.policy_engine.get,
                ),
            )
        self.assertEqual(
            control_plane_workspace._load_runtime_checkpoints(conn, limit=5),
            control_plane_runtime_views.load_runtime_checkpoints(
                conn,
                limit=5,
                table_exists_fn=control_plane_workspace._table_exists,
            ),
        )

    def test_sync_control_plane_workspace_creates_real_mirror_and_templates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "vool.db"
            workspace_root = tmp / "workspace"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO task_offers (
                        task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                        input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                        priority, deadline_ts, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '{}', 1, 'high', ?, 'open', ?, ?)
                    """,
                    ("task-1", "peer-parent", "capsule-1", "research", "research", "Investigate control mirror", "hash-1", "2026-03-10T12:00:00+00:00", "2026-03-10T10:00:00+00:00", "2026-03-10T10:05:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO task_claims (
                        claim_id, task_id, helper_peer_id, declared_capabilities_json,
                        current_load, host_group_hint_hash, status, claimed_at, updated_at
                    ) VALUES (?, ?, ?, '[]', 0, '', 'accepted', ?, ?)
                    """,
                    ("claim-1", "task-1", "peer-helper", "2026-03-10T10:05:00+00:00", "2026-03-10T10:05:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO task_assignments (
                        assignment_id, task_id, claim_id, parent_peer_id, helper_peer_id, assignment_mode,
                        status, capability_token_id, lease_expires_at, last_progress_state, last_progress_note,
                        assigned_at, updated_at, progress_updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', '', ?, 'researching', 'first pass', ?, ?, ?, NULL)
                    """,
                    ("assign-1", "task-1", "claim-1", "peer-parent", "peer-helper", "bounded", "2026-03-10T13:00:00+00:00", "2026-03-10T10:05:00+00:00", "2026-03-10T10:06:00+00:00", "2026-03-10T10:06:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO task_results (
                        result_id, task_id, helper_peer_id, result_type, summary, result_hash, confidence,
                        evidence_json, abstract_steps_json, risk_flags_json, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '[]', '[]', 'submitted', ?, ?)
                    """,
                    ("result-1", "task-1", "peer-helper", "summary", "Need review", "hash", 0.8, "2026-03-10T10:07:00+00:00", "2026-03-10T10:07:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO runtime_sessions (
                        session_id, started_at, updated_at, event_count, last_event_type,
                        last_message, request_preview, task_class, status, last_checkpoint_id
                    ) VALUES (?, ?, ?, 2, 'task_completed', 'First pass done.', 'pull hive tasks', 'research', 'completed', ?)
                    """,
                    ("session-1", "2026-03-10T10:05:00+00:00", "2026-03-10T10:08:00+00:00", "checkpoint-1"),
                )
                conn.execute(
                    """
                    INSERT INTO runtime_checkpoints (
                        checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                        status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                        failure_text, resume_count, created_at, updated_at, completed_at, resumed_from_checkpoint_id
                    ) VALUES (?, ?, ?, ?, ?, '{}', 'completed', 3, 'hive.claim_task', '{}', '{}', 'done', '', 0, ?, ?, ?, NULL)
                    """,
                    (
                        "checkpoint-1",
                        "session-1",
                        "task-1",
                        "research",
                        "pull hive tasks",
                        "2026-03-10T10:05:00+00:00",
                        "2026-03-10T10:08:00+00:00",
                        "2026-03-10T10:08:00+00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO runtime_session_events (
                        session_id, seq, event_type, message, details_json, created_at
                    ) VALUES (?, 1, 'task_received', 'Received request.', '{}', ?),
                             (?, 2, 'task_completed', 'Completed request.', '{}', ?)
                    """,
                    ("session-1", "2026-03-10T10:05:00+00:00", "session-1", "2026-03-10T10:08:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO runtime_tool_receipts (
                        receipt_key, session_id, checkpoint_id, tool_name, idempotency_key,
                        arguments_json, execution_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "receipt-1",
                        "session-1",
                        "checkpoint-1",
                        "workspace.write_file",
                        "idemp-1",
                        json.dumps({"target_path": "/tmp/output.txt"}, sort_keys=True),
                        json.dumps({"status": "ok"}, sort_keys=True),
                        "2026-03-10T10:06:00+00:00",
                        "2026-03-10T10:06:00+00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO operator_action_requests (
                        action_id, session_id, task_id, action_kind, scope_json,
                        result_json, status, created_at, updated_at, executed_at
                    ) VALUES (?, ?, ?, ?, '{}', '{}', 'pending_approval', ?, ?, NULL)
                    """,
                    ("action-1", "session-1", "task-1", "cleanup_temp_files", "2026-03-10T10:09:00+00:00", "2026-03-10T10:09:00+00:00"),
                )
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
                        "entry-1",
                        "task-1",
                        "peer-helper",
                        "peer-parent",
                        0.92,
                        8,
                        0.88,
                        "2026-03-10T10:07:00+00:00",
                        "2026-03-10T16:07:00+00:00",
                        "2026-03-10T10:06:00+00:00",
                        "2026-03-10T10:05:00+00:00",
                        "2026-03-10T16:07:00+00:00",
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
                        "proof-1",
                        "entry-1",
                        "task-1",
                        "peer-helper",
                        "peer-parent",
                        0.88,
                        8,
                        "2026-03-10T16:07:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            with mock.patch("core.trainable_base_manager.list_staged_trainable_bases", return_value=[]):
                payload = sync_control_plane_workspace(workspace_root=workspace_root, db_path=db_path)

            self.assertTrue(payload["ok"])
            self.assertTrue((workspace_root / "control" / "queue" / "open_task_offers.json").exists())
            self.assertTrue((workspace_root / "control" / "approvals" / "pending_operator_actions.json").exists())
            self.assertTrue((workspace_root / "control" / "runs" / "sessions" / "session-1.json").exists())
            self.assertTrue((workspace_root / "control" / "metrics" / "proof_of_useful_work.json").exists())
            self.assertTrue((workspace_root / "control" / "metrics" / "runtime_truth.json").exists())
            self.assertTrue((workspace_root / "templates" / "reviewer" / "spawn.json").exists())

            overview = json.loads((workspace_root / "control" / "metrics" / "overview.json").read_text(encoding="utf-8"))
            self.assertEqual(overview["open_task_count"], 1)
            self.assertEqual(overview["pending_approval_count"], 1)
            self.assertEqual(overview["approval_backlog_count"], 1)
            self.assertEqual(overview["pending_operator_action_count"], 1)
            self.assertEqual(overview["pending_runtime_checkpoint_count"], 0)
            self.assertEqual(overview["runtime_pending_approval_count"], 0)
            self.assertEqual(overview["proof_of_useful_work"]["finalized_count"], 1)
            self.assertEqual(overview["proof_of_useful_work"]["recent_receipts"][0]["stage"], "finalized")
            self.assertEqual(overview["runtime_truth"]["session_count"], 1)
            self.assertEqual(overview["runtime_truth"]["status_counts"]["completed"], 1)
            self.assertEqual(overview["runtime_truth"]["session_pending_approval_count"], 0)

            session_payload = json.loads((workspace_root / "control" / "runs" / "sessions" / "session-1.json").read_text(encoding="utf-8"))
            self.assertEqual(session_payload["execution_history"]["checkpoint"]["status"], "completed")
            self.assertIn("/tmp/output.txt", session_payload["execution_history"]["touched_paths"])

            reviewer_lane = json.loads((workspace_root / "control" / "queue" / "reviewer_lane.json").read_text(encoding="utf-8"))
            self.assertEqual(len(reviewer_lane["items"]), 1)

    def test_collect_control_plane_status_reports_budget_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO hive_topics (
                        topic_id, created_by_agent_id, title, summary, topic_tags_json,
                        status, visibility, evidence_mode, linked_task_id, created_at, updated_at
                    ) VALUES (
                        'topic-1', 'agent:vool', 'Budget test topic', 'Budget test summary', '[]',
                        'researching', 'agent_public', 'candidate_only', NULL, ?, ?
                    )
                    """,
                    ("2026-03-10T10:00:00+00:00", "2026-03-10T10:00:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO public_hive_write_quota_events (
                        peer_id, day_bucket, route, amount, trust_score, trust_tier, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("peer-local", "2026-03-10", "posts", 12.5, 0.72, "established", "2026-03-10T10:00:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO hive_topic_claims (
                        claim_id, topic_id, agent_id, status, note, capability_tags_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', '', '[]', ?, ?)
                    """,
                    ("claim-1", "topic-1", "agent:vool", "2026-03-10T10:00:00+00:00", "2026-03-10T10:00:00+00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO contribution_ledger (
                        entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type, outcome,
                        helpfulness_score, points_awarded, wnull_pending, wnull_released,
                        compute_credits_pending, compute_credits_released,
                        finality_state, finality_depth, finality_target, confirmed_at, finalized_at,
                        slashed_flag, fraud_window_end_ts, created_at, updated_at
                    ) VALUES
                    (?, ?, ?, ?, 'assist', 'pending', ?, ?, 0, 0, ?, 0, 'pending', 0, 2, NULL, NULL, 0, ?, ?, ?),
                    (?, ?, ?, ?, 'assist', 'released', ?, ?, 0, 0, 0, ?, 'confirmed', 1, 2, ?, NULL, 0, ?, ?, ?)
                    """,
                    (
                        "entry-pending",
                        "task-pending",
                        "peer-a",
                        "peer-parent",
                        0.75,
                        5,
                        0.55,
                        "2026-03-10T18:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "entry-confirmed",
                        "task-confirmed",
                        "peer-b",
                        "peer-parent",
                        0.81,
                        7,
                        0.67,
                        "2026-03-10T11:00:00+00:00",
                        "2026-03-10T18:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T11:00:00+00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO adaptation_corpora (
                        corpus_id, label, source_config_json, filters_json, output_path,
                        example_count, source_stats_json, quality_score, quality_details_json,
                        content_hash, last_scored_at, latest_build_at, created_at, updated_at
                    ) VALUES (?, ?, '{}', '{}', '', 32, '{}', 0.83, '{}', 'hash-corpus-1', ?, ?, ?, ?)
                    """,
                    (
                        "corpus-1",
                        "Proof corpus",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO adaptation_jobs (
                        job_id, corpus_id, label, base_model_ref, base_provider_name, base_model_name,
                        adapter_provider_name, adapter_model_name, output_dir, status, device,
                        dependency_status_json, training_config_json, metrics_json, metadata_json,
                        registered_manifest_json, error_text, started_at, completed_at, promoted_at,
                        rolled_back_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'promoted', '', '{}', '{}', '{}', ?, '{}', '', ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        "adapt-job-1",
                        "corpus-1",
                        "Proof job",
                        "base:model",
                        "base",
                        "model",
                        "vool-adapted",
                        "vool-loop-proof",
                        "/tmp/adapt-job-1",
                        json.dumps({"quality_score": 0.83}),
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T11:00:00+00:00",
                        "2026-03-10T11:05:00+00:00",
                        "2026-03-10T10:00:00+00:00",
                        "2026-03-10T11:05:00+00:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO adaptation_eval_runs (
                        eval_id, job_id, corpus_id, eval_kind, split_name, status, sample_count,
                        baseline_provider_ref, candidate_provider_ref, baseline_score, candidate_score, score_delta,
                        metrics_json, decision, error_text, created_at, completed_at, updated_at
                    ) VALUES
                    (?, ?, ?, 'promotion_gate', 'eval', 'completed', 8, 'base:model', 'vool-loop-proof', 0.52, 0.61, 0.09, '{}', 'promote_candidate', '', ?, ?, ?),
                    (?, ?, ?, 'pre_promotion_canary', 'canary', 'completed', 6, 'base:model', 'vool-loop-proof', 0.53, 0.60, 0.07, '{}', 'canary_pass', '', ?, ?, ?)
                    """,
                    (
                        "eval-1",
                        "adapt-job-1",
                        "corpus-1",
                        "2026-03-10T11:00:00+00:00",
                        "2026-03-10T11:02:00+00:00",
                        "2026-03-10T11:02:00+00:00",
                        "eval-2",
                        "adapt-job-1",
                        "corpus-1",
                        "2026-03-10T11:03:00+00:00",
                        "2026-03-10T11:04:00+00:00",
                        "2026-03-10T11:04:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            payload = collect_control_plane_status(db_path=db_path)
            hive_budget = payload["public_hive_budget_today"]
            self.assertGreater(hive_budget["estimated_daily_quota"], hive_budget["used_total"])
            self.assertEqual(hive_budget["active_claim_count"], 1)
            self.assertIn("useful_outputs", payload)
            self.assertEqual(payload["proof_of_useful_work"]["pending_count"], 1)
            self.assertEqual(payload["proof_of_useful_work"]["confirmed_count"], 1)
            self.assertEqual(payload["adaptation_proof"]["proof_state"], "candidate_beating_baseline")
            self.assertEqual(payload["adaptation_proof"]["promoted_job_count"], 1)
            self.assertIn("runtime_truth", payload)
            self.assertEqual(payload["runtime_truth"]["session_count"], 0)
            self.assertEqual(payload["pending_approval_count"], 0)
            self.assertEqual(payload["approval_backlog_count"], 0)

    def test_collect_control_plane_status_deduplicates_stale_pending_checkpoints_from_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "vool.db"
            run_migrations(db_path)
            conn = get_connection(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO runtime_sessions (
                        session_id, started_at, updated_at, event_count, last_event_type,
                        last_message, request_preview, task_class, status, last_checkpoint_id
                    ) VALUES (?, ?, ?, 2, 'task_pending_approval', 'Awaiting approval.', 'write file', 'operator_action', 'pending_approval', ?)
                    """,
                    ("session-approval", "2026-03-10T10:00:00+00:00", "2026-03-10T10:02:00+00:00", "checkpoint-current"),
                )
                conn.executemany(
                    """
                    INSERT INTO runtime_checkpoints (
                        checkpoint_id, session_id, task_id, task_class, request_text, source_context_json,
                        status, step_count, last_tool_name, pending_intent_json, state_json, final_response,
                        failure_text, resume_count, created_at, updated_at, completed_at, resumed_from_checkpoint_id
                    ) VALUES (?, ?, ?, ?, ?, '{}', 'pending_approval', 1, 'workspace.write_file', '{}', '{}', '', '', 0, ?, ?, NULL, NULL)
                    """,
                    [
                        (
                            "checkpoint-stale",
                            "session-approval",
                            "task-1",
                            "operator_action",
                            "write file",
                            "2026-03-10T10:00:00+00:00",
                            "2026-03-10T10:01:00+00:00",
                        ),
                        (
                            "checkpoint-current",
                            "session-approval",
                            "task-1",
                            "operator_action",
                            "write file",
                            "2026-03-10T10:01:30+00:00",
                            "2026-03-10T10:02:00+00:00",
                        ),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO runtime_session_events (
                        session_id, seq, event_type, message, details_json, created_at
                    ) VALUES
                    (?, 1, 'task_received', 'Received request.', '{}', ?),
                    (?, 2, 'task_pending_approval', 'Awaiting approval.', '{}', ?)
                    """,
                    (
                        "session-approval",
                        "2026-03-10T10:00:00+00:00",
                        "session-approval",
                        "2026-03-10T10:02:00+00:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            payload = collect_control_plane_status(db_path=db_path)

            self.assertEqual(payload["pending_approval_count"], 1)
            self.assertEqual(payload["approval_backlog_count"], 1)
            self.assertEqual(payload["pending_runtime_checkpoint_count"], 1)
            self.assertEqual(payload["runtime_pending_approval_count"], 1)
            self.assertEqual(payload["runtime_truth"]["session_pending_approval_count"], 1)


if __name__ == "__main__":
    unittest.main()
