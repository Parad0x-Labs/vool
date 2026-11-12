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

The same public route also has to keep three further promises, pinned below: a file on a
mounted external volume nobody shared stays unreadable (an OS-level denial from the kernel
profile, with a read inside the workspace still working), a process the job backgrounds does
not outlive the tool call, and in Manual mode a command that needs approval never starts.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_execution_tools import execute_runtime_tool

_SKIP_REASON = "Kernel-enforced sandboxing (sandbox-exec) is macOS-only; this proof needs a real backend, not a mock."

# The operator's real home, from the account database rather than $HOME, so no fixture can hide it.
# `pwd` exists only on POSIX; elsewhere the class below is skipped and the module must still collect.
if sys.platform == "darwin":
    import pwd

    _REAL_HOME: Path | None = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
else:
    _REAL_HOME = None


def _context(workspace_dir: str) -> dict:
    """Auto mode, explicitly. Without a mode the execution gate defaults to Manual and refuses every
    command for approval before any sandbox runs (core/execution_gate.py), so a denied escape write
    proved nothing about confinement: these tests passed vacuously (Pack 2b review, 2026-10-07)."""
    return {"workspace": workspace_dir, "operating_mode": "auto"}


def _ran(result) -> bool:
    """The command reached the sandbox: the gate did not refuse it for approval."""
    return result is not None and result.status != "user_action_required"


def _writable_external_volume() -> Path | None:
    """A mounted volume under /Volumes this account can write to, or None.

    The boot disk appears there as a symlink to "/" and is skipped: it is not an external
    volume. Nothing machine-specific is named; whatever disk is mounted is used.
    """
    root = Path("/Volumes")
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if os.access(entry, os.W_OK | os.X_OK):
                return entry
        except OSError:
            continue
    return None


