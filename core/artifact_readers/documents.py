"""Spreadsheets and presentations: XLSX and PPTX, read as bounded provenance-addressed structure.

Both formats are ZIPs of XML, so -- exactly like DOCX -- they are read here directly with the
standard library: no third-party package, no external decoder, no code path that could execute
anything. The container itself is bounded before it is opened, and every part this reader reads
is size-capped like an archive member.

What these readers owe the model that a flatter extraction would not:

* **Cell and slide identity travel.** ``Q3!B7`` and ``slide 4`` are the citation handles; a number
  with no address cannot be cited, checked or re-read.
* **Formulas are reported, never executed.** A formula cell shows its CACHED result and says so --
  ``[= SUM(B1:B2); cached, not recalculated]`` -- because a stale cache is the file's claim about
  its last calculation, not a fresh computation, and presenting one as the other is a fabrication.
  A formula whose cache is missing says that instead of quietly rendering an empty cell.
* **Formatting semantics are resolved, not guessed.** A spreadsheet stores the date 2026-09-03 as
  the number ``46268`` wearing a date format. Rendering the bare serial where the sheet shows a
  date, or silently assuming every number is a date, are both lies; the file's own number formats
  decide, and the conversion is stated on the cell.
* **Containers report what they carry and read none of it.** Hyperlinks, linked workbooks and
  embedded media are counted and named as not fetched. Active content -- macros -- is ignored
  explicitly.

Errors stay typed: a damaged container is a refusal that says which part is damaged, and an
OOXML package that is not the claimed document type is refused rather than read as an empty one.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import struct
import zipfile
from typing import Any
from xml.etree import ElementTree

from ._limits import (
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_DOCX_BLOCKS,
    MAX_EPUB_SECTIONS,
    MAX_PPTX_SLIDES,
    MAX_RTF_GROUP_DEPTH,
    MAX_SHARED_STRINGS,
    MAX_SHEET_CELLS,
    MAX_SHEETS,
    MAX_UNIT_CHARS,
    MAX_XLSX_GRID_COLUMNS,
    MAX_XLSX_ROWS,
)
from ._types import (
    UNIT_SECTION,
    UNIT_SHEET,
    UNIT_SLIDE,
    ReaderRefused,
    ReaderResult,
    ReaderUnavailable,
    ReaderUnit,
)
from .office import ZIP_MAGIC, _BytesReader

XLSX_EXTRACTOR = "vool.xlsx 1.0.0 (stdlib zipfile + ElementTree)"
PPTX_EXTRACTOR = "vool.pptx 1.0.0 (stdlib zipfile + ElementTree)"

_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_SSML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_DRAWML_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PML_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

_CELL_ADDRESS = re.compile(r"^([A-Z]{1,3})([0-9]{1,7})$")


def columns_of(address: str) -> int:
    """``B7`` -> 2. Unaddressed cells (no ``r=``) keep their document order instead."""
    match = _CELL_ADDRESS.match(address)
    if not match:
        return 0
    column = 0
    for char in match.group(1):
        column = column * 26 + (ord(char) - ord("A") + 1)
    return column


# --- shared OOXML container plumbing ---------------------------------------------------------------


def _zip_names(data: bytes) -> set[str] | None:
    """The central directory's member names, or None when the bytes are not a readable ZIP."""
    if not bytes(data[:4]).startswith(ZIP_MAGIC):
        return None
    try:
        with zipfile.ZipFile(_BytesReader(data)) as archive:
            return set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None


