"""The exact list of changes one run would make, worked out without writing anything.

The plan is what the person approves. Its digest binds the approval to these files, as they are now
(size, time and content), these new names and folders, and these list rows: a run whose folder no
longer gives the same plan refuses and asks the person to look again.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from core.routines import receipts
from core.routines.folders import admit
from core.routines.infer import file_values
from core.routines.model import RoutineError, render, render_folder, render_name, uses_receipts

_SHAPES = {
    "date": r"\d{4}-\d{2}-\d{2}", "doc_date": r"\d{4}-\d{2}-\d{2}",
    "year": r"\d{4}", "doc_year": r"\d{4}", "month": r"\d{2}", "doc_month": r"\d{2}", "day": r"\d{2}",
    "doc_amount": r"\d+\.\d{2}",
}


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def renamed_shape(pieces: list[dict[str, str]]) -> re.Pattern[str] | None:
    """What a name made by this rule looks like, so a file this routine already renamed in place is
    left alone instead of being renamed again.

    None when the rule has no fixed text and no value with a shape (the shop alone, say): every name
    would look done. Each file's own new name then tells, since it is the name the file already has."""
    if not pieces or not any("text" in piece or piece["token"] in _SHAPES for piece in pieces):
        return None
    parts = []
    for piece in pieces:
        if "text" in piece:
            parts.append(re.escape(piece["text"]))
        else:
            parts.append(_SHAPES.get(piece["token"], r".+"))
    return re.compile("^" + "".join(parts) + "$", re.S)


def _log_state(folder: Path, relative: str) -> dict[str, Any]:
    path = folder / relative
    if path.is_symlink() or not path.is_file():
        raise RoutineError("list_missing", f"{relative} is not in the folder any more, so nothing will be done.", 409)
    data = path.read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise RoutineError("list_too_big", f"{relative} is too big for a routine to add rows to.", 409)
    text = data.decode("utf-8-sig", errors="replace")
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter if text.strip() else ","
    except csv.Error:
        delimiter = ","
    return {"file": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "delimiter": delimiter,
            "ends_with_newline": (not data) or data.endswith((b"\n", b"\r"))}


_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def safe_cell(text: str) -> str:
    """One list cell as a routine writes it: on one line, and never read as a formula by a spreadsheet
    (a file named "=HYPERLINK(...).pdf" would otherwise put a live formula in the person's list)."""
    value = re.sub(r"\s+", " ", str(text)).strip()
    if value[:1] in ("=", "+", "-", "@") and value != "-" and not _PLAIN_NUMBER.match(value):
        value = "'" + value
    return value


def row_text(row: list[str], delimiter: str) -> str:
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter, lineterminator="\n").writerow(row)
    return out.getvalue()


def plan(routine: dict[str, Any]) -> dict[str, Any]:
    folder = admit(routine["folder"])
    types = set(routine["types"])
    log_relative = (routine.get("log") or {}).get("file") or ""
    candidates, other_types = [], 0
    for entry in sorted(os.scandir(folder), key=lambda item: item.name):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
            continue
        if entry.name == log_relative:
            continue
        if Path(entry.name).suffix.lower() in types:
            candidates.append(entry)
        else:
            other_types += 1
    result: dict[str, Any] = {"routine_id": routine["id"], "folder": str(folder), "operations": [], "needs_you": [],
                              "left_alone": {"other_types": other_types, "already_done": 0}, "stop": "", "log": None,
                              "receipts_here": receipts.readable_here()}
    shape = renamed_shape(routine["rename"]) if not routine["into"] else None
    if shape is not None:
        fresh = [entry for entry in candidates if not shape.match(Path(entry.name).stem)]
        result["left_alone"]["already_done"] = len(candidates) - len(fresh)
        candidates = fresh
    if len(candidates) > routine["max_files"]:
        result["stop"] = "too_many"
        result["count"] = len(candidates)
        return _sealed(result, routine)
    log_state = _log_state(folder, log_relative) if log_relative else None
    needs_receipts = uses_receipts(routine)
    targets: set[str] = set()
    for entry in candidates:
        source = Path(entry.path)
        stat = entry.stat(follow_symlinks=False)
        values = file_values(entry.name, stat.st_mtime_ns)
        if needs_receipts:
            values.update(receipts.read_values(source))
        try:
            new_name = render_name(routine["rename"], values, source.suffix) if routine["rename"] else entry.name
            parts = render_folder(routine["into"], values)
        except RoutineError as exc:
            result["needs_you"].append({"file": entry.name, "reason": exc.code})
            continue
        target_relative = "/".join([*parts, new_name])
        target = folder.joinpath(*parts, new_name)
        if target_relative == entry.name:
            result["left_alone"]["already_done"] += 1
            continue
        problem = ""
        walked = folder
        make = []
        for part in parts:
            walked = walked / part
            if walked.is_symlink() or (walked.exists() and not walked.is_dir()):
                problem = "bad_target"
                break
            if not walked.exists():
                make.append(walked.relative_to(folder).as_posix())
        if not problem and (target.exists() or target.is_symlink()):
            problem = "target_exists"
        if not problem and target_relative.lower() in targets:
            problem = "same_target"
        row = None
        if not problem and log_state is not None:
            row_values = dict(values, new_name=new_name, folder="/".join(parts))
            try:
                row = [safe_cell(render(column, row_values)) for column in routine["log"]["columns"]]
            except RoutineError as exc:
                problem = exc.code
        if problem:
            result["needs_you"].append({"file": entry.name, "reason": problem})
            continue
        targets.add(target_relative.lower())
        try:
            sha = _digest(source)
        except OSError:
            result["needs_you"].append({"file": entry.name, "reason": "unreadable"})
            continue
        operation = {"from": entry.name, "to": target_relative, "make": make, "size": stat.st_size,
                     "mtime_ns": stat.st_mtime_ns, "sha256": sha}
        if row is not None:
            operation["row"] = row
        result["operations"].append(operation)
    if log_state is not None:
        result["log"] = dict(log_state, rows=sum(1 for op in result["operations"] if "row" in op))
    return _sealed(result, routine)


def _sealed(result: dict[str, Any], routine: dict[str, Any]) -> dict[str, Any]:
    bound = {key: routine[key] for key in ("id", "folder", "types", "rename", "into", "log", "max_files")}
    payload = {"routine": bound, "operations": result["operations"], "stop": result["stop"],
               "log": result.get("log"), "needs_you": result["needs_you"]}
    result["digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return result


__all__ = ["plan", "renamed_shape", "row_text", "safe_cell"]
