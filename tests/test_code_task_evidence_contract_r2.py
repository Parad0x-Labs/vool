"""The coding evidence contract, revision 2: obligation ownership, check-input identity and
repeat equivalence beyond the five review findings.

Everything here runs the production task/command path over disposable files. The five review
failures themselves stay in ``tests/review-r2/test_review_findings.py`` (imported unchanged
from the independent review); this file adds the ORIGINAL-failure continuations and the NOVEL
cases the correction asks for beside them:

* obligation ownership -- a novel irrelevant green command beside the review's three; a
  genuine check and a legitimate changed check selection still advance; a green cumulative
  that does not cover the retained obligation cannot complete the task; diagnostics stay
  lawful without retiring the obligation;
* check-input identity -- the bound input is the file the command executed under its declared
  cwd, recomputable after a runtime restart from the journal alone; an unrelated same-name
  root file is NOT the binding; the eight-input bound is represented honestly;
* repeat equivalence -- a task-level changed selection (``-k`` with a different value) is not
  a repeat, while a truly redundant identical rerun still gets the bounded correction.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.test_code_task_purposeful_verification import (  # noqa: F401  (fixture + helpers)
    CALC,
    CALC_FIXED,
    CHECK,
    Task,
    _door,
    _journal,
    _wrong_fix_task,
    project,
)

CHECK_EXTRA = "from calc import total\nassert total(1, 1) == 2, total(1, 1)\nprint('extra ok')\n"


# ---------------------------------------------------------------------------
# obligation ownership, beyond the review's three cases
# ---------------------------------------------------------------------------


def test_novel_irrelevant_green_command_cannot_retire_the_obligation(project):
    """A different irrelevant green than the review's three: same interpreter as the
    obligation's runner, no named operand, green exit, zero evidence."""
    task = _wrong_fix_task(project, "irrelevant-novel")
    outcome = task.step("sandbox.run_command", {"command": "python3 --version"})
    assert outcome.details["executed"] is True, outcome.status
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test", journal["stage"]
    assert journal["narrow"] is None
    entry = next(v for v in journal["verifications"] if v["step_id"] == outcome.details["step_id"])
    assert entry["obligation_match"] is False and entry["covers_obligation"] is None


def test_a_diagnostic_runs_but_retires_nothing(project):
    """Reading the file under repair is a lawful diagnostic at a verification stage's approach:
    it runs, it is journaled, and the unexercised obligation stays open."""
    task = _wrong_fix_task(project, "diagnostic")
    diagnostic = task.step("sandbox.run_command", {"command": "python3 -m py_compile calc.py"})
    assert diagnostic.details["executed"] is True, diagnostic.status
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["narrow"] is None


