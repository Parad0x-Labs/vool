"""Selftests and deliberate negative controls for ops/local_ci_reproduction.py.

Every control proves one honesty law of the runner on a tiny synthetic
repository (real git repo, real copies of the CI instruments, real pytest
runs -- nothing mocked):

  * a passing shard exits 0 and reports completed-clean with denominators;
  * a failing test stays failed: exit 1, the failed nodeid and its first
    error text are recorded, never softened;
  * a collection error is exit 2 and verdict ``collection-error``, never a
    pass, never silently dropped;
  * a wall-budget interruption is recorded as interrupted/incomplete with
    the watchdog's own verdict -- completion is never claimed;
  * a hanging phase is bounded by the watchdog: exit 124, stalled, incomplete;
  * a selection naming a missing file is refused BEFORE execution;
  * source that changed after the pin is refused (changed-source detection);
  * macOS routing and its drift are recorded, and the tool's pinned routing
    set cannot drift from .github/workflows/ci.yml;
  * two different selections are never conflated and never compared (on two
    EXECUTED receipts, so the refusal is for the mismatch alone);
  * the baseline/candidate comparison reports failed nodes and first errors
    without exonerating anything;
  * fail-closed harvest: a session that hard-exits mid-run (status 0 or 1)
    and evidence that is missing or malformed (watchdog, timing, JUnit) or
    disagreeing never yield a completed verdict or exit 0 -- a receipt is
    always written, verdict harvest-failed/-inconsistent, exit 92, unknown
    totals null; valid but incomplete or unrelated JUnit (empty, a case
    missing, a file outside the selection, another session, renamed nodes,
    swapped node outcomes) is never completed evidence, and a missing or
    malformed node ledger fails closed; nested classes, setup/teardown errors, skips, xfails
    and xpasses reconcile per file and two such red runs compare cleanly;
  * compare refuses empty, wrong-schema, refused, interrupted, aborted,
    unreadable and coverage-less or coverage-inconsistent receipts with a
    non-zero exit and no node diff;
  * the default canonical path executes; a failed or timed-out canonical
    collection is refused before execution, bounded by --collect-timeout or
    --wall-budget, with the collection's process group stopped;
  * an empty selection is refused before execution; a zero-test selection is
    no-tests-collected, exit 5; pytest exit 2 without collection errors is a
    session interruption and exit 3 an internal error, never completed;
  * a reused label is refused, so an aborted run can never harvest an
    earlier run's evidence;
  * first errors are compared raw AND normalised (paths, timestamps, ids),
    with excerpt truncation flagged.

Fault-injection controls replace ONE instrument in the synthetic repository
with a wrapper that runs the real instrument and then removes or corrupts its
evidence file; that is the fault under test, and it is labelled where used.

These are exposed development controls: they prove the runner's behavior on
fixtures built for the purpose, and are never a score on any unseen set.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _REPO_ROOT / "ops" / "local_ci_reproduction.py"
_OPS_INSTRUMENTS = (
    "ops/__init__.py",
    "ops/pytest_watchdog.py",
    "ops/pytest_timing.py",
    "ops/pytest_execution.py",
    "ops/pytest_manifest.py",
    "ops/shard_resolver.py",
    "ops/shard_plan.py",
)

PASSING_TEST = "def test_ok():\n    assert 1 + 1 == 2\n"
FAILING_TEST = "def test_broken():\n    assert 1 + 1 == 3, 'sum law violated'\n"
BROKEN_IMPORT_TEST = "import module_that_does_not_exist_xyz\n\n\ndef test_any():\n    pass\n"
SLEEPING_TEST = "import time\n\n\ndef test_sleeps():\n    time.sleep(600)\n"
FLAG_TEST = (
    "from pathlib import Path\n\n\n"
    "def test_flag():\n"
    "    flag = Path(__file__).resolve().parents[1] / 'flag.txt'\n"
    "    assert flag.read_text(encoding='utf-8').strip() == 'pass', 'flag is not pass'\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True,
                   timeout=60)


def make_synthetic_repo(tmp_path: Path, files: dict[str, str], *,
                        fault_injection: dict[str, str] | None = None) -> Path:
    """A real git repo carrying the repo's own CI instruments plus fixture tests.

    ``fault_injection`` replaces one instrument with a wrapper (FAULT INJECTION,
    labelled at each use); a replaced ``ops/pytest_timing.py`` keeps the real
    instrument beside it as ``ops/_real_pytest_timing.py`` for the wrapper."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ops").mkdir()
    (repo / "tests").mkdir()
    for relative in _OPS_INSTRUMENTS:
        source = _REPO_ROOT / relative
        assert source.is_file(), f"instrument missing from repo: {relative}"
        (repo / relative).write_bytes(source.read_bytes())
    for relative, content in (fault_injection or {}).items():
        if relative == "ops/pytest_timing.py":
            (repo / "ops" / "_real_pytest_timing.py").write_bytes(
                (_REPO_ROOT / relative).read_bytes())
        (repo / relative).write_text(content, encoding="utf-8")
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # Ignore bytecode exactly as the real repo's .gitignore does: the runner's own collection
    # imports the fixture tests, and on a host with no global ignore their __pycache__ would
    # otherwise read as a dirty tree and refuse every pinned run.
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "sls_0x")
    _git(repo, "config", "user.email", "sls_0x@users.noreply.github.com")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "synthetic fixture")
    assert not _git_status_dirty(repo)
    return repo


