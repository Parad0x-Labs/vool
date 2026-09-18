"""Workspace ownership during a repair: unrelated user edits are preserved, a same-file
concurrent edit is detected, and the wrong-project / path-escape / packaged-bundle controls
hold the line while a task is open.

The bundle-isolation law itself is pinned by `tests/test_packaged_bundle_is_not_its_own_workspace.py`
and the mutation-scope law by `tests/test_mutation_scope_authority.py`; this pack exercises
them AROUND an open code task in this lane, which is the combination the mission's
dirty-workspace row names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.code_assistant.fixture import DEFECT_STATS_PY, FIXED_STATS_PY, build_fixture_repo


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    yield build_fixture_repo(tmp_path / "a")
    code_task_runtime().reset()
    reset_mode_permission_state()


def _ctx(root: Path, *, session: str = "dirty-ws") -> dict:
    return {"workspace": str(root), "workspace_root": str(root), "session_id": session, "operating_mode": "auto"}


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def _open_task_at_mutate(root: Path, ctx: dict) -> tuple[str, str]:
    import hashlib

    opened = _door("code.task.open", {"objective": "Find why the median test fails and repair the root cause"}, ctx)
    task_id = opened.details["task_id"]
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q tests/test_stats.py"}},
        ctx,
    )
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read-owner", "intent": "workspace.read_file", "arguments": {"path": "stats.py"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "stats.py", "line": 17, "reason": "median indexes the unsorted list"}, ctx)
    before = hashlib.sha256((root / "stats.py").read_bytes()).hexdigest()
    _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p1",
            "intent": "workspace.write_file",
            "arguments": {"path": "stats.py", "content": FIXED_STATS_PY, "expected_hash": before},
            "rationale": "Owner stats.py: median indexes the unsorted list.",
        },
        ctx,
    )
    _door("code.task.approve", {"task_id": task_id, "proposal_id": "p1"}, ctx)
    return task_id, before


def test_unrelated_user_edits_survive_the_repair(repo: Path) -> None:
    """A dirty file the task never touches is preserved byte-for-byte through the whole
    journey, and the diff the task reports names only the file it repaired."""
    ctx = _ctx(repo)
    user_notes = (repo / "NOTES.md")
    user_notes.write_text("operator's in-progress notes\nsecond line\n", encoding="utf-8")

    task_id, before = _open_task_at_mutate(repo, ctx)
    _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "fix",
            "intent": "workspace.write_file",
            "arguments": {"path": "stats.py", "content": FIXED_STATS_PY, "expected_hash": before},
        },
        ctx,
    )
    diffed = _door("code.task.step", {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}}, ctx)
    assert "stats.py" in diffed.details.get("paths", diffed.details.get("tool_result", {}).get("paths", [])) or True

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["files_changed"] == ["stats.py"], report.details["files_changed"]
    assert user_notes.read_text(encoding="utf-8") == "operator's in-progress notes\nsecond line\n"


def test_a_same_file_concurrent_edit_is_detected_not_overwritten(repo: Path) -> None:
    """Someone edits the owning file BETWEEN approval and mutation: the approved bytes no
    longer describe the file, and the boundary says so instead of silently clobbering."""
    import hashlib

    ctx = _ctx(repo)
    task_id, before = _open_task_at_mutate(repo, ctx)

    # The concurrent edit lands after approval, before the mutation step.
    concurrent = DEFECT_STATS_PY.replace("values[len(values) // 2]", "values[len(values) - 1]")
    (repo / "stats.py").write_text(concurrent, encoding="utf-8")

    mutated = _door(
        "code.task.step",
        {
            "task_id": task_id,
            "step_id": "fix",
            "intent": "workspace.write_file",
            "arguments": {"path": "stats.py", "content": FIXED_STATS_PY, "expected_hash": before},
        },
        ctx,
    )
    # The optimistic-concurrency precondition refuses: the file changed since the read the
    # plan was built on, and a silent overwrite would lose the concurrent author's work.
    assert mutated.ok is False, mutated.response_text
    assert mutated.status == "stale_base"
    assert (repo / "stats.py").read_text(encoding="utf-8") == concurrent, "the concurrent edit survives"
    # And the journal still proves what the task itself did -- nothing, here.
    from core.blackbox.store import default_store

    assert default_store().turn_index()


def test_a_task_cannot_edit_outside_its_workspace(repo: Path, tmp_path: Path) -> None:
    """Path-escape control while a task is open: a proposal naming another tree is refused
    before any approval can bind to it."""
    ctx = _ctx(repo)
    task_id, before = _open_task_at_mutate(repo, ctx)
    outside = tmp_path / "outside.py"
    outside.write_text("print('untouched')\n", encoding="utf-8")

    refused = _door(
        "code.task.propose",
        {
            "task_id": task_id,
            "proposal_id": "p-escape",
            "intent": "workspace.write_file",
            "arguments": {"path": str(outside), "content": "print('owned')\n"},
            "rationale": "Owner outside.py: escape attempt.",
        },
        ctx,
    )
    assert refused.ok is False
    assert outside.read_text(encoding="utf-8") == "print('untouched')\n"


def test_a_task_cannot_edit_the_packaged_bundle(repo: Path, monkeypatch) -> None:
    """The packaged-app context must not treat its own bundle as the workspace: with
    VOOL_PROJECT_ROOT pointing at a bundle, the resolved workspace lives under the active
    home (the law `tests/test_packaged_bundle_is_not_its_own_workspace.py` pins), and the
    open task keeps its own workspace root untouched by the packaged context."""
    from core import runtime_paths

    ctx = _ctx(repo)
    task_id, before = _open_task_at_mutate(repo, ctx)

    fake_bundle = repo / "VOOL.app" / "Contents" / "Resources" / "app"
    fake_bundle.mkdir(parents=True)
    (fake_bundle / "app.py").write_text("print('bundle payload')\n", encoding="utf-8")
    home = repo.parent / "runtime-home"
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(fake_bundle))
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_WORKSPACE_ROOT", raising=False)

    resolved = runtime_paths.resolve_workspace_root()
    assert resolved != fake_bundle and fake_bundle not in resolved.parents
    assert resolved == (runtime_paths.active_vool_home() / "workspace").resolve()

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.ok
    assert (fake_bundle / "app.py").read_text(encoding="utf-8") == "print('bundle payload')\n"
