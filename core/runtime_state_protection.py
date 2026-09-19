"""Paths the generic tools may not reach: this runtime's own state and its running code.

The tools a model -- and so a skill steering a model -- can call reach the filesystem through three owners, and each one
asks this module before it touches a path:

* the machine file tools (core.runtime_execution_tools: machine.read_file, machine.write_file, machine.ensure_directory,
  machine.move_path, machine.list_directory, machine.find_largest);
* the workspace tools (core.execution.workspace_tools.resolve_workspace_path and the workspace command working directory);
* the command sandbox's Seatbelt profile (sandbox.job_runner), which plugin commands share.

Protected state -- no read, no write, no listing: the runtime home's ``data`` and ``config`` directories and the directory
of the active database when it lives elsewhere. They hold the SQLite database with its -wal/-shm/-journal files, Contacts
and their pending changes, the operator credential's verifiers and attempt counters, the node signing key (data/keys) and
wallet custody.

Protected code -- no write: the running code root (``core.runtime_paths.PROJECT_ROOT``), except workspace folders strictly
inside it, so a generic tool cannot rewrite the checks that guard it.

A path is compared both as written (made absolute) and with symlinks resolved; an existing regular file with more than one
hard link is also compared by inode with the files of the protected state, so a link planted in an allowed folder does not
lead back in.

Not covered, stated: another program running as the same user with its own file access (a terminal, an installed app, a
plugin running outside the sandbox) is not a generic tool, and nothing here constrains it.
"""
from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

REFUSAL_PREFIX = "protected_runtime_state"
_MAX_WALK_FILES = 20000


def _dedupe(paths: list[Path]) -> tuple[Path, ...]:
    seen: dict[str, Path] = {}
    for path in paths:
        seen.setdefault(str(path), path)
    return tuple(seen.values())


def _forms(path: Path) -> list[Path]:
    forms: list[Path] = []
    with contextlib.suppress(OSError, ValueError):
        forms.append(Path(os.path.abspath(path)))
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        forms.append(path.resolve())
    return list(_dedupe(forms))


def protected_state_roots() -> tuple[Path, ...]:
    from core import runtime_paths

    roots = [runtime_paths.active_data_dir(), runtime_paths.active_config_home_dir()]
    try:
        from storage.db import active_default_db_path

        roots.append(Path(active_default_db_path()).parent)
    except Exception:
        pass
    return _dedupe([form for root in roots for form in _forms(Path(root))])


def protected_code_roots() -> tuple[Path, ...]:
    from core import runtime_paths

    return _dedupe(_forms(Path(runtime_paths.PROJECT_ROOT)))


def code_write_exemptions() -> tuple[Path, ...]:
    """Workspace folders strictly inside the code root. The code root itself is never exempt."""
    from core import runtime_paths

    candidates = [Path(runtime_paths.WORKSPACE_DIR)]
    with contextlib.suppress(Exception):
        candidates.append(runtime_paths.active_workspace_dir())
    roots = protected_code_roots()
    exempt = [form for candidate in candidates for form in _forms(candidate) if any(root in form.parents for root in roots)]
    return _dedupe(exempt)


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _linked_into_state(forms: list[Path]) -> bool:
    for form in forms:
        try:
            info = os.stat(form)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
            continue
        target = (info.st_dev, info.st_ino)
        seen = 0
        for root in protected_state_roots():
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    seen += 1
                    if seen > _MAX_WALK_FILES:
                        return True  # cannot rule it out within the bound: refuse
                    try:
                        other = os.stat(os.path.join(dirpath, name))
                    except OSError:
                        continue
                    if (other.st_dev, other.st_ino) == target:
                        return True
        return False
    return False


def refusal(path: str | Path, *, write: bool) -> str:
    """'' when a generic tool may use ``path`` for this purpose; otherwise the refusal to report (nothing was touched)."""
    candidate = Path(str(path)).expanduser()
    forms = _forms(candidate)
    state_roots = protected_state_roots()
    if any(_within(form, root) for form in forms for root in state_roots) or any(_within(root, form) for form in forms for root in state_roots if write):
        return (f"{REFUSAL_PREFIX}: `{candidate}` is this runtime's own protected state (its database, contacts, credentials and keys). "
                "Generic file and command tools cannot read or change it; nothing was touched.")
    if write:
        exempt = code_write_exemptions()
        for form in forms:
            if any(_within(form, root) for root in protected_code_roots()) and not any(_within(form, allowed) for allowed in exempt):
                return (f"{REFUSAL_PREFIX}: `{candidate}` is inside this runtime's running code. Generic tools cannot change it; nothing was touched.")
    if _linked_into_state(forms):
        return (f"{REFUSAL_PREFIX}: `{candidate}` is a hard link to a file of this runtime's protected state. "
                "Generic tools cannot use it; nothing was touched.")
    return ""


__all__ = ["REFUSAL_PREFIX", "code_write_exemptions", "protected_code_roots", "protected_state_roots", "refusal"]
