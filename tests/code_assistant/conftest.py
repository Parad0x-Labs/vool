"""Shared fixtures for the coding-assistant lane: isolated permission state, an isolated
Blackbox store, a disposable fixture repository, and the source-context shape the boundary
trusts. Mirrors tests/test_blackbox_flight_recorder.py's proven rig."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.code_assistant.fixture import build_fixture_repo
from core.mode_permission_policy import (
    PermissionAction,
    grant_internal_authority,
    reset_mode_permission_state,
    set_active_mode,
)

SESSION = "code-assist-sess"


@pytest.fixture(autouse=True)
def _permission_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


@pytest.fixture(autouse=True)
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "blackbox-store"
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(root))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield root
    store_module.reset_default_store()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return build_fixture_repo(tmp_path / "engagement", name="fixture-repo")


@pytest.fixture
def auto_context(workspace: Path) -> dict:
    set_active_mode(SESSION, "auto")
    return {
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "session_id": SESSION,
        "surface": "api",
        "operating_mode": "auto",
    }


def write_authority(workspace: Path) -> str:
    """The bounded, expiring, workspace-bound scope an operator grants the assistant for edits —
    the same shape any internal caller must hold. Auto mode allows MODIFY without it; tests that
    need deterministic authority (overwrites, rollback) hold it explicitly."""
    return grant_internal_authority(
        label="code-assistant-write",
        actions=[
            PermissionAction.CREATE_FILES,
            PermissionAction.MODIFY_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
        ],
        intents=[
            "workspace.write_file",
            "workspace.replace_in_file",
            "workspace.apply_unified_diff",
            "workspace.ensure_directory",
        ],
        workspace_root=str(workspace.resolve()),
        duration_seconds=300,
    )


def rollback_authority(workspace: Path) -> str:
    return grant_internal_authority(
        label="code-assistant-rollback",
        actions=[
            PermissionAction.DELETE_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
            PermissionAction.MODIFY_FILES,
        ],
        intents=["workspace.rollback_last_change"],
        workspace_root=str(workspace.resolve()),
        duration_seconds=300,
    )


def door(intent: str, arguments: dict, ctx: dict):
    """ONE production door: every call in these packs crosses
    ``core.runtime_execution_tools.execute_runtime_tool`` -- the same door a served model, skill
    or plugin proposal crosses. Nothing here calls the task runtime directly."""
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, f"{intent} is not contracted at the production door"
    return result


def drive_to_approved_proposal(ctx: dict, *, proposal_id: str = "p") -> str:
    """Drive one task through the fixture repo's journey up to an approved proposal:
    open -> reproduce (the narrow test really fails) -> identify -> read the owner ->
    propose (rationale bound) -> approve. Returns (task_id, proposal arguments)."""
    from core.code_assistant.fixture import (
        DEFECT_OLD_TEXT,
        DEFECT_STATS_PY,
        FIX_NEW_TEXT,
        NARROW_TEST_COMMAND,
        OWNER_PATH,
    )

    opened = door("code.task.open", {"objective": "repair the median defect"}, ctx)
    assert opened.ok, opened.response_text
    task_id = opened.details["task_id"]
    repro = door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "repro",
            "intent": "workspace.run_tests",
            "arguments": {"command": NARROW_TEST_COMMAND},
        },
        ctx,
    )
    assert repro.details["executed"] is True and repro.details["tool_result"]["success"] is False
    ident = door(
        "code.task.identify",
        {"task_id": task_id, "path": OWNER_PATH, "line": 6, "reason": "median indexes an unsorted list"},
        ctx,
    )
    assert ident.ok, ident.response_text
    read = door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": OWNER_PATH}},
        ctx,
    )
    assert read.ok, read.response_text
    proposal_arguments = {
        "path": OWNER_PATH,
        "old_text": DEFECT_OLD_TEXT,
        "new_text": FIX_NEW_TEXT,
        "expected_hash": __import__("hashlib").sha256(DEFECT_STATS_PY.encode("utf-8")).hexdigest(),
    }
    proposal = door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": proposal_id,
            "intent": "workspace.replace_in_file",
            "arguments": proposal_arguments,
            "rationale": f"Owner {OWNER_PATH}: median indexes the midpoint without sorting; sort before indexing.",
        },
        ctx,
    )
    assert proposal.ok, proposal.response_text
    approved = door("code.task.approve", {"task_id": task_id, "proposal_id": proposal_id}, ctx)
    assert approved.ok, approved.response_text
    return task_id, proposal_arguments
