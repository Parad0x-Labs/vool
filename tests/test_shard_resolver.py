"""Contract tests for the duration-aware CI shard resolver (ops/shard_resolver.py).

The resolver is the ACTIVATION of ops/shard_plan.py's evidence: it may rebalance
shards only under guarantees — every collected Linux file in exactly one shard,
deterministic LPT over the committed snapshot, explicit fallback weight for
unmeasured files, stale evidence ignored, invalid evidence degraded to the
documented size round-robin fallback with a visible warning, and the snapshot
treated as untrusted DATA (no path, value, or field of it ever executed or
trusted). Each test pins one way a resolver could otherwise silently lose
coverage, trust bad evidence, or run unexecuted input.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ops.shard_plan import current_assignment
from ops.shard_plan import plan_lpt as real_plan_lpt
from ops.shard_resolver import (
    MODE_FALLBACK,
    MODE_MEASURED,
    ResolverError,
    assign_duration_aware,
    load_weights_snapshot,
    resolve_shard,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SHARDS = 10


def _synthetic_repo(tmp_path: Path, *, file_sizes: dict[str, int]) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    for name, size in file_sizes.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#" * size, encoding="utf-8")
    return repo


def _write_snapshot(
    path: Path,
    weights: dict[str, float],
    *,
    sys_platform: str = "linux",
    fallback: float = 7.5,
    **overrides: object,
) -> Path:
    payload: dict[str, object] = {
        "schema": "vool.shard-weights.v1",
        "platform": "linux",
        "sys_platform": sys_platform,
        "fallback_weight_seconds": fallback,
        "provenance": {"generated_by": "test fixture"},
        "weights": weights,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _files(count: int, *, prefix: str = "tests/test_file") -> list[str]:
    return sorted(f"{prefix}_{index}.py" for index in range(count))


def _resolve_all(
    files: list[str], *, repo: Path, snapshot: Path, shards: int = SHARDS
) -> list:
    return [
        resolve_shard(
            files,
            shard=shard,
            shards=shards,
            snapshot_path=snapshot,
            repo_root=repo,
            sys_platform="linux",
        )
        for shard in range(shards)
    ]


def test_valid_snapshot_assigns_every_file_exactly_once_deterministically(
    tmp_path: Path,
) -> None:
    files = _files(30)
    weights = {name: float((index % 7) + 1) for index, name in enumerate(files)}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assigned = [name for resolution in resolutions for name in resolution.files]
    assert Counter(assigned) == Counter(files)
    assert all(resolution.mode == MODE_MEASURED for resolution in resolutions)
    assert all(resolution.reason is None for resolution in resolutions)
    again = _resolve_all(files, repo=repo, snapshot=snapshot)
    assert [r.files for r in again] == [r.files for r in resolutions]


def test_measured_partition_differs_from_size_round_robin_on_skewed_weights(
    tmp_path: Path,
) -> None:
    # One dominant duration on a small file: size round-robin cannot see it,
    # the measured resolver isolates it on its own shard's lightest slot.
    files = _files(20)
    weights = {name: 2.0 for name in files}
    weights[files[3]] = 500.0
    sizes = {name: 100 for name in files}
    sizes[files[7]] = 5000  # the size heuristic keys on a DIFFERENT file
    repo = _synthetic_repo(tmp_path, file_sizes=sizes)
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)
    measured_slices = {tuple(resolution.files) for resolution in resolutions}
    fallback_slices = {
        tuple(current_assignment(files, repo_root=repo, shards=SHARDS)[i])
        for i in range(SHARDS)
    }
    assert measured_slices != fallback_slices


def test_new_file_without_measurement_gets_explicit_fallback_and_stays_assigned(
    tmp_path: Path,
) -> None:
    files = _files(21)
    measured = files[:-1]
    new_file = files[-1]
    weights = {name: float(index + 1) for index, name in enumerate(measured)}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assigned = [name for resolution in resolutions for name in resolution.files]
    assert Counter(assigned) == Counter(files)  # the unmeasured file still runs
    owner = next(r for r in resolutions if new_file in r.files)
    assert owner.mode == MODE_MEASURED
    unmeasured_reports = [r for r in resolutions if r.unmeasured]
    assert unmeasured_reports and all(r.unmeasured == [new_file] for r in unmeasured_reports)
    assert any("fallback weight" in line for r in resolutions for line in r.warnings())


def test_zero_duration_is_a_valid_measurement_not_a_missing_file(tmp_path: Path) -> None:
    files = _files(5)
    weights = {name: 1.0 for name in files}
    weights[files[2]] = 0.0
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assigned = [name for resolution in resolutions for name in resolution.files]
    assert Counter(assigned) == Counter(files)
    assert all("fallback weight" not in line for r in resolutions for line in r.warnings())


def test_stale_snapshot_entries_for_deleted_files_are_ignored_not_coverage(
    tmp_path: Path,
) -> None:
    files = _files(10)
    weights = {name: float(index + 1) for index, name in enumerate(files)}
    weights["tests/test_deleted_suite.py"] = 999.0  # stale: not collected anymore
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assigned = [name for resolution in resolutions for name in resolution.files]
    assert Counter(assigned) == Counter(files)  # collected set unchanged
    assert all(resolution.stale == ["tests/test_deleted_suite.py"] for resolution in resolutions)
    assert any("ignored" in line for r in resolutions for line in r.warnings())


@pytest.mark.parametrize(
    "damage",
    ["missing", "corrupt", "wrong_schema", "no_weights", "weights_not_a_map", "no_fallback"],
)
def test_invalid_evidence_uses_the_documented_safe_fallback(tmp_path: Path, damage: str) -> None:
    files = _files(12)
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 + index for index, name in enumerate(files)})
    snapshot_path = tmp_path / "shard_weights.json"
    if damage == "missing":
        snapshot = tmp_path / "absent.json"  # never written
    else:
        weights = {name: float(index + 1) for index, name in enumerate(files)}
        snapshot = _write_snapshot(snapshot_path, weights)
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        if damage == "corrupt":
            snapshot.write_text("{ this is not json", encoding="utf-8")
        elif damage == "wrong_schema":
            payload["schema"] = "something.else.v1"
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
        elif damage == "no_weights":
            payload["weights"] = {}
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
        elif damage == "weights_not_a_map":
            payload["weights"] = ["not", "a", "map"]
            snapshot.write_text(json.dumps(payload), encoding="utf-8")
        elif damage == "no_fallback":
            del payload["fallback_weight_seconds"]
            snapshot.write_text(json.dumps(payload), encoding="utf-8")

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assigned = [name for resolution in resolutions for name in resolution.files]
    assert Counter(assigned) == Counter(files)  # invalid evidence never skips tests
    assert all(resolution.mode == MODE_FALLBACK for resolution in resolutions)
    assert all(resolution.reason for resolution in resolutions)
    assert any("fell back" in line for r in resolutions for line in r.warnings())
    # The documented fallback IS the previous resolver partition, exactly.
    previous = current_assignment(files, repo_root=repo, shards=SHARDS)
    assert [tuple(r.files) for r in resolutions] == [tuple(shard) for shard in previous]


def test_foreign_platform_snapshot_is_refused_not_averaged_in(tmp_path: Path) -> None:
    files = _files(8)
    weights = {name: float(index + 1) for index, name in enumerate(files)}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(
        tmp_path / "shard_weights.json", weights, sys_platform="darwin"
    )

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)

    assert all(resolution.mode == MODE_FALLBACK for resolution in resolutions)
    assert all("'darwin'" in resolution.reason for resolution in resolutions)


@pytest.mark.parametrize(
    "key,value",
    [
        ("../escape.py", 1.0),
        ("tests/../../escape.py", 1.0),
        ("/absolute/path.py", 1.0),
        ("tests\\windows.py", 1.0),
        ("tests//double.py", 1.0),
        ("tests/trailing/", 1.0),
        ("tests/negative.py", -1.0),
        ("tests/string.py", "10"),
        ("tests/boolean.py", True),
    ],
)
def test_malicious_keys_and_values_invalidate_the_whole_snapshot(
    tmp_path: Path, key: str, value: object
) -> None:
    files = _files(6)
    weights = {name: float(index + 1) for index, name in enumerate(files)}
    weights[key] = value
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    with pytest.raises(ResolverError):
        load_weights_snapshot(snapshot, sys_platform="linux")

    resolutions = _resolve_all(files, repo=repo, snapshot=snapshot)
    assigned = [name for resolution in resolutions for name in resolution.files]
    # The tampered entry never reaches a shard list, and coverage survives.
    assert Counter(assigned) == Counter(files)
    assert key not in assigned
    assert all(resolution.mode == MODE_FALLBACK for resolution in resolutions)


def test_non_finite_weight_values_are_rejected(tmp_path: Path) -> None:
    files = _files(6)
    weights = {name: 1.0 for name in files}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    # json.loads parses 1e999 to float('inf'); a NaN parses from the literal too.
    payload["weights"][files[0]] = float("inf")
    payload["weights"][files[1]] = float("nan")
    snapshot.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ResolverError, match="not a valid duration"):
        load_weights_snapshot(snapshot, sys_platform="linux")


def test_incomplete_assignment_is_refused_and_degrades_to_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _files(9)
    weights = {name: float(index + 1) for index, name in enumerate(files)}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    real_lpt = real_plan_lpt

    def dropping_lpt(weights: dict[str, float], *, shards: int):
        assignment = list(real_lpt(weights, shards=shards))
        assignment[0] = assignment[0][:-1]  # silently drop one file
        return tuple(assignment)

    monkeypatch.setattr("ops.shard_resolver.plan_lpt", dropping_lpt)
    with pytest.raises(ResolverError, match="not complete"):
        assign_duration_aware(
            files, load_weights_snapshot(snapshot, sys_platform="linux"), shards=3
        )

    def duplicating_lpt(weights: dict[str, float], *, shards: int):
        assignment = list(real_lpt(weights, shards=shards))
        assignment[1] = assignment[1] + (assignment[0][0],)  # run one file twice
        return tuple(assignment)

    monkeypatch.setattr("ops.shard_resolver.plan_lpt", duplicating_lpt)
    resolution = resolve_shard(
        files, shard=0, shards=3, snapshot_path=snapshot, repo_root=repo, sys_platform="linux"
    )
    assert resolution.mode == MODE_FALLBACK
    assert "not complete" in resolution.reason


def test_duplicate_collected_files_are_a_red_resolver_bug_not_fallback(tmp_path: Path) -> None:
    files = _files(6)
    weights = {name: 1.0 for name in files}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)

    with pytest.raises(ResolverError, match="duplicates"):
        resolve_shard(
            files + files[:1],
            shard=0,
            shards=SHARDS,
            snapshot_path=snapshot,
            repo_root=repo,
            sys_platform="linux",
        )


def test_shard_index_out_of_range_is_refused(tmp_path: Path) -> None:
    files = _files(6)
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", {name: 1.0 for name in files})
    with pytest.raises(ResolverError, match="out of range"):
        resolve_shard(
            files, shard=SHARDS, shards=SHARDS, snapshot_path=snapshot,
            repo_root=repo, sys_platform="linux",
        )


def test_cli_end_to_end_matches_the_library_and_reports_warnings(tmp_path: Path) -> None:
    files = _files(11)
    weights = {name: float(index + 1) for index, name in enumerate(files)}
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", weights)
    files_list = tmp_path / "collected.txt"
    files_list.write_text("\n".join(files) + "\n", encoding="utf-8")
    output = tmp_path / "shard-0-files.txt"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "ops" / "shard_resolver.py"),
            "--shard", "0",
            "--shards", str(SHARDS),
            "--files", str(files_list),
            "--weights", str(snapshot),
            "--repo-root", str(repo),
            "--platform", "linux",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )

    assert completed.returncode == 0, completed.stderr
    expected = resolve_shard(
        files, shard=0, shards=SHARDS, snapshot_path=snapshot,
        repo_root=repo, sys_platform="linux",
    )
    assert output.read_text(encoding="utf-8").splitlines() == expected.files
    assert f"[{MODE_MEASURED}]" in completed.stdout

    # Corrupt snapshot: the CLI must still exit 0 on the documented fallback,
    # print the ::warning:: annotation, and write a complete shard list.
    snapshot.write_text("{ corrupt", encoding="utf-8")
    fallback_run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "ops" / "shard_resolver.py"),
            "--shard", "0",
            "--shards", str(SHARDS),
            "--files", str(files_list),
            "--weights", str(snapshot),
            "--repo-root", str(repo),
            "--platform", "linux",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert fallback_run.returncode == 0, fallback_run.stderr
    assert "::warning::" in fallback_run.stdout
    assert f"[{MODE_FALLBACK}]" in fallback_run.stdout
    assert output.read_text(encoding="utf-8").splitlines()


def test_cli_is_red_when_even_the_safe_fallback_cannot_partition(tmp_path: Path) -> None:
    # Invalid evidence AND a collected file missing from disk: the measured
    # path is refused and the size fallback cannot even stat the file. That is
    # a red resolver bug, never a silent empty shard.
    files = [*_files(4), "tests/test_missing_from_disk.py"]
    repo = _synthetic_repo(tmp_path, file_sizes={name: 100 for name in files[:-1]})
    snapshot = _write_snapshot(tmp_path / "shard_weights.json", {name: 1.0 for name in files})
    snapshot.write_text("{ corrupt", encoding="utf-8")  # force the fallback path
    files_list = tmp_path / "collected.txt"
    files_list.write_text("\n".join(files) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "ops" / "shard_resolver.py"),
            "--shard", "0",
            "--shards", str(SHARDS),
            "--files", str(files_list),
            "--weights", str(snapshot),
            "--repo-root", str(repo),
            "--platform", "linux",
            "--output", str(tmp_path / "out.txt"),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 1
    assert "safe fallback" in completed.stderr


def test_committed_snapshot_is_valid_linux_evidence() -> None:
    """The repo's committed snapshot must load as trusted data or the resolver
    is silently running on the size fallback in every CI run -- this pin makes
    that degradation a red test, not a quiet one."""

    snapshot_path = REPO_ROOT / "ops" / "shard_weights.json"
    assert snapshot_path.is_file(), "ops/shard_weights.json is missing: the resolver has no evidence"
    snapshot = load_weights_snapshot(snapshot_path, sys_platform="linux")
    assert snapshot.per_file
    assert snapshot.fallback_seconds > 0
    assert snapshot.provenance.get("generated_by") == "ops/shard_plan.py --write-weights"


def test_snapshot_written_by_the_planner_loads_in_the_resolver(tmp_path: Path) -> None:
    """Round trip: shard_plan --write-weights output must be exactly what the
    resolver trusts -- measured medians only, explicit conservative fallback,
    provenance carried. A snapshot the resolver would refuse is a red bug here,
    before it ever reaches CI."""

    import shutil

    from ops.shard_plan import MANIFEST_SCHEMA, build_plan, macos_only_targets, write_weights_snapshot

    linux = _files(10)
    macos = sorted(macos_only_targets(REPO_ROOT))
    # The planner replicates the size round-robin, so every file needs a size.
    workflow_repo = tmp_path / "wrepo"
    (workflow_repo / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / ".github" / "workflows" / "ci.yml",
        workflow_repo / ".github" / "workflows" / "ci.yml",
    )
    for name in linux:
        target = workflow_repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#" * 100, encoding="utf-8")
    for name in macos:
        target = workflow_repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# macos suite\n", encoding="utf-8")

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "item_count": 30,
                "targets": sorted(set(linux) | set(macos)),
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    timing = tmp_path / "timing.json"
    timing.write_text(
        json.dumps(
            {
                "schema": "vool.pytest-timing.v1",
                "complete": True,
                "exitstatus": 0,
                "environment": {"sys_platform": "linux"},
                "source": {"git_head_sha": "a" * 40, "git_dirty": False},
                "files": {name: {"total_seconds": float(index + 1)} for index, name in enumerate(linux)},
            }
        ),
        encoding="utf-8",
    )

    plan = build_plan(
        repo_root=workflow_repo,
        manifest_path=manifest,
        timing_paths=[timing],
        shards=SHARDS,
        platform_request="linux",
    )
    snapshot_path = tmp_path / "shard_weights.json"
    write_weights_snapshot(plan, snapshot_path)

    snapshot = load_weights_snapshot(snapshot_path, sys_platform="linux")
    assert set(snapshot.per_file) == set(linux)  # only measured files stored
    assert snapshot.per_file[linux[0]] == 1.0
    assert snapshot.provenance["timing_provenance"][0]["git_head_sha"] == "a" * 40
    # The conservative default fallback is the p90 of measured medians.
    import statistics

    medians = sorted(float(index + 1) for index in range(10))
    expected_p90 = statistics.quantiles(medians, n=100, method="inclusive")[89]
    assert snapshot.fallback_seconds == pytest.approx(expected_p90)
