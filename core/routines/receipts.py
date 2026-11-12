"""The date, shop and amount of a receipt, read from its text.

Reading the document itself goes through VOOL's confined document readers (the macOS sandbox, or
bubblewrap on Linux); on a computer with neither, a routine that needs a receipt's values says so and
leaves those files alone.
The text rules are plain and strict: a value that cannot be read with confidence is left empty, and the
routine then leaves that file for the person instead of guessing.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from core.routines.model import clean_value

_ISO = re.compile(r"\b(20\d\d)[-./](\d{1,2})[-./](\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})([./])(\d{1,2})\2(20\d\d)\b")
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,6}(?:[ .,  ]\d{3})*[.,]\d{2})(?![\d])")
_TOTAL_WORDS = re.compile(
    r"\b(total|totaal|summe|gesamt|betrag|totale|importe|montant|итого|всього|razem|suma|toplam|tổng|合計|合计|총액|합계|iš viso|סה\"כ|المجموع|कुल)\b",
    re.I,
)
READABLE_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff", ".txt")


def _valid(year: int, month: int, day: int) -> str:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def read_date(text: str) -> str:
    """The first unambiguous date: 2026-11-03; 03.11.2026 (dots are day first); 03/11/2026 only when the
    day is above 12 or both readings are the same day. An ambiguous date with slashes is left empty."""
    found = []
    for match in _ISO.finditer(text):
        value = _valid(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if value:
            found.append((match.start(), value))
    for match in _DMY.finditer(text):
        first, second, year = int(match.group(1)), int(match.group(3)), int(match.group(4))
        day_first = _valid(year, second, first)
        month_first = _valid(year, first, second)
        if day_first and (match.group(2) == "." or first > 12 or day_first == month_first or not month_first):
            found.append((match.start(), day_first))
        elif month_first and not day_first:
            found.append((match.start(), month_first))
    return min(found)[1] if found else ""


def _number(text: str) -> float | None:
    digits = re.sub(r"[   ]", "", text)
    decimal = digits[-3]
    whole = re.sub(r"[.,]", "", digits[:-3])
    try:
        return float(whole + "." + digits[-2:]) if decimal in ".," else None
    except ValueError:
        return None


def read_amount(text: str) -> str:
    """The amount on the last line that names a total; with no such line, the largest amount."""
    lines = [line for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if _TOTAL_WORDS.search(line):
            amounts = [_number(m.group(1)) for m in _AMOUNT.finditer(line)]
            amounts = [value for value in amounts if value is not None]
            if amounts:
                return f"{amounts[-1]:.2f}"
    amounts = [_number(m.group(1)) for m in _AMOUNT.finditer(text)]
    amounts = [value for value in amounts if value is not None]
    return f"{max(amounts):.2f}" if amounts else ""


def read_shop(text: str) -> str:
    """The first line with at least three letters that is not a date, an amount or a total."""
    for line in text.splitlines()[:8]:
        candidate = line.strip()
        if (len(re.findall(r"[^\W\d_]", candidate)) >= 3 and not read_date(candidate)
                and not _AMOUNT.search(candidate) and not _TOTAL_WORDS.search(candidate)):
            return clean_value(candidate, 40)
    return ""


def values_from_text(text: str) -> dict[str, str]:
    when = read_date(text)
    return {
        "doc_date": when,
        "doc_year": when[:4] if when else "",
        "doc_month": when[5:7] if when else "",
        "doc_shop": read_shop(text),
        "doc_amount": read_amount(text),
    }


def readable_here() -> bool:
    """Whether this computer can read receipts (the confined document readers)."""
    try:
        from core.artifact_readers._sandbox import confinement_available

        return bool(confinement_available())
    except Exception:
        return False


def read_values(path: Path) -> dict[str, str]:
    """The receipt values of one file; empty values when it cannot be read here or at all."""
    empty = {"doc_date": "", "doc_year": "", "doc_month": "", "doc_shop": "", "doc_amount": ""}
    suffix = path.suffix.lower()
    if suffix not in READABLE_SUFFIXES:
        return empty
    try:
        if suffix == ".txt":
            data = path.read_bytes()[: 256 * 1024]
            return values_from_text(data.decode("utf-8", errors="replace"))
        if not readable_here():
            return empty
        from core import artifact_readers

        data = path.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            return empty
        result = artifact_readers.read(data, name=path.name)
        return values_from_text(str(getattr(result, "text", "") or ""))
    except Exception:
        return empty


__all__ = ["READABLE_SUFFIXES", "read_amount", "read_date", "read_shop", "read_values", "readable_here", "values_from_text"]
