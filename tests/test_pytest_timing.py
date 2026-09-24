"""Contract tests for the CI timing instrument (ops/pytest_timing.py).

The instrument's promise is narrow and absolute: it measures the shard pytest
ALREADY runs -- same argv, same order, same verdict -- and leaves evidence even
when that verdict is red. These tests pin each half of that promise, because a
measurement tool that changed what runs, or that swallowed a failure, would be
worse than no tool at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from ops.pytest_timing import (
    SCHEMA,
    _TimingPlugin,
)

WRAPPER = Path(__file__).resolve().parent.parent / "ops" / "pytest_timing.py"

#: Set for the one nested re-run the regression pin spawns, so the inner copy
#: of that same test does not recurse further (each level adds no evidence).
_NESTED_PIN_ENV = "VOOL_TEST_TIMING_NESTED_PIN"


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


def _run_wrapper(
    output: Path, *pytest_targets: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(extra_env or {})}
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
        env=env,
    )


def _collect_only(*pytest_targets: Path) -> list[str]:
    """Pytest's own node inventory for an argv -- the oracle for how the same
    invocation keys its files, independent of where pytest's rootdir lands
    (a target outside the checkout is keyed by its own rootdir, not basename)."""

    plain = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *[str(target) for target in pytest_targets],
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(WRAPPER.parent.parent),
    )
    return [line for line in plain.stdout.splitlines() if "::" in line]


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
    # One record, keyed the way this argv's own collection keys the file: the
    # nodeid prefix however pytest's rootdir expresses it, never a guessed
    # basename (a target outside the checkout is NOT keyed by basename).
    (ok_key,) = {nodeid.split("::", 1)[0] for nodeid in _collect_only(ok)}
    assert set(payload["files"]) == {ok_key}
    record = payload["files"][ok_key]
    assert record["collected_nodes"] == 1
    assert record["started_nodes"] == 1
    assert record["passed"] == 1
    assert record["call_seconds"] > 0
    assert record["total_seconds"] >= record["call_seconds"]


def test_failing_test_stays_red_and_failure_is_recorded_with_diagnostics(tmp_path: Path) -> None:
    _, bad = _write_suite(tmp_path)
    output = tmp_path / "timing.json"
    collected = _collect_only(bad)

    completed = _run_wrapper(output, bad)

    # The wrapped pytest's verdict is THE verdict: exit 1 propagates untouched.
    assert completed.returncode == 1
    payload = _load(output)
    assert payload["exitstatus"] == 1
    # Evidence exists beside the red verdict, and names the failing node.
    assert payload["complete"] is True
    (bad_key,) = {nodeid.split("::", 1)[0] for nodeid in collected}
    record = payload["files"][bad_key]
    assert record["failed"] == 1
    assert record["passed"] == 1
    failed = payload["failed_nodes"]
    failing = [nodeid for nodeid in collected if nodeid.endswith("::test_fails")]
    assert [entry["nodeid"] for entry in failed] == failing
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
    # One file record per file, keyed the way this argv's own collection keys
    # them (the nodeid prefix), whatever rootdir pytest assigns here.
    assert set(payload["files"]) == {nodeid.split("::", 1)[0] for nodeid in plain_nodeids}
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


def test_manifest_write_failure_is_red_not_green(tmp_path: Path) -> None:
    """A manifest write that cannot land turns the wrapper red -- proven at the
    process boundary against a genuinely unwritable target.

    The earlier in-process form monkeypatched ``Path.write_text`` for the whole
    process while calling the wrapper's main() from inside an outer pytest
    session. When this suite itself runs under the timing wrapper, that global
    patch also hit the OUTER instrument's periodic snapshot (CI run
    35873216239: the snapshot boundary fell inside the patched window, the
    wrapper correctly retained write_error, and an all-green shard went red).
    The fault now lives exactly where the failure lives -- the output path is
    an existing directory, so the manifest write fails with a real OSError --
    and no foreign writer in any outer session is sabotaged."""
    ok, _ = _write_suite(tmp_path)
    unwritable = tmp_path / "occupied-by-a-directory"
    unwritable.mkdir()

    completed = _run_wrapper(unwritable, ok)

    # The suite itself passes; the wrapper is red SOLELY because its evidence
    # could not be written. Missing evidence can never read as green.
    assert "1 passed" in completed.stdout
    assert completed.returncode == 1
    assert "timing manifest could not be written" in completed.stderr
    assert "Error" in completed.stderr  # the recorded exception class (IsADirectoryError here)


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


def test_nested_fault_injection_cannot_redden_the_outer_instrument(tmp_path: Path) -> None:
    """Regression pin for CI run 35873216239, with the REAL wrapper around THIS
    file and the snapshot boundary aligned onto the fault test.

    The instrument snapshots its manifest every 512 test reports. The filler
    file (164 passing tests) plus this file's six tests preceding the fault
    test account for 510 reports; the fault test's setup report is #511, so
    its CALL report -- the one that used to arrive while the retired
    process-global ``Path.write_text`` patch was live -- is exactly #512.
    With the fault confined to the child process, the outer wrapper must stay
    green, write its manifest complete, and never report a write failure."""
    if os.environ.get(_NESTED_PIN_ENV) == "1":
        # This is the inner re-run the outer instance of this test spawns:
        # recursing another level adds no evidence, so keep the suite green.
        return
    filler = tmp_path / "test_filler.py"
    filler.write_text(
        "".join(f"def test_filler_{index}():\n    assert True\n" for index in range(164)),
        encoding="utf-8",
    )
    output = tmp_path / "outer-timing.json"

    completed = _run_wrapper(output, filler, Path(__file__), extra_env={_NESTED_PIN_ENV: "1"})

    assert completed.returncode == 0, completed.stderr
    assert "timing manifest could not be written" not in completed.stderr
    payload = _load(output)
    assert payload["complete"] is True
    assert payload["exitstatus"] == 0
    # This file's record, keyed however this argv's own collection keys it.
    collected = _collect_only(filler, Path(__file__))
    this_key = next(
        prefix
        for prefix in (nodeid.split("::", 1)[0] for nodeid in collected)
        if prefix.endswith("test_pytest_timing.py")
    )
    this_record = payload["files"][this_key]
    assert this_record["collected_nodes"] == 9
    assert this_record["started_nodes"] == 9
