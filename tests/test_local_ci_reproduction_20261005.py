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
  * two different selections are never conflated and never compared;
  * the baseline/candidate comparison reports failed nodes and first errors
    without exonerating anything.

These are exposed development controls: they prove the runner's behavior on
fixtures built for the purpose, and are never a score on any unseen set.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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


def make_synthetic_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """A real git repo carrying the repo's own CI instruments plus fixture tests."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ops").mkdir()
    (repo / "tests").mkdir()
    for relative in _OPS_INSTRUMENTS:
        source = _REPO_ROOT / relative
        assert source.is_file(), f"instrument missing from repo: {relative}"
        (repo / relative).write_bytes(source.read_bytes())
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
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


def run_runner(tmp_path: Path, repo: Path, label: str, selection: Path, *extra: str,
               timeout: float = 300.0) -> tuple[int, dict, Path]:
    """Invoke the runner as a real subprocess; return (exit, RESULT.json, run dir)."""

    artifacts = tmp_path / "artifacts"
    profiles = tmp_path / "profiles"
    command = [sys.executable, str(_RUNNER),
               "--repo", str(repo), "--artifacts", str(artifacts),
               "--profiles", str(profiles),
               "run", "--label", label,
               "--selection-explicit", str(selection),
               "--selection-label", f"synthetic-{label}",
               *extra]
    process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    run_dir = artifacts / label
    result_path = run_dir / "RESULT.json"
    assert result_path.is_file(), (
        f"runner left no RESULT.json (exit {process.returncode}); "
        f"stderr:\n{process.stderr}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert process.returncode == result.get("exit_code") or result["verdict"] == "refused", (
        f"runner exit {process.returncode} disagrees with RESULT exit_code "
        f"{result.get('exit_code')}")
    return process.returncode, result, run_dir


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
    repo = make_synthetic_repo(tmp_path, {
        "tests/test_flag.py": FLAG_TEST,
        "tests/test_alpha.py": PASSING_TEST,
    })
    (repo / "flag.txt").write_text("fail", encoding="utf-8")
    first = write_selection(tmp_path, "one.txt", ["tests/test_flag.py"])
    second = write_selection(tmp_path, "two.txt", ["tests/test_alpha.py"])
    run_runner(tmp_path, repo, "mix-base", first)
    run_runner(tmp_path, repo, "mix-cand", second)
    artifacts = tmp_path / "artifacts"
    process = subprocess.run(
        [sys.executable, str(_RUNNER), "compare",
         "--baseline", str(artifacts / "mix-base" / "RESULT.json"),
         "--candidate", str(artifacts / "mix-cand" / "RESULT.json"),
         "--output", str(artifacts / "mix-compare.json")],
        capture_output=True, text=True, timeout=60)
    assert process.returncode == 1
    comparison = json.loads((artifacts / "mix-compare.json").read_text(encoding="utf-8"))
    assert any("selection mismatch" in problem for problem in comparison["problems"])


def test_junit_first_error_has_real_text(tmp_path):
    repo = make_synthetic_repo(tmp_path, {"tests/test_alpha.py": FAILING_TEST})
    selection = write_selection(tmp_path, "sel.txt", ["tests/test_alpha.py"])
    _, result, _ = run_runner(tmp_path, repo, "junit", selection)
    entry = result["failed_nodes"]["tests/test_alpha.py::test_broken"]
    assert "AssertionError" in entry["first_error"] or "assert" in entry["first_error"]
    assert entry["timing"]["nodeid"] == "tests/test_alpha.py::test_broken"