def test_genuine_check_and_changed_selection_still_advance(project):
    (project / "check_extra.py").write_text(CHECK_EXTRA, encoding="utf-8")
    task = Task(project, "changed-selection")
    # A legitimate changed check selection: the same runner executing a different, existing
    # workspace operand. Genuine; it discharges narrow.
    focused = task.step("workspace.run_tests", {"command": "python3 check_extra.py"})
    assert focused.details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "cumulative"
    # And the cumulative that covers the retained obligation (check.py, plus more) completes.
    covering = task.step("workspace.run_tests", {"command": "python3 check.py"})
    assert covering.details["tool_result"]["success"] is True
    assert _journal(task.id)["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed"
    assert report["obligation"]["reproduced_command"] == "python3 check.py"
    assert report["obligation"]["covered_by_current_cumulative"] is True


def test_green_cumulative_that_does_not_cover_the_obligation_cannot_complete(project):
    (project / "check_extra.py").write_text(CHECK_EXTRA, encoding="utf-8")
    task = Task(project, "uncovered-cumulative")
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["stage"] == "cumulative"
    sibling = task.step("workspace.run_tests", {"command": "python3 check_extra.py"})
    journal = _journal(task.id)
    entry = journal["verifications"][-1]
    assert entry["success"] is True and entry["covers_obligation"] is False
    assert "check.py" in entry["stale_reason"]
    # The stage holds at cumulative: the retained obligation names the check still owed.
    assert journal["stage"] == "cumulative"
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved"
    assert report["obligation"]["covered_by_current_cumulative"] is False
    # The door stays open: the covering check then completes the task.
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


# ---------------------------------------------------------------------------
# check-input identity: resolved cwd, restart, same-name root file, bound honesty
# ---------------------------------------------------------------------------


def test_input_binding_survives_restart_and_ignores_a_same_name_root_file(tmp_path, monkeypatch):
    from core.code_assistant.task_runtime import code_task_runtime
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_CODE_TASK_DIR", str(tmp_path / "code_tasks"))
    code_task_runtime().reset()
    import subprocess

    root = tmp_path / "project"
    (root / "checks").mkdir(parents=True)
    (root / "calc.py").write_text(CALC, encoding="utf-8")
    (root / "checks" / "check.py").write_text(
        "import sys\nsys.path.insert(0, '..')\n" + CHECK, encoding="utf-8")
    # NOTE: no root check.py exists at all -- the executed check lives only under checks/.
    env = {**os.environ, "GIT_AUTHOR_NAME": "fx", "GIT_AUTHOR_EMAIL": "fx@local",
           "GIT_COMMITTER_NAME": "fx", "GIT_COMMITTER_EMAIL": "fx@local"}
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "seed"]):
        assert subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30,
                              env=env).returncode == 0
    # The reproduction itself runs under the command's own cwd (`checks/`, where the check
    # file lives), so the retained red is the ARITHMETIC failure the check asserts -- not a
    # "can't open file" miss of an absent root check.py. (Setup correction recorded in
    # TESTS.md: revision 2 drove this fixture through Task.__init__, whose root reproduction
    # recorded a missing-file failure as the obligation while the comment claimed a cwd
    # reproduction journey.)
    ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": "restart-binding",
           "runtime_session_id": "restart-binding", "operating_mode": "auto"}
    task_id = _door("code.task.open", {"objective": "Repair total() so its check passes"}, ctx).details["task_id"]
    red = _door("code.task.step", {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                                   "arguments": {"command": "python3 check.py", "cwd": "checks"}}, ctx)
    assert red.details["tool_result"]["success"] is False
    assert red.details["tool_result"]["returncode"] != 2, red.details["tool_result"]
    assert "AssertionError" in str(red.details["tool_result"].get("stderr") or "") \
        or "AssertionError" in str(red.details["tool_result"].get("stdout") or ""), red.details["tool_result"]
    assert _journal(task_id)["reproduced_failure"]["cwd"] == "checks"
    assert _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2,
                                        "reason": "tax subtracted"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                                    "arguments": {"path": "calc.py"}}, ctx).ok
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "fix", "intent": "workspace.write_file",
                                       "arguments": {"path": "calc.py", "content": CALC_FIXED},
                                       "rationale": "Owner calc.py: the check requires the sum."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "fix"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "mut", "intent": "workspace.write_file",
                                    "arguments": {"path": "calc.py", "content": CALC_FIXED}}, ctx).ok

    class RestartTask(Task):
        def __init__(self) -> None:
            self.root = root
            self.ctx = ctx
            self.id = task_id
            self.n = 50

    task = RestartTask()
    args = {"command": "python3 check.py", "cwd": "checks"}
    try:
        assert task.step("workspace.run_tests", args).details["stage"] == "cumulative"
        assert task.step("workspace.run_tests", args).details["stage"] == "inspect_diff"
        bound = _journal(task.id)["cumulative"]["bytes"]
        assert "checks/check.py" in bound and "check.py" not in bound
        assert task.step("workspace.git_diff", {}).ok
        assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"
        # A runtime restart (fresh instance; the journal on disk is the only state).
        code_task_runtime().reset()
        # An unrelated same-name file appears at the root: it was never executed, so editing it
        # changes nothing the evidence bound.
        (root / "check.py").write_text("raise AssertionError('root decoy')\n", encoding="utf-8")
        report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
        assert report["verdict"] == "completed", report["bytes_changed"]
        # The executed check fixture changes AFTER the restart: identity recomputed from the
        # journal (command + cwd) still names checks/check.py, so the evidence invalidates.
        (root / "checks" / "check.py").write_text(
            "import sys\nsys.path.insert(0, '..')\n" + CHECK + "assert total(3, 3) == 6\n", encoding="utf-8")
        report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
        assert report["verdict"] == "unresolved"
        assert report["bytes_changed"] == ["checks/check.py"]
    finally:
        code_task_runtime().reset()
        reset_mode_permission_state()


