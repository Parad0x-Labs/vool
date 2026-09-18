from __future__ import annotations

from core.context_scope import ContextAccessPolicy
from core.runtime_execution_history import build_runtime_execution_history, summarize_runtime_surface
from core.runtime_operator_snapshot import build_runtime_operator_snapshot


def test_execution_history_surfaces_pending_approval_and_touched_paths() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:approval",
            "request_preview": "Clean temp files under alpha",
            "task_class": "operator_action",
            "status": "pending_approval",
            "last_message": "Awaiting approval.",
            "updated_at": "2026-03-30T12:00:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-approval",
            "status": "pending_approval",
            "step_count": 1,
            "resume_count": 0,
            "last_tool_name": "operator.cleanup_temp_files",
            "pending_intent": {"intent": "operator.cleanup_temp_files"},
        },
        events=[
            {
                "event_type": "task_received",
                "message": "Received request.",
                "request_preview": "Clean temp files under alpha",
            },
            {
                "event_type": "tool_preview",
                "message": "Approval required for operator.cleanup_temp_files.",
                "tool_name": "operator.cleanup_temp_files",
                "status": "pending_approval",
                "target_path": "alpha/tmp",
            },
        ],
        receipts=[
            {
                "tool_name": "workspace.write_file",
                "arguments": {"target_path": "alpha/plan.txt"},
                "execution": {},
            }
        ],
    )

    assert history["bounded_execution"]["approval_state"] == "pending"
    assert history["bounded_execution"]["checkpoint_status"] == "pending_approval"
    assert history["latest_tool"] == "operator.cleanup_temp_files"
    assert "alpha/tmp" in history["changed_paths"]
    assert "alpha/plan.txt" in history["touched_paths"]
    assert history["timeline"][1]["value"] == "pending"


def test_execution_history_uses_runtime_event_details_for_tool_and_changed_paths() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:builder-chain",
            "request_preview": "read the file back exactly",
            "task_class": "unknown",
            "status": "completed",
            "last_message": "Fast-path response ready.",
            "updated_at": "2026-03-31T05:47:58+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-builder-chain",
            "status": "completed",
            "step_count": 0,
            "resume_count": 0,
            "last_tool_name": "",
            "pending_intent": {},
        },
        events=[
            {
                "event_type": "task_completed",
                "message": "Fast-path response ready: alpha line",
                "status": "builder_controller_direct_response",
                "details": {
                    "tool_name": "workspace.read_file",
                    "changed_paths": ["march_shift_folder/weekly_notes.txt"],
                },
            }
        ],
        receipts=[],
    )

    assert history["latest_tool"] == "workspace.read_file"
    assert "march_shift_folder/weekly_notes.txt" in history["changed_paths"]
    assert "march_shift_folder/weekly_notes.txt" in history["touched_paths"]


def test_execution_history_surfaces_validation_failure_and_completed_rollback() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:repair",
            "request_preview": "Patch the failing function",
            "task_class": "debugging",
            "status": "failed",
            "last_message": "Verifier stayed red after patch.",
            "updated_at": "2026-03-30T12:05:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-repair",
            "status": "failed",
            "step_count": 3,
            "resume_count": 1,
            "last_tool_name": "workspace.run_tests",
            "pending_intent": {},
        },
        events=[
            {
                "event_type": "task_envelope_started",
                "message": "coder envelope `coder-1` started.",
                "task_role": "coder",
            },
            {
                "event_type": "task_envelope_step_completed",
                "message": "coder envelope `coder-1` ran `workspace.apply_unified_diff`.",
                "task_role": "coder",
                "intent": "workspace.apply_unified_diff",
                "status": "executed",
                "target_path": "app.py",
            },
            {
                "event_type": "task_envelope_step_failed",
                "message": "verifier envelope `verify-1` failed while running `workspace.run_tests`.",
                "task_role": "verifier",
                "intent": "workspace.run_tests",
                "status": "failed",
            },
            {
                "event_type": "task_envelope_rollback_completed",
                "message": "coder envelope `coder-1` rolled back the last workspace mutation after failed validation.",
                "task_role": "coder",
                "intent": "workspace.rollback_last_change",
                "status": "executed",
            },
            {
                "event_type": "task_envelope_failed",
                "message": "coder envelope `coder-1` failed.",
                "task_role": "coder",
                "status": "failed",
                "receipt_types": ["tool_receipt", "validation_result"],
            },
        ],
        receipts=[
            {
                "tool_name": "workspace.apply_unified_diff",
                "arguments": {"target_path": "app.py"},
                "execution": {},
            },
            {
                "tool_name": "workspace.rollback_last_change",
                "arguments": {},
                "execution": {"restored_paths": ["app.py"]},
            },
        ],
    )

    assert history["bounded_execution"]["verifier_state"] == "failed"
    assert history["bounded_execution"]["model_review_state"] == "not_run"
    assert history["bounded_execution"]["validation_state"] == "failed"
    assert history["bounded_execution"]["rollback_state"] == "completed"
    assert history["bounded_execution"]["mutating_tool_count"] == 2
    assert history["receipt_types"] == ["tool_receipt", "validation_result"]
    timeline = {item["label"]: item for item in history["timeline"]}
    assert timeline["Model review"]["value"] == "not_run"
    assert timeline["Validation"]["value"] == "failed"
    assert timeline["Recovery"]["value"] == "completed"
    assert "app.py" in history["touched_paths"]


