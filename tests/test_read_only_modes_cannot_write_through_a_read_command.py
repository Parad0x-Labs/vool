"""A command that looks like a read may not write a file or start a program in a read-only mode.

Measured on 9adff83 through the real executor and the real sandbox (bwrap): in Manual, Review
edits and Plan -- the modes that promise to ask, or to change nothing -- each of these ran with no
prompt and changed the workspace, because the classifier matched the base command name only:

    sed --in-place s/a/b/ f      rewrote f            (only a literal `-i...` token was caught)
    sed -Ei s/a/b/ f             rewrote f            (a flag cluster carrying i)
    sed -n '1e touch X' f        ran `touch X`        (GNU sed `e` runs a shell)
    rg --pre sh -l x run.sh      executed run.sh      (`--pre` runs a program on every file)
    git diff --output=X          wrote X
    git grep -O'touch X' add     ran `touch X`        (opens matches in a pager it starts)
    find . -fprintf X %p         wrote X              (-fprint0/-fprintf missing from the list)

Plan mode is advertised as read-only, so `rg --pre sh` there was arbitrary code execution from a
mode that denies every write tool.

The cases are driven end to end where a sandbox backend exists, and the classifier verdict is
pinned on every host, so the property holds on CI runners without bwrap too.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from core import runtime_paths
from core.execution_gate import ExecutionGate
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    PermissionAction,
    command_is_read_only,
    reset_mode_permission_state,
    set_active_mode,
)

# (command, file it must not create, or "@file:marker" text it must not write into file)
WRITING_READS = [
    ("sed --in-place s/add/MARK_A/ app.py", "@app.py:MARK_A"),
    ("sed -Ei s/add/MARK_B/ app.py", "@app.py:MARK_B"),
    ("sed -n '1e touch MARK_C' app.py", "MARK_C"),
    ("sed -n 's/add/x/w MARK_D' app.py", "MARK_D"),
    ("sed 's/add/touch MARK_E/e' app.py", "MARK_E"),
    ("rg --pre sh -l touch run.sh", "MARK_F"),
    ("git diff --output=MARK_G", "MARK_G"),
    ("git diff --outp=MARK_H", "MARK_H"),
    ("git grep -O'touch MARK_I' add", "MARK_I"),
    ("find . -maxdepth 1 -fprintf MARK_J %p", "MARK_J"),
    ("find . -maxdepth 1 -fprint0 MARK_K", "MARK_K"),
]

# Reads a coder runs all day; tightening must not turn these into prompts.
GENUINE_READS = [
    "sed -n 1,20p app.py",
    "sed -n '/def /p' app.py",
    "sed 's/add/sum/g' app.py",
    "sed -e 1p -e 2p app.py",
    "sed -n '3,+2p' app.py",
    "sed '/^#/d' app.py",
    "sed 10q app.py",
    "rg -n add",
    "git diff --stat",
    "git log --oneline -5",
    "git grep -n add",
    "git blame app.py",
    "find . -name '*.py'",
]


@pytest.mark.parametrize(("command", "_effect"), WRITING_READS)
def test_a_writing_read_is_not_classified_read_only(command: str, _effect: str) -> None:
    assert not command_is_read_only(command), command


@pytest.mark.parametrize("command", GENUINE_READS)
def test_a_genuine_read_still_runs_unprompted(command: str) -> None:
    assert command_is_read_only(command), command


@pytest.mark.parametrize(("command", "_effect"), WRITING_READS)
def test_the_sandbox_gate_agrees_with_the_classifier(command: str, _effect: str) -> None:
    import shlex

    argv = shlex.split(command)
    base = ExecutionGate._base_command(argv)
    assert not ExecutionGate._is_read_only_command(base, argv), command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm notes.txt", {PermissionAction.DELETE_FILES}),
        ("mv a.py b.py", {PermissionAction.MODIFY_FILES, PermissionAction.DELETE_FILES}),
        ("git rm a.py", {PermissionAction.DELETE_FILES}),
        ("git branch -D feature", {PermissionAction.GIT_RESET_CLEAN}),
        ("find . -delete", {PermissionAction.DELETE_FILES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("python3 -m pip install requests", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
        ("npx create-thing", {PermissionAction.INSTALL_DEPENDENCIES, PermissionAction.RUN_SIDE_EFFECTING_COMMANDS}),
    ],
)
def test_a_deleting_or_installing_command_names_what_it_does(command: str, expected: set) -> None:
    """Auto prompts for deletes and installs; a shell spelling must not dodge the prompt."""
    from core.mode_permission_policy import _command_actions

    assert _command_actions(command) == expected


# --------------------------------------------------------------------------------------
# End to end: the real executor, the real sandbox, a real git workspace.
# --------------------------------------------------------------------------------------


def _bwrap_usable() -> bool:
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return False
    probe = subprocess.run(
        [bwrap, "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"],
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (ws / "run.sh").write_text("touch MARK_F\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=ws,
        check=True,
    )
    yield ws
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _landed(ws: Path, effect: str) -> bool:
    if effect.startswith("@"):
        name, marker = effect[1:].split(":")
        return marker in (ws / name).read_text(encoding="utf-8")
    return (ws / effect).exists()


@pytest.mark.skipif(not _bwrap_usable(), reason="no usable sandbox backend on this host")
@pytest.mark.parametrize("mode", ["manual", "review_edits", "plan"])
@pytest.mark.parametrize(("command", "effect"), WRITING_READS)
def test_no_read_only_mode_lets_a_writing_read_change_the_workspace(workspace, mode, command, effect) -> None:
    from core.tool_intent_executor import execute_tool_intent

    session, turn = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    set_active_mode(session, mode, client_turn_id=turn)
    execution = execute_tool_intent(
        {"intent": "sandbox.run_command", "arguments": {"command": command}},
        task_id="task-read-only-mode",
        session_id=session,
        source_context={
            "runtime_session_id": session,
            "operating_mode": mode,
            "workspace_root": str(workspace),
            "workspace": str(workspace),
            "cancel_turn_id": turn,
        },
        hive_activity_tracker=HiveActivityTracker(
            config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)
        ),
    )
    expected = "blocked_by_mode" if mode == "plan" else "pending_approval"
    assert execution.status == expected, (command, execution.status, execution.response_text)
    assert not _landed(workspace, effect), f"{mode}: `{command}` changed the workspace unasked"
