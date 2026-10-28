"""Mutating local file operations for VOOL's assistant tools — the destructive safety tier.

Moving or renaming a file/folder is destructive, so it sits in the highest tier: two independent gates
must both pass before anything moves.

  1. A protected-path denylist blocks system directories, drive roots, and VOOL's own runtime/wallet
     directory outright — the agent can never move INTO or OUT OF them, regardless of consent.
  2. A live OS user-consent prompt (Windows Hello / credential prompt — the SAME gate the wallet uses)
     must confirm the exact source -> destination.

Fail-closed everywhere: an unresolved path, a protected path, a missing source, an existing destination,
no consent mechanism, or a decline all mean nothing moves. The consent function and protected roots are
injectable, so the whole flow is unit-testable without a real prompt or touching system state.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.os_consent_gate import ConsentDeniedError, require_os_user_consent


@dataclass
class FileOpResult:
    ok: bool
    status: str
    message: str
    source: str = ""
    dest: str = ""


def _protected_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        sysdrive = os.environ.get("SystemDrive", "C:")
        roots += [Path(f"{sysdrive}\\{name}") for name in ("Windows", "Program Files", "Program Files (x86)", "ProgramData")]
    else:
        roots += [Path(p) for p in ("/bin", "/sbin", "/usr", "/etc", "/boot", "/sys", "/proc", "/dev", "/lib", "/lib64")]
    # VOOL's own runtime home holds the wallet/keys/tx DB — never let the agent move it.
    try:
        from core.runtime_paths import active_vool_home

        home = active_vool_home()
        if home:
            roots.append(Path(home))
    except Exception:
        pass
    # Sensitive user credential dirs. Unlike read/write/list (whitelisted to Desktop/Downloads/
    # Documents), move_path was gated only by this denylist + a consent click, so it could target
    # ~/.ssh, cloud creds, GPG, and shell dotfiles that the other lanes cannot touch. Deny them.
    # These are NOT platform-specific: Windows OpenSSH, the AWS CLI, gpg4win and the Solana CLI
    # all use the same ~/.ssh, ~/.aws, ~/.gnupg and ~/.config layout there, and Windows is the
    # platform VOOL ships an installer for.
    try:
        userhome = Path.home()
        roots += [
            userhome / ".ssh", userhome / ".aws", userhome / ".gnupg",
            userhome / ".config" / "solana", userhome / ".config" / "gcloud",
            userhome / ".config" / "gh", userhome / "Library" / "Keychains",
        ]
        if sys.platform == "win32":
            for env_name in ("APPDATA", "LOCALAPPDATA"):
                base = os.environ.get(env_name)
                if base:
                    roots += [Path(base) / "gnupg", Path(base) / "Microsoft" / "Credentials"]
    except Exception:
        pass
    return roots


def _is_protected(path: Path, protected: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        return True  # cannot resolve -> treat as unsafe
    if resolved == Path(resolved.anchor):
        return True  # a bare drive root (e.g. C:\) or filesystem root
    target = os.path.normcase(str(resolved))
    for root in protected:
        try:
            root_norm = os.path.normcase(str(root.resolve()))
        except Exception:
            root_norm = os.path.normcase(str(root))
        if target == root_norm or target.startswith(root_norm + os.sep):
            return True
    return False


def _canonicalize(raw: str) -> Path | None:
    """Expand `~` and resolve `raw` against the real filesystem -- following every symlink in
    its EXISTING ancestors and normalizing `..` against what is actually on disk, not lexically.
    `Path.resolve()` (non-strict) still produces a canonical path when the final leaf does not
    exist yet: it resolves as far as the real filesystem goes and appends the remainder, so a
    destination that hasn't been created yet is still correctly canonicalized through whatever
    real (possibly symlinked) ancestor directories it will land under.

    Returns `None` on a resolution failure (permission error, OS-level path error) -- the caller
    must fail closed on `None`, never fall back to the raw, unresolved string.
    """
    try:
        return Path(str(raw or "")).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _within_allowed_roots(resolved: Path, allowed_roots: list[Path]) -> bool:
    for root in allowed_roots:
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == root_resolved or root_resolved in resolved.parents:
            return True
    return False


def move_path(
    source: str,
    destination: str,
    *,
    consent_fn: Callable[[str], bool] = require_os_user_consent,
    protected: list[Path] | None = None,
    allowed_roots: list[Path] | None = None,
) -> FileOpResult:
    """Move/rename ``source`` to ``destination`` after canonical resolution, the protected-path
    denylist, an (optional) allowed-roots containment check, and OS-consent all pass.

    ANVIL, 2026-08-07, F6: the source and destination used to be only `Path(raw).expanduser()` --
    never `.resolve()`'d -- so `workspace/../../outside_target/exfil.txt` was taken at face
    value: it is not a protected system/wallet path, so the denylist gate (the only containment
    that existed) waved it straight through, the consent prompt showed the unresolved string
    (making the escape harder to notice, not easier), and the file landed outside the workspace
    with the source gone. Consent is not a containment boundary -- it confirms an action a human
    can evaluate, and a human cannot evaluate an escape they cannot see.

    Both `source` and `destination` are now canonically resolved BEFORE anything else runs,
    including consent -- a `..` segment, a symlink, or a symlinked ANCESTOR directory is resolved
    against the real filesystem, not manipulated lexically, so it cannot be hidden from either
    the containment check or the human being asked to confirm. `allowed_roots`, when given (the
    real dispatcher always supplies the bound workspace plus the safe machine roots), is a
    positive containment check -- not another denylist -- proven independently of it: something
    the denylist alone never was.
    """
    src_raw, dst_raw = str(source or "").strip(), str(destination or "").strip()
    if not src_raw or not dst_raw:
        return FileOpResult(False, "invalid_arguments", "Both a source and a destination path are required.")

    src = _canonicalize(src_raw)
    dst = _canonicalize(dst_raw)
    if src is None or dst is None:
        return FileOpResult(
            False, "invalid_arguments",
            "Could not resolve the source or destination path.", src_raw, dst_raw,
        )
    guarded = protected if protected is not None else _protected_roots()

    if not src.exists():
        return FileOpResult(False, "source_missing", f"Source does not exist: {src}", str(src), str(dst))
    if _is_protected(src, guarded) or _is_protected(dst, guarded):
        return FileOpResult(False, "blocked_protected_path",
                            "That path is a protected system or wallet location and cannot be moved.",
                            str(src), str(dst))
    # this runtime's own data, config and running code are never moved by a generic tool, wherever they live: the
    # denylist above catches the OS/wallet locations and the active runtime home; this adds the code root and any
    # active database directory the denylist does not name (core.runtime_state_protection).
    from core.runtime_state_protection import refusal as _protected_refusal

    for _candidate in (src, dst):
        _reason = _protected_refusal(_candidate, write=True)
        if _reason:
            return FileOpResult(False, "protected_runtime_state", _reason, str(src), str(dst))
    if allowed_roots is not None and not (
        _within_allowed_roots(src, allowed_roots) and _within_allowed_roots(dst, allowed_roots)
    ):
        return FileOpResult(
            False, "blocked_outside_scope",
            "Both the source and the destination must resolve inside the active workspace or a "
            "safe local folder (Desktop, Downloads, Documents) -- this move would leave that "
            "scope, so nothing was moved.",
            str(src), str(dst),
        )
    if dst.exists():
        return FileOpResult(False, "dest_exists", f"Destination already exists: {dst}", str(src), str(dst))
    if not dst.parent.exists():
        return FileOpResult(False, "dest_parent_missing", f"Destination folder does not exist: {dst.parent}", str(src), str(dst))

    try:
        if not consent_fn(f"Move {src} to {dst}"):
            return FileOpResult(False, "consent_declined", "You declined the move.", str(src), str(dst))
    except ConsentDeniedError:
        return FileOpResult(False, "consent_declined", "You declined the move.", str(src), str(dst))
    except Exception:
        # ConsentUnavailableError or anything unexpected -> fail closed, never move without a prompt.
        return FileOpResult(False, "consent_unavailable",
                            "No OS confirmation prompt is available, so the move was refused.",
                            str(src), str(dst))

    # The consent prompt can sit open for minutes. What it showed must still be what moves: both
    # paths resolve to the same places, and nothing has appeared at the destination meanwhile (a
    # file the owner saved while the prompt was open used to be replaced silently by the rename,
    # independent review 2026-10-28). The rename itself refuses to replace, atomically where the
    # OS can, and runs on parent folders opened without following links.
    if _canonicalize(src_raw) != src or _canonicalize(dst_raw) != dst:
        return FileOpResult(False, "path_changed", "The paths changed while you were asked, so nothing was moved.",
                            str(src), str(dst))
    if dst.exists() or dst.is_symlink():
        return FileOpResult(False, "dest_exists", f"Destination already exists: {dst}", str(src), str(dst))
    try:
        rename_no_replace(src, dst)
    except FileExistsError:
        return FileOpResult(False, "dest_exists", f"Destination already exists: {dst}", str(src), str(dst))
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            return FileOpResult(False, "error", f"Move failed: {exc}", str(src), str(dst))
        try:
            # Another disk: a copy that never replaces, then the source goes.
            copy_no_replace(src, dst)
        except FileExistsError:
            return FileOpResult(False, "dest_exists", f"Destination already exists: {dst}", str(src), str(dst))
        except Exception as move_exc:
            return FileOpResult(False, "error", f"Move failed: {move_exc}", str(src), str(dst))
    except Exception as exc:
        return FileOpResult(False, "error", f"Move failed: {exc}", str(src), str(dst))
    return FileOpResult(True, "moved", f"Moved {src} to {dst}.", str(src), str(dst))


# --- no-replace primitives --------------------------------------------------------------------

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1  # Linux renameat2
_RENAME_EXCL = 0x00000004  # macOS renameatx_np


#: Folders opened link by link need descriptor-relative calls. Windows has neither those nor
#: O_NOFOLLOW; there os.rename itself refuses to replace and O_EXCL creates without replacing.
PINNED_DIRS = os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW")


def open_pinned_dir(folder: Path) -> int:
    """A descriptor for an absolute, already-resolved folder, opened one part at a time from "/"
    without following links: a link swapped in anywhere on the way fails instead of being followed."""
    if not PINNED_DIRS:
        raise OSError(errno.ENOTSUP, "folders cannot be pinned on this system", str(folder))
    parts = Path(folder).parts
    if not parts or parts[0] != os.sep:
        raise ValueError(f"not an absolute path: {folder}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(os.sep, flags)
    try:
        for part in parts[1:]:
            nxt = os.open(part, flags | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _libc():
    try:
        return ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
    except OSError:
        return None


def _native_rename_no_replace(src_fd: int, src_name: str, dst_fd: int, dst_name: str) -> bool:
    """True when done by the OS's own atomic no-replace rename; False when there is none here."""
    libc = _libc()
    if libc is None:
        return False
    names = (os.fsencode(src_name), os.fsencode(dst_name))
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(src_fd, names[0], dst_fd, names[1], _RENAME_EXCL)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(src_fd, names[0], dst_fd, names[1], _RENAME_NOREPLACE)
    else:
        return False
    if result == 0:
        return True
    code = ctypes.get_errno()
    if code in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
        return False  # this filesystem has no such rename; the fallback below
    if code == errno.EEXIST or code == errno.ENOTEMPTY:
        raise FileExistsError(code, os.strerror(code), dst_name)
    raise OSError(code, os.strerror(code), src_name)


