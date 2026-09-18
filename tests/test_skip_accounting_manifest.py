"""PASS != SKIP != XFAIL != DID-NOT-RUN at the authoritative execution manifest.

The pre-repair hole: the execution manifest recorded only collected/started nodeids, so a shard
whose every test self-skipped produced exitstatus 0 with collected == started == expected and was
indistinguishable from an all-passed shard -- proven in audit by a ``@pytest.mark.skip`` test
whose body is ``assert False``. These tests pin the repaired behavior: skips are recorded as
first-class manifest entries, an all-skipped shard fails execution-manifest verification, and a
partially-skipped shard stays green while surfacing exactly which nodes proved nothing.

Each case drives the REAL plugin through pytest.main (in-process) or the real module entry point
(subprocess), never a hand-written manifest, except where a manifest's shape itself is under test.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ops.pytest_execution import _ExecutionPlugin
from ops.pytest_shards import _execution_manifest_error, skip_accounting

REPO = Path(__file__).resolve().parents[1]

PASS_FILE = "def test_ok():\n    assert True\n"
FAIL_FILE = "def test_bad():\n    assert False\n"
SKIP_FILE = (
    "import pytest\n"
    "@pytest.mark.skip(reason='audit demo')\n"
    "def test_should_fail_if_executed():\n    assert False\n"
)
XFAIL_FILE = (
    "import pytest\n"
    "@pytest.mark.xfail(reason='known gap')\n"
    "def test_known_gap():\n    assert False\n"
)


def _run_plugin(tmp_path: Path, request: pytest.FixtureRequest, *sources: str) -> dict:
    # Unique basenames: two tmp_dirs with the same test-file basename collide in sys.modules
    # ("import file mismatch") once an inner run has imported the first one.
    slug = request.node.name.replace("test_", "").replace("_", "")[:20]
    for index, source in enumerate(sources):
        (tmp_path / f"test_{slug}_{index}.py").write_text(source, encoding="utf-8")
    output = tmp_path / "manifest.json"
    exitcode = pytest.main(
        [
            "-o", "addopts=",
            "-p", "no:cacheprovider",
            "--rootdir", str(tmp_path),
            str(tmp_path),
        ],
        plugins=[_ExecutionPlugin(output_path=output)],
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    return {"exitcode": int(exitcode), **payload}


def _expected_nodeids(payload: dict) -> tuple[str, ...]:
    return tuple(str(item) for item in payload["collected_nodeids"])


def test_passing_test_is_recorded_without_skip_records(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    run = _run_plugin(tmp_path, request, PASS_FILE)
    assert run["exitcode"] == 0
    assert run["skipped"] == [] and run["xfailed"] == [] and run["xpassed"] == []
    assert len(run["started_nodeids"]) == 1


def test_failing_test_is_failed_and_exit_nonzero(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    run = _run_plugin(tmp_path, request, FAIL_FILE)
    assert run["exitcode"] != 0
    assert run["skipped"] == []


def test_skipped_test_with_false_body_records_skipped_not_passed(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """The sabotage: the body would fail if executed. The manifest must say SKIPPED."""
    run = _run_plugin(tmp_path, request, SKIP_FILE)
    assert run["exitcode"] == 0  # pytest exits zero; that alone must not read as proof
    assert len(run["skipped"]) == 1
    record = run["skipped"][0]
    assert record["nodeid"] == _expected_nodeids(run)[0]
    assert record["reason"] == "audit demo"


def test_mixed_pass_and_skip_reports_both_accurately(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    run = _run_plugin(tmp_path, request, PASS_FILE, SKIP_FILE)
    assert run["exitcode"] == 0
    skipped_ids = {entry["nodeid"] for entry in run["skipped"]}
    assert len(skipped_ids) == 1
    passed_ids = set(_expected_nodeids(run)) - skipped_ids
    assert len(passed_ids) == 1


def test_xfail_is_distinct_from_plain_skip(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    run = _run_plugin(tmp_path, request, XFAIL_FILE)
    assert run["exitcode"] == 0
    assert run["skipped"] == []
    assert len(run["xfailed"]) == 1
    assert run["xpassed"] == []


def test_all_skipped_shard_fails_execution_manifest_verification() -> None:
    """An all-skip shard can no longer be described as equivalent to an all-pass one."""
    error = _execution_manifest_error(
        _manifest(
            {
                "exitstatus": 0,
                "collected_nodeids": ["a.py::test_one"],
                "started_nodeids": ["a.py::test_one"],
                "skipped": [{"nodeid": "a.py::test_one"}],
            }
        ),
        expected_nodeids=("a.py::test_one",),
    )
    assert "proved nothing" in error


def test_partially_skipped_shard_remains_green_and_counted() -> None:
    """Legitimate partial skips stay possible (release policy is not zero-skip)."""
    error = _execution_manifest_error(
        _manifest(
            {
                "exitstatus": 0,
                "collected_nodeids": ["a.py::test_a", "a.py::test_b"],
                "started_nodeids": ["a.py::test_a", "a.py::test_b"],
                "skipped": [{"nodeid": "a.py::test_b"}],
            }
        ),
        expected_nodeids=("a.py::test_a", "a.py::test_b"),
    )
    assert error == ""


def test_skip_record_outside_collection_is_rejected() -> None:
    error = _execution_manifest_error(
        _manifest(
            {
                "exitstatus": 0,
                "collected_nodeids": ["a.py::test_a"],
                "started_nodeids": ["a.py::test_a"],
                "skipped": [{"nodeid": "ghost.py::test_never_there"}],
            }
        ),
        expected_nodeids=("a.py::test_a",),
    )
    assert "outside canonical collection" in error


def test_manifest_missing_skip_fields_is_rejected() -> None:
    """A manifest from before skip accounting cannot pass the new validator."""
    path = Path(tempfile.mkdtemp()) / "old.json"
    path.write_text(json.dumps({
        "schema": "vool.pytest-execution.v1",
        "exitstatus": 0,
        "collected_nodeids": ["a.py::test_a"],
        "started_nodeids": ["a.py::test_a"],
    }), encoding="utf-8")
    assert _execution_manifest_error(path, expected_nodeids=("a.py::test_a",)) != ""


def test_subprocess_entry_point_writes_skip_accounting(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    (tmp_path / "test_sabotage_probe.py").write_text(SKIP_FILE, encoding="utf-8")
    output = tmp_path / "manifest.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO / "ops" / "pytest_execution.py"),
            "--output", str(output),
            "--",
            "-q",
            "-p", "no:cacheprovider",
            str(tmp_path / "test_sabotage_probe.py"),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0  # pytest-level exit; the manifest tells the truth
    payload = json.loads(output.read_text(encoding="utf-8"))
    skipped_ids = [entry["nodeid"] for entry in payload["skipped"]]
    assert len(skipped_ids) == 1
    assert skipped_ids[0].endswith("test_sabotage_probe.py::test_should_fail_if_executed")
    accounting = skip_accounting(output)
    assert accounting["skipped"] == 1 and accounting["xfailed"] == 0


def _manifest(fields: dict) -> Path:
    """A synthetic manifest that is INTERNALLY CONSISTENT.

    The manifest gained a `summary` block (per-outcome counts, cross-checked against a per-file
    breakdown) after this test was written. A hand-written summary that disagrees with the node
    lists is rejected on shape before the skip rules under test are ever reached, so the summary
    is derived here from the same fields each test supplies.
    """

    collected = [str(item) for item in fields.get("collected_nodeids", [])]
    skipped = [e for e in fields.get("skipped", []) if isinstance(e, dict) and e.get("nodeid")]
    xfailed = [str(item) for item in fields.get("xfailed", [])]
    xpassed = [str(item) for item in fields.get("xpassed", [])]
    failed = [e for e in fields.get("failed", []) if isinstance(e, dict) and e.get("nodeid")]

    non_pass = {e["nodeid"] for e in skipped} | set(xfailed) | set(xpassed) | {e["nodeid"] for e in failed}
    passed_nodes = [node for node in collected if node not in non_pass]

    per_file: dict[str, dict[str, int]] = {}

    def _bump(nodeid: str, outcome: str) -> None:
        bucket = per_file.setdefault(str(nodeid).split("::", 1)[0], {})
        bucket[outcome] = bucket.get(outcome, 0) + 1

    for node in passed_nodes:
        _bump(node, "passed")
    for entry in failed:
        _bump(entry["nodeid"], "failed")
    for entry in skipped:
        _bump(entry["nodeid"], "skipped")
    for node in xfailed:
        _bump(node, "xfailed")
    for node in xpassed:
        _bump(node, "xpassed")

    summary = {
        "passed": len(passed_nodes),
        "failed": len(failed),
        "skipped": len(skipped),
        "xfailed": len(xfailed),
        "xpassed": len(xpassed),
        "errors": 0,
        "executed_total": len(passed_nodes) + len(failed),
        "collected_total": len(collected),
        "started_total": len(fields.get("started_nodeids", collected)),
        # Every per-file bucket must carry ALL outcome keys plus its own executed_total: the
        # validator cross-checks each file's numbers against the totals and reads each key by name.
        "files": {
            path_: {
                **{key: counts.get(key, 0) for key in
                   ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")},
                "executed_total": counts.get("passed", 0) + counts.get("failed", 0)
                + counts.get("xfailed", 0) + counts.get("xpassed", 0) + counts.get("errors", 0),
            }
            for path_, counts in sorted(per_file.items())
        },
    }

    path = Path(tempfile.mkdtemp()) / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "vool.pytest-execution.v1",
                "xfailed": [],
                "xpassed": [],
                "failed": [],
                "summary": summary,
                **fields,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path