@unittest.skipUnless(sys.platform == "darwin", _SKIP_REASON)
class RunTestsFilesystemConfinementTests(unittest.TestCase):
    def setUp(self) -> None:
        # Escape canaries go to Desktop/Documents of a FAKE home under a temp dir, never the
        # operator's: in Auto mode these commands really run, so a confinement regression would
        # otherwise write into the real ~/Desktop.
        fake = tempfile.TemporaryDirectory()
        self.addCleanup(fake.cleanup)
        self.fake_home = Path(fake.name).resolve()
        for name in ("Desktop", "Documents", ".cache", ".config", ".local/share"):
            (self.fake_home / name).mkdir(parents=True, exist_ok=True)
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.fake_home),
            "XDG_CACHE_HOME": str(self.fake_home / ".cache"),
            "XDG_CONFIG_HOME": str(self.fake_home / ".config"),
            "XDG_DATA_HOME": str(self.fake_home / ".local" / "share"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.assertEqual(Path.home().resolve(), self.fake_home)

    def _canary(self, folder: str, name: str) -> Path:
        canary = Path.home() / folder / name
        assert _REAL_HOME is not None  # the class runs only on macOS, where the home was read above
        self.assertFalse(canary.resolve().is_relative_to(_REAL_HOME),
                         f"canary {canary} resolves under the real home; refusing to run an escape probe there")
        self.addCleanup(lambda: canary.unlink(missing_ok=True))
        return canary

    def test_run_tests_denies_a_tmp_escape_write_from_inside_the_generated_test(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside:
            canary = Path(outside) / "vool_run_tests_escape_probe.txt"
            source_context = _context(workspace_dir)

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
            self.assertTrue(_ran(result), result.response_text if result else None)
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
            canary = self._canary("Desktop", "vool_run_lint_escape_probe.txt")
            source_context = _context(workspace_dir)

            result = execute_runtime_tool(
                "workspace.run_lint",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertTrue(_ran(result), result.response_text if result else None)
            self.assertFalse(canary.exists(), "the ~/Desktop escape write must be denied")

    def test_run_formatter_denies_a_documents_escape_write_via_a_substituted_command(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            canary = self._canary("Documents", "vool_run_formatter_escape_probe.txt")
            source_context = _context(workspace_dir)

            result = execute_runtime_tool(
                "workspace.run_formatter",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertTrue(_ran(result), result.response_text if result else None)
            self.assertFalse(canary.exists(), "the ~/Documents escape write must be denied")

    def test_in_workspace_writes_still_succeed_under_all_three_validation_tools(self) -> None:
        """Filesystem confinement holding must not mean the tools stop working -- an in-workspace
        write under the identical relaxed-network policy still succeeds for each of them."""
        with tempfile.TemporaryDirectory() as workspace_dir:
            source_context = _context(workspace_dir)
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
                self.assertTrue(_ran(result), result.response_text if result else None)
                self.assertTrue(target.exists(), f"{intent} should still be able to write inside the workspace")

    def test_sandbox_run_command_control_denies_the_identical_escape(self) -> None:
        """The pre-existing working control, exercised at the same public dispatcher level, for
        direct comparison against the three validation tools above."""
        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as outside:
            canary = Path(outside) / "vool_sandbox_run_command_escape_probe.txt"
            source_context = _context(workspace_dir)

            result = execute_runtime_tool(
                "sandbox.run_command",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('x')\""},
                source_context=source_context,
            )
            self.assertTrue(_ran(result), result.response_text if result else None)
            self.assertFalse(canary.exists())

    def test_validation_tools_cannot_read_an_unshared_external_volume(self) -> None:
        """A file on a mounted external volume that no workspace or read root names stays
        unreadable through all three validation tools. The denial must come from the kernel
        profile ("Operation not permitted"), not from the dispatcher refusing to run, and a
        read inside the workspace through the same tool must still work."""
        volume = _writable_external_volume()
        if volume is None:
            self.skipTest("no writable external volume is mounted under /Volumes")
        with tempfile.TemporaryDirectory(dir=volume) as sentinel_dir, \
                tempfile.TemporaryDirectory() as workspace_dir:
            sentinel = Path(sentinel_dir) / f"vool_volume_read_probe_{os.getpid()}_{time.time_ns()}.txt"
            sentinel.write_text("UNSHARED_EXTERNAL_VOLUME_CONTENTS", encoding="utf-8")
            source_context = _context(workspace_dir)
            for intent in ("workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"):
                result = execute_runtime_tool(
                    intent,
                    {"command": f"{sys.executable} -c \"print(open({str(sentinel)!r}).read())\""},
                    source_context=source_context,
                )
                self.assertTrue(_ran(result), result.response_text if result else None)
                self.assertFalse(result.ok, f"{intent} read a file on an unshared external volume")
                self.assertIn("Operation not permitted", result.response_text,
                              f"{intent} must be denied by the kernel profile, not refused by the dispatcher")
                self.assertNotIn("UNSHARED_EXTERNAL_VOLUME_CONTENTS", result.response_text)

                allowed = Path(workspace_dir) / f"allowed_read_{intent.rsplit('.', 1)[-1]}.txt"
                allowed.write_text("ALLOWED_CONTENT", encoding="utf-8")
                control = execute_runtime_tool(
                    intent,
                    {"command": f"{sys.executable} -c \"assert open({str(allowed)!r}).read() == 'ALLOWED_CONTENT'\""},
                    source_context=source_context,
                )
                self.assertTrue(_ran(control) and control.ok,
                                f"{intent} must still read inside the workspace: "
                                f"{control.response_text if control else None}")

    def test_run_tests_leaves_no_background_descendant_behind(self) -> None:
        """A process the job backgrounds (its streams redirected, so it does not hold the job
        open) is terminated when the tool call returns, through the public tool route."""
        import contextlib
        import signal

        with tempfile.TemporaryDirectory() as workspace_dir:
            pid_file = Path(workspace_dir) / "tool_grandchild.pid"
            (Path(workspace_dir) / "spawn_grandchild.py").write_text(
                "import subprocess, sys\n"
                "p = subprocess.Popen(\n"
                "    [sys.executable, '-c', 'import time; time.sleep(20)'],\n"
                "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n",
                encoding="utf-8",
            )

            def _kill_leftover() -> None:
                if pid_file.exists():
                    with contextlib.suppress(OSError, ValueError):
                        os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)

            self.addCleanup(_kill_leftover)
            result = execute_runtime_tool(
                "workspace.run_tests",
                {"command": f"{sys.executable} spawn_grandchild.py"},
                source_context=_context(workspace_dir),
            )
            self.assertTrue(_ran(result) and result.ok, result.response_text if result else None)
            self.assertTrue(pid_file.exists(), "the job never started its background process")
            grandchild = int(pid_file.read_text().strip())
            # A killed orphan is reaped by launchd a moment later; until then its pid still
            # answers. Two seconds is reaping time only: an untouched grandchild sleeps 20 s,
            # so it is still alive when this window closes and the check fails.
            alive = True
            deadline = time.monotonic() + 2.0
            while alive and time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except OSError:
                    alive = False
                else:
                    time.sleep(0.05)
            self.assertFalse(alive, f"background process {grandchild} outlived the tool call")


class ManualModeLaunchTests(unittest.TestCase):
    """Platform-independent: the refusal happens before any sandbox backend is chosen."""

    def test_manual_mode_never_starts_a_command_that_needs_approval(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_dir:
            canary = Path(workspace_dir) / "should_never_run.txt"
            result = execute_runtime_tool(
                "workspace.run_tests",
                {"command": f"{sys.executable} -c \"open({str(canary)!r}, 'w').write('executed')\""},
                source_context={"workspace": workspace_dir, "operating_mode": "manual"},
            )
            self.assertIsNotNone(result)
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "user_action_required", result.response_text)
            self.assertFalse(canary.exists(), "a command refused for approval must never start")


if __name__ == "__main__":
    unittest.main()
