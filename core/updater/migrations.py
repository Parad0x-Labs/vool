"""Migration authority: transactional, user-data-only, reversible.

Laws:
  * migrations touch ONLY user data (config/state under the data home) — the app
    bundle is never migrated, because user data must live outside the replaced bundle;
  * every step declares its affected paths; they are snapshotted BEFORE the step runs;
  * a failure mid-sequence runs the completed steps' backward hooks in reverse and
    restores the snapshots — leaving user data exactly as it was (transactional);
  * user data whose recorded schema version is NEWER than anything this app's catalog
    understands is refused (`data_too_new`): that is the guard that makes downgrades
    need explicit support instead of silently misreading newer state.

Steps are registered in a code-owned catalog; each hop is (from_version → to_version)
and hops are chained to build the path from the installed version to the target.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.updater.versions import Semver, parse_semver

logger = logging.getLogger("vool.updater.migrations")

SCHEMA_STATE_NAME = "data_schema.json"


class MigrationReason(Enum):
    OK = "ok"
    NO_PATH = "no_migration_path"
    STEP_FAILED = "migration_step_failed"
    RESTORE_FAILED = "migration_restore_failed"
    DATA_TOO_NEW = "data_schema_too_new"


@dataclass(frozen=True)
class MigrationContext:
    user_home: Path
    snapshot_dir: Path
    step_name: str


@dataclass(frozen=True)
class MigrationStep:
    name: str
    from_version: str
    to_version: str
    affected_paths: tuple[str, ...]
    forward: Callable[[MigrationContext], None]
    backward: Callable[[MigrationContext], None] | None = None


@dataclass
class MigrationResult:
    ok: bool
    reason: MigrationReason
    applied: list[str] = field(default_factory=list)
    rolled_back: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def plain_message(self) -> str:
        if self.reason is MigrationReason.OK:
            return "Your settings were brought up to date for the new version."
        if self.reason is MigrationReason.NO_PATH:
            return "This version doesn't know how to update your settings from the previous version."
        if self.reason is MigrationReason.STEP_FAILED:
            return (
                "Bringing your settings up to date failed, so everything was put back exactly "
                "as it was and the previous version keeps running."
            )
        if self.reason is MigrationReason.DATA_TOO_NEW:
            return (
                "Your settings were already updated by a newer version of the app; this version "
                "can't safely use them, so nothing was changed."
            )
        if self.reason is MigrationReason.RESTORE_FAILED:
            return "Settings recovery is incomplete. Keep the update backups and contact support."
        return "Settings update result unknown."  # pragma: no cover


class MigrationCatalog:
    def __init__(self) -> None:
        self._steps: dict[tuple[Semver, Semver], MigrationStep] = {}

    def register(self, step: MigrationStep) -> None:
        self._steps[(parse_semver(step.from_version), parse_semver(step.to_version))] = step

    @property
    def max_supported_version(self) -> Semver:
        if not self._steps:
            return parse_semver("0.0.0")
        return max(to for _, to in self._steps)

    def hop(self, from_version: Semver, to_version: Semver) -> MigrationStep | None:
        return self._steps.get((from_version, to_version))

    def path(self, from_version: Semver, to_version: Semver, *, max_hops: int = 16) -> list[MigrationStep]:
        """Chain hops from → to (Dijkstra-unneeded: versions are totally ordered)."""
        chain: list[MigrationStep] = []
        current = from_version
        for _ in range(max_hops):
            if current == to_version:
                return chain
            candidates = [(to, step) for (frm, to), step in self._steps.items() if frm == current]
            if not candidates:
                return []
            # the smallest hop that still moves toward the target
            to, step = min(candidates, key=lambda pair: pair[0])
            chain.append(step)
            current = to
        return []


def _snapshot_key(relative_path: str) -> Path:
    """Where one declared relative path lives inside a snapshot: the whole relative path, never
    just its basename (two declared paths such as ``alpha/state.json`` and ``beta/state.json``
    would otherwise share one snapshot entry, and a restore would write one file's bytes into
    both). Absolute paths and ``..`` would leave the snapshot folder, so they are refused."""
    candidate = Path(str(relative_path))
    if not candidate.parts or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe snapshot path: {relative_path!r}")
    return Path(*candidate.parts)


def _snapshot_keys(paths: list[Path], relative_paths) -> list[str]:
    keys = [path.name for path in paths] if relative_paths is None else [str(rel) for rel in relative_paths]
    if len(keys) != len(paths):
        raise ValueError("relative_paths must name every path, in order")
    return keys


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def snapshot_tree(sources: list[Path], snapshot_dir: Path,
                  relative_paths: list[str] | tuple[str, ...] | None = None) -> dict[str, Path]:
    """Copy each existing source into snapshot_dir, returning {key: snapshot_path}.

    The key is the declared relative path when ``relative_paths`` is given (every production
    caller knows it), else the basename, which is what snapshots taken before this change used."""
    saved: dict[str, Path] = {}
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source, key in zip(sources, _snapshot_keys(sources, relative_paths), strict=True):
        if not source.exists():
            continue
        target = snapshot_dir / _snapshot_key(key)
        _remove(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
        saved[key] = target
    return saved


def restore_tree(snapshot_dir: Path, destinations: list[Path],
                 relative_paths: list[str] | tuple[str, ...] | None = None) -> None:
    """Restore snapshots over their destinations (removing what arrived after).

    The snapshot is copied, never moved, so a retry after an interrupted restore still has it.
    The copy is staged beside the destination and swapped in with renames: an interruption
    mid-copy leaves the live destination as it was instead of deleted or half written.
    A destination with no snapshot entry did not exist when the snapshot was taken, so it is
    removed."""
    for destination, key in zip(destinations, _snapshot_keys(destinations, relative_paths), strict=True):
        restored = snapshot_dir / _snapshot_key(key)
        if not restored.exists():
            _remove(destination)
            continue
        staged = destination.parent / f".vool-restore-{destination.name}"
        discard = destination.parent / f".vool-discard-{destination.name}"
        _remove(staged)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(restored, staged) if restored.is_dir() else shutil.copy2(restored, staged)
        if destination.exists() or destination.is_symlink():
            _remove(discard)
            os.rename(destination, discard)
        os.rename(staged, destination)
        _remove(discard)


def read_recorded_schema(user_home: Path) -> str:
    try:
        data = json.loads((Path(user_home) / SCHEMA_STATE_NAME).read_text(encoding="utf-8"))
        return str(data.get("schema_version") or "")
    except (OSError, ValueError):
        return ""


def write_recorded_schema(user_home: Path, version: str) -> None:
    home = Path(user_home)
    home.mkdir(parents=True, exist_ok=True)
    (home / SCHEMA_STATE_NAME).write_text(
        json.dumps({"schema_version": str(version)}, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_migrations(
    catalog: MigrationCatalog,
    *,
    from_version: str,
    to_version: str,
    user_home: Path,
    snapshot_root: Path,
    recorded_schema: str | None = None,
) -> MigrationResult:
    home = Path(user_home)
    source = parse_semver(from_version)
    target = parse_semver(to_version)

    recorded = recorded_schema if recorded_schema is not None else read_recorded_schema(home)
    if recorded:
        try:
            if parse_semver(recorded) > catalog.max_supported_version:
                return MigrationResult(False, MigrationReason.DATA_TOO_NEW, detail=recorded)
        except ValueError:
            pass  # an unparsable recorded schema is treated as absent

    if source == target:
        return MigrationResult(True, MigrationReason.OK)

    steps = catalog.path(source, target)
    if not steps:
        return MigrationResult(False, MigrationReason.NO_PATH, detail=f"{from_version} → {to_version}")

    applied: list[tuple[MigrationStep, Path, list[Path]]] = []  # (step, snapshot, affected)
    for index, step in enumerate(steps):
        step_snapshot = snapshot_root / f"step-{index:02d}-{step.name}"
        affected = [home / rel for rel in step.affected_paths]
        snapshot_complete = False
        try:
            snapshot_tree(affected, step_snapshot, relative_paths=step.affected_paths)
            snapshot_complete = True
            step.forward(MigrationContext(user_home=home, snapshot_dir=step_snapshot, step_name=step.name))
        except Exception as exc:
            logger.warning("migration step %s failed: %s", step.name, exc)
            rolled_back: list[str] = []
            restore_errors: list[str] = []
            for done_step, done_snapshot, _done_affected in reversed(applied):
                try:
                    if done_step.backward is not None:
                        done_step.backward(
                            MigrationContext(
                                user_home=home, snapshot_dir=done_snapshot, step_name=done_step.name
                            )
                        )
                    rolled_back.append(done_step.name)
                except Exception as rexc:  # rollback failure is logged, never hidden
                    logger.error("migration rollback of %s ALSO failed: %s", done_step.name, rexc)
            # Snapshot restores run failed-step-first, then completed steps in reverse:
            # each earlier snapshot overwrites the later one, so the LAST restore is
            # step 0's — the user data ends at its exact pre-migration state.
            restore_order = ([(step_snapshot, affected, step.affected_paths)] if snapshot_complete else []) + [
                (snap, aff, done.affected_paths) for done, snap, aff in reversed(applied)
            ]
            for snap, aff, rels in restore_order:
                try:
                    restore_tree(snap, aff, relative_paths=rels)
                except Exception as rexc:
                    logger.error("migration snapshot restore ALSO failed: %s", rexc)
                    restore_errors.append(str(rexc))
            return MigrationResult(
                False,
                MigrationReason.RESTORE_FAILED if restore_errors else MigrationReason.STEP_FAILED,
                applied=[s.name for s, _, _ in applied],
                rolled_back=rolled_back,
                detail=f"{step.name}: {exc}" + (f"; restore failed: {'; '.join(restore_errors)}" if restore_errors else ""),
            )
        applied.append((step, step_snapshot, affected))

    write_recorded_schema(home, to_version)
    return MigrationResult(True, MigrationReason.OK, applied=[s.name for s, _, _ in applied])


__all__ = [
    "MigrationCatalog",
    "MigrationContext",
    "MigrationReason",
    "MigrationResult",
    "MigrationStep",
    "read_recorded_schema",
    "restore_tree",
    "run_migrations",
    "snapshot_tree",
    "write_recorded_schema",
]
