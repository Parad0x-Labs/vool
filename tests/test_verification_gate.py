from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from ops.verify import REQUIRED_PYTHON, REQUIRED_TOOLS, StageResult, run_stage, run_verification

PY312 = SimpleNamespace(major=3, minor=12, micro=13)
TOOLS = dict(REQUIRED_TOOLS)


def _stage_runner(returncodes: tuple[int, ...], observed: list[tuple[str, ...]]):
    outcomes = iter(returncodes)

    def run(**kwargs):  # type: ignore[no-untyped-def]
        command = tuple(kwargs["command"])
        observed.append(command)
        log_path = Path(kwargs["log_path"])
        log_path.write_text("green-looking final line\n", encoding="utf-8")
        return StageResult(
            name=str(kwargs["name"]),
            command=command,
            returncode=next(outcomes),
            log_path=log_path,
        )

    return run


def test_gate_rejects_any_interpreter_other_than_ci_exact_version(tmp_path: Path) -> None:
    wrong = SimpleNamespace(major=3, minor=11, micro=15)

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=tmp_path / "logs",
        version_info=wrong,
        tool_versions=TOOLS,
    )

    assert rc == 1
    receipt = json.loads((tmp_path / "logs" / "verification-receipt.json").read_text())
    assert receipt["required_python"] == ".".join(map(str, REQUIRED_PYTHON))
    assert receipt["failure"] == "interpreter_version"


def test_lint_failure_stops_the_top_level_gate_red(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=tmp_path / "logs",
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((1,), observed),
    )

    assert rc == 1
    assert len(observed) == 1
    assert observed[0][1:4] == ("-m", "ruff", "check")


def test_failed_or_crashed_shard_stage_makes_top_level_gate_red(tmp_path: Path) -> None:
    for shard_rc in (1, 2, -11):
        observed: list[tuple[str, ...]] = []
        rc = run_verification(
            repo_root=tmp_path,
            log_dir=tmp_path / f"logs-{shard_rc}",
            version_info=PY312,
            tool_versions=TOOLS,
            stage_runner=_stage_runner((0, shard_rc), observed),
            tail_lines=1,
        )
        assert rc == 1
        assert len(observed) == 2


