"""Where an installed skill came from, recorded when it is installed and read when a turn is built.

A skill's ``allowed-tools`` may ADD tools to a turn's offer only when the person wrote that skill
themselves in VOOL. Every other skill (a zip or chat import, a GitHub/Browse add-on, a Skill
Codex install, a pack VOOL ships, VOOL's own library) narrows the offer at most. Before this module
the decision read the folder a skill sat in: imports land in ``local-skills`` beside the person's
own skills, so an imported skill's ``allowed-tools`` seated ``workspace.write_file`` on a "save
this recipe" turn (red team t6) and ``profile.list`` + ``operator.find_notes`` beside a body
asking to send them to a web address (t14).

The record has two halves, both durable in ``<VOOL home>/config/skill_origins.json``:

- ``created``: the sha256 of every SKILL.md VOOL's own ``skill.create`` wrote for the person.
  Only the ``skill.create`` tool handler adds to it, from the bytes it just wrote.
- ``installed``: per installed SKILL.md path, the origin decided at install time
  (``created_here`` only when the installed bytes are bytes ``skill.create`` wrote and the
  history does not mark them imported; otherwise ``imported``, ``pack`` or ``installed``, the
  last being any other file on disk).

``may_add_tools`` widens only when the install record says ``created_here`` AND the bytes on disk
NOW are still bytes ``skill.create`` wrote: an import that later replaces the person's skill under
the same name (t9), or any edit outside VOOL, fails closed to narrow-only. A missing or unreadable
record fails closed too.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

ORIGIN_CREATED_HERE = "created_here"
ORIGIN_IMPORTED = "imported"
ORIGIN_REGISTRY = "registry"
ORIGIN_BUNDLED = "bundled"
ORIGIN_NATIVE = "native"
ORIGIN_PACK = "pack"
#: Shipped as a ``skill://`` resource by a configured MCP server.
ORIGIN_MCP = "mcp"
#: Installed from some other file on disk: not something the person wrote in VOOL.
ORIGIN_INSTALLED = "installed"
ORIGINS = (
    ORIGIN_CREATED_HERE, ORIGIN_IMPORTED, ORIGIN_REGISTRY, ORIGIN_BUNDLED, ORIGIN_NATIVE,
    ORIGIN_PACK, ORIGIN_MCP, ORIGIN_INSTALLED,
)

_LOCK = threading.Lock()
_MAX_CREATED = 5000


def _store_path() -> Path:
    from core.runtime_paths import active_vool_home

    return active_vool_home() / "config" / "skill_origins.json"


def _read() -> dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"created": {}, "installed": {}}
    if not isinstance(data, dict):
        return {"created": {}, "installed": {}}
    created = data.get("created") if isinstance(data.get("created"), dict) else {}
    installed = data.get("installed") if isinstance(data.get("installed"), dict) else {}
    return {"created": dict(created), "installed": dict(installed)}


def _write(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _key(path: str | Path) -> str:
    try:
        return str(Path(str(path)).expanduser().resolve())
    except OSError:
        return str(path)


def _digest_of(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest()
    except OSError:
        return ""


def note_created_here(sha256: str, *, slug: str = "") -> None:
    """``skill.create`` wrote these exact bytes for the person. Called only by that handler."""
    digest = str(sha256 or "").strip().lower()
    if len(digest) != 64:
        return
    with _LOCK:
        data = _read()
        created = data["created"]
        created[digest] = {"slug": str(slug or "")[:120], "at": int(time.time())}
        if len(created) > _MAX_CREATED:
            for old in sorted(created, key=lambda d: int((created[d] or {}).get("at") or 0))[
                : len(created) - _MAX_CREATED
            ]:
                created.pop(old, None)
        _write(data)


def created_here_digest(sha256: str) -> bool:
    return str(sha256 or "").strip().lower() in _read()["created"]


def decide_install_origin(sha256: str, *, history_source: str = "", default: str = ORIGIN_INSTALLED) -> str:
    """The origin of bytes just installed: ``created_here`` only for bytes ``skill.create`` wrote."""
    if str(history_source or "").strip().lower() == "import":
        return ORIGIN_IMPORTED
    if created_here_digest(sha256):
        return ORIGIN_CREATED_HERE
    return default if default in ORIGINS else ORIGIN_INSTALLED


def record_install(path: str | Path, *, origin: str, sha256: str = "") -> None:
    """Durably record the origin of the SKILL.md now installed at ``path``."""
    clean = origin if origin in ORIGINS else ORIGIN_INSTALLED
    digest = str(sha256 or "").strip().lower() or _digest_of(path)
    with _LOCK:
        data = _read()
        data["installed"][_key(path)] = {"origin": clean, "sha256": digest, "at": int(time.time())}
        _write(data)


def install_record(path: str | Path) -> dict[str, Any]:
    row = _read()["installed"].get(_key(path))
    return dict(row) if isinstance(row, dict) else {}


def _history_source(skill_md: Path) -> str:
    try:
        from core.skill_tools import VERSIONS_DIRNAME

        history = json.loads((skill_md.parent / VERSIONS_DIRNAME / "history.json").read_text(encoding="utf-8"))
        versions = list(history.get("versions") or [])
        head = int(history.get("head_version") or 0)
        for entry in versions:
            if int(entry.get("version") or 0) == head:
                return str(entry.get("source") or "")
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        pass
    return ""


def origin_of(skill: Any) -> str:
    """The origin of a loaded skill, from where VOOL itself put it and the install record."""
    path = str(getattr(skill, "path", "") or "")
    plugin_id = str(getattr(skill, "plugin_id", "") or "")
    # A typed contract renders with its source ("plugin:<id>") where a plain skill has its id.
    plugin_id = plugin_id.removeprefix("plugin:")
    if plugin_id.startswith("mcp:") or path.startswith("skill://"):
        return ORIGIN_MCP
    if not path:
        return ORIGIN_INSTALLED
    resolved = Path(_key(path))
    try:
        from core.native_skill_library import native_skills_root

        if resolved.is_relative_to(native_skills_root().resolve()):
            return ORIGIN_NATIVE
    except Exception:
        pass
    try:
        from core.plugin_catalog import bundled_plugin_dirs

        if any(resolved.is_relative_to(Path(b).resolve()) for b in bundled_plugin_dirs()):
            return ORIGIN_BUNDLED
    except Exception:
        pass
    if plugin_id.startswith("registry-"):
        return ORIGIN_REGISTRY
    if plugin_id.startswith("discover-"):
        return ORIGIN_IMPORTED
    row = install_record(resolved)
    recorded = str(row.get("origin") or "")
    if recorded == ORIGIN_CREATED_HERE:
        # The bytes on disk now must still be bytes skill.create wrote: a same-name import or an
        # edit outside VOOL since the install is not the person's own skill any more.
        current = _digest_of(resolved)
        if current and created_here_digest(current) and _history_source(resolved) != "import":
            return ORIGIN_CREATED_HERE
        return ORIGIN_IMPORTED if _history_source(resolved) == "import" else ORIGIN_INSTALLED
    if recorded in ORIGINS:
        return recorded
    return ORIGIN_IMPORTED if _history_source(resolved) == "import" else ORIGIN_INSTALLED


#: What a person is told when a skill they turn on lists tools it will not be given.
TOOLS_NOT_ADDED = (
    "It was not written here in VOOL, so the tools it lists are not added: it works only with "
    "the tools VOOL already offers for your request."
)
#: What a person is told when a skill they turn on carries automatic-start settings.
TYPED_KEYS_IGNORED = (
    "Its automatic settings (id, task-families and the like) are ignored: it is used only when "
    "your words ask for it."
)


def may_add_tools(skill: Any) -> bool:
    """Whether a matched skill's ``allowed-tools`` may seat tools the turn would not otherwise get."""
    return origin_of(skill) == ORIGIN_CREATED_HERE


__all__ = [
    "ORIGINS",
    "ORIGIN_BUNDLED",
    "ORIGIN_CREATED_HERE",
    "ORIGIN_IMPORTED",
    "ORIGIN_INSTALLED",
    "ORIGIN_MCP",
    "ORIGIN_NATIVE",
    "ORIGIN_PACK",
    "ORIGIN_REGISTRY",
    "TOOLS_NOT_ADDED",
    "TYPED_KEYS_IGNORED",
    "created_here_digest",
    "decide_install_origin",
    "install_record",
    "may_add_tools",
    "note_created_here",
    "origin_of",
    "record_install",
]
