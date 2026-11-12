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
_PROJECTS_LOCK = threading.RLock()
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
        if isinstance(item, dict) and item.get("id") and (item.get("root") or item.get("local_context_imports")):
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
    projects = [row for row in _load().values() if row.get("root") and _metadata_visible(row)]
    for entry in projects:
        entry["exists"] = os.path.isdir(str(entry.get("root") or ""))
    projects.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return projects


def get_project(project_id: str) -> dict[str, Any] | None:
    row = _load().get(str(project_id or "").strip())
    if row and row.get("root") and _metadata_visible(row):
        return row
    return None


def project_root(project_id: str) -> str | None:
    """The bound folder for a project, only if it still exists as a directory (else None)."""
    project = get_project(project_id)
    if not project:
        return None
    root = str(project.get("root") or "")
    return root if root and os.path.isdir(root) else None


# Folders that hold a whole computer's worth of files: binding VOOL to one of them is the same as
# binding it to the whole disk. Compared by real path, so a symlink spelling cannot slip past.
_SYSTEM_ROOTS = (
    "/Applications", "/Library", "/System", "/Users", "/Volumes", "/bin", "/boot", "/dev", "/etc",
    "/home", "/lib", "/opt", "/private", "/proc", "/sbin", "/sys", "/tmp", "/usr", "/var",
)


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def _too_broad_root(resolved: str) -> str:
    """A plain refusal when ``resolved`` is the home folder, the folder holding every home, or a system folder; else ''."""
    home = _real(os.path.expanduser("~"))
    if home and home not in {"~", os.sep} and resolved == home:
        return "that is your whole home folder. Pick one folder inside it, like Documents."
    if home and home not in {"~", os.sep} and resolved == _real(os.path.dirname(home)):
        return "that folder holds every user's files. Pick one folder inside your home folder, like Documents."
    if home and resolved == _real(os.path.join(home, "Library")):
        return "that is a system folder. Pick a normal folder, like Documents."
    if any(resolved == _real(p) for p in _SYSTEM_ROOTS):
        return "that is a system folder. Pick a normal folder, like Documents."
    return ""


def validate_project_root(root: str) -> tuple[bool, str]:
    """The shared folder boundary for project creation and migration preview."""
    raw = str(root or "").strip()
    if not raw:
        return False, "a project folder is required. Type its path or choose one."
    if not raw.startswith("~") and not os.path.isabs(raw):
        return False, "type the whole folder path, starting with / or ~ (for example ~/Documents)."
    resolved = _normalize_root(raw)
    if Path(resolved).parent == Path(resolved):
        return False, "pick one folder, not the whole filesystem."
    refusal = _too_broad_root(resolved)
    if refusal:
        return False, refusal
    if not os.path.exists(resolved):
        return False, f"that folder does not exist: {resolved}. Check the spelling."
    if not os.path.isdir(resolved):
        return False, f"that is a file, and a project must be a folder: {resolved}."
    return True, resolved


def _create_project(name: str, root: str, *, _import: dict | None = None) -> tuple[bool, Any]:
    """Register a folder as a project. Returns (True, project) or (False, error_message).

    Idempotent by real path: adding the same folder twice returns the existing project (name refreshed).
    The root must be an existing absolute directory (or start with ``~``). A relative path is refused
    instead of being resolved against the server's own working directory (which would bind VOOL to its
    install folder), and so are the filesystem root, the whole home folder, the folder that holds every user's home, and the
    operating system's own folders. Error messages are plain sentences that start in
    lower case (the callers prefix them) and name the resolved path where it helps.
    """
    valid, resolved = validate_project_root(root)
    if not valid:
        return False, resolved
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
        if existing.get("import_origin"):
            entry["import_origin"] = existing["import_origin"]
        elif _import:
            from core.chat_import.ownership import origin

            entry["import_origin"] = origin(payload=_project_import_payload(entry), **_import)
        if existing.get("local_context_imports"):
            entry["local_context_imports"] = existing["local_context_imports"]
        projects[pid] = entry
        _save(projects)
    return True, {**entry, "exists": True}


def import_project(name: str, root: str, *, batch_id: str, session_id: str, source: str) -> dict[str, Any]:
    """Reuse a resident project unchanged, or register a reversibly owned folder."""
    from core.memory.revocation import writing
    from core.secret_redaction import contains_secret

    valid, resolved = validate_project_root(root)
    if not valid:
        raise ValueError(resolved)
    if contains_secret(name) or contains_secret(resolved):
        raise ValueError("project metadata contains a secret")
    pid = _project_id_for(resolved)
    with hold_project_context(pid), writing({"name": name, "root": resolved}, session_id), _PROJECTS_LOCK:
        projects = _load()
        if pid in projects:
            return {**projects[pid], "import_created": False}
        ok, result = create_project(name, resolved, _import={
            "batch_id": batch_id, "session_id": session_id, "source": source})
        if not ok:
            raise ValueError(result)
        return {**result, "import_created": True}