def test_success_requires_both_lint_and_pytest_zero(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []

    rc = run_verification(
        repo_root=tmp_path,
        paths=("tests/test_example.py",),
        log_dir=tmp_path / "logs",
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
        focused=True,
    )

    assert rc == 0
    assert "tests/test_example.py" in observed[1]
    assert "--artifact-root" in observed[1]
    receipt = json.loads((tmp_path / "logs" / "verification-receipt.json").read_text())
    assert receipt["outcome"] == "focused_passed"
    assert receipt["mode"] == "focused"
    assert receipt["targets"] == ["tests/test_example.py"]
    assert [stage["returncode"] for stage in receipt["stages"]] == [0, 0]


def test_authoritative_success_requires_full_default_scope(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=tmp_path / "logs",
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
    )

    assert rc == 0
    receipt = json.loads((tmp_path / "logs" / "verification-receipt.json").read_text())
    assert receipt["outcome"] == "passed"
    assert receipt["mode"] == "authoritative"
    assert receipt["targets"] == []


def test_explicit_targets_cannot_be_reported_as_authoritative(tmp_path: Path) -> None:
    rc = run_verification(
        repo_root=tmp_path,
        paths=("tests/test_example.py",),
        log_dir=tmp_path / "logs",
        version_info=PY312,
        tool_versions=TOOLS,
    )

    assert rc == 1
    receipt = json.loads((tmp_path / "logs" / "verification-receipt.json").read_text())
    assert receipt["failure"] == "focused_mode_required"


def test_selection_or_execution_pytest_arguments_are_rejected(tmp_path: Path) -> None:
    for position, unsafe in enumerate(("--collect-only", "-k", "--ignore=tests")):
        rc = run_verification(
            repo_root=tmp_path,
            pytest_args=(unsafe,),
            log_dir=tmp_path / f"logs-{position}",
            version_info=PY312,
            tool_versions=TOOLS,
        )
        assert rc == 1
        receipt = json.loads(
            (tmp_path / f"logs-{position}" / "verification-receipt.json").read_text()
        )
        assert receipt["failure"] == "unsafe_pytest_args"


def test_gate_rejects_floating_pytest_or_ruff_versions(tmp_path: Path) -> None:
    for package in REQUIRED_TOOLS:
        installed = dict(TOOLS)
        installed[package] = "999.0"
        rc = run_verification(
            repo_root=tmp_path,
            log_dir=tmp_path / f"logs-{package}",
            version_info=PY312,
            tool_versions=installed,
        )
        assert rc == 1


# ------------------------------------------------------- skip-drift truth in the receipt

_COUNT_KEYS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")


def _counts(*, passed: int = 0, skipped: int = 0) -> dict[str, int]:
    return {
        "passed": passed,
        "failed": 0,
        "skipped": skipped,
        "xfailed": 0,
        "xpassed": 0,
        "errors": 0,
        "executed_total": passed,
    }


def _plant_execution_manifest(
    log_dir: Path,
    *,
    shard: int,
    files: dict[str, dict[str, int]],
) -> Path:
    manifest = log_dir / "pytest-shards" / f"run-{shard}" / "logs" / f"shard-{shard}-execution.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    totals = {key: sum(counts[key] for counts in files.values()) for key in _COUNT_KEYS}
    totals["executed_total"] = sum(counts["executed_total"] for counts in files.values())
    totals["collected_total"] = sum(
        counts["passed"] + counts["skipped"] for counts in files.values()
    )
    totals["started_total"] = totals["collected_total"]
    payload = {
        "schema": "vool.pytest-execution.v1",
        "exitstatus": 0,
        "collected_nodeids": [],
        "started_nodeids": [],
        "summary": {"files": files, **totals},
    }
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _all_required_files_executing() -> dict[str, dict[str, int]]:
    from ops.pytest_shards import REQUIRED_EXECUTED_FILES

    return {name: _counts(passed=2) for name in REQUIRED_EXECUTED_FILES}


def test_receipt_carries_passed_failed_skipped_and_executed_counts(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []
    files = _all_required_files_executing()
    files["tests/test_extra_lane.py"] = _counts(skipped=7)
    log_dir = tmp_path / "logs"
    _plant_execution_manifest(log_dir, shard=1, files=files)
    _plant_execution_manifest(log_dir, shard=2, files={"tests/test_more.py": _counts(passed=1)})

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=log_dir,
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
    )

    assert rc == 0
    receipt = json.loads((log_dir / "verification-receipt.json").read_text())
    tests = receipt["tests"]
    assert tests["state"] == "aggregated"
    assert tests["shards"] == 2
    assert tests["totals"]["passed"] == 11
    assert tests["totals"]["skipped"] == 7
    assert tests["totals"]["executed_total"] == 11
    assert all(
        lane["state"] == "executed" for lane in tests["required_lanes"]["required_executed_files"].values()
    )


def test_authoritative_green_with_a_zero_executed_required_lane_is_red(tmp_path: Path) -> None:
    from ops.pytest_shards import REQUIRED_EXECUTED_FILES

    observed: list[tuple[str, ...]] = []
    files = _all_required_files_executing()
    silenced = REQUIRED_EXECUTED_FILES[1]
    files[silenced] = _counts(skipped=12)
    log_dir = tmp_path / "logs"
    _plant_execution_manifest(log_dir, shard=1, files=files)

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=log_dir,
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
    )

    assert rc == 1
    receipt = json.loads((log_dir / "verification-receipt.json").read_text())
    assert receipt["outcome"] == "failed"
    assert receipt["failure"] == "required_lane_zero_executed"
    lane = receipt["tests"]["required_lanes"]["required_executed_files"][silenced]
    assert lane == {"executed_total": 0, "skipped": 12, "state": "zero_executed"}