def rename_no_replace(src: Path, dst: Path) -> None:
    """Rename `src` to `dst` (both absolute and resolved) without ever replacing what is at `dst`.
    FileExistsError when something is there; OSError(EXDEV) across disks."""
    if not PINNED_DIRS:
        os.rename(src, dst)  # Windows: never replaces an existing destination
        return
    src_fd = open_pinned_dir(src.parent)
    try:
        dst_fd = open_pinned_dir(dst.parent)
        try:
            if _native_rename_no_replace(src_fd, src.name, dst_fd, dst.name):
                return
            if not os.path.isdir(src) or os.path.islink(src):
                # A hard link fails if the name is taken, so it is the no-replace step; then the
                # old name goes. (Links themselves are linked, not followed.)
                os.link(src.name, dst.name, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)
                os.unlink(src.name, dir_fd=src_fd)
                return
            try:
                os.lstat(dst.name, dir_fd=dst_fd)
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(dst))
            except FileNotFoundError:
                pass
            os.rename(src.name, dst.name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def create_file_exclusive(target: Path, content: str) -> None:
    """Create a new text file at an absolute, resolved `target`, never replacing anything and never
    through a link on the way (its folder is opened link by link). FileExistsError when taken. A
    write that fails part way removes the file it began, so "nothing was written" stays true."""
    data = content.encode("utf-8")
    if not PINNED_DIRS:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        try:
            _write_all(fd, data)
        except BaseException:
            os.close(fd)
            os.unlink(target)
            raise
        os.close(fd)
        return
    parent_fd = open_pinned_dir(target.parent)
    try:
        fd = os.open(
            target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent_fd
        )
        try:
            _write_all(fd, data)
        except BaseException:
            os.close(fd)
            os.unlink(target.name, dir_fd=parent_fd)
            raise
        os.close(fd)
    finally:
        os.close(parent_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def copy_no_replace(src: Path, dst: Path) -> None:
    """Across disks: copy `src` to `dst` without ever replacing what is at `dst`, then remove `src`.
    A file is created exclusively; a folder's top is made exclusively. A failed copy removes what it
    began, so the source stays the only copy."""
    if src.is_dir() and not src.is_symlink():
        os.mkdir(dst)  # FileExistsError when taken
        try:
            shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
        except BaseException:
            shutil.rmtree(dst, ignore_errors=True)
            raise
        shutil.rmtree(src)  # a failure part way leaves both; the error says the move failed
        return
    if src.is_symlink():
        os.symlink(os.readlink(src), dst)
        os.unlink(src)
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(dst, flags, 0o644)
    try:
        with open(src, "rb") as source:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                _write_all(fd, chunk)
        os.close(fd)
        fd = -1
        shutil.copystat(src, dst, follow_symlinks=False)
        os.unlink(src)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if os.path.lexists(src):
            os.unlink(dst)  # the source is still whole: the copy goes, so there is one copy, not two
        raise
