"""Trusted-controller sabotage tests: the subject must not grade itself.

Cases proven end-to-end through real git worktrees and the controller-owned
observer plugin:

    A/D  old-generation source + immutable external probe, identical failure
         -> PRE_EXISTING_CONFIRMED under one controller (cross-generation proof)
    B    candidate tampers with its own verifier files -> comparison unaffected
    C    identical behavior, differing declared dependencies -> ENVIRONMENT_MISMATCH
    E    baseline red, candidate skips the node -> COVERAGE_REGRESSION
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ops import trusted_controller
from ops.trusted_controller import (
    EXTERNAL_REGRESSION_PROBE,
    Fingerprint,
    _fingerprints_compatible,
)

_OPS_FILES = ("__init__.py", "process_tree.py", "pytest_shards.py", "pytest_execution.py")

# An OLD-generation verification stack: no failure records, no skip accounting.
_OLD_PYTEST_EXECUTION = '''"""Legacy observer: records collected/started only."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
import pytest


class Plugin:
    def __init__(self, *, output_path: Path) -> None:
        self._output_path = output_path
        self._collected: list[str] = []
        self._started: list[str] = []

    def pytest_collection_finish(self, session: Any) -> None:
        self._collected = [str(item.nodeid) for item in session.items]

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        del location
        self._started.append(str(nodeid))

    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        del session
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_text(
            json.dumps(
                {
                    "schema": "vool.pytest-execution.v0",
                    "exitstatus": int(exitstatus),
                    "collected_nodeids": self._collected,
                    "started_nodeids": self._started,
                }
            ) + "\\n",
            encoding="utf-8",
        )


def main() -> int:
    raise SystemExit("legacy gate")


if __name__ == "__main__":
    main()
