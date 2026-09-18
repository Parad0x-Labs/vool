"""Versioned local reference for bounded file-format standard membership questions.

This module owns a deliberately small, primary-source-backed registry.  It is not a general ISO
catalogue and never infers membership from a candidate merely being a file format.  A query is
claimed only when it supplies a bounded option list and an exact registered standard identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FileFormatStandard:
    canonical_id: str
    format_name: str
    format_aliases: tuple[str, ...]
    accepted_ids: tuple[str, ...]
    title: str
    edition: int
    publication_year: int
    status: str
    authority: str
    source_url: str


FILE_FORMAT_STANDARDS = (
    FileFormatStandard(
        canonical_id="ISO/IEC 15948:2004",
        format_name="PNG",
        format_aliases=("PNG", "Portable Network Graphics"),
        accepted_ids=("ISO/IEC 15948", "ISO/IEC 15948:2004"),
        title="Portable Network Graphics (PNG): Functional specification",
        edition=1,
        publication_year=2004,
        status="published; confirmed 2021",
        authority="ISO/IEC JTC 1/SC 24",
        source_url="https://www.iso.org/standard/29581.html",
    ),
    FileFormatStandard(
        canonical_id="ISO 32000-1:2008",
        format_name="PDF",
        format_aliases=("PDF", "Portable Document Format"),
        accepted_ids=("ISO 32000-1", "ISO 32000-1:2008"),
        title="Document management — Portable document format — Part 1: PDF 1.7",
        edition=1,
        publication_year=2008,
        status="published; confirmed 2023",
        authority="ISO/TC 171/SC 2",
        source_url="https://www.iso.org/standard/51502.html",
    ),
    FileFormatStandard(
        canonical_id="ISO 32000-2:2020",
        format_name="PDF",
        format_aliases=("PDF", "Portable Document Format"),
        accepted_ids=("ISO 32000", "ISO 32000-2", "ISO 32000-2:2020"),
        title="Document management — Portable document format — Part 2: PDF 2.0",
        edition=2,
        publication_year=2020,
        status="published; confirmed",
        authority="ISO/TC 171/SC 2",
        source_url="https://www.iso.org/standard/75839.html",
    ),
    FileFormatStandard(
        canonical_id="ISO/IEC 10918-1:1994",
        format_name="JPEG",
        format_aliases=("JPEG", "JPG"),
        accepted_ids=("ISO/IEC 10918", "ISO/IEC 10918-1", "ISO/IEC 10918-1:1994"),
        title="Digital compression and coding of continuous-tone still images",
        edition=1,
        publication_year=1994,
        status="published; under review",
        authority="ISO/IEC JTC 1/SC 29",
        source_url="https://www.iso.org/standard/18902.html",
    ),
    FileFormatStandard(
        canonical_id="ISO/IEC 15444-1:2024",
        format_name="JPEG 2000",
        format_aliases=("JPEG 2000", "JPEG-2000", "JP2"),
        accepted_ids=("ISO/IEC 15444", "ISO/IEC 15444-1", "ISO/IEC 15444-1:2024"),
        title="JPEG 2000 image coding system — Part 1: Core coding system",
        edition=5,
        publication_year=2024,
        status="published",
        authority="ISO/IEC JTC 1/SC 29",
        source_url="https://www.iso.org/standard/87632.html",
    ),
)

_QUERY_RE = re.compile(
    r"\bwhich\s+of\s+(?P<candidates>[^?\n]{1,320}?)\s+"
    r"(?:is|are)\s+(?:(?:the|an?)\s+)?"
    r"(?:(?:(?:file|image|document)\s+)?formats?\s+)?"
    r"(?:defined|specified|standardized)\s+by\s+(?:(?:the\s+)?standard\s+)?"
    r"(?P<standard>ISO\s*(?:(?:/|-)\s*IEC|\s+IEC)?\s+\d{4,5}"
    r"(?:\s*-\s*\d+)?(?:\s*:\s*\d{4})?)\b",
    re.IGNORECASE,
)
_CANDIDATE_SPLIT_RE = re.compile(r"\s*(?:,|;|\band\b|\bor\b)\s*", re.IGNORECASE)
_ATOMIC_CANDIDATE_RE = re.compile(r"[A-Za-z][A-Za-z0-9 +_-]{0,39}\Z")


def _normalize_standard_id(value: str) -> str:
    normalized = value.upper().strip()
    normalized = re.sub(r"\s*(?:/|-)\s*IEC\b|\s+IEC\b", "/IEC", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    normalized = re.sub(r"\s*:\s*", ":", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _normalize_format(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = normalized.lstrip(".")
    normalized = re.sub(r"[-_]+", " ", normalized)
    return " ".join(normalized.split())


_STANDARD_BY_ID = {
    _normalize_standard_id(identifier): standard
    for standard in FILE_FORMAT_STANDARDS
    for identifier in standard.accepted_ids
}
_FORMAT_ALIASES = {
    _normalize_format(alias): standard.format_name
    for standard in FILE_FORMAT_STANDARDS
    for alias in standard.format_aliases
}


def stable_file_format_standard_response(user_text: str) -> str | None:
    """Return listed formats that belong to an exact, registered file-format standard."""

    text = str(user_text or "").strip()
    match = _QUERY_RE.search(text)
    if match is None:
        return None
    standard = _STANDARD_BY_ID.get(_normalize_standard_id(match.group("standard")))
    if standard is None:
        return None

    candidates: list[str] = []
    for raw in _CANDIDATE_SPLIT_RE.split(match.group("candidates")):
        candidate = raw.strip(" \t\"'‘’“”`().")
        # Oxford comma + conjunction creates an empty split between two delimiters.
        if not candidate:
            continue
        if not _ATOMIC_CANDIDATE_RE.fullmatch(candidate):
            return None
        if candidate not in candidates:
            candidates.append(candidate)
    if not 2 <= len(candidates) <= 20:
        return None

    expected = standard.format_name
    admitted: list[str] = []
    for candidate in candidates:
        canonical = _FORMAT_ALIASES.get(_normalize_format(candidate))
        if canonical == expected and canonical not in admitted:
            admitted.append(canonical)

    if not admitted:
        return f"None of the listed formats — {standard.canonical_id}."
    return "\n".join(f"{name} — {standard.canonical_id}." for name in admitted)


__all__ = [
    "FILE_FORMAT_STANDARDS",
    "FileFormatStandard",
    "stable_file_format_standard_response",
]
