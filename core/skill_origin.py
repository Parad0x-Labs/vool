"""Where an installed skill came from, recorded when it is installed and read when a turn is built.

A skill's ``allowed-tools`` may ADD tools to a turn's offer only when the person wrote that skill
themselves in VOOL, or approved its tools on the turn-on card for its exact bytes. Every other skill (a zip or chat import, a GitHub/Browse add-on, a Skill
Codex install, a pack VOOL ships, VOOL's own library) narrows the offer at most. Before this module
the decision read the folder a skill sat in: imports land in ``local-skills`` beside the person's
own skills, so an imported skill's ``allowed-tools`` seated ``workspace.write_file`` on a "save
this recipe" turn (red team t6) and ``profile.list`` + ``operator.find_notes`` beside a body
asking to send them to a web address (t14).

The record has two halves, both durable in ``<VOOL home>/config/skill_origins.json``:

- ``created``: the sha256 of every SKILL.md the person made in their own chat ("make me a skill
  that ..."): that chat lane adds to it (core.agent_runtime.fast_paths_skill), from the bytes
  ``skill.create`` just wrote for that message; so does a direct in-process ``create_skill`` call
  from VOOL's own code (``made_by_person=True``), never the ``skill.create`` tool.
- ``drafted``: the sha256 of every other SKILL.md ``skill.create`` wrote, whoever called it. A model
  can call ``skill.create`` in a turn that is reading a web page, an email or someone else's skill,
  so these bytes are not the person's words (audit H1, 2026-11-11): their ``allowed-tools`` add tools
  only once the person approved turning on this exact version on a card that listed those tools.
- ``installed``: per installed SKILL.md path, the origin decided at install time
  (``created_here`` only for ``created`` bytes the history does not mark imported, ``drafted`` for
  ``drafted`` bytes, with ``tools_confirmed`` true only when the person approved that exact install
  on its card; otherwise ``imported``, ``pack`` or ``installed``, the last being any other file on
  disk).

``may_add_tools`` widens only when the install record says ``created_here`` (or ``drafted`` with
``tools_confirmed``) AND the bytes on disk NOW are still those bytes: an import that later replaces the person's skill under
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
#: Written by ``skill.create`` outside the person's own chat lane (a model's tool call).
ORIGIN_DRAFTED = "drafted"
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
    ORIGIN_CREATED_HERE, ORIGIN_DRAFTED, ORIGIN_IMPORTED, ORIGIN_REGISTRY, ORIGIN_BUNDLED, ORIGIN_NATIVE,
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
        return {"created": {}, "drafted": {}, "installed": {}}
    if not isinstance(data, dict):
        return {"created": {}, "drafted": {}, "installed": {}}
    return {key: dict(data.get(key)) if isinstance(data.get(key), dict) else {}
            for key in ("created", "drafted", "installed")}


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


def _note(half: str, sha256: str, slug: str) -> None:
    digest = str(sha256 or "").strip().lower()
    if len(digest) != 64:
        return
    with _LOCK:
        data = _read()
        rows = data[half]
        rows[digest] = {"slug": str(slug or "")[:120], "at": int(time.time())}
        if len(rows) > _MAX_CREATED:
            for old in sorted(rows, key=lambda d: int((rows[d] or {}).get("at") or 0))[: len(rows) - _MAX_CREATED]:
                rows.pop(old, None)
        _write(data)


def note_created_here(sha256: str, *, slug: str = "") -> None:
    """The person made these exact bytes in their own chat ("make me a skill that ...").

    Called only by the chat lane that answers the person's own message, after ``skill.create``
    wrote the draft. A model's ``skill.create`` call is noted with ``note_drafted`` instead.
    """
    _note("created", sha256, slug)


def note_drafted(sha256: str, *, slug: str = "") -> None:
    """``skill.create`` wrote these exact bytes; who asked for them is not known (core.skill_tools)."""
    _note("drafted", sha256, slug)


def created_here_digest(sha256: str) -> bool:
    return str(sha256 or "").strip().lower() in _read()["created"]


def drafted_digest(sha256: str) -> bool:
    return str(sha256 or "").strip().lower() in _read()["drafted"]


def decide_install_origin(sha256: str, *, history_source: str = "", default: str = ORIGIN_INSTALLED) -> str:
    """The origin of bytes just installed: ``created_here`` only for bytes the person made in their
    own chat, ``drafted`` for other bytes ``skill.create`` wrote."""
    if str(history_source or "").strip().lower() == "import":
        return ORIGIN_IMPORTED
    if created_here_digest(sha256):
        return ORIGIN_CREATED_HERE
    if drafted_digest(sha256):
        return ORIGIN_DRAFTED
    return default if default in ORIGINS else ORIGIN_INSTALLED


def record_install(path: str | Path, *, origin: str, sha256: str = "", tools_confirmed: bool = False) -> None:
    """Durably record the origin of the SKILL.md now installed at ``path``.

    ``tools_confirmed`` is True only when the person approved turning on these exact bytes on a card
    that listed the tools they ask for (core.runtime_execution_tools._skill_install).
    """
    clean = origin if origin in ORIGINS else ORIGIN_INSTALLED
    digest = str(sha256 or "").strip().lower() or _digest_of(path)
    row: dict[str, Any] = {"origin": clean, "sha256": digest, "at": int(time.time())}
    if tools_confirmed:
        row["tools_confirmed"] = True
    with _LOCK:
        data = _read()
        data["installed"][_key(path)] = row
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
    if plugin_id.startswith("registry-") and _registry_record(plugin_id):
        # Decided by the signed install's lifecycle record, never by the folder name alone: a pack
        # someone names "registry-..." is not a Skill Codex package (audit L3).
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
    if recorded == ORIGIN_DRAFTED:
        # Still the bytes VOOL recorded at install; an edit outside VOOL since is some other file,
        # unless the bytes now are ones the person made in their own chat (as for created_here).
        current = _digest_of(resolved)
        if _history_source(resolved) != "import":
            if current and created_here_digest(current):
                return ORIGIN_CREATED_HERE
            if current == str(row.get("sha256") or ""):
                return ORIGIN_DRAFTED
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


#: What a person is told when a skill drafted by ``skill.create`` (not in their own chat) is turned
#: on without approving its exact version on a card that listed its tools.
TOOLS_NOT_CONFIRMED = (
    "It was not made from your own words in this chat, and turning it on was not tied to this exact "
    "version, so the tools it lists are not added: it works only with the tools VOOL already offers "
    "for your request."
)


def _registry_record(plugin_id: str) -> bool:
    try:
        from core.plugin_lifecycle import record_for

        return record_for(plugin_id) is not None
    except Exception:
        return False


def may_add_tools(skill: Any) -> bool:
    """Whether a matched skill's ``allowed-tools`` may seat tools the turn would not otherwise get.

    The person's own skill (made in their chat), or a drafted one whose exact bytes the person
    approved on a card listing its tools. Everything else only narrows.
    """
    origin = origin_of(skill)
    if origin == ORIGIN_CREATED_HERE:
        return True
    if origin != ORIGIN_DRAFTED:
        return False
    path = str(getattr(skill, "path", "") or "")
    row = install_record(path)
    return bool(row.get("tools_confirmed")) and _digest_of(path) == str(row.get("sha256") or "")


__all__ = [
    "ORIGINS",
    "ORIGIN_BUNDLED",
    "ORIGIN_CREATED_HERE",
    "ORIGIN_DRAFTED",
    "ORIGIN_IMPORTED",
    "ORIGIN_INSTALLED",
    "ORIGIN_MCP",
    "ORIGIN_NATIVE",
    "ORIGIN_PACK",
    "ORIGIN_REGISTRY",
    "TOOLS_NOT_ADDED",
    "TOOLS_NOT_CONFIRMED",
    "TYPED_KEYS_IGNORED",
    "created_here_digest",
    "decide_install_origin",
    "drafted_digest",
    "install_record",
    "may_add_tools",
    "note_created_here",
    "note_drafted",
    "origin_of",
    "record_install",
]
