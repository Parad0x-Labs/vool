"""Complete, bounded package identity and private execution copies.

There are no filename exclusions: executable code, vendored dependencies, locks,
references and empty directories all belong to the approved distribution. Runtime
state belongs in scratch. Links and unstable reads cannot produce an attestation.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DIGEST_SCHEMA = "vool.plugin_package.v2"
APPROVAL_SCHEMA = "vool.plugin_approval.v1"
MAX_FILES = 4096
MAX_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024


class PackageError(RuntimeError):
    """The complete package cannot be identified safely."""


@dataclass(frozen=True)
class PackageIdentity:
    digest: str
    manifest: dict
    entries: tuple[dict, ...]


def approval_digest(package_digest: str, dependencies: dict[str, str]) -> str:
    """Bind a distribution to its frozen recursive dependency approvals."""
    encoded = json.dumps({"schema": APPROVAL_SCHEMA, "package": package_digest,
                          "dependencies": dependencies}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _stamp(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def capture_package(root: Path, *, destination: Path | None = None) -> PackageIdentity:
    """Hash bytes read through no-follow descriptors; optionally copy those same bytes.

    Directory and file identities are checked after reading. A concurrent change
    refuses capture rather than blessing a mixture. The digest covers executable
    bits but not writable bits, since the execution copy is always read-only.
    """
    base = Path(root)
    if base.is_symlink():
        raise PackageError("package root must not be a link")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise PackageError("safe package capture is unavailable on this host")
    entries: list[dict] = []
    manifests: list[bytes] = []
    total = 0
    flags = os.O_RDONLY | os.O_NOFOLLOW

    def walk(directory: int, relative: str, target: Path | None) -> None:
        nonlocal total
        before = os.fstat(directory)
        names = sorted(os.listdir(directory))
        if len({name.casefold() for name in names}) != len(names):
            raise PackageError("case-colliding package paths")
        for name in names:
            path = f"{relative}/{name}" if relative else name
            if "\\" in name or len(path) > 1024:
                raise PackageError("ambiguous or oversized package path")
            if len(entries) >= MAX_FILES:
                raise PackageError("package file limit exceeded")
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                raise PackageError("privileged package mode refused")
            output = target / name if target is not None else None
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=directory)
                try:
                    if _stamp(info) != _stamp(os.fstat(child)):
                        raise PackageError("package directory changed during capture")
                    entries.append({"path": path, "type": "directory"})
                    if output is not None:
                        output.mkdir(mode=0o700)
                    walk(child, path, output)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_BYTES:
                    raise PackageError("package byte limit exceeded")
                source = os.open(name, flags, dir_fd=directory)
                digest = hashlib.sha256()
                size = 0
                content: list[bytes] = []
                writer = None
                try:
                    if _stamp(info) != _stamp(os.fstat(source)):
                        raise PackageError("package file changed during capture")
                    if output is not None:
                        writer = output.open("xb")
                    while chunk := os.read(source, 1024 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES or total + size > MAX_BYTES:
                            raise PackageError("package byte limit exceeded")
                        digest.update(chunk)
                        if writer is not None:
                            writer.write(chunk)
                        if path == ".codex-plugin/plugin.json":
                            if size > 1024 * 1024:
                                raise PackageError("plugin manifest is oversized")
                            content.append(chunk)
                    after = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if _stamp(info) != _stamp(os.fstat(source)) or _stamp(info) != _stamp(after):
                        raise PackageError("package file changed during capture")
                finally:
                    if writer is not None:
                        writer.close()
                    os.close(source)
                total += size
                executable = stat.S_IMODE(info.st_mode) & 0o111
                entries.append({"path": path, "type": "file", "bytes": size,
                                "executable": executable, "sha256": digest.hexdigest()})
                if output is not None:
                    output.chmod(0o444 | executable)
                if path == ".codex-plugin/plugin.json":
                    manifests.append(b"".join(content))
            else:
                raise PackageError("package links and special files are refused")
        if _stamp(before) != _stamp(os.fstat(directory)):
            raise PackageError("package directory changed during capture")
        if target is not None:
            target.chmod(0o555)

    descriptor = None
    try:
        if destination is not None:
            destination.mkdir(mode=0o700)
        descriptor = os.open(base, flags | os.O_DIRECTORY)
        before = os.fstat(descriptor)
        walk(descriptor, "", destination)
        if _stamp(before) != _stamp(os.stat(base, follow_symlinks=False)):
            raise PackageError("package root changed during capture")
        if len(manifests) != 1:
            raise PackageError("package manifest is missing")
        manifest = json.loads(manifests[0])
        if not isinstance(manifest, dict):
            raise PackageError("package manifest must be an object")
        encoded = json.dumps({"schema": DIGEST_SCHEMA, "entries": entries},
                             sort_keys=True, separators=(",", ":")).encode()
        return PackageIdentity(hashlib.sha256(encoded).hexdigest(), manifest, tuple(entries))
    except (OSError, ValueError, RecursionError) as exc:
        raise PackageError(f"package capture failed ({type(exc).__name__})") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextlib.contextmanager
def execution_directory(parent: Path) -> Iterator[Path]:
    """Own one private snapshot directory and remove only that directory on return."""
    parent.mkdir(parents=True, exist_ok=True)
    owned = Path(tempfile.mkdtemp(prefix="invocation-", dir=parent))
    try:
        yield owned
    finally:
        # Captured directories are sealed. Restore only our directories' write
        # bits to remove them, without following links or touching source trees.
        for current, directories, _ in os.walk(owned, followlinks=False):
            Path(current).chmod(0o700)
            for name in directories:
                path = Path(current) / name
                if not path.is_symlink():
                    path.chmod(0o700)
        shutil.rmtree(owned)
