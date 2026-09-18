"""Server-side project registry: a project is a named local folder a chat can be bound to.

Codex-style isolation for VOOL. A chat bound to a project operates in that project's folder and (with
per-project context scoping) never sees another project's files or memory. Projects live in
``<data>/projects.json``. Owner-local and path-validated: a root must be an existing absolute directory
on this machine. A project's id is derived from its real path, so re-adding the same folder is
idempotent (same id, name refreshed) rather than creating a duplicate.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.memory.files import utcnow
from core.runtime_paths import data_path

_PROJECTS_FILE = "projects.json"
_PROJECTS_LOCK = threading.Lock()
_NAME_MAX = 80


def projects_path() -> Path:
    return data_path(_PROJECTS_FILE)


def _project_id_for(real_root: str) -> str:
    return "proj_" + hashlib.sha256(str(real_root).encode("utf-8")).hexdigest()[:12]


def _normalize_root(root: str) -> str:
    """Absolute, user-expanded, symlink-resolved path (so two spellings of one folder collapse to one id)."""
    raw = str(root or "").strip()
    if not raw:
        return ""
    return os.path.realpath(os.path.abspath(os.path.expanduser(raw)))


def _load() -> dict[str, dict[str, Any]]:
    path = projects_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in projects:
        if isinstance(item, dict) and item.get("id") and item.get("root"):
            out[str(item["id"])] = item
    return out


def _save(projects: dict[str, dict[str, Any]]) -> None:
    path = projects_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"projects": list(projects.values())}, ensure_ascii=False)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    with contextlib.suppress(OSError):  # last-good backup
        path.with_name(path.name + ".bak").write_text(payload, encoding="utf-8")


def list_projects() -> list[dict[str, Any]]:
    """All projects, newest first. Each: {id, name, root, created_at, exists}."""
    projects = list(_load().values())
    for entry in projects:
        entry["exists"] = os.path.isdir(str(entry.get("root") or ""))
    projects.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return projects


def get_project(project_id: str) -> dict[str, Any] | None:
    return _load().get(str(project_id or "").strip())


def project_root(project_id: str) -> str | None:
    """The bound folder for a project, only if it still exists as a directory (else None)."""
    project = get_project(project_id)
    if not project:
        return None
    root = str(project.get("root") or "")
    return root if root and os.path.isdir(root) else None


def create_project(name: str, root: str) -> tuple[bool, Any]:
    """Register a folder as a project. Returns (True, project) or (False, error_message).

    Idempotent by real path: adding the same folder twice returns the existing project (name refreshed).
    The root must be an existing absolute directory; the filesystem root '/' is refused as a guard.
    """
    resolved = _normalize_root(root)
    if not resolved:
        return False, "a project folder is required"
    # ``_normalize_root`` returns the drive root on Windows (``C:\\``), so comparing
    # with ``os.path.sep`` only protects POSIX. A filesystem root is its own parent on
    # every platform and must never become a project boundary.
    if Path(resolved).parent == Path(resolved):
        return False, "refusing to use the whole filesystem as a project"
    if not os.path.exists(resolved):
        return False, "that folder does not exist"
    if not os.path.isdir(resolved):
        return False, "a project must be a folder"
    clean_name = " ".join(str(name or "").split()).strip()[:_NAME_MAX] or os.path.basename(resolved) or resolved
    pid = _project_id_for(resolved)
    with _PROJECTS_LOCK:
        projects = _load()
        existing = projects.get(pid) or {}
        entry = {
            "id": pid,
            "name": clean_name,
            "root": resolved,
            "emoji": existing.get("emoji") or "",   # a sidebar marker to tell projects apart at a glance
            "color": existing.get("color") or "",   # an optional accent colour (#rrggbb) tinting the sidebar + context bar
            "created_at": existing.get("created_at") or utcnow(),
        }
        projects[pid] = entry
        _save(projects)
    return True, {**entry, "exists": True}


_EMOJI_MAX = 8   # a couple of emoji code points (some emoji are multi-codepoint), never a long string
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")   # strict #rrggbb only — never arbitrary CSS


def normalize_color(color: str) -> str:
    """A stored colour is either '' (none) or a lowercase #rrggbb; anything else collapses to ''."""
    raw = str(color or "").strip().lower()
    return raw if _HEX_COLOR.match(raw) else ""


