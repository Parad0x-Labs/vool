from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ops.pytest_shards import (
    GATE_ENV_VAR,
    REQUIRED_EXECUTED_FILES,
    ShardAssignment,
    _execution_manifest_error,
    assignments_match_manifest,
    build_shard_command,
    collect_test_manifest,
    collection_manifest_gaps,
    discover_test_targets,
    partition_targets,
    run_collected_shards,
    run_shards,
    tracked_test_targets,
    validate_pytest_args,
)


def test_explicit_discovery_resolves_files_and_directories(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_alpha.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tests_dir / "nested").mkdir()
    (tests_dir / "nested" / "test_beta.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    targets = discover_test_targets(repo_root=repo_root, paths=("tests",))

    assert targets == ("tests/nested/test_beta.py", "tests/test_alpha.py")


def test_default_filesystem_discovery_is_rejected() -> None:
    try:
        discover_test_targets(repo_root=Path("."))
    except ValueError as exc:
        assert "canonical collector" in str(exc)
    else:
        raise AssertionError("a handcrafted default file walk could silently omit pytest scope")


def test_canonical_manifest_includes_configured_scope_outside_tests(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "Beta2_Website" / "tests").mkdir(parents=True)
    (repo_root / "tests" / "test_inside.py").write_text(
        "def test_inside():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "Beta2_Website" / "tests" / "test_outside.py").write_text(
        "def test_outside():\n    assert True\n", encoding="utf-8"
    )
    (repo_root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests", "."]\n', encoding="utf-8"
    )
    run_root = tmp_path / "run"
    run_root.mkdir()

    rc, manifest = collect_test_manifest(repo_root=repo_root, run_root=run_root)

    assert rc == 0 and manifest is not None
    assert manifest.item_count == 2
    assert manifest.targets == (
        "Beta2_Website/tests/test_outside.py",
        "tests/test_inside.py",
    )
    assert {nodeid for nodeid, _ in manifest.items} == {
        "Beta2_Website/tests/test_outside.py::test_outside",
        "tests/test_inside.py::test_inside",
    }


def test_collection_error_is_red(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_broken.py").write_text("def test_broken(:\n", encoding="utf-8")
    run_root = tmp_path / "run"
    run_root.mkdir()

    rc, manifest = collect_test_manifest(repo_root=repo_root, run_root=run_root, tail_lines=2)

    assert rc == 1
    assert manifest is None


def test_collection_timeout_is_red(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    run_root = tmp_path / "run"
    run_root.mkdir()

    def timeout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired("collector", 1)

    rc, manifest = collect_test_manifest(
        repo_root=repo_root,
        run_root=run_root,
        runner=timeout,
        timeout_seconds=0.01,
    )

    assert rc == 1
    assert manifest is None


def test_partition_is_complete_and_unrelated_files_do_not_reshuffle(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    targets = tuple(f"tests/test_{name}.py" for name in ("a", "b", "c", "d"))
    assignments = partition_targets(targets, repo_root=repo_root, workers=4)
    expanded = partition_targets((*targets, "tests/test_unrelated.py"), repo_root=repo_root, workers=4)

    assert assignments_match_manifest(targets, assignments)
    original_bucket = {
        target: assignment.index for assignment in assignments for target in assignment.targets
    }
    expanded_bucket = {
        target: assignment.index for assignment in expanded for target in assignment.targets
    }
    assert {target: expanded_bucket[target] for target in targets} == original_bucket


def test_manifest_comparison_rejects_omissions_and_duplicates() -> None:
    targets = ("tests/test_a.py", "tests/test_b.py")

    assert assignments_match_manifest(
        targets,
        (ShardAssignment(1, targets, 2),),
    )
    assert not assignments_match_manifest(
        targets,
        (ShardAssignment(1, ("tests/test_a.py",), 1),),
    )
    assert not assignments_match_manifest(
        targets,
        (ShardAssignment(1, (*targets, targets[0]), 3),),
    )


def test_tracked_collection_gap_catches_a_deleted_or_omitted_suite() -> None:
    tracked = ("tests/test_a.py", "Beta2_Website/tests/test_beta.py")

    assert collection_manifest_gaps(tracked, tracked) == ()
    assert collection_manifest_gaps(("tests/test_a.py",), tracked) == (
        "Beta2_Website/tests/test_beta.py",
    )


def test_repository_tracked_scope_includes_beta_and_excludes_legacy_script() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    targets = tracked_test_targets(repo_root)

    assert "Beta2_Website/tests/test_beta_website.py" in targets
    assert "tests/legacy/test_cas.py" not in targets


def test_build_shard_command_keeps_pytest_q_and_targets() -> None:
    assignment = ShardAssignment(
        index=1,
        targets=("tests/test_alpha.py", "tests/test_beta.py"),
        estimated_weight=123,
    )

    command = build_shard_command(assignment, pytest_args=("--tb=short",))

    assert command == (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_alpha.py",
        "tests/test_beta.py",
        "--tb=short",
    )


def test_only_display_safe_pytest_arguments_are_accepted() -> None:
    assert validate_pytest_args(("--tb=short",)) == ("--tb=short",)
    for unsafe in (
        "--collect-only",
        "--deselect=tests/test_a.py::test_a",
        "-k",
        "--ignore=Beta2_Website",
        "-p=no:verification",
        "--setup-plan",
    ):
        try:
            validate_pytest_args((unsafe,))
        except ValueError as exc:
            assert "unsafe pytest" in str(exc)
        else:
            raise AssertionError(f"selection/execution control was accepted: {unsafe}")

class _FakeProc:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", 1)
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _assignments() -> tuple[ShardAssignment, ...]:
    return (
        ShardAssignment(index=1, targets=("tests/test_alpha.py",), estimated_weight=10),
        ShardAssignment(index=2, targets=("tests/test_beta.py",), estimated_weight=20),
    )


def test_run_shards_assigns_unique_runtime_homes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    seen_homes: list[str] = []
    seen_shards: list[str] = []

    def fake_launcher(  # type: ignore[no-untyped-def]
        command, cwd, env, stdout, stderr, text, **process_options
    ):
        del command, cwd, stdout, stderr, text, process_options
        seen_homes.append(env["VOOL_HOME"])
        seen_shards.append(env["VOOL_TEST_SHARD"])
        return _FakeProc(0)

    rc = run_shards(
        _assignments(),
        repo_root=repo_root,
        pytest_args=("--tb=short",),
        shard_label="ci",
        launcher=fake_launcher,
        run_root=tmp_path / "run",
    )

    assert rc == 0
    assert len(set(seen_homes)) == 2
    assert seen_shards == ["1", "2"]
    receipt = json.loads((tmp_path / "run" / "shard-assignments.json").read_text())
    assert receipt["schema"] == "vool.pytest-shard-assignments.v1"
    assert [item["index"] for item in receipt["assignments"]] == [1, 2]


def test_shard_runtime_never_lands_inside_the_repository(tmp_path: Path) -> None:
    """A shard's VOOL_HOME must sit outside the checkout, whatever the artifact root is.

    Measured, not hypothetical: a shard runtime mints `data/keys/node_signing_key.json` on first
    use, CI runs the gate with `--log-dir=.verification-logs` INSIDE the checkout, and the runtime
    root used to hang off that. The authoritative run wrote four private keys into the working tree
    and then failed its own `tests/test_repo_hygiene_check.py` on them -- the gate failing itself.

    The artifact root here is deliberately shaped like CI's: a directory under the repo root. The
    evidence still belongs there; the process state must not.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    artifact_root = repo_root / ".verification-logs" / "pytest-shards"
    seen_homes: list[Path] = []

    def fake_launcher(  # type: ignore[no-untyped-def]
        command, cwd, env, stdout, stderr, text, **process_options
    ):
        del command, cwd, stdout, stderr, text, process_options
        seen_homes.append(Path(env["VOOL_HOME"]).resolve())
        return _FakeProc(0)

    rc = run_shards(
        _assignments(),
        repo_root=repo_root,
        shard_label="ci",
        launcher=fake_launcher,
        run_root=artifact_root,
    )

    assert rc == 0
    assert len(seen_homes) == 2
    resolved_repo = repo_root.resolve()
    for home in seen_homes:
        assert home != resolved_repo
        assert resolved_repo not in home.parents, f"shard runtime {home} is inside the repository"
    # And it does not outlive the run: a temp dir left behind every invocation is its own leak.
    for home in seen_homes:
        assert not home.parent.exists(), f"shard runtime root {home.parent} survived the run"
    # The evidence a reader actually needs still lands where the caller asked for it.
    assert (artifact_root / "shard-assignments.json").is_file()


def test_production_shards_inherit_the_coordinator_kill_domain(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    launch_options: list[dict[str, object]] = []
    monkeypatch.setattr("ops.pytest_shards.stop_owned_process_group_children", lambda: None)

    def fake_launcher(*_args, **kwargs):  # type: ignore[no-untyped-def]
        launch_options.append(
            {
                key: kwargs[key]
                for key in ("start_new_session", "creationflags")
                if key in kwargs
            }
        )
        return _FakeProc(0)

    rc = run_shards(
        (_assignments()[0],),
        repo_root=tmp_path,
        launcher=fake_launcher,
        run_root=tmp_path / "run",
        share_process_group=True,
    )

    assert rc == 0
    assert launch_options == [{}]


def test_any_failed_or_crashed_child_makes_parallel_gate_red(tmp_path: Path) -> None:
    outcomes = iter((0, -11))

    def fake_launcher(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return _FakeProc(next(outcomes))

    rc = run_shards(
        _assignments(),
        repo_root=tmp_path,
        launcher=fake_launcher,
        tail_lines=0,
    )

    assert rc == 1


def test_empty_assignments_are_red(tmp_path: Path) -> None:
    assert run_shards((), repo_root=tmp_path) == 1


def test_child_launch_failure_is_red(tmp_path: Path) -> None:
    def broken_launcher(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("cannot fork")

    assert run_shards(_assignments(), repo_root=tmp_path, launcher=broken_launcher) == 1


def test_hung_child_has_a_bounded_red_result_and_is_reaped(tmp_path: Path) -> None:
    process = _FakeProc(None)
    now = [0.0]

    def clock() -> float:
        return now[0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    rc = run_shards(
        (_assignments()[0],),
        repo_root=tmp_path,
        launcher=lambda *_args, **_kwargs: process,
        timeout_seconds=0.2,
        monotonic=clock,
        sleeper=advance,
    )

    assert rc == 1
    assert process.terminated is True


def test_shard_node_inventory_must_match_canonical_collection(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("VOOL_TEST_SHARD", raising=False)
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (tests_dir / "conftest.py").write_text(
        """
import os


def pytest_collection_modifyitems(items):
    if os.environ.get("VOOL_TEST_SHARD"):
        items[:] = items[:1]
""".lstrip(),
        encoding="utf-8",
    )
    (tests_dir / "test_inventory.py").write_text(
        "def test_one():\n    assert True\n\n"
        "def test_two():\n    assert True\n",
        encoding="utf-8",
    )

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=("tests/test_inventory.py",),
        workers=1,
        timeout_seconds=30,
        collection_timeout_seconds=30,
        tail_lines=20,
    )

    assert rc == 1


def test_pytest_control_environment_cannot_mask_execution(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (tests_dir / "test_environment.py").write_text(
        "def test_one():\n    assert True\n\n"
        "def test_two():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    artifacts = tmp_path / "artifacts"

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=("tests/test_environment.py",),
        workers=1,
        timeout_seconds=30,
        collection_timeout_seconds=30,
        tail_lines=20,
        artifact_root=artifacts,
    )

    assert rc == 0
    manifests = list(artifacts.glob("*/logs/shard-1-execution.json"))
    assert len(manifests) == 1
    execution = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert len(execution["collected_nodeids"]) == 2
    assert len(execution["started_nodeids"]) == 2


# ------------------------------------------------------- skip-drift truth (required lanes)

_OUTCOME_KEYS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")


def _file_counts(*, passed: int = 0, skipped: int = 0) -> dict[str, int]:
    return {
        "passed": passed,
        "failed": 0,
        "skipped": skipped,
        "xfailed": 0,
        "xpassed": 0,
        "errors": 0,
        "executed_total": passed,
    }


def _manifest_payload(
    *,
    nodeids: tuple[str, ...],
    files: dict[str, dict[str, int]],
    exitstatus: int = 0,
) -> dict:
    totals = {key: sum(counts[key] for counts in files.values()) for key in _OUTCOME_KEYS}
    totals["executed_total"] = sum(counts["executed_total"] for counts in files.values())
    totals["collected_total"] = len(nodeids)
    totals["started_total"] = len(nodeids)
    return {
        "schema": "vool.pytest-execution.v1",
        "exitstatus": exitstatus,
        "collected_nodeids": list(nodeids),
        "started_nodeids": list(nodeids),
        "summary": {"files": files, **totals},
    }


def _manifest_error_for(payload: dict) -> str:
    nodeids = tuple(str(item) for item in payload["collected_nodeids"])
    return _execution_manifest_error(Path("/unused.json"), expected_nodeids=nodeids)


def test_a_fully_skipped_shard_cannot_report_green(tmp_path: Path) -> None:
    payload = _manifest_payload(
        nodeids=("tests/test_lane.py::test_one", "tests/test_lane.py::test_two"),
        files={"tests/test_lane.py": _file_counts(skipped=2)},
    )
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert "zero executed tests" in error


def test_a_required_lane_that_skips_everything_is_red(tmp_path: Path) -> None:
    required = REQUIRED_EXECUTED_FILES[0]
    payload = _manifest_payload(
        nodeids=(f"{required}::test_one", "tests/test_other.py::test_two"),
        files={
            required: _file_counts(skipped=1),
            "tests/test_other.py": _file_counts(passed=1),
        },
    )
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert "required lane executed zero tests" in error
    assert required in error


def test_a_required_lane_whose_browser_scenarios_skip_while_unit_tests_pass_is_red(
    tmp_path: Path,
) -> None:
    """The measured live shape: '1 passed, 48 skipped' in a required file is not browser proof."""

    required = REQUIRED_EXECUTED_FILES[0]
    payload = _manifest_payload(
        nodeids=(f"{required}::test_unit", f"{required}::test_browser"),
        files={required: _file_counts(passed=1, skipped=1)},
    )
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert "required lane skipped scenarios" in error
    assert required in error


def test_a_healthy_manifest_with_some_skips_still_passes(tmp_path: Path) -> None:
    required = REQUIRED_EXECUTED_FILES[0]
    payload = _manifest_payload(
        nodeids=(f"{required}::test_one", f"{required}::test_two", "tests/test_other.py::test_x"),
        files={
            required: _file_counts(passed=2),
            "tests/test_other.py": _file_counts(skipped=1),
        },
    )
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert error == ""


def test_summary_numbers_that_disagree_with_their_breakdown_are_malformed(
    tmp_path: Path,
) -> None:
    payload = _manifest_payload(
        nodeids=("tests/test_lane.py::test_one",),
        files={"tests/test_lane.py": _file_counts(passed=1)},
    )
    payload["summary"]["passed"] = 99  # a fabricated total the files do not support
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert error.startswith("missing or malformed")


def test_a_manifest_without_the_outcome_summary_is_malformed(tmp_path: Path) -> None:
    payload = {
        "schema": "vool.pytest-execution.v1",
        "exitstatus": 0,
        "collected_nodeids": ["tests/test_lane.py::test_one"],
        "started_nodeids": ["tests/test_lane.py::test_one"],
    }
    manifest = tmp_path / "shard-1-execution.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    error = _execution_manifest_error(manifest, expected_nodeids=payload["collected_nodeids"])

    assert error.startswith("missing or malformed")


def test_full_scope_run_requires_the_required_lane_files_on_disk(tmp_path: Path) -> None:
    """A repo that is otherwise healthy and green must go red when a required suite is deleted.

    The synthetic repo is a git repo with a collectable, passing file, so the tracked-scope and
    collection guards below the existence check would let the run pass -- only the required-file
    existence guard stands between this repo and an empty green.
    """

    repo_root = tmp_path / "repo"
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    (repo_root / "tests" / "test_extra.py").write_text(
        "def test_extra():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(("git", "init", "-q", str(repo_root)), check=True, capture_output=True)

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=(),
        workers=1,
        timeout_seconds=120,
        collection_timeout_seconds=120,
        tail_lines=20,
    )

    assert rc == 1


_REQUIRED_GATE_BODY = (
    "import os\n\n\n"
    "def test_required_lane_runs_under_the_gate():\n"
    f"    assert os.environ.get({GATE_ENV_VAR!r}) == '1', "
    "'full-scope shards must run under the gate'\n"
)

_REQUIRED_NO_GATE_BODY = (
    "import os\n\n\n"
    "def test_extra_runs_outside_the_gate():\n"
    f"    assert not os.environ.get({GATE_ENV_VAR!r}), "
    "'explicit targets stay outside the gate'\n"
)

_REQUIRED_SKIP_BODY = (
    "import pytest\n\n\n"
    "def test_required_but_unavailable():\n"
    "    pytest.skip('no chromium build available')\n"
)


def _synthetic_gate_repo(tmp_path: Path, required_body: str) -> Path:
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8"
    )
    for name in REQUIRED_EXECUTED_FILES:
        path = repo_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(required_body, encoding="utf-8")
    (tests_dir / "test_extra.py").write_text(
        "def test_extra():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(("git", "init", "-q", str(repo_root)), check=True, capture_output=True)
    return repo_root


def test_full_scope_children_run_under_the_gate_env_and_required_lanes_execute(
    tmp_path: Path,
) -> None:
    repo_root = _synthetic_gate_repo(tmp_path, _REQUIRED_GATE_BODY)

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=(),
        workers=1,
        timeout_seconds=120,
        collection_timeout_seconds=120,
        tail_lines=20,
    )

    assert rc == 0, "a healthy full-scope run must execute the required lanes under VOOL_GATE"


def test_full_scope_required_lane_that_skips_everything_is_red(tmp_path: Path) -> None:
    repo_root = _synthetic_gate_repo(tmp_path, _REQUIRED_SKIP_BODY)

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=(),
        workers=1,
        timeout_seconds=120,
        collection_timeout_seconds=120,
        tail_lines=20,
    )

    assert rc == 1


def test_explicit_targets_stay_outside_the_gate_env(tmp_path: Path) -> None:
    repo_root = _synthetic_gate_repo(tmp_path, _REQUIRED_NO_GATE_BODY)

    rc = run_collected_shards(
        repo_root=repo_root,
        paths=("tests/test_extra.py",),
        workers=1,
        timeout_seconds=120,
        collection_timeout_seconds=120,
        tail_lines=20,
    )

    assert rc == 0, "focused/explicit-target runs remain dev-friendly (no VOOL_GATE)"
