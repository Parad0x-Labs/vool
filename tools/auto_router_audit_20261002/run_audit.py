#!/usr/bin/env python3
"""Run the auto-router audit at the pinned public base.

Usage (from the repo root, through the SSD launcher):

    python3 tools/auto_router_audit_20261002/run_audit.py \
        --profile-root <dedicated scratch dir>/profiles/auto-router-audit \
        --artifacts  <dedicated scratch dir>/artifacts/auto-router-audit \
        [--splits dev,heldout,control] [--ids a,b] [--keep-profiles]

Produces in --artifacts: BASELINE.json, SCORECARD.md, traces/<id>.json,
corpus_copy.json. Networking is fully guarded: no live call can leave.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOOLS_PKG = Path(__file__).resolve().parent
if str(TOOLS_PKG) not in sys.path:
    sys.path.insert(0, str(TOOLS_PKG))

from audit_lib import guard_transport, report, runner


def repo_head() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-root", required=True, type=Path,
                        help="job profile root (disposable VOOL_HOME lives under it)")
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--splits", default="dev,heldout,control")
    parser.add_argument("--ids", default="", help="comma-separated scenario ids to run")
    parser.add_argument("--keep-profiles", action="store_true",
                        help="keep disposable profiles (default: delete after traces are written)")
    args = parser.parse_args()

    profile_root = runner.require_disposable_profile_root(args.profile_root)
    artifacts = args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    profile_root.mkdir(parents=True, exist_ok=True)

    corpus = json.loads((TOOLS_PKG / "corpus.json").read_text(encoding="utf-8"))
    (artifacts / "corpus_copy.json").write_text(json.dumps(corpus, indent=2), encoding="utf-8")

    wanted_splits = {s.strip() for s in args.splits.split(",") if s.strip()}
    wanted_ids = {s.strip() for s in args.ids.split(",") if s.strip()}
    scenarios = [
        s for s in corpus["scenarios"]
        if (not wanted_splits or s["split"] in wanted_splits)
        and (not wanted_ids or s["id"] in wanted_ids)
    ]
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    command = " ".join(sys.argv)
    guard_self = guard_transport.self_test()
    print(f"[audit] base={corpus['base_commit']} head={repo_head()} corpus={report.corpus_hash(corpus)[:12]}")
    print(f"[audit] guard self-test: {guard_self}")
    if not all(guard_self.values()):
        print("[audit] GUARD SELF-TEST FAILED — refusing to run", file=sys.stderr)
        return 3

    traces_dir = artifacts / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    def _save(trace: dict) -> None:
        path = traces_dir / f"{trace.get('scenario_id', 'unknown')}.json"
        path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
        row = trace.get("outcome_class")
        print(f"[audit] {trace.get('scenario_id')}: outcome={row} attempts={len(trace.get('transport_calls', []))}")

    traces = runner.run_scenarios(
        scenarios, profile_root=profile_root, repo_root=REPO_ROOT, on_scenario=_save
    )

    baseline = report.build_baseline(
        traces, corpus, repo_head=repo_head(), guard_self_test=guard_self, command=command
    )
    (artifacts / "BASELINE.json").write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
    (artifacts / "SCORECARD.md").write_text(report.build_scorecard(baseline), encoding="utf-8")

    summary = baseline["summary"]
    print(
        f"[audit] done: pass={summary['pass']} fail={summary['fail']} "
        f"error={summary['harness_error']} controls={summary['controls']}"
    )

    if not args.keep_profiles:
        import shutil

        for child in sorted(profile_root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)

    failed = summary["fail"] + summary["harness_error"]
    bad_controls = summary["controls"] - {"control_observed"}
    return 1 if (failed or bad_controls) else 0


if __name__ == "__main__":
    raise SystemExit(main())
