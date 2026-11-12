"""Sharing a routine as a small file, and bringing one in.

What leaves is the routine's rules and its words, never where it worked: no folder, no schedule, no
id, no versions and nothing it ever moved. Fixed words the routine carries (its name, the text in a new
name or folder, the list's file name) go through the same replacer the cloud preview uses: email
addresses, phone numbers, card and bank numbers, street addresses and the names of saved contacts
become placeholders such as [Email 1]. That is a best effort, so the person sees the file's exact
contents before it is written, and the file written is the one they saw (its checksum is checked).

A routine brought in is read strictly (two known members, small, the same checks as a saved routine),
its words are written again by VOOL from its rules, never taken from the file, and it stays inactive,
with no folder, until the person chooses one and saves it.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Any

from core.routines.model import RoutineError, validate

SHARE_FORMAT = "vool.routine-share/1"
MAX_SHARE_BYTES = 64 * 1024
MAX_MEMBER_BYTES = 32 * 1024
_MEMBERS = {"routine.json", "SKILL.md"}
_SHARED_FIELDS = ("name", "types", "rename", "into", "log", "max_files")


def _replacer(texts: list[str]) -> tuple[list[str], bool]:
    """The texts with private-looking details replaced, and whether contact names were checked."""
    from core.disclosure.replacer import replace_texts, replace_using_contacts

    try:
        return list(replace_using_contacts(texts).texts), True
    except Exception:
        # The contacts could not be read: replace what the text alone shows, and say names were not checked.
        return list(replace_texts(texts).texts), False


def _sanitised(routine: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The routine's shared rules with private-looking fixed words replaced, and what was done."""
    log = routine.get("log") or None
    texts: list[str] = [routine["name"]]
    slots: list[tuple[str, int, int]] = []  # (rule, column or -1, piece index)
    for rule in ("rename", "into"):
        for index, piece in enumerate(routine[rule]):
            if "text" in piece:
                slots.append((rule, -1, index))
                texts.append(piece["text"])
    if log:
        for column_index, column in enumerate(log["columns"]):
            for index, piece in enumerate(column):
                if "text" in piece:
                    slots.append(("log", column_index, index))
                    texts.append(piece["text"])
        texts.append(log["file"][: -len(".csv")])
    replaced, contacts_checked = _replacer(texts)
    changed = sum(1 for before, after in zip(texts, replaced, strict=True) if before != after)
    # A routine's name takes no brackets (it is a skill's name): keep the placeholder's words.
    name = re.sub(r"\s+", " ", replaced[0].replace("[", "").replace("]", "")).strip() or "Shared routine"
    rename = [dict(piece) for piece in routine["rename"]]
    into = [dict(piece) for piece in routine["into"]]
    columns = [[dict(piece) for piece in column] for column in (log or {}).get("columns") or []]
    for (rule, column_index, index), text in zip(slots, replaced[1:1 + len(slots)], strict=True):
        target = rename if rule == "rename" else into if rule == "into" else columns[column_index]
        target[index] = {"text": text}
    record: dict[str, Any] = {"format": SHARE_FORMAT, "name": name, "types": list(routine["types"]),
                              "rename": rename, "into": into, "log": None, "max_files": routine["max_files"]}
    if log:
        record["log"] = {"file": replaced[-1] + ".csv", "columns": columns}
    # What leaves must still be a routine VOOL can read back (a placeholder in a name is plain text).
    validate({key: record[key] for key in _SHARED_FIELDS}, require_folder=False)
    return record, {"replaced": changed, "contacts_checked": contacts_checked}


def shared_record(routine: dict[str, Any]) -> dict[str, Any]:
    """The rules that travel: what the routine does, nothing about where it ran."""
    return _sanitised(routine)[0]


def _files(record: dict[str, Any]) -> dict[str, str]:
    from core.routines.store import skill_markdown

    routine = validate({key: record[key] for key in _SHARED_FIELDS}, require_folder=False)
    return {
        "routine.json": json.dumps(record, ensure_ascii=False, indent=1),
        "SKILL.md": skill_markdown(dict(routine, folder="")),
    }


def _zip(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("routine.json", "SKILL.md"):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name].encode("utf-8"))
    return out.getvalue()


def preview(routine: dict[str, Any]) -> dict[str, Any]:
    """The exact text of both files, what stays on this computer, and the checksum of the file."""
    record, done = _sanitised(routine)
    files = _files(record)
    return {
        "files": files,
        "kept_here": ["folder", "schedule", "history", "runs"],
        "replaced": done["replaced"],
        "contacts_checked": done["contacts_checked"],
        "sha256": hashlib.sha256(_zip(files)).hexdigest(),
    }


def export(routine: dict[str, Any], sha256: str = "") -> bytes:
    """The file the person looked at; RoutineError when it is no longer that file."""
    data = _zip(preview(routine)["files"])
    if hashlib.sha256(data).hexdigest() != str(sha256 or ""):
        raise RoutineError("share_changed", "The routine changed since you looked. Look at the file again.", 409)
    return data


def read(data: bytes) -> dict[str, Any]:
    """The routine in a shared file, checked; RoutineError with a plain reason when it is not one."""
    refused = RoutineError("bad_share", "That file is not a routine shared from VOOL.")
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_SHARE_BYTES:
        raise refused
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or not set(names) <= _MEMBERS or "routine.json" not in names:
                raise refused
            info = archive.getinfo("routine.json")
            if info.file_size > MAX_MEMBER_BYTES or info.flag_bits & 0x1:
                raise refused
            raw = archive.read("routine.json")
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError, ValueError, NotImplementedError):
        raise refused from None
    if len(raw) > MAX_MEMBER_BYTES:
        raise refused
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise refused from None
    if not isinstance(record, dict) or record.get("format") != SHARE_FORMAT or set(record) - {"format", *_SHARED_FIELDS}:
        raise refused
    rules = {key: record[key] for key in _SHARED_FIELDS if key in record}
    # A fresh id, no folder and no schedule: what the sender's routine was here is not this one.
    return validate(dict(rules, folder=""), require_folder=False)


__all__ = ["MAX_SHARE_BYTES", "SHARE_FORMAT", "export", "preview", "read", "shared_record"]