def _open_package(data: bytes, *, name: str, fmt: str, marker: str) -> zipfile.ZipFile:
    """Open the OOXML container or refuse, typed, with the reason a refusal owes the operator."""
    try:
        archive = zipfile.ZipFile(_BytesReader(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ReaderRefused(
            f"{name} is not a readable {fmt.upper()}: its ZIP container is damaged.",
            code=f"{fmt}_corrupt",
            remediation="Re-save or re-download the file and attach it again.",
            fmt=fmt,
        ) from exc
    if marker not in archive.namelist():
        archive.close()
        raise ReaderRefused(
            f"{name} is a ZIP file but not a {fmt.upper()} package (it has no {marker}).",
            code=f"{fmt}_not_a_package",
            fmt=fmt,
        )
    return archive


def _package_member(archive: zipfile.ZipFile, member: str, *, name: str, fmt: str) -> bytes:
    """One package part's bytes, size-capped before decompression like an archive member."""
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise ReaderRefused(
            f"{name} refers to {member}, which its package does not contain; it is damaged.",
            code=f"{fmt}_corrupt",
            fmt=fmt,
        ) from exc
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise ReaderRefused(
            f"{name} holds a {info.file_size / (1024 * 1024):.0f} MB {member}, over this reader's limit.",
            code=f"{fmt}_part_too_large",
            fmt=fmt,
        )
    try:
        return archive.read(member)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise ReaderRefused(
            f"{name}'s {member} could not be decompressed; it may be corrupt or password-protected.",
            code=f"{fmt}_unreadable",
            remediation="If the file is password-protected, attach an unprotected copy.",
            fmt=fmt,
        ) from exc


def _parse(member: str, payload: bytes, *, name: str, fmt: str) -> ElementTree.Element:
    """Parse one package XML part. ElementTree resolves no external entities and fetches no DTD,
    so a part cannot reach the filesystem or the network through this parse."""
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise ReaderRefused(
            f"{name}'s {member} is damaged XML and the file was not read.",
            code=f"{fmt}_corrupt",
            remediation="Re-save or re-export the file and attach it again.",
            fmt=fmt,
        ) from exc


class Relationships:
    """One part's relationships: r:id -> (target, external, type). External targets are KEPT so
    they can be counted and named as not fetched -- never opened."""

    def __init__(self, archive: zipfile.ZipFile, rels_member: str, *, name: str, fmt: str) -> None:
        self._by_id: dict[str, tuple[str, bool, str]] = {}
        if rels_member not in archive.namelist():
            return
        root = _parse(rels_member, _package_member(archive, rels_member, name=name, fmt=fmt), name=name, fmt=fmt)
        for relationship in root.findall(f"{_PKG_REL_NS}Relationship"):
            identifier = relationship.get("Id") or ""
            if not identifier:
                continue
            self._by_id[identifier] = (
                relationship.get("Target") or "",
                (relationship.get("TargetMode") or "").strip().lower() == "external",
                relationship.get("Type") or "",
            )

    def internal(self, identifier: str) -> str:
        """The ZIP member a relationship points at inside this package, or ``""``."""
        target, external, _type = self._by_id.get(identifier, ("", True, ""))
        return "" if external else target

    def count(self, *, external_only: bool = False, type_suffix: str = "") -> int:
        matches = 0
        for _target, external, relationship_type in self._by_id.values():
            if external_only and not external:
                continue
            if type_suffix and not relationship_type.rstrip("/").endswith(type_suffix):
                continue
            matches += 1
        return matches

    def targets(self, *, external_only: bool = False) -> list[str]:
        return [target for target, external, _type in self._by_id.values() if external or not external_only]

    def find(self, *, type_suffix: str) -> list[tuple[str, bool]]:
        """Every relationship whose type ends with ``type_suffix``, as (target, external)."""
        return [
            (target, external)
            for target, external, relationship_type in self._by_id.values()
            if relationship_type.rstrip("/").endswith(type_suffix)
        ]


def _resolve_part(base_dir: str, target: str) -> str:
    """A relationship target resolved against its part's directory, in ZIP-member terms.

    ``worksheets/sheet1.xml`` under ``xl/`` is ``xl/worksheets/sheet1.xml``; ``/xl/theme.xml``
    (a leading slash, absolute inside the package) is ``xl/theme.xml``. ``..`` segments are
    collapsed textually against the base directory -- a target cannot escape the package this
    way, because the result is only ever looked up among the container's own member names.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    segments: list[str] = []
    for segment in (base_dir + target).split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)
    return "/".join(segments)


# --- XLSX ------------------------------------------------------------------------------------------


def sniff_xlsx(data: bytes) -> bool:
    """An XLSX is a ZIP whose package holds the spreadsheet workbook part."""
    names = _zip_names(data)
    return names is not None and "xl/workbook.xml" in names


def sniff_pptx(data: bytes) -> bool:
    """A PPTX is a ZIP whose package holds the presentation part."""
    names = _zip_names(data)
    return names is not None and "ppt/presentation.xml" in names


#: Builtin number-format ids that mean a date and/or time (ECMA-376 §18.8.30). 27-36 are
#: era-calendar date formats. 18-21 and 45-47 are time-only: the serial renders as a clock time,
#: not as a calendar date.
_BUILTIN_DATE_FORMAT_IDS = frozenset({*range(14, 18), 22, *range(27, 37)})
_BUILTIN_TIME_FORMAT_IDS = frozenset({18, 19, 20, 21, 45, 46, 47})
_DATE_CODE = re.compile(r"[ymdhs]", re.IGNORECASE)

#: Excel's 1900 serial-number system pretends 1900 was a leap year for Lotus compatibility:
#: serial 60 is a date that never happened. Real dates before it sit one day off the naive
#: conversion, so the two ranges convert against different epochs.
_EPOCH_PRE_LEAP = _dt.datetime(1899, 12, 31)
_EPOCH_1900 = _dt.datetime(1899, 12, 30)
_EPOCH_1904 = _dt.datetime(1904, 1, 1)


def _serial_to_datetime(serial: float, *, date_1904: bool) -> tuple[_dt.datetime | None, str | None]:
    """A serial to a real calendar instant, or ``(None, note)`` for Excel's fictitious 1900-02-29,
    which the 1900 system carries for Lotus compatibility but the calendar does not contain."""
    if date_1904:
        epoch = _EPOCH_1904
    elif serial >= 61:
        # At and after serial 61 the epoch absorbs the fictitious Feb 29, so 1900-03-01 onward
        # map to the calendar exactly.
        epoch = _EPOCH_1900
    else:
        epoch = _EPOCH_PRE_LEAP
    if not date_1904 and int(serial) == 60:
        return None, "renders as 1900-02-29 in the sheet, which is not a real date (a Lotus-compatibility artefact)"
    days = int(serial)
    seconds = round((serial - days) * 86400)
    return epoch + _dt.timedelta(days=days, seconds=seconds), None


class NumberFormats:
    """Style index -> "this cell is formatted as a date/time", read from styles.xml once.

    This is the whole of formatting semantics this reader claims: whether a numeric cell is a
    DATE or a TIME. Currency, percent and font styling are presentation, and are not claimed. A
    custom format code counts as a date only when it carries date/time codes outside quoted
    literals and bracketed conditions.
    """

    def __init__(self, archive: zipfile.ZipFile, *, name: str, fmt: str) -> None:
        self._date_styles: set[int] = set()
        self._time_styles: set[int] = set()
        if "xl/styles.xml" not in archive.namelist():
            return
        root = _parse("xl/styles.xml", _package_member(archive, "xl/styles.xml", name=name, fmt=fmt), name=name, fmt=fmt)
        custom_date: dict[int, bool] = {}
        custom_time: dict[int, bool] = {}
        for number_format in root.findall(f"{_SSML_NS}numFmts/{_SSML_NS}numFmt"):
            try:
                identifier = int(number_format.get("numFmtId") or "")
            except ValueError:
                continue
            code = number_format.get("formatCode") or ""
            stripped = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
            has_date = bool(_DATE_CODE.search(stripped))
            has_hours = bool(re.search(r"[hs]", stripped, re.IGNORECASE))
            custom_date[identifier] = has_date
            custom_time[identifier] = has_date and not re.search(r"[ymd]", stripped, re.IGNORECASE) and has_hours
        for index, cell_format in enumerate(root.findall(f"{_SSML_NS}cellXfs/{_SSML_NS}xf")):
            try:
                identifier = int(cell_format.get("numFmtId") or "")
            except ValueError:
                continue
            if identifier in _BUILTIN_DATE_FORMAT_IDS or custom_date.get(identifier, False):
                self._date_styles.add(index)
            if identifier in _BUILTIN_TIME_FORMAT_IDS or custom_time.get(identifier, False):
                self._time_styles.add(index)

    def is_date(self, style: str | None) -> bool:
        return self._style_in(style, self._date_styles)

    def is_time(self, style: str | None) -> bool:
        return self._style_in(style, self._time_styles)

    @staticmethod
    def _style_in(style: str | None, styles: set[int]) -> bool:
        if not style:
            return False
        try:
            return int(style) in styles
        except ValueError:
            return False


class SharedStrings:
    """The workbook's string table. Rich runs concatenate; phonetic hints (``rPh``) do not --
    they are pronunciation metadata for East-Asian text, and emitting them would double every
    such string into the extraction."""

    def __init__(self, archive: zipfile.ZipFile, *, name: str, fmt: str) -> None:
        self._strings: list[str] = []
        self.truncated = False
        if "xl/sharedStrings.xml" not in archive.namelist():
            return
        root = _parse("xl/sharedStrings.xml", _package_member(archive, "xl/sharedStrings.xml", name=name, fmt=fmt), name=name, fmt=fmt)
        for item in root.findall(f"{_SSML_NS}si"):
            if len(self._strings) >= MAX_SHARED_STRINGS:
                self.truncated = True
                break
            self._strings.append("".join(_visible_text(item)))

    def get(self, index: str | None) -> str:
        if index is None:
            return ""
        try:
            position = int(index)
        except ValueError:
            return ""
        return self._strings[position] if 0 <= position < len(self._strings) else ""


def _visible_text(node: ElementTree.Element) -> list[str]:
    """Every ``t`` descendant except those inside phonetic runs."""
    parts: list[str] = []
    stack = [iter(list(node))]
    phonetic_depth = 0
    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            if phonetic_depth and stack and phonetic_depth >= len(stack):
                phonetic_depth -= 1
            continue
        if child.tag == f"{_SSML_NS}rPh":
            phonetic_depth += 1
            stack.append(iter(list(child)))
            continue
        if child.tag == f"{_SSML_NS}t" and not phonetic_depth:
            parts.append(child.text or "")
        if len(child):
            stack.append(iter(list(child)))
    return parts


def _cell_text(
    cell: ElementTree.Element,
    *,
    shared: SharedStrings,
    formats: NumberFormats,
    date_1904: bool,
) -> tuple[str, bool]:
    """One cell's rendered text and whether it is a formula cell.

    ``t=`` names the cached TYPE: ``s`` shared string, ``inlineStr`` embedded string, ``str``
    formula string result, ``b`` boolean, ``e`` a cached error, nothing numeric. The visible
    value is always the CACHE: the file's stored claim about what its last calculation produced,
    which this reader never re-runs.
    """
    kind = cell.get("t") or "n"
    value_node = cell.find(f"{_SSML_NS}v")
    raw = (value_node.text or "").strip() if value_node is not None else ""
    formula_node = cell.find(f"{_SSML_NS}f")
    formula_text = ""
    if formula_node is not None:
        formula_text = _formula_annotation(formula_node, cached=bool(raw) or kind == "inlineStr")
    if kind == "inlineStr":
        text = "".join(t.text or "" for t in cell.iter(f"{_SSML_NS}t"))
    elif kind == "s":
        text = shared.get(raw)
    elif kind == "b":
        text = "TRUE" if raw == "1" else ("FALSE" if raw == "0" else raw)
    elif kind == "e":
        text = raw or "(cached error value)"
    elif kind == "str":
        text = raw
    else:
        text = _numeric_text(raw, cell.get("s"), formats=formats, date_1904=date_1904)
    if formula_text:
        return f"{text} {formula_text}".strip(), True
    return text, False


def _formula_annotation(formula_node: ElementTree.Element, *, cached: bool) -> str:
    """How a formula cell states itself: the formula, and the honest status of its result."""
    formula = (formula_node.text or "").strip()
    if not formula and formula_node.get("t") == "shared":
        formula = f"shared formula #{formula_node.get('si') or '?'}"
    state = "cached, not recalculated" if cached else "NO cached result in the file"
    return f"[= {formula}; {state}]" if formula else f"[formula; {state}]"


def _numeric_text(raw: str, style: str | None, *, formats: NumberFormats, date_1904: bool) -> str:
    """A numeric cell as the model should see it: the number, or the date its format declares.

    ``46268`` wearing a date format is 2026-09-03; printing the bare serial where the sheet shows
    a date would make every date question unanswerable, and converting unformatted numbers on a
    guess would invent dates out of quantities. The conversion is driven by the file's own number
    format and says so on the cell.
    """
    if raw == "":
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    if number >= 0 and (formats.is_date(style) or formats.is_time(style)):
        stamp, note = _serial_to_datetime(number, date_1904=date_1904)
        if stamp is None:
            return f"1900-02-29 (date-formatted serial {raw}; {note})"
        rendered = _stamp_text(stamp, time_only=formats.is_time(style))
        return f"{rendered} (date-formatted serial {raw})" + (f" [{note}]" if note else "")
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def _stamp_text(stamp: _dt.datetime, *, time_only: bool) -> str:
    if time_only:
        return stamp.strftime("%H:%M:%S") if stamp.second else stamp.strftime("%H:%M")
    return stamp.strftime("%Y-%m-%d %H:%M:%S").replace(" 00:00:00", "")


def read_xlsx(data: bytes, *, name: str = "workbook.xlsx") -> ReaderResult:
    """A workbook as one unit per sheet: a grid that keeps columns columns and cells addressed."""
    digest = hashlib.sha256(data).hexdigest()
    archive = _open_package(data, name=name, fmt="xlsx", marker="xl/workbook.xml")
    try:
        workbook = _parse(
            "xl/workbook.xml",
            _package_member(archive, "xl/workbook.xml", name=name, fmt="xlsx"),
            name=name,
            fmt="xlsx",
        )
        workbook_pr = workbook.find(f"{_SSML_NS}workbookPr")
        date_1904 = workbook_pr is not None and (workbook_pr.get("date1904") or "").lower() in ("1", "true")
        relationships = Relationships(archive, "xl/_rels/workbook.xml.rels", name=name, fmt="xlsx")
        shared = SharedStrings(archive, name=name, fmt="xlsx")
        formats = NumberFormats(archive, name=name, fmt="xlsx")

        sheet_names: list[tuple[str, str]] = []
        external_sheet_refs = 0
        for sheet in workbook.findall(f"{_SSML_NS}sheets/{_SSML_NS}sheet"):
            target = relationships.internal(sheet.get(f"{_OFFICE_REL_NS}id") or "")
            if not target:
                external_sheet_refs += 1
                continue
            sheet_names.append((sheet.get("name") or "sheet", _resolve_part("xl/", target)))
        #: An externalReference is a link to another workbook's cells. Whether the producer marked
        #: it TargetMode external or not, it points outside this package and is never opened.
        linked_workbooks = [target for target, _external in relationships.find(type_suffix="externalReference")]

        warnings: list[str] = []
        omitted: list[str] = []
        units: list[ReaderUnit] = []
        total_cells = 0
        macro_parts = sorted(member for member in archive.namelist() if member.lower().endswith("vbaproject.bin"))
        if len(sheet_names) > MAX_SHEETS:
            warnings.append(f"This workbook has {len(sheet_names)} sheets; only the first {MAX_SHEETS} were read.")
            for dropped_name, _dropped in sheet_names[MAX_SHEETS:]:
                omitted.append(f'sheet "{dropped_name}"')
            sheet_names = sheet_names[:MAX_SHEETS]
        for sheet_name, member in sheet_names:
            if total_cells >= MAX_SHEET_CELLS:
                omitted.append(f'sheet "{sheet_name}"')
                continue
            if member not in archive.namelist():
                warnings.append(f'Sheet "{sheet_name}" points at {member}, which the package does not contain; it was skipped.')
                continue
            unit, cells_used, sheet_omissions = _read_sheet(
                archive,
                member,
                sheet_name=sheet_name,
                shared=shared,
                formats=formats,
                date_1904=date_1904,
                name=name,
                cell_budget=MAX_SHEET_CELLS - total_cells,
            )
            units.append(unit)
            total_cells += cells_used
            omitted.extend(sheet_omissions)
        if shared.truncated:
            warnings.append("The shared string table was longer than this reader reads; some text cells may be shown empty.")
        if macro_parts:
            warnings.append(f"This workbook contains a macro project ({macro_parts[0]}). It was NOT run and its code was not read.")
        if external_sheet_refs:
            warnings.append(f"{external_sheet_refs} sheet reference(s) point outside the file; they were NOT fetched.")
        if linked_workbooks:
            warnings.append(f"A linked workbook ({linked_workbooks[0]}) is referenced but was NOT opened or fetched.")
    finally:
        archive.close()

    warnings.append("Formulas were not executed: values shown for formula cells are the file's own cached results and may be stale.")
    return ReaderResult(
        fmt="xlsx",
        extractor=XLSX_EXTRACTOR,
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "sheets": len(units),
            "cells": total_cells,
            "has_macros": bool(macro_parts),
            "external_references": external_sheet_refs + len(linked_workbooks),
            "date_system": "1904" if date_1904 else "1900",
        },
    )


def _read_sheet(
    archive: zipfile.ZipFile,
    member: str,
    *,
    sheet_name: str,
    shared: SharedStrings,
    formats: NumberFormats,
    date_1904: bool,
    name: str,
    cell_budget: int,
) -> tuple[ReaderUnit, int, list[str]]:
    """One worksheet as grid rows: ``row | A | B | C`` under the sheet's used columns.

    The grid is bounded twice over -- rows and total cells -- and both cuts are stated. Formula
    cells render their cached value with the formula beside it, so arithmetic shown can be told
    apart from arithmetic merely claimed.
    """
    root = _parse(member, _package_member(archive, member, name=name, fmt="xlsx"), name=name, fmt="xlsx")
    rows: list[tuple[int, dict[int, str]]] = []
    formula_count = 0
    cells_read = 0
    omissions: list[str] = []
    width = 0
    row_cut = False
    cell_cut = False
    for row_index, row in enumerate(root.findall(f"{_SSML_NS}sheetData/{_SSML_NS}row")):
        if len(rows) >= MAX_XLSX_ROWS or cells_read >= cell_budget:
            row_cut = True
            break
        cells: dict[int, str] = {}
        for cell in row.findall(f"{_SSML_NS}c"):
            if cells_read >= cell_budget:
                cell_cut = True
                break
            text, is_formula = _cell_text(cell, shared=shared, formats=formats, date_1904=date_1904)
            if not text:
                continue
            cells_read += 1
            formula_count += 1 if is_formula else 0
            column = columns_of(cell.get("r") or "")
            cells[column] = text
            width = max(width, column)
        if cells:
            try:
                row_number = int(row.get("r") or "")
            except ValueError:
                row_number = row_index + 1
            rows.append((row_number, cells))
    if row_cut:
        omissions.append(f'sheet "{sheet_name}": rows beyond {len(rows):,} (row or cell budget)')
    if cell_cut:
        omissions.append(f'sheet "{sheet_name}": cells beyond {cells_read:,} (cell budget)')
    if width > MAX_XLSX_GRID_COLUMNS:
        omissions.append(f'sheet "{sheet_name}": columns beyond {MAX_XLSX_GRID_COLUMNS} are not rendered')
        width = MAX_XLSX_GRID_COLUMNS
    header = "row | " + " | ".join(_column_name(index) for index in range(1, width + 1)) if width else "row | (no cells)"
    lines = [
        f'sheet "{sheet_name}" -- {len(rows):,} row(s) with content, {cells_read:,} non-empty cell(s), {formula_count:,} formula cell(s)',
        header,
    ]
    for row_number, cells in rows:
        rendered = " | ".join(_grid_cell(cells.get(column, "")) for column in range(1, width + 1))
        lines.append(f"{row_number:>4} | {rendered}")
    text = "\n".join(lines)
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS]
        omissions.append(f'sheet "{sheet_name}": text cut at {MAX_UNIT_CHARS:,} characters')
    return (
        ReaderUnit(
            locator=f'sheet "{sheet_name}"',
            kind=UNIT_SHEET,
            text=text,
            meta={"cells": cells_read, "formulas": formula_count, "rows": len(rows)},
        ),
        cells_read,
        omissions,
    )


def _grid_cell(text: str) -> str:
    """One grid cell: pipes inside a cell's own text cannot impersonate column separators."""
    return text.replace("|", "\\|") if "|" in text else text


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


# --- PPTX ------------------------------------------------------------------------------------------


_NOTES_SLIDE_TYPE = "notesSlide"


def read_pptx(data: bytes, *, name: str = "deck.pptx") -> ReaderResult:
    """A deck as one unit per slide: title, body paragraphs, tables -- in slide order.

    Slide order is the presentation's own, from ``sldIdLst`` through its relationships -- never
    the numeric accident of the slide part's file name. Speaker notes join their slide marked as
    notes. Hyperlinks, linked media and embedded objects are counted and not fetched.
    """
    digest = hashlib.sha256(data).hexdigest()
    archive = _open_package(data, name=name, fmt="pptx", marker="ppt/presentation.xml")
    try:
        presentation = _parse(
            "ppt/presentation.xml",
            _package_member(archive, "ppt/presentation.xml", name=name, fmt="pptx"),
            name=name,
            fmt="pptx",
        )
        relationships = Relationships(archive, "ppt/_rels/presentation.xml.rels", name=name, fmt="pptx")
        external_links = relationships.count(external_only=True)
        media = sorted(member for member in archive.namelist() if member.startswith("ppt/media/"))
        macro_parts = sorted(member for member in archive.namelist() if member.lower().endswith("vbaproject.bin"))

        units: list[ReaderUnit] = []
        warnings: list[str] = []
        omitted: list[str] = []
        slide_ids = presentation.findall(f"{_PML_NS}sldIdLst/{_PML_NS}sldId")
        if len(slide_ids) > MAX_PPTX_SLIDES:
            warnings.append(f"This deck has {len(slide_ids)} slides; only the first {MAX_PPTX_SLIDES} were read.")
            for dropped_position in range(MAX_PPTX_SLIDES + 1, len(slide_ids) + 1):
                omitted.append(f"slide {dropped_position}")
            slide_ids = slide_ids[:MAX_PPTX_SLIDES]
        for position, slide_id in enumerate(slide_ids, start=1):
            reference = slide_id.get(f"{_OFFICE_REL_NS}id") or ""
            target = relationships.internal(reference)
            if not target:
                warnings.append(f"slide {position} points outside the file; it was NOT fetched.")
                continue
            member = _resolve_part("ppt/", target)
            if member not in archive.namelist():
                warnings.append(f"slide {position} points at {member}, which the package does not contain; it was skipped.")
                continue
            units.append(_read_slide(archive, member, position=position, name=name))
        if macro_parts:
            warnings.append(f"This presentation contains an active-content project ({macro_parts[0]}). It was NOT run and its code was not read.")
        if external_links:
            warnings.append(f"{external_links} external link(s) are referenced; nothing outside the file was fetched.")
        if media:
            warnings.append(f"{len(media)} embedded media object(s) are present; they were not opened or rendered.")
    finally:
        archive.close()

    return ReaderResult(
        fmt="pptx",
        extractor=PPTX_EXTRACTOR,
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "slides": len(units),
            "has_macros": bool(macro_parts),
            "external_links": external_links,
            "embedded_media": len(media),
        },
    )


def _read_slide(archive: zipfile.ZipFile, member: str, *, position: int, name: str) -> ReaderUnit:
    root = _parse(member, _package_member(archive, member, name=name, fmt="pptx"), name=name, fmt="pptx")
    title = ""
    paragraphs: list[str] = []
    tables = 0
    for shape in root.iter(f"{_PML_NS}sp"):
        placeholder = shape.find(f"{_PML_NS}nvSpPr/{_PML_NS}nvPr/{_PML_NS}ph")
        is_title = placeholder is not None and (placeholder.get("type") or "").lower() in ("title", "ctrtitle")
        shape_paragraphs = _shape_paragraphs(shape)
        if is_title and shape_paragraphs and not title:
            title = shape_paragraphs[0]
            paragraphs.extend(shape_paragraphs[1:])
        else:
            paragraphs.extend(shape_paragraphs)
    for table in root.iter(f"{_DRAWML_NS}tbl"):
        tables += 1
        rendered = _slide_table(table)
        if rendered:
            paragraphs.append(rendered)

    text = f"Title: {title}\n" + "\n".join(paragraphs) if title else "\n".join(paragraphs)
    notes = _slide_notes(archive, member, title=title, name=name)
    if notes:
        text = f"{text}\n[speaker notes]\n{notes}" if text else f"[speaker notes]\n{notes}"
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS] + "\n[text cut at the limit]"
    return ReaderUnit(
        locator=f"slide {position}",
        kind=UNIT_SLIDE,
        text=text.strip("\n"),
        meta={"title": title, "paragraphs": len(paragraphs), "tables": tables},
    )


