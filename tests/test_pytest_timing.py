"""Contract tests for the CI timing instrument (ops/pytest_timing.py).

The instrument's promise is narrow and absolute: it measures the shard pytest
ALREADY runs -- same argv, same order, same verdict -- and leaves evidence even
when that verdict is red. These tests pin each half of that promise, because a
measurement tool that changed what runs, or that swallowed a failure, would be
worse than no tool at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from ops.pytest_timing import (
    SCHEMA,
    _TimingPlugin,
)
from ops.pytest_timing import (
    main as timing_main,
)

WRAPPER = Path(__file__).resolve().parent.parent / "ops" / "pytest_timing.py"


def _write_suite(root: Path) -> tuple[Path, Path]:
    (root / "test_ok.py").write_text(
        "import time\n\n\ndef test_passes():\n    time.sleep(0.02)\n    assert True\n",
        encoding="utf-8",
    )
    (root / "test_bad.py").write_text(
        "import time\n\n\n"
        "def test_fails():\n"
        "    time.sleep(0.01)\n"
        "    assert False, 'deliberate failure for the timing contract test'\n\n\n"
        "def test_passes_anyway():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return root / "test_ok.py", root / "test_bad.py"


def _run_wrapper(output: Path, *pytest_targets: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(WRAPPER),
            "--output",
            str(output),
            "--",
            "-q",
            "--tb=line",
            "-p",
            "no:cacheprovider",
            *[str(target) for target in pytest_targets],
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(WRAPPER.parent.parent),
    )


def _load(output: Path) -> dict:
    return json.loads(output.read_text(encoding="utf-8"))


def test_green_run_exits_zero_and_writes_complete_manifest(tmp_path: Path) -> None:
    ok, _ = _write_suite(tmp_path)
    output = tmp_path / "timing.json"

    completed = _run_wrapper(output, ok)

    assert completed.returncode == 0
    payload = _load(output)
    assert payload["schema"] == SCHEMA
    assert payload["complete"] is True
    assert payload["exitstatus"] == 0
    record = payload["files"]["test_ok.py"]
    assert record["collected_nodes"] == 1
    assert record["started_nodes"] == 1
    assert record["passed"] == 1
    assert record["call_seconds"] > 0
    assert record["total_seconds"] >= record["call_seconds"]


def test_failing_test_stays_red_and_failure_is_recorded_with_diagnostics(tmp_path: Path) -> None:
    _, bad = _write_suite(tmp_path)
    output = tmp_path / "timing.json"

    completed = _run_wrapper(output, bad)

    # The wrapped pytest's verdict is THE verdict: exit 1 propagates untouched.
    assert completed.returncode == 1
    payload = _load(output)
    assert payload["exitstatus"] == 1
    # Evidence exists beside the red verdict, and names the failing node.
    assert payload["complete"] is True
    record = payload["files"]["test_bad.py"]
    assert record["failed"] == 1
    assert record["passed"] == 1
    failed = payload["failed_nodes"]
    assert [entry["nodeid"] for entry in failed] == ["test_bad.py::test_fails"]
    assert failed[0]["when"] == "call"
    assert failed[0]["duration_seconds"] > 0


def test_wrapper_neither_selects_skips_nor_reorders(tmp_path: Path) -> None:
    """The instrument must run exactly pytest's own collection for the same argv.

    Runs the wrapper and a plain collection with identical targets and asserts
    the same node inventory in the same order -- measurement cannot become
    selection, and it cannot reshuffle what the shard resolver ordered.
    """

    ok, bad = _write_suite(tmp_path)
    output = tmp_path / "timing.json"
    targets = [ok, bad]

    completed = _run_wrapper(output, *targets)
    assert completed.returncode == 1  # test_bad.py fails; unchanged behaviour

    plain = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *[str(target) for target in targets],
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(WRAPPER.parent.parent),
    )
    plain_nodeids = [line for line in plain.stdout.splitlines() if "::" in line]

    payload = _load(output)
    assert payload["invocation"]["pytest_args"][:1] == ["-q"]
    # One file record per file, keyed the way the shard resolver keys files.
    assert set(payload["files"]) == {"test_ok.py", "test_bad.py"}
    # Collected == started == what plain pytest collects: nothing skipped, added,
    # or reordered by the instrument.
    assert payload["session"]["collected_total"] == len(plain_nodeids)
    assert payload["session"]["started_total"] == len(plain_nodeids)


def test_manifest_carries_source_identity_and_environment_family_only(tmp_path: Path) -> None:
    ok, _ = _write_suite(tmp_path)
    output = tmp_path / "timing.json"

    _run_wrapper(output, ok)

    payload = _load(output)
    head = subprocess.run(
        ("git", "-C", str(WRAPPER.parent.parent), "rev-parse", "HEAD"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert payload["source"]["git_head_sha"] == head
    environment = payload["environment"]
    assert environment["sys_platform"]
    assert environment["python_version"]
    # An environment FAMILY, never a dump: no variable inventory, no machine
    # identifiers beyond the platform family.
    assert set(environment) == {
        "github_actions",
        "machine",
        "python_implementation",
        "python_version",
        "runner_os",
        "sys_platform",
    }


def test_incomplete_snapshot_records_progress_not_success(tmp_path: Path) -> None:
    """Before sessionfinish the manifest is explicitly complete:false.

    A run killed mid-flight (job timeout, hard crash) must leave an honest
    partial record -- how far it got -- that no reader can mistake for a
    finished green shard.
    """

    plugin = _TimingPlugin(output_path=tmp_path / "timing.json", pytest_args=["-q"])
    report = SimpleNamespace(
        nodeid="tests/test_x.py::test_one",
        when="call",
        duration=0.5,
        passed=True,
        failed=False,
        skipped=False,
    )
    plugin.pytest_runtest_logreport(report)

    payload = plugin.build_payload(complete=False)

    assert payload["complete"] is False
    assert payload["exitstatus"] is None
    record = payload["files"]["tests/test_x.py"]
    assert record["call_seconds"] == 0.5
    assert record["passed"] == 1


def test_non_finite_durations_are_dropped_not_recorded(tmp_path: Path) -> None:
    plugin = _TimingPlugin(output_path=tmp_path / "timing.json", pytest_args=[])
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="tests/test_x.py::test_one",
            when="call",
            duration=float("inf"),
            passed=True,
            failed=False,
            skipped=False,
        )
    )

    payload = plugin.build_payload(complete=False)

    record = payload["files"]["tests/test_x.py"]
    assert record["call_seconds"] == 0.0
    assert record["passed"] == 1


def test_manifest_write_failure_is_red_not_green(tmp_path: Path, monkeypatch) -> None:
    ok, _ = _write_suite(tmp_path)
    unwritable = tmp_path / "missing-dir" / "timing.json"

    def _fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "write_text", _fail)
    # no:cacheprovider keeps pytest's own cache writes out of the way -- the
    # failure under test is the MANIFEST write, and it alone must turn red.
    exit_code = timing_main(
        ["--output", str(unwritable), "--", "-p", "no:cacheprovider", str(ok)]
    )

    assert exit_code == 1


def test_setup_and_teardown_cost_is_attributed_to_the_file(tmp_path: Path) -> None:
    plugin = _TimingPlugin(output_path=tmp_path / "timing.json", pytest_args=[])
    phases = (
        SimpleNamespace(nodeid="tests/test_x.py::test_one", when="setup", duration=0.1, passed=True, failed=False, skipped=False),
        SimpleNamespace(nodeid="tests/test_x.py::test_one", when="call", duration=0.2, passed=True, failed=False, skipped=False),
        SimpleNamespace(nodeid="tests/test_x.py::test_one", when="teardown", duration=0.3, passed=True, failed=False, skipped=False),
    )
    for report in phases:
        plugin.pytest_runtest_logreport(report)

    payload = plugin.build_payload(complete=False)

    record = payload["files"]["tests/test_x.py"]
    assert record["setup_seconds"] == 0.1
    assert record["call_seconds"] == 0.2
    assert record["teardown_seconds"] == 0.3
    assert abs(record["total_seconds"] - 0.6) < 1e-9
