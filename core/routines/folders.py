"""Which folder a routine may watch and work in.

The same places VOOL's own file moves may write: Desktop, Downloads and Documents in the person's
home, or a folder inside one of them. Never a hidden folder, a link to somewhere else, a protected
system or credential folder, or VOOL's own data.
"""
from __future__ import annotations

from pathlib import Path

from core.routines.model import RoutineError

#: The English names of the three folders (core.safe_folder_names.ENGLISH_DIR_NAMES). On Linux the
#: folders on disk follow xdg-user-dirs ("~/Schreibtisch"), so the roots come from safe_roots().
SAFE_NAMES = ("Desktop", "Downloads", "Documents")


def safe_roots() -> tuple[Path, ...]:
    """The same roots VOOL's own file moves use (core.safe_folder_names.safe_root_paths)."""
    from core.safe_folder_names import safe_root_paths

    return safe_root_paths(Path.home().resolve())


def admit(raw: str) -> Path:
    """The resolved folder, or RoutineError with a plain reason."""
    text = str(raw or "").strip()
    if not text:
        raise RoutineError("no_folder", "Choose the folder this routine works in.")
    given = Path(text).expanduser()
    if not given.is_absolute():
        raise RoutineError("bad_folder", "Choose a folder in Desktop, Downloads or Documents.")
    try:
        if given.is_symlink():
            raise RoutineError("bad_folder", "That folder is a link to somewhere else. Choose the folder itself.")
        folder = given.resolve(strict=True)
    except RoutineError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise RoutineError("folder_missing", "That folder does not exist any more.") from None
    if not folder.is_dir():
        raise RoutineError("bad_folder", "Choose a folder, not a file.")
    root = next((root for root in safe_roots() if folder == root or root in folder.parents), None)
    if root is None:
        raise RoutineError("bad_folder", "Choose a folder in Desktop, Downloads or Documents.")
    if any(part.startswith(".") for part in folder.relative_to(root).parts):
        raise RoutineError("bad_folder", "Hidden folders cannot be used for a routine.")
    from core.machine_file_ops import _is_protected, _protected_roots
    from core.runtime_state_protection import refusal

    if _is_protected(folder, _protected_roots()) or refusal(folder, write=True):
        raise RoutineError("bad_folder", "That folder holds data VOOL protects. Choose another folder.")
    return folder


def inside(folder: Path, path: Path) -> bool:
    """Whether ``path`` (resolved) is ``folder`` or below it."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved == folder or folder in resolved.parents


__all__ = ["SAFE_NAMES", "admit", "inside", "safe_roots"]
