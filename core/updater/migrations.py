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


def snapshot_tree(sources: list[Path], snapshot_dir: Path) -> dict[str, Path]:
    """Copy each existing source into snapshot_dir, returning {name: snapshot_path}."""
    saved: dict[str, Path] = {}
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if not source.exists():
            continue
        target = snapshot_dir / source.name
        if target.exists():
            shutil.rmtree(target) if target.is_dir() else target.unlink()
        shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
        saved[source.name] = target
    return saved


def restore_tree(snapshot_dir: Path, destinations: list[Path]) -> None:
    """Restore snapshots over their destinations (removing what arrived after)."""
    for destination in destinations:
        restored = snapshot_dir / destination.name
        if destination.exists():
            shutil.rmtree(destination) if destination.is_dir() else destination.unlink()
        if restored.exists():
            shutil.move(str(restored), str(destination))


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
        try:
            snapshot_tree(affected, step_snapshot)
            step.forward(MigrationContext(user_home=home, snapshot_dir=step_snapshot, step_name=step.name))
        except Exception as exc:
            logger.warning("migration step %s failed: %s", step.name, exc)
            rolled_back: list[str] = []
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
            restore_order = [(step_snapshot, affected)] + [(snap, aff) for _, snap, aff in reversed(applied)]
            for snap, aff in restore_order:
                try:
                    restore_tree(snap, aff)
                except Exception as rexc:
                    logger.error("migration snapshot restore ALSO failed: %s", rexc)
            return MigrationResult(
                False,
                MigrationReason.STEP_FAILED,
                applied=[s.name for s, _, _ in applied],
                rolled_back=rolled_back,
                detail=f"{step.name}: {exc}",
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
