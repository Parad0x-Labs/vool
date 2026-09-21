from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.learning import load_procedure_shards, promote_verified_procedure
from core.orchestration import EnvelopeExecutionResult, build_task_envelope, execute_task_envelope
from core.runtime_execution_tools import RuntimeExecutionResult, execute_runtime_tool
from core.runtime_task_events import (
    configure_runtime_event_store,
    list_runtime_session_events,
    list_runtime_sessions,
    reset_runtime_event_state,
)
from storage.migrations import run_migrations


class OrchestrationExecutionPhase1Tests(unittest.TestCase):
    def test_coder_envelope_executes_patch_and_validation_with_required_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def answer():",
                    "-    return 41",
                    "+    return 42",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-1",
                goal="Patch the answer and validate it",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                        {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            receipt_types = {item["receipt_type"] for item in result.receipts}
            self.assertIn("tool_receipt", receipt_types)
            self.assertIn("validation_result", receipt_types)
            self.assertEqual(len(result.details["step_results"]), 2)

    def test_execute_task_envelope_emits_append_only_runtime_events_for_worker_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            db_path = workspace / "runtime-events.db"
            run_migrations(db_path=db_path)
            configure_runtime_event_store(str(db_path))
            reset_runtime_event_state()
            try:
                (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
                (workspace / "test_app.py").write_text(
                    "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                    encoding="utf-8",
                )
                patch_text = "\n".join(
                    [
                        "--- a/app.py",
                        "+++ b/app.py",
                        "@@ -1,2 +1,2 @@",
                        " def answer():",
                        "-    return 41",
                        "+    return 42",
                        "",
                    ]
                )
                envelope = build_task_envelope(
                    role="coder",
                    task_id="coder-proof-worker",
                    goal="Patch and validate the answer function",
                    inputs={
                        "task_class": "debugging",
                        "runtime_tools": [
                            {"step_id": "patch", "intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                            {
                                "step_id": "verify",
                                "intent": "workspace.run_tests",
                                "arguments": {"command": "python3 -m pytest -q test_app.py"},
                            },
                        ],
                    },
                    required_receipts=("tool_receipt", "validation_result"),
                )

                result = execute_task_envelope(
                    envelope,
                    workspace_root=tmpdir,
                    session_id="openclaw:proof-worker",
    source_context={"operating_mode": "auto"},
)

                self.assertTrue(result.ok)
                sessions = list_runtime_sessions(limit=10)
                self.assertEqual(sessions[0]["session_id"], "openclaw:proof-worker")
                self.assertEqual(sessions[0]["request_preview"], "Patch and validate the answer function")
                self.assertEqual(sessions[0]["status"], "completed")
                events = list_runtime_session_events("openclaw:proof-worker", after_seq=0, limit=20)
                # `workspace_mutation_completed` is a TOOLSMITH-lane addition: `execute_runtime_tool`
                # now emits one canonical Activity event per workspace mutation (here,
                # `workspace.apply_unified_diff`'s patch step) on this same append-only event
                # stream, in addition to the orchestrator's own `task_envelope_step_completed`.
                # `workspace.run_tests` is not a mutating intent, so it does not get one.
                self.assertEqual(
                    [item["event_type"] for item in events],
                    [
                        "task_envelope_started",
                        "workspace_mutation_completed",
                        "task_envelope_step_completed",
                        "task_envelope_step_completed",
                        "task_envelope_completed",
                    ],
                )
                self.assertEqual(events[1]["tool_intent"], "workspace.apply_unified_diff")
                self.assertEqual(events[2]["intent"], "workspace.apply_unified_diff")
                self.assertEqual(events[3]["intent"], "workspace.run_tests")
                self.assertEqual(events[4]["receipt_types"], ["tool_receipt", "validation_result"])
            finally:
                reset_runtime_event_state()
                configure_runtime_event_store(None)

    def test_coder_envelope_fails_closed_when_required_validation_receipt_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('goodbye')",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-2",
                goal="Patch without validation",
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "missing_required_receipts")
            self.assertIn("validation_result", result.details["missing_receipts"])

    def test_verifier_envelope_rejects_mutating_workspace_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('tampered')",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="verifier",
                task_id="verify-1",
                goal="Try to mutate the workspace",
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "permission_denied")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")

    def test_coder_envelope_fails_closed_when_attached_provider_lane_is_remote_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1 +1 @@",
                    "-print('hello')",
                    "+print('goodbye')",
                    "",
                ]
            )
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-remote-lane",
                goal="Patch through remote-only lane",
                model_constraints={
                    "provider_capability_truth": [
                        {
                            "provider_id": "kimi:k2",
                            "role_fit": "queen",
                            "locality": "remote",
                            "tool_support": ["structured_json", "code_complex"],
                            "structured_output_support": True,
                            "queue_depth": 0,
                            "max_safe_concurrency": 4,
                        }
                    ]
                },
                inputs={"runtime_tools": [{"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}}]},
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "capacity_blocked")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")
            self.assertEqual(result.details["capacity_state"]["availability_state"], "blocked")

    def test_queen_envelope_executes_children_and_merges_results_deterministically(self) -> None:
        coder = build_task_envelope(
            role="coder",
            task_id="coder-child",
            parent_task_id="queen-parent",
            goal="Patch the code",
            latency_budget="deep",
        )
        verifier = build_task_envelope(
            role="verifier",
            task_id="verify-child",
            parent_task_id="queen-parent",
            goal="Validate the patch",
            latency_budget="low_latency",
        )
        queen = build_task_envelope(
            role="queen",
            task_id="queen-parent",
            goal="Coordinate patch and verification",
            merge_strategy="highest_score",
            inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
        )

        def _child_executor(child: object) -> EnvelopeExecutionResult:
            envelope = child if hasattr(child, "role") else verifier
            if envelope.role == "verifier":
                return EnvelopeExecutionResult(
                    envelope=envelope,
                    ok=True,
                    status="completed",
                    output_text="Validated patch and tests passed.",
                    receipts=({"receipt_type": "validation_result", "ok": True},),
                    details={"score": 0.95},
                )
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=True,
                status="completed",
                output_text="Applied patch.",
                receipts=({"receipt_type": "tool_receipt", "ok": True},),
                details={"score": 0.55},
            )

        result = execute_task_envelope(queen, child_executor=_child_executor,     source_context={"operating_mode": "auto"},
)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output_text, "Validated patch and tests passed.")
        self.assertEqual(result.details["scheduled_children"][0], "verify-child")
        self.assertEqual(result.details["merged_result"]["winner"]["task_id"], "verify-child")
        graph_rows = {item["task_id"]: item for item in result.details["graph"]}
        self.assertEqual(graph_rows["queen-parent"]["status"], "completed")
        self.assertEqual(graph_rows["verify-child"]["status"], "completed")
        self.assertEqual(graph_rows["coder-child"]["status"], "completed")

    def test_queen_envelope_fails_closed_when_final_verifier_child_fails(self) -> None:
        coder = build_task_envelope(
            role="coder",
            task_id="coder-child",
            parent_task_id="queen-parent",
            goal="Patch the code",
            latency_budget="deep",
        )
        verifier = build_task_envelope(
            role="verifier",
            task_id="verify-child",
            parent_task_id="queen-parent",
            goal="Validate the patch",
            latency_budget="low_latency",
            inputs={"depends_on": ["coder-child"]},
        )
        queen = build_task_envelope(
            role="queen",
            task_id="queen-parent",
            goal="Coordinate patch and verification",
            merge_strategy="highest_score",
            inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
        )

        def _child_executor(child: object) -> EnvelopeExecutionResult:
            envelope = child if hasattr(child, "role") else verifier
            if envelope.role == "verifier":
                return EnvelopeExecutionResult(
                    envelope=envelope,
                    ok=False,
                    status="executed",
                    output_text="Tests still fail after the patch.",
                    receipts=({"receipt_type": "validation_result", "ok": False},),
                    details={"score": 0.0},
                )
            return EnvelopeExecutionResult(
                envelope=envelope,
                ok=True,
                status="completed",
                output_text="Applied patch.",
                receipts=({"receipt_type": "tool_receipt", "ok": True},),
                details={"score": 0.95},
            )

        result = execute_task_envelope(queen, child_executor=_child_executor,     source_context={"operating_mode": "auto"},
)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "merge_failed")
        self.assertEqual(result.output_text, "Tests still fail after the patch.")
        self.assertEqual(result.details["merged_result"]["winner"]["task_id"], "verify-child")
        graph_rows = {item["task_id"]: item for item in result.details["graph"]}
        self.assertEqual(graph_rows["queen-parent"]["status"], "failed")
        self.assertEqual(graph_rows["coder-child"]["status"], "completed")
        self.assertEqual(graph_rows["verify-child"]["status"], "failed")

    def test_execute_task_envelope_emits_merge_and_child_runtime_events_for_queen_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            db_path = workspace / "runtime-events.db"
            run_migrations(db_path=db_path)
            configure_runtime_event_store(str(db_path))
            reset_runtime_event_state()
            try:
                coder = build_task_envelope(
                    role="coder",
                    task_id="coder-child",
                    parent_task_id="queen-parent",
                    goal="Patch the code",
                    latency_budget="deep",
                )
                verifier = build_task_envelope(
                    role="verifier",
                    task_id="verify-child",
                    parent_task_id="queen-parent",
                    goal="Validate the patch",
                    latency_budget="low_latency",
                )
                queen = build_task_envelope(
                    role="queen",
                    task_id="queen-parent",
                    goal="Coordinate patch and verification",
                    merge_strategy="highest_score",
                    inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
                )

                def _child_executor(child: object) -> EnvelopeExecutionResult:
                    envelope = child if hasattr(child, "role") else verifier
                    if envelope.role == "verifier":
                        return EnvelopeExecutionResult(
                            envelope=envelope,
                            ok=True,
                            status="completed",
                            output_text="Validated patch and tests passed.",
                            receipts=({"receipt_type": "validation_result", "ok": True},),
                            details={"score": 0.95},
                        )
                    return EnvelopeExecutionResult(
                        envelope=envelope,
                        ok=True,
                        status="completed",
                        output_text="Applied patch.",
                        receipts=({"receipt_type": "tool_receipt", "ok": True},),
                        details={"score": 0.55},
                    )

                result = execute_task_envelope(
                    queen,
                    session_id="openclaw:proof-queen",
                    child_executor=_child_executor,
    source_context={"operating_mode": "auto"},
)

                self.assertTrue(result.ok)
                sessions = list_runtime_sessions(limit=10)
                self.assertEqual(sessions[0]["status"], "completed")
                events = list_runtime_session_events("openclaw:proof-queen", after_seq=0, limit=20)
                event_types = [item["event_type"] for item in events]
                self.assertIn("task_envelope_children_scheduled", event_types)
                self.assertIn("task_envelope_merge_completed", event_types)
                merge_event = next(item for item in events if item["event_type"] == "task_envelope_merge_completed")
                self.assertEqual(merge_event["winner_task_id"], "verify-child")
                child_events = [item for item in events if item["task_id"] in {"coder-child", "verify-child"}]
                self.assertEqual(len(child_events), 2)
                self.assertTrue(all(item["event_type"] == "task_envelope_completed" for item in child_events))
            finally:
                reset_runtime_event_state()
                configure_runtime_event_store(None)

    def test_queen_envelope_respects_child_dependencies_for_real_runtime_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def answer():",
                    "-    return 41",
                    "+    return 42",
                    "",
                ]
            )
            coder = build_task_envelope(
                role="coder",
                task_id="coder-child",
                parent_task_id="queen-parent",
                goal="Patch the code first",
                latency_budget="deep",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                        {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            verifier = build_task_envelope(
                role="verifier",
                task_id="verify-child",
                parent_task_id="queen-parent",
                goal="Verify after the patch lands",
                latency_budget="low_latency",
                inputs={
                    "task_class": "file_inspection",
                    "depends_on": ["coder-child"],
                    "runtime_tools": [{"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}}],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            queen = build_task_envelope(
                role="queen",
                task_id="queen-parent",
                goal="Coordinate patch and verification",
                merge_strategy="highest_score",
                inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
            )

            result = execute_task_envelope(queen, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["scheduled_children"], ["coder-child", "verify-child"])
            graph_rows = {item["task_id"]: item for item in result.details["graph"]}
            self.assertEqual(graph_rows["coder-child"]["status"], "completed")
            self.assertEqual(graph_rows["verify-child"]["status"], "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_coder_envelope_can_resolve_search_reference_into_read_replace_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            path_ref = {
                "$from_step": "locate-target",
                "$path": "observation.primary_path",
                "$require_single_match": True,
            }
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-search-replace",
                goal="Find the target file, patch it, and validate the change",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "locate-target",
                            "intent": "workspace.search_text",
                            "arguments": {"query": "return 41", "limit": 2},
                        },
                        {
                            "step_id": "inspect-target",
                            "intent": "workspace.read_file",
                            "arguments": {"path": dict(path_ref), "start_line": 1, "max_lines": 40},
                        },
                        {
                            "step_id": "apply-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": dict(path_ref),
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "validate-target",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            self.assertEqual(result.details["step_results"][0]["step_id"], "locate-target")
            self.assertEqual(result.details["step_results"][2]["arguments"]["path"], "app.py")

    def test_coder_envelope_can_continue_after_allowed_failure_validation_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-preflight-failure",
                goal="Capture failing tests, patch, then verify cleanly",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "capture-failing-tests",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                            "allow_failure": True,
                        },
                        {
                            "step_id": "patch-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "verify-clean",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")
            self.assertFalse(result.details["step_results"][0]["ok"])
            self.assertTrue(result.details["step_results"][0]["failure_allowed"])
            self.assertTrue(result.details["step_results"][2]["ok"])

    def test_verifier_envelope_can_rollback_tracked_change_after_failed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            session_id = "rollback-session"
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            coder = build_task_envelope(
                role="coder",
                task_id="coder-rollback-setup",
                goal="Apply a bad patch first",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "patch-bad",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 40",
                                "replace_all": True,
                            },
                        },
                    ],
                },
                required_receipts=("tool_receipt",),
            )
            coder_result = execute_task_envelope(coder, workspace_root=tmpdir, session_id=session_id,     source_context={"operating_mode": "auto"},
)
            self.assertTrue(coder_result.ok)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 40\n")

            verifier = build_task_envelope(
                role="verifier",
                task_id="verify-rollback",
                goal="Fail validation and restore the last tracked mutation",
                inputs={
                    "task_class": "file_inspection",
                    "rollback_on_failure": True,
                    "runtime_tools": [
                        {
                            "step_id": "verify-bad-patch",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        }
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )

            result = execute_task_envelope(verifier, workspace_root=tmpdir, session_id=session_id,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            # The pytest run exited nonzero, so the step is "command_failed" rather than the blanket
            # "executed" this used to assert -- sandbox.run_command no longer calls every exit code a
            # success. The outcome under test (ok is False, the change is rolled back) is unchanged.
            self.assertEqual(result.status, "command_failed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")
            rollback = dict(result.details["failure_rollback"] or {})
            self.assertEqual(rollback["intent"], "workspace.rollback_last_change")
            self.assertTrue(rollback["ok"])

    def test_worker_envelope_rejects_allow_failure_on_non_validation_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-invalid-allow-failure",
                goal="Reject invalid allow failure usage",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "patch-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                            "allow_failure": True,
                        },
                    ],
                },
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "invalid_allow_failure")
            self.assertIn("only validation steps can continue after failure", result.output_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")

    def test_coder_envelope_fails_closed_when_search_reference_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "backup.py").write_text("def answer_backup():\n    return 41\n", encoding="utf-8")
            path_ref = {
                "$from_step": "locate-target",
                "$path": "observation.primary_path",
                "$require_single_match": True,
            }
            envelope = build_task_envelope(
                role="coder",
                task_id="coder-ambiguous-search",
                goal="Find the target file and patch it",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "locate-target",
                            "intent": "workspace.search_text",
                            "arguments": {"query": "return 41", "limit": 2},
                        },
                        {
                            "step_id": "apply-target",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": dict(path_ref),
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                    ],
                },
                required_receipts=("tool_receipt",),
            )

            result = execute_task_envelope(envelope, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "unresolved_step_reference")
            self.assertIn("returned 2 matches", result.output_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")
            self.assertEqual((workspace / "backup.py").read_text(encoding="utf-8"), "def answer_backup():\n    return 41\n")

    def test_queen_envelope_can_run_fallback_child_after_failed_dependency_and_merge_last_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            bad_coder = build_task_envelope(
                role="coder",
                task_id="coder-primary",
                parent_task_id="queen-recovery",
                goal="Try the first repair",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "patch-bad",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 40",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "verify-bad",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            fallback_coder = build_task_envelope(
                role="coder",
                task_id="coder-fallback",
                parent_task_id="queen-recovery",
                goal="Recover from the failed first repair",
                inputs={
                    "task_class": "debugging",
                    "depends_on": ["coder-primary"],
                    "continue_on_dependency_failure": True,
                    "restore_workspace_before_run": True,
                    "runtime_tools": [
                        {
                            "step_id": "patch-fallback",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "verify-fallback",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            final_verifier = build_task_envelope(
                role="verifier",
                task_id="verify-final",
                parent_task_id="queen-recovery",
                goal="Confirm the recovered workspace",
                inputs={
                    "task_class": "file_inspection",
                    "depends_on": ["coder-fallback"],
                    "runtime_tools": [{"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}}],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            queen = build_task_envelope(
                role="queen",
                task_id="queen-recovery",
                goal="Recover from the first failed coder path",
                merge_strategy="last_success",
                inputs={"subtasks": [bad_coder.to_dict(), fallback_coder.to_dict(), final_verifier.to_dict()]},
            )

            result = execute_task_envelope(queen, workspace_root=tmpdir,     source_context={"operating_mode": "auto"},
)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["merged_result"]["winner"]["task_id"], "verify-final")
            fallback_result = next(
                item for item in result.details["child_results"] if item["task_id"] == "coder-fallback"
            )
            self.assertEqual(fallback_result["details"]["workspace_restore"]["status"], "executed")
            self.assertEqual(
                fallback_result["details"]["workspace_restore"]["details"]["restore_session_id"],
                "coder-primary",
            )
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_queen_envelope_fails_closed_when_fallback_restore_cannot_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            bad_coder = build_task_envelope(
                role="coder",
                task_id="coder-primary",
                parent_task_id="queen-recovery-fail",
                goal="Mutate the file and fail validation",
                inputs={
                    "task_class": "debugging",
                    "runtime_tools": [
                        {
                            "step_id": "patch-bad",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 40",
                                "replace_all": True,
                            },
                        },
                        {
                            "step_id": "verify-bad",
                            "intent": "workspace.run_tests",
                            "arguments": {"command": "python3 -m pytest -q test_app.py"},
                        },
                    ],
                },
                required_receipts=("tool_receipt", "validation_result"),
            )
            fallback_coder = build_task_envelope(
                role="coder",
                task_id="coder-fallback",
                parent_task_id="queen-recovery-fail",
                goal="Recover from the failed first repair",
                inputs={
                    "task_class": "debugging",
                    "depends_on": ["coder-primary"],
                    "continue_on_dependency_failure": True,
                    "restore_workspace_before_run": True,
                    "runtime_tools": [
                        {
                            "step_id": "patch-fallback",
                            "intent": "workspace.replace_in_file",
                            "arguments": {
                                "path": "app.py",
                                "old_text": "return 41",
                                "new_text": "return 42",
                                "replace_all": True,
                            },
                        }
                    ],
                },
                required_receipts=("tool_receipt",),
            )
            queen = build_task_envelope(
                role="queen",
                task_id="queen-recovery-fail",
                goal="Try a fallback repair only if restore succeeds",
                merge_strategy="last_success",
                inputs={"subtasks": [bad_coder.to_dict(), fallback_coder.to_dict()]},
            )

            def _runtime_tool_executor(
                intent: str, arguments: dict[str, object], source_context: dict[str, object] | None
            ) -> RuntimeExecutionResult:
                if intent == "workspace.rollback_last_change":
                    return RuntimeExecutionResult(
                        handled=True,
                        ok=False,
                        status="disabled",
                        response_text="Rollback is disabled for this recovery test.",
                        details={},
                    )
                result = execute_runtime_tool(intent, arguments, source_context=source_context)
                assert result is not None
                return result

            result = execute_task_envelope(
                queen,
                workspace_root=tmpdir,
                runtime_tool_executor=_runtime_tool_executor,
    source_context={"operating_mode": "auto"},
)

            self.assertFalse(result.ok)
            self.assertEqual(result.status, "merge_failed")
            self.assertEqual(result.details["merged_result"]["winner"]["task_id"], "coder-fallback")
            self.assertEqual(result.details["merged_result"]["winner"]["status"], "restore_failed")
            self.assertIn("could not restore the workspace before recovery", result.output_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 40\n")

    def test_successful_envelope_records_verified_reuse_metrics_for_attached_procedures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )
            patch_text = "\n".join(
                [
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -1,2 +1,2 @@",
                    " def answer():",
                    "-    return 41",
                    "+    return 42",
                    "",
                ]
            )

            def _data_path(*parts: str) -> Path:
                path = Path(tmpdir) / "data"
                for part in parts:
                    path /= str(part)
                return path

            with mock.patch("core.learning.procedure_shards.data_path", side_effect=_data_path):
                shard = promote_verified_procedure(
                    task_class="debugging",
                    title="Patch and verify a repo file",
                    preconditions=["workspace is writable"],
                    steps=["apply diff", "run tests"],
                    tool_receipts=[{"intent": "workspace.apply_unified_diff"}],
                    # PB01: ok=True alone no longer stages a lesson; the evidence must carry the
                    # verification signal the production caller records (tool/command/returncode).
                    validation={
                        "ok": True,
                        "tool": "workspace.run_tests",
                        "command": "python3 -m pytest -q test_app.py",
                        "returncode": 0,
                    },
                    rollback={"intent": "workspace.rollback_last_change"},
                )

                assert shard is not None
                envelope = build_task_envelope(
                    role="coder",
                    task_id="coder-reuse",
                    goal="Reuse a verified procedure and validate it",
                    inputs={
                        "task_class": "debugging",
                        "reused_procedure_ids": [shard.procedure_id],
                        "runtime_tools": [
                            {"intent": "workspace.apply_unified_diff", "arguments": {"patch": patch_text}},
                            {"intent": "workspace.run_tests", "arguments": {"command": "python3 -m pytest -q test_app.py"}},
                        ],
                    },
                    required_receipts=("tool_receipt", "validation_result"),
                )

                result = execute_task_envelope(envelope, workspace_root=str(workspace), session_id="reuse-session",     source_context={"operating_mode": "auto"},
)

                self.assertTrue(result.ok)
                self.assertTrue(result.details["verified_reuse"])
                self.assertEqual(result.details["reused_procedure_updates"][0]["procedure_id"], shard.procedure_id)

                loaded = {item.procedure_id: item for item in load_procedure_shards()}
                self.assertEqual(loaded[shard.procedure_id].reuse_count, 1)
                self.assertEqual(loaded[shard.procedure_id].verified_reuse_count, 1)
                self.assertEqual(loaded[shard.procedure_id].last_reuse_task_class, "debugging")


if __name__ == "__main__":
    unittest.main()
