"""Paired baseline/candidate verification: mechanical attribution of a red.

A failure may be called PRE-EXISTING only when the candidate and a named baseline,
run with the byte-identical command, targets, interpreter, and execution topology,
produce the SAME failing-nodeid set. Anything else is unattributed.

Composition, not a new runner: each side runs this repo's own shard gate
(`python -m ops.pytest_shards`) inside its own temporary git worktree, so both
sides inherit canonical collection, stable sharding, exit-code authority, and
skip accounting. The user's current worktree is never touched or checked out.

Verdicts (machine artifact: vool.baseline-comparison.v1):

    PRE_EXISTING_CONFIRMED      same non-empty failing set on both sides
    NEW_REGRESSION              baseline green, candidate red
    BASELINE_RED_CANDIDATE_GREEN  candidate fixed it; nothing to attribute
    BOTH_GREEN                  nothing to attribute
    FAILURE_SET_CHANGED         both red, different failing sets (diff reported)
    COVERAGE_REGRESSION         a node red on baseline is SKIPPED on candidate --
                                proof loss, never pre-existing
    ENVIRONMENT_MISMATCH        interpreter/pytest identity differs between sides
    TARGET_MISMATCH             collected nodeids differ between sides
    BASELINE_UNRUNNABLE / CANDIDATE_UNRUNNABLE  side did not complete a comparable run

Exit codes are part of the contract:
    0  PRE_EXISTING_CONFIRMED only
    1  red present but NOT attributable as pre-existing
    2  attribution impossible (mismatch / unrunnable)
    3  infrastructure error (bad ref, git failure)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

PRE_EXISTING_CONFIRMED = "PRE_EXISTING_CONFIRMED"
NEW_REGRESSION = "NEW_REGRESSION"
BASELINE_RED_CANDIDATE_GREEN = "BASELINE_RED_CANDIDATE_GREEN"
BOTH_GREEN = "BOTH_GREEN"
FAILURE_SET_CHANGED = "FAILURE_SET_CHANGED"
COVERAGE_REGRESSION = "COVERAGE_REGRESSION"
ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
TARGET_MISMATCH = "TARGET_MISMATCH"

# Exit code classes: anything not PRE_EXISTING_CONFIRMED must not be able to read as green.
_EXIT_PRE_EXISTING = 0
_EXIT_NOT_PRE_EXISTING = {
    NEW_REGRESSION,
    BASELINE_RED_CANDIDATE_GREEN,
    BOTH_GREEN,
    COVERAGE_REGRESSION,
}
_EXIT_UNATTRIBUTABLE = {ENVIRONMENT_MISMATCH, TARGET_MISMATCH}


@dataclass(frozen=True)
class SideResult:
    name: str
    sha: str
    returncode: int | None
    artifact_dir: Path | None
    collected: frozenset[str]
    failed: frozenset[str]
    skipped: frozenset[str]
    xfailed: frozenset[str]
    xpassed: frozenset[str]
    pytest_version: str
    error: str


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _resolve_commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _pytest_version(python: str, cwd: Path) -> str:
    completed = subprocess.run(
        (python, "-c", "import pytest;print(pytest.__version__)"),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(cwd),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"could not determine pytest version in {cwd}: {completed.stderr}")
    return completed.stdout.strip()


def _run_side(
    *,
    repo: Path,
    ref: str,
    name: str,
    target: str,
    worktree_root: Path,
    artifact_root: Path,
    python: str,
    workers: int,
    timeout_seconds: float,
) -> SideResult:
    """Check out `ref` into an isolated detached worktree and run the shard gate there."""

    sha = _resolve_commit(repo, ref)
    checkout = worktree_root / name
    _git(repo, "worktree", "add", "--detach", str(checkout), sha)
    try:
        artifact_root.mkdir(parents=True, exist_ok=True)
        pytest_version = _pytest_version(python, checkout)
        side_artifacts = Path(mkdtemp(prefix=f"{name}-", dir=artifact_root))
        command = (
            python,
            "-m",
            "ops.pytest_shards",
            target,
            "--workers",
            str(workers),
            "--label",
            f"baseline-check-{name}",
            "--timeout-seconds",
            str(timeout_seconds),
            "--artifact-root",
            str(side_artifacts),
        )
        # The shard gate sanitizes PYTEST_ADDOPTS/plugins itself; run from the checkout root
        # so `-m ops` resolves to THIS tree's verification machinery.
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(60.0, timeout_seconds * 2),
            cwd=str(checkout),
            check=False,
        )
        # Fresh per-side directory: a reused artifact root must never let a stale
        # run's manifests be mistaken for this run's evidence.
        runs = sorted(side_artifacts.glob("vool-pytest-shards-*"))
        if not runs:
            return SideResult(name, sha, completed.returncode, None, frozenset(), frozenset(),
                              frozenset(), frozenset(), frozenset(), pytest_version,
                              f"no shard-run artifacts under {side_artifacts}: "
                              f"{completed.stdout[-400:]} {completed.stderr[-400:]}")
        logs_dir = runs[-1] / "logs"
        collected: set[str] = set()
        failed: set[str] = set()
        skipped: set[str] = set()
        xfailed: set[str] = set()
        xpassed: set[str] = set()
        manifests = sorted(logs_dir.glob("shard-*-execution.json"))
        if not manifests:
            return SideResult(name, sha, completed.returncode, logs_dir.parent, frozenset(),
                              frozenset(), frozenset(), frozenset(), frozenset(), pytest_version,
                              "no shard execution manifests written")
        for manifest_path in manifests:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            # Manifests predating failure accounting cannot support identity comparison;
            # fail closed rather than compare against an empty set.
            if "failed" not in payload:
                raise RuntimeError(
                    f"{manifest_path} lacks failure records -- regenerate with current "
                    "ops/pytest_execution.py; comparison would be unsound"
                )
            collected.update(str(item) for item in payload["collected_nodeids"])
            failed.update(str(entry["nodeid"]) for entry in payload["failed"])
            skipped.update(str(entry["nodeid"]) for entry in payload["skipped"])
            xfailed.update(str(item) for item in payload["xfailed"])
            xpassed.update(str(item) for item in payload["xpassed"])
        return SideResult(name, sha, completed.returncode, logs_dir.parent,
                          frozenset(collected), frozenset(failed), frozenset(skipped),
                          frozenset(xfailed), frozenset(xpassed), pytest_version, "")
    finally:
        _git(repo, "worktree", "remove", "--force", str(checkout))


def _decide(candidate: SideResult, baseline: SideResult) -> tuple[str, dict[str, Any]]:
    details: dict[str, Any] = {}
    for side in (baseline, candidate):
        if side.error:
            return (f"{side.name.upper()}_UNRUNNABLE", {"reason": side.error})
        if side.returncode not in (0, 1):
            return (
                f"{side.name.upper()}_UNRUNNABLE",
                {"reason": f"runner exited {side.returncode}"},
            )
    if baseline.pytest_version != candidate.pytest_version:
        return (
            ENVIRONMENT_MISMATCH,
            {
                "baseline_pytest": baseline.pytest_version,
                "candidate_pytest": candidate.pytest_version,
            },
        )
    if baseline.collected != candidate.collected:
        return (
            TARGET_MISMATCH,
            {
                "only_baseline": sorted(baseline.collected - candidate.collected),
                "only_candidate": sorted(candidate.collected - baseline.collected),
            },
        )
    base_fail, cand_fail = baseline.failed, candidate.failed
    details["failing_only_in_baseline"] = sorted(base_fail - cand_fail)
    details["failing_only_in_candidate"] = sorted(cand_fail - base_fail)
    # A node that FAILED on baseline but merely SKIPPED on candidate lost its proof.
    # Silence is not agreement: this can never confirm pre-existence.
    proof_lost = base_fail & (candidate.skipped | candidate.xfailed)
    if proof_lost:
        return (
            COVERAGE_REGRESSION,
            {**details, "proof_lost_nodes": sorted(proof_lost)},
        )
    if base_fail and cand_fail:
        if base_fail == cand_fail:
            return (PRE_EXISTING_CONFIRMED, details)
        return (FAILURE_SET_CHANGED, details)
    if not base_fail and not cand_fail:
        return (BOTH_GREEN, details)
    if not base_fail:
        return (NEW_REGRESSION, details)
    return (BASELINE_RED_CANDIDATE_GREEN, details)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attribute a red: paired candidate/baseline run through the real shard gate."
    )
    parser.add_argument("--repo", type=Path, required=True, help="Git repository to compare.")
    parser.add_argument("--candidate", required=True, help="Candidate ref (SHA, branch, HEAD...).")
    parser.add_argument("--baseline", required=True, help="Baseline/parent ref.")
    parser.add_argument("--target", required=True, help="Test path/nodeid passed to the shard gate.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--label", default="paired")
    parser.add_argument(
        "--python", default=sys.executable, help="Interpreter used for BOTH sides."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    artifact_root = args.artifact_root.resolve() / args.label
    artifact_root.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema": "vool.baseline-comparison.v1",
        "repo": str(repo),
        "candidate_ref": args.candidate,
        "baseline_ref": args.baseline,
        "target": args.target,
        "exact_command_template": [
            args.python,
            "-m",
            "ops.pytest_shards",
            args.target,
            "--workers",
            str(args.workers),
            "...gate defaults...",
        ],
        "workers": args.workers,
        "dirty_state_of_calling_worktree": _git(repo, "status", "--porcelain").splitlines(),
    }
    verdict = "INFRASTRUCTURE_ERROR"
    details: dict[str, Any] = {}
    try:
        with TemporaryDirectory(prefix="vool-baseline-check-") as tmp:
            worktree_root = Path(tmp)
            candidate = _run_side(
                repo=repo,
                ref=args.candidate,
                name="candidate",
                target=args.target,
                worktree_root=worktree_root,
                artifact_root=artifact_root / "candidate",
                python=args.python,
                workers=args.workers,
                timeout_seconds=args.timeout_seconds,
            )
            baseline = _run_side(
                repo=repo,
                ref=args.baseline,
                name="baseline",
                target=args.target,
                worktree_root=worktree_root,
                artifact_root=artifact_root / "baseline",
                python=args.python,
                workers=args.workers,
                timeout_seconds=args.timeout_seconds,
            )
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        record["infrastructure_error"] = str(exc)
        _write_record(record, artifact_root, verdict)
        print(f"!! baseline-check infrastructure error: {exc}", flush=True)
        return 3

    def as_dict(side: SideResult) -> dict[str, Any]:
        return {
            "sha": side.sha,
            "returncode": side.returncode,
            "artifacts": str(side.artifact_dir) if side.artifact_dir else None,
            "pytest_version": side.pytest_version,
            "collected_nodeids": sorted(side.collected),
            "failed_nodeids": sorted(side.failed),
            "skipped_nodeids": sorted(side.skipped),
            "xfailed_nodeids": sorted(side.xfailed),
            "xpassed_nodeids": sorted(side.xpassed),
            "error": side.error,
        }

    verdict, details = _decide(candidate, baseline)
    record["candidate"] = as_dict(candidate)
    record["baseline"] = as_dict(baseline)
    record["verdict"] = verdict
    record["details"] = details
    record["environment_contract"] = {
        "python": subprocess.run((args.python, "--version"), capture_output=True, text=True).stdout.strip(),
        "pytest": candidate.pytest_version,
        "pytest_addopts": "stripped by shard gate on both sides",
        "topology": f"{args.workers} worker(s), stable-hash partitioning, identical target",
    }
    _write_record(record, artifact_root, verdict)

    if verdict == PRE_EXISTING_CONFIRMED:
        print(f"baseline-check: {verdict} (identical failing set on both sides)")
        return _EXIT_PRE_EXISTING
    if verdict in _EXIT_NOT_PRE_EXISTING:
        print(f"baseline-check: {verdict} -- NOT attributable as pre-existing")
        return 1
    if verdict in _EXIT_UNATTRIBUTABLE:
        print(f"baseline-check: {verdict} -- fail closed to unattributed")
        return 2
    print(f"baseline-check: {verdict}")
    return 2


def _write_record(record: dict[str, Any], artifact_root: Path, verdict: str) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    path = artifact_root / "vool.baseline-comparison.v1.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"baseline-check artifact: {path} verdict={verdict}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
