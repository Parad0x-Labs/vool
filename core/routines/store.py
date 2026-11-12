"""Routines saved as private skills: SKILL.md (what it does, in words) beside routine.json (how).

They live in their own "routines" pack in the skills tree, so the skill machinery drafts, validates,
installs and versions them like any skill: every save is a version, and a rollback restores both the
words and the routine exactly. The skill text never holds the folder path. It tells the model that the
routine runs only from My routines, with the person's approval, and that it must not do the steps itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from core.routines.model import RoutineError, valid_id, validate

PACK = "routines"
LABELS = {
    "date": "file date", "year": "year", "month": "month", "day": "day", "name": "original name",
    "doc_date": "receipt date", "doc_year": "receipt year", "doc_month": "receipt month", "doc_shop": "shop",
    "doc_amount": "amount", "new_name": "new file name", "folder": "folder",
}


def _data() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "routines"


def _pack_dir() -> Path:
    from core.skill_tools import plugins_root

    return plugins_root() / "plugins" / PACK


def _ensure_pack() -> None:
    manifest = _pack_dir() / ".codex-plugin" / "plugin.json"
    if manifest.is_file():
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "name": PACK, "version": "0.1.0", "description": "Routines the person taught VOOL.", "skills": "./skills/",
        "interface": {"displayName": "Routines", "shortDescription": "Routines you taught VOOL."},
        "vool": {"origin": "user"},
    }, indent=2) + "\n", encoding="utf-8")


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


#: What a routine's skill is called: the person's name for it after this (a skill name takes no colon).
SKILL_PREFIX = "Routine - "


def skill_name(routine: dict[str, Any]) -> str:
    return SKILL_PREFIX + routine["name"]


def rule_words(pieces: list[dict[str, str]]) -> str:
    return "".join(piece["text"] if "text" in piece else "[" + LABELS[piece["token"]] + "]" for piece in pieces)


def describe(routine: dict[str, Any]) -> str:
    kinds = ", ".join(routine["types"])
    parts = []
    if routine["rename"]:
        parts.append(f"renames each new {kinds} file in the folder the person chose to \"{rule_words(routine['rename'])}\"")
    else:
        parts.append(f"takes each new {kinds} file in the folder the person chose")
    if routine["into"]:
        parts.append(f"moves it into \"{rule_words(routine['into'])}\" inside that folder")
    if routine.get("log"):
        columns = "; ".join(rule_words(column) for column in routine["log"]["columns"])
        parts.append(f"adds a row to {routine['log']['file']} ({columns})")
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."


def _yaml(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def skill_markdown(routine: dict[str, Any]) -> str:
    name = routine["name"]
    return (
        "---\n"
        f"name: {_yaml(skill_name(routine))}\n"
        f"description: {_yaml('A routine the person taught VOOL (' + name + '). It runs only from My routines, after the person approves the exact changes.')}\n"
        "local-only: true\n"
        "---\n\n"
        f"\"{name}\" is one of the person's routines, saved in My routines. It {describe(routine)}\n\n"
        "You cannot run this routine, and you must not do its steps yourself with file tools. When the person asks "
        f"to run it, tell them to open My routines from the Home menu and choose Run next to \"{name}\": VOOL shows "
        "the exact changes first and does nothing until they approve. A run never replaces or deletes a file.\n"
    )


def _skill_dirs() -> list[Path]:
    root = _pack_dir() / "skills"
    try:
        return sorted(path for path in root.iterdir() if (path / "routine.json").is_file() and (path / "SKILL.md").is_file())
    except OSError:
        return []


def _head(skill_dir: Path) -> int:
    from core.skill_tools import _read_history

    return int(_read_history(skill_dir).get("head_version") or 0)


def all_routines() -> list[dict[str, Any]]:
    out = []
    for skill_dir in _skill_dirs():
        try:
            routine = validate(json.loads((skill_dir / "routine.json").read_text(encoding="utf-8")), require_folder=False)
        except (OSError, ValueError, RoutineError):
            continue
        routine["version"] = _head(skill_dir)
        out.append(routine)
    return sorted(out, key=lambda item: item["name"].casefold())


def _find(routine_id: str) -> tuple[dict[str, Any], Path]:
    if not valid_id(routine_id):
        raise RoutineError("not_found", "That routine is not there any more.", 404)
    for skill_dir in _skill_dirs():
        try:
            routine = validate(json.loads((skill_dir / "routine.json").read_text(encoding="utf-8")), require_folder=False)
        except (OSError, ValueError, RoutineError):
            continue
        if routine["id"] == routine_id:
            routine["version"] = _head(skill_dir)
            return routine, skill_dir
    raise RoutineError("not_found", "That routine is not there any more.", 404)


def get(routine_id: str) -> dict[str, Any]:
    return _find(routine_id)[0]


def save(raw: dict[str, Any]) -> dict[str, Any]:
    """Install a new routine or a new version of one; RoutineError with a plain reason when it cannot."""
    from core.routines.folders import admit
    from core.skill_tools import _slug, install_skill, remove_skill

    routine = validate(dict(raw), require_folder=True)
    routine["folder"] = str(admit(routine["folder"]))
    previous_dir = None
    try:
        _previous, previous_dir = _find(routine["id"])
    except RoutineError:
        previous_dir = None
    for other in all_routines():
        if other["id"] != routine["id"] and other["name"].casefold() == routine["name"].casefold():
            raise RoutineError("name_taken", "You already have a routine with that name. Choose another name.", 409)
    slug = _slug(skill_name(routine))
    target_dir = _pack_dir() / "skills" / slug
    if previous_dir is None and target_dir.exists():
        raise RoutineError("name_taken", "You already have a routine with that name. Choose another name.", 409)
    from core.skill_tools import _installed_skill_dir_by_name

    elsewhere = _installed_skill_dir_by_name(skill_name(routine))
    if elsewhere is not None and elsewhere.parent.parent.name != PACK:
        raise RoutineError("name_taken", "A skill with that name already exists. Choose another name.", 409)
    _ensure_pack()
    markdown = skill_markdown(routine).encode("utf-8")
    staging = _data() / "staging" / routine["id"]
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _atomic(staging / "SKILL.md", markdown)
        result = install_skill(str(staging / "SKILL.md"), plugin_id=PACK, overwrite=target_dir.exists(),
                               expected_sha256=hashlib.sha256(markdown).hexdigest(), origin="routine")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if result.get("status") != "ok":
        # The skill check's own reason (a word in the name it refuses, say) is the useful one.
        raise RoutineError("save_failed", str(result.get("reason") or "The routine could not be saved. Try again."), 500)
    skill_dir = Path(result["path"]).parent
    record = json.dumps({key: routine[key] for key in ("schema", "id", "name", "folder", "types", "rename", "into",
                                                        "log", "max_files", "schedule")},
                        ensure_ascii=False, indent=1).encode("utf-8")
    _atomic(skill_dir / "routine.json", record)
    _atomic(skill_dir / "versions" / f"routine-v{int(result['version'])}.json", record)
    if previous_dir is not None and previous_dir != skill_dir:
        # Renamed: the routine now lives under its new name; the old one leaves the skills tree.
        (previous_dir / "routine.json").unlink(missing_ok=True)
        remove_skill(previous_dir.name, scope="global")
    routine["version"] = int(result["version"])
    return routine


def history(routine_id: str) -> list[dict[str, Any]]:
    from core.skill_tools import _read_history

    _routine, skill_dir = _find(routine_id)
    out = []
    for row in _read_history(skill_dir).get("versions") or []:
        version = int(row.get("version") or 0)
        if (skill_dir / "versions" / f"routine-v{version}.json").is_file():
            out.append({"version": version, "at": row.get("installed_at") or "",
                        "source": row.get("source") or ""})
    return sorted(out, key=lambda row: row["version"], reverse=True)


def rollback(routine_id: str, version: int) -> dict[str, Any]:
    from core.skill_tools import rollback_skill

    routine, skill_dir = _find(routine_id)
    if type(version) is not int:
        raise RoutineError("unknown_version", "That version is not in this routine's history.", 404)
    snapshot = skill_dir / "versions" / f"routine-v{version}.json"
    if not snapshot.is_file():
        raise RoutineError("unknown_version", "That version is not in this routine's history.", 404)
    restored = validate(json.loads(snapshot.read_text(encoding="utf-8")), require_folder=False)
    if restored["id"] != routine["id"] or restored["name"] != routine["name"]:
        raise RoutineError("unknown_version", "That version is not in this routine's history.", 404)
    result = rollback_skill(skill_name(routine), version=version)
    if result.get("status") != "ok":
        raise RoutineError("rollback_failed", "That version could not be brought back.", 500)
    data = snapshot.read_bytes()
    _atomic(skill_dir / "routine.json", data)
    _atomic(skill_dir / "versions" / f"routine-v{int(result['version'])}.json", data)
    restored["version"] = int(result["version"])
    return restored


def remove(routine_id: str) -> None:
    from core.skill_tools import remove_skill

    routine, skill_dir = _find(routine_id)
    result = remove_skill(skill_dir.name, scope="global")
    if result.get("status") != "ok":
        raise RoutineError("remove_failed", "The routine could not be removed. Try again.", 500)
    shutil.rmtree(_data() / "runs" / routine["id"], ignore_errors=True)


# --- staged routines: imported, not active, with no folder until the person chooses one -------------

def _staged_dir() -> Path:
    return _data() / "staged"


def stage(routine: dict[str, Any]) -> dict[str, Any]:
    clean = validate(dict(routine, folder=""), require_folder=False)
    clean["schedule"] = {"every": "", "time": "09:00", "weekday": 0}
    _atomic(_staged_dir() / f"{clean['id']}.json", json.dumps(clean, ensure_ascii=False).encode("utf-8"))
    return clean


def staged() -> list[dict[str, Any]]:
    out = []
    try:
        paths = sorted(_staged_dir().glob("rt-*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            out.append(validate(json.loads(path.read_text(encoding="utf-8")), require_folder=False))
        except (OSError, ValueError, RoutineError):
            continue
    return out


def unstage(routine_id: str) -> dict[str, Any]:
    if not valid_id(routine_id) or not re.match(r"^rt-[0-9a-f]{10}$", routine_id):
        raise RoutineError("not_found", "That routine is not there any more.", 404)
    path = _staged_dir() / f"{routine_id}.json"
    try:
        routine = validate(json.loads(path.read_text(encoding="utf-8")), require_folder=False)
    except (OSError, ValueError):
        raise RoutineError("not_found", "That routine is not there any more.", 404) from None
    path.unlink(missing_ok=True)
    return routine


__all__ = ["LABELS", "PACK", "all_routines", "describe", "get", "history", "remove", "rollback", "rule_words", "save",
           "skill_markdown", "skill_name", "stage", "staged", "unstage"]