def _git_status_dirty(repo: Path) -> bool:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo),
                            capture_output=True, text=True, timeout=60)
    return bool(result.stdout.strip())


def run_runner(tmp_path: Path, repo: Path, label: str, selection: Path | None,
               *extra: str, timeout: float = 300.0) -> tuple[int, dict, Path]:
    """Invoke the runner as a real subprocess; return (exit, RESULT.json, run dir).

    ``selection=None`` runs the canonical (collected) selection. Every receipt
    must record the runner's own exit, and the process must have exited with
    exactly that code -- for executed and refused runs alike."""

    artifacts = tmp_path / "artifacts"
    profiles = tmp_path / "profiles"
    command = [sys.executable, str(_RUNNER),
               "--repo", str(repo), "--artifacts", str(artifacts),
               "--profiles", str(profiles),
               "run", "--label", label,
               *(["--selection-explicit", str(selection)] if selection is not None else []),
               "--selection-label", f"synthetic-{label}",
               *extra]
    process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    run_dir = artifacts / label
    result_path = run_dir / "RESULT.json"
    assert result_path.is_file(), (
        f"runner left no RESULT.json (exit {process.returncode}); "
        f"stderr:\n{process.stderr}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert process.returncode == result.get("runner_exit"), (
        f"runner exit {process.returncode} disagrees with RESULT runner_exit "
        f"{result.get('runner_exit')}")
    return process.returncode, result, run_dir


def run_compare(tmp_path: Path, baseline: Path, candidate: Path,
                name: str) -> tuple[int, dict]:
    output = tmp_path / "compare" / f"{name}.json"
    process = subprocess.run(
        [sys.executable, str(_RUNNER), "--artifacts", str(tmp_path / "artifacts"),
         "--profiles", str(tmp_path / "profiles"), "compare",
         "--baseline", str(baseline), "--candidate", str(candidate),
         "--output", str(output)],
        capture_output=True, text=True, timeout=60)
    assert output.is_file(), f"compare left no output; stderr:\n{process.stderr}"
    return process.returncode, json.loads(output.read_text(encoding="utf-8"))


def write_selection(tmp_path: Path, name: str, files: list[str]) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(files) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Controls: outcomes and exits
# --------------------------------------------------------------------------- #

def test_pass_control_completed_clean(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    exit_code, result, run_dir = run_runner(tmp_path, repo, "pass", selection)
    assert result["verdict"] == "completed-clean"
    assert exit_code == 0 and result["exit_code"] == 0
    assert result["complete"] is True
    assert result["failed_nodes"] == {}
    totals = result["timing_summary"]["outcome_totals"]
    assert totals["passed"] >= 1 and totals["failed"] == 0
    assert (run_dir / "run-stdout.txt").is_file()
    assert (run_dir / "run-stderr.txt").is_file()


def test_fail_control_preserves_failure_and_first_error(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": FAILING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, "fail", selection)
    assert exit_code == 1 and result["exit_code"] == 1
    assert result["verdict"] == "completed-with-failures"
    assert list(result["failed_nodes"]) == ["tests/test_alpha.py::test_broken"]
    first_error = result["failed_nodes"]["tests/test_alpha.py::test_broken"]["first_error"]
    assert "sum law violated" in first_error


def test_collection_error_control_is_never_a_pass(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_broken_import.py": BROKEN_IMPORT_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_broken_import.py"])
    exit_code, result, run_dir = run_runner(tmp_path, repo, "collerr", selection)
    assert exit_code == 2 and result["exit_code"] == 2
    assert result["verdict"] == "collection-error"
    raw = ((run_dir / "run-stdout.txt").read_text(encoding="utf-8", errors="replace")
           + (run_dir / "run-stderr.txt").read_text(encoding="utf-8", errors="replace"))
    assert "ModuleNotFoundError" in raw


def test_wall_budget_interruption_is_incomplete(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_sleep.py": SLEEPING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_sleep.py"])
    exit_code, result, _ = run_runner(
        tmp_path, repo, "interrupt", selection, "--wall-budget", "6", timeout=120)
    assert exit_code == 130 and result["exit_code"] == 130
    assert result["verdict"] == "interrupted-by-wall-budget"
    assert result["complete"] is False
    assert result["watchdog"]["interrupted"] is True


def test_hanging_phase_bounded_by_watchdog(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_hang.py": SLEEPING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_hang.py"])
    exit_code, result, _ = run_runner(
        tmp_path, repo, "hang", selection, "--phase-timeout", "4", timeout=120)
    assert exit_code == 124 and result["exit_code"] == 124
    assert result["verdict"] == "stalled-phase-timeout"
    assert result["complete"] is False
    assert result["watchdog"]["timed_out"] is True


# --------------------------------------------------------------------------- #
# Controls: refusals before claiming execution
# --------------------------------------------------------------------------- #

