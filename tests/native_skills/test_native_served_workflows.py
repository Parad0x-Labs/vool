"""Three served workflows: native selection → real tools through the real door.

"Served" means the skill is selected automatically by the loader authority and its declared tools
then EXECUTE through `execute_runtime_tool` — the one runtime tool door every real effect goes
through — against generated project trees on this machine. No mock executor, no private path: if
the door refuses, the workflow fails here exactly as it would in a live turn.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from core.native_skill_library import (
    guidance_for_selection,
    select_native_skills,
)
from core.native_skill_library import (
    native_skills_root as native_skills_root_path,
)
from core.runtime_execution_tools import execute_runtime_tool
from core.task_router import classify


def _git(*args: str, cwd) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)},
    )


@pytest.fixture()
def onboarding_project(tmp_path, monkeypatch):
    project = tmp_path / "orbital-service"
    (project / "src").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "README.md").write_text(
        "# Orbital Service\n\nSchedules orbital window calculations for ground stations.\n",
        encoding="utf-8",
    )
    (project / "src" / "scheduler.py").write_text(
        "def next_window(station: str) -> str:\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (project / "tests" / "test_scheduler.py").write_text(
        "def test_placeholder() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(project))
    return project


def _select(text: str, *, task_class: str = ""):
    """Canonical typed selection: the task class is the production classifier's output for this
    text (or an explicitly given one when the classifier genuinely routes the phrase elsewhere)."""
    resolved = task_class or str(classify(text).get("task_class") or "")
    selection = select_native_skills(task_class=resolved, user_text=text)
    assert selection.selected, (text, resolved, [(r.id, r.state) for r in selection.records])
    text_out, _, _ = guidance_for_selection(selection.selected)
    assert text_out.strip(), "a selected skill must contribute its instructions"
    return selection


def test_served_workflow_repo_onboarding_maps_a_real_project(onboarding_project) -> None:
    """Ask for onboarding → the package is selected automatically → its read tools run for real."""
    selection = _select("audit this project and show me how the files are organised", task_class="workspace_audit")
    gate = next(c for c in selection.selected if c.id == "repo-onboarding")
    from core.native_skill_library import guidance_for_selection as _g
    _text, _, _ = _g(selection.selected)
    assert _text.strip()

    tree = execute_runtime_tool("workspace.list_tree", {"path": ""})
    assert tree is not None and tree.ok is True, tree.response_text if tree else "no dispatch"
    assert "scheduler.py" in tree.response_text

    readme = execute_runtime_tool("workspace.read_file", {"path": "README.md"})
    assert readme is not None and readme.ok is True
    assert "Orbital Service" in readme.response_text


def test_served_workflow_cumulative_testing_runs_the_real_test_pack(tmp_path, monkeypatch) -> None:
    """Ask for the cumulative pack → vool-cumulative-testing selected → workspace.run_tests
    executes a real bounded pytest run over a generated mini project and reports its result."""
    project = tmp_path / "mini-service"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_pack.py").write_text(
        "def test_alpha() -> None:\n    assert 1 + 1 == 2\n\n"
        "def test_beta() -> None:\n    assert 'vool' in 'vool'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(project))

    selection = _select("run the lint and formatter over the tests and attribute the findings", task_class="debugging")
    assert any(c.id == "cumulative-testing" for c in selection.selected), [
        (c.id, c.priority) for c in selection.selected
    ]

    result = execute_runtime_tool(
        "workspace.run_tests",
        {"command": f"{sys.executable} -m pytest -q tests/test_pack.py"},
    )
    assert result is not None, "workspace.run_tests did not dispatch"
    details = result.details or {}
    assert "2 passed" in str(details.get("stdout") or result.response_text), (
        f"the served test run did not really execute: {result.response_text[:300]}"
    )


def test_served_workflow_git_worktrees_lists_real_worktrees(tmp_path, monkeypatch) -> None:
    """Ask for a worktree → vool-git-worktrees selected → the real git state is read through the
    sandboxed command door of a real repository."""
    repo = tmp_path / "worktree-repo"
    repo.mkdir()
    (repo / "app.txt").write_text("payload\n", encoding="utf-8")
    _git("init", "-q", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "seed", cwd=repo)
    _git("worktree", "add", "-b", "experiment-lane", str(tmp_path / "experiment-lane"), cwd=repo)
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(repo))

    selection = _select("set up a git worktree for this experiment", task_class="shell_guidance")
    assert any(c.id == "git-worktrees" for c in selection.selected)

    status = execute_runtime_tool("workspace.git_status", {})
    assert status is not None and status.ok is True, status.response_text if status else "none"

    # A provably read-only git command executes through the sandboxed command door for real.
    read_only = execute_runtime_tool(
        "sandbox.run_command", {"command": "git status --porcelain", "cwd": str(repo)}
    )
    assert read_only is not None, "sandbox.run_command did not dispatch"
    assert read_only.ok is True, read_only.response_text

    # And the mutation the gate cannot prove read-only is refused verbatim — the door fails
    # closed on git verbs it cannot vouch for, exactly as the package's contract states.
    mutation = execute_runtime_tool(
        "sandbox.run_command", {"command": "git worktree add -b x /tmp/x", "cwd": str(repo)}
    )
    assert mutation is not None
    assert "approval" in mutation.response_text.lower(), mutation.response_text


def test_served_effects_stay_inside_the_declared_permission_surface(onboarding_project) -> None:
    """The served onboarding workflow only ever declares read-class effects: what ran must match
    what the package said it would do. This is the no-widening law pinned on a served run."""
    selection = _select("audit this project and show me how the files are organised", task_class="workspace_audit")
    gate = next(c for c in selection.selected if c.id == "repo-onboarding")
    from core.plugin_skills import parse_skill
    from core.runtime_tool_contracts import runtime_tool_contract_map

    record = parse_skill(native_skills_root_path() / "vool-repo-onboarding" / "SKILL.md")
    contracts = runtime_tool_contract_map()
    for tool in gate.permitted_tools:
        classes = str(getattr(contracts[tool], "side_effect_class", ""))
        assert classes in set(record.effects), (
            f"{record.name} declares effects {sorted(record.effects)} but tool "
            f"{tool} carries side-effect class {classes!r}"
        )
