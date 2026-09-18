"""What is on disk at one path, observed without following symlinks, with the bytes captured
into the verified CAS when they fit the capture limit.

``observe_path`` is the read-only half (used for conflict checks at rollback and for
post-crash recovery); ``snapshot_path`` is the same observation plus the blob put. The hash is
always computed for a regular file, even past the capture limit -- the limit bounds what can be
RESTORED, never what can be VERIFIED -- and the record says which of the two it is.
"""
from __future__ import annotations

import hashlib
import os
import stat as stat_module
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from storage.blackbox.blobs import BlobStore

_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class PathObservation:
    exists: bool
    kind: str  # missing | file | dir | symlink | other
    size: int
    sha256: str
    mode: int | None
    nlink: int
    blob: str | None
    bytes_captured: bool
    capture_limit_exceeded: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> PathObservation:
        data = dict(payload or {})
        return cls(
            exists=bool(data.get("exists", False)),
            kind=str(data.get("kind") or "missing"),
            size=int(data.get("size") or 0),
            sha256=str(data.get("sha256") or ""),
            mode=(int(data["mode"]) if isinstance(data.get("mode"), int) else None),
            nlink=int(data.get("nlink") or 0),
            blob=(str(data["blob"]) if data.get("blob") else None),
            bytes_captured=bool(data.get("bytes_captured", False)),
            capture_limit_exceeded=bool(data.get("capture_limit_exceeded", False)),
        )

    def same_content_as(self, other: PathObservation) -> bool:
        if self.exists != other.exists:
            return False
        if not self.exists:
            return True
        if self.kind != other.kind:
            return False
        if self.kind == "file":
            return self.sha256 == other.sha256
        return True


MISSING = PathObservation(False, "missing", 0, "", None, 0, None, True, False)


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def observe_path(target: Path) -> PathObservation:
    """Observe without following a leaf symlink. Unreadable files are reported, not guessed."""
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return MISSING
    except OSError:
        return PathObservation(True, "other", 0, "", None, 0, None, False, False)
    mode = info.st_mode & 0o7777
    if stat_module.S_ISLNK(info.st_mode):
        return PathObservation(True, "symlink", 0, "", None, int(info.st_nlink), None, False, False)
    if stat_module.S_ISDIR(info.st_mode):
        return PathObservation(True, "dir", 0, "", mode, int(info.st_nlink), None, True, False)
    if not stat_module.S_ISREG(info.st_mode):
        return PathObservation(True, "other", 0, "", mode, int(info.st_nlink), None, False, False)
    try:
        digest, size = _hash_file(target)
    except OSError:
        return PathObservation(True, "file", int(info.st_size), "", mode, int(info.st_nlink), None, False, False)
    return PathObservation(True, "file", size, digest, mode, int(info.st_nlink), None, True, False)


def snapshot_path(target: Path, *, blobs: BlobStore, max_bytes: int) -> PathObservation:
    """Observe AND capture: a regular file within the limit has its exact bytes stored in the CAS."""
    observed = observe_path(target)
    if observed.kind != "file" or not observed.bytes_captured:
        return observed
    if observed.size > max_bytes:
        return PathObservation(
            True, "file", observed.size, observed.sha256, observed.mode, observed.nlink, None, False, True
        )
    data = target.read_bytes()
    ref = blobs.put(data)
    address = ref.ref or ref.sha256  # opaque id under the encrypted CAS; the digest under v1
    if ref.sha256 != observed.sha256:
        # The file changed between the hash pass and the read: report what was CAPTURED.
        return PathObservation(True, "file", ref.size, ref.sha256, observed.mode, observed.nlink, address, True, False)
    return PathObservation(True, "file", observed.size, observed.sha256, observed.mode, observed.nlink, address, True, False)


def canonical_relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def relative_path_escapes(relative: str) -> bool:
    """Lexical containment for a path READ BACK from the journal: absolute, empty, or any ``..``
    segment is refused before the filesystem is consulted at all."""
    clean = str(relative or "")
    if not clean or clean.startswith("/") or clean.startswith("\\"):
        return True
    parts = [part for part in clean.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts:
        return True
    return any(part == ".." for part in parts)


def parent_chain_inside_root(root: Path, target: Path) -> bool:
    """The parent directory chain, fully resolved, still lands inside the resolved root.

    Catches a parent directory swapped for a symlink pointing outside the workspace -- an
    ordinary filename under it resolves outside just the same.
    """
    resolved_root = root.resolve()
    try:
        resolved_parent = target.parent.resolve()
    except OSError:
        return False
    return resolved_parent == resolved_root or resolved_root in resolved_parent.parents


def missing_ancestors(root: Path, target: Path) -> list[str]:
    """Ancestors of ``target`` under ``root`` that do not exist yet -- the directories a create
    will bring into being as a side effect, deepest first."""
    out: list[str] = []
    current = target.parent
    root_resolved = root.resolve()
    while True:
        try:
            current.relative_to(root_resolved)
        except ValueError:
            break
        if current == root_resolved or current.exists():
            break
        out.append(current.relative_to(root_resolved).as_posix())
        current = current.parent
    return out


__all__ = [
    "MISSING",
    "PathObservation",
    "canonical_relative",
    "missing_ancestors",
    "observe_path",
    "parent_chain_inside_root",
    "relative_path_escapes",
    "snapshot_path",
]
