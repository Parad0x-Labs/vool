"""The multi-question boundary: a turn that names two problems fixes only the authorized one,
and two authorized dependent edits accumulate their checks after each.

The control plane is per-task and per-session; authority comes from the operator's objective
and the task journal, never from "the model noticed a second bug". This pack pins both
directions: the refusal to widen scope, and the ability to do dependent work lawfully.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.code_assistant.fixture import DEFECT_STATS_PY, FIXED_STATS_PY, build_fixture_repo


@pytest.fixture
def two_bugs_repo(tmp_path, monkeypatch) -> Path:
    """One repository, two INDEPENDENT defects: the fixture's median bug and a shout bug
    planted in the clean neighbour module."""
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    root = build_fixture_repo(tmp_path / "a")
    # The second defect, in the neighbour module the fixture keeps clean on purpose.
    (root / "textutil.py").write_text(
        '"""Text helpers (fixture repo)."""\n\n\ndef shout(text):\n    return text.lower() + "!"\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_textutil.py").write_text(
        'from textutil import shout\n\n\ndef test_shout():\n    assert shout("hey") == "HEY!"\n',
        encoding="utf-8",
    )
    yield root
    code_task_runtime().reset()
    reset_mode_permission_state()


def _ctx(root: Path) -> dict:
    return {"workspace": str(root), "workspace_root": str(root), "session_id": "multi-question", "operating_mode": "auto"}


def _door(intent: str, arguments: dict, ctx: dict):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool(intent, arguments, source_context=ctx)
    assert result is not None, intent
    return result


def test_two_problems_only_the_authorized_one_is_fixed(two_bugs_repo: Path) -> None:
    """The task is authorized for the median defect only. The shout defect is inspected and
    EXPLAINED, but no byte of it changes -- scope is the task's objective, not a suggestion."""
    root = two_bugs_repo
    ctx = _ctx(root)
    shout_before = (root / "textutil.py").read_text(encoding="utf-8")

    opened = _door("code.task.open", {"objective": "Find why the median test fails and repair the root cause"}, ctx)
    task_id = opened.details["task_id"]

    # Inspecting the second problem is a lawful READ: looking is not changing.
    other = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "inspect-other", "intent": "workspace.read_file", "arguments": {"path": "textutil.py"}},
        ctx,
    )
    assert other.ok, other.response_text
    other_tests = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "inspect-other-tests", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q tests/test_textutil.py"}},
        ctx,
    )
    assert other_tests.details["tool_result"]["success"] is False, "the second defect genuinely fails"

    # The authorized repair: median.
    repro = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q tests/test_stats.py"}},
        ctx,
    )
    assert repro.details["tool_result"]["success"] is False
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "read-stats", "intent": "workspace.read_file", "arguments": {"path": "stats.py"}},
        ctx,
    )
    _door("code.task.identify", {"task_id": task_id, "path": "stats.py", "line": 17, "reason": "median indexes the unsorted list"}, ctx)
    import hashlib

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
    _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": "stats.py", "content": FIXED_STATS_PY}},
        ctx,
    )
    narrow = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "narrow", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q tests/test_stats.py"}},
        ctx,
    )
    assert narrow.details["tool_result"]["success"] is True

    # The cumulative run sees BOTH defects: the authorized fix green, the unauthorized one
    # still red. The report must carry that truth rather than claiming completion.
    cumulative = _door(
        "code.task.step",
        {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests", "arguments": {"command": "python -m pytest -q"}},
        ctx,
    )
    assert cumulative.details["tool_result"]["success"] is False, "the unapproved defect must still fail the pack"

    report = _door("code.task.report", {"task_id": task_id}, ctx)
    assert report.details["verdict"] == "unresolved"
    assert (root / "textutil.py").read_text(encoding="utf-8") == shout_before, "the unauthorized file is byte-identical"

    # The PR description refuses to launder a task whose cumulative run is red.
    refused = _door("code.task.pr_description", {"task_id": task_id}, ctx)
    assert refused.ok is False and refused.status == "insufficient_evidence"


def test_two_authorized_dependent_edits_accumulate_checks(two_bugs_repo: Path) -> None:
    """Both defects authorized, as two dependent TASKS: the stage machine admits one approved
    repair per task, so the second edit is its own task, and its cumulative check covers both
    files -- a regression the second edit introduced in the first fix is caught there."""
    root = two_bugs_repo
    ctx = _ctx(root)
    import hashlib

    def repair(owner: str, buggy_text: str, fixed_text: str, reason: str, narrow: str, cumulative: str) -> str:
        opened = _door("code.task.open", {"objective": f"Repair the {owner} defect"}, ctx)
        task_id = opened.details["task_id"]
        _door(
            "code.task.step",
            {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests", "arguments": {"command": narrow}},
            ctx,
        )
        _door(
            "code.task.step",
            {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file", "arguments": {"path": owner}},
            ctx,
        )
        _door("code.task.identify", {"task_id": task_id, "path": owner, "line": 2, "reason": reason}, ctx)
        before = hashlib.sha256((root / owner).read_bytes()).hexdigest()
        _door(
            "code.task.propose",
            {
                "task_id": task_id,
                "proposal_id": "p",
                "intent": "workspace.write_file",
                "arguments": {"path": owner, "content": fixed_text, "expected_hash": before},
                "rationale": f"Owner {owner}: {reason}.",
            },
            ctx,
        )
        _door("code.task.approve", {"task_id": task_id, "proposal_id": "p"}, ctx)
        _door(
            "code.task.step",
            {"task_id": task_id, "step_id": "fix", "intent": "workspace.write_file", "arguments": {"path": owner, "content": fixed_text, "expected_hash": before}},
            ctx,
        )
        narrow_run = _door(
            "code.task.step",
            {"task_id": task_id, "step_id": "narrow", "intent": "workspace.run_tests", "arguments": {"command": narrow}},
            ctx,
        )
        assert narrow_run.details["tool_result"]["success"] is True, narrow_run.details
        cumulative_run = _door(
            "code.task.step",
            {"task_id": task_id, "step_id": "cumulative", "intent": "workspace.run_tests", "arguments": {"command": cumulative}},
            ctx,
        )
        assert cumulative_run.details["tool_result"]["success"] is True, cumulative_run.details
        _door("code.task.step", {"task_id": task_id, "step_id": "diff", "intent": "workspace.git_diff", "arguments": {}}, ctx)
        return task_id

    # First dependent edit: median. Its cumulative scope is the statistics pack.
    first = repair("stats.py", DEFECT_STATS_PY, FIXED_STATS_PY, "median indexes the unsorted list",
                   "python -m pytest -q tests/test_stats.py", "python -m pytest -q tests/test_stats.py")

    # Second dependent edit: shout. Its cumulative is the WHOLE pack, so it also re-proves the
    # first fix -- the "after each" accumulation the mission row names.
    shout_fixed = '"""Text helpers (fixture repo)."""\n\n\ndef shout(text):\n    return text.upper() + "!"\n'
    second = repair("textutil.py", "lower", shout_fixed, "shout lowercases instead of uppercasing",
                    "python -m pytest -q tests/test_textutil.py", "python -m pytest -q")

    report = _door("code.task.report", {"task_id": second}, ctx)
    assert report.details["verdict"] == "completed"

    description = _door("code.task.pr_description", {"task_id": second}, ctx)
    assert description.ok, description.response_text
    assert "textutil.py" in description.details["body"]
    # The first task completed too, on its own scoped cumulative.
    first_report = _door("code.task.report", {"task_id": first}, ctx)
    assert first_report.details["verdict"] == "completed"
