from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.job_runner import JobRunner, KernelIsolationUnavailableError, _macos_confined_profile
from sandbox.resource_limits import ExecutionPolicy


class JobRunnerTests(unittest.TestCase):
    def test_network_command_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            with self.assertRaises(ValueError):
                runner.run(["curl", "https://example.com"])

    def test_workspace_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            escape_dir = "C:\\" if sys.platform == "win32" else "/"
            with self.assertRaises(ValueError):
                runner.run(["python3", "-c", "print('ok')"], cwd=escape_dir)

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_os_enforced_network_isolation_requires_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="os_enforced",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", return_value=None
            ):
                with self.assertRaises(ValueError):
                    runner.run(["python3", "-c", "print('safe')"])

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_auto_network_isolation_now_fails_closed_without_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", return_value=None
            ):
                with self.assertRaises(ValueError):
                    runner.run(["python3", "-c", "print('safe')"])

    def test_heuristic_only_relaxes_network_trust_and_never_confinement(self) -> None:
        """Replaces `test_heuristic_only_mode_remains_explicit_opt_in`.

        That test asserted `_with_network_isolation` returned the argv UNCHANGED in
        `heuristic_only` mode — the "explicit opt-in to the weaker static-guard guarantee".
        Running a job with no profile while every caller and every receipt still says
        "sandboxed" is the defect this lane exists to remove, so the opt-in is gone: the mode
        now relaxes NETWORK trust only, and a host where no backend can confine the filesystem
        refuses the job instead of running it bare.

        Driven with `unshare` faked onto a fake Linux: `unshare -n` expresses network isolation
        and nothing else, so it cannot satisfy "confine writes, permit network" and there is
        no backend left to fall back to.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="heuristic_only",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", return_value="/usr/bin/unshare"
            ):
                with self.assertRaises(KernelIsolationUnavailableError):
                    runner._with_network_isolation(["python3", "-c", "print('x')"])

    def test_network_only_linux_backend_cannot_claim_filesystem_confinement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                return "/usr/bin/unshare" if cmd == "unshare" else None
            # _backend_usable is stubbed because this pins how the prefix is BUILT. The backend paths
            # above are fictional, and since the probe now asks whether a backend actually runs (a
            # present-but-denied `unshare` was reporting every user command as failed in CI), an
            # unstubbed probe would correctly reject them and this test would assert nothing.
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ), patch("sandbox.job_runner._backend_usable", return_value=True):
                with self.assertRaises(KernelIsolationUnavailableError):
                    runner._with_network_isolation(["python3", "-c", "print('x')"])

    def test_macos_sandbox_exec_prefix_applied_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )

            def _which(cmd: str) -> str | None:
                return "/usr/bin/sandbox-exec" if cmd == "sandbox-exec" else None

            # Stubbed for the same reason as the bwrap/unshare cases, and this one was missed at
            # first: it passed on a Mac for the WRONG reason. `sys.platform` is faked to "darwin"
            # but the probe ran the REAL /usr/bin/sandbox-exec, which exists there and succeeds --
            # so the test looked green locally and failed on Linux CI, where that path is absent.
            # A test that pins prefix construction must not depend on the host owning the binary.
            with patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ), patch("sandbox.job_runner._backend_usable", return_value=True):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[0], "/usr/bin/sandbox-exec")
                self.assertEqual(argv[1], "-p")
                self.assertIn("deny network*", argv[2])
                self.assertEqual(argv[3], "--")
                self.assertEqual(argv[4:], ["python3", "-c", "print('x')"])

    def test_linux_declared_reads_are_restored_without_becoming_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            program = root / "program"
            program.mkdir()
            scratch = root / "scratch"
            scratch.mkdir()
            peer = root / "peer"
            peer.mkdir()
            runner = JobRunner(ExecutionPolicy(workspace_root=program, read_roots=(scratch,)))
            with patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None
            ), patch("sandbox.job_runner._backend_usable", return_value=True), patch(
                "sandbox.job_runner._private_read_roots", return_value=(root,)
            ):
                argv = runner._linux_bwrap_prefix([str(program / "run")], ())
            assert argv is not None
            mask_index = next(i for i in range(len(argv) - 1) if argv[i:i + 2] == ["--tmpfs", str(root)])
            for allowed in (program, scratch):
                bind_index = next(i for i in range(len(argv) - 2)
                                  if argv[i:i + 3] == ["--ro-bind", str(allowed), str(allowed)])
                self.assertGreater(bind_index, mask_index)
            self.assertNotIn("--bind", argv)
            self.assertNotIn(str(peer), argv)
            freeze_index = next(i for i in range(len(argv) - 1)
                                if argv[i:i + 2] == ["--remount-ro", str(root)])
            self.assertGreater(freeze_index, bind_index)

    def test_linux_private_parent_freezes_after_explicit_write_grants(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            runner = JobRunner(ExecutionPolicy(workspace_root=workspace))
            with patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", return_value="/usr/bin/bwrap"
            ), patch("sandbox.job_runner._backend_usable", return_value=True), patch(
                "sandbox.job_runner._private_read_roots", return_value=(root,)
            ):
                argv = runner._linux_bwrap_prefix(["true"], (workspace,))
            write_index = argv.index("--bind")
            freeze_index = next(i for i in range(len(argv) - 1)
                                if argv[i:i + 2] == ["--remount-ro", str(root)])
            self.assertGreater(freeze_index, write_index)
            self.assertNotIn(["--remount-ro", str(workspace)],
                             [argv[i:i + 2] for i in range(len(argv) - 1)])

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_macos_sandbox_exec_real_execution_succeeds(self) -> None:
        # Real end-to-end on macOS: a no-network 'auto' job runs under the kernel
        # Seatbelt wrapper without raising, and produces correct output.
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            result = runner.run(["/bin/echo", "sandbox-ok"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("sandbox-ok", result.stdout)

    def test_windows_missing_backend_error_names_heuristic_only_and_wsl2(self) -> None:
        # On native Windows there is no kernel network-isolation backend, so a
        # no-network 'auto' job must fail closed with an ACTIONABLE message that
        # names the two real options: WSL2/Linux and the heuristic_only override.
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            # The narrow platform seam, never the global os.name: pathlib dispatches on os.name
            # at Path() construction, so a global flip poisons every other Path in the process
            # -- including pytest's own failure formatter (run 36063857499 shards 5/9).
            with patch("sandbox.job_runner._is_windows_platform", return_value=True), patch(
                "sandbox.job_runner.sys.platform", "win32"
            ), patch("sandbox.job_runner.shutil.which", return_value=None):
                message = runner._no_kernel_isolation_message()

        self.assertIn("WSL2", message)
        # The message used to offer `heuristic_only` as an informed override. There is no
        # override any more — the only thing one could buy is running with no boundary while
        # still reporting a sandbox — so the message must say so rather than name a way out.
        self.assertIn("no override", message.lower())
        self.assertNotIn("static command guard", message.lower())

    def test_linux_bwrap_prefix_preferred_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            resolved = str(Path(tmpdir).resolve())
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                if cmd == "bwrap":
                    return "/usr/bin/bwrap"
                if cmd == "unshare":
                    return "/usr/bin/unshare"
                return None
            # Stubbed for the same reason as the unshare case above: fictional backend paths, and a
            # real probe would rightly refuse them.
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ), patch("sandbox.job_runner._backend_usable", return_value=True):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                # Network namespace plus filesystem confinement: host mounted
                # read-only, workspace re-bound writable, private /tmp.
                self.assertEqual(argv[0], "/usr/bin/bwrap")
                self.assertIn("--unshare-net", argv)
                self.assertIn("--ro-bind", argv)
                self.assertIn("--tmpfs", argv)
                self.assertIn("--bind", argv)
                self.assertIn(resolved, argv)
                self.assertEqual(argv[-3:], ["python3", "-c", "print('x')"])

    def test_absolute_path_arg_outside_workspace_is_rejected(self) -> None:
        # Regression: the proven exploit was a command reading an absolute path
        # OUTSIDE the workspace (e.g. 'cat /abs/secret'). Pre-fix this ran and
        # returned the secret; it must now be rejected before exec, on any OS.
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("TOP_SECRET")
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(ws)))
            with self.assertRaises(ValueError) as ctx:
                runner.run(["cat", str(secret)])
            self.assertIn("escapes allowed workspace", str(ctx.exception))

    def test_relative_dotdot_path_arg_escape_is_rejected(self) -> None:
        # A relative path that climbs out of the (validated) cwd must also be
        # rejected, not just absolute paths.
        with tempfile.TemporaryDirectory() as ws:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(ws)))
            with self.assertRaises(ValueError) as ctx:
                runner.run(["cat", "../../etc/passwd"])
            self.assertIn("escapes allowed workspace", str(ctx.exception))

    def test_in_workspace_path_arg_is_allowed(self) -> None:
        # The guard must not reject legitimate in-workspace path arguments.
        with tempfile.TemporaryDirectory() as ws:
            inside = Path(ws) / "inside.txt"
            allowed_roots = (Path(ws).resolve(),)
            from sandbox.resource_limits import path_args_within_roots

            self.assertIsNone(
                path_args_within_roots(["cat", str(inside)], allowed_roots, cwd=Path(ws).resolve())
            )
            # A bare relative name and a non-path flag must not be flagged.
            self.assertIsNone(
                path_args_within_roots(["python3", "-c", "print('hi')"], allowed_roots, cwd=Path(ws).resolve())
            )

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_macos_profile_confines_writes_to_workspace(self) -> None:
        # The Seatbelt profile must deny file writes by default and allow them
        # only under the workspace roots (in addition to denying network).
        with tempfile.TemporaryDirectory() as ws:
            ws_resolved = str(Path(ws).resolve())
            profile = _macos_confined_profile((Path(ws).resolve(),))
            self.assertIn("(deny network*)", profile)
            self.assertIn("(deny file-write*)", profile)
            self.assertIn("(allow file-write*", profile)
            self.assertIn(ws_resolved, profile)

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_macos_seatbelt_denies_out_of_workspace_write(self) -> None:
        # End-to-end on macOS: a write to an absolute path OUTSIDE the workspace
        # whose target is hidden inside a code string (so the path-arg guard
        # cannot see it) must be denied by the kernel Seatbelt layer.
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "pwned.txt"
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(ws), network_isolation_mode="auto")
            )
            result = runner.run(
                [sys.executable, "-c", f"open({str(target)!r}, 'w').write('x')"]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())
            # And an in-workspace write under the same profile still succeeds.
            ok = runner.run(
                [sys.executable, "-c", f"open({str(Path(ws) / 'ok.txt')!r}, 'w').write('x'); print('done')"]
            )
            self.assertEqual(ok.returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_macos_seatbelt_still_confines_writes_when_network_is_relaxed(self) -> None:
        """ANVIL, 2026-08-07, CRITICAL: `workspace.run_tests`/`run_lint`/`run_formatter` set
        `network_isolation_mode="heuristic_only"` for trusted-local test/lint commands. Before
        this fix, that mode returned argv UNMODIFIED -- no sandbox-exec wrapper at all -- so
        relaxing NETWORK trust silently dropped FILESYSTEM confinement too. Live-verified: a
        generated pytest file that did `open('/tmp/anvil_runtests_escape_probe.txt', 'w')`
        succeeded with no denial and no approval prompt.

        Identical scenario to `test_macos_seatbelt_denies_out_of_workspace_write` above, with
        `network_isolation_mode="heuristic_only"` instead of `"auto"` -- the escape write must be
        denied exactly the same way. This is the regression lock for the CRITICAL finding.
        """
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "anvil_runtests_escape_probe.txt"
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(ws), network_isolation_mode="heuristic_only")
            )
            result = runner.run(
                [sys.executable, "-c", f"open({str(target)!r}, 'w').write('x')"]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())
            # Filesystem confinement holding does not mean the command can no longer run at all --
            # an in-workspace write under the same relaxed-network policy still succeeds.
            ok = runner.run(
                [sys.executable, "-c", f"open({str(Path(ws) / 'ok.txt')!r}, 'w').write('x'); print('done')"]
            )
            self.assertEqual(ok.returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_heuristic_only_profile_omits_the_network_deny_clause(self) -> None:
        """The whole point of `heuristic_only`: network trust is still relaxed. Only the
        filesystem-write confinement is now unconditional."""
        with tempfile.TemporaryDirectory() as ws:
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(ws), network_isolation_mode="heuristic_only")
            )
            argv = runner._with_network_isolation(["true"])
            self.assertIn("sandbox-exec", argv[0])
            profile = argv[argv.index("-p") + 1]
            self.assertNotIn("(deny network*)", profile)
            self.assertIn("(deny file-write*)", profile)
            self.assertIn("(allow file-write*", profile)


class MutationSabotageJobRunnerTests(unittest.TestCase):
    """Mutation for the F5 CRITICAL fix: reverting `_with_network_isolation`'s `heuristic_only`
    branch to its pre-repair `return argv` turns the new regression lock red."""

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_reverting_heuristic_only_to_skip_the_wrapper_turns_the_escape_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "anvil_runtests_escape_probe.txt"
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(ws), network_isolation_mode="heuristic_only")
            )
            with patch.object(
                JobRunner, "_with_network_isolation", lambda self, argv, allowed_roots=(): argv
            ):
                result = runner.run(
                    [sys.executable, "-c", f"open({str(target)!r}, 'w').write('x')"]
                )
            self.assertEqual(result.returncode, 0, "sabotage did not reproduce the F5 symptom")
            self.assertTrue(
                target.exists(),
                "sabotage should have let the escape write through, unconfined",
            )


# ---------------------------------------------------------------------------------------------
# ANVIL, round 3: `JobRunner.run` sets `PYTHONDONTWRITEBYTECODE=1` on the child's own env copy so
# a sandboxed command never leaves bytecode behind in a tree it only read (a real CI-observed
# regression: `python -m unittest` importing a subject module by file location wrote
# `__pycache__/<stem>.pyc` next to it, mutating a tree an audit asserted byte-identical). The
# behavior was correct but had ZERO tests that would go red if it broke -- this closes that gap
# through the actual subprocess seam, not a mock of it.
# ---------------------------------------------------------------------------------------------


def _import_by_file_location_snippet(path: str) -> str:
    return (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('subject', {path!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
    )


class BytecodeContainmentTests(unittest.TestCase):
    def test_control_this_interpreter_writes_bytecode_without_the_guard(self) -> None:
        """Proves the claim, not just asserts it: THIS Python interpreter, on THIS host, really
        does write `__pycache__` when importing a module by file location and nothing suppresses
        it -- the control the rest of this class's claims depend on."""
        with tempfile.TemporaryDirectory() as tmp:
            subject = Path(tmp) / "subject.py"
            subject.write_text("VALUE = 1\n", encoding="utf-8")
            env = os.environ.copy()
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            completed = subprocess.run(
                [sys.executable, "-c", _import_by_file_location_snippet(str(subject))],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            pycache = Path(tmp) / "__pycache__"
            self.assertTrue(
                pycache.exists() and any(pycache.iterdir()),
                "control failed: this interpreter should write bytecode without the guard -- if "
                "it doesn't, the tests below prove nothing about what the guard is preventing",
            )

    def test_job_runner_execution_creates_no_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subject = Path(tmp) / "subject.py"
            subject.write_text("VALUE = 1\n", encoding="utf-8")
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(tmp), allow_network_egress=True)
            )
            result = runner.run(
                [sys.executable, "-c", _import_by_file_location_snippet(str(subject))], cwd=tmp
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (Path(tmp) / "__pycache__").exists(),
                "JobRunner execution must not leave bytecode behind in a tree it only read",
            )

    def test_removing_the_bytecode_guard_from_child_env_turns_the_test_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            subject = Path(tmp) / "subject.py"
            subject.write_text("VALUE = 1\n", encoding="utf-8")
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(tmp), allow_network_egress=True)
            )
            real_run = subprocess.Popen

            def _run_without_the_guard(argv, **kwargs):
                env = kwargs.get("env")
                if env is not None:
                    env = dict(env)
                    env.pop("PYTHONDONTWRITEBYTECODE", None)
                    kwargs["env"] = env
                return real_run(argv, **kwargs)

            # `Popen`, not `run`: the runner took over its own teardown (its own process
            # group, killed as a group on timeout/cancel) and no longer goes through
            # `subprocess.run`. Same sabotage, at the call it actually makes.
            with patch("sandbox.job_runner.subprocess.Popen", side_effect=_run_without_the_guard):
                result = runner.run(
                    [sys.executable, "-c", _import_by_file_location_snippet(str(subject))],
                    cwd=tmp,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(
                (Path(tmp) / "__pycache__").exists(),
                "sabotage did not reproduce the bytecode-leak symptom",
            )


if __name__ == "__main__":
    unittest.main()