def _shape_paragraphs(shape: ElementTree.Element) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in shape.iter(f"{_DRAWML_NS}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_DRAWML_NS}t")).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _slide_notes(archive: zipfile.ZipFile, slide_member: str, *, title: str, name: str) -> str:
    """The speaker notes belonging to one slide, resolved through the slide's OWN rels so a notes
    part renumbered independently of its slide still resolves. The slide-number placeholder that
    every notes part carries is dropped: it is slide furniture, not content."""
    directory, _slash, filename = slide_member.rpartition("/")
    rels = f"{directory}/_rels/{filename}.rels"
    if rels not in archive.namelist():
        return ""
    relationships = Relationships(archive, rels, name=name, fmt="pptx")
    for target, external in relationships.find(type_suffix=_NOTES_SLIDE_TYPE):
        if external:
            continue
        member = _resolve_part(f"{directory}/", target)
        if member not in archive.namelist():
            continue
        root = _parse(member, _package_member(archive, member, name=name, fmt="pptx"), name=name, fmt="pptx")
        lines: list[str] = []
        for paragraph in root.iter(f"{_DRAWML_NS}p"):
            line = "".join(node.text or "" for node in paragraph.iter(f"{_DRAWML_NS}t")).strip()
            if line and line != title and not line.isdigit():
                lines.append(line)
        return "\n".join(lines)
    return ""


