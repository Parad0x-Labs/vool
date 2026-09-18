"""Manual mode never asks an operator to approve a sandbox command the runtime will refuse.

Measured live 2026-09-07: `sandbox.run_command` with `cwd=/Users` produced an approval prompt
("Affected: /Users"), the operator approved, and the resumed call was refused as an unknown
outcome. The working-directory decision now runs before the permission decision: an outside
`cwd` is a typed refusal with no approval request, and an inside `cwd` still asks, exactly as
Manual mode must. The command carries a pipe on purpose: Manual mode allows a bounded plain
command and asks only for an "exact action" with shell operators -- the shape the live turn had.
"""
from __future__ import annotations

import pytest

from core import project_store, runtime_paths
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode
from core.tool_intent_executor import execute_tool_intent


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _run(tmp_path, cwd: str):
    folder = tmp_path / "repo"
    folder.mkdir(exist_ok=True)
    ok, project = project_store.create_project("repo", str(folder))
    assert ok
    session = "openclaw:sandbox-cwd"
    set_active_mode(session, "manual", project_id=str(project["id"]), client_turn_id="turn-a")
    context = {
        "runtime_session_id": session,
        "operating_mode": "manual",
        "workspace_root": str(project["root"]),
        "workspace": str(project["root"]),
        "project_id": str(project["id"]),
        "cancel_turn_id": "turn-a",
        "surface": "openclaw",
        "platform": "openclaw",
    }
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    return execute_tool_intent(
        {"intent": "sandbox.run_command", "arguments": {"command": "ls -laS 2>/dev/null | head -20", "cwd": cwd}},
        task_id="task-a",
        session_id=session,
        source_context=context,
        hive_activity_tracker=tracker,
        checkpoint_id="runtime-checkpoint-1",
        step_index=0,
    )


def test_an_outside_cwd_is_refused_with_no_approval_request(tmp_path) -> None:
    outside = _run(tmp_path, "/Users")
    assert outside.mode == "tool_failed" and outside.status == "cwd_outside_workspace", (outside.mode, outside.status)
    assert not outside.details.get("approval_request"), outside.details
    assert "/Users" in outside.response_text and "Nothing was run" in outside.response_text
    assert "unknown" not in outside.response_text.lower()


def test_an_inside_cwd_still_asks_for_approval_in_manual_mode(tmp_path) -> None:
    inside = _run(tmp_path, ".")
    assert inside.mode == "tool_preview" and inside.status == "pending_approval", (inside.mode, inside.status)
    assert inside.details.get("approval_request"), inside.details
