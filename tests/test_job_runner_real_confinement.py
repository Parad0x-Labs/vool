"""Executable tools must never run unwrapped while reporting themselves sandboxed.

The defect, confirmed at `a6c8e3c4`: `sandbox/sandbox_runner.py` builds a REPLACEMENT
`JobRunner` with `allow_network_egress=True` whenever the base command is one of
`pip pip3 npm npx yarn pnpm cargo`, and `JobRunner._with_network_isolation` opens with

    if self.policy.allow_network_egress:
        return argv

so the argv comes back with no Seatbelt profile and no bwrap — the FIRST statement of the
only function that ever applies confinement. The method is named for network isolation and
is in fact the sole filesystem boundary, so trusting the network trusted everything.

Install subcommands are refused earlier by the network guard. What actually runs unwrapped
is the non-network half, which is the half that executes other people's code:
`cargo build` (build.rs, proc macros), `npm run <script>` (package.json scripts),
`pip uninstall`. The static argv scan that remains reads argument STRINGS and cannot see
what a process does once it is running.

Everything here is a REAL drive on a real kernel boundary. No package is ever installed and
no credential is ever touched: `npm` and `cargo` are two-line shell stubs on PATH, secrets
are sentinel files in a temporary directory, and the network probe is a loopback socket this
test opened itself.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import pytest

from core.execution_gate import ExecutionGate
from sandbox.job_runner import JobRunner
from sandbox.resource_limits import ExecutionPolicy
from sandbox.sandbox_runner import SandboxRunner

_MACOS_ONLY = "Kernel-enforced confinement here is macOS Seatbelt; a mock would prove nothing."

PY = sys.executable or "python3"


@pytest.fixture(autouse=True)
def disposable_home(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def _approved_command_context(workspace: Path) -> dict:
    """Select Auto through the controller before freezing the command's policy."""
    from core.mode_permission_policy import set_active_mode

    session = "confinement:" + str(workspace)
    set_active_mode(session, "auto", workspace_root=str(workspace))
    return {"workspace": str(workspace), "runtime_session_id": session}


def _stub(directory: Path, name: str, body: str) -> None:
    """A harmless executable standing in for a package tool. Never installs anything."""
    path = directory / name
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _escape_py(target: Path) -> str:
    return f'{PY} -c "open({str(target)!r}, \'w\').write(\'escaped\')"'


class _Bench:
    """A workspace, an outside directory holding sentinels, and a stub tool dir on PATH."""

    def __init__(self, stack: list):
        self.workspace = Path(tempfile.mkdtemp(prefix="conf_ws_")).resolve()
        self.outside = Path(tempfile.mkdtemp(prefix="conf_out_")).resolve()
        self.tools = Path(tempfile.mkdtemp(prefix="conf_bin_")).resolve()
        self.canary = self.outside / "sentinel.txt"
        self.canary.write_text("ORIGINAL", encoding="utf-8")
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.tools}{os.pathsep}{self._old_path}"
        stack.append(self.close)

    def close(self) -> None:
        os.environ["PATH"] = self._old_path

    def runner(self) -> SandboxRunner:
        return SandboxRunner(ExecutionGate(), str(self.workspace))

    def run(self, command: str) -> dict:
        """Drive one command with an effect ledger open.

        Without a ledger `execution_gate.evaluate_command` raises PolicyUnavailableError and
        `run_command` returns `blocked_by_policy` having executed NOTHING — which made the
        first version of these tests pass at the defective base for a reason that had nothing
        to do with confinement. The scope is the sanctioned non-turn path and is exactly what
        a real background caller opens.
        """
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(
            "test.confinement_drive", source_context=_approved_command_context(self.workspace)
        ):
            result = self.runner().run_command(command)
        assert result.get("status") in {"executed", "blocked_by_hard_link"}, result
        return result


