"""DOCX and legacy DOC.

**DOCX** is a ZIP of XML, so it is read here directly with the standard library: no third-party
package, and no code path that could execute anything. Paragraphs keep their heading level, and a
table is emitted as a table -- rows and cells, in document order, interleaved with the prose that
surrounds them. A table flattened into a wall of words cannot be added up, so it is not flattened.

What is deliberately NOT read: ``vbaProject.bin`` (macros), embedded OLE objects, and external
relationship targets. A macro is code, an embedded object is a container this reader is not, and an
external target is a URL. None of the three is document text, and fetching or running any of them
is exactly the behaviour the reader contract forbids. Their presence is reported instead, because
an operator should know a document carries a macro even though the macro was not run.

**Legacy DOC** (the pre-2007 OLE compound format) has no ZIP and no XML; it needs a real
converter. macOS ships one: ``/usr/bin/textutil``, the same converter TextEdit uses. It runs under
the reader sandbox with no network. Where it is absent, DOC is BLOCKED and says so -- a partial
salvage of the readable ASCII runs out of a binary OLE stream would be a plausible-looking text
that silently drops tables, footnotes and half the words, which is worse than a clear refusal.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ._limits import MAX_ARCHIVE_MEMBER_BYTES, MAX_DOCX_BLOCKS, MAX_UNIT_CHARS
from ._sandbox import Scratch, extraction_budget, require_confinement, run_confined
from ._types import UNIT_SECTION, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_EXTRACTOR = "vool.docx 1.0.0 (stdlib zipfile + ElementTree)"
TEXTUTIL = "/usr/bin/textutil"
#: OLE2 compound-file signature: legacy .doc, .xls, .ppt all start with it.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"


def sniff_docx(data: bytes) -> bool:
    """A DOCX is a ZIP whose central directory holds ``word/document.xml``."""
    if not bytes(data[:4]).startswith(ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(_BytesReader(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return "word/document.xml" in names


def sniff_doc(data: bytes) -> bool:
    """OLE magic AND no workbook stream: an XLS is also an OLE compound file, and the two must
    never share a reader -- a workbook named .doc would otherwise be "converted" to mojibake."""
    if bytes(data[:8]) != OLE_MAGIC:
        return False
    return not (
        b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k" in data or b"B\x00o\x00o\x00k\x00" in data
    )


class _BytesReader:
    """A read-only file-like over bytes, so nothing is written to disk to inspect an upload."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._offset :]
            self._offset = len(self._data)
            return chunk
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._offset, 2: len(self._data)}[whence]
        self._offset = max(0, base + offset)
        return self._offset

    def tell(self) -> int:
        return self._offset

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True


def _paragraph_text(node: ElementTree.Element) -> str:
    """A paragraph's visible characters, tabs and breaks included, in document order.

    ``w:t`` alone loses tabs (``w:tab``) and line breaks (``w:br``) -- which is how a two-column
    tabbed layout silently becomes one run-on line. They are kept.
    """
    parts: list[str] = []
    for element in node.iter():
        tag = element.tag
        if tag == f"{_W}t":
            parts.append(element.text or "")
        elif tag == f"{_W}tab":
            parts.append("\t")
        elif tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
        elif tag == f"{_W}noBreakHyphen":
            parts.append("-")
    return "".join(parts)


def _style_of(node: ElementTree.Element) -> str:
    properties = node.find(f"{_W}pPr")
    if properties is None:
        return ""
    style = properties.find(f"{_W}pStyle")
    return (style.get(f"{_W}val") or "") if style is not None else ""


def _heading_level(style: str) -> int:
    match = re.fullmatch(r"(?i)heading\s*([1-9])", style.replace("-", " ").strip())
    return int(match.group(1)) if match else 0


def _table_text(node: ElementTree.Element) -> tuple[str, int, int]:
    """A table rendered as pipe-delimited rows: columns survive, so the numbers can be added."""
    rows: list[list[str]] = []
    for row in node.findall(f"{_W}tr"):
        cells: list[str] = []
        for cell in row.findall(f"{_W}tc"):
            text = " ".join(_paragraph_text(p).strip() for p in cell.findall(f"{_W}p"))
            cells.append(" ".join(text.split()))
        if cells:
            rows.append(cells)
    if not rows:
        return "", 0, 0
    width = max(len(row) for row in rows)
    lines = [" | ".join((row + [""] * width)[:width]) for row in rows]
    return "\n".join(lines), len(rows), width


