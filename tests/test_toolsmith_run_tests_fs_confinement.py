"""ANVIL F5, CRITICAL, 2026-08-07: `workspace.run_tests`/`run_lint`/`run_formatter` silently
dropped filesystem confinement.

Confirmed live: `workspace.write_file` created a test, `workspace.run_tests` executed it, the
test wrote `/tmp/anvil_runtests_escape_probe.txt` outside the workspace, and the write SUCCEEDED
with no denial and no approval prompt. The control, `sandbox.run_command` with the identical
write primitive, correctly denied it.

Root cause (see `sandbox/job_runner.py` and its own test file for the unit-level fix and proof):
`_run_validation` unconditionally sets `_trusted_local_only=True` for all three workspace
validation tools, which relaxes network policy to `"heuristic_only"` for a pytest/ruff/unittest
command -- and the OLD `JobRunner._with_network_isolation` skipped its sandbox-exec/bwrap wrapper
ENTIRELY in that mode, dropping filesystem confinement as an unintended side effect of a NETWORK
policy relaxation. `sandbox.run_command` never receives `_trusted_local_only`, so it always got
full isolation, which is why it was the working control.

This file is the end-to-end proof at the PUBLIC tool-intent level (`execute_runtime_tool`), one
disposable workspace at a time, covering all three validation tools plus the sandbox.run_command
comparison ANVIL asked for. The JobRunner-level unit proof and its mutation test live in
`tests/test_job_runner.py`.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from core.runtime_execution_tools import execute_runtime_tool

_SKIP_REASON = "Kernel-enforced sandboxing (sandbox-exec) is macOS-only; this proof needs a real backend, not a mock."


@unittest.skipUnless(sys.platform == "darwin", _SKIP_REASON)
class RunTestsFilesystemConfinementTests(unittest.TestCase):
    def test_run_tests_denies_a_tmp_escape_write_from_inside_the_generated_test(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside:
            canary = Path(outside) / "vool_run_tests_escape_probe.txt"
            source_context = {"workspace": workspace_dir}

            written = execute_runtime_tool(
                "workspace.write_file",
                {
                    "path": "test_escape.py",
                    "content": (
                        "def test_escape():\n"
                        f"    with open({str(canary)!r}, 'w') as fh:\n"
                        "        fh.write('escaped')\n"
                    ),
                },
                source_context=source_context,
            )
            self.assertTrue(written is not None and written.ok, written.response_text if written else None)

            result = execute_runtime_tool("workspace.run_tests", {}, source_context=source_context)
            self.assertIsNotNone(result)
            # The command RUNS (this is not a refusal to execute) -- the escape write inside it
            # is what gets denied, which surfaces as the generated test itself failing.
            self.assertFalse(
                canary.exists(),
                "the escape write must be denied by filesystem confinement, not merely by pytest",
            )

    def test_run_lint_denies_a_desktop_escape_write_via_a_substituted_command(self) -> None:
        """`run_lint`'s default command is `ruff check .`, which offers no code-execution
        primitive of its own -- `arguments["command"]` legitimately overrides it (the same
        override path a real ruff-config-driven invocation would use), which is what exercises
        the SAME `_run_validation` -> `_trusted_local_only=True` -> `_run_command` path with an
        explicit write attempt, matching ANVIL's literal ~/Desktop/outside-probe scenario."""
        with tempfile.TemporaryDirectory() as workspace_dir:
            canary = Path.home() / "Desktop" / "vool_run_lint_escape_probe.txt"
            self.addCleanup(lambda: canary.unlink(missing_ok=True))
            source_context = {"workspace": workspace_dir}

            result = execute_runtime_tool(
                "workspace.run_lint",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertIsNotNone(result)
            self.assertFalse(canary.exists(), "the ~/Desktop escape write must be denied")

    def test_run_formatter_denies_a_documents_escape_write_via_a_substituted_command(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            canary = Path.home() / "Documents" / "vool_run_formatter_escape_probe.txt"
            self.addCleanup(lambda: canary.unlink(missing_ok=True))
            source_context = {"workspace": workspace_dir}

            result = execute_runtime_tool(
                "workspace.run_formatter",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertIsNotNone(result)
            self.assertFalse(canary.exists(), "the ~/Documents escape write must be denied")

    def test_in_workspace_writes_still_succeed_under_all_three_validation_tools(self) -> None:
        """Filesystem confinement holding must not mean the tools stop working -- an in-workspace
        write under the identical relaxed-network policy still succeeds for each of them."""
        with tempfile.TemporaryDirectory() as workspace_dir:
            source_context = {"workspace": workspace_dir}
            for intent, filename in (
                ("workspace.run_tests", "run_tests_ok.txt"),
                ("workspace.run_lint", "run_lint_ok.txt"),
                ("workspace.run_formatter", "run_formatter_ok.txt"),
            ):
                target = Path(workspace_dir) / filename
                result = execute_runtime_tool(
                    intent,
                    {"command": f"{sys.executable} -c \"open({str(target)!r}, 'w').write('ok')\""},
                    source_context=source_context,
                )
                self.assertIsNotNone(result)
                self.assertTrue(target.exists(), f"{intent} should still be able to write inside the workspace")

    def test_sandbox_run_command_control_denies_the_identical_escape(self) -> None:
        """The pre-existing working control, exercised at the same public dispatcher level, for
        direct comparison against the three validation tools above."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside:
            canary = Path(outside) / "vool_sandbox_run_command_escape_probe.txt"
            source_context = {"workspace": workspace_dir}

            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertIsNotNone(result)
            self.assertFalse(canary.exists())


if __name__ == "__main__":
    unittest.main()