def _slide_table(table: ElementTree.Element) -> str:
    """A slide table as pipe rows, exactly as a DOCX table is rendered: columns survive."""
    rows: list[list[str]] = []
    for row in table.findall(f"{_DRAWML_NS}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_DRAWML_NS}tc"):
            text = " ".join(
                "".join(node.text or "" for node in paragraph.iter(f"{_DRAWML_NS}t")).strip()
                for paragraph in cell.iter(f"{_DRAWML_NS}p")
            )
            cells.append(" ".join(text.split()))
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    lines = [" | ".join((row + [""] * width)[:width]) for row in rows]
    return "[table]\n" + "\n".join(lines)


__all__ = [
    "PPTX_EXTRACTOR",
    "UNIT_SHEET",
    "UNIT_SLIDE",
    "XLSX_EXTRACTOR",
    "columns_of",
    "read_pptx",
    "read_xlsx",
    "sniff_pptx",
    "sniff_xlsx",
]


# --- RTF -------------------------------------------------------------------------------------------
#
# RTF is a 7-bit text format with an explicit escaping story, so it is read here with a small
# bounded scanner: no third-party package, and no code path that could execute anything. The
# scanner owes the same honesty the spreadsheet readers owe: what it found is addressed, and what
# it deliberately did NOT read -- pictures, embedded objects, headers, field INSTRUCTIONS -- is
# counted and named. A field's instruction (HYPERLINK "https://...") is a link target, never
# document text; the field's cached RESULT is content, and is kept.

#: Destinations whose CONTENT is not body text. None of it is fetched or decoded; several are
#: counted (pictures, objects) so the operator learns what the document carries.
_RTF_SKIP_DESTINATIONS = frozenset(
    {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
        "colorschememapping", "datastore", "rsidtbl", "latentstyles", "listtable",
        "listoverridetable", "revtbl", "generator", "xmlnstbl", "fchars", "lchars",
        "nonesttables", "fldinst", "header", "headerl", "headerr", "headerf",
        "footer", "footerl", "footerr", "footerf", "ftnsep", "ftnsepc", "aeroskip",
    }
)
_RTF_PARAGRAPH_WORDS = frozenset({"par", "line", "sect", "page"})
_RTF_HEX_ESCAPE = re.compile(rb"^['']([0-9a-fA-F]{2})")


def sniff_rtf(data: bytes) -> bool:
    """RTF starts ``{\\rtf`` -- the one format here whose magic is plain text."""
    head = bytes(data[:64]).lstrip()
    return head[:5].lower() == b"{\\rtf"


