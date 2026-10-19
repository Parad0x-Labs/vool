"""A command the operator allowed runs; a command nobody allowed still does not.

Measured on main 48dfe06 through the real executor, the real permission controller, the real
ExecutionGate and the real bwrap sandbox, with the per-turn autonomy the chat page sends for Auto and
Bypass (`autonomy: 'auto'`):

    Auto / Bypass   python3 -m pytest, mkdir, python -c, npm, workspace.run_tests
                    -> "Command requires explicit user approval." with no card to press
    Manual          every command the user approved -> refused again the same way
    Auto            git commit / git stash after Allow -> refused again

The sandbox gate re-derived approval from the mode matrix and its own rules and could not see the
controller's decision, and under `auto` autonomy it asked for anything that was not a read. The
authorized execution boundary now publishes the ALLOW it acts on for that one dispatch, and the gate
honours it only for the exact command it covers.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from core import runtime_paths
from core.execution_gate import (
    ExecutionGate,
    reset_request_autonomy_override,
    set_request_autonomy_override,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    OperatingMode,
    PermissionAction,
    PermissionDecision,
    PermissionEffect,
    activate_bypass_grant,
    current_command_authorization,
    publish_command_authorization,
    request_bypass_confirmation,
    reset_mode_permission_state,
    resolve_approval,
    set_active_mode,
    withdraw_command_authorization,
)


def _sandbox_usable() -> bool:
    if sys.platform.startswith("linux"):
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return False
        probe = subprocess.run(
            [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
            capture_output=True, check=False,
        )
        return probe.returncode == 0
    if sys.platform == "darwin":
        sandbox_exec = shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec"
        probe = subprocess.run([sandbox_exec, "-p", "(version 1)(allow default)", "--", "true"],
                               capture_output=True, check=False)
        return probe.returncode == 0
    return False


SANDBOX = pytest.mark.skipif(not _sandbox_usable(), reason="no usable sandbox backend on this host")
PYTHON = shutil.which("python3") or sys.executable
PYTEST_AVAILABLE = subprocess.run([PYTHON, "-c", "import pytest"], capture_output=True, check=False).returncode == 0


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (ws / "test_app.py").write_text("from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    (ws / "notes.txt").write_text("notes\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"],
                 ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=ws, check=True)
    (ws / "app.py").write_text("def add(a, b):\n    return a + b\n# edited\n", encoding="utf-8")
    yield ws
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


class _Turn:
    """One chat turn's identity and mode, dispatched through the real executor."""

    def __init__(self, workspace: Path, mode: str) -> None:
        self.workspace = workspace
        self.mode = mode
        self.session = f"s-{uuid.uuid4().hex[:8]}"
        self.turn = f"t-{uuid.uuid4().hex[:8]}"
        self.context = {
            "runtime_session_id": self.session,
            "workspace_root": str(workspace),
            "workspace": str(workspace),
            "cancel_turn_id": self.turn,
            "operating_mode": mode,
        }
        if mode == "bypass_permissions":
            confirmation = request_bypass_confirmation(session_id=self.session, task_id=self.turn, scope="task")
            grant = activate_bypass_grant(session_id=self.session, task_id=self.turn, scope="task",
                                          confirmation_id=confirmation)
            set_active_mode(self.session, mode, client_turn_id=self.turn, bypass_token=grant["token"])
            self.context["bypass_token"] = grant["token"]
        else:
            set_active_mode(self.session, mode, client_turn_id=self.turn)

    def run(self, intent: str, arguments: dict, *, approval_token: str = ""):
        from core.tool_intent_executor import execute_tool_intent

        context = dict(self.context)
        if approval_token:
            context["mode_approval_token"] = approval_token
        return execute_tool_intent(
            {"intent": intent, "arguments": dict(arguments)},
            task_id="task-run-commands",
            session_id=self.session,
            source_context=context,
            hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
        )

    def command(self, command: str, **kwargs):
        return self.run("sandbox.run_command", {"command": command}, **kwargs)

    def approve(self, execution) -> str:
        request = dict((execution.details or {}).get("approval_request") or {})
        token = str(request.get("token") or request.get("approval_id") or "")
        assert token, execution.details
        resolve_approval(token, decision="allow")
        return token


@pytest.fixture
def chat_autonomy():
    """What the chat page sends for Auto and Bypass: `autonomy: 'auto'` on the turn."""
    token = set_request_autonomy_override("auto")
    yield
    reset_request_autonomy_override(token)


def _git_log(ws: Path) -> str:
    return subprocess.run(["git", "log", "--oneline", "-1"], cwd=ws, capture_output=True, text=True, check=True).stdout


# ------------------------------------------------------------------------------------------------
# Runs: Auto, Bypass, and Manual after Allow
# ------------------------------------------------------------------------------------------------


