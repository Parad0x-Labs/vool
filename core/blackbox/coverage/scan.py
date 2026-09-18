"""Bounded workspace scans: what the disk holds before and after a tool whose targets are not
statically known (a shell command, a plugin, an MCP call, a coding lane).

The scan walks the ONE workspace root -- never the machine -- under explicit bounds:

- ``max_files``: past this, the scan is DEGRADED (hash-only, no byte capture) and says so.
- ``max_total_capture_bytes``: the byte-capture budget for preimages. Past it, files are hashed
  but not captured, and effects touching them cannot claim rollback.
- Git metadata (``.git``) is walked for DETECTION but its internals are never byte-captured into
  the CAS -- a preimage of a pack file is snapshot weight with no rollback value, and the drift
  is flagged high-risk instead.

Every observation is a v1 ``PathObservation`` (hash, size, mode, nlink, blob) plus ``mtime_ns``,
so drift can be restored to the exact bytes AND mode AND mtime. File bytes live ONLY in the
verified CAS; the journal carries hashes and metadata, never content.
"""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.blackbox.snapshot import MISSING, observe_path
from core.blackbox.store import BlackboxStore

DEFAULT_MAX_FILES = 4000
DEFAULT_MAX_TOTAL_CAPTURE_BYTES = 64 * 1024 * 1024

_SKIP_DIRS = frozenset({"__pycache__", ".pytest_cache", "node_modules"})


@dataclass(frozen=True)
class ScanBounds:
    max_files: int = DEFAULT_MAX_FILES
    max_total_capture_bytes: int = DEFAULT_MAX_TOTAL_CAPTURE_BYTES


@dataclass
class ScanResult:
    files: dict[str, dict[str, Any]]  # relpath -> observation dict (+mtime_ns, capture_skipped)
    manifest_sha256: str
    degraded: bool
    files_scanned: int
    bytes_captured: int
    capture_skipped: int

    def manifest(self) -> dict[str, str]:
        return {rel: str(item.get("sha256") or "") for rel, item in self.files.items()}


def _store_dir_roots(store: BlackboxStore) -> list[Path]:
    with contextlib.suppress(OSError):
        root = store.root.resolve()
        return [root] if str(root) != "/" else []
    return []


def scan_workspace(root: Path, *, store: BlackboxStore, bounds: ScanBounds | None = None) -> ScanResult:
    """Observe every regular file under ``root`` (bounded), capturing preimage blobs into the CAS."""
    bounds = bounds or ScanBounds()
    root = Path(root).resolve()
    store_dirs = {str(item) for item in _store_dir_roots(store)}
    files: dict[str, dict[str, Any]] = {}
    bytes_captured = 0
    capture_skipped = 0
    degraded = False
    scanned = 0

    def _capture(path: Path, rel: str) -> dict[str, Any]:
        nonlocal bytes_captured, capture_skipped
        try:
            stat = path.lstat()
            mtime_ns = stat.st_mtime_ns
            if stat.st_size > store.max_blob_bytes or bytes_captured + stat.st_size > bounds.max_total_capture_bytes:
                observation = observe_path(path)
                capture_skipped += 1 if observation.exists else 0
                return {**observation.to_dict(), "mtime_ns": mtime_ns, "capture_skipped": True}
            observation = store.snapshot(path)
            if observation.bytes_captured:
                bytes_captured += observation.size
            return {**observation.to_dict(), "mtime_ns": mtime_ns, "capture_skipped": not observation.bytes_captured}
        except OSError:
            return {**MISSING.to_dict(), "mtime_ns": 0, "capture_skipped": True}

    walk_stack = [(root, False)]
    while walk_stack:
        current, inside_git = walk_stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError:
            degraded = True
            continue
        for entry in entries:
            # The Blackbox's own store (journal, HEAD, blobs) is never scan payload: without this
            # exclusion every journaled append and blob put would report itself as drift.
            with contextlib.suppress(OSError):
                if any(str(Path(entry.path).resolve()).startswith(store_dir + os.sep) for store_dir in store_dirs):
                    continue
            if scanned >= bounds.max_files:
                degraded = True
                break
            try:
                if entry.is_symlink():
                    continue  # observed at use; a scan never follows links out of the root
                if entry.is_dir(follow_symlinks=False):
                    name = entry.name
                    child_git = inside_git or name == ".git"
                    if not child_git and name in _SKIP_DIRS:
                        continue
                    scanned += 1
                    walk_stack.append((Path(entry.path), child_git))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                scanned += 1
                rel = str(Path(entry.path).relative_to(root))
                files[rel] = _capture(Path(entry.path), rel)
            except OSError:
                degraded = True
    manifest_body = "\n".join(f"{rel}\t{item.get('sha256')}" for rel, item in sorted(files.items()))
    manifest_sha = hashlib.sha256(manifest_body.encode("utf-8")).hexdigest()
    return ScanResult(
        files=files,
        manifest_sha256=manifest_sha,
        degraded=degraded,
        files_scanned=len(files),
        bytes_captured=bytes_captured,
        capture_skipped=capture_skipped,
    )


