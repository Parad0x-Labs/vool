"""Trusted baseline controller: the subject under test must not grade itself.

ops/baseline_check.py ran each side through ITS OWN verification stack, so an
old revision (no failure records, no skip accounting) was incomparable, and a
tampered revision could in principle grade itself. This controller lives
outside both subjects:

  trusted controller (this module + a copied observer plugin)
      |- checkout baseline source -> run target with CONTROLLER instrumentation
      '- checkout candidate source -> run target with CONTROLLER instrumentation

The controller owns: manifest schema, skip/fail accounting, verdict semantics,
artifact schema, environment fingerprinting. The subject owns only its source,
tests, and declared dependencies. The observer plugin is copied to a neutral
runtime directory before injection so no controller package name can shadow or
be shadowed by subject imports.

Proof types are distinct and never collapsed:

    NATIVE_SUITE              each side's own tests at the same target path;
                              comparable only when collected nodeid sets match
    EXTERNAL_REGRESSION_PROBE one immutable probe file (sha256-recorded),
                              copied into both revisions; answers "did THIS
                              exact behavior already fail on the parent SHA"

Verdicts derive ONLY from controller-written manifests. A candidate that
rewrites its own verifier, fakes manifests, or relabels failures as skips has
no channel into the comparison.

Exit contract (identical to baseline_check): 0 = PRE_EXISTING_CONFIRMED only;
1 = red present but not attributable; 2 = cannot attribute; 3 = infra error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

from ops.baseline_check import (
    BOTH_GREEN,
    COVERAGE_REGRESSION,
    ENVIRONMENT_MISMATCH,
    FAILURE_SET_CHANGED,
    PRE_EXISTING_CONFIRMED,
    TARGET_MISMATCH,
    SideResult,
    _git,
)

NATIVE_SUITE = "NATIVE_SUITE"
EXTERNAL_REGRESSION_PROBE = "EXTERNAL_REGRESSION_PROBE"
NEW_REGRESSION = "NEW_REGRESSION"
BASELINE_RED_CANDIDATE_GREEN = "BASELINE_RED_CANDIDATE_GREEN"
IMPORT_RESOLUTION_MISMATCH = "IMPORT_RESOLUTION_MISMATCH"
TEST_DEFINITION_CHANGED = "TEST_DEFINITION_CHANGED"
CONTROLLER_IDENTITY_UNTRUSTED = "CONTROLLER_IDENTITY_UNTRUSTED"

_OBSERVER_SOURCE = Path(__file__).resolve().parent / "pytest_execution.py"
_DEPENDENCY_DECLARATIONS = ("requirements.txt", "requirements-lock.txt", "pyproject.toml")

# Bounded resolved-environment query: a deterministic digest of the exact
# distribution name==version set visible to the subject interpreter. Not a
# machine hash; limitations are stated in the artifact (transitive metadata
# skew within one version, editable path tricks, are NOT individually visible).
_INSTALLED_DIGEST_PROGRAM = (
    "import hashlib, importlib.metadata as md\n"
    "items = sorted(f'{d.metadata[\"Name\"].lower()}=={d.version}' "
    "for d in md.distributions() if d.metadata.get('Name'))\n"
    "print(hashlib.sha256(chr(10).join(items).encode()).hexdigest(), len(items))\n"
)


@dataclass(frozen=True)
class Fingerprint:
    python_version: str
    pytest_version: str
    plugins: tuple[str, ...]
    platform_id: str
    dependency_declaration_sha: str
    import_resolution: str
    installed_packages_sha: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "python_version": self.python_version,
            "pytest_version": self.pytest_version,
            "plugins": list(self.plugins),
            "platform": self.platform_id,
            "dependency_declaration_sha": self.dependency_declaration_sha,
            "import_resolution": self.import_resolution or None,
            "installed_packages_sha": self.installed_packages_sha or None,
        }


@dataclass(frozen=True)
class ControlledSide:
    result: SideResult
    fingerprint: Fingerprint
    probe_sha256: str
    resolution_ok: bool = True
    test_file_digests: dict[str, str] | None = None


def _controller_identity() -> dict[str, Any]:
    """Identity of the EXACT implementation producing the verdict.

    A git SHA alone does not identify uncommitted controller bytes, so the
    primary identity is the byte-hash of the full controller closure
    (this module + the injected observer). Uncommitted modification of any
    closure file marks the identity UNTRUSTED: such a run may never issue
    PRE_EXISTING_CONFIRMED.
    """
    controller_path = Path(__file__).resolve()
    closure = (controller_path, _OBSERVER_SOURCE.resolve())
    closure_sha = hashlib.sha256(b"".join(p.read_bytes() for p in closure)).hexdigest()
    root = controller_path.parent.parent
    dirty = _git(
        root, "status", "--porcelain", "--", *(str(p.relative_to(root)) for p in closure)
    ).splitlines()
    return {
        "controller_source": str(controller_path),
        "observer_source": str(_OBSERVER_SOURCE),
        "observer_sha256": hashlib.sha256(_OBSERVER_SOURCE.read_bytes()).hexdigest(),
        "controller_closure_sha256": closure_sha,
        # Best-effort provenance only; NOT an identity claim for dirty trees.
        "controller_git_sha": _git(root, "rev-parse", "HEAD"),
        "controller_worktree_clean": not dirty,
        "identity_trusted": not bool(dirty),
    }


def _resolve_commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _fingerprint(python: str, checkout: Path, resolution_module: str | None) -> Fingerprint:
    version_out = subprocess.run(
        (python, "-m", "pytest", "--version"),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(checkout),
        check=False,
    )
    if version_out.returncode != 0:
        raise RuntimeError(f"pytest --version failed in {checkout}: {version_out.stderr}")
    text = version_out.stdout + version_out.stderr
    pytest_version = ""
    plugins: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("pytest ") and not pytest_version:
            pytest_version = line.split()[1]
        if line.startswith("plugins:"):
            plugins = sorted(item.strip() for item in line[len("plugins:"):].split(","))
    declarations = sorted(
        (name, (checkout / name).read_bytes())
        for name in _DEPENDENCY_DECLARATIONS
        if (checkout / name).exists()
    )
    declaration_sha = hashlib.sha256(
        b"".join(name.encode() + b"\0" + blob for name, blob in declarations)
    ).hexdigest()
    installed = subprocess.run(
        (python, "-c", _INSTALLED_DIGEST_PROGRAM),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    installed_sha = installed.stdout.split()[0] if installed.returncode == 0 else ""
    resolution = ""
    if resolution_module:
        completed = subprocess.run(
            (python, "-c", f"import {resolution_module} as m; print(m.__file__)"),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(checkout),
            check=False,
        )
        resolution = (
            completed.stdout.strip()
            if completed.returncode == 0
            else f"unresolvable:{completed.stderr.strip()[:200]}"
        )
    return Fingerprint(
        python_version=subprocess.run(
            (python, "--version"), capture_output=True, text=True, check=False
        ).stdout.strip(),
        pytest_version=pytest_version,
        plugins=tuple(plugins),
        platform_id=f"{platform.system()}/{platform.machine()}",
        dependency_declaration_sha=declaration_sha,
        import_resolution=resolution,
        installed_packages_sha=installed_sha,
    )


def _fingerprints_compatible(base: Fingerprint, cand: Fingerprint) -> tuple[bool, dict[str, Any]]:
    diffs: dict[str, Any] = {}
    # Interpreter, plugin set, and platform must be identical. Declared dependency
    # sets may legitimately differ between revisions; that is recorded as drift and
    # fails closed -- drift may have CAUSED the outcome difference.
    for field in ("python_version", "pytest_version", "platform_id"):
        if getattr(base, field) != getattr(cand, field):
            diffs[field] = {"baseline": getattr(base, field), "candidate": getattr(cand, field)}
    if base.plugins != cand.plugins:
        diffs["plugins"] = {
            "only_baseline": sorted(set(base.plugins) - set(cand.plugins)),
            "only_candidate": sorted(set(cand.plugins) - set(base.plugins)),
        }
    if base.dependency_declaration_sha != cand.dependency_declaration_sha:
        diffs["dependency_declaration"] = (
            "DIFFERS -- outcome differences may be environmental, not behavioral"
        )
    if (
        base.installed_packages_sha
        and cand.installed_packages_sha
        and base.installed_packages_sha != cand.installed_packages_sha
    ):
        diffs["installed_packages"] = (
            "RESOLVED ENVIRONMENT DIFFERS -- same declarations can still resolve "
            "to different installed distributions"
        )
    return (not diffs, diffs)


def _import_resolution_ok(fp: Fingerprint, checkout: Path) -> bool:
    """A subject whose imports resolve OUTSIDE its checkout (editable install of
    another tree, stale egg-link) would produce outcomes not attributable to the
    checked-out source. Fail closed."""
    if not fp.import_resolution:
        return True
    if fp.import_resolution.startswith("unresolvable:"):
        return False
    return Path(fp.import_resolution).resolve().is_relative_to(checkout.resolve())


def _target_file_digests(checkout: Path, target: str) -> dict[str, str]:
    """Byte digests of the native test files the target covers. Native failure
    identity is only comparable when the test DEFINITIONS are identical bytes;
    a same-nodeid different-body test is a different test."""
    root = checkout / target
    if root.is_file():
        files = [root]
    elif root.is_dir():
        files = sorted(p for p in root.rglob("*.py") if p.is_file())
    else:
        return {}
    return {
        str(p.relative_to(checkout)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }


def _run_side(
    *,
    repo: Path,
    ref: str,
    name: str,
    target: str,
    probe_path: Path | None,
    worktree_root: Path,
    artifact_root: Path,
    python: str,
    timeout_seconds: float,
    resolution_module: str | None,
) -> ControlledSide:
    sha = _resolve_commit(repo, ref)
    checkout = worktree_root / name
    _git(repo, "worktree", "add", "--detach", str(checkout), sha)
    try:
        fingerprint = _fingerprint(python, checkout, resolution_module)
        run_target = target
        probe_sha = ""
        test_digests: dict[str, str] | None = None
        if probe_path is not None:
            probe_dir = checkout / ".trusted_probe"
            probe_dir.mkdir()
            shutil.copy(probe_path, probe_dir / "test_external_regression_probe.py")
            run_target = ".trusted_probe/test_external_regression_probe.py"
            probe_sha = hashlib.sha256(probe_path.read_bytes()).hexdigest()
        elif target:
            test_digests = _target_file_digests(checkout, target)
        # Neutral runtime dir: the observer is controller property, executed by path,
        # never imported as part of any package either side could shadow.
        runtime_dir = Path(mkdtemp(prefix="trusted-controller-runtime-"))
        observer = runtime_dir / "pytest_observer.py"
        shutil.copy(_OBSERVER_SOURCE, observer)
        artifact_root.mkdir(parents=True, exist_ok=True)
        manifest = artifact_root / "execution.json"
        command = (python, str(observer), "--output", str(manifest), "--", "-q", run_target)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(60.0, timeout_seconds * 2),
            cwd=str(checkout),
            check=False,
        )
        if not manifest.exists():
            return ControlledSide(
                SideResult(
                    name, sha, completed.returncode, None, frozenset(), frozenset(),
                    frozenset(), frozenset(), frozenset(), fingerprint.pytest_version,
                    f"no controller manifest written: "
                    f"{completed.stdout[-300:]} {completed.stderr[-300:]}",
                ),
                fingerprint,
                probe_sha,
                resolution_ok=_import_resolution_ok(fingerprint, checkout),
                test_file_digests=test_digests,
            )
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema") != "vool.pytest-execution.v1" or "failed" not in payload:
            raise RuntimeError("controller manifest malformed -- controller bug, fail closed")
        result = SideResult(
            name=name,
            sha=sha,
            returncode=completed.returncode,
            artifact_dir=artifact_root,
            collected=frozenset(str(item) for item in payload["collected_nodeids"]),
            failed=frozenset(str(entry["nodeid"]) for entry in payload["failed"]),
            skipped=frozenset(str(entry["nodeid"]) for entry in payload["skipped"]),
            xfailed=frozenset(str(item) for item in payload["xfailed"]),
            xpassed=frozenset(str(item) for item in payload["xpassed"]),
            pytest_version=fingerprint.pytest_version,
            error="",
        )
        return ControlledSide(
            result,
            fingerprint,
            probe_sha,
            resolution_ok=_import_resolution_ok(fingerprint, checkout),
            test_file_digests=test_digests,
        )
    finally:
        _git(repo, "worktree", "remove", "--force", str(checkout))


def _decide(
    baseline: ControlledSide,
    candidate: ControlledSide,
    *,
    env_ok: bool,
    env_diffs: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    details: dict[str, Any] = dict(env_diffs)
    base, cand = baseline.result, candidate.result
    for side in (base, cand):
        if side.error:
            return (f"{side.name.upper()}_UNRUNNABLE", {"reason": side.error})
        if side.returncode not in (0, 1):
            return (
                f"{side.name.upper()}_UNRUNNABLE",
                {"reason": f"runner exited {side.returncode}"},
            )
    if not baseline.resolution_ok or not candidate.resolution_ok:
        return (
            IMPORT_RESOLUTION_MISMATCH,
            {
                **details,
                "reason": "subject imports resolve outside its own checkout; outcomes are "
                "not attributable to the checked-out source",
                "baseline_resolution": baseline.fingerprint.import_resolution or None,
                "candidate_resolution": candidate.fingerprint.import_resolution or None,
            },
        )
    if not env_ok:
        return (ENVIRONMENT_MISMATCH, details)
    if baseline.probe_sha256 or candidate.probe_sha256:
        if baseline.probe_sha256 != candidate.probe_sha256:
            return ("PROBE_MISMATCH", {"reason": "sides ran different probe bytes"})
        details["probe_sha256"] = baseline.probe_sha256
    if base.collected != cand.collected:
        return (
            TARGET_MISMATCH,
            {
                "only_baseline": sorted(base.collected - cand.collected),
                "only_candidate": sorted(cand.collected - base.collected),
                "note": "native test sets differ between revisions; for cross-revision "
                "behavior questions use EXTERNAL_REGRESSION_PROBE mode",
            },
        )
    details["failing_only_in_baseline"] = sorted(base.failed - cand.failed)
    details["failing_only_in_candidate"] = sorted(cand.failed - base.failed)
    proof_lost = base.failed & (cand.skipped | cand.xfailed)
    if proof_lost:
        return (COVERAGE_REGRESSION, {**details, "proof_lost_nodes": sorted(proof_lost)})
    base_digests, cand_digests = baseline.test_file_digests or {}, candidate.test_file_digests or {}
    if base_digests != cand_digests:
        changed = sorted(
            name
            for name in set(base_digests) & set(cand_digests)
            if base_digests[name] != cand_digests[name]
        )
        return (
            TEST_DEFINITION_CHANGED,
            {
                **details,
                "changed_test_files": changed,
                "note": "native test definitions are not byte-identical across revisions; "
                "a same-nodeid test with a different body is a different test -- use "
                "EXTERNAL_REGRESSION_PROBE for behavior attribution",
            },
        )
    if base.failed and cand.failed:
        if base.failed == cand.failed:
            return (PRE_EXISTING_CONFIRMED, details)
        return (FAILURE_SET_CHANGED, details)
    if not base.failed and not cand.failed:
        return (BOTH_GREEN, details)
    if not base.failed:
        return (NEW_REGRESSION, details)
    return (BASELINE_RED_CANDIDATE_GREEN, details)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two revisions under ONE trusted controller-owned instrumentation."
    )
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target", help="Native test path (each revision's own tests).")
    group.add_argument("--probe", type=Path, help="Immutable external probe run on BOTH sides.")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--label", default="trusted-paired")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--resolution-module",
        default=None,
        help="Module whose import must resolve INSIDE each checkout "
        "(editable-install/dependency-drift guard).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    artifact_root = args.artifact_root.resolve() / args.label
    identity = _controller_identity()
    record: dict[str, Any] = {
        "schema": "vool.trusted-baseline-comparison.v1",
        "controller": identity,
        "proof_type": EXTERNAL_REGRESSION_PROBE if args.probe else NATIVE_SUITE,
        "repo": str(repo),
        "candidate_ref": args.candidate,
        "baseline_ref": args.baseline,
        "target_or_probe": str(args.probe or args.target),
        "dirty_state_of_calling_worktree": _git(repo, "status", "--porcelain").splitlines(),
    }
    if args.probe is not None:
        record["probe_sha256"] = hashlib.sha256(args.probe.resolve().read_bytes()).hexdigest()

    def as_dict(side: ControlledSide) -> dict[str, Any]:
        payload = {
            "sha": side.result.sha,
            "returncode": side.result.returncode,
            "artifacts": str(side.result.artifact_dir) if side.result.artifact_dir else None,
            "collected_nodeids": sorted(side.result.collected),
            "failed_nodeids": sorted(side.result.failed),
            "skipped_nodeids": sorted(side.result.skipped),
            "xfailed_nodeids": sorted(side.result.xfailed),
            "xpassed_nodeids": sorted(side.result.xpassed),
            "error": side.result.error,
            "environment_fingerprint": side.fingerprint.as_dict(),
        }
        if side.probe_sha256:
            payload["probe_sha256"] = side.probe_sha256
        return payload

    verdict = "INFRASTRUCTURE_ERROR"
    details: dict[str, Any] = {}
    try:
        with TemporaryDirectory(prefix="vool-trusted-controller-") as tmp:
            worktree_root = Path(tmp)
            common = {
                "repo": repo,
                "worktree_root": worktree_root,
                "artifact_root": artifact_root,
                "python": args.python,
                "timeout_seconds": args.timeout_seconds,
                "resolution_module": args.resolution_module,
            }
            candidate = _run_side(
                ref=args.candidate,
                name="candidate",
                target=args.target or "",
                probe_path=args.probe,
                **common,
            )
            baseline = _run_side(
                ref=args.baseline,
                name="baseline",
                target=args.target or "",
                probe_path=args.probe,
                **common,
            )
            env_ok, env_diffs = _fingerprints_compatible(
                baseline.fingerprint, candidate.fingerprint
            )
            verdict, details = _decide(baseline, candidate, env_ok=env_ok, env_diffs=env_diffs)
            record["candidate"] = as_dict(candidate)
            record["baseline"] = as_dict(baseline)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        record["infrastructure_error"] = str(exc)
        _write_record(record, artifact_root, verdict)
        print(f"!! trusted-controller infrastructure error: {exc}", flush=True)
        return 3

    record["verdict"] = verdict
    record["details"] = details
    # A run whose controller closure is uncommitted/dirty may not confirm
    # anything: its bytes are not identified by any commit, and the recorded
    # closure hash is the only true identity.
    if verdict == PRE_EXISTING_CONFIRMED and not identity["identity_trusted"]:
        verdict = CONTROLLER_IDENTITY_UNTRUSTED
        details = {
            "suppressed_verdict": PRE_EXISTING_CONFIRMED,
            "reason": "controller closure has uncommitted modifications; "
            "the implementation that produced this comparison is not committed, "
            "so no attribution may be confirmed under it",
        }
        record["verdict"] = verdict
        record["details"] = details
    _write_record(record, artifact_root, verdict)
    if verdict == PRE_EXISTING_CONFIRMED:
        print(f"trusted-controller: {verdict} (identical failing set under one controller)")
        return 0
    if verdict in {NEW_REGRESSION, BASELINE_RED_CANDIDATE_GREEN, BOTH_GREEN, COVERAGE_REGRESSION}:
        print(f"trusted-controller: {verdict} -- NOT attributable as pre-existing")
        return 1
    if verdict == FAILURE_SET_CHANGED:
        print(f"trusted-controller: {verdict}")
        return 2
    print(f"trusted-controller: {verdict} -- fail closed to unattributed")
    return 2


def _write_record(record: dict[str, Any], artifact_root: Path, verdict: str) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    path = artifact_root / "vool.trusted-baseline-comparison.v1.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"trusted-controller artifact: {path} verdict={verdict}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
