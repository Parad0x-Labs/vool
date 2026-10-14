"""A turn that ordered a downloadable file in a named format is not answered by a disk write of another.

Measured live 2026-10-06 (pinned gpt-4.1-mini): "Write a shell script that backs up my Documents folder
into a dated folder, plus a short usage note, and zip both files." -- the model called
machine.write_file for backup_documents.sh (refused by the machine lanes), no zip was built and the
turn ended "I wasn't able to turn that into a completed action". The refusal is now a correction that
names file.deliver and the ordered format.
"""
from __future__ import annotations

import pytest

from core import project_store, runtime_paths
from core.deliverables import REQUESTED_FORMATS_KEY
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode
from core.tool_intent_executor import execute_tool_intent


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    reset_mode_permission_state()
    folder = tmp_path / "ws"
    folder.mkdir()
    yield folder
    reset_mode_permission_state()
    runtime_paths.configure_runtime_home(None)


def _call(folder, intent: str, path: str, wanted):
    ok, project = project_store.create_project("ws", str(folder))
    assert ok
    session = "openclaw:ordered-file-writer"
    set_active_mode(session, "auto", project_id=str(project["id"]), client_turn_id="turn-a")
    context = {
        "runtime_session_id": session, "operating_mode": "auto", "workspace_root": str(project["root"]),
        "workspace": str(project["root"]), "project_id": str(project["id"]), "cancel_turn_id": "turn-a",
        "surface": "openclaw", "platform": "openclaw",
    }
    if wanted is not None:
        context[REQUESTED_FORMATS_KEY] = list(wanted)
    tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
    return execute_tool_intent(
        {"intent": intent, "arguments": {"path": path, "content": "#!/bin/sh\necho backup\n"}},
        task_id="task-a", session_id=session, source_context=context, hive_activity_tracker=tracker,
        checkpoint_id="runtime-checkpoint-1", step_index=0,
    )


@pytest.mark.parametrize("intent", ["machine.write_file", "workspace.write_file"])
def test_a_disk_write_of_another_format_is_a_correction_toward_file_deliver(workspace, intent):
    result = _call(workspace, intent, "backup_documents.sh", ("zip",))
    assert result.mode == "tool_failed" and result.status == "format_not_requested", (result.mode, result.status)
    assert "file.deliver" in result.response_text and ".zip" in result.response_text
    assert "Nothing was written" in result.response_text
    assert not (workspace / "backup_documents.sh").exists()


def test_a_disk_write_in_the_ordered_format_still_writes(workspace):
    result = _call(workspace, "workspace.write_file", "notes/report.md", ("md",))
    assert result.ok, (result.mode, result.status, result.response_text)
    assert (workspace / "notes" / "report.md").exists()


@pytest.mark.parametrize("wanted", [None, ()])
def test_a_turn_that_ordered_no_format_is_unchanged(workspace, wanted):
    result = _call(workspace, "workspace.write_file", "scripts/backup.sh", wanted)
    assert result.ok, (result.mode, result.status, result.response_text)
    assert (workspace / "scripts" / "backup.sh").exists()
