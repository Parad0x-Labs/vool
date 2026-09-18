from __future__ import annotations

import contextlib
import fnmatch
import os
import sys
from pathlib import Path
from typing import Any


def resolve_target_path(
    raw_path: str | None,
    *,
    os_name: str,
    env: Any,
    home_dir_fn: Any,
) -> Path:
    if raw_path:
        return Path(os.path.expandvars(raw_path)).expanduser()
    if os_name == "nt":
        drive = env.get("SystemDrive", "C:")
        return Path(f"{drive}\\")
    return home_dir_fn()


def inspect_storage(
    target: Path,
    *,
    disk_usage_fn: Any,
    path_size_fn: Any,
    monotonic_fn: Any,
) -> dict[str, Any]:
    try:
        disk_total, _used, disk_free = disk_usage_fn(target)
    except Exception:
        disk_total, disk_free = 0, 0

    deadline = monotonic_fn() + 2.0
    top_entries: list[dict[str, Any]] = []
    try:
        children = list(target.iterdir())
    except Exception:
        children = []

    for child in children[:32]:
        size_info = path_size_fn(child, deadline=deadline)
        top_entries.append(
            {
                "name": child.name or str(child),
                "path": str(child),
                "bytes": int(size_info["bytes"]),
                "approximate": bool(size_info["approximate"]),
            }
        )
        if monotonic_fn() >= deadline:
            break

    top_entries.sort(key=lambda row: int(row["bytes"]), reverse=True)
    return {
        "disk_total_bytes": int(disk_total),
        "disk_free_bytes": int(disk_free),
        "top_entries": top_entries[:8],
    }


def path_size(
    path: Path,
    *,
    deadline: float,
    max_entries: int = 6000,
    walk_fn: Any,
    monotonic_fn: Any,
) -> dict[str, Any]:
    approximate = False
    total = 0
    scanned = 0
    try:
        if path.is_symlink():
            return {"bytes": 0, "approximate": False}
        if path.is_file():
            return {"bytes": int(path.stat().st_size), "approximate": False}
    except Exception:
        return {"bytes": 0, "approximate": True}

    for root, dirs, files in walk_fn(path, topdown=True):
        if monotonic_fn() >= deadline or scanned >= max_entries:
            approximate = True
            break
        dirs[:] = [name for name in dirs if not name.startswith(".vool_local")]
        for name in files:
            file_path = Path(root) / name
            try:
                if file_path.is_symlink():
                    continue
                total += int(file_path.stat().st_size)
            except Exception:
                approximate = True
            scanned += 1
            if monotonic_fn() >= deadline or scanned >= max_entries:
                approximate = True
                break
        if approximate:
            break
    return {"bytes": total, "approximate": approximate}


def parse_move_request(
    text: str,
    *,
    fallback_source: str | None = None,
    fallback_destination: str | None = None,
    extract_quoted_values_fn: Any,
    data_path_fn: Any,
    expandvars_fn: Any,
) -> dict[str, str] | None:
    lowered = str(text or "").lower()
    source = str(fallback_source or "").strip()
    destination = str(fallback_destination or "").strip()
    quoted = extract_quoted_values_fn(text)
    if quoted:
        source = source or quoted[0]
        if len(quoted) >= 2:
            destination = destination or quoted[1]
    if not source:
        return None
    if not destination:
        if "archive" not in lowered:
            return None
        destination = str(data_path_fn("archive_outbox"))
    return {
        "source_path": str(Path(expandvars_fn(source)).expanduser()),
        "destination_dir": str(Path(expandvars_fn(destination)).expanduser()),
    }


def candidate_cleanup_roots(
    target_path: str | None,
    *,
    env: Any,
    gettempdir_fn: Any,
    is_temp_cleanup_path_fn: Any,
    expandvars_fn: Any,
) -> list[Path]:
    roots: list[Path] = []
    for env_name in ("TMPDIR", "TMP", "TEMP"):
        value = str(env.get(env_name) or "").strip()
        if value:
            roots.append(Path(value).expanduser())
    roots.append(Path(gettempdir_fn()).expanduser())
    if target_path:
        target = Path(expandvars_fn(target_path)).expanduser()
        if is_temp_cleanup_path_fn(target):
            roots.insert(0, target)

    deduped: list[tuple[Path, Path]] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.resolve() if root.exists() else root
        key = str(resolved)
        if key in seen:
            continue
        if not root.exists() or not root.is_dir():
            continue
        if not is_temp_cleanup_path_fn(root):
            continue
        if any(existing_resolved.is_relative_to(resolved) for _existing_root, existing_resolved in deduped):
            continue
        deduped = [
            (existing_root, existing_resolved)
            for existing_root, existing_resolved in deduped
            if not resolved.is_relative_to(existing_resolved)
        ]
        seen.add(key)
        deduped.append((root, resolved))
    return [root for root, _resolved in deduped[:6]]


