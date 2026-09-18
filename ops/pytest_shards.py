from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from ops.process_tree import (
    ensure_process_group_owner,
    process_group_kwargs,
    stop_owned_process_group_children,
    stop_process_tree,
)

_NON_PYTEST_TEST_SCRIPTS = frozenset({"tests/legacy/test_cas.py"})
# The historical-gold vault preserves recovered test_*.py files as a snapshot; they are NOT this
# repo's suite (import-isolated, off sys.path) and pytest is configured not to collect them
# (pyproject norecursedirs). They are excluded from the tracked-test enumeration too, so the
# "a suite cannot silently disappear" guard does not read them as omitted. See
# tests/test_recovery_vault_isolation.py for the full containment contract.
_VAULT_TEST_PREFIX = "recovery/"
_PYTEST_CONTROL_ENVIRONMENT = (
    "PYTEST_ADDOPTS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_PLUGINS",
)

#: Set in the environment of the children of a full-scope (default-scope) shard run. Required
#: lanes read it (tests/served_browser.py) and turn availability skips into loud failures there:
#: inside the authoritative lane a missing browser is a provisioning defect, not a skip.
GATE_ENV_VAR = "VOOL_GATE"

#: Test files whose scenarios ARE the release proof for a surface. The served chat-UI browser
#: suites are the only tests that drive a real browser against the served page; a runner without
#: a chromium binary used to setup-skip all of them green (started == collected still held and
#: pytest's exit status stayed 0). Each file here must exist in the repository and must execute at
#: least one test in every full-scope shard run -- adding a file to this tuple is how coverage
#: becomes required, and removing one is a reviewed edit, never a silent disappearance.
REQUIRED_EXECUTED_FILES = (
    "tests/test_activity_chat_scope.py",
    "tests/test_chat_activity_search.py",
    "tests/test_chat_page_layout_geometry.py",
    "tests/test_composer_model_anchor.py",
    "tests/test_master_live_incident_contracts.py",
)


@dataclass(frozen=True)
class PytestManifest:
    item_count: int
    targets: tuple[str, ...]
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ShardAssignment:
    index: int
    targets: tuple[str, ...]
    estimated_weight: int


_ALLOWED_PYTEST_ARGS = frozenset(
    {
        "--tb=auto",
        "--tb=long",
        "--tb=short",
        "--tb=line",
        "--tb=native",
        "--tb=no",
    }
)


def validate_pytest_args(pytest_args: Sequence[str]) -> tuple[str, ...]:
    """Accept display-only options; selection and execution controls are forbidden."""

    normalized = tuple(str(item) for item in pytest_args)
    rejected = tuple(item for item in normalized if item not in _ALLOWED_PYTEST_ARGS)
    if rejected:
        rendered = ", ".join(repr(item) for item in rejected)
        raise ValueError(f"unsafe pytest argument(s): {rendered}")
    return normalized


