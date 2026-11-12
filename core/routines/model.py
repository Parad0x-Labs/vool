"""The routine record (routine.json) and how its parts turn into names and folders.

A routine is deliberately small and typed: the file types it handles, how a file is renamed, which
folder inside the chosen folder it goes to, and optionally one row added to a CSV file in that folder.
Every name and folder is built from pieces: a fixed text the person typed, or a value VOOL reads
(the file's date, its original name, or a receipt's date, shop and amount). Nothing in a routine can
name a path outside its folder, run a program, delete a file or replace one.
"""
from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any

SCHEMA = "vool.routine/1"
MAX_FILES_LIMIT = 200
DEFAULT_MAX_FILES = 25

#: Values a piece can take. FILE_TOKENS are always known; RECEIPT_TOKENS need the document read
#: (untrusted documents are read only inside process confinement: the macOS sandbox, or bubblewrap on Linux).
FILE_TOKENS = ("date", "year", "month", "day", "name")
RECEIPT_TOKENS = ("doc_date", "doc_year", "doc_month", "doc_shop", "doc_amount")
#: Only a CSV column can carry these two: what the file ended up as.
RESULT_TOKENS = ("new_name", "folder")
NAME_TOKENS = FILE_TOKENS + RECEIPT_TOKENS
COLUMN_TOKENS = NAME_TOKENS + RESULT_TOKENS
SCHEDULE_EVERY = ("", "daily", "weekly")

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_TYPE = re.compile(r"^\.[a-z0-9]{1,8}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ID = re.compile(r"^rt-[0-9a-f]{10}$")
#: A routine's name: what core.skill_tools accepts as a skill's name, short enough for its prefix.
NAME_LIMIT = 50
_NAME = re.compile(r"[^\W_][\w '\u2019\-]{0,%d}" % (NAME_LIMIT - 1))


class RoutineError(Exception):
    """A plain refusal: ``code`` for the page to translate, ``message`` as the English fallback."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def new_id() -> str:
    return "rt-" + secrets.token_hex(5)


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def clean_value(value: Any, limit: int = 60) -> str:
    """A value read from a file or a document, made safe to use inside one file or folder name."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _FORBIDDEN.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:limit].strip(" .-")


def _text_piece(value: Any, *, allow_slash: bool) -> str:
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    if len(text) > 60:
        raise RoutineError("text_too_long", "A fixed text in a name can be at most 60 characters.")
    probe = text.replace("/", "") if allow_slash else text
    if _FORBIDDEN.search(probe):
        raise RoutineError("bad_text", 'A name cannot contain \\ / : * ? " < > | or control characters.')
    return text


def _pieces(raw: Any, tokens: tuple[str, ...], *, allow_slash: bool, limit: int = 12) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > limit:
        raise RoutineError("bad_rule", "That rule could not be read. Check it and try again.")
    pieces: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or len(item) != 1:
            raise RoutineError("bad_rule", "That rule could not be read. Check it and try again.")
        if "token" in item:
            if item["token"] not in tokens:
                raise RoutineError("bad_rule", "That rule uses a value this routine cannot read.")
            pieces.append({"token": item["token"]})
        elif "text" in item:
            text = _text_piece(item["text"], allow_slash=allow_slash)
            if text:
                if pieces and "text" in pieces[-1]:
                    pieces[-1] = {"text": pieces[-1]["text"] + text}
                else:
                    pieces.append({"text": text})
        else:
            raise RoutineError("bad_rule", "That rule could not be read. Check it and try again.")
    return pieces


def _log_file(raw: Any) -> str:
    text = str(raw or "").strip()
    parts = [part for part in text.split("/")]
    if (not text or len(text) > 120 or not text.lower().endswith(".csv") or text.startswith("/")
            or any(part in {"", ".", ".."} or _FORBIDDEN.search(part) for part in parts)):
        raise RoutineError("bad_log_file", "The list file must be a .csv file inside the routine's folder.")
    return text


def validate(raw: Any, *, require_folder: bool = True) -> dict[str, Any]:
    """The routine as stored, or RoutineError. Unknown fields are refused, never carried along."""
    if not isinstance(raw, dict):
        raise RoutineError("bad_routine", "That routine could not be read.")
    allowed = {"schema", "id", "name", "folder", "types", "rename", "into", "log", "max_files", "schedule"}
    if set(raw) - allowed:
        raise RoutineError("bad_routine", "That routine has parts this version does not know.")
    if raw.get("schema", SCHEMA) != SCHEMA:
        raise RoutineError("bad_routine", "That routine was made by a newer VOOL.")
    routine_id = raw.get("id") or new_id()
    if not valid_id(routine_id):
        raise RoutineError("bad_routine", "That routine could not be read.")
    name = unicodedata.normalize("NFC", str(raw.get("name") or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not _NAME.fullmatch(name):
        # The routine is kept as a skill named after it, and a skill's name takes these characters.
        raise RoutineError("bad_name", "Give the routine a name of up to 50 letters, numbers, spaces, hyphens or "
                                       "apostrophes.")
    folder = str(raw.get("folder") or "")
    if require_folder and not folder:
        raise RoutineError("no_folder", "Choose the folder this routine works in.")
    types = raw.get("types")
    if not isinstance(types, list) or not types or len(types) > 12:
        raise RoutineError("bad_types", "Choose at least one kind of file for this routine.")
    clean_types = sorted({str(value).strip().lower() for value in types})
    if not all(_TYPE.match(value) for value in clean_types):
        raise RoutineError("bad_types", "A file kind looks like .pdf or .jpg.")
    if ".csv" in clean_types:
        raise RoutineError("bad_types", "A routine cannot move .csv files; it can only add rows to one.")
    rename = _pieces(raw.get("rename"), NAME_TOKENS, allow_slash=False)
    into = _pieces(raw.get("into"), NAME_TOKENS, allow_slash=True)
    log = raw.get("log")
    clean_log = None
    if log is not None:
        if not isinstance(log, dict) or set(log) - {"file", "columns"}:
            raise RoutineError("bad_rule", "That rule could not be read. Check it and try again.")
        columns = log.get("columns")
        if not isinstance(columns, list) or not columns or len(columns) > 12:
            raise RoutineError("bad_columns", "A list row needs between 1 and 12 columns.")
        clean_columns = [_pieces(column, COLUMN_TOKENS, allow_slash=True, limit=8) for column in columns]
        if any(not column for column in clean_columns):
            raise RoutineError("bad_columns", "Every column in the list row needs a value.")
        clean_log = {"file": _log_file(log.get("file")), "columns": clean_columns}
    if not rename and not into:
        # A list row alone would be added again for the same files on every run: a routine always
        # renames or moves what it handles, so the next run can tell new files from finished ones.
        raise RoutineError("no_steps", "A routine needs to rename files or move them into a folder.")
    max_files = raw.get("max_files", DEFAULT_MAX_FILES)
    if type(max_files) is not int or not 1 <= max_files <= MAX_FILES_LIMIT:
        raise RoutineError("bad_max_files", f"A run can handle between 1 and {MAX_FILES_LIMIT} files.")
    schedule = raw.get("schedule") or {"every": "", "time": "09:00", "weekday": 0}
    if (not isinstance(schedule, dict) or set(schedule) - {"every", "time", "weekday"}
            or schedule.get("every", "") not in SCHEDULE_EVERY
            or not _TIME.match(str(schedule.get("time", "09:00")))
            or type(schedule.get("weekday", 0)) is not int or not 0 <= schedule.get("weekday", 0) <= 6):
        raise RoutineError("bad_schedule", "Choose a day and a time like 09:00.")
    return {
        "schema": SCHEMA,
        "id": routine_id,
        "name": name,
        "folder": folder,
        "types": clean_types,
        "rename": rename,
        "into": into,
        "log": clean_log,
        "max_files": max_files,
        "schedule": {"every": schedule.get("every", ""), "time": str(schedule.get("time", "09:00")),
                     "weekday": schedule.get("weekday", 0)},
    }


def uses_receipts(routine: dict[str, Any]) -> bool:
    pieces = list(routine.get("rename") or []) + list(routine.get("into") or [])
    for column in (routine.get("log") or {}).get("columns") or []:
        pieces.extend(column)
    return any(piece.get("token") in RECEIPT_TOKENS for piece in pieces)


def render(pieces: list[dict[str, str]], values: dict[str, str]) -> str:
    """The text a rule gives for one file; RoutineError naming the value when one is missing."""
    out = []
    for piece in pieces:
        if "text" in piece:
            out.append(piece["text"])
            continue
        value = values.get(piece["token"], "")
        if not value:
            raise RoutineError("missing_value:" + piece["token"], "A value this routine needs could not be read.")
        out.append(value)
    return "".join(out)


def render_name(pieces: list[dict[str, str]], values: dict[str, str], suffix: str) -> str:
    stem = re.sub(r"\s+", " ", render(pieces, values)).strip(" .")
    if not stem or _FORBIDDEN.search(stem):
        raise RoutineError("bad_new_name", "The new name came out empty or with characters a file name cannot have.")
    return (stem[:150] + suffix)


def render_folder(pieces: list[dict[str, str]], values: dict[str, str]) -> list[str]:
    if not pieces:
        return []
    parts = [re.sub(r"\s+", " ", part).strip(" .") for part in render(pieces, values).split("/")]
    if len(parts) > 4 or any(not part or part in {".", ".."} or _FORBIDDEN.search(part) for part in parts):
        raise RoutineError("bad_new_folder", "The folder came out empty or with characters a folder name cannot have.")
    return parts


__all__ = [
    "COLUMN_TOKENS", "DEFAULT_MAX_FILES", "FILE_TOKENS", "MAX_FILES_LIMIT", "NAME_LIMIT", "NAME_TOKENS", "RECEIPT_TOKENS",
    "RESULT_TOKENS", "SCHEMA", "RoutineError", "clean_value", "new_id", "render", "render_folder", "render_name",
    "uses_receipts", "valid_id", "validate",
]
