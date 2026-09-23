from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import secrets
import stat
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path


def truncate_text(text: str, *, limit: int = 1800) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n...[truncated]"


def content_sha256(text: str) -> str:
    """Deterministic content identity for a text file's current bytes.

    Used both to report "what's on disk right now" (read_file) and as an optimistic-concurrency
    precondition (write_file/replace_in_file `expected_hash`) so a caller's stale view of a file
    fails loudly as `stale_base` instead of silently overwriting a change it never saw.
    """
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8", mode: int | None = None) -> None:
    """Write `content` to `path` so a crash or exception never leaves a partially-written file.

    Writes to a temp file in the SAME directory (so the final `os.replace` is same-filesystem and
    therefore atomic on every OS this ships on), flushes and fsyncs it, then renames it over the
    target. A failure at any point before `os.replace` leaves the original file untouched; `os.replace`
    itself is a single atomic filesystem operation, so readers never observe a partial write.

    ANVIL-confirmed regression this closes: `tempfile.mkstemp()` always creates its temp file at
    0600 (a deliberate security default, not configurable), and `os.replace()` carries THAT mode
    onto the destination -- not the destination's own prior mode. Left uncorrected, overwriting an
    existing 0755 executable script through this function silently left it at 0600, unexecutable.

    - Overwriting an EXISTING file: the destination's current permission bits (including
      executable bits) are preserved exactly. Applied to the temp file BEFORE the swap, and
      treated as FATAL if it fails -- silently replacing an executable script with one that lost
      its executable bit is precisely the regression this function exists to prevent, so a chmod
      failure must stop the write, not degrade it quietly. Ownership (uid/gid) is preserved
      best-effort via `os.chown`: an unprivileged process can only "preserve" ownership it already
      holds (chowning to a foreign uid/gid requires privileges this process does not have), so a
      failure here is swallowed rather than fatal -- losing owning-uid precision on what is, in
      practice, always a single-owner workspace is not the same class of regression as silently
      narrowing or broadening permissions.
    - Creating a NEW file: ordinary create-file semantics (0666 masked by the process's active
      umask), matching what a plain `open(path, "w")` would have produced -- not a forced 0600.
    - `mode`, when given explicitly, wins over both of the above (for a caller -- like the
      mutation ledger's OWN, separate, explicitly-0600-pinned write path -- that needs a specific
      mode regardless of what's on disk or the process umask; this function does not decide that
      pin for any caller on its own).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_stat = None
    with contextlib.suppress(OSError):
        existing_stat = target.stat()

    if mode is not None:
        target_mode = mode
    elif existing_stat is not None:
        target_mode = existing_stat.st_mode & 0o7777
    else:
        # No "get umask" syscall exists -- os.umask() only ever SETS and returns the PREVIOUS
        # value, so the standard way to query it is to set 0 and immediately restore it.
        current_umask = os.umask(0)
        os.umask(current_umask)
        target_mode = 0o666 & ~current_umask

    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, target_mode)
        if existing_stat is not None:
            with contextlib.suppress(OSError):
                os.chown(tmp_name, existing_stat.st_uid, existing_stat.st_gid)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class DestinationChangedError(OSError):
    """The reviewed destination is no longer the one a write resolved: a component of its path was
    replaced by a link, or the path now names another directory or file. Nothing was written."""


class ReviewedBaseChangedError(OSError):
    """The reviewed destination is still the same file, but it no longer holds the bytes that were
    reviewed. Nothing was written."""


def _open_pinned_directory(parent: Path) -> int:
    """Open ``parent`` as a directory descriptor and prove it IS the canonical directory resolved
    for a reviewed write: after the open, no component of the path is a link and the descriptor is
    the directory the path names. A write made relative to the descriptor stays in that directory
    whatever happens to the path afterwards."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(str(parent), flags)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise DestinationChangedError(f"`{parent}` is no longer the reviewed directory") from exc
    try:
        current = Path(parent.anchor)
        for part in parent.parts[1:]:
            current = current / part
            try:
                is_link = stat.S_ISLNK(os.lstat(current).st_mode)
            except FileNotFoundError as exc:
                raise DestinationChangedError(f"`{current}` disappeared while it was being written") from exc
            if is_link:
                raise DestinationChangedError(f"`{current}` is a link, not the reviewed directory")
        opened, named = os.fstat(fd), os.stat(parent)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise DestinationChangedError(f"`{parent}` no longer names the directory that was opened")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _pinned_entry_sha256(name: str, fd: int) -> str:
    """sha256 of the entry ``name`` inside the pinned directory, read without following a link;
    "" when no entry exists."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        entry_fd = os.open(name, flags, dir_fd=fd)
    except FileNotFoundError:
        return ""
    except OSError as exc:  # ELOOP: the entry became a link
        raise DestinationChangedError(f"`{name}` is no longer the reviewed file") from exc
    digest = hashlib.sha256()
    with os.fdopen(entry_fd, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pinned_atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    expected_prior_sha256: str | None = None,
    mode: int | None = None,
) -> tuple[int, int]:
    """`atomic_write_text` for a REVIEWED destination. ``path`` is the canonical target (no links in
    it); the temporary file and the final rename both happen relative to the parent directory as
    pinned by `_open_pinned_directory`, never by walking the path again, with `atomic_write_text`'s
    mode and ownership rules (``mode``, when given, wins here too: a compensation restoring a recorded
    state passes the mode it recorded). When ``expected_prior_sha256`` is given ("" meaning the file must not
    exist), the bytes at the destination are re-read inside the pinned directory immediately before
    the rename and must be those reviewed bytes, else `ReviewedBaseChangedError`. A final entry that
    became a link, or a parent that is no longer the reviewed directory, raises
    `DestinationChangedError`. Either way no byte of the destination changes. (A writer that alters
    the file between that last read and the rename itself is not detectable through a rename.)
    Returns the (device, inode) of the file this call left at the destination, which a compensation
    needs in order to remove exactly that file and no other (`pinned_remove_created_file`). Platforms
    without directory descriptors fall back to `atomic_write_text` after the same link and
    reviewed-bytes checks on the path."""
    target = Path(path)
    parent, name = target.parent, target.name
    # `os.rename` with directory descriptors is `renameat`, which on POSIX atomically replaces an existing
    # destination exactly as `os.replace` does (they differ only on Windows, where neither accepts directory
    # descriptors). Some builds -- CPython 3.11 on macOS among them -- list `os.rename` but not `os.replace`
    # in `os.supports_dir_fd`, so testing `os.replace` there silently took the path-based fallback.
    dir_fd_calls = (os.open, os.stat, os.rename, os.unlink)
    if os.name != "posix" or not all(call in os.supports_dir_fd for call in dir_fd_calls):
        current = Path(target.anchor)
        for part in target.parts[1:]:
            current = current / part
            if os.path.lexists(current) and stat.S_ISLNK(os.lstat(current).st_mode):
                raise DestinationChangedError(f"`{current}` is a link, not the reviewed destination")
        if expected_prior_sha256 is not None:
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""
            if actual != expected_prior_sha256:
                raise ReviewedBaseChangedError(f"`{target}` no longer holds the reviewed bytes")
        atomic_write_text(target, content, encoding=encoding, mode=mode)
        written = os.lstat(target)
        return (written.st_dev, written.st_ino)
    fd = _open_pinned_directory(parent)
    tmp_name = ""
    try:
        existing_stat = None
        with contextlib.suppress(FileNotFoundError):
            existing_stat = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if existing_stat is not None and stat.S_ISLNK(existing_stat.st_mode):
            raise DestinationChangedError(f"`{target}` is a link, not the reviewed file")
        if expected_prior_sha256 is not None and _pinned_entry_sha256(name, fd) != expected_prior_sha256:
            raise ReviewedBaseChangedError(f"`{target}` no longer holds the reviewed bytes")
        if mode is not None:
            target_mode = mode
        elif existing_stat is not None:
            target_mode = existing_stat.st_mode & 0o7777
        else:
            current_umask = os.umask(0)
            os.umask(current_umask)
            target_mode = 0o666 & ~current_umask
        tmp_name = f".{name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
        tmp_fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=fd)
        with os.fdopen(tmp_fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), target_mode)
            if existing_stat is not None:
                with contextlib.suppress(OSError):
                    os.fchown(handle.fileno(), existing_stat.st_uid, existing_stat.st_gid)
            written = os.fstat(handle.fileno())
        os.rename(tmp_name, name, src_dir_fd=fd, dst_dir_fd=fd)
        tmp_name = ""
        with contextlib.suppress(OSError):
            os.fsync(fd)
        return (written.st_dev, written.st_ino)
    except BaseException:
        if tmp_name:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=fd)
        raise
    finally:
        os.close(fd)


def _require_created_entry(target: Path, entry: os.stat_result, created_identity: tuple[int, int]) -> None:
    """Raise `DestinationChangedError` unless ``entry``, examined without following a link, is still the
    regular file identified by ``created_identity``."""
    if stat.S_ISLNK(entry.st_mode):
        raise DestinationChangedError(f"`{target}` is now a link, not the file this operation created")
    if not stat.S_ISREG(entry.st_mode):
        raise DestinationChangedError(f"`{target}` is no longer the regular file this operation created")
    if (entry.st_dev, entry.st_ino) != tuple(created_identity):
        raise DestinationChangedError(f"`{target}` was replaced by another file after this operation created it")


def _put_back_aside_entry(fd: int, aside: str, name: str, target: Path) -> None:
    """Return an entry a compensation moved aside, which turned out to be another writer's, to its name.

    Never over a newer entry: a file goes back through ``os.link``, which refuses to replace; a
    DIRECTORY can only go back through ``rename``, which on POSIX silently replaces an empty
    directory another writer created at the name -- exactly the entry this compensation exists to
    protect. So a directory goes back only when the name is FREE, and is otherwise kept as the
    aside with a typed recovery path. (Residual window, stated plainly: between the absence check
    and the rename another writer may still create an empty directory at the name; the standard
    library has no rename-without-replace to close it.)"""
    try:
        if stat.S_ISDIR(os.stat(aside, dir_fd=fd, follow_symlinks=False).st_mode):
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(aside, name, src_dir_fd=fd, dst_dir_fd=fd)  # the name is free: nothing is replaced
                return
            raise DestinationChangedError(
                f"`{target}` was replaced by another writer's directory while it was being removed; that newer "
                f"directory stays untouched at the name and the directory removed in its place is kept as `{target.parent / aside}`"
            )
        os.link(aside, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)  # never over a newer entry
    except OSError as exc:
        raise DestinationChangedError(
            f"`{target}` was replaced while it was being removed, and the replacement could not be put back "
            f"under its name ({exc}); it is kept as `{target.parent / aside}`"
        ) from exc
    os.unlink(aside, dir_fd=fd)


def pinned_remove_created_file(path: Path, *, created_identity: tuple[int, int], expected_sha256: str) -> str:
    """Remove, as compensation, a file a reviewed write CREATED -- that file and no other.

    ``created_identity`` is what `pinned_atomic_write_text` returned when it created the file, and
    ``expected_sha256`` the digest of the bytes it wrote. Inside the parent directory pinned by
    `_open_pinned_directory`, the entry is examined without following a link: it must still be that
    regular file, holding those bytes. It is then renamed aside within the same directory -- an atomic
    move of exactly the entry the name held at that instant -- and examined again under the aside name
    before it is unlinked. So a writer that replaced the name after the first examination never has its
    file removed: an aside entry that is not the created file goes back under the name (never over a
    newer entry) and the removal is refused. Returns "removed", or "already_absent" when nothing is at the
    name (the state before the write holds again). Raises `DestinationChangedError` when the parent is no
    longer the reviewed directory or the entry is a link, another type or another file, and
    `ReviewedBaseChangedError` when it is the created file holding other bytes; either way the entry at the
    name stays as it is. (A process that already holds the file open can still write to it after the last
    examination; as with the writer's own last-read/rename window, a directory entry cannot show that.)
    Platforms without directory descriptors run the same examinations on the path and remove it by path."""
    target = Path(path)
    parent, name = target.parent, target.name
    dir_fd_calls = (os.open, os.stat, os.rename, os.unlink, os.link)
    if os.name != "posix" or not all(call in os.supports_dir_fd for call in dir_fd_calls):
        current = Path(target.anchor)
        for part in target.parts[1:-1]:
            current = current / part
            if os.path.lexists(current) and stat.S_ISLNK(os.lstat(current).st_mode):
                raise DestinationChangedError(f"`{current}` is a link, not the reviewed directory")
        try:
            entry = os.lstat(target)
        except FileNotFoundError:
            return "already_absent"
        _require_created_entry(target, entry, created_identity)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
            raise ReviewedBaseChangedError(f"`{target}` changed after this operation wrote it")
        os.unlink(target)
        return "removed"
    fd = _open_pinned_directory(parent)
    try:
        try:
            entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return "already_absent"
        _require_created_entry(target, entry, created_identity)
        if _pinned_entry_sha256(name, fd) != expected_sha256:
            raise ReviewedBaseChangedError(f"`{target}` changed after this operation wrote it")
        aside = f".{name}.{os.getpid()}.{secrets.token_hex(6)}.removing"
        try:
            os.rename(name, aside, src_dir_fd=fd, dst_dir_fd=fd)
        except FileNotFoundError:
            return "already_absent"
        try:
            _require_created_entry(target, os.stat(aside, dir_fd=fd, follow_symlinks=False), created_identity)
            if _pinned_entry_sha256(aside, fd) != expected_sha256:
                raise ReviewedBaseChangedError(f"`{target}` changed while it was being removed")
        except (DestinationChangedError, ReviewedBaseChangedError):
            _put_back_aside_entry(fd, aside, name, target)
            raise
        except OSError as exc:
            # the aside could not be examined (a read-only parent, a vanished entry): it is NOT
            # put back blind and NOT removed; it stays as the aside under a typed recovery path
            raise DestinationChangedError(
                f"`{target}` was moved aside for removal but could not be examined ({exc}); "
                f"it is preserved as `{target.parent / aside}` for recovery"
            ) from exc
        try:
            os.unlink(aside, dir_fd=fd)
        except OSError as exc:
            # the entry was proven to be the created file and its bytes, but the removal itself was
            # denied: nothing is claimed removed and the entry is preserved under its typed aside name
            raise DestinationChangedError(
                f"`{target}` was moved aside for removal but could not be removed ({exc}); "
                f"it is preserved as `{target.parent / aside}` for recovery"
            ) from exc
        with contextlib.suppress(OSError):
            os.fsync(fd)
        return "removed"
    finally:
        os.close(fd)


def diff_preview(*, before: str, after: str, path: str, limit: int = 1600) -> str:
    diff_lines = list(
        difflib.unified_diff(
            str(before or "").splitlines(),
            str(after or "").splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
            n=2,
        )
    )
    if not diff_lines:
        return ""
    preview = "\n".join(diff_lines[:40]).strip()
    if len(preview) <= limit:
        return preview
    return preview[: max(1, limit - 3)].rstrip() + "..."


def build_file_diff_artifact(
    *,
    path: str,
    action: str,
    before: str,
    after: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact = {
        "artifact_type": "file_diff",
        "path": str(path or "").strip(),
        "action": str(action or "").strip(),
        "diff_preview": diff_preview(before=before, after=after, path=str(path or "").strip()),
        "before_hash": content_sha256(before),
        "after_hash": content_sha256(after),
    }
    artifact.update(dict(extra or {}))
    return artifact


def build_command_artifact(
    *,
    command: str,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    status: str,
    artifact_type: str = "command_output",
) -> dict[str, Any]:
    return {
        "artifact_type": artifact_type,
        "command": str(command or "").strip(),
        "cwd": str(cwd or "").strip(),
        "returncode": int(returncode or 0),
        "stdout": truncate_text(stdout, limit=2400),
        "stderr": truncate_text(stderr, limit=1600),
        "status": str(status or "").strip(),
    }


def build_failure_artifact(
    *,
    command: str,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "artifact_type": "failure",
        "command": str(command or "").strip(),
        "cwd": str(cwd or "").strip(),
        "returncode": int(returncode or 0),
        "summary": str(summary or "").strip(),
        "stdout": truncate_text(stdout, limit=2400),
        "stderr": truncate_text(stderr, limit=1600),
    }


def session_key_for_workspace(session_id: str | None, workspace_root: Path | None = None) -> str:
    """The mutation-ledger key for a session, falling back to the workspace when there is no session.

    ``workspace_root`` is optional because it is only consulted on the fallback branch: a caller
    that HAS a session id never needs the root, and one of them -- the receipt lookup behind "where
    did that file go?" -- is asking precisely because the root is the unknown.
    """
    clean_session = str(session_id or "").strip()
    if clean_session:
        return clean_session.replace("/", "_")
    if workspace_root is None:
        raise ValueError("session_key_for_workspace needs a session id or a workspace root")
    digest = hashlib.sha1(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return f"workspace-{digest[:16]}"


def _force_fresh_source_timestamp(path: Path, *, minimum_mtime_ns: int = 0) -> None:
    if not path.exists() or not path.is_file():
        return
    stat_result = path.stat()
    target_ns = max(
        int(stat_result.st_mtime_ns) + 1,
        int(minimum_mtime_ns or 0) + 1_000_000_000,
        time.time_ns() + 1_000_000_000,
    )
    os.utime(path, ns=(target_ns, target_ns))


def record_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
    intent: str,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_changes: list[dict[str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        enriched = dict(change)
        # Persisted at record time, not derived only at rollback time: a restart-safe conflict
        # check needs "what did THIS mutation actually leave on disk" to be part of the durable
        # record, not something reconstructed from ambient state that may itself have moved on.
        enriched.setdefault("before_hash", content_sha256(str(enriched.get("before_text") or "")))
        enriched.setdefault("after_hash", content_sha256(str(enriched.get("after_text") or "")))
        clean_changes.append(enriched)
    if not clean_changes:
        return {}
    record = {
        "mutation_id": f"mutation-{uuid.uuid4().hex}",
        "session_key": session_key_for_workspace(session_id, workspace_root),
        "workspace_root": str(workspace_root.resolve()),
        "intent": str(intent or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "changes": clean_changes,
        "rolled_back_at": None,
    }
    records = _load_mutation_records(record["session_key"])
    records.append(record)
    _store_mutation_records(record["session_key"], records)
    return record


def _target_parent_within_workspace(raw_target: Path, *, workspace_root: Path) -> bool:
    """Whether `raw_target`'s PARENT directory chain, fully resolved (following every symlink in
    it), still lands inside the workspace root -- the rollback-side equivalent of the containment
    check `resolve_workspace_path` already enforces on the forward write path.

    ANVIL-confirmed escape this closes: checking only the leaf's own `is_symlink()` rejects a
    symlink AT the final path component, but not an ordinary filename sitting under a PARENT
    directory that was replaced by a symlink pointing outside the workspace -- that resolves
    outside it just the same, and reading/writing/deleting through it silently touches whatever
    the symlinked parent actually points to.
    """
    resolved_root = workspace_root.resolve()
    try:
        resolved_parent = raw_target.parent.resolve()
    except OSError:
        return False
    return resolved_parent == resolved_root or resolved_root in resolved_parent.parents


def _diagnose_rollback_conflict(
    raw_target: Path,
    *,
    workspace_root: Path,
    existed_before: bool,
    expected_existed_after: bool,
    expected_after_hash: str,
) -> str | None:
    """Whether the CURRENT on-disk state at `raw_target` still matches what this mutation left
    behind, or `None` if it is safe to restore/remove.

    `raw_target` must be the UNRESOLVED join of workspace_root and the stored relative path --
    resolving it first would silently follow a symlink an external process swapped in, which is
    exactly the escape this function exists to refuse. A leaf that is now a symlink is refused
    outright, before anything reads through it or follows it anywhere -- and so is a PARENT
    directory that was replaced by one (see `_target_parent_within_workspace`).
    """
    if not _target_parent_within_workspace(raw_target, workspace_root=workspace_root):
        return "parent_directory_escapes_workspace"
    if raw_target.is_symlink():
        return "target_replaced_by_symlink"
    exists = raw_target.exists()
    if expected_existed_after:
        if not exists:
            # The rollback ACTION differs by what preceded the mutation, and so does what "missing
            # now" means: for a mutation that CREATED this file (existed_before=False), rollback's
            # job is to delete it -- if it's already gone, that goal is already met, not a
            # conflict. For a mutation that MODIFIED an existing file (existed_before=True),
            # rollback's job is to restore the old content -- recreating a file the user
            # deliberately removed since is exactly the silent overwrite this check prevents.
            return "target_missing" if existed_before else None
        if raw_target.is_dir() or not raw_target.is_file():
            return "target_type_changed"
        current_hash = content_sha256(raw_target.read_bytes().decode("utf-8", errors="replace"))
        if current_hash != expected_after_hash:
            return "content_changed"
        return None
    # The mutation left this path absent (a create it undoes, or a delete it leaves deleted).
    # Anything now sitting there was put there by someone else after the mutation ran.
    if exists:
        return "unexpected_existence"
    return None


def rollback_last_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
) -> dict[str, Any] | None:
    session_key = session_key_for_workspace(session_id, workspace_root)
    records = _load_mutation_records(session_key)
    workspace_text = str(workspace_root.resolve())
    target_index = -1
    target_record: dict[str, Any] | None = None
    for index in range(len(records) - 1, -1, -1):
        record = dict(records[index] or {})
        if str(record.get("workspace_root") or "") != workspace_text:
            continue
        if record.get("rolled_back_at"):
            continue
        target_record = record
        target_index = index
        break
    if target_record is None:
        return None

    changes = list(target_record.get("changes") or [])

    # Pass 1: verify EVERY change against current disk state before writing ANYTHING. A mutation
    # that touched three files must not partially roll back two of them and then discover the
    # third conflicts -- "do not write anything" means the whole rollback, not a best-effort one.
    conflicts: list[dict[str, Any]] = []
    for change in changes:
        relative_path = str(change.get("path") or "").strip()
        if not relative_path:
            continue
        raw_target = workspace_root / relative_path
        existed_before = bool(change.get("existed_before", False))
        expected_existed_after = bool(change.get("existed_after", False))
        expected_after_hash = str(
            change.get("after_hash") or content_sha256(str(change.get("after_text") or ""))
        )
        reason = _diagnose_rollback_conflict(
            raw_target,
            workspace_root=workspace_root,
            existed_before=existed_before,
            expected_existed_after=expected_existed_after,
            expected_after_hash=expected_after_hash,
        )
        if reason is None:
            continue
        current_hash = ""
        if raw_target.exists() and not raw_target.is_symlink() and raw_target.is_file():
            with contextlib.suppress(OSError, UnicodeError):
                current_hash = content_sha256(raw_target.read_bytes().decode("utf-8", errors="replace"))
        conflicts.append(
            {
                "path": relative_path,
                "reason": reason,
                "expected_hash": expected_after_hash,
                "current_hash": current_hash,
            }
        )

    if conflicts:
        return {
            "status": "conflict",
            "mutation_id": str(target_record.get("mutation_id") or "").strip(),
            "intent": str(target_record.get("intent") or "").strip(),
            "conflicts": conflicts,
        }

    # Pass 2: every change verified clean above -- safe to actually write now.
    restored_paths: list[str] = []
    removed_paths: list[str] = []
    for change in reversed(changes):
        relative_path = str(change.get("path") or "").strip()
        if not relative_path:
            continue
        target = workspace_root / relative_path
        existed_before = bool(change.get("existed_before", False))
        before_text = str(change.get("before_text") or "")
        if existed_before:
            previous_mtime_ns = int(target.stat().st_mtime_ns) if target.exists() and target.is_file() else 0
            atomic_write_text(target, before_text)
            # `atomic_write_text` already preserves whatever mode is CURRENTLY on `target` across
            # the swap -- correct for an ordinary write, but rollback's job is stronger: restore
            # the mode the file actually had BEFORE this mutation, which may differ from whatever
            # is on disk right now (e.g. something else chmod'd the file, without touching its
            # content, in between the mutation and this rollback). Explicit re-apply here is a
            # deliberate belt-and-suspenders on top of that, not a duplicate of it.
            before_mode = change.get("before_mode")
            if isinstance(before_mode, int):
                with contextlib.suppress(OSError):
                    os.chmod(target, before_mode)
            _force_fresh_source_timestamp(target, minimum_mtime_ns=previous_mtime_ns)
            restored_paths.append(relative_path)
        else:
            if target.exists() and not target.is_symlink():
                target.unlink()
                removed_paths.append(relative_path)

    target_record["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    records[target_index] = target_record
    _store_mutation_records(session_key, records)
    return {
        "status": "rolled_back",
        "mutation_id": str(target_record.get("mutation_id") or "").strip(),
        "intent": str(target_record.get("intent") or "").strip(),
        "restored_paths": restored_paths,
        "removed_paths": removed_paths,
        "changes": list(target_record.get("changes") or []),
    }


def latest_workspace_mutation(
    *,
    session_id: str | None,
    workspace_root: Path,
    require_unpromoted: bool = False,
) -> dict[str, Any] | None:
    session_key = session_key_for_workspace(session_id, workspace_root)
    workspace_text = str(workspace_root.resolve())
    records = _load_mutation_records(session_key)
    for index in range(len(records) - 1, -1, -1):
        record = dict(records[index] or {})
        if str(record.get("workspace_root") or "") != workspace_text:
            continue
        if record.get("rolled_back_at"):
            continue
        if require_unpromoted and str(record.get("procedure_id") or "").strip():
            continue
        return record
    return None


def latest_workspace_mutation_for_session(session_id: str) -> dict[str, Any] | None:
    """The newest un-rolled-back mutation this SESSION recorded, in whatever workspace it targeted.

    `latest_workspace_mutation` filters by a workspace root the caller already knows. The follow-up
    "where did that file go?" is the case where the root is exactly what is missing -- a General
    chat carries no bound workspace at all -- so this reads the session's own ledger and hands back
    the record, absolute ``workspace_root`` included. Read-only; it never writes the ledger.
    """
    clean_session = str(session_id or "").strip()
    if not clean_session:
        return None
    records = _load_mutation_records(session_key_for_workspace(clean_session))
    for index in range(len(records) - 1, -1, -1):
        record = dict(records[index] or {})
        if record.get("rolled_back_at"):
            continue
        if not list(record.get("changes") or []):
            continue
        return record
    return None


def mark_workspace_mutation_promoted(
    *,
    session_id: str | None,
    workspace_root: Path,
    mutation_id: str,
    procedure_id: str,
) -> None:
    clean_mutation_id = str(mutation_id or "").strip()
    clean_procedure_id = str(procedure_id or "").strip()
    if not clean_mutation_id or not clean_procedure_id:
        return
    session_key = session_key_for_workspace(session_id, workspace_root)
    records = _load_mutation_records(session_key)
    changed = False
    for index, raw_record in enumerate(records):
        record = dict(raw_record or {})
        if str(record.get("mutation_id") or "").strip() != clean_mutation_id:
            continue
        record["procedure_id"] = clean_procedure_id
        records[index] = record
        changed = True
        break
    if changed:
        _store_mutation_records(session_key, records)


def _mutation_log_path(session_key: str) -> Path:
    return data_path("runtime_execution", "mutations", f"{session_key}.json")


def _load_mutation_records(session_key: str) -> list[dict[str, Any]]:
    path = _mutation_log_path(session_key)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def _store_mutation_records(session_key: str, records: list[dict[str, Any]]) -> None:
    # This ledger holds `before_text` VERBATIM and must keep doing so: rollback_last_workspace_mutation
    # writes those exact bytes back to the user's file, so redacting them here would restore a
    # "[redacted]" placeholder over real content -- data loss dressed up as a security fix. The right
    # control is access, not content: owner-only (0600) on both the directory and the file, since a
    # rolled-back .env leaves real key material on disk by design.
    path = _mutation_log_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    payload = json.dumps(records, indent=2, sort_keys=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)  # tighten a file that already existed at a looser mode