@dataclass
class DriftItem:
    path: str  # workspace-relative
    kind: str  # created | removed | changed
    before: dict[str, Any]
    after: dict[str, Any]

    @property
    def rollback_capable(self) -> bool:
        before, after = self.before, self.after
        if bool(before.get("capture_skipped")) or bool(after.get("capture_skipped")):
            return False
        if not before.get("exists", False):
            return True  # a created leaf is removed on rollback; no bytes needed
        return bool(before.get("bytes_captured") and before.get("blob")) and bool(
            after.get("bytes_captured") or not after.get("exists", False)
        )


def diff_scans(before: ScanResult, after: ScanResult, *, hash_key: str = "sha256") -> list[DriftItem]:
    """Every path whose observation changed between the two scans, in stable path order.
    ``hash_key`` selects which row field carries the content binding -- the raw digest for
    in-memory scans, the keyed opaque content id for journal-recovered rows."""
    drift: list[DriftItem] = []
    for rel in sorted(set(before.files) | set(after.files)):
        was = before.files.get(rel)
        now = after.files.get(rel)
        if was is None and now is not None:
            drift.append(DriftItem(rel, "created", {**MISSING.to_dict(), "mtime_ns": 0, "capture_skipped": False}, now))
        elif was is not None and now is None:
            drift.append(DriftItem(rel, "removed", was, {**MISSING.to_dict(), "mtime_ns": 0, "capture_skipped": False}))
        elif str(was.get(hash_key) or "") != str(now.get(hash_key) or ""):
            drift.append(DriftItem(rel, "changed", was, now))
    return drift


# --------------------------------------------------------------------- shell target parsing ----

#: Mutating commands whose FILE operands we can read off the command line.
_FILE_OPERAND_COMMANDS = {
    "rm": "all",
    "touch": "all",
    "mkdir": "all",
    "rmdir": "all",
    "truncate": "all",
    "mv": "last",
    "cp": "last",
    "ln": "last",
    "install": "last",
    "tee": "all",
    "shred": "all",
    "unlink": "all",
}
_GIT_MUTATING_SUBCOMMANDS = frozenset(
    {"commit", "checkout", "reset", "rebase", "merge", "clean", "stash", "restore", "switch", "cherry-pick", "revert", "apply", "am", "rm", "mv", "tag", "init"}
)


def declared_targets_for_command(command: str, *, cwd: Path) -> list[str]:
    """Path strings this command will plausibly mutate, read conservatively from the command text.

    Redirections, ``tee``, the file operands of the common mutators and ``curl|wget -o``. Anything
    the parser cannot name (a script that writes where it pleases) returns nothing, and the scan
    -- not this parser -- is what journals the effect. Never raises: a malformed command line is
    the sandbox's problem, and the empty answer is the honest one.
    """
    targets: list[str] = []
    try:
        tokens = shlex.split(str(command or ""))
    except ValueError:
        return targets
    if not tokens:
        return targets

    def _add(raw: str) -> None:
        text = str(raw or "").strip().strip('"\'')
        if not text:
            return
        candidate = Path(text)
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        resolved = str(candidate)
        if resolved not in targets:
            targets.append(resolved)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in {">", ">>"} and index + 1 < len(tokens):
            _add(tokens[index + 1])
            index += 2
            continue
        for prefix in ("2>", "&>", "1>"):
            if token.startswith(prefix) and len(token) > len(prefix):
                _add(token[len(prefix):])
            elif token == prefix and index + 1 < len(tokens):
                _add(tokens[index + 1])
        if token == "git" and index + 1 < len(tokens) and tokens[index + 1] in _GIT_MUTATING_SUBCOMMANDS:
            _add(str(Path(cwd) / ".git"))
        index += 1

    head = tokens[0]
    basename = Path(head).name
    operands = [t for t in tokens[1:] if not t.startswith("-")]
    if basename == "sudo" and operands:
        head = operands[0]
        basename = Path(head).name
        operands = operands[1:]
    if basename == "dd":
        for token in operands:
            if token.startswith("of="):
                _add(token[3:])
    elif basename in {"curl", "wget"}:
        for flag, value in itertools.pairwise(tokens):
            if flag in {"-o", "--output", "-O"} and not value.startswith("-"):
                _add(value)
            elif flag.startswith("--output="):
                _add(flag[len("--output="):])
    elif basename == "sed" and any(t == "-i" or t.startswith("-i") for t in tokens[1:] if t.startswith("-")):
        # sed -i[ext] SCRIPT files... -- the first non-flag operand is the script, the rest are files.
        file_operands = [t for t in tokens[1:] if not t.startswith("-")]
        for token in file_operands[1:]:
            _add(token)
    elif basename in _FILE_OPERAND_COMMANDS:
        mode = _FILE_OPERAND_COMMANDS[basename]
        file_operands = [t for t in operands if not t.startswith("-")]
        if mode == "last" and file_operands:
            _add(file_operands[-1])
        elif mode == "all":
            for token in file_operands:
                _add(token)
    return targets


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_TOTAL_CAPTURE_BYTES",
    "DriftItem",
    "ScanBounds",
    "ScanResult",
    "declared_targets_for_command",
    "diff_scans",
    "scan_workspace",
]
