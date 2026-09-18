#!/usr/bin/env python3
"""Connection-acquisition latency, measured the same way at base and at tip.

The recorded storage baseline is a per-`get_connection()` figure taken on a 99-table
store. A single timing cannot settle whether this branch regressed it: the number moves
with machine load, page cache, and whatever else is running. So this takes N runs of M
acquisitions each and reports the median AND the range, and it builds its own store in
an isolated home so the two trees are never measuring each other's leftovers.

Everything that could differ between the two measurements is pinned by the caller
running the SAME interpreter against both worktrees with the same flags:

    <venv>/bin/python -m tools.perf_connection_probe --runs 5 --iterations 400

Run it at the base worktree and at the tip with identical arguments and compare medians
and ranges, not single numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import tempfile
import time


def _measure(iterations: int) -> tuple[list[float], int]:
    """One run: build a store in a fresh isolated home, then time acquisitions."""
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    conn = get_connection()
    tables = int(
        conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    )

    # One warm acquisition first: the very first call pays for pool construction, which
    # is a startup cost and not the per-acquisition cost under measurement.
    get_connection()
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        get_connection()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples, tables


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--iterations", type=int, default=400)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    run_medians: list[float] = []
    run_p95s: list[float] = []
    table_counts: list[int] = []
    for _ in range(args.runs):
        # A fresh isolated home per run: no shared state between runs, and none with the
        # operator's real home.
        with tempfile.TemporaryDirectory(prefix="perf-home-") as home:
            for key in ("HOME", "VOOL_HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
                os.environ[key] = home
            for mod in [m for m in list(sys.modules) if m.startswith(("storage.", "core."))]:
                sys.modules.pop(mod, None)
            samples, tables = _measure(args.iterations)
        run_medians.append(statistics.median(samples))
        run_p95s.append(sorted(samples)[int(len(samples) * 0.95) - 1])
        table_counts.append(tables)

    payload = {
        "runs": args.runs,
        "iterations_per_run": args.iterations,
        "interpreter": sys.executable,
        "python": sys.version.split()[0],
        "cwd": str(pathlib.Path.cwd()),
        "tables_per_run": table_counts,
        "median_ms_per_run": [round(v, 4) for v in run_medians],
        "median_of_medians_ms": round(statistics.median(run_medians), 4),
        "median_range_ms": [round(min(run_medians), 4), round(max(run_medians), 4)],
        "p95_ms_per_run": [round(v, 4) for v in run_p95s],
        "p95_median_ms": round(statistics.median(run_p95s), 4),
    }
    text = json.dumps(payload, indent=2) + "\n"
    if args.json:
        pathlib.Path(args.json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