def test_missing_file_refused_before_execution(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    selection = write_selection(tmp_path, "sel.txt",
                                ["tests/test_alpha.py", "tests/test_absent.py"])
    exit_code, result, run_dir = run_runner(tmp_path, repo, "missing", selection)
    assert result["verdict"] == "refused"
    assert result["executed"] is False
    assert any("missing file" in refusal for refusal in result["refusals"])
    assert exit_code == 90
    assert not (run_dir / "run-stdout.txt").exists()


def test_changed_source_refused_against_pin(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    run_runner(tmp_path, repo, "pin-a", selection)
    fingerprints = tmp_path / "artifacts" / "pin-a" / "fingerprints.json"
    assert fingerprints.is_file()
    (repo / "tests" / "test_alpha.py").write_text(FAILING_TEST, encoding="utf-8")
    exit_code, result, _ = run_runner(
        tmp_path, repo, "pin-b", selection,
        "--require-file-hashes", str(fingerprints), "--allow-dirty")
    assert result["verdict"] == "refused"
    assert any("source changed since pin" in refusal for refusal in result["refusals"])
    assert exit_code == 91


def test_head_pin_refuses_different_head(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    exit_code, result, _ = run_runner(
        tmp_path, repo, "headpin", selection, "--require-head", "0" * 40)
    assert result["verdict"] == "refused" and exit_code in (90, 91)
    assert any("pinned" in refusal for refusal in result["refusals"])


# --------------------------------------------------------------------------- #
# Routing, selection identity, comparison
# --------------------------------------------------------------------------- #

def test_macos_routing_and_drift_recorded(tmp_path):
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_alpha.py": PASSING_TEST,
        "tests/test_macos_app_packaging.py": PASSING_TEST,
        "tests/wallet/test_wallet_approval_methods.py": BROKEN_IMPORT_TEST,
    })
    artifacts = tmp_path / "collect-artifacts"
    process = subprocess.run(
        [sys.executable, str(_RUNNER), "--repo", str(repo),
         "--artifacts", str(artifacts), "collect"],
        capture_output=True, text=True, timeout=300)
    # The drifted fixture makes collection itself error (exit 2 from pytest);
    # the collect subcommand reports that honestly as a nonzero exit while
    # still writing COLLECTION.json.
    assert process.returncode == 1, process.stderr
    payload = json.loads((artifacts / "COLLECTION.json").read_text(encoding="utf-8"))
    assert payload["collection"]["collection_command_exit"] == 2
    collection = payload["collection"]
    assert collection["macos_routed_out"] == ["tests/test_macos_app_packaging.py"]
    # The dep-broken macOS file is NOT silently repaired into the linux set:
    # it is recorded as local drift.
    assert "tests/wallet/test_wallet_approval_methods.py" in \
        collection["macos_only_not_collected_locally"]
    linux = (artifacts / "linux-collected.txt").read_text(encoding="utf-8").splitlines()
    assert linux == ["tests/test_alpha.py"]


def test_routing_set_pinned_to_ci_workflow():
    workflow = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    match = re.search(r"macos_only = \{(.*?)\}", workflow, re.S)
    assert match, "ci.yml macos_only set not found"
    workflow_set = set(re.findall(r'"([^"]+\.py)"', match.group(1)))
    from ops.local_ci_reproduction import MACOS_ONLY_FILES
    assert workflow_set == set(MACOS_ONLY_FILES), (
        f"routing drift between ci.yml and local_ci_reproduction.py: "
        f"{workflow_set ^ set(MACOS_ONLY_FILES)}")


def test_different_selections_never_conflate():
    from ops.local_ci_reproduction import build_selection
    first = build_selection(source="explicit-filelist", label="a",
                            files=["tests/a.py"], provenance="x")
    second = build_selection(source="explicit-filelist", label="a",
                             files=["tests/b.py"], provenance="x")
    assert first["sha256"] != second["sha256"]


def test_compare_reports_failed_nodes_and_first_errors(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_flag.py": FLAG_TEST,
                                          "flag.txt": "fail"})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_flag.py"])
    run_runner(tmp_path, repo, "cmp-base", selection)
    (repo / "flag.txt").write_text("pass", encoding="utf-8")
    run_runner(tmp_path, repo, "cmp-cand", selection, "--allow-dirty")
    artifacts = tmp_path / "artifacts"
    output = artifacts / "compare.json"
    process = subprocess.run(
        [sys.executable, str(_RUNNER), "compare",
         "--baseline", str(artifacts / "cmp-base" / "RESULT.json"),
         "--candidate", str(artifacts / "cmp-cand" / "RESULT.json"),
         "--output", str(output)],
        capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    comparison = json.loads(output.read_text(encoding="utf-8"))
    assert comparison["problems"] == []
    assert [entry["nodeid"] for entry in comparison["failed_only_in_baseline"]] == \
        ["tests/test_flag.py::test_flag"]
    assert comparison["failed_only_in_candidate"] == []
    assert "sum law" not in json.dumps(comparison)
    assert "flag is not pass" in comparison["failed_only_in_baseline"][0]["first_error"]


def test_compare_refuses_different_selections(tmp_path):
    # Both receipts EXECUTED and complete (clean committed repo), so the only
    # thing standing between them is the selection: the refusal must be the
    # mismatch alone, not a never-executed receipt.
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_flag.py": FLAG_TEST,
        "tests/test_alpha.py": PASSING_TEST,
        "flag.txt": "fail",
    })
    first = write_selection(tmp_path, "one.txt", ["tests/test_flag.py"])
    second = write_selection(tmp_path, "two.txt", ["tests/test_alpha.py"])
    _, base, _ = run_runner(tmp_path, repo, "mix-base", first)
    _, cand, _ = run_runner(tmp_path, repo, "mix-cand", second)
    assert base["executed"] is True and base["verdict"] == "completed-with-failures"
    assert cand["executed"] is True and cand["verdict"] == "completed-clean"
    artifacts = tmp_path / "artifacts"
    exit_code, comparison = run_compare(tmp_path, artifacts / "mix-base" / "RESULT.json",
                                        artifacts / "mix-cand" / "RESULT.json", "mix")
    assert exit_code == 1
    assert comparison["comparable"] is False
    assert len(comparison["problems"]) == 1
    assert comparison["problems"][0].startswith("selection mismatch")
    assert comparison["failed_only_in_baseline"] == [] and comparison["failed_in_both"] == []