'''

_FAILING_X = "def test_x():\n    assert False\n\n\ndef test_y():\n    assert True\n"
_PASSING = "def test_x():\n    assert True\n\n\ndef test_y():\n    assert True\n"
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


def _scratch_repo(tmp_path: Path, *, legacy_ops: bool, requirements: str = "") -> Path:
    """Baseline commit = optionally legacy verification stack; candidate = HEAD tip."""
    repo = tmp_path / "scratch"
    (repo / "tests").mkdir(parents=True)
    (repo / "ops").mkdir()
    ops_source = Path(trusted_controller.__file__).resolve().parent.parent / "ops"
    if legacy_ops:
        shutil.copy(ops_source / "__init__.py", repo / "ops" / "__init__.py")
        shutil.copy(ops_source / "pytest_manifest.py", repo / "ops" / "pytest_manifest.py")
        (repo / "ops" / "pytest_execution.py").write_text(_OLD_PYTEST_EXECUTION, encoding="utf-8")
    else:
        for name in _OPS_FILES:
            shutil.copy(ops_source / name, repo / "ops" / name)
    _run(("git", "init", "-q"), cwd=repo)
    _run(("git", "config", "user.email", "audit@local"), cwd=repo)
    _run(("git", "config", "user.name", "auditor"), cwd=repo)
    if requirements:
        (repo / "requirements.txt").write_text(requirements, encoding="utf-8")
    _run(("git", "add", "-A"), cwd=repo)
    _run(("git", "commit", "-qm", "baseline"), cwd=repo)
    return repo


def _commit_candidate(repo: Path, body: str, requirements: str | None = None) -> None:
    test_file = repo / "tests" / "test_demo.py"
    test_file.write_text("# candidate\n" + body, encoding="utf-8")
    (repo / "tests" / "test_demo.py").parent.joinpath(".gitkeep-absent")
    if requirements is not None:
        (repo / "requirements.txt").write_text(requirements, encoding="utf-8")
    _run(("git", "commit", "-aqm", "candidate"), cwd=repo)


def _commit_all(repo: Path, message: str) -> None:
    _run(("git", "add", "-A"), cwd=repo)
    _run(("git", "commit", "-aqm", message), cwd=repo)


def _compare(repo: Path, tmp_path: Path, label: str, **kwargs: object) -> tuple[int, dict]:
    artifact_root = tmp_path / "artifacts"
    argv = [
        "--repo",
        str(repo),
        "--candidate",
        str(kwargs.pop("candidate", "HEAD")),
        "--baseline",
        str(kwargs.pop("baseline", "HEAD~1")),
        "--artifact-root",
        str(artifact_root),
        "--label",
        label,
    ]
    for key, value in kwargs.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    exit_code = trusted_controller.main(argv)
    record_path = artifact_root / label / "vool.trusted-baseline-comparison.v1.json"
    assert record_path.exists(), "comparison artifact must always be written"
    return exit_code, json.loads(record_path.read_text(encoding="utf-8"))


def test_case_ad_probe_confirms_pre_existing_across_generations(tmp_path: Path) -> None:
    # Baseline predates failure records AND skip accounting entirely.
    repo = _scratch_repo(tmp_path, legacy_ops=True)
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline behavior")
    probe = tmp_path / "probe_regression.py"
    probe.write_text("def test_behavior():\n    assert False\n", encoding="utf-8")
    exit_code, record = _compare(repo, tmp_path, "case-ad", probe=probe)
    assert record["verdict"] == "PRE_EXISTING_CONFIRMED"
    assert exit_code == 0
    assert record["proof_type"] == EXTERNAL_REGRESSION_PROBE
    assert record["baseline"]["failed_nodeids"] == [
        ".trusted_probe/test_external_regression_probe.py::test_behavior"
    ]
    assert record["candidate"]["failed_nodeids"] == record["baseline"]["failed_nodeids"]


def test_case_b_tampered_subject_verifier_is_ignored(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, legacy_ops=False)
    # The probe measures actual source behavior via a repo module.
    (repo / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_demo.py").write_text(_PASSING, encoding="utf-8")
    _commit_all(repo, "baseline green")
    # Candidate: breaks the behavior AND forges its self-grading verifier stack
    # so that (if consulted) it would claim everything is green.
    (repo / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    _commit_all(repo, "candidate breaks behavior")
    forged = repo / "ops" / "pytest_execution.py"
    forged.parent.mkdir(exist_ok=True)
    forged.write_text(
        "raise SystemExit(0)  # forged: claims success without running anything\n",
        encoding="utf-8",
    )
    (repo / "ops" / "pytest_shards.py").write_text(
        "FAKE_MANIFEST = {'exitstatus': 0}\n", encoding="utf-8"
    )
    _run(("git", "add", "-A"), cwd=repo)
    _run(("git", "commit", "-qm", "candidate tampers with its verifier"), cwd=repo)
    probe = tmp_path / "probe_regression.py"
    probe.write_text(
        "import sys\n"
        "from pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\n"
        "from helper import VALUE\n\n\ndef test_behavior():\n    assert VALUE == 1\n",
        encoding="utf-8",
    )
    # Three commits: HEAD~1 already contains the break, so compare against HEAD~2.
    exit_code, record = _compare(repo, tmp_path, "case-b", probe=probe, baseline="HEAD~2")
    assert record["verdict"] == "NEW_REGRESSION"
    assert exit_code != 0


def test_case_c_dependency_drift_blocks_confirmation(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, legacy_ops=False, requirements="helper-lib==1.0\n")
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline")
    # IDENTICAL behavior, different declared dependency version.
    _commit_candidate(repo, _FAILING_X, requirements="helper-lib==2.0\n")
    probe = tmp_path / "probe_regression.py"
    probe.write_text("def test_behavior():\n    assert False\n", encoding="utf-8")
    exit_code, record = _compare(repo, tmp_path, "case-c", probe=probe)
    assert record["verdict"] == "ENVIRONMENT_MISMATCH"
    assert exit_code == 2
    assert "dependency_declaration" in record["details"]


def test_case_e_native_skip_never_confirms(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, legacy_ops=False)
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline")
    _commit_candidate(repo, _SKIPPING_X)
    exit_code, record = _compare(repo, tmp_path, "case-e", target="tests/test_demo.py")
    assert record["verdict"] == "COVERAGE_REGRESSION"
    assert exit_code != 0
    assert record["details"]["proof_lost_nodes"] == ["tests/test_demo.py::test_x"]


def test_native_mode_reports_test_set_change(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, legacy_ops=False)
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline")
    # Candidate renames the test: same path, different node identity.
    _commit_candidate(repo, "def test_renamed_x():\n    assert False\n")
    exit_code, record = _compare(repo, tmp_path, "renamed", target="tests/test_demo.py")
    assert record["verdict"] == "TARGET_MISMATCH"
    assert exit_code != 0


def test_fingerprint_plugin_difference_detected() -> None:
    base = Fingerprint("3.12.13", "9.1.0", ("cov",), "Darwin/arm64", "sha-a", "")
    cand = Fingerprint("3.12.13", "9.1.0", (), "Darwin/arm64", "sha-a", "")
    ok, diffs = _fingerprints_compatible(base, cand)
    assert not ok
    assert diffs["plugins"]["only_baseline"] == ["cov"]


def test_s3_same_declarations_different_install_detected() -> None:
    # Same interpreter/pytest/plugins/declaration bytes, but the RESOLVED
    # environment differs (dep X 1.0 vs X 2.0 actually installed).
    base = Fingerprint(
        "3.12.13", "9.1.0", (), "Darwin/arm64", "decl-sha", "", installed_packages_sha="pkg-sha-1"
    )
    cand = Fingerprint(
        "3.12.13", "9.1.0", (), "Darwin/arm64", "decl-sha", "", installed_packages_sha="pkg-sha-2"
    )
    ok, diffs = _fingerprints_compatible(base, cand)
    assert not ok
    assert diffs["installed_packages"]
    identical = _fingerprints_compatible(
        Fingerprint("a", "b", (), "p", "d", "", "same"),
        Fingerprint("a", "b", (), "p", "d", "", "same"),
    )
    assert identical == (True, {})


def test_s2_same_nodeid_different_body_is_not_pre_existing(tmp_path: Path) -> None:
    repo = _scratch_repo(tmp_path, legacy_ops=False)
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline")
    # Candidate: SAME nodeids, DIFFERENT body bytes -- both sides still red,
    # but the native definitions are no longer the same test.
    (repo / "tests" / "test_demo.py").write_text(
        "def test_x():\n    assert False, 'rewritten'\n\n\ndef test_y():\n    assert True\n",
        encoding="utf-8",
    )
    _commit_all(repo, "candidate rewrites body")
    exit_code, record = _compare(repo, tmp_path, "s2-native", target="tests/test_demo.py")
    assert record["verdict"] == "TEST_DEFINITION_CHANGED"
    assert exit_code != 0
    assert record["details"]["changed_test_files"] == ["tests/test_demo.py"]


def test_s1_uncommitted_controller_mutation_kills_identity(tmp_path: Path) -> None:
    controller_file = Path(trusted_controller.__file__).resolve()
    original_bytes = controller_file.read_bytes()
    repo = _scratch_repo(tmp_path, legacy_ops=True)
    (repo / "tests" / "test_demo.py").write_text(_FAILING_X, encoding="utf-8")
    _commit_all(repo, "baseline behavior")
    probe = tmp_path / "probe_regression.py"
    probe.write_text("def test_behavior():\n    assert False\n", encoding="utf-8")
    try:
        # Clean run: identity trusted, confirmation possible.
        exit_clean, record_clean = _compare(repo, tmp_path, "s1-clean", probe=probe)
        assert record_clean["verdict"] == "PRE_EXISTING_CONFIRMED"
        assert exit_clean == 0
        assert record_clean["controller"]["identity_trusted"] is True
        clean_sha = record_clean["controller"]["controller_closure_sha256"]
        assert record_clean["controller"]["controller_worktree_clean"] is True

        # Sabotage: mutate the controller WITHOUT committing. The old identity
        # must no longer validate, and confirmation must be impossible.
        controller_file.write_bytes(original_bytes + b"\n# UNCOMMITTED TAMPER\n")
        exit_dirty, record_dirty = _compare(repo, tmp_path, "s1-dirty", probe=probe)
        assert record_dirty["verdict"] == "CONTROLLER_IDENTITY_UNTRUSTED"
        assert exit_dirty != 0
        assert record_dirty["details"]["suppressed_verdict"] == "PRE_EXISTING_CONFIRMED"
        assert record_dirty["controller"]["identity_trusted"] is False
        assert record_dirty["controller"]["controller_closure_sha256"] != clean_sha
    finally:
        controller_file.write_bytes(original_bytes)