def discover_test_targets(
    *,
    repo_root: Path,
    paths: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Resolve explicit targets only.

    The executable gate does not use this filesystem walk for its default scope. Pytest performs
    canonical collection first, honoring ``testpaths``, hooks, and collection errors, and the
    resulting manifest is what gets sharded.
    """

    raw_paths = [str(item).strip() for item in list(paths or []) if str(item).strip()]
    if not raw_paths:
        raise ValueError("default test discovery belongs to pytest's canonical collector")

    discovered: list[str] = []
    seen: set[str] = set()
    for raw in raw_paths:
        resolved = (
            (repo_root / raw).resolve()
            if not Path(raw).is_absolute()
            else Path(raw).resolve()
        )
        if resolved.is_file():
            rel = resolved.relative_to(repo_root.resolve()).as_posix()
            if rel not in seen:
                seen.add(rel)
                discovered.append(rel)
            continue
        if resolved.is_dir():
            for item in _discover_from_dir(resolved, repo_root=repo_root):
                if item not in seen:
                    seen.add(item)
                    discovered.append(item)
            continue
        raise FileNotFoundError(f"pytest shard target does not exist: {raw}")
    return tuple(discovered)


def collect_test_manifest(
    *,
    repo_root: Path,
    run_root: Path,
    paths: Sequence[str] = (),
    pytest_args: Sequence[str] = (),
    runner: Callable[..., Any] | None = None,
    tail_lines: int = 200,
    timeout_seconds: float = 300.0,
    share_process_group: bool = False,
) -> tuple[int, PytestManifest | None]:
    """Run one authoritative collection and return its machine-written manifest."""

    manifest_path = run_root / "pytest-manifest.json"
    log_path = run_root / "collection.log"
    command = (
        sys.executable,
        str(Path(__file__).resolve().with_name("pytest_manifest.py")),
        "--repo-root",
        str(repo_root.resolve()),
        "--output",
        str(manifest_path),
        "--",
        *tuple(str(item) for item in paths),
        *tuple(str(item) for item in pytest_args),
    )
    print("==> canonical pytest collection", flush=True)
    print(f"$ {' '.join(command)}", flush=True)
    process: Any | None = None
    try:
        with log_path.open("w", encoding="utf-8") as handle:
            if runner is not None:
                completed = runner(
                    command,
                    cwd=str(repo_root),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=max(0.01, float(timeout_seconds)),
                    env=_sanitized_pytest_environment(),
                )
            else:
                process = subprocess.Popen(
                    command,
                    cwd=str(repo_root),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=_sanitized_pytest_environment(),
                    **({} if share_process_group else process_group_kwargs()),
                )
                process.wait(timeout=max(0.01, float(timeout_seconds)))
                completed = process
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"!! pytest collection could not launch: {type(exc).__name__}: {exc}", flush=True)
        return 1, None
    finally:
        if process is not None:
            if share_process_group and os.name != "nt":
                stop_owned_process_group_children()
            else:
                stop_process_tree(process)

    rc = int(completed.returncode)
    if rc != 0:
        print(f"!! pytest collection failed (exit {rc})", flush=True)
        _print_log_tail(log_path, tail_lines=tail_lines)
        return 1, None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "vool.pytest-manifest.v1":
            raise ValueError("unexpected manifest schema")
        item_count = int(payload["item_count"])
        targets = tuple(str(item) for item in payload["targets"])
        raw_items = payload["items"]
        items = tuple((str(item["nodeid"]), str(item["path"])) for item in raw_items)
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"!! pytest collection produced no valid manifest: {exc}", flush=True)
        return 1, None
    if item_count <= 0 or not targets:
        print(
            f"!! pytest collection was empty (items={item_count}, files={len(targets)})",
            flush=True,
        )
        return 1, None
    if len(targets) != len(set(targets)):
        print("!! pytest collection manifest contains duplicate files", flush=True)
        return 1, None
    nodeids = tuple(nodeid for nodeid, _ in items)
    item_targets = {path for _, path in items}
    if len(items) != item_count or len(nodeids) != len(set(nodeids)):
        print("!! pytest collection manifest has an invalid node inventory", flush=True)
        return 1, None
    if item_targets != set(targets):
        print("!! pytest collection file and node inventories disagree", flush=True)
        return 1, None
    print(f"collected {item_count} items from {len(targets)} files", flush=True)
    return 0, PytestManifest(item_count=item_count, targets=targets, items=items)


def partition_targets(
    targets: Sequence[str],
    *,
    repo_root: Path,
    workers: int,
) -> tuple[ShardAssignment, ...]:
    """Assign paths by stable hash so unrelated files cannot reshuffle existing shards."""

    normalized_workers = max(1, int(workers))
    ordered_targets = sorted({str(item) for item in targets if str(item).strip()})
    if not ordered_targets:
        return tuple()
    shard_lists: list[list[str]] = [[] for _ in range(normalized_workers)]
    for target in ordered_targets:
        digest = hashlib.blake2b(target.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % normalized_workers
        shard_lists[index].append(target)
    return tuple(
        ShardAssignment(
            index=index + 1,
            targets=tuple(items),
            estimated_weight=sum(_target_weight(repo_root / item) for item in items),
        )
        for index, items in enumerate(shard_lists)
        if items
    )


def assignments_match_manifest(
    targets: Sequence[str], assignments: Sequence[ShardAssignment]
) -> bool:
    assigned = [target for assignment in assignments for target in assignment.targets]
    return Counter(str(item) for item in targets) == Counter(assigned)


def tracked_test_targets(repo_root: Path) -> tuple[str, ...]:
    """Return tracked pytest-shaped files so deleting a suite cannot become empty success."""

    completed = subprocess.run(
        ("git", "-C", str(repo_root), "ls-files", "-z"),
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git ls-files failed (exit {completed.returncode})")
    return tuple(
        sorted(
            path
            for path in completed.stdout.split("\0")
            if path
            and Path(path).name.startswith("test_")
            and path.endswith(".py")
            and path not in _NON_PYTEST_TEST_SCRIPTS
            and not path.startswith(_VAULT_TEST_PREFIX)
        )
    )


def collection_manifest_gaps(
    collected_targets: Sequence[str], tracked_targets: Sequence[str]
) -> tuple[str, ...]:
    return tuple(sorted(set(tracked_targets) - set(collected_targets)))


def build_shard_command(
    assignment: ShardAssignment,
    *,
    pytest_args: Sequence[str] = (),
    execution_manifest_path: Path | None = None,
) -> tuple[str, ...]:
    if execution_manifest_path is not None:
        return (
            sys.executable,
            str(Path(__file__).resolve().with_name("pytest_execution.py")),
            "--output",
            str(execution_manifest_path),
            "--",
            "-q",
            *assignment.targets,
            *tuple(str(item) for item in pytest_args),
        )
    return (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *assignment.targets,
        *tuple(str(item) for item in pytest_args),
    )


def run_shards(
    assignments: Sequence[ShardAssignment],
    *,
    repo_root: Path,
    pytest_args: Sequence[str] = (),
    shard_label: str = "full",
    dry_run: bool = False,
    launcher: Callable[..., subprocess.Popen[str]] | None = None,
    run_root: Path | None = None,
    timeout_seconds: float = 1800.0,
    tail_lines: int = 200,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    expected_nodeids_by_target: dict[str, tuple[str, ...]] | None = None,
    share_process_group: bool = False,
    extra_child_env: dict[str, str] | None = None,
) -> int:
    try:
        validated_pytest_args = validate_pytest_args(pytest_args)
    except ValueError as exc:
        print(f"!! {exc}", flush=True)
        return 1
    if not assignments:
        print("!! no shard assignments generated", flush=True)
        return 1

    owned_run_root = run_root is None
    actual_run_root = run_root or Path(mkdtemp(prefix=f"vool-pytest-shards-{shard_label}-"))
    logs_dir = actual_run_root / "logs"
    # Shard runtimes live OUTSIDE the artifact root, always, and are removed when the run ends.
    #
    # A shard's `VOOL_HOME` mints a node signing key on first use. The artifact root is wherever
    # the caller wants its EVIDENCE, and CI points it at `.verification-logs/` inside the checkout
    # -- so hanging runtime state off it wrote four private keys into the working tree, and
    # `tests/test_repo_hygiene_check.py` then failed on the gate's own output. The gate failed
    # itself, and only because of where it had put its scratch space.
    #
    # Splitting the two roots is what makes that structurally impossible rather than a rule someone
    # has to remember: evidence a reader needs (logs, execution manifests, assignments) goes to the
    # artifact root and is uploaded; process state nobody reads goes to a temp dir and is deleted.
    runtime_root = Path(mkdtemp(prefix=f"vool-pytest-runtime-{shard_label}-"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    execution_paths = {
        assignment.index: logs_dir / f"shard-{assignment.index}-execution.json"
        for assignment in assignments
    }
    (actual_run_root / "shard-assignments.json").write_text(
        json.dumps(
            {
                "schema": "vool.pytest-shard-assignments.v1",
                "assignments": [
                    {
                        "index": assignment.index,
                        "targets": list(assignment.targets),
                        "estimated_weight": assignment.estimated_weight,
                        "command": list(
                            build_shard_command(
                                assignment,
                                pytest_args=validated_pytest_args,
                                execution_manifest_path=(
                                    execution_paths[assignment.index]
                                    if expected_nodeids_by_target is not None
                                    else None
                                ),
                            )
                        ),
                        "execution_manifest": (
                            execution_paths[assignment.index].relative_to(actual_run_root).as_posix()
                            if expected_nodeids_by_target is not None
                            else None
                        ),
                    }
                    for assignment in assignments
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if owned_run_root:
        print(f"Shard run root: {actual_run_root}", flush=True)

    popen = launcher or subprocess.Popen
    processes: list[tuple[ShardAssignment, Any, Path]] = []
    try:
        for position, assignment in enumerate(assignments, start=1):
            command = build_shard_command(
                assignment,
                pytest_args=validated_pytest_args,
                execution_manifest_path=(
                    execution_paths[assignment.index]
                    if expected_nodeids_by_target is not None
                    else None
                ),
            )
            runtime_home = runtime_root / f"shard-{assignment.index}"
            runtime_home.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / f"shard-{assignment.index}.log"
            print(
                f"==> shard {position}/{len(assignments)} [id={assignment.index}] "
                f"({len(assignment.targets)} files, weight={assignment.estimated_weight})\n"
                f"$ {command[0]} <verified pytest runner> "
                f"<{len(assignment.targets)} manifest files> "
                f"{' '.join(validated_pytest_args)}",
                flush=True,
            )
            if dry_run:
                continue
            env = _sanitized_pytest_environment()
            if extra_child_env:
                env.update(extra_child_env)
            env["VOOL_HOME"] = str(runtime_home)
            env["VOOL_TEST_SHARD"] = str(assignment.index)
            # C15: a shard child is a scratch process by definition — the unattended
            # preflight in tests/conftest.py keys on this marker and pins non-interactive
            # storage (vault + file signer, no Keychain grant) before any runtime import.
            env["VOOL_TEST_MODE"] = "1"
            env.setdefault("VOOL_SKIP_TORCH_GPU_PROBE", "1")
            env.setdefault("PYTHONUNBUFFERED", "1")
            try:
                with log_path.open("w", encoding="utf-8") as handle:
                    process = popen(
                        command,
                        cwd=str(repo_root),
                        env=env,
                        stdout=handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                        **({} if share_process_group else process_group_kwargs()),
                    )
            except (OSError, subprocess.SubprocessError) as exc:
                print(
                    f"!! shard {assignment.index} could not launch: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                return 1
            processes.append((assignment, process, log_path))

        if dry_run:
            return 0

        deadline = monotonic() + max(0.01, float(timeout_seconds))
        pending = list(processes)
        while pending:
            made_progress = False
            for record in list(pending):
                assignment, process, log_path = record
                rc = process.poll()
                if rc is None:
                    continue
                made_progress = True
                pending.remove(record)
                rc = int(rc)
                if rc != 0:
                    print(f"!! shard {assignment.index} failed (exit {rc})", flush=True)
                    _print_log_tail(log_path, tail_lines=tail_lines)
                    return 1
                print(f"ok shard {assignment.index}", flush=True)
            if not pending:
                break
            if monotonic() >= deadline:
                shard_ids = ", ".join(str(item[0].index) for item in pending)
                print(
                    f"!! shard deadline exceeded after {timeout_seconds:g}s; pending: {shard_ids}",
                    flush=True,
                )
                return 1
            if not made_progress:
                sleeper(0.1)
        if expected_nodeids_by_target is not None:
            for assignment, _, _ in processes:
                expected = tuple(
                    nodeid
                    for target in assignment.targets
                    for nodeid in expected_nodeids_by_target.get(target, ())
                )
                error = _execution_manifest_error(
                    execution_paths[assignment.index], expected_nodeids=expected
                )
                if error:
                    print(
                        f"!! shard {assignment.index} execution manifest failed: {error}",
                        flush=True,
                    )
                    return 1
        return 0
    finally:
        if share_process_group and os.name != "nt":
            stop_owned_process_group_children()
        else:
            for _, process, _ in processes:
                stop_process_tree(process)
        # After the shards are down, so nothing is still writing into it.
        shutil.rmtree(runtime_root, ignore_errors=True)
        if dry_run and owned_run_root:
            shutil.rmtree(actual_run_root, ignore_errors=True)


def run_collected_shards(
    *,
    repo_root: Path,
    paths: Sequence[str] = (),
    workers: int,
    pytest_args: Sequence[str] = (),
    shard_label: str = "full",
    dry_run: bool = False,
    timeout_seconds: float = 1800.0,
    collection_timeout_seconds: float = 300.0,
    tail_lines: int = 200,
    artifact_root: Path | None = None,
    share_process_group: bool = False,
) -> int:
    try:
        validated_pytest_args = validate_pytest_args(pytest_args)
    except ValueError as exc:
        print(f"!! {exc}", flush=True)
        return 1
    if artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(
        mkdtemp(
            prefix=f"vool-pytest-shards-{shard_label}-",
            dir=str(artifact_root) if artifact_root is not None else None,
        )
    )
    print(f"Shard run root: {run_root}", flush=True)
    if not paths:
        # A required lane cannot silently disappear: the file must exist on disk, and after the
        # shards run, at least one of its tests must have executed. Deleting a required suite is
        # a reviewed edit to REQUIRED_EXECUTED_FILES, never an empty success.
        missing_required = tuple(
            name for name in REQUIRED_EXECUTED_FILES if not (repo_root / name).is_file()
        )
        if missing_required:
            print("!! required lane files are missing from the repository:", flush=True)
            for name in missing_required:
                print(f"  - {name}", flush=True)
            return 1
    collection_rc, manifest = collect_test_manifest(
        repo_root=repo_root,
        run_root=run_root,
        paths=paths,
        pytest_args=validated_pytest_args,
        tail_lines=tail_lines,
        timeout_seconds=collection_timeout_seconds,
        share_process_group=share_process_group,
    )
    if collection_rc != 0 or manifest is None:
        return 1
    if not paths:
        try:
            tracked_targets = tracked_test_targets(repo_root)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"!! could not establish tracked test scope: {exc}", flush=True)
            return 1
        gaps = collection_manifest_gaps(manifest.targets, tracked_targets)
        if gaps:
            print("!! canonical collection omitted tracked test files:", flush=True)
            for target in gaps:
                print(f"  - {target}", flush=True)
            return 1
    assignments = partition_targets(
        manifest.targets,
        repo_root=repo_root,
        workers=max(1, int(workers)),
    )
    if not assignments_match_manifest(manifest.targets, assignments):
        print("!! shard union does not exactly match the collection manifest", flush=True)
        return 1
    expected_nodeids_by_target = {
        target: tuple(nodeid for nodeid, path in manifest.items if path == target)
        for target in manifest.targets
    }
    return run_shards(
        assignments,
        repo_root=repo_root,
        pytest_args=validated_pytest_args,
        shard_label=shard_label,
        dry_run=dry_run,
        run_root=run_root,
        timeout_seconds=timeout_seconds,
        tail_lines=tail_lines,
        expected_nodeids_by_target=expected_nodeids_by_target,
        share_process_group=share_process_group,
        # A full-scope run is a release-grade lane: required lanes must fail loudly on missing
        # provisioning instead of skipping green. Explicit targets stay dev-friendly.
        extra_child_env={GATE_ENV_VAR: "1"} if not paths else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect canonically, then run VOOL pytest files in isolated stable shards."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional pytest file or directory targets. Defaults to pytest's configured scope.",
    )
    parser.add_argument("--workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument forwarded to collection and each shard pytest invocation.",
    )
    parser.add_argument("--label", default="full", help="Label for runtime-home isolation folders.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="Overall deadline for the parallel shard phase.",
    )
    parser.add_argument(
        "--collection-timeout-seconds",
        type=float,
        default=300.0,
        help="Deadline for canonical pytest collection.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="Parent directory for complete collection, assignment, shard, and runtime logs.",
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=200,
        help="Lines printed from a failed log; the complete log remains under the run root.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_process_group_owner()
    repo_root = Path(__file__).resolve().parent.parent
    return run_collected_shards(
        repo_root=repo_root,
        paths=tuple(str(item) for item in args.paths),
        workers=max(1, int(args.workers)),
        pytest_args=tuple(str(item) for item in args.pytest_arg),
        shard_label=str(args.label or "full"),
        dry_run=bool(args.dry_run),
        timeout_seconds=max(0.01, float(args.timeout_seconds)),
        collection_timeout_seconds=max(0.01, float(args.collection_timeout_seconds)),
        tail_lines=max(0, int(args.tail_lines)),
        artifact_root=args.artifact_root,
        share_process_group=True,
    )



def _skip_entries(raw: object) -> list[dict[str, str]]:
    """The manifest's skip records, or [] when the shape is not the one we write."""

    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict) and entry.get("nodeid")]