class _RtfScanner:
    """One bounded pass over RTF bytes that produces body text and counts what it skipped.

    The scanner never decodes picture or object data: ``\\objdata`` hex and image payloads are
    skipped at the group level, so a hostile blob cannot burn time or memory inside this reader.
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0
        self.parts: list[str] = []
        self.characters = 0
        self.truncated = False
        self.paragraphs = 0
        self.pictures = 0
        self.objects = 0
        self.hyperlinks = 0
        self.unhandled_destinations: set[str] = set()
        self.unicode_skip = 1
        #: Per-group scanner state. ``skip`` suppresses output; ``star`` means a ``\*`` marker
        #: was seen and the group's fate waits on its first control word.
        self.stack: list[dict[str, bool]] = [{"skip": False, "star": False}]
        self.code_page = "cp1252"

    # -- output ------------------------------------------------------------------

    @property
    def skipping(self) -> bool:
        return any(frame["skip"] for frame in self.stack)

    def emit(self, text: str) -> None:
        if self.skipping or not text:
            return
        if self.characters + len(text) > MAX_UNIT_CHARS:
            keep = MAX_UNIT_CHARS - self.characters
            if keep > 0:
                self.parts.append(text[:keep])
            self.characters = MAX_UNIT_CHARS
            self.truncated = True
            return
        self.parts.append(text)
        self.characters += len(text)

    def emit_paragraph_break(self) -> None:
        if self.skipping:
            return
        if self.parts and not self.parts[-1].endswith("\n"):
            self.emit("\n")
            self.paragraphs += 1

    # -- scanning ----------------------------------------------------------------

    def run(self) -> None:
        depth = 1
        while self.position < len(self.data):
            if depth > MAX_RTF_GROUP_DEPTH:
                raise ReaderRefused(
                    "This RTF nests braces beyond any real document's structure; it was not read.",
                    code="rtf_too_deep",
                    remediation="The file may be damaged or deliberately hostile.",
                    fmt="rtf",
                )
            char = self.data[self.position : self.position + 1]
            if char == b"{":
                depth += 1
                self.stack.append({"skip": self.skipping, "star": False})
                self.position += 1
            elif char == b"}":
                depth -= 1
                if depth <= 0:
                    return  # the document's closing brace; trailing bytes are not body text
                self.stack.pop()
                self.position += 1
            elif char == b"\\":
                self.scan_control()
            elif char in (b"\r", b"\n"):
                self.position += 1  # a bare line break is a token separator, not a paragraph
            else:
                self.position += 1
                if not self.skipping:
                    self.emit(self._decode(char))

    def _decode(self, byte: bytes) -> str:
        try:
            return byte.decode(self.code_page)
        except (UnicodeDecodeError, LookupError):
            return byte.decode("cp1252", "replace")

    def scan_control(self) -> None:
        data = self.data
        start = self.position + 1
        if start >= len(data):
            self.position = len(data)
            return
        first = data[start : start + 1]
        if first == b"*":
            # Optional-destination marker: the group's fate waits on its first control word.
            self.position = start + 1
            self.stack[-1]["star"] = True
            return
        if first == b"'":
            match = _RTF_HEX_ESCAPE.match(b"'" + data[start + 1 : start + 3])
            if match:
                self.position = start + 3
                if not self.skipping:
                    self.emit(self._decode(bytes([int(match.group(1), 16)])))
                return
            self.position = start + 1
            return
        if first.isalpha():
            end = start
            while end < len(data) and (data[end : end + 1].isalpha()):
                end += 1
            word = data[start:end].decode("ascii", "replace")
            parameter: int | None = None
            if end < len(data) and (data[end : end + 1] == b"-"):
                number_end = end + 1
                while number_end < len(data) and data[number_end : number_end + 1].isdigit():
                    number_end += 1
                parameter = int(data[end + 1 : number_end] or b"0")
                end = number_end
            elif end < len(data) and data[end : end + 1].isdigit():
                number_end = end
                while number_end < len(data) and data[number_end : number_end + 1].isdigit():
                    number_end += 1
                parameter = int(data[end:number_end])
                end = number_end
            if end < len(data) and data[end : end + 1] == b" ":
                end += 1  # a control word's delimiter space is consumed, not emitted
            self.position = end
            self.handle_word(word, parameter)
            return
        # A control SYMBOL: \\{ \\} \\- \\_ \\~ \\: and friends emit their second character.
        self.position = start + 1
        symbol = first.decode("ascii", "replace")
        if not self.skipping and symbol in "{}\\-~:":
            self.emit(symbol)

    def handle_word(self, word: str, parameter: int | None) -> None:
        frame = self.stack[-1]
        if frame["star"]:
            frame["star"] = False
            if word in _RTF_SKIP_DESTINATIONS or word not in _RTF_KNOWN_WORDS:
                frame["skip"] = True
                self.unhandled_destinations.add(word)
                return
        if word == "uc" and parameter is not None:
            self.unicode_skip = max(0, parameter)
            return
        if word == "u" and parameter is not None:
            self.emit_unicode(parameter)
            return
        if word == "bin" and parameter is not None:
            self.position = min(len(self.data), self.position + max(0, parameter))
            return
        if word == "ansicpg" and parameter is not None:
            codec = f"cp{parameter}"
            try:
                b"".decode(codec)
                self.code_page = codec
            except LookupError:
                pass
            return
        if word in _RTF_PARAGRAPH_WORDS:
            self.emit_paragraph_break()
            return
        if word == "tab":
            self.emit("\t")
            return
        if word == "cell":
            # A table cell boundary. Rendered as a separator so columns stay columns; RTF rows
            # are paragraph-shaped and would otherwise glue every cell into one run-on line.
            self.emit(" | ")
            return
        if word == "row":
            self.emit_paragraph_break()
            return
        if word == "emdash":
            self.emit("—" if not self.skipping else "")
            return
        if word == "endash":
            self.emit("–" if not self.skipping else "")
            return
        if word == "bullet":
            self.emit("•" if not self.skipping else "")
            return
        if word in _RTF_SKIP_DESTINATIONS:
            already_inside = self.skipping
            frame["skip"] = True
            # A picture inside an already-skipped object is part of that object: the OUTER
            # container is the thing counted, so hostile nesting cannot inflate the numbers.
            if not already_inside:
                if word == "pict":
                    self.pictures += 1
                elif word == "object":
                    self.objects += 1
            return
        # Unknown but ordinary control words (\\b, \\fs24, ...) format text; they emit nothing.

    def emit_unicode(self, parameter: int) -> None:
        """``\\uN`` carries a signed 16-bit code unit. Surrogate halves pair up; the ``\\uc``
        substitute bytes that follow are SKIPPED, so a glyph is never emitted twice."""
        unit = parameter % 65536
        if 0xD800 <= unit <= 0xDBFF:
            self._pending_surrogate = unit
            self.skip_substitute()
            return
        if 0xDC00 <= unit <= 0xDFFF and getattr(self, "_pending_surrogate", None):
            combined = 0x10000 + ((self._pending_surrogate - 0xD800) << 10) + (unit - 0xDC00)
            self._pending_surrogate = None
            self.emit(chr(combined))
            self.skip_substitute()
            return
        self.emit(chr(unit))
        self.skip_substitute()

    def skip_substitute(self) -> None:
        """Consume the ``\\uc`` alternate representations that follow one ``\\u`` character."""
        for _ in range(self.unicode_skip):
            if self.position >= len(self.data):
                return
            char = self.data[self.position : self.position + 1]
            if char == b"{":
                depth = 1
                while self.position < len(self.data) and depth:
                    if self.data[self.position : self.position + 1] == b"{":
                        depth += 1
                    elif self.data[self.position : self.position + 1] == b"}":
                        depth -= 1
                    self.position += 1
                return
            if char == b"\\":
                if self.data[self.position + 1 : self.position + 2] == b"'":
                    self.position += 3
                    continue
                self.position += 2
                continue
            self.position += 1


#: Words this scanner gives standard meaning to. Any OTHER word after a ``\*`` marker is, by the
#: RTF specification, an optional destination this reader does not model -- and skipping unknown
#: destinations is the safe reading, because a destination's content was never body text.
_RTF_KNOWN_WORDS = frozenset(
    {"uc", "u", "bin", "ansicpg", "par", "line", "sect", "page", "tab", "cell", "row", "emdash", "endash", "bullet"}
    | _RTF_SKIP_DESTINATIONS
)

#: Link targets, counted on the raw bytes: the ``fldinst`` form carries them inside a group the
#: scanner skips, so the count is taken where the targets actually live. The targets themselves
#: are never emitted and never fetched.
_RTF_LINK_PATTERNS = (rb"HYPERLINK\s+\"", rb"\\hyperlink\s")


def _rtf_hyperlink_count(data: bytes) -> int:
    return sum(len(re.findall(pattern, data)) for pattern in _RTF_LINK_PATTERNS)


def read_rtf(data: bytes, *, name: str = "document.rtf") -> ReaderResult:
    """An RTF as one body of paragraphs, with everything the scanner skipped counted."""
    digest = hashlib.sha256(data).hexdigest()
    if len(data) > MAX_ARCHIVE_MEMBER_BYTES:
        raise ReaderRefused(
            f"{name} is {len(data) / (1024 * 1024):.0f} MB, over this reader's limit.",
            code="rtf_too_large",
            fmt="rtf",
        )
    scanner = _RtfScanner(data)
    hyperlink_count = _rtf_hyperlink_count(data)
    try:
        scanner.run()
    except ReaderRefused:
        raise
    except Exception as exc:  # a scanner bug must never become a half-truthful extraction
        raise ReaderRefused(f"{name} could not be read as RTF; it appears malformed.", code="rtf_malformed", fmt="rtf") from exc
    warnings: list[str] = []
    if scanner.truncated:
        warnings.append(f"The document body was cut at {MAX_UNIT_CHARS:,} characters.")
    if scanner.objects:
        warnings.append(
            f"This document embeds {scanner.objects} OLE object(s). They were NOT opened, decoded or run."
        )
    if scanner.pictures:
        warnings.append(f"{scanner.pictures} picture(s) are present; they were not decoded or described.")
    if scanner.hyperlinks or hyperlink_count:
        warnings.append(f"{hyperlink_count or scanner.hyperlinks} hyperlink(s) are referenced; nothing outside the file was fetched.")
    text = "".join(scanner.parts).strip("\n")
    unit = ReaderUnit(
        locator="document body",
        kind=UNIT_SECTION,
        text=text,
        meta={"paragraphs": scanner.paragraphs, "embedded_objects": scanner.objects, "pictures": scanner.pictures},
    )
    return ReaderResult(
        fmt="rtf",
        extractor="vool.rtf 1.0.0 (stdlib scanner)",
        units=(unit,),
        warnings=tuple(warnings),
        omitted=tuple(),
        source_sha256=digest,
        meta={
            "paragraphs": scanner.paragraphs,
            "embedded_objects": scanner.objects,
            "pictures": scanner.pictures,
            "hyperlinks": hyperlink_count,
        },
    )


# --- ODT -------------------------------------------------------------------------------------------

_ANOTHER_NS = ""  # ODF tags are matched by LOCAL name: producers rename namespaces freely.


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def sniff_odt(data: bytes) -> bool:
    """An ODT is a ZIP whose manifest/mimetype declares the ODF TEXT document, or which carries
    content.xml with the ODF text namespace."""
    names = _zip_names(data)
    if not names:
        return False
    if "META-INF/container.xml" in names:
        return False  # that shape belongs to EPUB
    if "xl/workbook.xml" in names or "word/document.xml" in names or "ppt/presentation.xml" in names:
        return False
    if names and "mimetype" in names:
        try:
            with zipfile.ZipFile(_BytesReader(data)) as archive:
                mimetype = archive.read("mimetype")[:120].decode("ascii", "replace").strip()
        except (zipfile.BadZipFile, OSError, ValueError):
            return False
        if mimetype.startswith("application/vnd.oasis.opendocument.") and not mimetype.endswith("text"):
            return False
        return mimetype == "application/vnd.oasis.opendocument.text"
    return "content.xml" in names


def _odt_inline(node: ElementTree.Element) -> str:
    """Inline ODF text: spaces, tabs and line breaks are ELEMENTS, so a flattened join would
    glue words together."""
    parts: list[str] = []
    for element in node.iter():
        local = _local(element.tag)
        if local == "s":
            parts.append(" " * max(1, int(element.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c") or "1")))
        elif local == "tab":
            parts.append("\t")
        elif local == "line-break":
            parts.append("\n")
        elif element.text:
            parts.append(element.text)
        if element.tail:
            parts.append(element.tail)
    return "".join(parts)


def read_odt(data: bytes, *, name: str = "document.odt") -> ReaderResult:
    """An ODT as headings, paragraphs and tables -- the same shape the DOCX reader returns."""
    digest = hashlib.sha256(data).hexdigest()
    archive = _open_package(data, name=name, fmt="odt", marker="content.xml")
    try:
        names = set(archive.namelist())
        manifest_member = "META-INF/manifest.xml"
        if manifest_member in names:
            manifest = _parse(manifest_member, _package_member(archive, manifest_member, name=name, fmt="odt"), name=name, fmt="odt")
            for entry in manifest.iter():
                if _local(entry.tag) == "encryption-data":
                    raise ReaderRefused(
                        f"{name} is encrypted; its content was not read.",
                        code="password_protected",
                        remediation="Attach an unprotected copy of the document.",
                        fmt="odt",
                    )
        content = _parse("content.xml", _package_member(archive, "content.xml", name=name, fmt="odt"), name=name, fmt="odt")
        macros = sorted(member for member in names if member.startswith("Basic/"))
        embedded = sorted(member for member in names if member.startswith("Object ") and member.rstrip("/").split(" ")[-1].isdigit())
        blocks: list[str] = []
        warnings: list[str] = []
        omitted: list[str] = []
        paragraphs = headings = tables = 0

        def walk(node: ElementTree.Element) -> None:
            nonlocal paragraphs, headings, tables
            if len(blocks) >= MAX_DOCX_BLOCKS:
                return
            for child in node:
                local = _local(child.tag)
                if local == "tracked-changes":
                    continue  # change markup is editorial history, not document text
                if local == "h":
                    try:
                        level = min(9, max(1, int(child.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}outline-level") or "1")))
                    except ValueError:
                        level = 1
                    text = _odt_inline(child).strip()
                    if len(blocks) < MAX_DOCX_BLOCKS:
                        headings += 1
                        blocks.append(f"{'#' * level} {text}")
                elif local == "p":
                    text = _odt_inline(child).strip()
                    if len(blocks) < MAX_DOCX_BLOCKS:
                        paragraphs += 1
                        blocks.append(text)
                elif local == "table":
                    rows: list[list[str]] = []
                    for row in child.iter():
                        if _local(row.tag) == "table-row":
                            cells = [
                                " ".join(_odt_inline(cell).split())
                                for cell in row
                                if _local(cell.tag) == "table-cell"
                            ]
                            if cells:
                                rows.append(cells)
                    if rows:
                        tables += 1
                        width = max(len(row) for row in rows)
                        rendered = "\n".join(" | ".join((row + [""] * width)[:width]) for row in rows)
                        blocks.append(f"[table {tables}: {len(rows)} rows x {width} columns]\n{rendered}")
                        if len(blocks) >= MAX_DOCX_BLOCKS:
                            return
                    continue  # a table's paragraphs were consumed by the cells
                walk(child)

        body = None
        for element in content.iter():
            if _local(element.tag) == "body":
                body = element
                break
        if body is not None:
            walk(body)
        if len(blocks) >= MAX_DOCX_BLOCKS:
            omitted.append(f"content after {MAX_DOCX_BLOCKS:,} blocks")
            warnings.append(f"This document is longer than {MAX_DOCX_BLOCKS:,} blocks; the rest was not read.")
        if macros:
            warnings.append(f"This document contains Basic macro code ({macros[0]}). It was NOT run and its code was not read.")
        if embedded:
            warnings.append(f"This document embeds {len(embedded)} object(s). They were not opened.")
    finally:
        archive.close()

    text = "\n".join(blocks).strip("\n")
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS]
        warnings.append(f"The document body was cut at {MAX_UNIT_CHARS:,} characters.")
    unit = ReaderUnit(
        locator="document body",
        kind=UNIT_SECTION,
        text=text,
        meta={"paragraphs": paragraphs, "headings": headings, "tables": tables},
    )
    return ReaderResult(
        fmt="odt",
        extractor="vool.odt 1.0.0 (stdlib zipfile + ElementTree)",
        units=(unit,),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "paragraphs": paragraphs,
            "headings": headings,
            "tables": tables,
            "has_macros": bool(macros),
            "embedded_objects": len(embedded),
        },
    )


# --- EPUB ------------------------------------------------------------------------------------------

_XHTML_MEDIA_TYPES = ("application/xhtml+xml", "text/html")


def sniff_epub(data: bytes) -> bool:
    """An EPUB is a ZIP whose container points at an OPF package document."""
    names = _zip_names(data)
    return names is not None and "META-INF/container.xml" in names


def _xhtml_sections(payload: bytes, *, name: str) -> list[tuple[int, str, str]]:
    """One XHTML section as (level, text, inline-tag) blocks: headings and paragraphs, in order."""
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return []
    blocks: list[tuple[int, str, str]] = []
    head = ""
    for element in root.iter():
        local = _local(element.tag)
        if local == "title" and head == "":
            head = "".join(element.itertext()).strip()
        if local == "style" or local == "script":
            continue
    def walk(node: ElementTree.Element) -> None:
        for child in node:
            local = _local(child.tag)
            if local in ("style", "script", "head"):
                continue
            if local in ("h1", "h2", "h3", "h4", "h5", "h6"):
                text = " ".join("".join(child.itertext()).split())
                if text:
                    blocks.append((int(local[1]), text, "heading"))
            elif local == "p":
                text = " ".join("".join(child.itertext()).split())
                if text:
                    blocks.append((0, text, "p"))
            elif local == "li":
                text = " ".join("".join(child.itertext()).split())
                if text:
                    blocks.append((0, f"• {text}", "li"))
            elif local == "table":
                rows: list[list[str]] = []
                for row in child.iter():
                    if _local(row.tag) == "tr":
                        cells = [" ".join("".join(cell.itertext()).split()) for cell in row if _local(cell.tag) in ("td", "th")]
                        if cells:
                            rows.append(cells)
                if rows:
                    width = max(len(row) for row in rows)
                    rendered = "\n".join(" | ".join((row + [""] * width)[:width]) for row in rows)
                    blocks.append((0, f"[table]\n{rendered}", "table"))
                continue
            walk(child)

    body = None
    for element in root.iter():
        if _local(element.tag) == "body":
            body = element
            break
    if body is not None:
        walk(body)
    del head
    return blocks


def read_epub(data: bytes, *, name: str = "book.epub") -> ReaderResult:
    """An EPUB as one unit per spine section: the book's own reading order, headings kept.

    The spine is the publication's declared order of contents -- the only order that deserves the
    name. Images, stylesheets and fonts are container furniture: counted in warnings, never
    fetched or rendered. Encrypted (DRM) packages are refused as unreadable, never scraped.
    """
    digest = hashlib.sha256(data).hexdigest()
    archive = _open_package(data, name=name, fmt="epub", marker="META-INF/container.xml")
    try:
        names = set(archive.namelist())
        if "META-INF/encryption.xml" in names:
            encryption = _parse("META-INF/encryption.xml", _package_member(archive, "META-INF/encryption.xml", name=name, fmt="epub"), name=name, fmt="epub")
            encrypted = any(
                _local(element.tag).lower() in ("encrypteddata", "encrypted-data")
                for element in encryption.iter()
            )
            if encrypted:
                raise ReaderRefused(
                    f"{name} is encrypted (DRM); its content was not read.",
                    code="password_protected",
                    remediation="Attach an unprotected copy of the book.",
                    fmt="epub",
                )
        container = _parse("META-INF/container.xml", _package_member(archive, "META-INF/container.xml", name=name, fmt="epub"), name=name, fmt="epub")
        opf_member = ""
        for rootfile in container.iter():
            if _local(rootfile.tag) == "rootfile" and rootfile.get("full-path"):
                opf_member = rootfile.get("full-path")
                break
        if not opf_member or opf_member not in names:
            raise ReaderRefused(
                f"{name}'s container points at {opf_member or 'no package document'}, which the file does not contain.",
                code="epub_corrupt",
                fmt="epub",
            )
        opf = _parse(opf_member, _package_member(archive, opf_member, name=name, fmt="epub"), name=name, fmt="epub")
        title = creator = ""
        for element in opf.iter():
            local = _local(element.tag)
            if local == "title" and not title:
                title = " ".join("".join(element.itertext()).split())
            elif local == "creator" and not creator:
                creator = " ".join("".join(element.itertext()).split())
        manifest: dict[str, tuple[str, str]] = {}
        for item in opf.iter():
            if _local(item.tag) == "item" and item.get("id"):
                manifest[item.get("id")] = (item.get("href") or "", item.get("media-type") or "")
        spine: list[tuple[str, bool]] = []
        for itemref in opf.iter():
            if _local(itemref.tag) == "itemref" and itemref.get("idref"):
                spine.append((itemref.get("idref"), (itemref.get("linear") or "yes").lower() != "no"))

        warnings: list[str] = []
        omitted: list[str] = []
        units: list[ReaderUnit] = []
        images = 0
        metadata_unit = ReaderUnit(
            locator="book metadata",
            kind=UNIT_SECTION,
            text="\n".join(filter(None, [f"Title: {title}" if title else "", f"Author: {creator}" if creator else ""])),
            meta={"title": title, "creator": creator},
        )
        if metadata_unit.text:
            units.append(metadata_unit)
        if len(spine) > MAX_EPUB_SECTIONS:
            warnings.append(f"This book has {len(spine)} spine sections; only the first {MAX_EPUB_SECTIONS} were read.")
            for dropped in range(MAX_EPUB_SECTIONS + 1, len(spine) + 1):
                omitted.append(f"section {dropped}")
            spine = spine[:MAX_EPUB_SECTIONS]
        opf_dir = opf_member.rpartition("/")[0] + "/" if "/" in opf_member else ""
        sections = 0
        for order, (idref, linear) in enumerate(spine, start=1):
            href, media_type = manifest.get(idref, ("", ""))
            member = _resolve_part(opf_dir, href)
            if media_type.startswith("image/") or media_type == "image/svg+xml":
                images += 1
                continue
            if media_type not in _XHTML_MEDIA_TYPES or not member or member not in names:
                if media_type and media_type not in ("",) and not media_type.startswith("image/"):
                    omitted.append(f"section {order} ({media_type}): this reader extracts XHTML sections only")
                continue
            blocks = _xhtml_sections(_package_member(archive, member, name=name, fmt="epub"), name=name)
            sections += 1
            lines: list[str] = []
            section_title = ""
            for level, text, shape in blocks:
                if shape == "heading":
                    lines.append(f"{'#' * level} {text}")
                    if not section_title:
                        section_title = text
                else:
                    lines.append(text)
            body = "\n".join(lines)
            if len(body) > MAX_UNIT_CHARS:
                body = body[:MAX_UNIT_CHARS]
                omitted.append(f"section {order}: text cut at {MAX_UNIT_CHARS:,} characters")
            if not body:
                body = "[no text extracted]"
            suffix = "" if linear else " [non-linear section]"
            units.append(
                ReaderUnit(
                    locator=f"section {order}{suffix}",
                    kind=UNIT_SECTION,
                    text=body,
                    meta={"href": href, "title": section_title, "linear": linear},
                )
            )
        if images:
            warnings.append(f"{images} image section(s) are present; they were not rendered or described.")
        warnings.append("Links and images inside sections were not fetched; the reader opens no network connections.")
    finally:
        archive.close()

    return ReaderResult(
        fmt="epub",
        extractor="vool.epub 1.0.0 (stdlib zipfile + ElementTree)",
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "title": title,
            "creator": creator,
            "sections": sections,
            "spine_items": len(spine),
        },
    )


# --- legacy XLS (BIFF through the pinned xlrd decoder) ----------------------------------------------
#
# The 1997 binary format has no standard-library reader. The decoder used here is ``xlrd==2.0.1``
# -- pure Python, no native code, pinned, and resolved from the lane-local dependency directory
# the same way the PDF fallback's ``pypdf`` is. It parses IN PROCESS, so the external-decoder
# sandbox does not apply; what bounds it instead is the cell/row/sheet budget below plus xlrd's
# own record checks, exactly the trust model pypdf already has. Where the package is absent the
# format is BLOCKED and says so -- never "read as empty".
#
# xlrd returns cell VALUES only, so formula text would be lost -- which would quietly erase the
# formula/cache distinction the spreadsheet contract requires. The reader therefore walks the raw
# BIFF stream itself (through xlrd's own compound-document reader) and records which cells carry
# FORMULA records; those render their cached value marked as a claim, not a recomputation.

_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: OLE directory entries are UTF-16LE. A workbook stream is named "Workbook" (BIFF8) or "Book"
#: (older); their presence is what distinguishes an XLS container from any other OLE file.
_XLS_STREAM_MARKERS = (b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k", b"B\x00o\x00o\x00k\x00")
#: The BIFF FORMULA record id (identical across BIFF versions this reader accepts).
_BIFF_FORMULA_RECORD = 0x0006


def sniff_xls(data: bytes) -> bool:
    """An OLE compound file that carries a workbook stream."""
    if bytes(data[:8]) != _XLS_MAGIC:
        return False
    return any(marker in data for marker in _XLS_STREAM_MARKERS)


def _import_xrd():
    try:
        import xlrd

        return xlrd
    except ImportError:
        return None


def xlrd_available() -> bool:
    return _import_xrd() is not None


def _xls_formula_cells(data: bytes) -> set[tuple[int, int]]:
    """The (row, column) set of every FORMULA record in the workbook stream.

    Only the record's first fields are read: row and column. The formula EXPRESSION is not
    decompiled -- what matters for honesty is WHICH cells are formulas, so their cached values
    are labelled as caches. A damaged compound document yields an empty set here while the main
    parse above decides the file's fate; this scan never invents a refusal of its own.
    """
    try:
        from xlrd import compdoc

        if bytes(data[:8]) != compdoc.SIGNATURE:
            return set()
        compound = compdoc.CompDoc(data)
        stream = b""
        for qname in ("Workbook", "Book"):
            mem, base, length = compound.locate_named_stream(qname)
            if mem:
                stream = bytes(mem[base : base + length])
                break
        if not stream:
            return set()
        cells: set[tuple[int, int]] = set()
        position = 0
        total = len(stream)
        while position + 4 <= total:
            record_id, size = struct.unpack_from("<HH", stream, position)
            position += 4
            if size < 0 or position + size > total:
                break
            if record_id == _BIFF_FORMULA_RECORD and size >= 4:
                row, column = struct.unpack_from("<HH", stream, position)
                cells.add((row, column))
            position += size
        return cells
    except Exception:
        return set()


def read_xls(data: bytes, *, name: str = "workbook.xls") -> ReaderResult:
    """A legacy workbook as one unit per sheet -- the same grid shape the XLSX reader returns."""
    xlrd = _import_xrd()
    if xlrd is None:
        raise ReaderUnavailable(
            f"{name} is a legacy Excel (.xls) file, and this machine is missing the pinned "
            f"decoder for that format (xlrd==2.0.1), so it was not read.",
            code="xls_decoder_unavailable",
            remediation="Install the pinned lane dependency xlrd==2.0.1, or save the file as .xlsx and attach it again.",
            fmt="xls",
        )
    if not sniff_xls(data):
        raise ReaderRefused(
            f"{name} is not a legacy Excel workbook (no OLE container with a workbook stream).",
            code="not_a_xls",
            fmt="xls",
        )
    digest = hashlib.sha256(data).hexdigest()
    try:
        book = xlrd.open_workbook(file_contents=data)
    except xlrd.XLRDError as exc:
        message = str(exc).lower()
        if "encrypted" in message:
            raise ReaderRefused(
                f"{name} is encrypted; its content was not read.",
                code="password_protected",
                remediation="Attach an unprotected copy of the workbook.",
                fmt="xls",
            ) from exc
        raise ReaderRefused(
            f"{name} could not be read as a legacy Excel workbook ({exc}).",
            code="xls_corrupt",
            remediation="Re-save the file from Excel as .xlsx and attach it again.",
            fmt="xls",
        ) from exc

    formula_cells = _xls_formula_cells(data)
    date_epoch_note = "the workbook's 1904 date system" if book.datemode == 1 else ""
    warnings: list[str] = []
    omitted: list[str] = []
    units: list[ReaderUnit] = []
    total_cells = 0
    sheet_count = book.nsheets
    if sheet_count > MAX_SHEETS:
        warnings.append(f"This workbook has {sheet_count} sheets; only the first {MAX_SHEETS} were read.")
    for sheet_index in range(min(sheet_count, MAX_SHEETS)):
        if total_cells >= MAX_SHEET_CELLS:
            omitted.append(f"sheet index {sheet_index} and beyond")
            continue
        sheet = book.sheet_by_index(sheet_index)
        unit, cells_used, sheet_omissions = _read_xls_sheet(
            sheet,
            formula_cells=formula_cells,
            datemode=book.datemode,
            name=name,
            cell_budget=MAX_SHEET_CELLS - total_cells,
        )
        units.append(unit)
        total_cells += cells_used
        omitted.extend(sheet_omissions)
    has_macros = any(marker in data for marker in (b"_\x00V\x00B\x00A\x00", b"_VBA_PROJECT_CUR"))
    if has_macros:
        warnings.append("This workbook contains a macro project. It was NOT run and its code was not read.")
    warnings.append("Formulas were not executed: values shown for formula cells are the file's own cached results and may be stale.")
    if date_epoch_note:
        warnings.append(f"Dates are interpreted under {date_epoch_note}.")

    return ReaderResult(
        fmt="xls",
        extractor=f"vool.xls 1.0.0 (xlrd {xlrd.__VERSION__})",
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "sheets": len(units),
            "cells": total_cells,
            "formulas": sum(int(unit.meta.get("formulas") or 0) for unit in units),
            "has_macros": has_macros,
            "date_system": "1904" if book.datemode == 1 else "1900",
            "decoder": "xlrd==2.0.1",
        },
    )


def _read_xls_sheet(sheet: Any, *, formula_cells: set[tuple[int, int]], datemode: int, name: str, cell_budget: int) -> tuple[ReaderUnit, int, list[str]]:
    """One BIFF sheet as a grid, bounded by rows/cells like its XLSX sibling."""
    xlrd = _import_xrd()
    error_text = getattr(xlrd.biffh, "error_text_from_code", {})
    rows: list[tuple[int, dict[int, str]]] = []
    formula_count = 0
    cells_read = 0
    omissions: list[str] = []
    width = 0
    row_cut = False
    row_limit = min(sheet.nrows, MAX_XLSX_ROWS)
    for row_index in range(row_limit):
        if len(rows) >= MAX_XLSX_ROWS or cells_read >= cell_budget:
            row_cut = True
            break
        cells: dict[int, str] = {}
        for column_index in range(min(sheet.ncols, MAX_XLSX_GRID_COLUMNS)):
            if cells_read >= cell_budget:
                break
            cell = sheet.cell(row_index, column_index)
            kind = cell.ctype
            if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                continue
            text = _xls_cell_text(cell, kind=kind, error_text=error_text, datemode=datemode)
            if not text:
                continue
            is_formula = (row_index, column_index) in formula_cells
            if is_formula:
                formula_count += 1
                state = "cached, not recalculated" if kind not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else "NO cached result in the file"
                text = f"{text} [= formula; {state}]".strip()
            cells[column_index + 1] = text
            cells_read += 1
            width = max(width, column_index + 1)
        if cells:
            rows.append((row_index + 1, cells))
    if row_cut:
        omissions.append(f'sheet "{sheet.name}": rows beyond {len(rows):,} (row or cell budget)')
    header = "row | " + " | ".join(_column_name(index) for index in range(1, width + 1)) if width else "row | (no cells)"
    lines = [
        f'sheet "{sheet.name}" -- {len(rows):,} row(s) with content, {cells_read:,} non-empty cell(s), {formula_count:,} formula cell(s)',
        header,
    ]
    for row_number, cells in rows:
        rendered = " | ".join(_grid_cell(cells.get(column, "")) for column in range(1, width + 1))
        lines.append(f"{row_number:>4} | {rendered}")
    text = "\n".join(lines)
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS]
        omissions.append(f'sheet "{sheet.name}": text cut at {MAX_UNIT_CHARS:,} characters')
    return (
        ReaderUnit(
            locator=f'sheet "{sheet.name}"',
            kind=UNIT_SHEET,
            text=text,
            meta={"cells": cells_read, "formulas": formula_count, "rows": len(rows)},
        ),
        cells_read,
        omissions,
    )


def _xls_cell_text(cell: Any, *, kind: int, error_text: dict[int, str], datemode: int) -> str:
    """One BIFF cell as text. DATE cells carry the raw serial in ``value``: the conversion is
    shown with its serial, exactly as the XLSX reader states it."""
    xlrd = _import_xrd()
    if kind == xlrd.XL_CELL_TEXT:
        return str(cell.value)
    if kind == xlrd.XL_CELL_NUMBER:
        number = float(cell.value)
        if number == int(number) and abs(number) < 1e15:
            return str(int(number))
        return repr(number)
    if kind == xlrd.XL_CELL_DATE:
        serial = float(cell.value)
        if serial == int(serial) and datemode == 0 and 1 <= int(serial) <= 59:
            stamp = _EPOCH_PRE_LEAP + _dt.timedelta(days=int(serial) - 1)
        elif serial == int(serial) and datemode == 0 and int(serial) == 60:
            return f"1900-02-29 (date-formatted serial {serial:g}; not a real date)"
        else:
            stamp = xlrd.xldate.xldate_as_datetime(serial, datemode)
        rendered = stamp.strftime("%Y-%m-%d %H:%M:%S").replace(" 00:00:00", "")
        return f"{rendered} (date-formatted serial {serial!r})"
    if kind == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if cell.value else "FALSE"
    if kind == xlrd.XL_CELL_ERROR:
        return error_text.get(cell.value, f"(cached error value {cell.value})")
    return ""