def test_junit_first_error_has_real_text(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": FAILING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    _, result, _ = run_runner(tmp_path, repo, "junit", selection)
    entry = result["failed_nodes"]["tests/test_alpha.py::test_broken"]
    assert "AssertionError" in entry["first_error"] or "assert" in entry["first_error"]
    assert entry["timing"]["nodeid"] == "tests/test_alpha.py::test_broken"


# --------------------------------------------------------------------------- #
# Controls: fail-closed harvest (a completed verdict is earned from evidence)
# --------------------------------------------------------------------------- #

HARD_EXIT_0_TEST = (
    "import os\n\n\n"
    "def test_one():\n    assert True\n\n\n"
    "def test_two():\n    os._exit(0)\n\n\n"
    "def test_three():\n    assert False, 'never reached'\n"
)
FAIL_THEN_HARD_EXIT_1_TEST = (
    "import os\n\n\n"
    "def test_one():\n    assert 1 == 2, 'deliberate failure'\n\n\n"
    "def test_two():\n    os._exit(1)\n"
)
MIXED_OUTCOMES_TEST = '''import pytest


class TestOuter:
    class TestInner:
        def test_same(self):
            assert False, "inner failure"

    def test_same(self):
        assert False, "outer failure"


@pytest.fixture
def broken_setup():
    raise RuntimeError("setup failure")


def test_setup_error(broken_setup):
    pass


@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("teardown failure")


def test_fails_then_teardown_errors(broken_teardown):
    assert False, "call failure"


def test_passes_then_teardown_errors(broken_teardown):
    assert True


def test_passes():
    assert True


@pytest.mark.skip(reason="marker skip")
def test_marker_skip():
    pass


def test_call_skip():
    pytest.skip("call skip")


@pytest.mark.xfail(reason="expected failure")
def test_xfail():
    assert False


@pytest.mark.xfail(run=False, reason="not run")
def test_xfail_not_run():
    pass


def test_imperative_xfail():
    pytest.xfail("imperative")


@pytest.mark.xfail(reason="unexpected pass")
def test_xpass():
    assert True


@pytest.mark.xfail(reason="strict", strict=True)
def test_xpass_strict():
    assert True


@pytest.mark.parametrize("value", [1, 2])
def test_param(value):
    assert value
'''
TWO_PASSING_TESTS = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n"
SKIP_THEN_XFAIL_TESTS = (
    "import pytest\n\n\n"
    "@pytest.mark.skip(reason='skipped')\ndef test_skipped():\n    pass\n\n\n"
    "@pytest.mark.xfail(reason='expected')\ndef test_xfailed():\n    assert False\n"
)

# FAULT INJECTION wrappers: each runs the real instrument (or, for the
# watchdog, the real wrapped command) and then removes or corrupts exactly one
# evidence file.
_WATCHDOG_WRAPPER = '''import json, subprocess, sys
from pathlib import Path
argv = sys.argv[1:]
out = Path(argv[argv.index("--output") + 1]); out.mkdir(parents=True, exist_ok=True)
code = subprocess.run(argv[argv.index("--") + 1:]).returncode
if {mode!r} == "malformed":
    (out / "result.json").write_text("{{not json", encoding="utf-8")
sys.exit(code)
'''
_TIMING_WRAPPER = '''import json, subprocess, sys
from pathlib import Path
argv = sys.argv[1:]
real = Path(__file__).with_name("_real_pytest_timing.py")
code = subprocess.run([sys.executable, str(real), *argv]).returncode
rest = argv[argv.index("--") + 1:]
timing = Path(argv[argv.index("--output") + 1])
junit = Path(rest[rest.index("--junitxml") + 1])
mode = {mode!r}
if mode == "junit-malformed":
    junit.write_text("<?xml version='1.0'?><testsuites><testsuite name='x'", encoding="utf-8")
elif mode == "junit-missing":
    junit.unlink()
elif mode == "timing-malformed":
    timing.write_text("{{", encoding="utf-8")
elif mode == "timing-missing":
    timing.unlink()
elif mode == "ledger-missing":
    timing.with_name("nodes.json").unlink()
elif mode == "ledger-malformed":
    timing.with_name("nodes.json").write_text("{{", encoding="utf-8")
elif mode in ("junit-renamed", "junit-swap-names"):
    import xml.etree.ElementTree as ET
    junit.with_name("junit-original.xml").write_bytes(junit.read_bytes())
    tree = ET.parse(junit)
    cases = list(tree.getroot().iter("testcase"))
    if mode == "junit-renamed":
        for number, case in enumerate(cases):
            case.set("name", "test_never_executed_" + str(number))
    else:
        first, second = cases[0].get("name"), cases[1].get("name")
        cases[0].set("name", second)
        cases[1].set("name", first)
    tree.write(junit, encoding="utf-8", xml_declaration=True)
elif mode in ("junit-empty", "junit-partial", "junit-foreign", "junit-other-session"):
    import datetime
    import xml.etree.ElementTree as ET
    junit.with_name("junit-original.xml").write_bytes(junit.read_bytes())
    tree = ET.parse(junit)
    for suite in tree.getroot().iter("testsuite"):
        cases = suite.findall("testcase")
        if mode == "junit-empty":
            for case in cases:
                suite.remove(case)
            suite.set("tests", "0")
        elif mode == "junit-partial":
            suite.remove(cases[-1])
            suite.set("tests", str(len(cases) - 1))
        elif mode == "junit-foreign":
            cases[-1].set("classname", "tests.test_elsewhere")
        else:
            stamp = datetime.datetime.fromisoformat(suite.get("timestamp"))
            suite.set("timestamp", (stamp - datetime.timedelta(days=1)).isoformat())
    tree.write(junit, encoding="utf-8", xml_declaration=True)
elif mode == "timing-drops-failure":
    payload = json.loads(timing.read_text(encoding="utf-8"))
    payload["failed_nodes"] = []
    timing.write_text(json.dumps(payload), encoding="utf-8")
sys.exit(code)
'''


def test_session_hard_exit_0_is_never_completed(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_exit.py": HARD_EXIT_0_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_exit.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, "hard-exit-0", selection)
    assert result["exit_code"] == 0  # the child really exited 0 ...
    assert result["verdict"] == "harvest-failed"  # ... and that earns nothing
    assert exit_code == 92 and result["runner_exit"] == 92
    assert result["complete"] is False
    assert result["timing_summary"]["outcome_totals"] is None
    assert result["failed_nodes"] is None
    assert result["evidence"]["timing"]["status"] == "missing"
    assert result["evidence"]["junit"]["status"] == "missing"


def test_session_abort_after_failure_is_not_completed_and_not_comparable(tmp_path):
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_x.py": FAILING_TEST.replace("test_broken", "test_one")})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_x.py"])
    _, base, _ = run_runner(tmp_path, repo, "abort-base", selection)
    assert base["verdict"] == "completed-with-failures"
    (repo / "tests" / "test_x.py").write_text(FAIL_THEN_HARD_EXIT_1_TEST, encoding="utf-8")
    exit_code, cand, _ = run_runner(tmp_path, repo, "abort-cand", selection, "--allow-dirty")
    assert cand["exit_code"] == 1
    assert cand["verdict"] == "harvest-failed" and exit_code == 92
    assert cand["failed_nodes"] is None  # unknown, not "no failures"
    artifacts = tmp_path / "artifacts"
    compare_exit, comparison = run_compare(
        tmp_path, artifacts / "abort-base" / "RESULT.json",
        artifacts / "abort-cand" / "RESULT.json", "abort")
    assert compare_exit == 1 and comparison["comparable"] is False
    assert comparison["failed_only_in_baseline"] == []  # never reads as a fix
    assert any(problem.startswith("candidate: verdict 'harvest-failed'")
               for problem in comparison["problems"])


