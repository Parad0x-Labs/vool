"""Contract tests for the duration-aware shard planner (ops/shard_plan.py).

The planner is allowed to rebalance shards ONLY under guarantees: complete and
duplicate-free assignment against the canonical manifest, macOS routing taken
from the workflow's own pin, deterministic output, honest handling of new/
deleted/corrupt/unmeasurable inputs, and no mixing of platforms. Each test
pins one way a planner could otherwise silently lose coverage or plan blind.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ops.shard_plan import (
    MANIFEST_SCHEMA,
    PlanningError,
    build_plan,
    current_assignment,
    load_manifest,
    load_timing_evidence,
    macos_only_targets,
    plan_lpt,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

_MACOS_ROUTED_COUNT = 13


def _synthetic_repo(tmp_path: Path, *, file_sizes: dict[str, int]) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    # The REAL workflow: the planner must parse macOS routing from the actual
    # authority, not from a fixture that could drift away from it.
    shutil.copy(REPO_ROOT / ".github" / "workflows" / "ci.yml", repo / ".github" / "workflows" / "ci.yml")
    for name, size in file_sizes.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#" * size, encoding="utf-8")
    return repo


def _macos_files() -> list[str]:
    return sorted(macos_only_targets(REPO_ROOT))


def _write_manifest(path: Path, targets: list[str]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "item_count": len(targets) * 3,
                "targets": sorted(targets),
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_timing(
    path: Path, durations: dict[str, float | None], *, sys_platform: str = "linux"
) -> Path:
    files = {
        name: {"total_seconds": seconds}
        for name, seconds in durations.items()
        if seconds is not None
    }
    path.write_text(
        json.dumps(
            {
                "schema": "vool.pytest-timing.v1",
                "complete": True,
                "exitstatus": 0,
                "environment": {"sys_platform": sys_platform},
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return path


def _standard_setup(tmp_path: Path, *, durations: dict[str, float | None]) -> tuple[Path, Path, Path]:
    linux = sorted(durations)
    macos = _macos_files()
    sizes = {name: 100 + index for index, name in enumerate(linux)}
    repo = _synthetic_repo(tmp_path, file_sizes={**sizes, **{name: 50 for name in macos}})
    manifest = _write_manifest(tmp_path / "manifest.json", linux + macos)
    timing = _write_timing(tmp_path / "timing.json", durations)
    return repo, manifest, timing


def _plan(repo: Path, manifest: Path, timing: Path, **overrides: object) -> dict:
    return build_plan(
        repo_root=repo,
        manifest_path=manifest,
        timing_paths=[timing],
        shards=3,
        platform_request="linux",
        **overrides,  # type: ignore[arg-type]
    )


def test_plan_assigns_every_linux_file_exactly_once(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    plan = _plan(repo, manifest, timing)

    assigned = [target for shard in plan["assignment"].values() for target in shard]
    linux = sorted(durations)
    assert Counter(assigned) == Counter(linux)
    assert not (set(assigned) & set(_macos_files()))


def test_macos_files_route_to_macos_exactly_once_and_drift_is_red(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    plan = _plan(repo, manifest, timing)

    assert sorted(plan["routed_to_macos"]) == _macos_files()
    assert len(plan["routed_to_macos"]) == _MACOS_ROUTED_COUNT

    # A manifest that no longer collects a pinned macOS file is routing drift:
    # the planner must refuse, exactly like the CI resolver itself.
    macos = _macos_files()
    shrunk = _write_manifest(tmp_path / "manifest-short.json", sorted(durations) + macos[:-1])
    with pytest.raises(PlanningError, match="macos routing drifted"):
        _plan(repo, shrunk, timing)


def test_new_file_without_timing_gets_deterministic_fallback_and_stays_included(
    tmp_path: Path,
) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    # A newly added test file: in the manifest, absent from the timing evidence.
    new_file = "tests/test_file_new.py"
    grown = _write_manifest(
        tmp_path / "manifest-grown.json",
        sorted(durations) + _macos_files() + [new_file],
    )
    (repo / new_file).write_text("# new suite\n", encoding="utf-8")
    plan = _plan(repo, grown, timing)

    assigned = [target for shard in plan["assignment"].values() for target in shard]
    assert Counter(assigned) == Counter([*durations, new_file])
    assert plan["weights"][new_file]["source"] == "fallback"
    assert plan["coverage"]["fallback_file_names"] == [new_file]
    assert plan["coverage"]["fraction"] < 1.0


def test_deleted_file_and_stale_timing_records_cannot_remove_coverage(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    # Timing evidence still mentions a deleted file; the manifest does not.
    stale_timing = _write_timing(
        tmp_path / "timing-stale.json",
        {**durations, "tests/test_deleted_suite.py": 999.0},
    )
    plan = _plan(repo, manifest, stale_timing)

    assert plan["coverage"]["stale_timing_files"] == ["tests/test_deleted_suite.py"]
    assigned = [target for shard in plan["assignment"].values() for target in shard]
    assert Counter(assigned) == Counter(sorted(durations))


def test_output_is_deterministic_across_runs(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float((i * 7) % 5 + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    first = _plan(repo, manifest, timing)
    second = _plan(repo, manifest, timing)

    assert first == second


def test_lpt_tie_breaks_are_deterministic() -> None:
    weights = {"tests/test_a.py": 5.0, "tests/test_b.py": 5.0, "tests/test_c.py": 5.0}

    first = plan_lpt(weights, shards=3)
    second = plan_lpt(weights, shards=3)

    assert first == second
    assert sorted(target for shard in first for target in shard) == sorted(weights)


def test_missing_or_corrupt_timing_evidence_is_red(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(PlanningError, match="unreadable"):
        _plan(repo, manifest, missing)

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(PlanningError, match="corrupt"):
        _plan(repo, manifest, corrupt)

    wrong_schema = tmp_path / "wrong-schema.json"
    wrong_schema.write_text(json.dumps({"schema": "something.else.v1", "files": {}}), encoding="utf-8")
    with pytest.raises(PlanningError, match="schema"):
        _plan(repo, manifest, wrong_schema)


def test_non_finite_samples_are_rejected_and_reported_not_averaged(tmp_path: Path) -> None:
    durations = {
        "tests/test_file_0.py": 10.0,
        "tests/test_file_1.py": 20.0,
        "tests/test_file_2.py": float("inf"),  # invalid record
    }
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    # pytest.json cannot hold inf; write it through json to get `Infinity`.
    payload = json.loads(timing.read_text(encoding="utf-8"))
    payload["files"]["tests/test_file_2.py"] = {"total_seconds": float("inf")}
    timing.write_text(json.dumps(payload), encoding="utf-8")
    plan = _plan(repo, manifest, timing)

    assert plan["coverage"]["invalid_sample_records"] == 1
    assert plan["weights"]["tests/test_file_2.py"]["source"] == "fallback"
    assigned = [target for shard in plan["assignment"].values() for target in shard]
    assert "tests/test_file_2.py" in assigned  # rejected sample, not dropped file


def test_all_invalid_evidence_is_red_not_a_fallback_silent_plan(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)
    payload = json.loads(timing.read_text(encoding="utf-8"))
    payload["files"] = {
        name: {"total_seconds": None} for name in payload["files"]
    }
    timing.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PlanningError, match="no usable measured durations"):
        _plan(repo, manifest, timing)


def test_platforms_are_not_mixed(tmp_path: Path) -> None:
    linux_durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, _ = _standard_setup(tmp_path, durations=linux_durations)

    darwin_timing = _write_timing(
        tmp_path / "timing-darwin.json",
        linux_durations,
        sys_platform="darwin",
    )
    # Evidence wholly from another platform: refuse to plan blind.
    with pytest.raises(PlanningError, match="no usable measured durations"):
        _plan(repo, manifest, darwin_timing)

    # Mixed evidence: darwin records are ignored and reported, linux records used.
    linux_timing = _write_timing(tmp_path / "timing-linux.json", linux_durations)
    evidence = load_timing_evidence(
        [linux_timing, darwin_timing], sys_platform="linux"
    )
    assert evidence.incompatible_files
    assert set(evidence.per_file_samples) == set(linux_durations)


def test_planner_improves_max_shard_on_skewed_durations_and_keeps_aggregate(
    tmp_path: Path,
) -> None:
    # One dominant file plus many small ones: round-robin by SIZE cannot see it,
    # duration-aware LPT isolates it.
    durations = {"tests/test_heavy.py": 100.0}
    durations.update({f"tests/test_small_{i}.py": 2.0 for i in range(8)})
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    plan = _plan(repo, manifest, timing)

    current_max = plan["estimated"]["current"]["max_shard_seconds"]
    planned_max = plan["estimated"]["planned"]["max_shard_seconds"]
    assert planned_max < current_max
    # Same files, same weights: aggregate is invariant; only the bound moves.
    assert (
        plan["estimated"]["planned"]["aggregate_seconds"]
        == plan["estimated"]["current"]["aggregate_seconds"]
    )
    # The comparison must label itself as estimates, not measurements.
    assert "ESTIMATES" in plan["units"]


def test_comparison_against_current_replicates_the_resolver_partition(tmp_path: Path) -> None:
    """The 'current' side of the comparison must be the resolver's own algorithm.

    Sizes descending, round-robin -- the exact partition written in
    .github/workflows/ci.yml. If this replication drifts, every before/after
    number the planner reports becomes fiction.
    """

    files = {f"tests/test_file_{i}.py": (i + 1) * 100 for i in range(7)}
    repo = _synthetic_repo(tmp_path, file_sizes=files)

    assignment = current_assignment(sorted(files), repo_root=repo, shards=3)

    sized = sorted(files, key=lambda f: -files[f])
    expected = tuple(
        tuple(sized[index] for index in range(len(sized)) if index % 3 == shard)
        for shard in range(3)
    )
    assert assignment == expected
    assigned = [target for shard in assignment for target in shard]
    assert Counter(assigned) == Counter(files.keys())


def test_manifest_loader_rejects_duplicates_and_foreign_schemas(tmp_path: Path) -> None:
    good = _write_manifest(
        tmp_path / "good.json", ["tests/test_a.py", "tests/test_b.py"]
    )
    assert load_manifest(good) == ("tests/test_a.py", "tests/test_b.py")

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "targets": ["tests/test_a.py", "tests/test_a.py"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PlanningError, match="duplicate"):
        load_manifest(duplicate)

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"schema": "other.v9", "targets": []}), encoding="utf-8")
    with pytest.raises(PlanningError, match="schema"):
        load_manifest(foreign)


def test_cli_end_to_end_writes_plan_and_shard_files(tmp_path: Path) -> None:
    durations = {f"tests/test_file_{i}.py": float(i + 1) for i in range(9)}
    repo, manifest, timing = _standard_setup(tmp_path, durations=durations)

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "ops" / "shard_plan.py"),
            "--repo-root",
            str(repo),
            "--manifest",
            str(manifest),
            "--timing",
            str(timing),
            "--shards",
            "3",
            "--output",
            str(tmp_path / "plan.json"),
            "--write-shard-files",
            str(tmp_path / "shards"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )

    assert completed.returncode == 0, completed.stderr
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["schema"] == "vool.shard-plan.v1"
    lists = sorted((tmp_path / "shards").glob("shard-*-files.txt"))
    assert len(lists) == 3
    union = [line for path in lists for line in path.read_text(encoding="utf-8").splitlines()]
    assert Counter(union) == Counter(sorted(durations))
    # The summary separates the measured/estimated labels.
    assert "ESTIMATES" in completed.stdout


@pytest.mark.parametrize("complete", [False, None, "true", 1])
def test_incomplete_timing_cannot_count_as_measured_coverage(tmp_path, complete):
    repo, manifest, timing = _standard_setup(tmp_path, durations={"tests/test_a.py": 10.0})
    payload = json.loads(timing.read_text())
    payload["complete"] = complete
    timing.write_text(json.dumps(payload))
    with pytest.raises(PlanningError, match="incomplete"):
        _plan(repo, manifest, timing)


@pytest.mark.parametrize("environment", [None, {}, [], "linux", {"sys_platform": ""}])
def test_missing_platform_identity_is_not_compatible_evidence(tmp_path, environment):
    repo, manifest, timing = _standard_setup(tmp_path, durations={"tests/test_a.py": 10.0})
    payload = json.loads(timing.read_text())
    payload["environment"] = environment
    timing.write_text(json.dumps(payload))
    with pytest.raises(PlanningError, match="platform"):
        _plan(repo, manifest, timing)


def test_completed_red_run_remains_usable_timing_evidence(tmp_path):
    repo, manifest, timing = _standard_setup(tmp_path, durations={"tests/test_a.py": 10.0})
    payload = json.loads(timing.read_text())
    payload["exitstatus"] = 1
    timing.write_text(json.dumps(payload))
    plan = _plan(repo, manifest, timing)
    assert plan["coverage"]["fraction"] == 1.0
    assert plan["weights"]["tests/test_a.py"]["estimated_seconds"] == 10.0