def imported_projects(batch_id: str, session_id: str) -> list[dict[str, Any]]:
    """Inventory source-created and shared metadata without claiming native ownership."""
    from core.chat_import.ownership import belongs, keyed_digest, unchanged

    result = []
    with _PROJECTS_LOCK:
        for row in _load().values():
            if not row.get("root"):
                continue
            created = belongs(row.get("import_origin"), batch_id, session_id)
            guard = next((item for item in row.get("local_context_imports", [])
                          if item.get("batch_id") == batch_id and item.get("session_id") == session_id), None)
            if not created and guard is None:
                continue
            payload = _project_import_payload(row)
            pristine = (unchanged(payload, row["import_origin"]) if created else
                        guard.get("metadata_digest") == keyed_digest(payload))
            result.append({"id": row["id"], "payload": payload,
                           "unchanged": pristine, "import_created": created})
    return result


def _project_import_payload(row: dict) -> dict:
    return {key: row.get(key) for key in ("name", "root", "emoji", "color")}


def purge_imported_projects(batch_id: str, session_id: str, *, token: str = "") -> int:
    """Undo owned metadata, or erase matching fields from source-linked native rows."""
    from core.chat_import.ownership import keyed_digest
    from core.memory.carriers import carries

    with _PROJECTS_LOCK:
        rows = imported_projects(batch_id, session_id)
        projects = _load()
        count = 0
        for row in rows:
            if not token and not row["import_created"]:
                continue
            if not token:
                if not row["unchanged"]:
                    raise ValueError("imported project changed")
                guard = projects[row["id"]].get("local_context_imports")
                if guard:
                    projects[row["id"]] = {"id": row["id"], "local_context_imports": guard}
                else:
                    del projects[row["id"]]
                count += 1
                continue
            if not carries(row["payload"], token):
                continue
            entry = projects[row["id"]]
            if carries({"root": entry.get("root")}, token):
                entry = {key: value for key, value in entry.items()
                         if key in {"id", "local_context_imports", "import_origin"}}
                projects[row["id"]] = entry
            else:
                for key in ("name", "emoji", "color"):
                    if carries({key: entry.get(key)}, token):
                        entry[key] = ""
            # Canonical erasure is the new pristine metadata state. Later
            # native edits still diverge and block undo; no withdrawn copy remains.
            stamp = keyed_digest(_project_import_payload(entry))
            if entry.get("import_origin"):
                entry["import_origin"]["content_hash"] = stamp
            for guard in entry.get("local_context_imports", []):
                guard["metadata_digest"] = stamp
            count += 1
        if count:
            _save(projects)
        return count


_EMOJI_MAX = 8   # a couple of emoji code points (some emoji are multi-codepoint), never a long string
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")   # strict #rrggbb only — never arbitrary CSS


def normalize_color(color: str) -> str:
    """A stored colour is either '' (none) or a lowercase #rrggbb; anything else collapses to ''."""
    raw = str(color or "").strip().lower()
    return raw if _HEX_COLOR.match(raw) else ""


def _set_project_marker(project_id: str, key: str, value: str) -> bool:
    pid = str(project_id or "").strip()
    if not pid:
        return False
    try:
        with _metadata_write(pid, {key: value}):
            projects = _load()
            entry = projects.get(pid)
            if not entry or not entry.get("root"):
                return False
            entry[key] = value
            _save(projects)
        return True
    except RuntimeError:
        return False


def set_project_emoji(project_id: str, emoji: str) -> bool:
    """Update the marker through the same source erasure fence as the name."""
    clean = "".join(str(emoji or "").split())[:_EMOJI_MAX]
    return _set_project_marker(project_id, "emoji", clean)


def set_project_color(project_id: str, color: str) -> bool:
    """Update the accent through the same source erasure fence as the name."""
    return _set_project_marker(project_id, "color", normalize_color(color))


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
        guard = projects[pid].get("local_context_imports")
        if guard:
            projects[pid] = {"id": pid, "local_context_imports": guard}
        else:
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