@SANDBOX
@pytest.mark.skipif(not PYTEST_AVAILABLE, reason="python3 on PATH has no pytest")
def test_auto_runs_the_test_suite_and_routine_commands(workspace, chat_autonomy) -> None:
    turn = _Turn(workspace, "auto")
    tests = turn.command("python3 -m pytest -q")
    assert tests.status == "executed" and tests.details["returncode"] == 0, tests.response_text
    made = turn.command("mkdir build")
    assert made.status == "executed" and (workspace / "build").is_dir(), made.response_text
    validation = turn.run("workspace.run_tests", {})
    assert validation.status == "executed", validation.response_text


@SANDBOX
@pytest.mark.skipif(not PYTEST_AVAILABLE, reason="python3 on PATH has no pytest")
def test_bypass_runs_tests_and_git_without_a_prompt(workspace, chat_autonomy) -> None:
    turn = _Turn(workspace, "bypass_permissions")
    assert turn.command("python3 -m pytest -q").status == "executed"
    assert turn.command("git add -A").status == "executed"
    committed = turn.command("git commit -qm from-bypass")
    assert committed.status == "executed", committed.response_text
    assert "from-bypass" in _git_log(workspace)


@SANDBOX
@pytest.mark.skipif(not PYTEST_AVAILABLE, reason="python3 on PATH has no pytest")
def test_manual_asks_first_and_runs_the_exact_command_after_allow(workspace) -> None:
    turn = _Turn(workspace, "manual")
    first = turn.command("python3 -m pytest -q")
    assert first.status == "pending_approval"
    assert not (workspace / ".pytest_cache").exists()
    token = turn.approve(first)
    second = turn.command("python3 -m pytest -q", approval_token=token)
    assert second.status == "executed" and second.details["returncode"] == 0, second.response_text


@SANDBOX
def test_auto_asks_before_a_commit_and_commits_after_allow(workspace, chat_autonomy) -> None:
    turn = _Turn(workspace, "auto")
    first = turn.command("git commit -qam wip")
    assert first.status == "pending_approval"
    assert "wip" not in _git_log(workspace)
    token = turn.approve(first)
    second = turn.command("git commit -qam wip", approval_token=token)
    assert second.status == "executed", second.response_text
    assert "wip" in _git_log(workspace)


@SANDBOX
def test_an_approval_binds_its_own_command_and_no_other(workspace) -> None:
    turn = _Turn(workspace, "manual")
    token = turn.approve(turn.command("mkdir approved"))
    other = turn.command("mkdir other", approval_token=token)
    assert other.status == "pending_approval"
    assert not (workspace / "other").exists() and not (workspace / "approved").exists()


# ------------------------------------------------------------------------------------------------
# Still refused
# ------------------------------------------------------------------------------------------------


@SANDBOX
def test_plan_blocks_every_command_that_is_not_a_read(workspace) -> None:
    turn = _Turn(workspace, "plan")
    for command in ("python3 -m pytest -q", "mkdir build", "git add -A"):
        assert turn.command(command).status == "blocked_by_mode", command
    assert not (workspace / "build").exists()


@SANDBOX
@pytest.mark.parametrize("mode", ["manual", "auto", "bypass_permissions"])
def test_rm_rf_is_refused_in_every_mode(workspace, chat_autonomy, mode) -> None:
    (workspace / "build").mkdir()
    turn = _Turn(workspace, mode)
    execution = turn.command("rm -rf build")
    if execution.status == "pending_approval":
        execution = turn.command("rm -rf build", approval_token=turn.approve(execution))
    assert execution.status == "blocked_by_policy", (execution.status, execution.response_text)
    assert (workspace / "build").is_dir()


@SANDBOX
def test_git_push_stays_offline_even_after_allow(workspace, chat_autonomy) -> None:
    turn = _Turn(workspace, "auto")
    first = turn.command("git push origin main")
    assert first.status == "pending_approval"
    second = turn.command("git push origin main", approval_token=turn.approve(first))
    assert second.status == "blocked_by_policy" and "Network egress" in second.response_text, second.response_text


@SANDBOX
def test_a_run_tests_call_carrying_another_command_is_not_covered(workspace, chat_autonomy) -> None:
    """The controller allowed `run_side_effecting_commands`; a commit is not that, so the gate decides
    it on its own and refuses it."""
    turn = _Turn(workspace, "auto")
    execution = turn.run("workspace.run_tests", {"command": "git commit -qam sneaky"})
    assert execution.status not in {"executed", "command_failed"}, execution.response_text
    assert "sneaky" not in _git_log(workspace)


# ------------------------------------------------------------------------------------------------
# The authorization itself: one dispatch, one command, no leaks
# ------------------------------------------------------------------------------------------------


def _allow(actions, *, granted_by: str = "", mode=OperatingMode.MANUAL) -> PermissionDecision:
    return PermissionDecision(effect=PermissionEffect.ALLOW, mode=mode, actions=tuple(actions), reason="t",
                              granted_by=granted_by)


