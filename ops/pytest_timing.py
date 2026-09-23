"""Run one pytest shard and write a machine-readable per-file timing manifest.

This is the CI measurement instrument, not a gate: it wraps the EXACT pytest
invocation the shard already runs and records what pytest did, when, for how
long, and with what outcome. Its contract with the shard step is:

  * it never selects, skips, reorders, or retries tests -- the argv it forwards
    is the argv that runs, and pytest's own collection decides the nodes;
  * the wrapped pytest's exit status IS this command's exit status -- a red
    shard stays red, a green shard stays green;
  * the timing manifest is written even when tests fail, so evidence uploads
    (`if: always()` on the artifact step) alongside the red verdict;
  * a snapshot with ``"complete": false`` is flushed periodically, so a run
    killed mid-flight (job timeout, hard crash) still leaves the evidence of
    HOW FAR it got -- started-but-unfinished is recorded, never guessed.

Per-file cost is the sum of pytest's own phase durations: collection reports
for the file plus every node's setup, call, and teardown reports. That is the
cost a shard planner actually balances; wall-clock of the whole session is
recorded separately and never attributed to a single file.

The manifest records source identity (git HEAD, dirty flag) and a minimal
environment fingerprint (python/platform family) so planning can refuse to mix
timings across incompatible environments. It deliberately contains NO
environment dump and NO machine inventory: nothing here is a secret, and
nothing here is unrelated telemetry.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Script-mode bootstrap (same pattern as ops/benchmark_vool.py, ops/repo_hygiene_check.py):
# this runs as `python ops/pytest_timing.py` from the repo root, which puts ops/ -- not the
# repo root -- at sys.path[0]. The outcome classifier is reused from the existing execution
# instrument rather than duplicated: one authority for "what pytest's report means".
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest

from ops.pytest_execution import _outcome_of

#: Schema of the manifest this instrument writes. Consumers (the shard planner)
#: validate against this string and refuse anything else.
SCHEMA = "vool.pytest-timing.v1"

#: The manifest is rewritten with ``complete: false`` every N test reports so a
#: killed run leaves its progress on disk. One shard carries a few thousand
#: nodes; at this cadence the rewrite cost is a handful of small writes.
_SNAPSHOT_EVERY_REPORTS = 512

_OUTCOME_KEYS = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")


def _finite(value: Any) -> float | None:
    """Accept only a real, non-negative, finite duration; anything else is None."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


