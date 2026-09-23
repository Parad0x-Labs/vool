"""Plan CI Linux shard assignments from measured per-file durations.

Reads the CANONICAL collection manifest (ops/pytest_manifest.py -- the same
authority the verify job writes and the shard resolver collects) and one or
more timing manifests (ops/pytest_timing.py -- the instrument the shard step
now writes), and produces a deterministic duration-aware assignment plus a
dry-run comparison against the CURRENT assignment (size-descending
round-robin, replicated exactly from .github/workflows/ci.yml).

Hard rules this planner refuses to break:

  * every canonically selected Linux file appears in EXACTLY ONE shard --
    the plan is checked against the manifest before it is written, and a
    missing or duplicated file is a red error, never a warning;
  * macOS-only files are routed by the workflow's OWN pinned list (parsed
    from .github/workflows/ci.yml -- there is no second authority to drift);
    they must all be present in the manifest or planning fails loudly, the
    same way the resolver itself fails;
  * deleted files and stale timing records cannot remove coverage: timings
    referencing files the manifest no longer collects are reported as stale
    and ignored, never propagated;
  * missing, corrupt, schema-mismatched, or wholly unusable timing evidence
    fails the planner -- it never silently degrades to a half-informed plan.
    Individual non-finite sample records are rejected and the file falls back
    to the deterministic default weight (reported, counted, never hidden);
  * timing inputs are DATA: JSON in, JSON out. No timing value is ever
    executed, interpolated into a command, or used as one;
  * platforms are not mixed: timing manifests carry the sys.platform they
    were measured on, and records from another platform are incompatible and
    unused. A planner run with zero usable measurements for its platform
    fails instead of planning blind.

All durations this tool prints are ESTIMATES derived from recorded evidence
(the median per file across usable samples), labelled as such in every output.
A shorter slowest shard is a wall-time improvement; it is NOT automatically a
reduction in total billed compute -- the comparison reports both.

This tool PLANS. Activating its output in CI is a separate, deliberate change
to the resolver; nothing here edits the workflow.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.pytest_timing import SCHEMA as TIMING_SCHEMA

MANIFEST_SCHEMA = "vool.pytest-manifest.v1"
PLAN_SCHEMA = "vool.shard-plan.v1"
DEFAULT_SHARDS = 10
#: sys.platform value the GitHub Linux shards measure as; timing manifests from
#: any other platform family are incompatible inputs for a Linux plan.
PLATFORM_BY_REQUEST = {"linux": "linux", "macos": "darwin", "windows": "win32"}


class PlanningError(Exception):
    """A condition under which no honest plan can be produced."""


@dataclass(frozen=True)
class FileWeight:
    seconds: float
    source: str  # "measured" | "fallback"
    samples: int
    invalid_samples: int


@dataclass
class TimingEvidence:
    per_file_samples: dict[str, list[float]] = field(default_factory=dict)
    invalid_by_file: dict[str, int] = field(default_factory=dict)
    invalid_samples: int = 0
    stale_files: set[str] = field(default_factory=set)
    incompatible_files: set[str] = field(default_factory=set)
    sources: list[str] = field(default_factory=list)


def load_manifest(path: Path) -> tuple[str, ...]:
    """Read the canonical collection manifest; anything else is a planning error."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanningError(f"manifest unreadable ({path}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanningError(f"manifest is not valid JSON ({path}): {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        raise PlanningError(f"manifest schema is not {MANIFEST_SCHEMA} ({path})")
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise PlanningError(f"manifest carries no targets ({path})")
    normalized = [str(item) for item in targets]
    if len(normalized) != len(set(normalized)):
        raise PlanningError(f"manifest contains duplicate targets ({path})")
    return tuple(sorted(normalized))


def _load_timing_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PlanningError(f"timing evidence unreadable ({path}): {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PlanningError(f"timing evidence is corrupt JSON ({path}): {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != TIMING_SCHEMA:
        raise PlanningError(f"timing evidence schema is not {TIMING_SCHEMA} ({path})")
    return payload


def load_timing_evidence(
    paths: Sequence[Path], *, sys_platform: str
) -> TimingEvidence:
    """Merge per-file duration samples from every timing manifest.

    Records measured on another platform are incompatible and dropped (counted,
    reported). Non-finite or negative sample values are rejected individually --
    the file keeps its other samples, or falls back if none remain. Files absent
    from the current manifest are NOT dropped silently here; they are collected
    as stale and only classified once the manifest scope is known.
    """

    evidence = TimingEvidence()
    for path in paths:
        payload = _load_timing_file(path)
        evidence.sources.append(str(path))
        environment = payload.get("environment") or {}
        measured_platform = str(environment.get("sys_platform") or "")
        files = payload.get("files")
        if not isinstance(files, dict):
            raise PlanningError(f"timing evidence carries no per-file records ({path})")
        for file_path, record in sorted(files.items()):
            if measured_platform and measured_platform != sys_platform:
                evidence.incompatible_files.add(str(file_path))
                continue
            total = record.get("total_seconds") if isinstance(record, dict) else None
            try:
                seconds = float(total)
            except (TypeError, ValueError):
                seconds = math.nan
            if isinstance(total, bool) or not math.isfinite(seconds) or seconds < 0:
                evidence.invalid_samples += 1
                evidence.invalid_by_file[str(file_path)] = (
                    evidence.invalid_by_file.get(str(file_path), 0) + 1
                )
                continue
            evidence.per_file_samples.setdefault(str(file_path), []).append(seconds)
    return evidence


def macos_only_targets(repo_root: Path) -> frozenset[str]:
    """The macOS routing list, parsed from the workflow itself (the only authority).

    Mirrors the pin in tests/test_delivery_contracts.py: the resolver's
    ``macos_only = {...}`` block and the macos job's file list are one contract.
    The planner reads it rather than restating it, so a workflow edit cannot
    leave the planner routing a stale set.
    """

    workflow_path = repo_root / ".github" / "workflows" / "ci.yml"
    try:
        workflow = workflow_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanningError(f"cannot read the workflow for macOS routing ({workflow_path}): {exc}") from exc
    match = re.search(r"macos_only = \{(.*?)\}", workflow, re.DOTALL)
    if not match:
        raise PlanningError("workflow resolver no longer pins a macos_only set")
    routed = frozenset(re.findall(r'"([^"]+)"', match.group(1)))
    if not routed:
        raise PlanningError("workflow resolver pins an EMPTY macos_only set")
    return routed


def current_assignment(
    targets: Sequence[str], *, repo_root: Path, shards: int
) -> tuple[tuple[str, ...], ...]:
    """Replicate the resolver's CURRENT partition exactly: sort by descending file
    size, deal round-robin. Same input, same bytes in ci.yml, same output."""

    def _size(target: str) -> int:
        try:
            return (repo_root / target).stat().st_size
        except OSError as exc:
            raise PlanningError(
                f"cannot replicate the current assignment: {target} is in the manifest "
                f"but not in the working tree ({exc})"
            ) from exc

    sized = sorted(targets, key=lambda f: -_size(f))
    buckets: list[list[str]] = [[] for _ in range(shards)]
    for index, target in enumerate(sized):
        buckets[index % shards].append(target)
    return tuple(tuple(items) for items in buckets)


def plan_lpt(
    weights: dict[str, float], *, shards: int
) -> tuple[tuple[str, ...], ...]:
    """Longest-processing-time-first assignment (deterministic).

    Classic LPT: files by descending weight (ties by path), each onto the
    currently lightest shard (ties by index). Deterministic by construction --
    no hashing, no randomness, no environment input.
    """

    order = sorted(weights, key=lambda f: (-weights[f], f))
    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0.0] * shards
    for target in order:
        index = min(range(shards), key=lambda i: (loads[i], i))
        buckets[index].append(target)
        loads[index] += weights[target]
    return tuple(tuple(items) for items in buckets)


def _assignment_is_exactly_one_each(
    assignment: Sequence[Sequence[str]], targets: Sequence[str]
) -> str:
    """Empty string when complete and duplicate-free; otherwise the reason."""

    assigned = [target for shard in assignment for target in shard]
    expected = Counter(targets)
    actual = Counter(assigned)
    missing = sorted(expected - actual)
    duplicated = sorted({target for target, count in actual.items() if count > 1})
    unexpected = sorted(actual - expected)
    problems = []
    if missing:
        problems.append(f"missing: {missing[:5]}{' ...' if len(missing) > 5 else ''}")
    if duplicated:
        problems.append(f"duplicated: {duplicated[:5]}{' ...' if len(duplicated) > 5 else ''}")
    if unexpected:
        problems.append(f"not in manifest: {unexpected[:5]}{' ...' if len(unexpected) > 5 else ''}")
    return "; ".join(problems)


def build_weights(
    linux_targets: Sequence[str],
    evidence: TimingEvidence,
    *,
    fallback_weight: float | None,
) -> tuple[dict[str, FileWeight], float]:
    """Median measured seconds per file; deterministic fallback for the unmeasured.

    The fallback defaults to the median of measured per-file medians -- "a
    typical file" -- and may be pinned explicitly. A pinned fallback with zero
    measurements produces a valid (if useless) plan marked coverage 0.0; an
    automatic fallback with zero measurements is a planning error.
    """

    measured_medians = [
        statistics.median(evidence.per_file_samples[target])
        for target in linux_targets
        if evidence.per_file_samples.get(target)
    ]
    if fallback_weight is None:
        if not measured_medians:
            raise PlanningError(
                "no usable measured durations for this platform -- refusing to plan "
                "blind (pass --fallback-weight <seconds> to plan explicitly unmeasured)"
            )
        fallback = statistics.median(measured_medians)
    else:
        fallback = float(fallback_weight)
    if not math.isfinite(fallback) or fallback <= 0:
        raise PlanningError(f"fallback weight must be a positive finite number, got {fallback!r}")
    weights: dict[str, FileWeight] = {}
    for target in linux_targets:
        samples = evidence.per_file_samples.get(target) or []
        invalid = evidence.invalid_by_file.get(target, 0)
        if samples:
            weights[target] = FileWeight(
                seconds=statistics.median(samples),
                source="measured",
                samples=len(samples),
                invalid_samples=invalid,
            )
        else:
            weights[target] = FileWeight(
                seconds=fallback, source="fallback", samples=0, invalid_samples=invalid
            )
    return weights, fallback


def build_plan(
    *,
    repo_root: Path,
    manifest_path: Path,
    timing_paths: Sequence[Path],
    shards: int,
    platform_request: str,
    fallback_weight: float | None = None,
) -> dict[str, Any]:
    if shards < 1:
        raise PlanningError("--shards must be >= 1")
    sys_platform = PLATFORM_BY_REQUEST[platform_request]
    targets = load_manifest(manifest_path)
    macos_only = macos_only_targets(repo_root)

    routed = sorted(set(targets) & macos_only)
    if set(routed) != macos_only:
        missing = sorted(macos_only - set(targets))
        raise PlanningError(
            f"macos routing drifted: manifest no longer collects {missing} -- "
            "the workflow's pinned macOS set and the collection disagree"
        )
    linux_targets = tuple(target for target in targets if target not in macos_only)
    if not linux_targets:
        raise PlanningError("no Linux targets remain after macOS routing")

    evidence = load_timing_evidence(timing_paths, sys_platform=sys_platform)
    target_set = set(targets)
    stale = sorted(target for target in evidence.per_file_samples if target not in target_set)
    weights, fallback = build_weights(
        linux_targets, evidence, fallback_weight=fallback_weight
    )
    measured_files = sorted(t for t, w in weights.items() if w.source == "measured")
    fallback_files = sorted(t for t, w in weights.items() if w.source == "fallback")

    planned = plan_lpt({target: w.seconds for target, w in weights.items()}, shards=shards)
    completeness = _assignment_is_exactly_one_each(planned, linux_targets)
    if completeness:
        raise PlanningError(f"planned assignment is not complete ({completeness})")

    current = current_assignment(linux_targets, repo_root=repo_root, shards=shards)
    current_completeness = _assignment_is_exactly_one_each(current, linux_targets)
    if current_completeness:
        raise PlanningError(
            f"replicated current assignment is not complete ({current_completeness}) -- "
            "the replication of the resolver's partition is wrong"
        )

    seconds = {target: w.seconds for target, w in weights.items()}
    planned_per_shard = [round(sum(seconds[t] for t in shard), 3) for shard in planned]
    current_per_shard = [round(sum(seconds[t] for t in shard), 3) for shard in current]
    coverage_fraction = len(measured_files) / len(linux_targets)
    return {
        "schema": PLAN_SCHEMA,
        "mode": "duration-aware-lpt",
        "platform": platform_request,
        "shards": shards,
        "units": "seconds (ESTIMATES from recorded evidence, not measurements of this plan)",
        "manifest": str(manifest_path),
        "timing_sources": list(evidence.sources),
        "routed_to_macos": routed,
        "weights": {
            target: {
                "estimated_seconds": round(weights[target].seconds, 6),
                "source": weights[target].source,
                "samples": weights[target].samples,
            }
            for target in sorted(weights)
        },
        "fallback_weight_seconds": round(fallback, 6),
        "assignment": {
            str(index): list(shard) for index, shard in enumerate(planned)
        },
        "current_assignment": {
            str(index): list(shard) for index, shard in enumerate(current)
        },
        "estimated": {
            "planned": {
                "per_shard_seconds": planned_per_shard,
                "max_shard_seconds": max(planned_per_shard),
                "aggregate_seconds": round(sum(planned_per_shard), 3),
            },
            "current": {
                "per_shard_seconds": current_per_shard,
                "max_shard_seconds": max(current_per_shard),
                "aggregate_seconds": round(sum(current_per_shard), 3),
            },
            "max_shard_delta_seconds": round(
                max(current_per_shard) - max(planned_per_shard), 3
            ),
            "note": (
                "aggregate is identical by construction (same files, same weights); "
                "max_shard_seconds is the estimated WALL-TIME bound of the matrix. "
                "A lower max shard is a wall-time improvement, not by itself a "
                "reduction in total billed compute."
            ),
        },
        "coverage": {
            "linux_files": len(linux_targets),
            "measured_files": len(measured_files),
            "fallback_files": len(fallback_files),
            "fallback_file_names": fallback_files,
            "fraction": round(coverage_fraction, 4),
            "stale_timing_files": stale,
            "incompatible_platform_files": sorted(evidence.incompatible_files),
            "invalid_sample_records": evidence.invalid_samples,
        },
    }


def _print_summary(plan: dict[str, Any]) -> None:
    estimated = plan["estimated"]
    coverage = plan["coverage"]
    print(f"shard plan ({plan['mode']}, {plan['shards']} shards, platform={plan['platform']})")
    print(
        f"  files: {coverage['linux_files']} linux "
        f"({len(plan['routed_to_macos'])} routed to macOS by the workflow's own pin)"
    )
    print(
        f"  measurement coverage: {coverage['measured_files']}/{coverage['linux_files']} "
        f"({coverage['fraction']:.1%}) -- {coverage['fallback_files']} on the "
        f"{plan['fallback_weight_seconds']:.3f}s fallback weight"
    )
    if coverage["stale_timing_files"]:
        print(f"  stale timing records (files no longer collected): {len(coverage['stale_timing_files'])}")
    if coverage["incompatible_platform_files"]:
        print(
            f"  records ignored from other platforms: "
            f"{len(coverage['incompatible_platform_files'])}"
        )
    if coverage["invalid_sample_records"]:
        print(
            f"  non-finite sample records rejected: {coverage['invalid_sample_records']}"
        )
    print("  ESTIMATES from recorded evidence (median per file):")
    print(
        f"    current  max shard {estimated['current']['max_shard_seconds']:>10.1f}s "
        f"| aggregate {estimated['current']['aggregate_seconds']:>10.1f}s"
    )
    print(
        f"    planned  max shard {estimated['planned']['max_shard_seconds']:>10.1f}s "
        f"| aggregate {estimated['planned']['aggregate_seconds']:>10.1f}s"
    )
    print(
        f"    estimated slowest-shard reduction: "
        f"{estimated['max_shard_delta_seconds']:.1f}s (wall time; not billed-compute)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan duration-aware Linux shard assignments from measured timing evidence."
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--manifest", type=Path, required=True, help="Canonical pytest manifest (ops/pytest_manifest.py).")
    parser.add_argument(
        "--timing", action="append", type=Path, required=True,
        help="Timing manifest (ops/pytest_timing.py); repeatable, one per shard run.",
    )
    parser.add_argument("--shards", type=int, default=DEFAULT_SHARDS)
    parser.add_argument(
        "--platform", choices=sorted(PLATFORM_BY_REQUEST), default="linux",
        help="Which environment's shard matrix is being planned.",
    )
    parser.add_argument(
        "--fallback-weight", type=float, default=None,
        help="Seconds for files with no usable measurements (default: median of measured files).",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Write the full plan JSON here (otherwise only the summary prints).",
    )
    parser.add_argument(
        "--write-shard-files", type=Path, default=None,
        help="Write shard-<i>-files.txt (resolver format) here for a future activation.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = build_plan(
            repo_root=args.repo_root.resolve(),
            manifest_path=args.manifest,
            timing_paths=args.timing,
            shards=args.shards,
            platform_request=args.platform,
            fallback_weight=args.fallback_weight,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(f"wrote {args.output}")
        if args.write_shard_files:
            args.write_shard_files.mkdir(parents=True, exist_ok=True)
            for index, shard in plan["assignment"].items():
                (args.write_shard_files / f"shard-{index}-files.txt").write_text(
                    "\n".join(shard) + "\n", encoding="utf-8"
                )
            print(f"wrote {args.shards} shard file lists to {args.write_shard_files}")
    except PlanningError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    _print_summary(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