@pytest.mark.parametrize(("instrument", "mode", "verdict", "evidence", "status"), [
    ("ops/pytest_watchdog.py", "missing", "harvest-failed", "watchdog", "missing"),
    ("ops/pytest_watchdog.py", "malformed", "harvest-failed", "watchdog", "malformed"),
    ("ops/pytest_timing.py", "junit-missing", "harvest-failed", "junit", "missing"),
    ("ops/pytest_timing.py", "junit-malformed", "harvest-failed", "junit", "malformed"),
    ("ops/pytest_timing.py", "timing-missing", "harvest-failed", "timing", "missing"),
    ("ops/pytest_timing.py", "timing-malformed", "harvest-failed", "timing", "malformed"),
    ("ops/pytest_timing.py", "timing-drops-failure", "harvest-inconsistent", "timing", "ok"),
    ("ops/pytest_timing.py", "ledger-missing", "harvest-failed", "ledger", "missing"),
    ("ops/pytest_timing.py", "ledger-malformed", "harvest-failed", "ledger", "malformed"),
])
def test_missing_malformed_or_disagreeing_evidence_fails_closed(
        tmp_path, instrument, mode, verdict, evidence, status):
    # FAULT INJECTION: one evidence file is removed, corrupted or contradicted.
    wrapper = (_WATCHDOG_WRAPPER if instrument.endswith("watchdog.py")
               else _TIMING_WRAPPER).format(mode=mode)
    test_body = FAILING_TEST if mode == "timing-drops-failure" else PASSING_TEST
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": test_body},
                               fault_injection={instrument: wrapper})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, f"fault-{mode}", selection)
    assert result["verdict"] == verdict
    assert exit_code == 92 and result["runner_exit"] == 92
    assert result["evidence"][evidence]["status"] == status
    assert result["evidence_problems"] or result["inconsistencies"]
    if verdict == "harvest-failed" and evidence == "timing":
        assert result["timing_summary"]["outcome_totals"] is None