def test_only_an_allow_with_a_command_is_published() -> None:
    side = [PermissionAction.RUN_SIDE_EFFECTING_COMMANDS]
    deny = PermissionDecision(effect=PermissionEffect.DENY, mode=OperatingMode.AUTO, actions=tuple(side), reason="t")
    assert publish_command_authorization("sandbox.run_command", deny, ["mkdir x"]) is None
    assert publish_command_authorization("sandbox.run_command", None, ["mkdir x"]) is None
    assert publish_command_authorization("workspace.read_file", _allow(side), []) is None
    assert current_command_authorization() is None


@pytest.mark.parametrize(
    "variant",
    ["mkdir  build", "env mkdir build", "command mkdir build", "mkdir build other", "mkdir build && ls", "touch build"],
)
def test_an_authorization_covers_only_the_exact_command_text(variant: str) -> None:
    token = publish_command_authorization(
        "sandbox.run_command", _allow([PermissionAction.RUN_SIDE_EFFECTING_COMMANDS]), ["mkdir build"]
    )
    try:
        authorization = current_command_authorization()
        assert authorization is not None and authorization.covers("mkdir build")
        assert authorization.covers("  mkdir build ")
        assert not authorization.covers(variant)
    finally:
        withdraw_command_authorization(token)
    assert current_command_authorization() is None


def test_an_authorization_never_covers_an_action_it_did_not_allow() -> None:
    token = publish_command_authorization(
        "workspace.run_tests", _allow([PermissionAction.RUN_SIDE_EFFECTING_COMMANDS]), ["git push", "rm notes.txt"]
    )
    try:
        authorization = current_command_authorization()
        assert not authorization.covers("git push")
        assert not authorization.covers("rm notes.txt")
    finally:
        withdraw_command_authorization(token)


def test_a_handler_that_raises_still_withdraws_the_authorization(monkeypatch, tmp_path) -> None:
    from core import authorized_tool_execution, runtime_execution_tools

    seen = {}

    def boom(*_args, **_kwargs):
        seen["during"] = current_command_authorization()
        raise RuntimeError("handler failed")

    monkeypatch.setattr(runtime_execution_tools, "execute_runtime_tool", boom)
    with pytest.raises(RuntimeError):
        authorized_tool_execution.execute_authorized_runtime_tool(
            "sandbox.run_command", {"command": "mkdir x"}, source_context={"workspace_root": str(tmp_path)},
            permission_decision=_allow([PermissionAction.RUN_SIDE_EFFECTING_COMMANDS], mode=OperatingMode.AUTO),
        )
    assert seen["during"] is not None and seen["during"].covers("mkdir x")
    assert current_command_authorization() is None


def test_two_threads_dispatching_at_once_see_only_their_own_authorization() -> None:
    barrier = threading.Barrier(2)
    results: dict[str, tuple[bool, bool]] = {}

    def dispatch(name: str, command: str, other: str) -> None:
        token = publish_command_authorization(
            "sandbox.run_command", _allow([PermissionAction.RUN_SIDE_EFFECTING_COMMANDS]), [command]
        )
        try:
            barrier.wait(timeout=10)
            authorization = current_command_authorization()
            results[name] = (authorization.covers(command), authorization.covers(other))
            barrier.wait(timeout=10)
        finally:
            withdraw_command_authorization(token)

    threads = [threading.Thread(target=dispatch, args=("a", "mkdir a", "mkdir b")),
               threading.Thread(target=dispatch, args=("b", "mkdir b", "mkdir a"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert results == {"a": (True, False), "b": (True, False)}


def test_a_direct_caller_with_no_dispatch_keeps_todays_gate(tmp_path, chat_autonomy) -> None:
    from core.effect_gateway import named_background_effect_scope

    with named_background_effect_scope("test.direct_gate", source_context={"workspace_root": str(tmp_path)}):
        verdict = ExecutionGate.evaluate_command("find x -delete", cwd=str(tmp_path))
    assert verdict["decision"] == "advice_only", verdict


def test_an_authorization_from_another_mode_is_not_honoured(tmp_path) -> None:
    from core.effect_gateway import named_background_effect_scope

    token = publish_command_authorization(
        "sandbox.run_command",
        _allow([PermissionAction.RUN_SIDE_EFFECTING_COMMANDS, PermissionAction.GIT_COMMIT], mode=OperatingMode.BYPASS_PERMISSIONS),
        ["git commit -qm x"],
    )
    try:
        with named_background_effect_scope("test.direct_gate", source_context={"workspace_root": str(tmp_path)}):
            verdict = ExecutionGate.evaluate_command("git commit -qm x", cwd=str(tmp_path))
    finally:
        withdraw_command_authorization(token)
    assert verdict["decision"] != "sandbox", verdict
