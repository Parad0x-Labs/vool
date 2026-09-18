"""Baseline-comparison sabotage tests: "pre-existing" must be earned mechanically.

Each end-to-end case builds a scratch git repo (vendoring this repo's real shard
gate), commits a baseline and a candidate, and runs ops.baseline_check through
real worktrees and the real runner. The verdicts below are the law:

    identical failing sets          -> PRE_EXISTING_CONFIRMED   (exit 0)
    baseline green, candidate red   -> NEW_REGRESSION           (not pre-existing)
    different failing sets          -> FAILURE_SET_CHANGED      (not pre-existing)
    baseline red, candidate SKIPS   -> COVERAGE_REGRESSION      (never pre-existing)
    env/target identity broken      -> fail closed              (unattributed)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ops import baseline_check
from ops.baseline_check import SideResult, _decide

_OPS_FILES = (
    "__init__.py",
    "process_tree.py",
    "pytest_shards.py",
    "pytest_execution.py",
    "pytest_manifest.py",
)

_FAILING_X = "def test_x():\n    assert False\n\n\ndef test_y():\n    assert True\n"
_PASSING = "def test_x():\n    assert True\n\n\ndef test_y():\n    assert True\n"
_FAILING_Y = "def test_x():\n    assert True\n\n\ndef test_y():\n    assert False\n"
_SKIPPING_X = (
    "import pytest\n\n\n"
    "@pytest.mark.skip(reason='gone')\ndef test_x():\n    assert False\n\n\n"
    "def test_y():\n    assert True\n"
)


def _run(command: tuple[str, ...], *, cwd: Path) -> str:
    completed = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, timeout=600, check=False
    )
    assert completed.returncode == 0, f"{command} failed: {completed.stderr}"
    return completed.stdout


def _scratch_repo(tmp_path: Path, baseline_body: str, candidate_body: str) -> Path:
    repo = tmp_path / "scratch"
    (repo / "tests").mkdir(parents=True)
    (repo / "ops").mkdir()
    ops_source = Path(baseline_check.__file__).resolve().parent.parent / "ops"
    for name in _OPS_FILES:
        shutil.copy(ops_source / name, repo / "ops" / name)
    _run(("git", "init", "-q"), cwd=repo)
    _run(("git", "config", "user.email", "audit@local"), cwd=repo)
    _run(("git", "config", "user.name", "auditor"), cwd=repo)
    for name in _OPS_FILES:
        _run(("git", "add", f"ops/{name}"), cwd=repo)
    _run(("git", "commit", "-qm", "vendored gate"), cwd=repo)
    baseline = repo / "tests" / "test_demo.py"
    baseline.write_text("# vool-demo version: 1\n" + baseline_body, encoding="utf-8")
    _run(("git", "add", "-A"), cwd=repo)
    _run(("git", "commit", "-qm", "baseline"), cwd=repo)
    # Version markers guarantee a real candidate commit even when behaviors match
    # (PRE_EXISTING_CONFIRMED and BOTH_GREEN compare two genuinely distinct trees).
    baseline.write_text("# vool-demo version: 2\n" + candidate_body, encoding="utf-8")
    _run(("git", "commit", "-aqm", "candidate"), cwd=repo)
    return repo


def _pair(repo: Path, tmp_path: Path, label: str) -> tuple[int, dict]:
    artifact_root = tmp_path / "artifacts"
    exit_code = baseline_check.main(
        [
            "--repo",
            str(repo),
            "--candidate",
            "HEAD",
            "--baseline",
            "HEAD~1",
            "--target",
            "tests/test_demo.py",
            "--workers",
            "1",
            "--artifact-root",
            str(artifact_root),
            "--label",
            label,
        ]
    )
    record_path = artifact_root / label / "vool.baseline-comparison.v1.json"
    assert record_path.exists(), "comparison artifact must always be written"
    return exit_code, json.loads(record_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("baseline_body", "candidate_body", "expected_verdict"),
    [
        (_FAILING_X, _FAILING_X, "PRE_EXISTING_CONFIRMED"),
        (_PASSING, _FAILING_Y, "NEW_REGRESSION"),
        (_FAILING_X, _FAILING_Y, "FAILURE_SET_CHANGED"),
        (_FAILING_X, _SKIPPING_X, "COVERAGE_REGRESSION"),
        (_PASSING, _PASSING, "BOTH_GREEN"),
    ],
)
def test_end_to_end_verdicts(
    tmp_path: Path,
    baseline_body: str,
    candidate_body: str,
    expected_verdict: str,
) -> None:
    repo = _scratch_repo(tmp_path, baseline_body, candidate_body)
    exit_code, record = _pair(repo, tmp_path, f"case-{expected_verdict.lower()}")
    assert record["verdict"] == expected_verdict
    if expected_verdict == "PRE_EXISTING_CONFIRMED":
        assert exit_code == 0
        # The confirmation must cite identical nodeid evidence, not just twin exit codes.
        assert record["baseline"]["failed_nodeids"] == ["tests/test_demo.py::test_x"]
        assert record["candidate"]["failed_nodeids"] == ["tests/test_demo.py::test_x"]
        assert record["baseline"]["sha"] != record["candidate"]["sha"]
    else:
        # Law 4: anything not confirmed cannot be able to read as green.
        assert exit_code != 0
        assert record["verdict"] in {
            "NEW_REGRESSION",
            "FAILURE_SET_CHANGED",
            "COVERAGE_REGRESSION",
            "BOTH_GREEN",
        }


def test_environment_mismatch_fails_closed() -> None:
    base = SideResult("baseline", "a" * 40, 0, None, frozenset({"t::x"}), frozenset(),
                      frozenset(), frozenset(), frozenset(), "8.0", "")
    cand = SideResult("candidate", "b" * 40, 0, None, frozenset({"t::x"}), frozenset(),
                      frozenset(), frozenset(), frozenset(), "9.9", "")
    verdict, details = _decide(cand, base)
    assert verdict == "ENVIRONMENT_MISMATCH"
    assert details["baseline_pytest"] == "8.0"


def test_target_mismatch_fails_closed_even_when_failures_look_alike() -> None:
    base = SideResult("baseline", "a" * 40, 1, None, frozenset({"t::x"}), frozenset({"t::x"}),
                      frozenset(), frozenset(), frozenset(), "8.0", "")
    cand = SideResult("candidate", "b" * 40, 1, None, frozenset({"t::x", "t::new"}),
                      frozenset({"t::x"}), frozenset(), frozenset(), frozenset(), "8.0", "")
    verdict, _details = _decide(cand, base)
    assert verdict == "TARGET_MISMATCH"


def test_baseline_unrunnable_fails_closed() -> None:
    base = SideResult("baseline", "a" * 40, 2, None, frozenset(), frozenset(), frozenset(),
                      frozenset(), frozenset(), "8.0", "collection exploded")
    cand = SideResult("candidate", "b" * 40, 1, None, frozenset({"t::x"}), frozenset({"t::x"}),
                      frozenset(), frozenset(), frozenset(), "8.0", "")
    verdict, details = _decide(cand, base)
    assert verdict == "BASELINE_UNRUNNABLE"
    assert "collection exploded" in details["reason"]