def test_mixed_outcomes_reconcile_and_valid_red_runs_compare(tmp_path):
    # Nested classes with a shared test name, setup and teardown errors (one
    # after a failing call: two JUnit rows for one node), skips, xfails, xpass
    # and strict xpass: a consistent completed run with full case coverage.
    repo = make_synthetic_repo(tmp_path, {"tests/test_mixed.py": MIXED_OUTCOMES_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_mixed.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, "mixed-a", selection)
    assert result["inconsistencies"] == [] and result["evidence_problems"] == []
    assert result["verdict"] == "completed-with-failures" and exit_code == 1
    prefix = "tests/test_mixed.py::"
    assert sorted(result["failed_nodes"]) == sorted(prefix + name for name in (
        "TestOuter::TestInner::test_same", "TestOuter::test_same", "test_setup_error",
        "test_fails_then_teardown_errors", "test_passes_then_teardown_errors",
        "test_xpass_strict"))
    assert result["failed_nodes"][prefix + "test_setup_error"]["outcome"] == "error"
    assert result["failed_nodes"][prefix + "test_passes_then_teardown_errors"]["outcome"] == "error"
    totals = result["timing_summary"]["outcome_totals"]
    assert totals == {"passed": 4, "xpassed": 1, "failed": 4, "errors": 3, "skipped": 2,
                      "xfailed": 3}
    coverage = result["coverage"]
    assert coverage["reconciled"] is True and coverage["reasons"] == []
    assert coverage["junit"] == coverage["timing"] == {
        "nodes": 15, "pass": 5, "failure": 4, "error": 3, "skip": 2, "xfail": 3}
    assert coverage["collected"] == 15
    identity = coverage["identity"]
    assert identity["verified"] is True and identity["reasons"] == []
    assert identity["started"] == identity["collected"] == identity["junit_nodes"] == 15

    _, second, _ = run_runner(tmp_path, repo, "mixed-b", selection)
    assert second["verdict"] == "completed-with-failures"
    artifacts = tmp_path / "artifacts"
    compare_exit, comparison = run_compare(tmp_path, artifacts / "mixed-a" / "RESULT.json",
                                           artifacts / "mixed-b" / "RESULT.json", "mixed")
    assert compare_exit == 0 and comparison["comparable"] is True
    assert comparison["problems"] == []
    assert len(comparison["failed_in_both"]) == 6
    assert comparison["failed_only_in_baseline"] == comparison["failed_only_in_candidate"] == []


@pytest.mark.parametrize(("mode", "reason"), [
    ("junit-empty", "does not reconcile"),
    ("junit-partial", "does not reconcile"),
    ("junit-foreign", "outside the selection"),
    ("junit-other-session", "not the same session"),
])
def test_incomplete_or_unrelated_junit_is_never_completed_evidence(tmp_path, mode, reason):
    # FAULT INJECTION: the real tests run and pass; the JUnit is then replaced
    # by VALID XML that is empty, missing one passing case, naming a file
    # outside the selection, or stamped by another session.
    repo = make_synthetic_repo(tmp_path, {"tests/test_two.py": TWO_PASSING_TESTS},
                               fault_injection={"ops/pytest_timing.py":
                                                _TIMING_WRAPPER.format(mode=mode)})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_two.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, mode, selection)
    assert result["exit_code"] == 0 and result["timing_summary"]["outcome_totals"]["passed"] == 2
    assert result["verdict"] == "harvest-inconsistent"
    assert exit_code == 92 and result["runner_exit"] == 92
    assert result["evidence"]["junit"]["status"] == "ok"  # parseable -- and still not enough
    assert result["coverage"]["reconciled"] is False
    assert any(reason in item for item in result["inconsistencies"]), result["inconsistencies"]


@pytest.mark.parametrize(("mode", "body", "reason"), [
    ("junit-renamed", TWO_PASSING_TESTS, "never started"),
    ("junit-swap-names", SKIP_THEN_XFAIL_TESTS, "different outcome"),
], ids=["renamed-nodes", "swapped-outcomes"])
def test_junit_node_identity_is_matched_not_counted(tmp_path, mode, body, reason):
    # FAULT INJECTION: real pytest runs the nodes; the JUnit keeps every count,
    # outcome and the session timestamp but names never-executed nodes, or
    # swaps the names of a skipped and an xfailed node. Counts reconcile;
    # identity must not.
    repo = make_synthetic_repo(tmp_path, {"tests/test_pair.py": body},
                               fault_injection={"ops/pytest_timing.py":
                                                _TIMING_WRAPPER.format(mode=mode)})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_pair.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, mode, selection)
    assert result["verdict"] == "harvest-inconsistent"
    assert exit_code == 92 and result["exit_code"] == 0
    assert result["coverage"]["reconciled"] is False
    assert result["coverage"]["identity"]["verified"] is False
    assert any(reason in item for item in result["inconsistencies"]), result["inconsistencies"]


