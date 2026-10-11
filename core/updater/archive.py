"""Bounded ZIP payloads, including the relative links in signed macOS bundles."""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_ENTRIES = 100_000
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_MEMBER_BYTES = 2 * 1024**3
MAX_LINK_BYTES = 4096


def extract_update_zip(archive_path: Path, destination: Path) -> Path:
    """Validate the entire namespace before writing; never follow an archive link.

    Extraction requires an empty, real directory. Links are created last, and may
    only resolve within that directory. Device files, duplicate/colliding names,
    privileged modes, traversal and children of symlinks are refused.
    """
    destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise ValueError("update extraction destination must be empty and not a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        total_bytes = sum(m.file_size for m in members)
        if len(members) > MAX_ENTRIES or total_bytes > MAX_TOTAL_BYTES:
            raise ValueError("update archive exceeds extraction limits")
        if shutil.disk_usage(root).free < total_bytes + len(members) * 4096 + 64 * 1024**2:
            raise OSError("not enough free space for the expanded update payload")
        names: dict[str, tuple[zipfile.ZipInfo, Path, int]] = {}
        folded: set[str] = set()
        links: dict[str, str] = {}
        for info in members:
            raw = info.filename.rstrip("/")
            path = PurePosixPath(raw)
            mode = info.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (not raw or path.is_absolute() or "\\" in raw or "\x00" in info.orig_filename
                    or any(p in ("", ".", "..") for p in raw.split("/"))
                    or ":" in raw or raw.casefold() in folded):
                raise ValueError(f"unsafe or duplicate archive path: {raw}")
            if info.file_size > MAX_MEMBER_BYTES or mode & 0o6000:
                raise ValueError(f"unsafe archive size or mode: {raw}")
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                raise ValueError(f"unsupported archive entry: {raw}")
            folded.add(raw.casefold())
            target = root.joinpath(*path.parts)
            names[raw] = (info, target, mode)
            if kind == stat.S_IFLNK:
                if info.file_size > MAX_LINK_BYTES:
                    raise ValueError(f"oversized symlink: {raw}")
                link = archive.read(info).decode("utf-8", errors="strict")
                if not link or PurePosixPath(link).is_absolute() or "\\" in link or "\x00" in link:
                    raise ValueError(f"unsafe symlink: {raw}")
                (target.parent / link).resolve().relative_to(root)
                links[raw] = link
        for raw, (_, _, _) in names.items():
            for parent in PurePosixPath(raw).parents:
                if str(parent) in links:
                    raise ValueError(f"archive entry beneath symlink: {raw}")
                entry = names.get(str(parent))
                if entry is not None and not entry[0].is_dir():
                    raise ValueError(f"archive entry beneath non-directory: {raw}")
        directories = []
        for raw, (info, target, mode) in names.items():
            if raw in links:
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                directories.append((target, mode))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                os.chmod(target, (mode & 0o777) or 0o644)
        for raw, link in links.items():
            target = names[raw][1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
        for raw in links:
            # Includes chained links and loops; Path.resolve refuses loops.
            names[raw][1].resolve().relative_to(root)
        for target, mode in reversed(directories):
            os.chmod(target, (mode & 0o777) or 0o755)
    entries = list(root.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink() else root