def skip_accounting(path: Path) -> dict[str, int]:
    """Read one execution manifest's non-pass accounting for receipt surfaces.

    Recovered 2026-08-29 from `audit/skip-accounting`. PASS != SKIP != XFAIL != DID-NOT-RUN: a
    receipt that reports only "green" over a shard that skipped everything is a false green, and
    counting them is how a surface can say so. Returns zeros when the manifest carries no skip
    records; callers surface the counts, they do not gate on them here.
    """

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return {
            "skipped": len(_skip_entries(payload["skipped"])),
            "xfailed": len(payload["xfailed"]),
            "xpassed": len(payload["xpassed"]),
        }
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return {"skipped": 0, "xfailed": 0, "xpassed": 0}


def _execution_manifest_error(path: Path, *, expected_nodeids: Sequence[str]) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != "vool.pytest-execution.v1":
            raise ValueError("unexpected schema")
        exitstatus = int(payload["exitstatus"])
        collected = tuple(str(item) for item in payload["collected_nodeids"])
        started = tuple(str(item) for item in payload["started_nodeids"])
        summary = _validated_execution_summary(payload["summary"])
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return f"missing or malformed: {exc}"
    if exitstatus != 0:
        return f"recorded pytest exit {exitstatus}"
    expected = Counter(str(item) for item in expected_nodeids)
    if Counter(collected) != expected:
        return "collected nodes differ from canonical collection"
    if Counter(started) != expected:
        return "started nodes differ from canonical collection"
    # PASS != SKIP != XFAIL != DID-NOT-RUN (recovered 2026-08-29 from audit/skip-accounting).
    # pytest exits zero for an all-skipped shard, so without this a shard that proved nothing is
    # indistinguishable from one that passed everything.
    skipped = _skip_entries(payload.get("skipped"))
    xfailed = tuple(str(item) for item in (payload.get("xfailed") or ()))
    xpassed = tuple(str(item) for item in (payload.get("xpassed") or ()))
    if len({entry["nodeid"] for entry in skipped}) != len(skipped):
        return "skipped node inventory contains duplicates"
    unaccounted = {entry["nodeid"] for entry in skipped} | set(xfailed) | set(xpassed)
    if not unaccounted <= expected.keys():
        return "skipped/xfail records reference nodes outside canonical collection"
    if expected and unaccounted >= expected.keys():
        return (
            f"every collected node was skipped or xfail "
            f"(skipped={len(skipped)}, xfailed={len(xfailed)}, xpassed={len(xpassed)}) -- "
            "the shard proved nothing and cannot be counted as green"
        )
    if expected and int(summary["executed_total"]) == 0:
        return (
            f"green with zero executed tests: {int(summary['collected_total'])} collected, "
            f"{int(summary['skipped'])} skipped, none reached a verdict"
        )
    targets = {str(nodeid).split("::", 1)[0] for nodeid in expected_nodeids}
    for target in sorted(targets & set(REQUIRED_EXECUTED_FILES)):
        file_summary = summary["files"].get(target) or {}
        executed = int(file_summary.get("executed_total", 0))
        skipped = int(file_summary.get("skipped", 0))
        if executed == 0:
            return (
                f"required lane executed zero tests: {target} was collected but all "
                f"{skipped} of its scenarios were skipped; a required surface may not report "
                "green on availability skips"
            )
        if skipped > 0:
            # A required suite whose browser scenarios skipped while incidental unit tests in
            # the same file passed is still green-with-no-browser-proof.
            return (
                f"required lane skipped scenarios: {target} executed {executed} but skipped "
                f"{skipped}; every scenario of a required surface must reach a verdict"
            )
    return ""