def set_project_emoji(project_id: str, emoji: str) -> bool:
    """Set (or clear with "") a project's sidebar emoji. Returns True if a project was updated."""
    pid = str(project_id or "").strip()
    if not pid:
        return False
    clean = "".join(str(emoji or "").split())[:_EMOJI_MAX]   # strip whitespace, cap length
    with _PROJECTS_LOCK:
        projects = _load()
        entry = projects.get(pid)
        if not entry:
            return False
        entry["emoji"] = clean
        projects[pid] = entry
        _save(projects)
    return True


def set_project_color(project_id: str, color: str) -> bool:
    """Set (or clear with "") a project's accent colour (#rrggbb). Returns True if a project was updated."""
    pid = str(project_id or "").strip()
    if not pid:
        return False
    clean = normalize_color(color)
    with _PROJECTS_LOCK:
        projects = _load()
        entry = projects.get(pid)
        if not entry:
            return False
        entry["color"] = clean
        projects[pid] = entry
        _save(projects)
    return True


_APPROVAL_KEY = "low_risk_approval"


def get_project_low_risk_approval(project_id: str) -> dict[str, Any]:
    """The standing 'allow low-risk actions in this project' grant, or {} when there is none.

    Low-risk means reads, searches, and file writes that land INSIDE this project's folder. The grant
    is deliberately NOT a permission set: it can only turn an approval PROMPT into an allow for that
    bounded class. Deletes, moves, network sends, spend, and anything resolving outside the root are
    not covered and keep prompting. See ``core.mode_permission_policy`` for the enforcement.
    """
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    entry = _load().get(pid) or {}
    grant = entry.get(_APPROVAL_KEY)
    return dict(grant) if isinstance(grant, dict) and grant.get("granted_at") else {}


def set_project_low_risk_approval(
    project_id: str,
    *,
    granted_at: str = "",
    granted_by_session: str = "",
    actions: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Persist the standing low-risk grant on the project entry. Returns the stored grant, or {}."""
    pid = str(project_id or "").strip()
    if not pid:
        return {}
    grant = {
        "granted_at": str(granted_at or utcnow()),
        "granted_by_session": str(granted_by_session or "")[:120],
        "actions": [str(item)[:60] for item in list(actions)[:24]],
    }
    with _PROJECTS_LOCK:
        projects = _load()
        entry = projects.get(pid)
        if not entry:
            return {}
        entry[_APPROVAL_KEY] = grant
        projects[pid] = entry
        _save(projects)
    return dict(grant)


def clear_project_low_risk_approval(project_id: str) -> bool:
    """Revoke the standing low-risk grant. Returns True if one was removed."""
    pid = str(project_id or "").strip()
    if not pid:
        return False
    with _PROJECTS_LOCK:
        projects = _load()
        entry = projects.get(pid)
        if not entry or not isinstance(entry.get(_APPROVAL_KEY), dict):
            return False
        del entry[_APPROVAL_KEY]
        projects[pid] = entry
        _save(projects)
    return True


def delete_project(project_id: str) -> bool:
    """Forget a project (does not touch the folder or its files). Returns True if one was removed."""
    pid = str(project_id or "").strip()
    if not pid:
        return False
    with _PROJECTS_LOCK:
        projects = _load()
        if pid not in projects:
            return False
        del projects[pid]
        _save(projects)
    return True


__all__ = [
    "clear_project_low_risk_approval",
    "create_project",
    "delete_project",
    "get_project",
    "get_project_low_risk_approval",
    "list_projects",
    "normalize_color",
    "project_root",
    "projects_path",
    "set_project_color",
    "set_project_emoji",
    "set_project_low_risk_approval",
]