def test_execution_history_keeps_model_review_and_validation_distinct() -> None:
    history = build_runtime_execution_history(
        session={"session_id": "typed-evidence", "status": "completed"},
        events=[
            {"event_type": "model_lane_verifier_completed", "status": "completed"},
            {
                "event_type": "tool_failed",
                "tool_name": "workspace.run_tests",
                "status": "failed",
            },
        ],
    )

    bounded = history["bounded_execution"]
    assert bounded["model_review_state"] == "passed"
    assert bounded["validation_state"] == "failed"
    assert bounded["verifier_state"] == "failed", "legacy aggregate remains for old consumers"
    labels = [item["label"] for item in history["timeline"]]
    assert "Model review" in labels
    assert "Validation" in labels
    assert "Verifier" not in labels
    assert "Verification" not in labels


def test_execution_history_preserves_each_causal_model_review_outcome() -> None:
    cases = {
        "model_lane_verifier_completed": ("passed", "passed"),
        "model_lane_verifier_flagged": ("flagged", "failed"),
        "model_lane_verifier_blocked": ("blocked", "unavailable"),
        "model_lane_verifier_degraded": ("degraded", "unavailable"),
        "model_lane_verifier_failed": ("runtime_failed", "unavailable"),
    }
    for event_type, (expected_review, expected_legacy) in cases.items():
        history = build_runtime_execution_history(
            session={"session_id": event_type, "status": "completed"},
            events=[{"event_type": event_type, "status": "failed"}],
        )

        bounded = history["bounded_execution"]
        assert bounded["model_review_state"] == expected_review
        assert bounded["validation_state"] == "not_run"
        assert bounded["verifier_state"] == expected_legacy
        assert history["status"] == "completed"
        model_review = next(item for item in history["timeline"] if item["label"] == "Model review")
        assert model_review["value"] == expected_review
        if expected_review != "flagged":
            assert "flagged verdict" not in model_review["detail"]


def test_ambiguous_verifier_role_does_not_invent_review_or_validation() -> None:
    history = build_runtime_execution_history(
        session={"session_id": "ambiguous-verifier", "status": "completed"},
        events=[
            {
                "event_type": "task_envelope_completed",
                "task_role": "verifier",
                "status": "completed",
            }
        ],
    )

    bounded = history["bounded_execution"]
    assert bounded["model_review_state"] == "not_run"
    assert bounded["validation_state"] == "not_run"
    assert bounded["verifier_state"] == "not_run"


