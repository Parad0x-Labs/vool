"""Run one pytest shard and write the nodes it collected, started, failed and skipped."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

#: Outcome categories mirror pytest's terminal summary. ``executed_total`` counts every test that
#: reached a verdict (passed, failed, errored, xfailed, xpassed); only a skip never executed, so
#: a green shard whose collected tests were all skipped reports executed_total == 0.
_EXECUTED_OUTCOMES = ("passed", "failed", "errors", "xfailed", "xpassed")
_ALL_OUTCOMES = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")


def _counts_view(counts: dict[str, int]) -> dict[str, int]:
    view = {outcome: int(counts.get(outcome, 0)) for outcome in _ALL_OUTCOMES}
    view["executed_total"] = sum(view[outcome] for outcome in _EXECUTED_OUTCOMES)
    return view


class _ExecutionPlugin:
    def __init__(self, *, output_path: Path) -> None:
        self._output_path = output_path
        self._collected: list[str] = []
        self._started: list[str] = []
        self._totals: dict[str, int] = {}
        self._by_file: dict[str, dict[str, int]] = {}
        # Per-NODE inventories, recovered from audit/skip-accounting. The counts above
        # answer "how many skipped"; a baseline comparison needs "WHICH tests failed",
        # which counts cannot express. Both are kept: neither replaces the other.
        self._failed: list[dict[str, str]] = []
        self._skipped: list[dict[str, str]] = []
        self._xfailed: list[str] = []
        self._xpassed: list[str] = []

    def pytest_collection_finish(self, session: Any) -> None:
        self._collected = [str(item.nodeid) for item in session.items]

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        del location
        self._started.append(str(nodeid))

    def pytest_runtest_logreport(self, report: Any) -> None:
        outcome = _outcome_of(report)
        if outcome is None:
            return
        path = str(report.nodeid).split("::", 1)[0]
        self._totals[outcome] = self._totals.get(outcome, 0) + 1
        per_file = self._by_file.setdefault(path, {})
        per_file[outcome] = per_file.get(outcome, 0) + 1
        nodeid = str(report.nodeid)
        if outcome == "failed":
            self._failed.append({"nodeid": nodeid, "when": str(getattr(report, "when", ""))})
        elif outcome == "skipped":
            reason = ""
            longrepr = getattr(report, "longrepr", None)
            if isinstance(longrepr, tuple) and len(longrepr) == 3:
                # pytest renders this as "Skipped: <reason>"; the record carries the reason the
                # author wrote, so a receipt can quote it back without the framework's prefix.
                reason = str(longrepr[2])
                if reason.startswith("Skipped: "):
                    reason = reason[len("Skipped: "):]
            self._skipped.append({"nodeid": nodeid, "reason": reason})
        elif outcome == "xfailed":
            self._xfailed.append(nodeid)
        elif outcome == "xpassed":
            self._xpassed.append(nodeid)

    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        del session
        payload = {
            "schema": "vool.pytest-execution.v1",
            "exitstatus": int(exitstatus),
            "collected_nodeids": self._collected,
            "started_nodeids": self._started,
            # PASS != SKIP != XFAIL != DID-NOT-RUN, per node. A shard that proved nothing can never
            # read as a shard that passed everything, and a baseline comparison can attribute a red
            # to a specific test rather than to a count.
            "failed": sorted(self._failed, key=lambda entry: entry["nodeid"]),
            "skipped": sorted(self._skipped, key=lambda entry: entry["nodeid"]),
            "xfailed": sorted(self._xfailed),
            "xpassed": sorted(self._xpassed),
            "summary": {
                **_counts_view(self._totals),
                "collected_total": len(self._collected),
                "started_total": len(self._started),
                "files": {
                    path: _counts_view(counts) for path, counts in sorted(self._by_file.items())
                },
            },
        }
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _outcome_of(report: Any) -> str | None:
    """Classify one test report the way pytest's terminal summary would.

    A passed/failed setup or teardown is not a test verdict (only its failures are, as errors);
    ``wasxfail`` marks the expected-failure family. Classifying here, rather than re-parsing the
    terminal output later, is what makes the counts authoritative.
    """

    wasxfail = hasattr(report, "wasxfail")
    if report.passed:
        if report.when != "call":
            return None
        return "xpassed" if wasxfail else "passed"
    if report.failed:
        if report.when != "call":
            return "errors"
        return "failed"
    if report.skipped:
        return "xfailed" if wasxfail else "skipped"
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run pytest with an execution manifest plugin.")
    parser.add_argument("--output", required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    for variable in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_PLUGINS"):
        os.environ.pop(variable, None)
    plugin = _ExecutionPlugin(output_path=Path(args.output))
    return int(pytest.main(["-o", "addopts=", *pytest_args], plugins=[plugin]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
