"""A chat that names its project works in that project.

Measured on 1cbf433 (cloud tool re-audit, 2026-10-29, coding W07/W09/S1/B1): with a folder
``~/Projects/garden-planner`` on the machine, "show me what's in plan.py in the garden planner
project" answered "There is no file at `plan.py`, and no file of that name elsewhere in this
project". The chat was bound to no project, so the turn ran in the default workspace, and the
project the person named was never looked up. Every follow-up ("now explain what it does", "run
it") stayed in the wrong folder too.

This module reads the project a message names and finds it: a project already registered under
that name first, then one folder of exactly that name in the places people keep projects. It never
guesses between two candidates, never walks the disk, and never moves a chat that is already bound
to a project -- a chat in project A naming project B stays in A, because project folders are the
isolation boundary between chats (see `core.runtime_execution_tools._machine_find_folder`).
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# "in the garden planner project", "from my garden-planner project", "the project called Garden Planner".
_NAMED_PROJECT_RES = (
    re.compile(
        r"\b(?:in|inside|from|of|for|on|at|to|into)\s+(?:my|the|our|this|that)\s+"
        r"(?P<name>[^\s,.;:!?\"'`][^,.;:!?\"'`\n]{0,48}?)\s+project\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in|inside|from|of|for|on|at|to|into)\s+"
        r"(?P<name>[^\s,.;:!?\"'`][^,.;:!?\"'`\n]{0,48}?)\s+project\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:my|the|our)\s+(?:little\s+|new\s+|old\s+)?(?P<name>[^\s,.;:!?\"'`][^,.;:!?\"'`\n]{0,48}?)\s+project\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bproject\s+(?:called|named)\s+[\"'“‘]?(?P<name>[^\s,.;:!?\"'`”’][^,.;:!?\"'`”’\n]{0,48}?)[\"'”’]?"
        r"(?=\s+(?:in|on|at|under|inside|from)\b|\s*[,.;:!?]|\s*$)",
        re.IGNORECASE,
    ),
)
# Words a name never starts with: "in the same project", "for this whole project".
_NOT_A_NAME = frozenset({
    "a", "an", "any", "current", "each", "every", "my", "new", "other", "same", "that", "the", "this",
    "whole", "your", "our", "one", "open", "vool", "coding", "code",
})
# A name with one of these inside it is a phrase that ran past the name ("the bug in the same project").
_FUNCTION_WORDS = frozenset({"in", "on", "at", "of", "for", "from", "to", "into", "the", "same", "this", "that", "a", "an"})
# Where people keep projects, relative to home. Searched one level deep, never recursively.
_PROJECT_HOMES = (
    "Projects", "projects", "Project", "Code", "code", "dev", "Dev", "Developer", "src", "repos", "Repos",
    "workspace", "Workspace", "git", "GitHub", "Documents", "Documents/Projects", "Documents/Code",
    "Desktop", "",
)


@dataclass(frozen=True)
class NamedProject:
    name: str
    root: str
    project_id: str = ""
    registered: bool = False


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _compact(text: str) -> str:
    """A name as its letters and digits only: "Garden Planner" == "garden-planner" == "garden_planner"."""
    return re.sub(r"[\W_]+", "", _fold(text))


def named_project_reference(text: str) -> str:
    """The project name the message names, or ''."""
    raw = " ".join(str(text or "").split())
    for pattern in _NAMED_PROJECT_RES:
        for match in pattern.finditer(raw):
            name = " ".join(match.group("name").split()).strip(" -")
            words = name.lower().split()
            if not words or words[0] in _NOT_A_NAME or len(words) > 5 or _FUNCTION_WORDS & set(words):
                continue
            if not _compact(name):
                continue
            return name
    return ""


# The person's own standard folders are places, not projects: "my Documents project" does not make
# Documents a project boundary.
_STANDARD_FOLDERS = frozenset({
    "applications", "desktop", "documents", "downloads", "library", "movies", "music", "pictures",
    "public", "videos", "templates", "projects", "code", "dev", "developer", "src", "repos", "workspace",
})


def _folder_candidates(name: str, home: str) -> list[str]:
    wanted = _compact(name)
    if wanted in _STANDARD_FOLDERS:
        return []
    found: list[str] = []
    for base in _PROJECT_HOMES:
        folder = os.path.join(home, base) if base else home
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                        continue
                    if _compact(entry.name) == wanted:
                        real = os.path.realpath(entry.path)
                        if real not in found:
                            found.append(real)
        except OSError:
            continue
    return found


def resolve_named_project(name: str, *, home: str | None = None) -> tuple[NamedProject | None, str]:
    """(project, '') for one unambiguous match, else (None, reason).

    reason is "not_found" or "ambiguous". A registered project wins over a folder of the same name.
    """
    clean = " ".join(str(name or "").split()).strip()
    if not clean:
        return None, "not_found"
    from core import project_store

    wanted = _compact(clean)
    registered = [
        p for p in project_store.list_projects()
        if _compact(str(p.get("name") or "")) == wanted
        or _compact(os.path.basename(str(p.get("root") or "").rstrip(os.sep))) == wanted
    ]
    if len(registered) > 1:
        return None, "ambiguous"
    if registered:
        project = registered[0]
        root = str(project_store.project_root(str(project.get("id") or "")) or "")
        if root:
            return NamedProject(name=str(project.get("name") or clean), root=root,
                                project_id=str(project.get("id") or ""), registered=True), ""
    base = os.path.realpath(home or os.path.expanduser("~"))
    folders = _folder_candidates(clean, base)
    if len(folders) > 1:
        return None, "ambiguous"
    if not folders:
        return None, "not_found"
    return NamedProject(name=os.path.basename(folders[0]), root=folders[0]), ""


def bind_chat_to_named_project(session_id: str, user_text: str) -> dict[str, Any] | None:
    """Bind an UNBOUND chat to the project its message names. None when it names none or it cannot
    be found unambiguously. A folder that is not a project yet is registered as one, under its own
    folder name, exactly as Settings would."""
    reference = named_project_reference(user_text)
    if not reference:
        return None
    found, _reason = resolve_named_project(reference)
    if found is None:
        return None
    from core import project_store

    project_id = found.project_id
    if not found.registered:
        ok, created = project_store.create_project(_display_name(found.name), found.root)
        if not ok or not isinstance(created, dict):
            return None
        project_id = str(created.get("id") or "")
    root = str(project_store.project_root(project_id) or "")
    if not project_id or not root:
        return None
    persisted = False
    try:
        from core.memory.entries import set_session_meta

        set_session_meta(session_id, project_id=project_id)
        persisted = True
    except Exception:
        # A chat with no saved row yet (a private chat) still gets this turn in the project.
        persisted = False
    return {"project_id": project_id, "root": root, "name": found.name, "persisted": persisted}


def _display_name(folder_name: str) -> str:
    words = re.split(r"[-_\s]+", str(folder_name or "").strip())
    return " ".join(w[:1].upper() + w[1:] for w in words if w) or str(folder_name or "")


__all__ = ["NamedProject", "bind_chat_to_named_project", "named_project_reference", "resolve_named_project"]
