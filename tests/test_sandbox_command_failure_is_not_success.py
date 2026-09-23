"""A shell command that exits nonzero is a failed command, not a successful tool call.

`sandbox.run_command` returned `ok=True, status="executed"` for every exit code. The correct
returncode was sitting in `details` the whole time -- only the two flags anything downstream actually
reads were wrong -- so `cat missing.txt` came back as `mode="tool_executed"` and produced a signed
receipt saying the call succeeded. Nothing between the sandbox and the ledger could tell a working
command from a broken one, which is the exact failure class the tool exists to surface.

These drive the real sandbox rather than a mock: the point is what a genuinely failing command does
end to end, and a mocked runner would assert nothing about that.

Driving the real sandbox means these only mean anything on a host that can actually sandbox. On a
GitHub Actions runner `unshare` is on PATH and the namespace syscall is denied, so every command --
including `cat present.txt` -- came back `ok=False, status='command_failed'`, and this file failed
in CI on every run while passing on every developer machine. That was the runtime mis-reporting an
unusable sandbox as a broken command; it is fixed at the source (the backends are probed for whether
they RUN, not for whether they exist) and the ones below that need real execution now skip with the
reason rather than fail. `test_an_unusable_backend_is_not_reported_as_a_failed_command` pins the fix
itself and runs everywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.runtime_execution_tools import _run_command
from core.runtime_paths import configure_runtime_home
from core.tool_intent_executor import execute_tool_intent
from sandbox import job_runner


def _isolation_probe_runner() -> job_runner.JobRunner:
    policy = job_runner.normalize_policy(
        job_runner.ExecutionPolicy(workspace_root=Path.cwd(), allow_network_egress=False)
    )
    return job_runner.JobRunner(policy)


def _host_can_sandbox() -> bool:
    """Whether this host has a working kernel isolation backend, asked of the runtime itself."""
    runner = _isolation_probe_runner()
    policy = runner.policy
    try:
        runner._with_network_isolation(["true"], (policy.workspace_root,))
    except job_runner.KernelIsolationUnavailableError:
        return False
    return True


needs_working_sandbox = pytest.mark.skipif(
    not _host_can_sandbox(),
    reason=(
        "no usable kernel isolation backend on this host (a backend may be installed and still "
        "denied the namespace syscall) -- these drive the real sandbox, so there is nothing here "
        "to assert. See test_an_unusable_backend_is_not_reported_as_a_failed_command."
    ),
)


@pytest.fixture()
def workspace(tmp_path):
    (tmp_path / "present.txt").write_text("hello\n", encoding="utf-8")
    return tmp_path


def test_an_unusable_backend_is_not_reported_as_a_failed_command(monkeypatch, tmp_path) -> None:
    """A sandbox that cannot isolate says so. It does not blame the user's command.

    This is the CI defect stated as a test, and it runs on every host because it fakes the
    condition rather than needing one. Before the fix, `unshare` being present-but-denied made
    `cat present.txt` -- a command that works -- come back `status='command_failed'`, which is the
    same answer a genuinely broken command gets. An operator on a locked-down host would have been
    told every one of their commands was broken.
    """
    (tmp_path / "present.txt").write_text("hello\n", encoding="utf-8")
    job_runner.reset_isolation_backend_probe_cache()
    # Every backend present, none of them permitted -- exactly the CI runner's shape.
    monkeypatch.setattr(job_runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(job_runner, "_backend_usable", lambda name, probe_argv: False)

    result = _run_command({"command": "cat present.txt"}, workspace_root=tmp_path)

    assert result.ok is False
    assert result.status == "sandbox_unavailable", (
        f"an unusable sandbox reported {result.status!r}; 'command_failed' blames the command"
    )
    assert "isolation" in result.response_text.lower()
    job_runner.reset_isolation_backend_probe_cache()


def test_a_backend_on_path_but_denied_is_not_chosen(monkeypatch) -> None:
    """Presence was the bug. `shutil.which` finding a backend is not evidence it can isolate.

    Asserts the rule rather than one platform's backend: whichever backend this host would reach
    for, it has to be probed before it is trusted. Naming `unshare` here would pass on Linux and
    vacuously skip the check on the macOS lane, where `_linux_*_prefix` returns early on platform.
    """
    job_runner.reset_isolation_backend_probe_cache()
    # Every backend "installed", so only the probe can rule any of them out.
    monkeypatch.setattr(job_runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    probed: list[str] = []

    def _probe(name, probe_argv):
        probed.append(name)
        return False

    monkeypatch.setattr(job_runner, "_backend_usable", _probe)
    runner = _isolation_probe_runner()

    with pytest.raises(job_runner.KernelIsolationUnavailableError):
        runner._with_network_isolation(["true"], (runner.policy.workspace_root,))

    assert probed, "a backend on PATH was accepted without ever being probed for whether it works"
    expected = "sandbox-exec" if sys.platform == "darwin" else "bwrap:deny_network=True"
    assert expected in probed, f"this host's backend was not probed; probed={probed}"
    job_runner.reset_isolation_backend_probe_cache()


@needs_working_sandbox
def test_successful_command_is_reported_as_success(workspace) -> None:
    result = _run_command({"command": "cat present.txt"}, workspace_root=workspace)

    assert result.ok is True
    assert result.status == "executed"
    assert (result.details or {}).get("returncode") == 0
    assert "hello" in result.response_text


@needs_working_sandbox
@pytest.mark.parametrize(
    ("command", "why"),
    [
        ("cat missing.txt", "the file does not exist"),
        ("grep zzz-no-such-pattern present.txt", "grep exits 1 when nothing matches"),
        ("ls missing_directory", "the directory does not exist"),
    ],
)
def test_failing_command_is_not_reported_as_success(workspace, command: str, why: str) -> None:
    result = _run_command({"command": command}, workspace_root=workspace)

    assert result.handled is True, "the sandbox did run it -- the call was handled"
    assert result.ok is False, why
    assert result.status == "command_failed"
    assert int((result.details or {}).get("returncode") or 0) != 0


@needs_working_sandbox
def test_the_model_still_receives_the_exit_code_and_streams(workspace) -> None:
    # Flipping ok must not cost the model the information it needs to react. A bare "it failed" would
    # trade one wrong answer for another.
    result = _run_command({"command": "cat missing.txt"}, workspace_root=workspace)

    assert "Exit code: 1" in result.response_text
    assert "cat missing.txt" in result.response_text
    assert "Stderr" in result.response_text


@needs_working_sandbox
def test_failure_reaches_the_turn_as_tool_failed(tmp_path, monkeypatch) -> None:
    # The flags only matter because of what reads them: mode drives the task event
    # ("tool.failed" -> repairing) and the receipt records ok.
    configure_runtime_home(tmp_path / "runtime-home")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "present.txt").write_text("hello\n", encoding="utf-8")
    monkeypatch.chdir(workspace)
    try:
        good = execute_tool_intent(
            {"intent": "sandbox.run_command", "arguments": {"command": "cat present.txt"}},
            task_id="t-ok",
            session_id="s-ok",
            source_context={},
            hive_activity_tracker=None,
        )
        bad = execute_tool_intent(
            {"intent": "sandbox.run_command", "arguments": {"command": "cat missing.txt"}},
            task_id="t-bad",
            session_id="s-bad",
            source_context={},
            hive_activity_tracker=None,
        )
    finally:
        configure_runtime_home(None)

    assert (good.ok, good.mode) == (True, "tool_executed")
    assert (bad.ok, bad.mode) == (False, "tool_failed")


@needs_working_sandbox
def test_a_failing_command_still_produces_a_command_artifact(workspace) -> None:
    result = _run_command({"command": "cat missing.txt"}, workspace_root=workspace)

    artifacts = (result.details or {}).get("artifacts") or []
    assert artifacts, "the transcript of what ran is kept regardless of outcome"
    assert any(str(item.get("status") or "") == "command_failed" for item in artifacts if isinstance(item, dict))