@unittest.skipUnless(sys.platform == "darwin", _MACOS_ONLY)
class PackageToolConfinementTests(unittest.TestCase):
    """Group A — the confirmed defect, driven through the public door."""

    def setUp(self) -> None:
        self._cleanup: list = []
        self.bench = _Bench(self._cleanup)

    def tearDown(self) -> None:
        for fn in reversed(self._cleanup):
            fn()

    def _assert_not_executed_unconfined(self, result: dict, label: str) -> None:
        self.assertEqual(
            self.bench.canary.read_text(encoding="utf-8"), "ORIGINAL",
            f"{label} wrote outside the workspace: {result}",
        )

    def test_an_npm_run_script_cannot_write_outside_the_workspace(self) -> None:
        """A package.json script is arbitrary code. `npm run build` is not a network command,
        so the network guard lets it through to the branch that drops confinement."""
        _stub(self.bench.tools, "npm", f"""
            {_escape_py(self.bench.canary)}
            echo "npm script done"
        """)
        result = self.bench.run("npm run build")
        self._assert_not_executed_unconfined(result, "npm run build")

    def test_a_cargo_build_script_cannot_write_outside_the_workspace(self) -> None:
        """`cargo build` compiles and RUNS build.rs and proc macros on the host."""
        _stub(self.bench.tools, "cargo", f"""
            {_escape_py(self.bench.canary)}
            echo "build.rs done"
        """)
        result = self.bench.run("cargo build")
        self._assert_not_executed_unconfined(result, "cargo build")

    def test_a_pip_uninstall_cannot_write_outside_the_workspace(self) -> None:
        _stub(self.bench.tools, "pip", f"""
            {_escape_py(self.bench.canary)}
            echo "uninstalled"
        """)
        result = self.bench.run("pip uninstall -y somepkg")
        self._assert_not_executed_unconfined(result, "pip uninstall")

    def test_a_file_in_the_configured_home_cannot_be_read(self) -> None:
        """Exercise the home rule with a real sentinel under the disposable test home."""
        sentinel = Path.home() / "vool_confinement_probe_sentinel.txt"
        sentinel.write_text("SENTINEL-NOT-A-KEY", encoding="utf-8")
        try:
            _stub(self.bench.tools, "npm", f"""
                {PY} -c "print(open({str(sentinel)!r}).read())"
            """)
            result = self.bench.run("npm run build")
        finally:
            sentinel.unlink(missing_ok=True)
        self.assertNotIn(
            "SENTINEL-NOT-A-KEY", str(result.get("stdout") or ""),
            f"a job read a file out of the operator's home: {result}",
        )
        self.assertNotEqual(result.get("returncode"), 0, result)

    def test_a_package_tool_cannot_read_a_credential_sentinel(self) -> None:
        """Reads matter as much as writes: a build script exfiltrating is the npm supply-chain
        attack this repo's own operator was hit by. The sentinel is a temp file, never a key."""
        secret_home = Path(tempfile.mkdtemp(prefix="conf_home_")).resolve()
        (secret_home / ".ssh").mkdir()
        sentinel = secret_home / ".ssh" / "id_sentinel"
        sentinel.write_text("SENTINEL-NOT-A-KEY", encoding="utf-8")
        leak = self.bench.workspace / "leaked.txt"
        _stub(self.bench.tools, "npm", f"""
            {PY} -c "open({str(leak)!r},'w').write(open({str(sentinel)!r}).read())" || true
            echo done
        """)
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(secret_home)
        try:
            self.bench.run("npm run build")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
        if leak.exists():
            self.assertNotIn(
                "SENTINEL-NOT-A-KEY", leak.read_text(encoding="utf-8"),
                "a package tool read a credential path from inside the sandbox",
            )

    def test_a_package_tool_cannot_reach_the_network_without_an_explicit_grant(self) -> None:
        """Loopback only — this test opened the socket itself. Egress must be a separate,
        explicit decision, not a side effect of being a package command."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        self._cleanup.append(listener.close)
        proof = self.bench.workspace / "connected.txt"
        _stub(self.bench.tools, "npm", f"""
            {PY} -c "import socket;s=socket.create_connection(('127.0.0.1',{port}),2);open({str(proof)!r},'w').write('connected')" || true
            echo done
        """)
        self.bench.run("npm run build")
        self.assertFalse(
            proof.exists(),
            "a package command reached the network with no explicit egress grant",
        )


@unittest.skipUnless(sys.platform == "darwin", _MACOS_ONLY)
class JobRunnerBoundaryTests(unittest.TestCase):
    """Group B — the unit-level contract on the one execution authority."""

    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="conf_jr_")).resolve()
        self.outside = Path(tempfile.mkdtemp(prefix="conf_jro_")).resolve()
        self.canary = self.outside / "sentinel.txt"
        self.canary.write_text("ORIGINAL", encoding="utf-8")

    def _policy(self, **over) -> ExecutionPolicy:
        base = dict(
            workspace_root=self.workspace,
            writable_roots=(self.workspace,),
            max_seconds=20,
            max_output_kb=64,
        )
        base.update(over)
        return ExecutionPolicy(**base)

    def test_network_trust_never_returns_an_unwrapped_argv(self) -> None:
        """The exact early return. `allow_network_egress=True` must still be confined."""
        runner = JobRunner(self._policy(allow_network_egress=True))
        argv = [PY, "-c", "pass"]
        wrapped = runner._with_network_isolation(list(argv), (self.workspace,))
        self.assertNotEqual(
            wrapped, argv,
            "the runner returned the argv unwrapped — no kernel profile was applied",
        )
        self.assertIn("sandbox-exec", " ".join(wrapped))

    def test_an_interpreter_cannot_write_outside_the_roots_with_network_trusted(self) -> None:
        runner = JobRunner(self._policy(allow_network_egress=True))
        runner.run([PY, "-c", f"open({str(self.canary)!r},'w').write('escaped')"], cwd=self.workspace)
        self.assertEqual(self.canary.read_text(encoding="utf-8"), "ORIGINAL")

    def test_a_shell_wrapper_cannot_write_outside_the_roots(self) -> None:
        runner = JobRunner(self._policy(allow_network_egress=True))
        runner.run(["/bin/sh", "-c", f"echo escaped > {self.canary}"], cwd=self.workspace)
        self.assertEqual(self.canary.read_text(encoding="utf-8"), "ORIGINAL")

    def test_a_symlink_out_of_the_workspace_cannot_be_written_through(self) -> None:
        link = self.workspace / "bridge"
        os.symlink(self.outside, link, target_is_directory=True)
        runner = JobRunner(self._policy(allow_network_egress=True))
        runner.run(
            [PY, "-c", f"open({str(link / 'sentinel.txt')!r},'w').write('escaped')"],
            cwd=self.workspace,
        )
        self.assertEqual(self.canary.read_text(encoding="utf-8"), "ORIGINAL")

    def test_a_hard_link_into_the_workspace_is_refused_before_the_job_starts(self) -> None:
        """Path-based confinement cannot see a hard link — `workspace/linked.txt` and the file
        outside are one inode with two names, and the kernel correctly allows a write to the
        path it was asked about. Measured on macOS 26.5.1: the write lands, and `(deny
        file-link)` does not change it (that rule governs CREATING links, and a confined job
        already cannot create one). So the link is caught before the job runs, from `st_nlink`."""
        from sandbox.job_runner import HardLinkedWritableRootError

        linked = self.workspace / "linked.txt"
        os.link(self.canary, linked)
        runner = JobRunner(self._policy(allow_network_egress=True))
        with self.assertRaises(HardLinkedWritableRootError):
            runner.run(
                [PY, "-c", f"open({str(linked)!r},'a').write('escaped')"],
                cwd=self.workspace,
            )
        self.assertEqual(
            self.canary.read_text(encoding="utf-8"), "ORIGINAL",
            "a hard link let a write reach the file outside the workspace",
        )

    def test_a_confined_job_cannot_create_a_hard_link_of_its_own(self) -> None:
        """The other half, and this one IS kernel-enforced: a link inside a writable root can
        only have been put there by something already running unconfined."""
        runner = JobRunner(self._policy(allow_network_egress=True))
        target = self.workspace / "made.txt"
        result = runner.run(
            [PY, "-c", f"import os;os.link({str(self.canary)!r}, {str(target)!r})"],
            cwd=self.workspace,
        )
        self.assertNotEqual(result.returncode, 0, "the job created a hard link out of its root")
        self.assertFalse(target.exists())

    def test_the_environment_is_sanitized_and_home_points_inside_the_task_root(self) -> None:
        """A child inheriting the operator's HOME, SSH_AUTH_SOCK and tokens is a child that
        can find everything worth stealing without ever naming a path in its argv."""
        os.environ["VOOL_CONF_PROBE_TOKEN"] = "super-secret-sentinel"
        os.environ["SSH_AUTH_SOCK"] = "/tmp/vool-conf-fake-agent.sock"
        try:
            runner = JobRunner(self._policy(allow_network_egress=True))
            out = self.workspace / "env.txt"
            result = runner.run(
                [PY, "-c",
                 f"import json,os;open({str(out)!r},'w').write(json.dumps(dict(os.environ)))"],
                cwd=self.workspace,
            )
            self.assertTrue(out.exists(), f"probe never ran: {result.stderr}")
            import json

            child_env = json.loads(out.read_text(encoding="utf-8"))
        finally:
            os.environ.pop("VOOL_CONF_PROBE_TOKEN", None)
            os.environ.pop("SSH_AUTH_SOCK", None)

        self.assertNotIn("VOOL_CONF_PROBE_TOKEN", child_env, "an operator secret reached the child")
        self.assertNotIn("SSH_AUTH_SOCK", child_env, "the ssh agent socket reached the child")
        home = child_env.get("HOME", "")
        self.assertTrue(home, "the child had no HOME at all")
        self.assertTrue(
            Path(home).resolve() == self.workspace or self.workspace in Path(home).resolve().parents,
            f"HOME points outside the task root: {home}",
        )
        for key in ("TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            value = child_env.get(key, "")
            self.assertTrue(value, f"{key} was not redirected into the task root")
            self.assertTrue(
                self.workspace in Path(value).resolve().parents or Path(value).resolve() == self.workspace,
                f"{key} points outside the task root: {value}",
            )

    def test_a_grandchild_does_not_survive_the_timeout(self) -> None:
        """`subprocess.run(timeout=…)` kills the direct child only. A build script that
        backgrounds a daemon leaves it running against a job the operator believes is over."""
        marker = self.workspace / "grandchild.pid"
        runner = JobRunner(self._policy(max_seconds=2, allow_network_egress=True))
        script = (
            "import os,subprocess,sys,time\n"
            f"p = subprocess.Popen([{PY!r}, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(marker)!r},'w').write(str(p.pid))\n"
            "time.sleep(120)\n"
        )
        runner.run([PY, "-c", script], cwd=self.workspace)
        self.assertTrue(marker.exists(), "the probe never started its grandchild")
        pid = int(marker.read_text(encoding="utf-8").strip())
        deadline = time.time() + 5
        alive = True
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
                break
            time.sleep(0.1)
        if alive:
            with contextlib_suppress():
                os.kill(pid, 9)
        self.assertFalse(alive, f"grandchild {pid} outlived the job's timeout")


def contextlib_suppress():
    import contextlib

    return contextlib.suppress(Exception)


@unittest.skipUnless(sys.platform == "darwin", _MACOS_ONLY)
class SandboxUnavailableTests(unittest.TestCase):
    """Group C — fail closed. Never run raw while claiming sandboxed."""

    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="conf_una_")).resolve()

    def test_a_host_with_no_backend_refuses_instead_of_running_raw(self) -> None:
        from sandbox import job_runner as jr

        policy = ExecutionPolicy(
            workspace_root=self.workspace,
            writable_roots=(self.workspace,),
            allow_network_egress=True,
        )
        runner = JobRunner(policy)
        original = jr.JobRunner._macos_sandbox_exec_prefix
        jr.JobRunner._macos_sandbox_exec_prefix = lambda self, argv, roots, **kw: None
        try:
            with self.assertRaises(jr.KernelIsolationUnavailableError):
                runner._with_network_isolation([PY, "-c", "pass"], (self.workspace,))
        finally:
            jr.JobRunner._macos_sandbox_exec_prefix = original

    def test_the_public_door_reports_sandbox_unavailable_not_a_command_failure(self) -> None:
        from sandbox import job_runner as jr

        workspace = Path(tempfile.mkdtemp(prefix="conf_una2_")).resolve()
        tools = Path(tempfile.mkdtemp(prefix="conf_una_bin_")).resolve()
        _stub(tools, "npm", "echo ran\n")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{tools}{os.pathsep}{old_path}"
        original = jr.JobRunner._macos_sandbox_exec_prefix
        jr.JobRunner._macos_sandbox_exec_prefix = lambda self, argv, roots, **kw: None
        try:
            from core.effect_gateway import named_background_effect_scope

            with named_background_effect_scope(
                "test.confinement_drive", source_context=_approved_command_context(workspace)
            ):
                result = SandboxRunner(ExecutionGate(), str(workspace)).run_command("npm run build")
        finally:
            jr.JobRunner._macos_sandbox_exec_prefix = original
            os.environ["PATH"] = old_path
        self.assertEqual(
            result.get("status"), "sandbox_unavailable",
            f"an unconfinable host must refuse by name, not run or report a command failure: {result}",
        )


class NonProcessToolsUnchangedTests(unittest.TestCase):
    """Group D — invariant 11. Typed non-process tools keep their behaviour exactly."""

    def test_a_typed_workspace_write_is_untouched_by_this_lane(self) -> None:
        from core.runtime_execution_tools import execute_runtime_tool

        with tempfile.TemporaryDirectory() as workspace:
            execution = execute_runtime_tool(
                "workspace.write_file",
                {"path": "notes.txt", "content": "ordinary\n"},
                source_context={"workspace": workspace, "runtime_session_id": "chat-1"},
            )
            self.assertTrue(execution is not None and execution.ok, execution)
            self.assertEqual(
                (Path(workspace) / "notes.txt").read_text(encoding="utf-8"), "ordinary\n"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


@unittest.skipUnless(sys.platform == "darwin", _MACOS_ONLY)
class TerminalOutcomeTests(unittest.TestCase):
    """Group E — invariant 10. Five outcomes, never collapsed into 'it failed'."""

    def setUp(self) -> None:
        self._cleanup: list = []
        self.bench = _Bench(self._cleanup)

    def tearDown(self) -> None:
        for fn in reversed(self._cleanup):
            fn()

    def test_a_command_that_exits_non_zero_is_executed_not_refused(self) -> None:
        _stub(self.bench.tools, "npm", "exit 3\n")
        result = self.bench.run("npm run build")
        self.assertEqual(result.get("status"), "executed", result)
        self.assertEqual(result.get("returncode"), 3, result)
        self.assertFalse(result.get("success"))

    def test_a_timeout_says_timed_out(self) -> None:
        # `node`, not `npm`: a package tool is rebuilt with its own longer clock inside
        # `run_command`, so shortening the shared runner's policy would not reach it.
        import dataclasses

        runner = self.bench.runner()
        runner.job_runner = JobRunner(
            dataclasses.replace(runner.job_runner.policy, max_seconds=2)
        )
        _stub(self.bench.tools, "node", "sleep 30\n")
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(
            "test.confinement_drive", source_context=_approved_command_context(self.bench.workspace)
        ):
            result = runner.run_command("node server.js")
        self.assertEqual(result.get("status"), "timed_out", result)
        self.assertEqual(result.get("returncode"), 124, result)

    def test_a_cancelled_job_says_cancelled_and_leaves_nothing_running(self) -> None:
        import threading

        marker = self.bench.workspace / "grandchild.pid"
        cancel = threading.Event()
        script = (
            "import subprocess,sys,time\n"
            f"p = subprocess.Popen([{PY!r}, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(marker)!r},'w').write(str(p.pid))\n"
            "time.sleep(120)\n"
        )
        _stub(self.bench.tools, "npm", f"{PY} -c '{script}'\n" if False else "")
        (self.bench.tools / "npm").write_text(
            "#!/bin/sh\nexec " + PY + " " + str(self.bench.workspace / "probe.py") + "\n",
            encoding="utf-8",
        )
        (self.bench.tools / "npm").chmod(0o755)
        (self.bench.workspace / "probe.py").write_text(script, encoding="utf-8")

        from core.effect_gateway import named_background_effect_scope

        runner = self.bench.runner()
        threading.Timer(1.5, cancel.set).start()
        with named_background_effect_scope(
            "test.confinement_drive", source_context=_approved_command_context(self.bench.workspace)
        ):
            result = runner.run_command("npm run build", cancel_event=cancel)

        self.assertEqual(result.get("status"), "cancelled", result)
        self.assertTrue(marker.exists(), "the probe never started its grandchild")
        pid = int(marker.read_text(encoding="utf-8").strip())
        deadline = time.time() + 5
        alive = True
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
                break
            time.sleep(0.1)
        if alive:
            with contextlib_suppress():
                os.kill(pid, 9)
        self.assertFalse(alive, f"grandchild {pid} survived cancellation")

    def test_a_hard_link_in_the_writable_root_has_its_own_status(self) -> None:
        os.link(self.bench.canary, self.bench.workspace / "linked.txt")
        _stub(self.bench.tools, "npm", "echo ran\n")
        result = self.bench.run("npm run build")
        self.assertEqual(result.get("status"), "blocked_by_hard_link", result)
        self.assertEqual(self.bench.canary.read_text(encoding="utf-8"), "ORIGINAL")
