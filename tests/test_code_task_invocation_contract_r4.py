"""The coding evidence contract, revision 4: execution modes, argv order, canonical pytest
selection and whole option values.

The revision-3 independent review measured eight false completions / identity collisions at the
effective-invocation owner (``core/code_assistant/task_runtime.py``): interpreter version/help
modes treated as running the named check, assertion-stripping ``-O`` composed with pytest
bypassing the weakening rule, program arguments and repeated options sorted into one identity,
pytest coverage decided by comparing file STRINGS (a sibling node or a same-spelled file under
another cwd "covered"), and ``option_values`` returning the first CHARACTER of each value. Those
eight reproductions stay verbatim in the review folder; this file owns the NOVEL cases on a
different fixture (``ledger.py`` with a helper-module assertion, a test class, parametrized
nodes and markers), the refusal controls, and the lawful positive progressions -- including the
checkpoint that must HOLD on unknown coverage and name the retained check instead of stranding a
genuinely repaired task past the stages where a command may still run.

Everything runs the production task/command door over disposable files with real subprocesses.
No model is involved and none is claimed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests import test_code_task_invocation_contract_r3 as r3
from tests import test_code_task_purposeful_verification as purposeful
from tests.test_code_task_invocation_contract_r3 import (
    RATE_BUGGY,
    RATE_FIXED,
    RATE_WRONG,
    _drive_to_narrow,
    _finish_with,
)
from tests.test_code_task_purposeful_verification import _door, _journal

#: The shared fixtures, re-exported by assignment so pytest discovers them in this module.
project = purposeful.project
rate_project = r3.rate_project

RUN = "workspace.run_tests"
LEDGER_BUGGY = "def balance(credits, debits):\n    return credits + debits\n"
LEDGER_WRONG = "def balance(credits, debits):\n    return credits * debits\n"
LEDGER_FIXED = "def balance(credits, debits):\n    return credits - debits\n"
#: The acceptance assertion lives in a HELPER module: pytest rewrites test modules, not helpers,
#: so ``python -O -m pytest`` strips exactly this assertion (measured on the installed 9.1.0).
LEDGER_CHECKS = (
    "from ledger import balance\n\n\n"
    "def expect_balance(credits, debits, expected):\n"
    "    assert balance(credits, debits) == expected, balance(credits, debits)\n"
)
LEDGER_TESTS = (
    "import pytest\n\n"
    "from tests.ledger_checks import expect_balance\n\n\n"
    "class TestBalance:\n"
    "    @pytest.mark.slow\n"
    "    def test_settles(self):\n"
    "        expect_balance(100, 30, 70)\n\n"
    "    @pytest.mark.smoke\n"
    "    def test_empty(self):\n"
    "        expect_balance(0, 0, 0)\n\n\n"
    "@pytest.mark.parametrize(\"credits,debits,expected\", [(0, 0, 0), (50, 20, 30)])\n"
    "def test_grid(credits, debits, expected):\n"
    "    expect_balance(credits, debits, expected)\n"
)
STANDALONE = "from ledger import balance\n\n\ndef test_settles():\n    assert balance(100, 30) == 70\n"
PASSING = "def test_misc():\n    assert True\n"
RETAINED = "python3 -m pytest -q tests/test_ledger.py"


@pytest.fixture
def ledger_project(project: Path) -> Path:
    (project / "ledger.py").write_text(LEDGER_BUGGY, encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (project / "tests" / "ledger_checks.py").write_text(LEDGER_CHECKS, encoding="utf-8")
    (project / "tests" / "test_ledger.py").write_text(LEDGER_TESTS, encoding="utf-8")
    return project


def _ledger(root: Path, session: str, *, command: str = RETAINED, cwd: str | None = None,
            owner: str = "ledger.py", fixed: bool = False):
    return _drive_to_narrow(root, session, command=command, cwd=cwd, owner=owner, buggy=LEDGER_BUGGY,
                            repair=LEDGER_FIXED if fixed else LEDGER_WRONG)


def _last(task, stage: str) -> dict:
    entries = [v for v in _journal(task.id)["verifications"] if v["stage"] == stage]
    assert entries, _journal(task.id)["verifications"]
    return entries[-1]


def _completes(task, command_args: dict) -> dict:
    """A covering full check at the checkpoint, then the diff and a completed report."""
    full = task.step(RUN, command_args)
    assert full.details["stage"] == "inspect_diff", full.details.get("verification")
    assert task.step("workspace.git_diff", {}).ok
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "completed", report
    assert report["obligation"]["covered_by_current_cumulative"] is True, report["obligation"]
    return report


# ---------------------------------------------------------------------------
# R1 -- modes that run no check, and assertion-stripping composed with pytest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "python3 -VV verify.py",     # version twice: prints the build and exits
    "python3 -BV verify.py",     # a version flag clustered behind a safe flag
    "python3 -? verify.py",      # the help alias
    "python3 -J verify.py",      # a reserved option: a usage error, nothing runs
])
def test_interpreter_modes_that_exit_before_the_entry_are_diagnostics_novel(rate_project, command):
    task = _drive_to_narrow(rate_project, "exit-modes", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    outcome = task.step(RUN, {"command": command})
    assert outcome.details["executed"] is True, outcome.status
    entry = _last(task, "narrow_test")
    assert entry["obligation_match"] is False, entry
    assert entry["invocation"]["family"] == "python-diagnostic" and entry["invocation"]["entry"] == "", entry
    journal = _journal(task.id)
    assert journal["stage"] == "narrow_test" and journal["narrow"] is None, journal["stage"]
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "unresolved"


@pytest.mark.parametrize("command", [
    "python3 -c'print(7)' verify.py",   # inline code with its value ATTACHED; verify.py is only argv
    "python3 -mjson.tool verify.py",    # a module entry with its value attached
])
def test_attached_inline_or_module_entries_are_not_the_named_script(rate_project, command):
    task = _drive_to_narrow(rate_project, "attached-entry", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    assert task.step(RUN, {"command": command}).details["executed"] is True
    entry = _last(task, "narrow_test")
    assert entry["obligation_match"] is False, entry
    assert entry["invocation"]["entry"] != "verify.py", entry
    assert _journal(task.id)["narrow"] is None


@pytest.mark.parametrize("command", [
    f"{sys.executable} -O -m pytest -q tests/test_ledger.py",                  # rewrite mode: helper assert stripped
    f"{sys.executable} -OO -m pytest -q --assert plain tests/test_ledger.py",  # no assertion executes at all
])
def test_assertion_stripping_modes_cannot_certify_a_pytest_obligation_novel(ledger_project, command):
    task = _ledger(ledger_project, "optimized-pytest")
    green = task.step(RUN, {"command": command})
    assert green.details["tool_result"]["success"] is True, green.details["tool_result"]  # really green, wrong repair
    entry = _last(task, "narrow_test")
    assert entry["covers_obligation"] is False and "-O" in entry["stale_reason"], entry
    report = _finish_with(task, {"command": command})
    assert report["verdict"] == "unresolved", report
    assert (ledger_project / "ledger.py").read_text(encoding="utf-8") == LEDGER_WRONG


@pytest.mark.parametrize("option", ["--setup-only", "--version"])
def test_pytest_modes_that_run_no_test_cannot_certify(ledger_project, option):
    task = _ledger(ledger_project, "inert-pytest")
    command = f"python3 -m pytest -q {option} tests/test_ledger.py"
    assert task.step(RUN, {"command": command}).details["tool_result"]["success"] is True
    entry = _last(task, "narrow_test")
    assert entry["covers_obligation"] is False and "does not execute" in entry["stale_reason"], entry
    assert _finish_with(task, {"command": command})["verdict"] == "unresolved"


def test_plain_assertion_mode_without_optimization_still_certifies(ledger_project):
    """Preservation: ``--assert=plain`` alone still executes every assertion (measured), so it
    covers; ``-p no:cacheprovider`` is documented as cosmetic when no cache filter is used."""
    task = _ledger(ledger_project, "plain-assert", fixed=True)
    assert task.step(RUN, {"command": RETAINED}).details["stage"] == "cumulative"
    _completes(task, {"command": "python3 -m pytest -q --assert=plain -p no:cacheprovider tests/test_ledger.py"})


def test_unknown_coverage_holds_the_full_check_and_names_the_retained_check(rate_project):
    """A GENUINELY repaired task whose full check's coverage is unknown must not be stranded:
    commands are lawful only up to ``cumulative``, so advancing past it on unknown coverage left
    a report that could never complete. The checkpoint holds, the result names the retained
    check as the lawful next action, an identical repeat is corrected with the same action, and
    the retained check then completes the SAME task."""
    task = _drive_to_narrow(rate_project, "unknown-hold", command="python3 verify.py", cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_FIXED)
    assert task.step(RUN, {"command": "python3 verify.py"}).details["stage"] == "cumulative"
    unknown = task.step(RUN, {"command": "python3 -X dev verify.py"})
    assert unknown.details["tool_result"]["success"] is True
    assert unknown.details["verification"]["covers_obligation"] is None, unknown.details["verification"]
    assert unknown.details["stage"] == "cumulative", unknown.details["stage"]
    owed = unknown.details["coverage_owed"]
    assert owed["retained_command"] == "python3 verify.py", owed
    assert any("python3 verify.py" in action for action in unknown.details["next"]), unknown.details["next"]
    repeat = task.step(RUN, {"command": "python3 -X dev verify.py"})
    assert repeat.status == "repeated_verification" and repeat.details["executed"] is False
    assert "python3 verify.py" in repeat.response_text and "does not cover" in repeat.response_text
    assert any("python3 verify.py" in action for action in repeat.details["next"]), repeat.details["next"]
    report = _door("code.task.report", {"task_id": task.id}, task.ctx).details
    assert report["verdict"] == "unresolved" and report["obligation"]["coverage_reason"], report["obligation"]
    retained = task.step(RUN, {"command": "python3 verify.py"})
    assert retained.details["stage"] == "inspect_diff", retained.details.get("verification")
    assert retained.details["coverage_owed"] is None
    assert task.step("workspace.git_diff", {}).ok
    assert _door("code.task.report", {"task_id": task.id}, task.ctx).details["verdict"] == "completed"


# ---------------------------------------------------------------------------
# R2 -- argv order, option occurrence order and argument VALUES are the check
# ---------------------------------------------------------------------------

VERIFY_MODES = (
    "import sys\n"
    "from rate import gross\n"
    "if sys.argv[1] in ('strict', 'mode=strict'):\n"
    "    assert gross(100, 20) == 120, gross(100, 20)\n"
    "print('verified', sys.argv[1:])\n"
)


@pytest.mark.parametrize("retained,changed", [
    ("python3 verify_modes.py strict lenient", "python3 verify_modes.py lenient strict"),
    ("python3 verify_modes.py mode=strict", "python3 verify_modes.py mode=lenient"),
])
def test_program_argument_order_and_values_cannot_be_swapped_novel(rate_project, retained, changed):
    (rate_project / "verify_modes.py").write_text(VERIFY_MODES, encoding="utf-8")
    task = _drive_to_narrow(rate_project, "argv-values", command=retained, cwd=None,
                            owner="rate.py", buggy=RATE_BUGGY, repair=RATE_WRONG)
    report = _finish_with(task, {"command": changed})
    assert report["verdict"] == "unresolved", report
    assert _last(task, "cumulative")["covers_obligation"] is not True
    assert (rate_project / "rate.py").read_text(encoding="utf-8") == RATE_WRONG


def test_reordered_filters_run_as_a_different_check_and_identical_repeats_are_corrected(ledger_project):
    task = _ledger(ledger_project, "reordered-filters")
    assert task.step(RUN, {"command": RETAINED + " -k test_empty"}).details["stage"] == "cumulative"
    first = RETAINED + " -k test_settles -k test_empty"   # argparse store: the LAST -k decides -> test_empty
    held = task.step(RUN, {"command": first})
    assert held.details["tool_result"]["success"] is True and held.details["stage"] == "cumulative"
    assert held.details["verification"]["covers_obligation"] is False
    repeat = task.step(RUN, {"command": first})
    assert repeat.status == "repeated_verification" and repeat.details["executed"] is False
    retained = held.details["coverage_owed"]["retained_command"]
    assert retained.endswith("-m pytest -q tests/test_ledger.py"), retained
    assert any(retained in action for action in repeat.details["next"]), repeat.details["next"]
    swapped = task.step(RUN, {"command": RETAINED + " -k test_empty -k test_settles"})  # -> test_settles
    assert swapped.status != "repeated_verification" and swapped.details["executed"] is True, swapped.status
    assert swapped.details["tool_result"]["success"] is False  # the materially changed check runs and fails
    journal = _journal(task.id)
    assert journal["stage"] == "cumulative" and journal["cumulative"]["success"] is False


def test_repeat_identity_keeps_order_values_and_interpreter():
    from core.code_assistant.task_runtime import _command_selection, _effective_invocation

    distinct = [
        ("python3 verify.py strict lenient", "python3 verify.py lenient strict"),
        ("python3 verify.py mode=strict", "python3 verify.py mode=lenient"),
        ("pytest tests/test_ledger.py -m slow -m smoke", "pytest tests/test_ledger.py -m smoke -m slow"),
        ("pytest --deselect tests/a.py::x --deselect tests/a.py::y",
         "pytest --deselect tests/a.py::y --deselect tests/a.py::x"),
        ("pytest tests/a.py tests/b.py", "pytest tests/b.py tests/a.py"),
        ("cat ledger.py rate.py", "cat rate.py ledger.py"),
        ("grep -e credit -e debit ledger.py", "grep -e debit -e credit ledger.py"),
        ("python3.11 -m pytest -q tests", "python3 -m pytest -q tests"),
        ("python3 -V verify.py", "python3 verify.py"),
    ]
    assert [pair for pair in distinct if _command_selection(pair[0]) == _command_selection(pair[1])] == []
    cosmetic = [
        ("pytest -q tests/test_ledger.py", "pytest tests/test_ledger.py -q"),  # argparse interleaving
        ("pytest -kalpha tests", "pytest -k alpha tests"),                    # attached short value
        ("pytest -k=alpha tests", "pytest -k alpha tests"),
        ("python3 -m pytest -q tests", "pytest -q tests"),  # the validation runner executes both identically
    ]
    assert [pair for pair in cosmetic if _command_selection(pair[0]) != _command_selection(pair[1])] == []
    invocation = _effective_invocation("pytest tests -k alpha -m 'slow and not smoke' -k apple")
    assert invocation.option_values("-k") == ("alpha", "apple")
    assert invocation.option_values("-m") == ("slow and not smoke",)
    assert invocation.last_value("-k") == "apple"


# ---------------------------------------------------------------------------
# R3 -- canonical, cwd-resolved pytest selection with node identity and real collection scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retained,verification", [
    ("tests/test_ledger.py::TestBalance::test_settles", "tests/test_ledger.py::TestBalance::test_empty"),
    ("tests/test_ledger.py::test_grid[50-20-30]", "tests/test_ledger.py::test_grid[0-0-0]"),
])
def test_a_sibling_node_or_parametrization_cannot_cover_the_retained_node_novel(ledger_project, retained,
                                                                                verification):
    task = _ledger(ledger_project, "sibling-node", command=f"python3 -m pytest -q {retained}")
    report = _finish_with(task, {"command": f"python3 -m pytest -q {verification}"})
    assert report["verdict"] == "unresolved", report
    entry = _last(task, "cumulative")
    assert entry["success"] is True and entry["covers_obligation"] is False, entry


@pytest.mark.parametrize("retained,broader", [
    ("tests/test_ledger.py::test_grid[50-20-30]", "tests/test_ledger.py::test_grid"),
    ("tests/test_ledger.py::TestBalance::test_settles", "tests/test_ledger.py::TestBalance"),
    ("tests/test_ledger.py::TestBalance::test_settles", "tests"),
])
def test_an_enclosing_node_file_or_directory_covers_the_retained_node(ledger_project, retained, broader):
    task = _ledger(ledger_project, "enclosing-node", command=f"python3 -m pytest -q {retained}", fixed=True)
    assert task.step(RUN, {"command": f"python3 -m pytest -q {retained}"}).details["stage"] == "cumulative"
    _completes(task, {"command": f"python3 -m pytest -q {broader}"})


@pytest.fixture
def service_project(ledger_project: Path) -> Path:
    """``svc/`` owns its own ledger and tests; the ROOT ``tests/test_ledger.py`` is an unrelated
    passing file with the same spelling."""
    svc = ledger_project / "svc"
    (svc / "tests").mkdir(parents=True)
    (svc / "ledger.py").write_text(LEDGER_BUGGY, encoding="utf-8")
    (svc / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (svc / "tests" / "ledger_checks.py").write_text(LEDGER_CHECKS, encoding="utf-8")
    (svc / "tests" / "test_ledger.py").write_text(LEDGER_TESTS, encoding="utf-8")
    (ledger_project / "tests" / "test_ledger.py").write_text(PASSING, encoding="utf-8")
    # the sandboxed interpreter resolves `-m pytest` imports from the process cwd only (see
    # _boot_rig's seeding note); the svc/ cwd needs the importable set beside it
    from tests.test_code_task_purposeful_verification import _seed_sandbox_pytest_into
    _seed_sandbox_pytest_into(ledger_project / "svc")
    return ledger_project


def test_the_same_spelled_selection_under_another_directory_cannot_cover_novel(service_project):
    task = _ledger(service_project, "other-directory", cwd="svc", owner="svc/ledger.py")
    report = _finish_with(task, {"command": RETAINED})  # the root's same-spelled passing file
    assert report["verdict"] == "unresolved", report
    entry = _last(task, "cumulative")
    assert entry["success"] is True and entry["covers_obligation"] is False, entry


@pytest.mark.parametrize("args", [
    {"command": "python3 -m pytest -q svc/tests/test_ledger.py"},  # the same file, spelled from the root
    {"command": RETAINED, "cwd": "./svc/"},                        # a cwd alias of the same directory
])
def test_the_same_file_through_another_spelling_still_covers(service_project, args):
    task = _ledger(service_project, "same-file-alias", cwd="svc", owner="svc/ledger.py", fixed=True)
    assert task.step(RUN, {"command": RETAINED, "cwd": "svc"}).details["stage"] == "cumulative"
    _completes(task, args)


def test_the_full_suite_form_covers_only_the_directory_it_collects(ledger_project):
    decoy = ledger_project / "decoy" / "tests"
    decoy.mkdir(parents=True)
    (decoy / "test_other.py").write_text(PASSING, encoding="utf-8")
    from tests.test_code_task_purposeful_verification import _seed_sandbox_pytest_into
    _seed_sandbox_pytest_into(ledger_project / "decoy")
    task = _ledger(ledger_project, "full-suite-elsewhere")
    report = _finish_with(task, {"cwd": "decoy"})  # the validation tool's own full-suite form, run elsewhere
    assert report["verdict"] == "unresolved", report
    entry = _last(task, "cumulative")
    assert entry["success"] is True and entry["covers_obligation"] is False, entry


@pytest.mark.parametrize("layout", ["python_files", "norecursedirs", "testpaths", "conftest"])
def test_a_recursive_run_that_would_not_collect_the_obligation_cannot_cover(project, layout):
    """Each broader run is really green under the wrong repair, and each is a scope pytest itself
    would NOT collect the retained file from (measured on 9.1.0): an explicit-only file name, a
    ``norecursedirs`` directory, an ini ``testpaths`` restriction, a conftest ``collect_ignore``.
    The first three are provable misses; conftest code is Python the contract cannot evaluate,
    so it stays unknown, holds the checkpoint and names the retained check."""
    (project / "ledger.py").write_text(LEDGER_BUGGY, encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_misc.py").write_text(PASSING, encoding="utf-8")
    retained, verification, keyword = {
        "python_files": ("tests/ledger_acceptance.py", "python3 -m pytest -q tests", "python_files"),
        "norecursedirs": ("build/test_ledger.py", "python3 -m pytest -q", "norecursedirs"),
        "testpaths": ("extra/test_ledger.py", "python3 -m pytest -q", "testpaths"),
        "conftest": ("tests/test_ledger.py", "python3 -m pytest -q tests", "conftest"),
    }[layout]
    target = project / retained
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(STANDALONE, encoding="utf-8")
    if layout == "testpaths":
        (project / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    if layout == "conftest":
        (project / "tests" / "conftest.py").write_text('collect_ignore = ["test_ledger.py"]\n', encoding="utf-8")
    task = _ledger(project, f"recursion-{layout}", command=f"python3 -m pytest -q {retained}")
    report = _finish_with(task, {"command": verification})
    assert report["verdict"] == "unresolved", report
    entry = _last(task, "cumulative")
    assert entry["success"] is True, entry
    assert entry["covers_obligation"] is (None if layout == "conftest" else False), entry
    assert keyword in entry["stale_reason"], entry["stale_reason"]
    if layout == "conftest":
        assert report["coverage_owed"]["retained_command"].endswith(retained), report.get("coverage_owed")


def test_recursive_scopes_cover_what_they_collect_beside_a_fixture_only_conftest(ledger_project):
    """Positive control against blanket unknown: a conftest that only defines fixtures does not
    change collection, and the full-suite form at the root covers a retained node."""
    (ledger_project / "tests" / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef zero():\n    return 0\n", encoding="utf-8")
    node = "python3 -m pytest -q tests/test_ledger.py::TestBalance::test_settles"
    task = _ledger(ledger_project, "fixture-conftest", command=node, fixed=True)
    assert task.step(RUN, {"command": node}).details["stage"] == "cumulative"
    _completes(task, {"command": "python3 -m pytest -q"})


# ---------------------------------------------------------------------------
# R4 -- whole option values, repeated-option semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retained_filter,verification_filter", [
    ("-m slow", "-m smoke"),               # same first character, a different marker
    ("-k test_settles", "-k test_empty"),  # same first character, a different test
    ("-k settles", "-k empty"),            # different prefixes stay different (control)
])
def test_filter_values_are_compared_whole_novel(ledger_project, retained_filter, verification_filter):
    task = _ledger(ledger_project, "whole-values", command=f"{RETAINED} {retained_filter}")
    report = _finish_with(task, {"command": f"{RETAINED} {verification_filter}"})
    assert report["verdict"] == "unresolved", report
    entry = _last(task, "cumulative")
    assert entry["success"] is True and entry["covers_obligation"] is False, entry
    assert "filters the suite" in entry["stale_reason"], entry["stale_reason"]


@pytest.mark.parametrize("broader", [
    f"{RETAINED} -k test_settles",                           # the identical filter
    "python3 -m pytest -q tests -k test_empty -k test_settles",  # repeated -k: the last one decides
    RETAINED,                                                # the unfiltered file is a superset
])
def test_equivalent_or_broader_filters_still_cover(ledger_project, broader):
    retained = f"{RETAINED} -k test_settles"
    task = _ledger(ledger_project, "equivalent-filter", command=retained, fixed=True)
    assert task.step(RUN, {"command": retained}).details["stage"] == "cumulative"
    _completes(task, {"command": broader})
