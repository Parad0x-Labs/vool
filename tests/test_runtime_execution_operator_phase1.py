from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.execution.validation_tools import runtime_validation_command, validation_command
from core.orchestration import build_task_envelope
from core.runtime_execution_tools import execute_runtime_tool
from core.task_router import classify
from core.tool_intent_executor import plan_tool_workflow


class RuntimeExecutionOperatorPhase1Tests(unittest.TestCase):
    def test_workspace_list_tree_and_symbol_search_are_grounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "app.py").write_text(
                "class Runner:\n    pass\n\n\ndef launch():\n    return Runner()\n",
                encoding="utf-8",
            )

            tree = execute_runtime_tool(
                "workspace.list_tree",
                {"path": ".", "limit": 20},
                source_context={"workspace": tmpdir},
            )
            assert tree is not None
            self.assertTrue(tree.ok)
            self.assertIn("src/", tree.response_text)
            self.assertIn("src/app.py", tree.response_text)
            self.assertEqual(tree.details["observation"]["intent"], "workspace.list_tree")

            symbol = execute_runtime_tool(
                "workspace.symbol_search",
                {"symbol": "launch", "path": "src"},
                source_context={"workspace": tmpdir},
            )
            assert symbol is not None
            self.assertTrue(symbol.ok)
            self.assertIn("[function_definition]", symbol.response_text)
            self.assertEqual(symbol.details["observation"]["matches"][0]["path"], "src/app.py")

    def test_workspace_apply_unified_diff_and_rollback_use_tracked_mutation_history(self) -> None:
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

            applied = execute_runtime_tool(
                "workspace.apply_unified_diff",
                {"patch": patch_text},
                source_context={"workspace": tmpdir, "session_id": "session-1"},
            )
            assert applied is not None
            self.assertTrue(applied.ok)
            self.assertIn("Applied unified diff", applied.response_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('goodbye')\n")

            rolled_back = execute_runtime_tool(
                "workspace.rollback_last_change",
                {},
                source_context={"workspace": tmpdir, "session_id": "session-1"},
            )
            assert rolled_back is not None
            self.assertTrue(rolled_back.ok)
            self.assertIn("restored `app.py`", rolled_back.response_text)
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('hello')\n")

    def test_workspace_apply_unified_diff_has_python_fallback_when_shell_patchers_are_unavailable(self) -> None:
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

            with mock.patch("core.execution.workspace_tools.shutil.which", return_value=None):
                applied = execute_runtime_tool(
                    "workspace.apply_unified_diff",
                    {"patch": patch_text},
                    source_context={"workspace": tmpdir, "session_id": "session-fallback"},
                )

            assert applied is not None
            self.assertTrue(applied.ok)
            self.assertEqual(applied.details["observation"]["engine"], "python_fallback")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "print('goodbye')\n")

    def test_workspace_git_status_and_diff_report_real_repo_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.py"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('goodbye')\n", encoding="utf-8")

            status = execute_runtime_tool(
                "workspace.git_status",
                {},
                source_context={"workspace": tmpdir},
            )
            assert status is not None
            self.assertTrue(status.ok)
            self.assertIn("tracked.py", status.response_text)

            diff = execute_runtime_tool(
                "workspace.git_diff",
                {},
                source_context={"workspace": tmpdir},
            )
            assert diff is not None
            self.assertTrue(diff.ok)
            self.assertIn("-print('hello')", diff.response_text)
            self.assertIn("+print('goodbye')", diff.response_text)

    def test_workspace_git_summary_reports_grounded_branch_and_commit_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.execution.git_tools._local_commit_day_windows",
            return_value=(
                (
                    "2026-03-30",
                    datetime.fromisoformat("2026-03-30T00:00:00+00:00"),
                    datetime.fromisoformat("2026-03-31T00:00:00+00:00"),
                ),
                (
                    "2026-03-29",
                    datetime.fromisoformat("2026-03-29T00:00:00+00:00"),
                    datetime.fromisoformat("2026-03-30T00:00:00+00:00"),
                ),
                "UTC",
            ),
        ):
            workspace = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=workspace, check=True, capture_output=True)

            env_yesterday = {
                **os.environ,
                "GIT_AUTHOR_DATE": "2026-03-29T12:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-03-29T12:00:00+00:00",
            }
            env_today = {
                **os.environ,
                "GIT_AUTHOR_DATE": "2026-03-30T12:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-03-30T12:00:00+00:00",
            }

            (workspace / "tracked.py").write_text("print('one')\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.py"], cwd=workspace, check=True, capture_output=True, env=env_yesterday)
            subprocess.run(["git", "commit", "-m", "yesterday"], cwd=workspace, check=True, capture_output=True, env=env_yesterday)
            subprocess.run(["git", "checkout", "-b", "feature-one"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('two')\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "today-one"], cwd=workspace, check=True, capture_output=True, env=env_today)
            subprocess.run(["git", "checkout", "-b", "feature-two"], cwd=workspace, check=True, capture_output=True)
            (workspace / "tracked.py").write_text("print('three')\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-am", "today-two"], cwd=workspace, check=True, capture_output=True, env=env_today)

            summary = execute_runtime_tool(
                "workspace.git_summary",
                {},
                source_context={"workspace": tmpdir},
            )
            assert summary is not None
            self.assertTrue(summary.ok)
            self.assertTrue(summary.response_text.splitlines()[0].startswith("Git summary: branch feature-two @"))
            self.assertIn("local branches: 3", summary.response_text)
            self.assertIn("remote tracking branches: 0", summary.response_text)
            self.assertIn("commits on 2026-03-30: 2", summary.response_text)
            self.assertIn("commits on 2026-03-29: 1", summary.response_text)
            observation = summary.details["observation"]
            self.assertEqual(observation["intent"], "workspace.git_summary")
            self.assertEqual(observation["local_branch_count"], 3)
            self.assertEqual(observation["remote_branch_count"], 0)
            self.assertEqual(observation["today_commit_count"], 2)
            self.assertEqual(observation["yesterday_commit_count"], 1)

    def test_workspace_git_summary_can_include_recent_commit_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tests@example.test"], cwd=workspace, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=workspace, check=True, capture_output=True)

            env = {
                **os.environ,
                "GIT_AUTHOR_DATE": "2026-03-30T12:00:00+00:00",
                "GIT_COMMITTER_DATE": "2026-03-30T12:00:00+00:00",
            }
            for index in range(1, 4):
                (workspace / "tracked.py").write_text(f"print('{index}')\n", encoding="utf-8")
                subprocess.run(["git", "add", "tracked.py"], cwd=workspace, check=True, capture_output=True, env=env)
                subprocess.run(["git", "commit", "-m", f"commit-{index}"], cwd=workspace, check=True, capture_output=True, env=env)

            summary = execute_runtime_tool(
                "workspace.git_summary",
                {"recent_limit": 3},
                source_context={"workspace": tmpdir},
            )
            assert summary is not None
            self.assertTrue(summary.ok)
            self.assertIn("- recent commits:", summary.response_text)
            self.assertIn("commit-3", summary.response_text)
            self.assertIn("commit-2", summary.response_text)
            self.assertIn("commit-1", summary.response_text)
            recent_commits = summary.details["observation"]["recent_commits"]
            self.assertEqual(len(recent_commits), 3)
            self.assertEqual(recent_commits[0]["subject"], "commit-3")

    def test_workspace_run_tests_executes_validation_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "test_sample.py").write_text(
                "def test_truth():\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            result = execute_runtime_tool(
                "workspace.run_tests",
                {"command": "python3 -m pytest -q test_sample.py"},
                source_context={"workspace": tmpdir},
            )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["observation"]["intent"], "workspace.run_tests")
            self.assertEqual(result.details["observation"]["returncode"], 0)
            self.assertIn(sys.executable, result.details["observation"]["command"])

    def test_workspace_run_tests_bypasses_linux_isolation_backends_for_trusted_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "test_sample.py").write_text(
                "def test_truth():\n    assert 2 + 2 == 4\n",
                encoding="utf-8",
            )
            with mock.patch(
                "sandbox.job_runner.JobRunner._kernel_network_isolation_prefix",
                side_effect=AssertionError("trusted validation should not request kernel isolation"),
            ):
                result = execute_runtime_tool(
                    "workspace.run_tests",
                    {"command": "python3 -m pytest -q test_sample.py"},
                    source_context={"workspace": tmpdir},
                )
            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["observation"]["returncode"], 0)

    def test_validation_command_defaults_are_honest(self) -> None:
        with mock.patch("core.execution.validation_tools.shutil.which") as which:
            which.side_effect = lambda name: "/usr/bin/" + name if name in {"pytest", "ruff"} else None
            self.assertEqual(validation_command("workspace.run_tests", {}), "pytest -q")
            self.assertEqual(validation_command("workspace.run_lint", {}), "ruff check .")
            self.assertEqual(validation_command("workspace.run_formatter", {}), "ruff format --check .")
            self.assertEqual(validation_command("workspace.run_formatter", {"apply": True}), "ruff format .")

    def test_runtime_validation_command_uses_current_interpreter_for_pytest_and_ruff(self) -> None:
        self.assertEqual(
            runtime_validation_command("python3 -m pytest -q test_sample.py"),
            shlex.join([sys.executable, "-m", "pytest", "-q", "test_sample.py"]),
        )
        self.assertEqual(
            runtime_validation_command("ruff check ."),
            shlex.join([sys.executable, "-m", "ruff", "check", "."]),
        )

    def test_runtime_tool_can_execute_coder_task_envelope(self) -> None:
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
                task_id="coder-runtime",
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

            result = execute_runtime_tool(
                "orchestration.execute_envelope",
                {"task_envelope": envelope.to_dict()},
                source_context={"workspace": tmpdir, "session_id": "session-envelope"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["observation"]["intent"], "orchestration.execute_envelope")
            self.assertEqual(result.details["observation"]["task_role"], "coder")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_runtime_tool_executes_queen_envelope_with_dependency_order(self) -> None:
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
                goal="Patch first",
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
                goal="Verify second",
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
                goal="Coordinate both steps",
                merge_strategy="highest_score",
                inputs={"subtasks": [coder.to_dict(), verifier.to_dict()]},
            )

            result = execute_runtime_tool(
                "orchestration.execute_envelope",
                {"task_envelope": queen.to_dict()},
                source_context={"workspace": tmpdir, "session_id": "session-queen"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.details["scheduled_children"], ["coder-child", "verify-child"])
            self.assertEqual(result.details["observation"]["task_role"], "queen")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_orchestrated_operator_envelope_executes_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            decision = plan_tool_workflow(
                user_text="replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-planned-envelope"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["observation"]["task_role"], "queen")
            self.assertEqual(result.details["scheduled_children"][0][:6], "coder-")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_orchestrated_operator_envelope_can_locate_target_before_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            decision = plan_tool_workflow(
                user_text="replace `return 41` with `return 42`, then run `python3 -m pytest -q test_app.py`",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
            subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
            coder_steps = list(subtasks[0]["inputs"]["runtime_tools"])
            self.assertEqual(coder_steps[0]["intent"], "workspace.search_text")
            self.assertEqual(coder_steps[1]["arguments"]["path"]["$from_step"], "locate-replacement-target")

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-planned-envelope-search"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_orchestrated_operator_envelope_can_capture_preflight_failure_then_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            decision = plan_tool_workflow(
                user_text=(
                    "tests are failing. replace `return 41` with `return 42` in app.py, "
                    "then run `python3 -m pytest -q test_app.py`"
                ),
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
            subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
            self.assertEqual([item["role"] for item in subtasks], ["verifier", "coder", "verifier"])

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-preflight-failure"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.details["observation"]["task_role"], "queen")
            self.assertEqual(len(result.details["merged_result"]["results"]), 3)
            preflight = next(
                item
                for item in result.details["merged_result"]["results"]
                if item["task_id"].startswith("preflight-verify-")
            )
            self.assertEqual(preflight["role"], "verifier")
            self.assertTrue(preflight["ok"])
            self.assertEqual(preflight["status"], "completed")
            self.assertFalse(preflight["details"]["step_results"][0]["ok"])
            self.assertTrue(preflight["details"]["step_results"][0]["failure_allowed"])
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_planned_validation_run_can_feed_failed_test_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            first = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(first.handled)
            self.assertEqual(first.next_payload["intent"], "workspace.run_tests")

            validation_result = execute_runtime_tool(
                first.next_payload["intent"],
                dict(first.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-validation-followup"},
            )

            assert validation_result is not None
            self.assertFalse(validation_result.ok)
            self.assertEqual(validation_result.details["observation"]["intent"], "workspace.run_tests")

            second = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(first.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    }
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(second.handled)
            self.assertEqual(second.next_payload["intent"], "workspace.read_file")
            self.assertEqual(second.next_payload["arguments"]["path"], "test_app.py")

            third = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(first.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": dict(second.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 5,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(third.handled)
            self.assertEqual(third.next_payload["intent"], "workspace.symbol_search")
            self.assertEqual(third.next_payload["arguments"]["symbol"], "answer")

            fourth = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(first.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": dict(second.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 5,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": dict(third.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": str(third.next_payload["arguments"]["symbol"]),
                            "match_count": 2,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(fourth.handled)
            self.assertEqual(fourth.next_payload["intent"], "workspace.read_file")
            self.assertEqual(fourth.next_payload["arguments"]["path"], "app.py")

            fifth = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(first.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": dict(second.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 5,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": dict(third.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": str(third.next_payload["arguments"]["symbol"]),
                            "match_count": 2,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": dict(fourth.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 2,
                            "lines": [
                                {"line_number": 1, "text": "def answer():"},
                                {"line_number": 2, "text": "    return 41"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(fifth.handled)
            self.assertEqual(fifth.next_payload["intent"], "orchestration.execute_envelope")

            result = execute_runtime_tool(
                fifth.next_payload["intent"],
                dict(fifth.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-diagnosis-candidate-repair"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")

    def test_validation_diagnosis_can_walk_second_unread_symbol_match_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "helpers.py").write_text("def answer_value():\n    return 41\n", encoding="utf-8")
            (workspace / "app.py").write_text(
                "from helpers import answer_value\n\n\ndef answer():\n    return answer_value()\n",
                encoding="utf-8",
            )
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            validation = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            validation_result = execute_runtime_tool(
                validation.next_payload["intent"],
                dict(validation.next_payload["arguments"]),
                source_context={"workspace": tmpdir},
            )
            assert validation_result is not None
            self.assertFalse(validation_result.ok)

            search = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(validation.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertEqual(search.next_payload["intent"], "workspace.symbol_search")

            next_read = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(validation.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": dict(search.next_payload["arguments"]),
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": "answer",
                            "match_count": 3,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                                {"path": "helpers.py", "line": 1, "kind": "function_definition", "snippet": "def answer_value():"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from helpers import answer_value"},
                                {"line_number": 3, "text": "def answer():"},
                                {"line_number": 4, "text": "    return answer_value()"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(next_read.handled)
            self.assertEqual(next_read.reason, "planned_next_read_after_symbol_diagnosis")
            self.assertEqual(next_read.next_payload["intent"], "workspace.read_file")
            self.assertEqual(next_read.next_payload["arguments"]["path"], "helpers.py")

    def test_planned_repair_can_patch_one_hop_delegated_helper_after_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "from helpers import answer_value\n\n\ndef answer():\n    return answer_value()\n",
                encoding="utf-8",
            )
            (workspace / "helpers.py").write_text("def answer_value():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            validation = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            validation_result = execute_runtime_tool(
                validation.next_payload["intent"],
                dict(validation.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-diagnosis-helper-repair"},
            )

            decision = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(validation.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": {"symbol": "answer", "limit": 10},
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": "answer",
                            "match_count": 3,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                                {"path": "helpers.py", "line": 1, "kind": "function_definition", "snippet": "def answer_value():"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from helpers import answer_value"},
                                {"line_number": 3, "text": "def answer():"},
                                {"line_number": 4, "text": "    return answer_value()"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "helpers.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "helpers.py",
                            "start_line": 1,
                            "line_count": 2,
                            "lines": [
                                {"line_number": 1, "text": "def answer_value():"},
                                {"line_number": 2, "text": "    return 41"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-delegated-helper-repair"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "helpers.py").read_text(encoding="utf-8"), "def answer_value():\n    return 42\n")

    def test_planned_repair_can_follow_delegate_lookup_before_helper_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "from helpers import answer_value\n\n\ndef answer():\n    return answer_value()\n",
                encoding="utf-8",
            )
            (workspace / "helpers.py").write_text("def answer_value():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            validation = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            validation_result = execute_runtime_tool(
                validation.next_payload["intent"],
                dict(validation.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-delegate-lookup-validation"},
            )

            validation_observation = {
                "tool_name": "workspace.run_tests",
                "arguments": dict(validation.next_payload["arguments"]),
                "observation": dict(validation_result.details["observation"]),
            }
            test_read_observation = {
                "tool_name": "workspace.read_file",
                "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                "observation": {
                    "intent": "workspace.read_file",
                    "tool_surface": "workspace",
                    "ok": True,
                    "status": "executed",
                    "path": "test_app.py",
                    "start_line": 1,
                    "line_count": 4,
                    "lines": [
                        {"line_number": 1, "text": "from app import answer"},
                        {"line_number": 4, "text": "    assert answer() == 42"},
                    ],
                },
            }
            answer_symbol_observation = {
                "tool_name": "workspace.symbol_search",
                "arguments": {"symbol": "answer", "limit": 10},
                "observation": {
                    "intent": "workspace.symbol_search",
                    "tool_surface": "workspace",
                    "ok": True,
                    "status": "executed",
                    "symbol": "answer",
                    "match_count": 2,
                    "matches": [
                        {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                        {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                    ],
                },
            }
            app_read_observation = {
                "tool_name": "workspace.read_file",
                "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                "observation": {
                    "intent": "workspace.read_file",
                    "tool_surface": "workspace",
                    "ok": True,
                    "status": "executed",
                    "path": "app.py",
                    "start_line": 1,
                    "line_count": 4,
                    "lines": [
                        {"line_number": 1, "text": "from helpers import answer_value"},
                        {"line_number": 3, "text": "def answer():"},
                        {"line_number": 4, "text": "    return answer_value()"},
                    ],
                },
            }

            delegate_lookup = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    validation_observation,
                    test_read_observation,
                    answer_symbol_observation,
                    app_read_observation,
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(delegate_lookup.handled)
            self.assertEqual(delegate_lookup.reason, "planned_symbol_search_after_delegate_inspection")
            lookup_result = execute_runtime_tool(
                delegate_lookup.next_payload["intent"],
                dict(delegate_lookup.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-delegate-lookup-symbols"},
            )

            delegate_symbol_observation = {
                "tool_name": "workspace.symbol_search",
                "arguments": dict(delegate_lookup.next_payload["arguments"]),
                "observation": dict(lookup_result.details["observation"]),
            }

            helper_read = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    validation_observation,
                    test_read_observation,
                    answer_symbol_observation,
                    app_read_observation,
                    delegate_symbol_observation,
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(helper_read.handled)
            self.assertEqual(helper_read.reason, "planned_read_after_symbol_search")
            helper_read_result = execute_runtime_tool(
                helper_read.next_payload["intent"],
                dict(helper_read.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-delegate-helper-read"},
            )

            repair = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    validation_observation,
                    test_read_observation,
                    answer_symbol_observation,
                    app_read_observation,
                    delegate_symbol_observation,
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": dict(helper_read.next_payload["arguments"]),
                        "observation": dict(helper_read_result.details["observation"]),
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(repair.handled)
            self.assertEqual(repair.reason, "planned_candidate_repair_after_validation_diagnosis")
            result = execute_runtime_tool(
                repair.next_payload["intent"],
                dict(repair.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-delegate-second-hop-repair"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "helpers.py").read_text(encoding="utf-8"), "def answer_value():\n    return 42\n")

    def test_planned_repair_can_patch_same_file_literal_binding_after_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "ANSWER = 41\n\n\ndef answer():\n    return ANSWER\n",
                encoding="utf-8",
            )
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            validation = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            validation_result = execute_runtime_tool(
                validation.next_payload["intent"],
                dict(validation.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-diagnosis-binding-repair"},
            )

            decision = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(validation.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": {"symbol": "answer", "limit": 10},
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": "answer",
                            "match_count": 2,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "ANSWER = 41"},
                                {"line_number": 3, "text": "def answer():"},
                                {"line_number": 4, "text": "    return ANSWER"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-binding-repair"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual(
                (workspace / "app.py").read_text(encoding="utf-8"),
                "ANSWER = 42\n\n\ndef answer():\n    return ANSWER\n",
            )

    def test_planned_repair_can_patch_imported_literal_binding_after_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "helpers.py").write_text("ANSWER = 41\n", encoding="utf-8")
            (workspace / "app.py").write_text(
                "from helpers import ANSWER\n\n\ndef answer():\n    return ANSWER\n",
                encoding="utf-8",
            )
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            validation = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            validation_result = execute_runtime_tool(
                validation.next_payload["intent"],
                dict(validation.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-imported-binding-repair"},
            )

            decision = plan_tool_workflow(
                user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
                task_class="debugging",
                executed_steps=[
                    {
                        "tool_name": "workspace.run_tests",
                        "arguments": dict(validation.next_payload["arguments"]),
                        "observation": dict(validation_result.details["observation"]),
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "test_app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from app import answer"},
                                {"line_number": 4, "text": "    assert answer() == 42"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.symbol_search",
                        "arguments": {"symbol": "answer", "limit": 10},
                        "observation": {
                            "intent": "workspace.symbol_search",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "symbol": "answer",
                            "match_count": 3,
                            "matches": [
                                {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                                {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                                {"path": "helpers.py", "line": 1, "kind": "reference", "snippet": "ANSWER = 41"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 4,
                            "lines": [
                                {"line_number": 1, "text": "from helpers import ANSWER"},
                                {"line_number": 3, "text": "def answer():"},
                                {"line_number": 4, "text": "    return ANSWER"},
                            ],
                        },
                    },
                    {
                        "tool_name": "workspace.read_file",
                        "arguments": {"path": "helpers.py", "start_line": 1, "max_lines": 60},
                        "observation": {
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "helpers.py",
                            "start_line": 1,
                            "line_count": 1,
                            "lines": [
                                {"line_number": 1, "text": "ANSWER = 41"},
                            ],
                        },
                    },
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-imported-binding-envelope"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertEqual((workspace / "helpers.py").read_text(encoding="utf-8"), "ANSWER = 42\n")

    def test_planned_orchestrated_operator_envelope_can_apply_multi_file_diff_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "from maths import adjust\n\n\ndef answer():\n    return adjust(41)\n",
                encoding="utf-8",
            )
            (workspace / "maths.py").write_text(
                "def adjust(value):\n    return value\n",
                encoding="utf-8",
            )
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            prompt = (
                "tests are failing. apply this patch, then run `python3 -m pytest -q test_app.py`\n"
                "```diff\n"
                "--- a/app.py\n"
                "+++ b/app.py\n"
                "@@ -1,4 +1,4 @@\n"
                "-from maths import adjust\n"
                "+from maths import increment\n"
                " \n"
                " \n"
                " def answer():\n"
                "-    return adjust(41)\n"
                "+    return increment(41)\n"
                "--- a/maths.py\n"
                "+++ b/maths.py\n"
                "@@ -1,2 +1,2 @@\n"
                "-def adjust(value):\n"
                "-    return value\n"
                "+def increment(value):\n"
                "+    return value + 1\n"
                "```\n"
            )

            decision = plan_tool_workflow(
                user_text=prompt,
                task_class=classify(prompt)["task_class"],
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
            subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
            self.assertEqual([item["role"] for item in subtasks], ["verifier", "coder", "verifier"])
            self.assertEqual(subtasks[1]["inputs"]["runtime_tools"][0]["intent"], "workspace.apply_unified_diff")

            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-diff-repair"},
            )

            assert result is not None
            self.assertTrue(result.ok)
            self.assertEqual(result.status, "completed")
            self.assertIn("coder-", result.details["scheduled_children"][1])
            self.assertEqual(
                (workspace / "app.py").read_text(encoding="utf-8"),
                "from maths import increment\n\n\ndef answer():\n    return increment(41)\n",
            )
            self.assertEqual(
                (workspace / "maths.py").read_text(encoding="utf-8"),
                "def increment(value):\n    return value + 1\n",
            )

    def test_planned_repair_rolls_back_workspace_when_final_verifier_stays_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            prompt = (
                "tests are failing. replace `return 41` with `return 40` in app.py, "
                "then run `python3 -m pytest -q test_app.py`"
            )

            decision = plan_tool_workflow(
                user_text=prompt,
                task_class=classify(prompt)["task_class"],
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(decision.handled)
            result = execute_runtime_tool(
                decision.next_payload["intent"],
                dict(decision.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-failed-repair-rollback"},
            )

            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "merge_failed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")
            verifier_result = next(
                item
                for item in result.details["merged_result"]["results"]
                if item["task_id"].startswith("verify-")
            )
            self.assertFalse(verifier_result["ok"])
            rollback = dict(verifier_result["details"]["failure_rollback"] or {})
            self.assertEqual(rollback["intent"], "workspace.rollback_last_change")
            self.assertTrue(rollback["ok"])

    def test_failed_planned_repair_can_continue_diagnosis_after_clean_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            prompt = (
                "tests are failing. replace `return 41` with `return 40` in app.py, "
                "then run `python3 -m pytest -q test_app.py`"
            )

            first = plan_tool_workflow(
                user_text=prompt,
                task_class=classify(prompt)["task_class"],
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(first.handled)
            self.assertEqual(first.next_payload["intent"], "orchestration.execute_envelope")
            result = execute_runtime_tool(
                first.next_payload["intent"],
                dict(first.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-failed-repair-followup"},
            )

            assert result is not None
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "merge_failed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")

            second = plan_tool_workflow(
                user_text=prompt,
                task_class=classify(prompt)["task_class"],
                executed_steps=[
                    {
                        "tool_name": first.next_payload["intent"],
                        "arguments": dict(first.next_payload["arguments"]),
                        "observation": dict(result.details["observation"] or {}),
                        "details": dict(result.details or {}),
                    }
                ],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(second.handled)
            self.assertEqual(second.reason, "planned_inspect_after_envelope_failure")
            self.assertEqual(second.next_payload["intent"], "workspace.read_file")
            self.assertEqual(second.next_payload["arguments"]["path"], "test_app.py")

    def test_failed_planned_repair_can_retry_with_second_envelope_after_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
            (workspace / "test_app.py").write_text(
                "from app import answer\n\n\ndef test_answer():\n    assert answer() == 42\n",
                encoding="utf-8",
            )

            first_prompt = (
                "tests are failing. replace `return 41` with `return 40` in app.py, "
                "then run `python3 -m pytest -q test_app.py`"
            )
            first = plan_tool_workflow(
                user_text=first_prompt,
                task_class=classify(first_prompt)["task_class"],
                executed_steps=[],
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertTrue(first.handled)
            self.assertEqual(first.next_payload["intent"], "orchestration.execute_envelope")
            first_result = execute_runtime_tool(
                first.next_payload["intent"],
                dict(first.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-second-repair-attempt-1"},
            )

            assert first_result is not None
            self.assertFalse(first_result.ok)
            self.assertEqual(first_result.status, "merge_failed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 41\n")

            executed_steps = [
                {
                    "tool_name": first.next_payload["intent"],
                    "arguments": dict(first.next_payload["arguments"]),
                    "observation": dict(first_result.details["observation"] or {}),
                    "details": dict(first_result.details or {}),
                }
            ]

            second = plan_tool_workflow(
                user_text=first_prompt,
                task_class=classify(first_prompt)["task_class"],
                executed_steps=executed_steps,
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            self.assertEqual(second.next_payload["intent"], "workspace.read_file")
            executed_steps.append(
                {
                    "tool_name": second.next_payload["intent"],
                    "arguments": dict(second.next_payload["arguments"]),
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                }
            )

            third = plan_tool_workflow(
                user_text=first_prompt,
                task_class=classify(first_prompt)["task_class"],
                executed_steps=executed_steps,
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            self.assertEqual(third.next_payload["intent"], "workspace.symbol_search")
            executed_steps.append(
                {
                    "tool_name": third.next_payload["intent"],
                    "arguments": dict(third.next_payload["arguments"]),
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                }
            )

            fourth = plan_tool_workflow(
                user_text=first_prompt,
                task_class=classify(first_prompt)["task_class"],
                executed_steps=executed_steps,
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            self.assertEqual(fourth.next_payload["intent"], "workspace.read_file")
            self.assertEqual(fourth.next_payload["arguments"]["path"], "app.py")
            executed_steps.append(
                {
                    "tool_name": fourth.next_payload["intent"],
                    "arguments": dict(fourth.next_payload["arguments"]),
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 2,
                        "lines": [
                            {"line_number": 1, "text": "def answer():"},
                            {"line_number": 2, "text": "    return 41"},
                        ],
                    },
                }
            )

            fifth = plan_tool_workflow(
                user_text=first_prompt,
                task_class=classify(first_prompt)["task_class"],
                executed_steps=executed_steps,
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            self.assertTrue(fifth.handled)
            self.assertEqual(fifth.reason, "planned_candidate_repair_after_validation_diagnosis")
            self.assertEqual(fifth.next_payload["intent"], "orchestration.execute_envelope")

            second_result = execute_runtime_tool(
                fifth.next_payload["intent"],
                dict(fifth.next_payload["arguments"]),
                source_context={"workspace": tmpdir, "session_id": "session-second-repair-attempt-2"},
            )

            assert second_result is not None
            self.assertTrue(second_result.ok)
            self.assertEqual(second_result.status, "completed")
            self.assertEqual((workspace / "app.py").read_text(encoding="utf-8"), "def answer():\n    return 42\n")


if __name__ == "__main__":
    unittest.main()
