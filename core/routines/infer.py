"""From what a watch saw to a proposed routine the person can read and change.

Each file the person renamed or moved is one example. VOOL explains the new name and folder with values
it can read again next time (the file's date and original name, or a receipt's date, shop and amount)
and keeps the rest as fixed text. When the examples do not agree, or a part cannot be explained, the
proposal says so and the person decides; nothing is guessed silently.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.routines import receipts
from core.routines.model import DEFAULT_MAX_FILES, FILE_TOKENS, RECEIPT_TOKENS, RESULT_TOKENS, RoutineError, new_id

_DIGITS = re.compile(r"^\d+$")


def file_values(name: str, mtime_ns: int) -> dict[str, str]:
    when = datetime.fromtimestamp(mtime_ns / 1e9)
    return {"date": when.strftime("%Y-%m-%d"), "year": when.strftime("%Y"), "month": when.strftime("%m"),
            "day": when.strftime("%d"), "name": Path(name).stem}


def decompose(text: str, values: dict[str, str], order: tuple[str, ...]) -> list[dict[str, str]]:
    """``text`` as fixed text and values, longest value first, earlier tokens in ``order`` winning ties.

    A value made of digits only counts where it stands as its own number, so the month "11" is never
    found inside "2011" or "scan_0113".
    """
    pieces: list[dict[str, str]] = []
    buffer = ""
    index = 0
    while index < len(text):
        best = ""
        for token in order:
            value = values.get(token) or ""
            if len(value) < 2 or not text.startswith(value, index):
                continue
            if _DIGITS.match(value):
                before = text[index - 1] if index else ""
                after = text[index + len(value)] if index + len(value) < len(text) else ""
                if before.isdigit() or after.isdigit():
                    continue
            if not best or len(value) > len(values[best]):
                best = token
        if best:
            if buffer:
                pieces.append({"text": buffer})
                buffer = ""
            pieces.append({"token": best})
            index += len(values[best])
        else:
            buffer += text[index]
            index += 1
    if buffer:
        pieces.append({"text": buffer})
    return pieces


_FILE_FIRST = FILE_TOKENS + RECEIPT_TOKENS
_RECEIPT_FIRST = RECEIPT_TOKENS + FILE_TOKENS


def _agreeing(texts: list[str], value_sets: list[dict[str, str]], extra: tuple[str, ...] = ()) -> tuple[list[dict[str, str]], bool]:
    """One rule that explains every example, and whether one was found (else the first example's).

    ``extra`` adds values only some rules may use (a list column's new name and folder)."""
    first: list[dict[str, str]] | None = None
    for order in ((*_FILE_FIRST, *extra), (*_RECEIPT_FIRST, *extra)):
        rules = [decompose(text, values, order) for text, values in zip(texts, value_sets, strict=True)]
        first = first or rules[0]
        if all(rule == rules[0] for rule in rules):
            return rules[0], True
    return first or [], False


def _unexplained(rule: list[dict[str, str]]) -> list[str]:
    """Fixed text that looks like it was typed from the file itself (letters or digits, not just
    separators): the person should confirm it is meant to be the same every time."""
    return [piece["text"].strip() for piece in rule if "text" in piece and re.search(r"[^\W_]{2,}", piece["text"])]


def propose(state: dict[str, Any]) -> dict[str, Any]:
    """The proposal for a finished watch: a draft routine, the examples it rests on, what it left out."""
    folder = Path(state["folder"])
    notes = list(state.get("notes") or [])
    before = (state.get("before") or {}).get("files") or {}
    moves = [note for note in notes if note["kind"] == "moved" and "/" not in note["from"]]
    left_out = [note for note in notes if note not in moves and note["kind"] != "rows"]
    rows = [note for note in notes if note["kind"] == "rows"]
    if not moves:
        raise RoutineError("nothing_learned", "VOOL did not see a file being renamed or moved in that folder. "
                                              "Start again and do the task once while it watches.")
    examples = []
    for note in moves:
        record = before.get(note["from"]) or {}
        values = file_values(note["from"], int(record.get("mtime_ns") or note.get("mtime_ns") or 0))
        values.update(receipts.read_values(folder / note["to"]))
        target = Path(note["to"])
        examples.append({"note": note, "values": values, "stem": target.stem, "folder": target.parent.as_posix()})

    same_name = all(example["stem"] == example["values"]["name"] for example in examples)
    rename, rename_ok = ([], True) if same_name else _agreeing(
        [example["stem"] for example in examples], [example["values"] for example in examples])
    stays = all(example["folder"] in {"", "."} for example in examples)
    into, into_ok = ([], True) if stays else _agreeing(
        [example["folder"] for example in examples], [example["values"] for example in examples])

    log = None
    log_ok = True
    if rows:
        chosen = max(rows, key=lambda note: len(note["rows"]))
        result_sets = []
        for example in examples:
            values = dict(example["values"])
            values["new_name"] = Path(example["note"]["to"]).name
            values["folder"] = "" if example["folder"] in {"", "."} else example["folder"]
            result_sets.append(values)
        columns: list[list[dict[str, str]]] = []
        usable = [row for row in chosen["rows"] if row]
        width = max(len(row) for row in usable) if usable else 0
        # Each added row belongs to the example whose values explain most of its text (characters,
        # not pieces: a wrong example explains "2026-11-03" as year, month and "-03" in more pieces).
        paired = []
        for row in usable[: len(examples)]:
            scores = [sum(len(values[piece["token"]]) for cell in row
                          for piece in decompose(cell, values, (*_FILE_FIRST, *RESULT_TOKENS)) if "token" in piece)
                      for values in result_sets]
            paired.append((row, result_sets[scores.index(max(scores))]))
        for index in range(min(width, 12)):
            cells = [row[index] if index < len(row) else "" for row, _ in paired]
            rule, ok = _agreeing(cells, [values for _, values in paired], RESULT_TOKENS) if cells else ([], False)
            if not rule:
                rule = [{"text": "-"}]
            if not ok or len(paired) != len(examples):
                log_ok = False
            columns.append(rule)
        if columns:
            log = {"file": chosen["path"], "columns": columns}

    routine = {
        "id": new_id(),
        "name": "",
        "folder": str(folder),
        "types": sorted({Path(example["note"]["from"]).suffix.lower() for example in examples if Path(example["note"]["from"]).suffix}),
        "rename": rename,
        "into": into,
        "log": log,
        "max_files": DEFAULT_MAX_FILES,
        "schedule": {"every": "", "time": "09:00", "weekday": 0},
    }
    unclear = []
    if not rename_ok:
        unclear.append("rename_differs")
    if not into_ok:
        unclear.append("folder_differs")
    if not log_ok:
        unclear.append("list_differs")
    if not routine["types"]:
        unclear.append("no_types")
    typed = sorted(set(_unexplained(rename) + _unexplained(into)))
    receipt_needed = any(piece.get("token") in RECEIPT_TOKENS for piece in rename + into)
    return {
        "routine": routine,
        "examples": [{"from": example["note"]["from"], "to": example["note"]["to"]} for example in examples],
        "unclear": unclear,
        "fixed_text": typed,
        "receipts_here": receipts.readable_here(),
        "uses_receipts": receipt_needed,
        "left_out": [{"kind": note["kind"], "path": note.get("path") or note.get("from") or ""} for note in left_out],
    }


__all__ = ["decompose", "file_values", "propose"]