class _FileTiming:
    """Running per-file cost and outcome counters."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.collect_seconds = 0.0
        self.setup_seconds = 0.0
        self.call_seconds = 0.0
        self.teardown_seconds = 0.0
        self.collected_nodes = 0
        self.started_nodes = 0
        self.outcomes: dict[str, int] = {key: 0 for key in _OUTCOME_KEYS}

    def record_duration(self, when: str, seconds: float) -> None:
        if when == "setup":
            self.setup_seconds += seconds
        elif when == "call":
            self.call_seconds += seconds
        elif when == "teardown":
            self.teardown_seconds += seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "collected_nodes": self.collected_nodes,
            "started_nodes": self.started_nodes,
            "collect_seconds": round(self.collect_seconds, 6),
            "setup_seconds": round(self.setup_seconds, 6),
            "call_seconds": round(self.call_seconds, 6),
            "teardown_seconds": round(self.teardown_seconds, 6),
            "total_seconds": round(
                self.collect_seconds + self.setup_seconds + self.call_seconds + self.teardown_seconds,
                6,
            ),
            **{key: self.outcomes[key] for key in _OUTCOME_KEYS},
        }


class _TimingPlugin:
    def __init__(self, *, output_path: Path, pytest_args: Sequence[str]) -> None:
        self._output_path = output_path
        self._pytest_args = [str(item) for item in pytest_args]
        self._files: dict[str, _FileTiming] = {}
        self._failed_nodes: list[dict[str, Any]] = []
        self._started_at = time.monotonic()
        self._started_wall = time.time()
        self._collect_seconds_by_key: dict[str, float] = {}
        self._other_collect_seconds = 0.0
        self._reports_since_snapshot = 0
        self._complete = False
        self._exitstatus: int | None = None
        #: Set when the manifest itself could not be written; the wrapper turns
        #: this into a red exit so missing evidence can never read as green.
        self.write_error: str | None = None

    # -- collection -----------------------------------------------------

    def pytest_collectreport(self, report: Any) -> None:
        seconds = _finite(getattr(report, "duration", None))
        if seconds is None:
            return
        nodeid = str(report.nodeid)
        key = nodeid.split("::", 1)[0]
        if key.endswith(".py"):
            self._collect_seconds_by_key[key] = self._collect_seconds_by_key.get(key, 0.0) + seconds
        else:
            # Directory/package/session collectors: real cost, but not attributable
            # to any one test file. Recorded as session overhead, never dropped.
            self._other_collect_seconds += seconds

    def pytest_collection_finish(self, session: Any) -> None:
        # File identity is the nodeid prefix -- the same identity the CI shard
        # resolver and ops/pytest_manifest.py use (rootdir-relative path). Using
        # item.path here instead would key the same file twice (absolute path
        # from collection, rootdir-relative from reports).
        for item in session.items:
            path = str(item.nodeid).split("::", 1)[0].replace("\\", "/")
            timing = self._files.setdefault(path, _FileTiming(path))
            timing.collected_nodes += 1
        for key, seconds in self._collect_seconds_by_key.items():
            if key in self._files:
                self._files[key].collect_seconds += seconds
            else:
                # Collection cost of a module that yielded no test nodes (or a
                # non-test module pytest still imported): session overhead.
                self._other_collect_seconds += seconds
        self._collect_seconds_by_key = {}

    # -- execution ------------------------------------------------------

    def pytest_runtest_logstart(self, nodeid: str, location: Any) -> None:
        del location
        path = nodeid.split("::", 1)[0]
        timing = self._files.setdefault(path, _FileTiming(path))
        timing.started_nodes += 1

    def pytest_runtest_logreport(self, report: Any) -> None:
        nodeid = str(report.nodeid)
        path = nodeid.split("::", 1)[0]
        timing = self._files.setdefault(path, _FileTiming(path))
        when = str(getattr(report, "when", ""))
        seconds = _finite(getattr(report, "duration", None))
        if seconds is not None:
            timing.record_duration(when, seconds)
        outcome = _outcome_of(report)
        if outcome is not None:
            timing.outcomes[outcome] = timing.outcomes.get(outcome, 0) + 1
            if outcome == "failed":
                self._failed_nodes.append(
                    {
                        "nodeid": nodeid,
                        "when": when,
                        "duration_seconds": seconds if seconds is not None else None,
                    }
                )
        self._reports_since_snapshot += 1
        if self._reports_since_snapshot >= _SNAPSHOT_EVERY_REPORTS:
            self._reports_since_snapshot = 0
            self._flush(complete=False)

    # -- session end ------------------------------------------------------

    def pytest_sessionfinish(self, session: Any, exitstatus: Any) -> None:
        del session
        self._exitstatus = int(exitstatus)
        self._complete = True
        self._flush(complete=True)

    # -- manifest ---------------------------------------------------------

    def build_payload(self, *, complete: bool) -> dict[str, Any]:
        started = sum(timing.started_nodes for timing in self._files.values())
        executed = sum(
            sum(timing.outcomes[key] for key in _OUTCOME_KEYS if key != "skipped")
            for timing in self._files.values()
        )
        return {
            "schema": SCHEMA,
            "complete": complete,
            "exitstatus": self._exitstatus,
            "session": {
                "wall_seconds": round(time.monotonic() - self._started_at, 6),
                "started_at_epoch": round(self._started_wall, 3),
                "other_collect_seconds": round(self._other_collect_seconds, 6),
                "collected_total": sum(t.collected_nodes for t in self._files.values()),
                "started_total": started,
                "executed_total": executed,
                # Per-run timings vary; the median total across files is the
                # honest "typical file" scale, exposed for fallback weighting.
                "median_file_total_seconds": _round_or_none(
                    _median([timing.as_dict()["total_seconds"] for timing in self._files.values()])
                ),
            },
            "source": _source_identity(),
            "environment": _environment_fingerprint(),
            "invocation": {"pytest_args": self._pytest_args},
            "files": {path: timing.as_dict() for path, timing in sorted(self._files.items())},
            "failed_nodes": sorted(self._failed_nodes, key=lambda entry: entry["nodeid"]),
        }

    def _flush(self, *, complete: bool) -> None:
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._output_path.write_text(
                json.dumps(self.build_payload(complete=complete), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            if self.write_error is None:
                self.write_error = f"{type(exc).__name__}: {exc}"


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _source_identity() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parent.parent
    try:
        head = subprocess.run(
            ("git", "-C", str(repo_root), "rev-parse", "HEAD"),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ("git", "-C", str(repo_root), "status", "--porcelain"),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.splitlines()
        return {
            "git_head_sha": head,
            "git_dirty": bool(status),
            "git_changed_paths": len(status),
        }
    except (OSError, subprocess.SubprocessError):
        return {"git_head_sha": None, "git_dirty": None, "git_changed_paths": None}


def _environment_fingerprint() -> dict[str, Any]:
    # Environment FAMILY, not an environment dump: enough to keep a planner from
    # averaging Linux shard timings with macOS ones, and nothing beyond that.
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sys_platform": sys.platform,
        "machine": platform.machine(),
        "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
        "runner_os": os.environ.get("RUNNER_OS") or None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pytest unchanged and record a per-file timing manifest beside it."
    )
    parser.add_argument("--output", required=True, help="Path of the timing manifest to write.")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    plugin = _TimingPlugin(output_path=Path(args.output), pytest_args=pytest_args)
    # Deliberately NO environment sanitization and NO ``-o addopts=``: the CI
    # shard step this replaces ran ``python -m pytest <args>`` verbatim, and the
    # measurement must not change what runs -- same addopts, same plugin
    # autoload, same argv, same order.
    exitstatus = int(pytest.main(pytest_args, plugins=[plugin]))
    if plugin.write_error is not None:
        print(f"!! timing manifest could not be written: {plugin.write_error}", file=sys.stderr)
        return 1
    return exitstatus


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