def test_runtime_surface_summary_counts_resume_and_failures() -> None:
    summary = summarize_runtime_surface(
        [
            {
                "session_id": "openclaw:one",
                "status": "completed",
                "updated_at": "2026-03-30T12:10:00+00:00",
                "execution_history": {
                    "title": "Finished request",
                    "status": "completed",
                    "request_status": "completed",
                    "latest_tool": "workspace.write_file",
                    "bounded_execution": {
                        "resume_available": False,
                        "approval_state": "not_required",
                        "verifier_state": "not_run",
                        "rollback_state": "not_triggered",
                        "restore_state": "not_triggered",
                        "failure_count": 0,
                    },
                },
            },
            {
                "session_id": "openclaw:two",
                "status": "failed",
                "updated_at": "2026-03-30T12:11:00+00:00",
                "execution_history": {
                    "title": "Broken request",
                    "status": "running",
                    "request_status": "running",
                    "latest_tool": "workspace.run_tests",
                    "bounded_execution": {
                        "resume_available": True,
                        "approval_state": "pending",
                        "verifier_state": "running",
                        "rollback_state": "running",
                        "restore_state": "not_triggered",
                        "failure_count": 1,
                    },
                },
            },
        ]
    )

    assert summary["session_count"] == 2
    assert summary["status_counts"]["completed"] == 1
    assert summary["status_counts"]["running"] == 1
    assert summary["active_execution_count"] == 1
    assert summary["resume_ready_count"] == 1
    assert summary["session_pending_approval_count"] == 1
    assert summary["approval_pending_count"] == 1
    assert summary["verifier_pending_count"] == 1
    assert summary["rollback_pending_count"] == 1
    assert summary["recovery_pending_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["latest_session"]["session_id"] == "openclaw:one"


def test_execution_history_clears_stale_approval_after_completion() -> None:
    history = build_runtime_execution_history(
        session={
            "session_id": "openclaw:completed-after-approval",
            "request_preview": "Post the result",
            "task_class": "integration_orchestration",
            "status": "completed",
            "last_message": "Completed after confirmation moved on.",
            "updated_at": "2026-03-30T12:15:00+00:00",
            "resume_available": False,
        },
        checkpoint={
            "checkpoint_id": "runtime-completed-after-approval",
            "status": "completed",
            "step_count": 2,
            "resume_count": 0,
            "last_tool_name": "",
            "pending_intent": {},
        },
        events=[
            {
                "event_type": "task_pending_approval",
                "message": "Awaiting approval to post.",
                "status": "pending_approval",
            },
            {
                "event_type": "task_completed",
                "message": "Posted successfully.",
                "status": "completed",
            },
        ],
    )

    assert history["bounded_execution"]["approval_state"] == "cleared"
    assert history["timeline"][1]["value"] == "cleared"


def test_runtime_operator_snapshot_merges_execution_and_memory_truth(monkeypatch) -> None:
    monkeypatch.setattr(
        "core.runtime_operator_snapshot.list_runtime_sessions",
        lambda limit: [
            {
                "session_id": "openclaw:operator",
                "status": "completed",
                "updated_at": "2026-03-31T01:00:00Z",
                "request_preview": "Read the file",
                "execution_history": {
                    "title": "Read the file",
                    "status": "completed",
                    "request_status": "completed",
                    "latest_tool": "workspace.read_file",
                    "changed_paths": ["workspace/notes.txt"],
                },
            }
        ],
    )
    monkeypatch.setattr(
        "core.runtime_operator_snapshot.list_runtime_session_events",
        lambda session_id, after_seq=0, limit=12: [
            {
                "seq": 1,
                "event_type": "task_completed",
                "status": "completed",
                "tool_name": "workspace.read_file",
                "message": "Completed readback.",
            }
        ],
    )
    monkeypatch.setattr(
        "core.runtime_operator_snapshot.memory_lifecycle_snapshot",
        lambda **kwargs: {
            "session_id": "openclaw:operator",
            "recent_conversation_event_count": 2,
            "relevant_memory_count": 1,
            "selection_summary": "query `continue the file work` selected 1 durable memory entries, 0 prior session summaries, and 1 heuristic signals.",
        },
    )
    monkeypatch.setattr(
        "core.runtime_operator_snapshot.recent_archived_dialogue_topics",
        lambda session_id, limit=4: [
            {
                "summary": "bitcoin price lookup | unresolved: couldn't ground fresh quote",
                "closure_status": "unresolved",
            }
        ],
    )

    snapshot = build_runtime_operator_snapshot(
        session_id="openclaw:operator",
        query_text="continue the file work",
        access_policy=ContextAccessPolicy(chat_id="openclaw:operator"),
    )

    assert snapshot["session"]["execution_history"]["latest_tool"] == "workspace.read_file"
    assert snapshot["session"]["recent_runtime_event_count"] == 1
    assert snapshot["memory_lifecycle"]["relevant_memory_count"] == 1
    assert snapshot["dialogue_lifecycle"]["recent_archived_topic_count"] == 1
    assert any("workspace/notes.txt" in line for line in snapshot["inspection_summary"])
    assert any("Latest closed topic" in line for line in snapshot["inspection_summary"])