def is_temp_cleanup_path(
    path: Path,
    *,
    path_is_denied_fn: Any,
    tempish_names: set[str],
    gettempdir_fn: Any,
    home_dir_fn: Any,
    is_relative_to_fn: Any,
) -> bool:
    if not path:
        return False
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    if path_is_denied_fn(resolved):
        return False

    name = resolved.name.lower()
    if name in tempish_names:
        return True

    home = home_dir_fn().resolve()
    try:
        temp_root = Path(gettempdir_fn()).resolve()
    except Exception:
        temp_root = resolved

    return is_relative_to_fn(resolved, temp_root) or (is_relative_to_fn(resolved, home) and name in tempish_names)


def operator_safe_path(
    path: Path,
    *,
    path_is_denied_fn: Any,
    gettempdir_fn: Any,
    data_path_fn: Any,
    is_relative_to_fn: Any,
    home_dir_fn: Any,
) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path.expanduser()
    if path_is_denied_fn(resolved):
        return False
    allowed_roots = [home_dir_fn().resolve()]
    with contextlib.suppress(Exception):
        allowed_roots.append(Path(gettempdir_fn()).resolve())
    with contextlib.suppress(Exception):
        allowed_roots.append(data_path_fn().parent.resolve())
    return any(is_relative_to_fn(resolved, root) or resolved == root for root in allowed_roots)


def validate_move_scope(
    source: Path,
    destination_dir: Path,
    *,
    operator_safe_path_fn: Any,
    resolved_move_target_fn: Any,
    is_relative_to_fn: Any,
) -> str | None:
    if not source.exists():
        return f"I can't move this path because it does not exist: {source}"
    if not operator_safe_path_fn(source):
        return f"I won't move protected or out-of-scope paths: {source}"
    destination_probe = destination_dir if destination_dir.exists() else destination_dir.parent
    if not operator_safe_path_fn(destination_probe):
        return f"I won't move content into a protected or out-of-scope destination: {destination_dir}"
    final_path = resolved_move_target_fn(source, destination_dir)
    if final_path == source:
        return "The requested source and destination resolve to the same path."
    if is_relative_to_fn(final_path, source):
        return "I won't move a folder into itself."
    return None


def resolved_move_target(source: Path, destination_dir: Path) -> Path:
    return destination_dir / source.name


def _fs_fold(text: str) -> str:
    # Case-fold ONLY on case-insensitive filesystems (macOS APFS default, Windows). On Linux
    # (case-sensitive ext4/xfs) distinct-case paths are different files, so folding would make a
    # deny entry over-match a genuinely different path.
    normalized = text.replace("\\", "/")
    return normalized.lower() if sys.platform in ("darwin", "win32") else normalized


def path_is_denied(path: Path, *, policy_get: Any) -> bool:
    normalized = _fs_fold(str(path))
    for pattern in policy_get("filesystem.deny_paths", []) or []:
        probe = _fs_fold(str(pattern or ""))
        if not probe:
            continue
        if "*" in probe:
            if fnmatch.fnmatch(normalized, probe):
                return True
            continue
        if normalized == probe or normalized.startswith(probe.rstrip("/") + "/"):
            return True
    return False


def delete_children(root: Path, *, rmtree_fn: Any) -> dict[str, Any]:
    deleted_files = 0
    deleted_dirs = 0
    errors: list[str] = []
    for child in list(root.iterdir()):
        try:
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
                deleted_files += 1
            elif child.is_dir():
                rmtree_fn(child)
                deleted_dirs += 1
        except Exception as exc:
            errors.append(f"{child}: {exc}")
    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "errors": errors,
    }


def fmt_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{int(value)} B"


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False
