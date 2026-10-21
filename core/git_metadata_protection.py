"""Repository metadata the generic write tools may not change.

A repository's ``.git`` entry (a directory, or a file pointing at one) holds configuration and hooks that git runs
later for the user, outside any sandbox. The command sandbox already denies writes to it (sandbox.job_runner's
``_SEATBELT_GIT_METADATA_FILTER``); this module is the same rule for the file tools, asked through
``core.runtime_state_protection.refusal`` on every write.

A path is refused when any of its components is ``.git`` in any letter case (macOS volumes are case-insensitive by
default), compared both as written (made absolute) and with symlinks resolved, or when it lies inside a directory
that git itself would treat as a repository directory, whatever that directory is called.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

REFUSAL_PREFIX = "protected_git_metadata"

# Code points HFS+ ignores in file names, so a name carrying them is still `.git` there (git's is_hfs_dotgit list).
_HFS_IGNORABLE = frozenset(
    "‌‍‎‏‪‫‬‭‮⁪⁫⁬⁭⁮⁯﻿"
)


def _is_dotgit(name: str) -> bool:
    return "".join(ch for ch in name if ch not in _HFS_IGNORABLE).casefold() == ".git"


def _forms(path: Path) -> list[Path]:
    forms: list[Path] = []
    with contextlib.suppress(OSError, ValueError):
        forms.append(Path(os.path.abspath(path)))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        resolved = path.resolve()
        if all(str(resolved) != str(form) for form in forms):
            forms.append(resolved)
    return forms


def _is_git_directory(directory: Path) -> bool:
    # git's own test for a repository directory: a HEAD file with objects and refs directories beside it.
    try:
        return (directory / "HEAD").is_file() and (directory / "objects").is_dir() and (directory / "refs").is_dir()
    except OSError:
        return False


def refusal(path: str | Path) -> str:
    """'' when a generic tool may write ``path``; otherwise the refusal to report (nothing was touched)."""
    candidate = Path(str(path)).expanduser()
    for form in _forms(candidate):
        if any(_is_dotgit(part) for part in form.parts):
            return (f"{REFUSAL_PREFIX}: `{candidate}` is inside a repository's .git metadata. File tools never change "
                    "a repository's own configuration or hooks; nothing was touched.")
        for directory in (form, *form.parents):
            if _is_git_directory(directory):
                return (f"{REFUSAL_PREFIX}: `{candidate}` is inside a git repository directory. File tools never "
                        "change a repository's own configuration or hooks; nothing was touched.")
    return ""


__all__ = ["REFUSAL_PREFIX", "refusal"]