def test_intact_junit_completes_and_partial_receipt_is_not_comparable(tmp_path):
    (tmp_path / "intact").mkdir()
    (tmp_path / "partial").mkdir()
    intact_repo = make_synthetic_repo(tmp_path / "intact", {"tests/test_two.py": TWO_PASSING_TESTS})
    partial_repo = make_synthetic_repo(
        tmp_path / "partial", {"tests/test_two.py": TWO_PASSING_TESTS},
        fault_injection={"ops/pytest_timing.py": _TIMING_WRAPPER.format(mode="junit-partial")})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_two.py"])
    exit_code, intact, _ = run_runner(tmp_path, intact_repo, "intact", selection)
    assert exit_code == 0 and intact["verdict"] == "completed-clean"
    assert intact["coverage"]["reconciled"] is True
    assert intact["coverage"]["junit"]["nodes"] == 2 and intact["coverage"]["junit"]["pass"] == 2
    assert intact["coverage"]["identity"]["verified"] is True
    exit_code, _, _ = run_runner(tmp_path, partial_repo, "partial", selection)
    assert exit_code == 92
    artifacts = tmp_path / "artifacts"
    compare_exit, comparison = run_compare(tmp_path, artifacts / "intact" / "RESULT.json",
                                           artifacts / "partial" / "RESULT.json", "partial")
    assert compare_exit == 1 and comparison["comparable"] is False
    assert any("case coverage not reconciled" in problem for problem in comparison["problems"])


def test_reused_label_is_refused_and_keeps_the_earlier_receipt(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    _, first, run_dir = run_runner(tmp_path, repo, "reuse", selection)
    assert first["verdict"] == "completed-clean"
    before = (run_dir / "RESULT.json").read_bytes()
    process = subprocess.run(
        [sys.executable, str(_RUNNER), "--repo", str(repo),
         "--artifacts", str(tmp_path / "artifacts"), "--profiles", str(tmp_path / "profiles"),
         "run", "--label", "reuse", "--selection-explicit", str(selection)],
        capture_output=True, text=True, timeout=120)
    assert process.returncode == 90
    assert (run_dir / "RESULT.json").read_bytes() == before
    refusals = sorted(run_dir.glob("REFUSED-*.json"))
    assert len(refusals) == 1
    refused = json.loads(refusals[0].read_text(encoding="utf-8"))
    assert refused["verdict"] == "refused" and refused["executed"] is False


# --------------------------------------------------------------------------- #
# Controls: compare accepts only executed, complete, consistent receipts
# --------------------------------------------------------------------------- #

def test_compare_refuses_invalid_receipts(tmp_path):
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_alpha.py": PASSING_TEST,
        "tests/test_exit.py": HARD_EXIT_0_TEST,
        "tests/test_sleep.py": SLEEPING_TEST,
    })
    artifacts = tmp_path / "artifacts"
    valid_sel = write_selection(tmp_path, "valid.txt", ["tests/test_alpha.py"])
    run_runner(tmp_path, repo, "valid", valid_sel)
    run_runner(tmp_path, repo, "refused",
               write_selection(tmp_path, "gone.txt", ["tests/test_gone.py"]))
    run_runner(tmp_path, repo, "aborted",
               write_selection(tmp_path, "exit.txt", ["tests/test_exit.py"]))
    run_runner(tmp_path, repo, "interrupted",
               write_selection(tmp_path, "sleep.txt", ["tests/test_sleep.py"]),
               "--wall-budget", "4", timeout=120)
    valid = artifacts / "valid" / "RESULT.json"
    payload = json.loads(valid.read_text(encoding="utf-8"))
    empty = tmp_path / "empty.json"
    empty.write_text("{}\n", encoding="utf-8")
    wrong_schema = tmp_path / "wrong-schema.json"
    wrong_schema.write_text(json.dumps({**payload, "schema": "something-else"}), encoding="utf-8")
    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text("{not json", encoding="utf-8")
    no_coverage = tmp_path / "no-coverage.json"
    no_coverage.write_text(json.dumps({key: value for key, value in payload.items()
                                       if key != "coverage"}), encoding="utf-8")
    edited = json.loads(json.dumps(payload))
    edited["coverage"]["junit"]["nodes"] -= 1  # a completed label over a short JUnit
    edited_coverage = tmp_path / "edited-coverage.json"
    edited_coverage.write_text(json.dumps(edited), encoding="utf-8")
    unidentified = json.loads(json.dumps(payload))
    del unidentified["coverage"]["identity"]
    no_identity = tmp_path / "no-identity.json"
    no_identity.write_text(json.dumps(unidentified), encoding="utf-8")

    exit_code, comparison = run_compare(tmp_path, valid, valid, "valid")
    assert exit_code == 0 and comparison["comparable"] is True
    assert comparison["problems"] == []
    for name, receipt, expected in (
            ("empty", empty, "empty or non-object receipt"),
            ("wrong-schema", wrong_schema, "schema"),
            ("refused", artifacts / "refused" / "RESULT.json", "never executed"),
            ("aborted", artifacts / "aborted" / "RESULT.json", "verdict 'harvest-failed'"),
            ("interrupted", artifacts / "interrupted" / "RESULT.json",
             "verdict 'interrupted-by-wall-budget'"),
            ("unreadable", unreadable, "receipt unreadable"),
            ("no-coverage", no_coverage, "no case-coverage evidence"),
            ("edited-coverage", edited_coverage, "junit case coverage"),
            ("no-identity", no_identity, "no verified node-identity evidence")):
        exit_code, comparison = run_compare(tmp_path, receipt, receipt, name)
        assert exit_code == 1, name
        assert comparison["comparable"] is False, name
        assert any(expected in problem for problem in comparison["problems"]), (
            name, comparison["problems"])
        assert comparison["failed_in_both"] == [], name