def test_input_bound_represents_truncation_honestly(project):
    """A command naming more operands than the eight-input bound binds the first eight and
    SAYS so in the recorded coverage: the journal never claims complete input evidence."""
    for index in range(10):
        (project / f"part_{index}.py").write_text(f"print('part {index}')\n", encoding="utf-8")
    task = Task(project, "truncation")
    listing = "python3 " + " ".join(f"part_{i}.py" for i in range(10))
    task.step("sandbox.run_command", {"command": listing})
    # The diagnostic ran; its verification record states what the binding covered.
    entry = _journal(task.id)["verifications"][-1]
    coverage = entry.get("input_coverage") or {}
    assert coverage.get("named") == 10 and coverage.get("truncated") is True
    assert coverage.get("coverage") == "declared"
    assert coverage.get("bound", 0) <= 8


# ---------------------------------------------------------------------------
# repeat equivalence: option-aware identity at task level
# ---------------------------------------------------------------------------


PYTEST_TEST = (
    "from calc import total\n\n"
    "def test_add():\n    assert total(2, 3) == 5\n\n"
    "def test_zero():\n    assert total(0, 1) == 1\n"
)


def test_task_level_changed_selection_flag_is_not_a_repeat(project):
    """The direct ``-k=first`` / ``-k=second`` collision is pinned by the review file; here the
    same class runs through the task: a rerun whose ``-k`` value names a different selection is
    NOT the repeat of the failed check, while the truly identical rerun still is."""
    (project / "test_calc.py").write_text(PYTEST_TEST, encoding="utf-8")
    command = "python3 -m pytest -q test_calc.py"
    # A task whose RETAINED OBLIGATION is the pytest run itself (reproduce -> wrong fix).
    ctx = {"workspace": str(project), "workspace_root": str(project), "session_id": "changed-flag",
           "runtime_session_id": "changed-flag", "operating_mode": "auto"}
    task_id = _door("code.task.open", {"objective": "repair calc.add so its test passes"}, ctx).details["task_id"]
    assert _door("code.task.step", {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                                    "arguments": {"command": command}}, ctx).details["tool_result"]["success"] is False
    assert _door("code.task.identify", {"task_id": task_id, "path": "calc.py", "line": 2,
                                        "reason": "add subtracts"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                                    "arguments": {"path": "calc.py"}}, ctx).ok
    wrong = "def total(subtotal, tax):\n    return subtotal * tax\n"
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                       "arguments": {"path": "calc.py", "content": wrong},
                                       "rationale": "Owner calc.py: wrong combination."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "w"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "mut", "intent": "workspace.write_file",
                                    "arguments": {"path": "calc.py", "content": wrong}}, ctx).ok

    class PytestTask(Task):
        def __init__(self) -> None:
            self.root = project
            self.ctx = ctx
            self.id = task_id
            self.n = 100

    task = PytestTask()
    first = task.step("workspace.run_tests", {"command": command})
    assert first.details["tool_result"]["success"] is False
    identical = task.step("workspace.run_tests", {"command": command})
    assert identical.status == "repeated_verification", identical.status
    changed = task.step("workspace.run_tests", {"command": command + " -k test_add"})
    assert changed.status != "repeated_verification", changed.status
    assert changed.details["executed"] is True
    journal = _journal(task.id)
    assert len([v for v in journal["verifications"]]) >= 2
    assert journal["stage"] == "narrow_test" and journal["narrow"]["success"] is False