def read_docx(data: bytes, *, name: str = "document.docx") -> ReaderResult:
    digest = hashlib.sha256(data).hexdigest()
    try:
        archive = zipfile.ZipFile(_BytesReader(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ReaderRefused(
            f"{name} is not a readable DOCX: its ZIP container is damaged.",
            code="docx_corrupt",
            remediation="Re-save or re-download the document and attach it again.",
            fmt="docx",
        ) from exc
    with archive:
        names = archive.namelist()
        if "word/document.xml" not in names:
            raise ReaderRefused(
                f"{name} is a ZIP file but not a Word document (it has no word/document.xml).",
                code="docx_not_a_document",
                fmt="docx",
            )
        info = archive.getinfo("word/document.xml")
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ReaderRefused(
                f"{name} holds a {info.file_size / (1024 * 1024):.0f} MB document body, over this reader's limit.",
                code="docx_too_large",
                fmt="docx",
            )
        try:
            body_xml = archive.read("word/document.xml")
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ReaderRefused(
                f"{name} could not be decompressed; it may be corrupt or password-protected.",
                code="docx_unreadable",
                remediation="If the document is password-protected, attach an unprotected copy.",
                fmt="docx",
            ) from exc
        macros = [n for n in names if n.lower().endswith("vbaproject.bin")]
        embedded = [n for n in names if n.lower().startswith("word/embeddings/")]
    try:
        # `ElementTree` resolves no external entities and fetches no DTD, so document XML cannot
        # reach the filesystem or the network through this parse.
        root = ElementTree.fromstring(body_xml)
    except ElementTree.ParseError as exc:
        raise ReaderRefused(f"{name} has a damaged document body and was not read.", code="docx_corrupt", fmt="docx") from exc

    body = root.find(f"{_W}body")
    blocks: list[str] = []
    warnings: list[str] = []
    omitted: list[str] = []
    tables = 0
    paragraphs = 0
    headings = 0
    if body is not None:
        for child in list(body):
            if len(blocks) >= MAX_DOCX_BLOCKS:
                omitted.append(f"content after {MAX_DOCX_BLOCKS:,} blocks")
                warnings.append(f"This document is longer than {MAX_DOCX_BLOCKS:,} blocks; the rest was not read.")
                break
            if child.tag == f"{_W}p":
                text = _paragraph_text(child)
                style = _style_of(child)
                level = _heading_level(style)
                if level:
                    headings += 1
                    blocks.append(f"{'#' * level} {text.strip()}")
                elif text.strip():
                    paragraphs += 1
                    blocks.append(text)
                else:
                    blocks.append("")
            elif child.tag == f"{_W}tbl":
                rendered, rows, columns = _table_text(child)
                if rendered:
                    tables += 1
                    blocks.append(f"[table {tables}: {rows} rows x {columns} columns]\n{rendered}")
    if macros:
        warnings.append(f"This document contains a macro project ({macros[0]}). It was NOT run and its code was not read.")
    if embedded:
        warnings.append(f"This document embeds {len(embedded)} object(s). They were not opened.")

    text = "\n".join(blocks).strip("\n")
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS]
        warnings.append(f"The document body was cut at {MAX_UNIT_CHARS:,} characters.")
    unit = ReaderUnit(locator="document body", kind=UNIT_SECTION, text=text, meta={"paragraphs": paragraphs, "tables": tables, "headings": headings})
    return ReaderResult(
        fmt="docx",
        extractor=DOCX_EXTRACTOR,
        units=(unit,),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={"paragraphs": paragraphs, "tables": tables, "headings": headings, "has_macros": bool(macros), "embedded_objects": len(embedded)},
    )


#: Above this share of NUL and C0 control characters, the "converted" text is the file's own
#: bytes, not its words.
_BINARY_LEAK_RATIO = 0.02


def _refuse_unconverted(name: str, text: str) -> None:
    """Refuse a conversion that returned the document's BYTES rather than its text.

    Measured 2026-09-03: given a damaged OLE file, textutil exits 0 and prints the raw bytes as
    if they were plain text -- 4,104 characters of mojibake for a 4 KB corrupt file. Exit status
    alone therefore cannot tell a converted document from an unconverted one, and without this
    check the reader would hand that mojibake to a model as the document's contents. (The older
    `-output` form behaved the same way; this is not a cost of converting through stdout.)
    """
    if not text:
        return
    suspicious = sum(1 for ch in text if ch == "\x00" or (ord(ch) < 32 and ch not in "\t\n\r"))
    if "\x00" in text or suspicious / len(text) > _BINARY_LEAK_RATIO:
        raise ReaderRefused(
            f"{name} could not be converted: the converter returned the file's raw bytes rather than "
            f"document text, which means it is damaged or is not really a Word document.",
            code="doc_conversion_failed",
            remediation="Re-save the document from Word as .docx or PDF, and attach that instead.",
            fmt="doc",
        )


def doc_converter_available() -> bool:
    """A converter is only 'available' if it can also be CONFINED; an unconfinable one is not."""
    from . import _sandbox

    return platform.system() == "Darwin" and os.access(TEXTUTIL, os.X_OK) and _sandbox.confinement_available()


def read_doc(data: bytes, *, name: str = "document.doc") -> ReaderResult:
    """Legacy DOC through the system converter, sandboxed. Absent a converter: BLOCKED, plainly."""
    if not sniff_doc(data):
        raise ReaderRefused(f"{name} is not a legacy Word document (no OLE compound-file header).", code="not_a_doc", fmt="doc")
    require_confinement("doc")
    if not doc_converter_available():
        raise ReaderUnavailable(
            f"{name} is a legacy Word (.doc) file and this machine has no converter for that format, so it was not read.",
            code="doc_converter_unavailable",
            remediation="Save the file as .docx or PDF and attach it again.",
            fmt="doc",
        )
    digest = hashlib.sha256(data).hexdigest()
    with Scratch("vool-doc-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / "input.doc"
        source.write_bytes(data)
        # `-stdout`, NOT `-output <file>`. The file form makes textutil's Cocoa save path write
        # through the per-user Darwin temp container, which previously earned it a
        # `file-write*` grant over that whole directory. A review demonstrated what that grant
        # actually permits: under the same construction, a process could overwrite an unrelated
        # sibling file there (exit 0, bytes CHANGED) -- read denial does not prevent mutation, and
        # such writes are outside the scratch quota's scan entirely.
        #
        # The stdout form needs NO write scope beyond the scratch directory. Measured: it converts
        # correctly under the content-allowlist profile with no temp grant at all, while a sibling
        # write under the same profile is refused and the original bytes survive.
        result = run_confined(
            [TEXTUTIL, "-convert", "txt", "-encoding", "UTF-8", "-stdout", str(source)],
            scratch=scratch.path,
            fmt="doc",
        )
        if result.returncode != 0 or not result.stdout.strip():
            detail = result.stderr.decode("utf-8", "replace").strip()[:200]
            raise ReaderRefused(
                f"{name} could not be converted; it may be corrupt or password-protected"
                + (f" ({detail})" if detail else "")
                + ".",
                code="doc_conversion_failed",
                remediation="If the document is password-protected, attach an unprotected copy; otherwise re-save it as .docx.",
                fmt="doc",
            )
        text = result.stdout.decode("utf-8", errors="replace")
        _refuse_unconverted(name, text)
        confined = bool(result.network_confined)
    warnings: list[str] = []
    if len(text) > MAX_UNIT_CHARS:
        text = text[:MAX_UNIT_CHARS]
        warnings.append(f"The document was cut at {MAX_UNIT_CHARS:,} characters.")
    warnings.append("Converted from legacy .doc; the converter keeps text and tables as plain text and drops layout.")
    unit = ReaderUnit(locator="document body", kind=UNIT_SECTION, text=text.strip("\n"), meta={"converter": "textutil"})
    return ReaderResult(
        fmt="doc",
        extractor="textutil (macOS) via vool.doc 1.0.0",
        units=(unit,),
        warnings=tuple(warnings),
        source_sha256=digest,
        # Confinement is not asserted here: `run_confined` refuses to spawn at all when it cannot
        # confine, so reaching this line IS the evidence, and `result.network_confined` carries it
        # from the run that actually happened rather than from a literal written by hand.
        meta={"converter": "textutil", "network_confined": bool(confined)},
    )


def decoders_available() -> dict[str, Any]:
    return {"docx": True, "doc": doc_converter_available()}


__all__ = [
    "DOCX_EXTRACTOR",
    "Path",
    "decoders_available",
    "doc_converter_available",
    "read_doc",
    "read_docx",
    "sniff_doc",
    "sniff_docx",
]
