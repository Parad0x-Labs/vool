"""Served-reality bench runner.

Usage (from the repo root, pinned python):

    python -m ops.served_reality.runner \
        [--sha <commit>] [--repo <path>] [--run-dir <dir>] \
        [--cases id1,id2 | --list] [--mutations] [--live provider,...] \
        [--case-timeout S] [--keep-run-dir]

Exit codes (truthful, never reinterpreted):

    0  every required case PASSed; every requested mutation was DETECTED
    1  one or more required cases FAILed or were skipped unexpectedly
       (an unexpectedly skipped required case IS a failure)
    2  harness error — staging/boot failure; the run could not prove anything
    3  usage error

The bench is ALLOWED to report product failures; a truthful red run is a
successful bench. Assertions are never weakened to green a run.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from ops.served_reality.bench import BenchRig
from ops.served_reality.classify import BenchError
from ops.served_reality.schema import (
    VERDICT_FAIL,
    VERDICT_PASS,
    VERDICT_SKIP_EXPECTED,
    CaseResult,
    RunManifest,
    new_run_id,
    write_results,
)

REPO_DEFAULT = Path(__file__).resolve().parents[2]


def _run_with_timeout(fn, timeout_s: float):
    """Run fn() with a wall-clock watchdog. Returns (value|None, timeout|None)."""
    box: dict = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    if done.wait(timeout=timeout_s):
        if "error" in box:
            raise box["error"]
        return box.get("value"), None
    return None, f"case watchdog timeout after {timeout_s:.0f}s"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="served-reality-bench")
    parser.add_argument("--sha", default="HEAD", help="commit to stage and prove (default HEAD)")
    parser.add_argument("--repo", default=str(REPO_DEFAULT), help="git repo to stage from")
    parser.add_argument("--run-dir", default="", help="run directory (default /tmp/served-reality-bench/<run_id>)")
    parser.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    parser.add_argument("--list", action="store_true", help="list corpus case ids and exit")
    parser.add_argument("--mutations", action="store_true", help="also run bench-sensitivity mutation controls")
    parser.add_argument("--live", default="", help="explicitly enable live providers (comma list, e.g. openrouter)")
    parser.add_argument("--case-timeout", type=float, default=300.0, help="per-case wall-clock bound (s)")
    parser.add_argument("--keep-run-dir", action="store_true", help="do not prune prior run dirs")
    args = parser.parse_args(argv)

    from ops.served_reality.corpus import CORPUS

    if args.list:
        for case in CORPUS:
            marker = "required" if case.required else "optional"
            print(f"{case.case_id:36s} {marker:8s} {case.title}")
        return 0

    from ops.served_reality.appstage import resolve_sha

    repo = Path(args.repo).expanduser().resolve()
    try:
        short, full, branch = resolve_sha(repo, args.sha)
    except BenchError as exc:
        print(f"HARNESS ERROR: {exc}: {exc.detail}", file=sys.stderr)
        return 2

    run_id = new_run_id()
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else Path("/tmp/served-reality-bench") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    live_providers = [p.strip() for p in args.live.split(",") if p.strip()]
    selected = {c.case_id: c for c in CORPUS}
    if args.cases:
        wanted = [c.strip() for c in args.cases.split(",") if c.strip()]
        unknown = [c for c in wanted if c not in selected]
        if unknown:
            print(f"usage error: unknown case ids: {unknown}", file=sys.stderr)
            return 3
        selected = {cid: selected[cid] for cid in wanted}

    extra_env: dict[str, str] = {}
    for provider in live_providers:
        if provider == "openrouter":
            import os

            key = os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                print("usage error: --live openrouter requires OPENROUTER_API_KEY in env", file=sys.stderr)
                return 3
            extra_env["OPENROUTER_API_KEY"] = key

    manifest = RunManifest(
        run_id=run_id,
        created_at=time.time(),
        product_sha=full,
        product_branch=branch,
        provider_mode=("live:" + ",".join(live_providers)) if live_providers else "deterministic",
        live_providers=live_providers,
        python_version=sys.version.split()[0],
    )

    rig = BenchRig(repo=repo, sha=full, run_dir=run_dir, extra_daemon_env=extra_env)
    results: list[CaseResult] = []

    def _persist() -> None:
        manifest.case_count = len(results)
        manifest.failures = sum(1 for r in results if r.verdict == VERDICT_FAIL)
        manifest.unexpected_skips = sum(1 for r in results if r.is_failure and r.verdict != VERDICT_FAIL)
        write_results(run_dir, manifest, results)

    try:
        try:
            rig.setup()
        except BenchError as exc:
            manifest.outcome = "harness_error"
            manifest.home_path = str(rig.home)
            print(f"HARNESS ERROR during setup: {exc}", file=sys.stderr)
            if exc.detail:
                print(f"  detail: {exc.detail[:2000]}", file=sys.stderr)
            _persist()
            return 2
        staged = rig.staged_for_manifest
        manifest.staged_from_clean_tree = staged.get("mutated") is not True
        manifest.staging_archive_sha256 = str(staged.get("archive_sha256") or "")
        manifest.home_path = str(rig.home)
        manifest.daemon_port = rig.daemon.port if rig.daemon else None
        manifest.daemon_pid = rig.daemon.pid if rig.daemon else None
        _persist()

        for case in selected.values():
            # Only an OPTIONAL case may ever skip (e.g. an optional live
            # variant without its provider enabled). A required case that
            # cannot run is a failure, never a skip.
            skip_reason = None
            if not case.required and case.case_id.startswith("live-") and not live_providers:
                skip_reason = "live_provider_not_enabled"
            if skip_reason is not None:
                results.append(
                    CaseResult(
                        case_id=case.case_id,
                        title=case.title,
                        required=case.required,
                        verdict=VERDICT_SKIP_EXPECTED,
                        reason=skip_reason,
                        started_at=time.time(),
                        provider_mode=manifest.provider_mode,
                    )
                )
                _persist()
                continue

            def _execute(case=case) -> CaseResult:
                return rig.run_case(case, provider_mode=manifest.provider_mode, product_sha=short)

            try:
                result, timeout = _run_with_timeout(_execute, args.case_timeout)
            except BenchError as exc:
                result = CaseResult(
                    case_id=case.case_id,
                    title=case.title,
                    required=case.required,
                    verdict=VERDICT_FAIL,
                    failure_class="HARNESS",
                    reason=f"bench error: {exc}",
                    detail=str(exc.detail or "")[:2000],
                    started_at=time.time(),
                    provider_mode=manifest.provider_mode,
                )
                results.append(result)
                _persist()
                continue
            if timeout is not None:
                result = CaseResult(
                    case_id=case.case_id,
                    title=case.title,
                    required=case.required,
                    verdict=VERDICT_FAIL,
                    failure_class="HARNESS",
                    reason=timeout,
                    detail=rig.daemon_stderr_tail(2000),
                    started_at=time.time(),
                    provider_mode=manifest.provider_mode,
                )
            results.append(result)
            _persist()
            marker = "PASS" if result.verdict == VERDICT_PASS else ("FAIL" if result.verdict == VERDICT_FAIL else result.verdict)
            suffix = f" — {result.failure_class}: {result.reason[:120]}" if result.verdict == VERDICT_FAIL else ""
            print(f"[{marker}] {result.case_id} ({result.duration_s:.1f}s){suffix}", flush=True)

        if args.mutations:
            from ops.served_reality.mutations import run_mutation_controls

            for mutation_result in run_mutation_controls(rig, timeout_s=args.case_timeout):
                results.append(mutation_result)
                _persist()
                marker = "DETECTED" if mutation_result.verdict == VERDICT_PASS else "MISSED"
                suffix = f" — {mutation_result.reason[:120]}" if mutation_result.verdict == VERDICT_FAIL else ""
                print(f"[{marker}] {mutation_result.case_id}{suffix}", flush=True)
    finally:
        rig.teardown()
        if rig.staged is not None:
            unchanged, drifted = rig.staged.verify_unchanged()
            manifest.app_census_unchanged = unchanged
            if not unchanged:
                results.append(
                    CaseResult(
                        case_id="staging-immutability",
                        title="staged app unchanged after the run",
                        required=True,
                        verdict=VERDICT_FAIL,
                        failure_class="VOOL",
                        reason=f"staged app drifted: {drifted[:10]}",
                        started_at=time.time(),
                        provider_mode=manifest.provider_mode,
                    )
                )
        _persist()

    failing = [r for r in results if r.is_failure]
    harness_failures = [r for r in failing if r.failure_class == "HARNESS"]
    manifest.outcome = "fail" if failing else "pass"
    if harness_failures and not [r for r in failing if r.failure_class != "HARNESS"]:
        manifest.outcome = "harness_error"
    _persist()

    print()
    print(f"run dir: {run_dir}")
    print(f"results: {run_dir / 'results.jsonl'}")
    print(f"manifest: {run_dir / 'manifest.json'}")
    print(f"outcome: {manifest.outcome} — {len(results)} cases, {manifest.failures} fail, {manifest.unexpected_skips} unexpected skips")
    if failing:
        print("failing cases:")
        for r in failing:
            print(f"  - {r.case_id} [{r.failure_class}] {r.reason[:160]}")
    return 0 if not failing else (2 if manifest.outcome == "harness_error" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