def test_compare_normalises_first_errors_and_flags_truncation(tmp_path):
    dynamic = '''import datetime
import uuid
from pathlib import Path


def test_dynamic(tmp_path):
    raise AssertionError(
        f"at {datetime.datetime.now(datetime.timezone.utc).isoformat()} "
        f"id {uuid.uuid4().hex} in {tmp_path}")


def test_changes():
    flag = Path(__file__).resolve().parents[1] / "flag.txt"
    assert False, "flag says " + flag.read_text(encoding="utf-8").strip()


def test_long():
    assert False, "\\n".join(f"line {number}" for number in range(30))
'''
    repo = make_synthetic_repo(tmp_path, {"tests/test_dyn.py": dynamic, "flag.txt": "a"})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_dyn.py"])
    run_runner(tmp_path, repo, "dyn-base", selection)
    (repo / "flag.txt").write_text("b", encoding="utf-8")
    run_runner(tmp_path, repo, "dyn-cand", selection, "--allow-dirty")
    artifacts = tmp_path / "artifacts"
    exit_code, comparison = run_compare(tmp_path, artifacts / "dyn-base" / "RESULT.json",
                                        artifacts / "dyn-cand" / "RESULT.json", "dyn")
    assert exit_code == 0
    by_node = {entry["nodeid"].split("::")[-1]: entry for entry in comparison["failed_in_both"]}
    assert by_node["test_dynamic"]["first_error_equal_raw"] is False
    assert by_node["test_dynamic"]["first_error_equal_normalised"] is True
    assert by_node["test_changes"]["first_error_equal_normalised"] is False
    assert by_node["test_long"]["excerpt_truncated"] is True
    assert comparison["first_error_summary"]["raw_differ"] == 2
    assert comparison["first_error_summary"]["normalised_differ"] == 1


# --------------------------------------------------------------------------- #
# Controls: canonical path, collection failure policy, bounds, exits
# --------------------------------------------------------------------------- #

def test_canonical_default_path_executes(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": PASSING_TEST})
    exit_code, result, _ = run_runner(tmp_path, repo, "canonical", None)
    assert exit_code == 0 and result["verdict"] == "completed-clean"
    assert result["selection"]["source"] == "canonical-shard"
    assert result["selection"]["files"] == ["tests/test_alpha.py"]
    assert result["collection"]["collection_command_exit"] == 0
    assert "manifest" not in result


def test_canonical_collection_failure_is_refused_before_execution(tmp_path):
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_alpha.py": PASSING_TEST,
        "tests/test_broken.py": BROKEN_IMPORT_TEST,
    })
    exit_code, result, run_dir = run_runner(
        tmp_path, repo, "partial", None, "--shard", "0", "--shards", "1")
    assert exit_code == 90 and result["verdict"] == "refused"
    assert result["executed"] is False
    assert result["collection"]["collection_command_exit"] == 2
    assert any("canonical collection exited 2" in refusal for refusal in result["refusals"])
    assert not (run_dir / "run-stdout.txt").exists()


@pytest.mark.parametrize("bound", [("--collect-timeout", "4"), ("--wall-budget", "4")])
def test_hung_canonical_collection_is_bounded(tmp_path, bound):
    repo = make_synthetic_repo(tmp_path, {
        "conftest.py": "import time\ntime.sleep(120)\n",
        "tests/test_alpha.py": PASSING_TEST,
    })
    started = time.monotonic()
    exit_code, result, run_dir = run_runner(
        tmp_path, repo, "slow", None, "--shard", "0", "--shards", "1", *bound, timeout=120)
    assert time.monotonic() - started < 30
    assert exit_code == 90 and result["verdict"] == "refused"
    assert result["executed"] is False
    assert result["collection"]["collection_timed_out"] is True
    assert result["collection"]["collection_command_exit"] is None
    with pytest.raises(ProcessLookupError):
        os.kill(result["collection"]["collection_pid"], 0)  # existence probe only
    assert not (run_dir / "run-stdout.txt").exists()


def test_empty_selection_is_refused_before_execution(tmp_path):
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_alpha.py": PASSING_TEST,
        "tests/test_unselected.py": FAILING_TEST,
    })
    exit_code, result, run_dir = run_runner(
        tmp_path, repo, "empty", write_selection(tmp_path, "empty.txt", []))
    assert exit_code == 90 and result["verdict"] == "refused"
    assert result["executed"] is False
    assert any("empty selection" in refusal for refusal in result["refusals"])
    assert not (run_dir / "run-stdout.txt").exists()


@pytest.mark.parametrize(("files", "verdict", "runner_exit"), [
    ({"tests/test_none.py": "VALUE = 1\n"}, "no-tests-collected", 5),
    ({"tests/test_none.py": "def test_ok():\n    pass\n\n\n"
                            "def test_interrupt():\n    raise KeyboardInterrupt\n"},
     "session-interrupted", 2),
    ({"tests/test_none.py": PASSING_TEST,
      "conftest.py": "def pytest_collection_modifyitems(items):\n"
                     "    raise RuntimeError('deliberate internal error')\n"},
     "internal-error", 3),
])
def test_non_completed_pytest_exits_map_explicitly(tmp_path, files, verdict, runner_exit):
    repo = make_synthetic_repo(tmp_path, files)
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_none.py"])
    exit_code, result, _ = run_runner(tmp_path, repo, verdict, selection)
    assert result["verdict"] == verdict
    assert exit_code == runner_exit and result["exit_code"] == runner_exit