def test_authoritative_green_with_a_partially_skipped_required_lane_is_red(
    tmp_path: Path,
) -> None:
    from ops.pytest_shards import REQUIRED_EXECUTED_FILES

    observed: list[tuple[str, ...]] = []
    files = _all_required_files_executing()
    drifted = REQUIRED_EXECUTED_FILES[2]
    files[drifted] = _counts(passed=3, skipped=20)
    log_dir = tmp_path / "logs"
    _plant_execution_manifest(log_dir, shard=1, files=files)

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=log_dir,
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
    )

    assert rc == 1
    receipt = json.loads((log_dir / "verification-receipt.json").read_text())
    assert receipt["failure"] == "required_lane_zero_executed"
    lane = receipt["tests"]["required_lanes"]["required_executed_files"][drifted]
    assert lane == {"executed_total": 3, "skipped": 20, "state": "partially_skipped"}


def test_authoritative_green_with_zero_executed_suite_is_red(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []
    log_dir = tmp_path / "logs"
    _plant_execution_manifest(
        log_dir,
        shard=1,
        files={"tests/test_whatever.py": _counts(skipped=3)},
    )

    rc = run_verification(
        repo_root=tmp_path,
        log_dir=log_dir,
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
    )

    assert rc == 1
    receipt = json.loads((log_dir / "verification-receipt.json").read_text())
    assert receipt["failure"] == "zero_executed_suite"


def test_focused_mode_reports_counts_without_required_lane_enforcement(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []
    log_dir = tmp_path / "logs"
    _plant_execution_manifest(
        log_dir,
        shard=1,
        files={"tests/test_whatever.py": _counts(skipped=3)},
    )

    rc = run_verification(
        repo_root=tmp_path,
        paths=("tests/test_whatever.py",),
        log_dir=log_dir,
        version_info=PY312,
        tool_versions=TOOLS,
        stage_runner=_stage_runner((0, 0), observed),
        focused=True,
    )

    assert rc == 0
    receipt = json.loads((log_dir / "verification-receipt.json").read_text())
    assert receipt["tests"]["state"] == "aggregated"
    assert receipt["tests"]["totals"]["executed_total"] == 0


def test_stage_launch_and_timeout_fail_nonzero_without_log_parsing(tmp_path: Path) -> None:
    def launch_error(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError("missing executable")

    def timeout(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired("hung", 1)

    for runner in (launch_error, timeout):
        result = run_stage(
            name="sabotage",
            command=("does-not-matter",),
            repo_root=tmp_path,
            log_path=tmp_path / f"{runner.__name__}.log",
            timeout_seconds=1,
            runner=runner,
        )
        assert result.returncode != 0


def test_stage_timeout_reaps_the_owned_descendant_process_group(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    pid_path = tmp_path / "descendant.pid"
    shard_script = tmp_path / "shard.py"
    coordinator_script = tmp_path / "coordinator.py"
    shard_script.write_text(
        """
import os
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen(
    (
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
    )
)
Path(sys.argv[1]).write_text(f"{os.getpid()} {child.pid}", encoding="utf-8")
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )
    coordinator_script.write_text(
        """
import subprocess
import sys
import time

subprocess.Popen((sys.executable, sys.argv[1], sys.argv[2]))
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )

    result = run_stage(
        name="descendant-sabotage",
        command=(
            sys.executable,
            str(coordinator_script),
            str(shard_script),
            str(pid_path),
        ),
        repo_root=tmp_path,
        log_path=tmp_path / "descendant.log",
        timeout_seconds=0.5,
    )

    assert result.returncode == 1
    descendant_pids = tuple(map(int, pid_path.read_text(encoding="utf-8").split()))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if all(not _process_exists(pid) for pid in descendant_pids):
            break
        time.sleep(0.02)
    else:
        survivors = tuple(pid for pid in descendant_pids if _process_exists(pid))
        raise AssertionError(f"nested shard descendants survived the gate timeout: {survivors}")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
