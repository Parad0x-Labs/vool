"""The coding evidence contract, revision 3: the EFFECTIVE INVOCATION behind coverage.

Revision 2's independent review measured five false-completion paths (F1 arguments/modes
treated as coverage, F2 same-basename checks across working directories, F3 option/value
association collapsed in repeat identity). The repair parses what a command EFFECTIVELY
executes -- the executed entry point, interpreter modes, option/value pairs and program
arguments, each resolved against its own working directory -- and binds coverage to the
retained failure through documented runner adapters. Everything here runs the production
task/command path over disposable files; no model is involved and none is claimed.

Beside the five review reproductions (kept verbatim in the review folders), this file owns:

* NOVEL false-completion cases -- a different layout (``rate.py``/``verify.py``/``tools/``),
  a different sibling entry point, ``-OO`` (a different assertion-stripping mode than the
  review's ``-O``), an inline print with the check as an argument, and a shell-runner
  variant where the check is an ARGUMENT to another script;
* the TRANSLATED cwd byte-binding oracle -- the revision-2 oracle's unsound assumption (a
  copied check under ``checks/`` covering the root obligation it never was) is corrected by
  reproducing the actual arithmetic failure under the command's own cwd BEFORE the repair,
  keeping the changed-check invalidation assertion and adding the same-name decoy negative;
* LAWFUL progressions -- the full-suite form and a broader pytest selection completing what
  a narrower check could not; a real cwd alias (``checks``/``./checks``/``checks/``) still
  completing; a safe interpreter mode (``-B``) not blocking completion;
* repeat identity -- a truly identical rerun suppressed, materially different ``-k``/``-m``
  associations and repeated options running, and a declared purposeful rerun permitted;
* honest unknowns -- a collect-only green and a node-id selector each represented as what
  they are, never promoted into completion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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

RATE_BUGGY = "def gross(net, vat):\n    return net - vat\n"
RATE_WRONG = "def gross(net, vat):\n    return net * vat - net + 2 * vat\n"
RATE_FIXED = "def gross(net, vat):\n    return net + vat\n"
VERIFY = "from rate import gross\nassert gross(100, 20) == 120, gross(100, 20)\nprint('rate ok')\n"
DIAG = "print('diagnostic only')\n"
#: A whitelisted generic runner's retained check: green exactly when rate.py holds the sum.
GREP_CHECK = 'grep -q "return net + vat" rate.py'
GREP_OTHER = 'grep -q "diagnostic only" tools/diag.py'
GREP_NARROWER = 'grep -q "return net" rate.py'

PYTEST_RATE_TEST = (
    "from rate import gross\n\n"
    "def test_gross():\n    assert gross(100, 20) == 120\n\n"
    "def test_zero():\n    assert gross(0, 0) == 0\n"
)


def _finish_with(task: Task, args: dict[str, Any]) -> dict[str, Any]:
    """Drive a wrong repair to the furthest stage the runtime honestly allows for ``args``,
    then ask the report door for its verdict (the review's journey helper, reused)."""
    first = task.step("workspace.run_tests", args)
    assert first.details.get("executed") is True, first.status
    if _journal(task.id)["stage"] == "cumulative":
        task.step("workspace.run_tests", args)
    if _journal(task.id)["stage"] == "inspect_diff":
        task.step("workspace.git_diff", {})
    return _door("code.task.report", {"task_id": task.id}, task.ctx).details


def _drive_to_narrow(root: Path, session: str, *, command: str, cwd: str | None,
                     owner: str, buggy: str, repair: str) -> Task:
    """A task reproduced by ``command`` under ``cwd`` (the retained obligation), whose owner
    carries the deliberately WRONG ``repair`` -- the state every false-completion case starts
    from. The arithmetic red is asserted so the obligation is never a missing-file miss."""
    ctx = {"workspace": str(root), "workspace_root": str(root), "session_id": session,
           "runtime_session_id": session, "operating_mode": "auto"}
    task_id = _door("code.task.open", {"objective": f"Repair {owner} so its check passes"}, ctx).details["task_id"]
    arguments = {"command": command, **({"cwd": cwd} if cwd else {})}
    red = _door("code.task.step", {"task_id": task_id, "step_id": "repro", "intent": "workspace.run_tests",
                                   "arguments": arguments}, ctx)
    assert red.details["tool_result"]["success"] is False
    assert "can't open file" not in str(red.details["tool_result"].get("stderr") or ""), red.details["tool_result"]
    assert _journal(task_id)["reproduced_failure"]["cwd"] == (cwd or "")
    assert _door("code.task.identify", {"task_id": task_id, "path": owner, "line": 2,
                                        "reason": "operator combined wrongly"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "read", "intent": "workspace.read_file",
                                    "arguments": {"path": owner}}, ctx).ok
    assert _door("code.task.propose", {"task_id": task_id, "proposal_id": "w", "intent": "workspace.write_file",
                                       "arguments": {"path": owner, "content": repair},
                                       "rationale": f"Owner {owner}: wrong combination."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task_id, "proposal_id": "w"}, ctx).ok
    assert _door("code.task.step", {"task_id": task_id, "step_id": "mut", "intent": "workspace.write_file",
                                    "arguments": {"path": owner, "content": repair}}, ctx).ok

    class Driven(Task):
        def __init__(self) -> None:
            self.root = root
            self.ctx = ctx
            self.id = task_id
            self.n = 50

    return Driven()


# ---------------------------------------------------------------------------
# novel false-completion cases: a different layout, entry point and mode
# ---------------------------------------------------------------------------


@pytest.fixture
def rate_project(project: Path) -> Path:
    (project / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    (project / "verify.py").write_text(VERIFY, encoding="utf-8")
    (project / "tools").mkdir()
    (project / "tools" / "diag.py").write_text(DIAG, encoding="utf-8")
    return project


@pytest.mark.parametrize("command", [
    'python3 -c "print(7)" verify.py',     # the check is an ARGUMENT to inline code
    "python3 tools/diag.py verify.py",     # a sibling entry point; the check is program argv
    "python3 -OO verify.py",               # a different assertion-stripping mode than -O
])
def test_arguments_and_modes_cannot_complete_a_wrong_repair_novel(rate_project, command):
    """The same false-completion class as the review's three, on a different layout and
    different entry points: every one of these may run and exit green, and none of them may
    finish a task whose retained acceptance check still fails."""
    task = _drive_to_narrow(rate_project, "novel-false-completion", command="python3 verify.py",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    assert task.step("workspace.run_tests", {"command": "python3 verify.py"}).details["tool_result"]["success"] is False
    report = _finish_with(task, {"command": command})
    assert report["verdict"] == "unresolved", report
    assert (rate_project / "rate.py").read_text(encoding="utf-8") == RATE_WRONG


@pytest.mark.parametrize("command", [GREP_OTHER, GREP_NARROWER])
def test_a_generic_runner_cannot_be_covered_by_a_different_selection(rate_project, command):
    """An unknown runner's semantics cannot be inferred from argument spelling: a green grep
    over a different file -- or the same file under a looser pattern -- is a DIFFERENT
    invocation and never stands in for the retained check."""
    task = _drive_to_narrow(rate_project, "generic-runner", command=GREP_CHECK, cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    assert task.step("workspace.run_tests", {"command": GREP_CHECK}).details["tool_result"]["success"] is False
    report = _finish_with(task, {"command": command})
    assert report["verdict"] == "unresolved", report


def test_the_retained_generic_check_itself_completes_the_generic_obligation(rate_project):
    """Positive control for the unknown-runner rule: re-running the retained invocation
    verbatim after the real repair covers and completes; only a different selection was
    refused. The recovery (re-diagnose, fresh approval, real repair) happens in the SAME
    durable task."""
    task = _drive_to_narrow(rate_project, "generic-retained", command=GREP_CHECK, cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    assert _finish_with(task, {"command": GREP_OTHER})["verdict"] == "unresolved"
    _recover_with_real_fix(task, owner="rate.py", fixed=RATE_FIXED, reason="vat subtracted", retained=GREP_CHECK)
    assert task.step("workspace.run_tests", {"command": GREP_CHECK}).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": GREP_CHECK}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report
    assert report["obligation"]["reproduced_command"] == GREP_CHECK
    assert report["obligation"]["covered_by_current_cumulative"] is True
    assert report["obligation"]["runner_family"] == "generic"


# ---------------------------------------------------------------------------
# the translated cwd byte-binding oracle (revision-2 oracle correction)
# ---------------------------------------------------------------------------


def test_cwd_check_binds_from_its_own_arithmetic_red_through_completion(project):
    """The honest translation of the revision-2 cwd oracle. Its scenario -- a copied check
    under ``checks/`` -- is reproduced HERE as the task's own obligation: the red runs under
    the command's cwd and is asserted to be the arithmetic failure. The executed cwd check
    stays byte-bound after green (changed-check invalidation preserved), while a same-name
    file that was never executed changes nothing."""
    checks = project / "checks"
    checks.mkdir()
    (checks / "check.py").write_text("import sys\nsys.path.insert(0, '..')\n" + CHECK, encoding="utf-8")
    task = _drive_to_narrow(project, "translated-cwd-oracle", command="python3 check.py", cwd="checks",
                            owner="calc.py", buggy=CALC, repair=CALC_FIXED)
    args = {"command": "python3 check.py", "cwd": "checks"}
    assert task.step("workspace.run_tests", args).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", args).details["stage"] == "inspect_diff"
    bound = _journal(task.id)["cumulative"]["bytes"]
    assert "checks/check.py" in bound and "check.py" not in bound
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"
    # A same-name file at the root that no executed command bound: editing it changes nothing.
    (project / "check.py").write_text("raise AssertionError('unexecuted same-name decoy')\n", encoding="utf-8")
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report
    # The EXECUTED check changing invalidates the evidence: the changed-check assertion the
    # revision-2 oracle owned, kept exactly as strong.
    (checks / "check.py").write_text(
        "import sys\nsys.path.insert(0, '..')\n" + CHECK + "assert total(3, 3) == 6\n", encoding="utf-8")
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved", report
    assert "checks/check.py" in report["bytes_changed"]


def test_a_root_obligation_is_not_covered_by_the_checks_directory_copy(project):
    """The other half of the translation: when the obligation IS the root check, the copied
    ``checks/`` check is a different executed entry point and cannot complete the task --
    the boundary the review's false completion measured, now held by the runtime."""
    task = Task(project, "root-vs-copy")
    checks = project / "checks"
    checks.mkdir()
    (checks / "check.py").write_text("import sys\nsys.path.insert(0, '..')\nprint('unrelated')\n", encoding="utf-8")
    args = {"command": "python3 check.py", "cwd": "checks"}
    assert task.step("workspace.run_tests", args).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", args).details["stage"] == "cumulative"  # holds: not the obligation
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved", report
    assert report["obligation"]["covered_by_current_cumulative"] is False
    # And the door stays open: the retained root check still completes the same task.
    assert task.step("workspace.run_tests", {"command": "python3 check.py"}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


def test_a_real_cwd_alias_retains_lawful_completion(project):
    """Cwd aliases naming the SAME directory are the same obligation: ``checks``, ``./checks``
    and ``checks/`` all resolve to one executed entry point and complete lawfully."""
    checks = project / "checks"
    checks.mkdir()
    (checks / "check.py").write_text("import sys\nsys.path.insert(0, '..')\n" + CHECK, encoding="utf-8")
    task = _drive_to_narrow(project, "cwd-alias", command="python3 check.py", cwd="checks",
                            owner="calc.py", buggy=CALC, repair=CALC_FIXED)
    assert task.step("workspace.run_tests", {"command": "python3 check.py", "cwd": "./checks"}) \
        .details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": "python3 check.py", "cwd": "checks/"}) \
        .details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


def test_a_safe_interpreter_mode_does_not_block_lawful_completion(rate_project):
    """``-B`` (no bytecode writing) cannot weaken an assertion-based check, so a green run
    under it covers the obligation: the conservative mode contract refuses only the modes
    that strip acceptance conditions."""
    task = _drive_to_narrow(rate_project, "safe-mode", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_FIXED)
    assert task.step("workspace.run_tests", {"command": "python3 -B verify.py"}).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": "python3 -B verify.py"}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


# ---------------------------------------------------------------------------
# lawful suite progressions: narrower cannot, broader and full-suite can
# ---------------------------------------------------------------------------


@pytest.fixture
def pytest_project(project: Path) -> Path:
    (project / "tests").mkdir()
    (project / "rate.py").write_text(RATE_BUGGY, encoding="utf-8")
    (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests" / "test_rate.py").write_text(PYTEST_RATE_TEST, encoding="utf-8")
    return project


def _recover_with_real_fix(task: Task, *, owner: str, fixed: str, reason: str,
                           retained: str | None = None) -> None:
    """The lawful recovery a failed or uncovered verification owes: if a green-but-uncovered
    check left the task at a verification stage, first run the RETAINED check so its honest
    red reopens review; then re-diagnose in the SAME durable task, propose against the
    current bytes, obtain a fresh approval, and land it."""
    stage = _journal(task.id)["stage"]
    if stage in {"narrow_test", "cumulative"} and retained:
        red = task.step("workspace.run_tests", {"command": retained})
        assert red.details["tool_result"]["success"] is False, red.details["tool_result"]
    ctx = task.ctx
    assert _door("code.task.identify", {"task_id": task.id, "path": owner, "line": 2,
                                        "reason": reason}, ctx).ok
    assert _door("code.task.step", {"task_id": task.id, "step_id": "read2", "intent": "workspace.read_file",
                                    "arguments": {"path": owner}}, ctx).ok
    assert _door("code.task.propose", {"task_id": task.id, "proposal_id": "real", "intent": "workspace.write_file",
                                       "arguments": {"path": owner, "content": fixed},
                                       "rationale": f"Owner {owner}: the check requires the sum."}, ctx).ok
    assert _door("code.task.approve", {"task_id": task.id, "proposal_id": "real"}, ctx).ok
    assert task.step("workspace.write_file", {"path": owner, "content": fixed}).ok


def test_narrower_filter_cannot_cover_but_the_full_suite_completes(pytest_project):
    """The lawful progression: a focused obligation reproduced red under the WRONG repair, a
    NARROWER filter's green refused at the cumulative boundary, then the real repair verified
    by the focused check and covered by the validation tool's own full-suite form."""
    task = _drive_to_narrow(pytest_project, "suite-progression", command="python3 -m pytest -q tests/test_rate.py",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    # The wrong repair fails the focused obligation (test_gross); the narrower -k test_zero
    # green advances the narrow stage but is recorded as not covering the obligation.
    narrower = task.step("workspace.run_tests",
                         {"command": "python3 -m pytest -q tests/test_rate.py -k test_zero"})
    assert narrower.details["tool_result"]["success"] is True
    entry = _journal(task.id)["verifications"][-1]
    assert entry["covers_obligation"] is False and "filters the suite" in entry["stale_reason"], entry
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved"
    # The wrong repair is still wrong; recover in the SAME task with a fresh approval.
    _recover_with_real_fix(task, owner="rate.py", fixed=RATE_FIXED, reason="vat subtracted",
                           retained="python3 -m pytest -q tests/test_rate.py")
    # Focused narrow green, then the DEFAULT full-suite form (no explicit command) covers it.
    assert task.step("workspace.run_tests",
                     {"command": "python3 -m pytest -q tests/test_rate.py"}).details["stage"] == "cumulative"
    full = task.step("workspace.run_tests", {})
    assert full.details["stage"] == "inspect_diff", full.details.get("verification")
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report
    assert report["obligation"]["reproduced_command"].endswith("-m pytest -q tests/test_rate.py")
    assert report["obligation"]["covered_by_current_cumulative"] is True


def test_a_directory_selection_covers_the_file_obligation_beneath_it(pytest_project):
    task = _drive_to_narrow(pytest_project, "directory-covers", command="python3 -m pytest -q tests/test_rate.py",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_FIXED)
    assert task.step("workspace.run_tests", {"command": "python3 -m pytest -q tests/test_rate.py"}) \
        .details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": "python3 -m pytest -q tests"}) \
        .details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


def test_collect_only_green_is_recorded_as_not_covering(pytest_project):
    """A collect-only run is green without executing any test: it must be represented as NOT
    covering the obligation, with a reason naming what it is."""
    task = _drive_to_narrow(pytest_project, "collect-only", command="python3 -m pytest -q tests/test_rate.py",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_FIXED)
    collect = task.step("workspace.run_tests",
                        {"command": "python3 -m pytest -q --co tests/test_rate.py"})
    assert collect.details["tool_result"]["success"] is True
    entry = _journal(task.id)["verifications"][-1]
    assert entry["covers_obligation"] is False, entry
    assert "collect" in entry["stale_reason"], entry["stale_reason"]
    assert _journal(task.id)["stage"] == "cumulative"
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved"
    # The executing check still completes the task afterwards.
    assert task.step("workspace.run_tests", {"command": "python3 -m pytest -q tests"}) \
        .details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


def test_a_node_id_obligation_binds_its_real_file(pytest_project):
    """A pytest node id selects INSIDE its file: the obligation's input binding names the
    real file, and a run of that whole file covers the node."""
    task = _drive_to_narrow(pytest_project, "node-id",
                            command="python3 -m pytest -q tests/test_rate.py::test_gross",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_FIXED)
    args = {"command": "python3 -m pytest -q tests/test_rate.py::test_gross"}
    assert task.step("workspace.run_tests", args).details["stage"] == "cumulative"
    bound = _journal(task.id)["narrow"]["bytes"]
    assert "tests/test_rate.py" in bound
    assert task.step("workspace.run_tests", {"command": "python3 -m pytest -q tests/test_rate.py"}) \
        .details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report


# ---------------------------------------------------------------------------
# repeat identity: identical suppressed, materially different run
# ---------------------------------------------------------------------------


def test_identical_rerun_suppressed_and_materially_different_associations_run(pytest_project):
    """The review's direct F3 collision, driven through the task: ``-k a -m b`` and
    ``-k b -m a`` are materially different requested verifications (each runs), while the
    truly identical rerun is the bounded repeat correction."""
    task = _drive_to_narrow(pytest_project, "repeat-identity",
                            command="python3 -m pytest -q tests/test_rate.py -k alpha -m beta",
                            cwd=None, owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    first = task.step("workspace.run_tests",
                      {"command": "python3 -m pytest -q tests/test_rate.py -k alpha -m beta"})
    assert first.details["executed"] is True, first.status
    identical = task.step("workspace.run_tests",
                          {"command": "python3 -m pytest -q tests/test_rate.py -k alpha -m beta"})
    assert identical.status == "repeated_verification", identical.status
    assert identical.details["executed"] is False
    swapped = task.step("workspace.run_tests",
                        {"command": "python3 -m pytest -q tests/test_rate.py -k beta -m alpha"})
    assert swapped.status != "repeated_verification", swapped.status
    assert swapped.details["executed"] is True
    repeated_flag = task.step("workspace.run_tests",
                              {"command": "python3 -m pytest -q tests/test_rate.py -k alpha -k beta"})
    assert repeated_flag.status != "repeated_verification", repeated_flag.status
    assert repeated_flag.details["executed"] is True
    purposeful = task.step("workspace.run_tests",
                           {"command": "python3 -m pytest -q tests/test_rate.py -k alpha -m beta"},
                           rerun_reason="operator asked to see the selection count again")
    assert purposeful.details["executed"] is True, purposeful.status
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test"
    assert len([v for v in journal["verifications"] if v.get("rerun_reason")]) == 1


def test_command_selection_keeps_flag_value_association_and_repeats():
    from core.code_assistant.task_runtime import _command_selection

    assert _command_selection("pytest tests/test_calc.py -k alpha -m beta") != _command_selection(
        "pytest tests/test_calc.py -k beta -m alpha")
    assert _command_selection("pytest -k a -k b") != _command_selection("pytest -k a")
    assert _command_selection("pytest -q tests/") == _command_selection("pytest tests/ -q")
    assert _command_selection("python3 check.py") != _command_selection("python3 -O check.py")
    assert _command_selection("python3 -c 'pass' check.py") != _command_selection("python3 check.py")


# ---------------------------------------------------------------------------
# diagnostics stay lawful while the obligation stays open
# ---------------------------------------------------------------------------


def test_diagnostics_run_and_retire_nothing_until_the_real_check_lands(rate_project):
    """Refusal/diagnostic controls on the novel layout: an inline green and a ``cat`` run as
    diagnostics that retire nothing (the stage holds); a sibling script through the
    obligation's own runner is a GENUINE focused check that advances narrow, yet still cannot
    complete; the same task then completes with its actual covering checks after the real
    repair."""
    task = _drive_to_narrow(rate_project, "diagnostics", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    for intent, command in [
        ("sandbox.run_command", 'python3 -c "print(7)"'),
        ("sandbox.run_command", "cat verify.py"),
    ]:
        outcome = task.step(intent, {"command": command})
        assert outcome.details["executed"] is True, outcome.status
        journal = _journal(task.id)
        assert journal["stage"] == "narrow_test", journal["stage"]
        assert journal["narrow"] is None
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved"
    assert report["obligation"]["reproduced_command"] == "python3 verify.py"
    # The sibling script is genuine (the obligation's runner executing a workspace script),
    # so its green may advance the focused stage -- and its coverage verdict still names the
    # obligation it did not check.
    sibling = task.step("sandbox.run_command", {"command": "python3 tools/diag.py verify.py"})
    assert sibling.details["executed"] is True, sibling.status
    entry = _journal(task.id)["verifications"][-1]
    assert entry["obligation_match"] is True and entry["covers_obligation"] is False, entry
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "unresolved"
    # The recovery the boundary owes: re-diagnose, fresh approval, real repair, real checks.
    _recover_with_real_fix(task, owner="rate.py", fixed=RATE_FIXED, reason="vat subtracted",
                           retained="python3 verify.py")
    assert task.step("workspace.run_tests", {"command": "python3 verify.py"}).details["stage"] == "cumulative"
    assert task.step("workspace.run_tests", {"command": "python3 verify.py"}).details["stage"] == "inspect_diff"
    assert task.step("workspace.git_diff", {}).ok
    final = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert final["verdict"] == "completed", final
    # The wrong-repair evidence survived in the journal as history, not as the verdict.
    verifications = _journal(task.id)["verifications"]
    assert any(v.get("success") is False for v in verifications)
    assert any(v.get("covers_obligation") is False for v in verifications if v.get("obligation_match"))


def test_new_verifications_carry_the_versioned_invocation_record(rate_project):
    """The persisted identity: every recorded verification carries a ``v``-keyed effective
    invocation (runner, family, entry, interpreter modes, bound options, operands, args, cwd)
    alongside the raw command it was decided from."""
    task = _drive_to_narrow(rate_project, "invocation-record", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    task.step("workspace.run_tests", {"command": "python3 -O verify.py"})
    record = _journal(task.id)["verifications"][-1]
    invocation = record.get("invocation") or {}
    assert invocation.get("v") == 2  # revision 4: ordered argv, whole option values, resolved executable
    assert invocation.get("runner") == "python3"
    assert invocation.get("family") == "python-script"
    assert invocation.get("entry") == "verify.py"
    assert ["-O", []] in invocation.get("interpreter", [])
    assert invocation.get("cwd") == ""
    # Old-journal honesty: a verification record WITHOUT the invocation key is still legible,
    # and completion never trusts a stale covers field it cannot recompute.
    raw = json.loads((Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task.id}.json").read_text(encoding="utf-8"))
    legacy = dict(raw["verifications"][-1])
    legacy.pop("invocation", None)
    legacy["covers_obligation"] = True  # a stale claim an older runtime might have written
    raw["verifications"].append({**legacy, "step_id": "legacy-1", "current": False, "legacy": True})
    (Path(os.environ["VOOL_CODE_TASK_DIR"]) / f"{task.id}.json").write_text(
        json.dumps(raw, sort_keys=True, indent=1), encoding="utf-8")
    from core.code_assistant.task_runtime import code_task_runtime

    code_task_runtime().reset()  # reload from disk; the journal is the authority
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved", report
