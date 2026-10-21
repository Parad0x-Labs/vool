"""Repository metadata the generic write tools may not change.

A repository's ``.git`` entry (a directory, or a file pointing at one) holds configuration and hooks that git runs
later for the user, outside any sandbox. The command sandbox already denies writes to it (sandbox.job_runner's
``_SEATBELT_GIT_METADATA_FILTER``); this module is the same rule for the file tools, asked through
``core.runtime_state_protection.refusal`` on every write.

A path is refused when any of its components is ``.git`` in any letter case (macOS volumes are case-insensitive by
default), compared both as written (made absolute) and with symlinks resolved, or when it lies inside a directory
that git itself would treat as a repository directory, whatever that directory is called.

A repository's configuration can also send git to ordinary project files: `core.hooksPath` names the hooks folder
(Husky sets it to a folder in the project) and `include.path` / `includeIf.*.path` pull in more configuration.
For a path inside a working tree, git itself is asked where its hooks folder and every configuration file it reads
are, and a write there is refused too. When git cannot answer and the repository's own configuration names either
setting, the write is refused rather than guessed.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
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


_GIT_TIMEOUT_SECONDS = 10


def _worktree_top(form: Path) -> Path | None:
    for directory in form.parents:
        with contextlib.suppress(OSError):
            if os.path.lexists(directory / ".git"):
                return directory
    return None


def _local_config_names_redirect(top: Path) -> bool:
    """Whether the repository's own config text mentions a hooks folder or an include (read without git)."""
    entry = top / ".git"
    try:
        if entry.is_file():
            pointer = entry.read_text(encoding="utf-8", errors="replace").strip()
            gitdir = Path(pointer.split(":", 1)[1].strip()) if pointer.lower().startswith("gitdir:") else None
            if gitdir is None:
                return True
            config = (gitdir if gitdir.is_absolute() else top / gitdir) / "config"
        else:
            config = entry / "config"
        text = config.read_text(encoding="utf-8", errors="replace").casefold() if config.exists() else ""
    except OSError:
        return True
    return "hookspath" in text or "include" in text


def _git(top: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        done = subprocess.run([git, "-C", str(top), *args], capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT_SECONDS, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _redirect_targets(top: Path) -> tuple[list[Path], list[Path]] | None:
    """(hooks folders, configuration files) git uses for the working tree at ``top``, or None if git cannot say."""
    hooks_out = _git(top, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    config_out = _git(top, "config", "--list", "--show-origin", "-z")
    if hooks_out is None or config_out is None:
        return None
    hooks = [Path(hooks_out.strip())] if hooks_out.strip() else []
    files: list[Path] = []
    # `--show-origin -z` emits: origin NUL key NEWLINE value NUL, repeated.
    parts = config_out.split("\0")
    for index in range(0, len(parts) - 1, 2):
        origin, entry = parts[index], parts[index + 1]
        key, _sep, value = entry.partition("\n")
        origin_file = Path(origin[5:]) if origin.startswith("file:") else None
        if origin_file is not None:
            files.append(origin_file if origin_file.is_absolute() else top / origin_file)
        lowered = key.casefold()
        if value and (lowered == "include.path" or (lowered.startswith("includeif.") and lowered.endswith(".path"))):
            named = Path(value).expanduser()
            base = (files[-1].parent if origin_file is not None else top)
            files.append(named if named.is_absolute() else base / named)
        elif value and lowered == "core.hookspath":
            named = Path(value).expanduser()
            hooks.append(named if named.is_absolute() else top / named)
    return hooks, files


def _redirect_refusal(candidate: Path, forms: list[Path], top: Path) -> str:
    targets = _redirect_targets(top)
    if targets is None:
        if _local_config_names_redirect(top):
            return (f"{REFUSAL_PREFIX}: git could not say where the repository at `{top}` keeps its hooks and included "
                    f"settings, and its configuration names them, so `{candidate}` was not written; nothing was touched.")
        return ""
    hooks, files = targets
    for target in forms:
        for folder in hooks:
            for folder_form in _forms(folder):
                if target == folder_form or folder_form in target.parents:
                    return (f"{REFUSAL_PREFIX}: `{candidate}` is in the hooks folder git uses for this repository. File "
                            "tools never change a repository's hooks; nothing was touched.")
        for config in files:
            if any(target == config_form for config_form in _forms(config)):
                return (f"{REFUSAL_PREFIX}: `{candidate}` is a configuration file git reads for this repository. File "
                        "tools never change a repository's own configuration; nothing was touched.")
    return ""


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
    forms = _forms(candidate)
    tops = {str(top): top for top in (_worktree_top(form) for form in forms) if top is not None}
    for top in tops.values():
        reason = _redirect_refusal(candidate, forms, top)
        if reason:
            return reason
    return ""


__all__ = ["REFUSAL_PREFIX", "refusal"]