def _validated_execution_summary(summary: Any) -> dict[str, Any]:
    """Accept only an internally consistent outcome summary, so the guards below cannot be
    fooled by a manifest whose numbers disagree with its own per-file breakdown."""

    if not isinstance(summary, dict):
        raise ValueError("summary is not an object")
    outcome_keys = (
        "passed",
        "failed",
        "skipped",
        "xfailed",
        "xpassed",
        "errors",
    )
    totals = {key: int(summary[key]) for key in (*outcome_keys, "executed_total", "collected_total", "started_total")}
    files_raw = summary["files"]
    if not isinstance(files_raw, dict):
        raise ValueError("summary files is not an object")
    files: dict[str, dict[str, int]] = {}
    for path, counts in files_raw.items():
        entry = {key: int(counts[key]) for key in (*outcome_keys, "executed_total")}
        if sum(entry[key] for key in outcome_keys if key != "skipped") != entry["executed_total"]:
            raise ValueError(f"per-file executed_total disagrees with outcomes for {path}")
        files[str(path)] = entry
    for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "executed_total"):
        if sum(entry[key] for entry in files.values()) != totals[key]:
            raise ValueError(f"summary {key} disagrees with its per-file breakdown")
    if totals["executed_total"] > totals["collected_total"]:
        raise ValueError("executed_total exceeds collected_total")
    return {**totals, "files": files}


def _print_log_tail(path: Path, *, tail_lines: int) -> None:
    if tail_lines <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"!! could not read log {path}: {exc}", flush=True)
        return
    print("\n".join(lines[-tail_lines:]), flush=True)


def _discover_from_dir(path: Path, *, repo_root: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        entry.relative_to(repo_root.resolve()).as_posix()
        for entry in sorted(path.rglob("test_*.py"))
        if entry.is_file()
    ]


def _target_weight(path: Path) -> int:
    try:
        return max(1, int(path.stat().st_size))
    except Exception:
        return 1


def _sanitized_pytest_environment() -> dict[str, str]:
    env = os.environ.copy()
    for variable in _PYTEST_CONTROL_ENVIRONMENT:
        env.pop(variable, None)
    return env


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
