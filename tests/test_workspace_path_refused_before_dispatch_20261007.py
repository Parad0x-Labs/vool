"""A workspace write to a path the handler refuses is a typed refusal, never an unknown outcome.

Measured live 2026-10-06 (served daemon, pinned paid model): "Write a tiny HTML page and a matching
CSS file ... then zip them together" led the model to `workspace.write_file` on a file inside the
running code. The protection check refused it ("nothing was touched"), but the refusal was raised
past the dispatch claim, so the A6 boundary recorded "Outcome unknown -- reconciliation required",
the effect obligation stayed open, finalization refused the turn and the request ended in HTTP 500.
"""
from __future__ import annotations

import pytest

from core import project_store, runtime_paths
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode
from core.tool_intent_executor import execute_tool_intent


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    folder = tmp_path / "ws"
    folder.mkdir()
    # The runtime home lives INSIDE the workspace, so `home/data/...` is protected runtime state
    # reachable by a workspace-relative path -- the same shape as a workspace inside the code root.
    monkeypatch.setenv("VOOL_HOME", str(folder / "home"))
    runtime_paths.configure_runtime_home(folder / "home")
    reset_mode_permission_state()
    yield folder
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _write(folder, intent: str, path: str):
    ok, project = project_store.create_project("ws", str(folder))
    assert ok
    session = "openclaw:workspace-path-refusal"
    set_active_mode(session, "auto", project_id=str(project["id"]), client_turn_id="turn-a")
    context = {
        "runtime_session_id": session,
        "operating_mode": "auto",
        "workspace_root": str(project["root"]),
        "workspace": str(project["root"]),
        "project_id": str(project["id"]),
        "cancel_turn_id": "turn-a",
        "surface": "openclaw",
        "platform": "openclaw",
    }
    arguments = {"path": path, "content": "<p>coming soon</p>\n"} if intent == "workspace.write_file" else {"path": path}
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    return execute_tool_intent(
        {"intent": intent, "arguments": arguments},
        task_id="task-a",
        session_id=session,
        source_context=context,
        hive_activity_tracker=tracker,
        checkpoint_id="runtime-checkpoint-1",
        step_index=0,
    )


@pytest.mark.parametrize("intent", ["workspace.write_file", "workspace.ensure_directory"])
@pytest.mark.parametrize("path", ["home/data/coming-soon.html", "../outside/coming-soon.html"])
def test_a_refused_path_is_a_typed_refusal_with_nothing_written(workspace, intent, path):
    result = _write(workspace, intent, path)
    assert result.mode == "tool_failed", (result.mode, result.status, result.response_text)
    assert result.status == "workspace_path_refused"
    assert "unknown" not in result.response_text.lower()
    assert "Nothing was written" in result.response_text
    assert not (workspace / path).exists()
    assert result.details.get("executed") is False


def test_an_allowed_path_still_writes(workspace):
    result = _write(workspace, "workspace.write_file", "site/coming-soon.html")
    assert result.ok, (result.mode, result.status, result.response_text)
    assert (workspace / "site" / "coming-soon.html").read_text() == "<p>coming soon</p>\n"