@contextlib.contextmanager
def hold_project_context(project_id: str):
    """Serialize context changes with served project turns, across local processes.

    Project context is shared. A migration must not add local source instructions
    to a cloud turn that already selected its privacy policy. The canonical FIFO
    gate supplies thread reentrancy; the payload-free file lock covers daemons.
    """
    from core.session_turn_gate import hold_session_turn_gate

    if not project_id:
        yield
        return
    key = hashlib.sha256(project_id.encode()).hexdigest()
    with hold_session_turn_gate("project-context:" + key):
        held = getattr(_PROJECT_CONTEXT_LOCAL, "held", set())
        if key in held:
            yield
            return
        folder = data_path("project-context-locks")
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / key).open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.read(1) == b"":
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            _PROJECT_CONTEXT_LOCAL.held = held | {key}
            try:
                yield
            finally:
                _PROJECT_CONTEXT_LOCAL.held = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


_PROJECT_CONTEXT_LOCAL = threading.local()


def local_context_required(project_id: str) -> bool:
    return bool((_load().get(project_id) or {}).get("local_context_imports"))


def add_import_privacy(project_id: str, batch_id: str, session_id: str) -> None:
    """Install the privacy ceiling before shared context is written."""
    prior_work = project_native_work(project_id)
    with _PROJECTS_LOCK:
        projects = _load()
        row = projects[project_id]
        guards = list(row.get("local_context_imports") or [])
        marker = {"batch_id": batch_id, "session_id": session_id, "prior_work": prior_work, "metadata_digest": _metadata_digest(row)}
        if not any(g.get("batch_id") == batch_id and g.get("session_id") == session_id for g in guards):
            guards.append(marker)
        row["local_context_imports"] = guards
        _save(projects)


def remove_import_privacy(batch_id: str, session_id: str) -> None:
    """Undo only after every source payload has been removed."""
    with _PROJECTS_LOCK:
        projects = _load()
        for pid, row in list(projects.items()):
            guards = [g for g in row.get("local_context_imports", [])
                      if g.get("batch_id") != batch_id or g.get("session_id") != session_id]
            if guards:
                row["local_context_imports"] = guards
            else:
                row.pop("local_context_imports", None)
                if not row.get("root"):
                    del projects[pid]
        _save(projects)


def project_native_work(project_id: str, *, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    """Keyed watermarks distinguish resident work from later native turns."""
    from core.chat_import.ownership import keyed_digest
    from core.context_namespace import list_chat_namespaces
    from core.persistent_memory import recent_conversation_events
    from storage.dialogue_memory import recent_dialogue_turns

    work = {}
    for namespace in list_chat_namespaces(limit=None):
        if namespace.project_id != project_id or namespace.chat_id in exclude:
            continue
        payload = [recent_conversation_events(namespace.chat_id, limit=1),
                   recent_dialogue_turns(namespace.chat_id, limit=1)]
        if any(payload):
            work[namespace.chat_id] = keyed_digest(payload)
    return work


@contextlib.contextmanager
def _metadata_write(project_id: str, payload: dict):
    """Order project gate, public erasure fences, then metadata lock."""
    from core.memory.revocation import writing

    with hold_project_context(project_id), contextlib.ExitStack() as fences:
        entry = _load().get(project_id) or {}
        source_sessions = {row.get("session_id") for row in entry.get("local_context_imports", [])}
        source_sessions.add((entry.get("import_origin") or {}).get("session_id"))
        for sid in sorted(session for session in source_sessions if session):
            fences.enter_context(writing(payload, sid))
        with _PROJECTS_LOCK:
            yield


def create_project(name: str, root: str, *, _import: dict | None = None) -> tuple[bool, Any]:
    """Create or rename a project without restoring withdrawn source metadata."""
    valid, resolved = validate_project_root(root)
    if not valid:
        return False, resolved
    try:
        with _metadata_write(_project_id_for(resolved), {"name": str(name or ""), "root": resolved}):
            return _create_project(name, resolved, _import=_import)
    except RuntimeError:
        from core.i18n import catalog_for

        return False, catalog_for("en").text("chatimport.error.generic")


def _metadata_digest(row: dict) -> str:
    from core.chat_import.ownership import keyed_digest

    return keyed_digest(_project_import_payload(row))


def _metadata_visible(row: dict) -> bool:
    from core.chat_import.ownership import visible
    from core.memory.revocation import authorize_payload

    payload = _project_import_payload(row)
    if not visible(payload, row.get("import_origin")):
        return False
    try:
        return all(authorize_payload(payload, str(guard["session_id"]))
                   for guard in row.get("local_context_imports", []))
    except (RuntimeError, KeyError):
        return False
