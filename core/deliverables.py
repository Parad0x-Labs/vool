"""Files the assistant hands to the user: built, verified, stored, and served as a download.

When the user asks for an answer *as a file* -- "give me that as a Word document", "export the
table to Excel", "zip these three scripts" -- the reply is a file, not a code block they have to
copy into one. This module is the one road for that:

    `create_deliverable` (format chosen, bytes built from the model's TEXT) -> verified by reading
    it back with this runtime's own readers -> stored under the runtime's data directory with a
    manifest -> `read_deliverable` (owner session only) -> served by the download route.

Rules this module owns:

* **The model supplies text; this module supplies the format.** Every input is a string: markdown
  for documents, slides and PDF, CSV or markdown tables for spreadsheets, raw text for code and
  data. The model never writes OOXML or binary, so a malformed file cannot be the model's typo.
* **Nothing is offered that does not open.** A built docx, xlsx, pptx, pdf, zip or tar.gz is read
  back by ``core.artifact_readers`` -- the same readers attachments use -- and must yield the
  content it was built from. A file that fails its own read-back is refused, not delivered.
* **Structured text must parse.** JSON, YAML and XML are parsed before they are written; a file
  that would not load in the user's tool is refused with the parser's reason.
* **Names are labels, never paths.** A filename is reduced to one safe component; bundle members
  are relative, unique and never escape the bundle.
* **Bounded.** Input characters, output bytes, bundle members and retention are all capped and
  stated in the refusal when crossed.
* **No new dependencies.** Office formats are written as their documented XML packages with the
  standard library; PDF uses the renderer the chat export already ships.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import tarfile
import threading
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from core.runtime_paths import active_data_dir

#: Characters of text one deliverable may be built from (summed over bundle members).
MAX_SOURCE_CHARS = 2_000_000
#: Bytes one built file may occupy.
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
#: Files one zip or tar.gz bundle may hold.
MAX_BUNDLE_FILES = 50
#: Rows and columns one spreadsheet sheet may hold; sheets per workbook; slides per deck.
MAX_SHEET_ROWS = 100_000
MAX_SHEET_COLUMNS = 512
MAX_SHEETS = 32
MAX_SLIDES = 200
#: A built file is kept this long after creation, then swept. Long enough to download tomorrow,
#: short enough that the data directory is not an archive of every file ever asked for.
RETENTION_SECONDS = 7 * 24 * 3600
MAX_NAME_CHARS = 120

_ID_RE = re.compile(r"^dlv_[0-9a-f]{32}$")
_LOCK = threading.RLock()

#: Format -> (canonical extension, media type).
FORMATS: dict[str, tuple[str, str]] = {
    "docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pptx": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "pdf": (".pdf", "application/pdf"),
    "md": (".md", "text/markdown"),
    "txt": (".txt", "text/plain"),
    "csv": (".csv", "text/csv"),
    "tsv": (".tsv", "text/tab-separated-values"),
    "json": (".json", "application/json"),
    "yaml": (".yaml", "application/yaml"),
    "xml": (".xml", "application/xml"),
    "html": (".html", "text/html"),
    "zip": (".zip", "application/zip"),
    "tar.gz": (".tar.gz", "application/gzip"),
}
#: Source code and other plain-text kinds, written verbatim as UTF-8 under their own extension.
CODE_EXTENSIONS: dict[str, str] = {
    ".py": "text/x-python", ".js": "text/javascript", ".mjs": "text/javascript", ".ts": "text/typescript",
    ".tsx": "text/typescript", ".jsx": "text/javascript", ".css": "text/css", ".sql": "application/sql",
    ".sh": "text/x-sh", ".rb": "text/x-ruby", ".go": "text/x-go", ".rs": "text/x-rust", ".java": "text/x-java",
    ".kt": "text/x-kotlin", ".swift": "text/x-swift", ".c": "text/x-c", ".h": "text/x-c", ".cpp": "text/x-c++",
    ".hpp": "text/x-c++", ".cs": "text/x-csharp", ".toml": "application/toml", ".ini": "text/plain",
    ".cfg": "text/plain", ".log": "text/plain", ".rst": "text/x-rst", ".tex": "text/x-tex", ".svg": "image/svg+xml",
    ".yml": "application/yaml", ".htm": "text/html", ".markdown": "text/markdown", ".diff": "text/x-diff",
    ".patch": "text/x-diff",
}
_ALIASES = {
    "word": "docx", "doc": "docx", "excel": "xlsx", "xls": "xlsx", "spreadsheet": "xlsx", "powerpoint": "pptx",
    "ppt": "pptx", "slides": "pptx", "markdown": "md", "text": "txt", "yml": "yaml", "htm": "html",
    "tgz": "tar.gz", "tar": "tar.gz", "gz": "tar.gz",
}
#: Formats this runtime cannot write, with the reason a person would accept.
_REFUSED_FORMATS = {
    "rar": "RAR is a proprietary format with no open writer; a ZIP opens everywhere RAR does.",
    "7z": "7z output is not built here; a ZIP or tar.gz opens on every system.",
}


class DeliverableRefused(Exception):
    """A typed refusal with a plain message the model can relay or act on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Built:
    filename: str
    fmt: str
    media_type: str
    data: bytes
    verified_by: str
    #: What the user should know about how the content was shaped (prose left out of a workbook).
    notes: tuple[str, ...] = ()


# --- names and formats ------------------------------------------------------------------------------


def safe_filename(name: Any, *, default_stem: str = "vool-file") -> str:
    """One path component, printable, no leading dot, bounded. Never a path."""
    raw = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/")
    raw = raw.rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in '<>:"|?*\x00').strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = default_stem
    if len(cleaned) > MAX_NAME_CHARS:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (stem[: MAX_NAME_CHARS - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else cleaned[:MAX_NAME_CHARS]
    return cleaned


def _extension_of(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".tar.gz"):
        return ".tar.gz"
    return os.path.splitext(lowered)[1]


def resolve_format(filename: str, fmt: Any = "") -> tuple[str, str, str]:
    """(format, filename with the right extension, media type) or a typed refusal."""
    requested = str(fmt or "").strip().lower().lstrip(".")
    requested = _ALIASES.get(requested, requested)
    extension = _extension_of(filename)
    if not requested:
        if extension in (".tar.gz", ".tgz"):
            requested = "tar.gz"
        elif extension.lstrip(".") in FORMATS:
            requested = extension.lstrip(".")
        elif extension in CODE_EXTENSIONS:
            return "code", filename, CODE_EXTENSIONS[extension]
        elif extension.lstrip(".") in _ALIASES:
            requested = _ALIASES[extension.lstrip(".")]
        else:
            requested = extension.lstrip(".")
    if requested in _REFUSED_FORMATS:
        raise DeliverableRefused("format_unsupported", _REFUSED_FORMATS[requested])
    if extension.lstrip(".") in _REFUSED_FORMATS:
        # "haiku.rar" built as text was delivered as autumn_haiku.rar.txt (measured live,
        # 2026-10-06): a name that claims a format this runtime refuses is the same refusal.
        raise DeliverableRefused("format_unsupported", _REFUSED_FORMATS[extension.lstrip(".")])
    if extension in CODE_EXTENSIONS and requested in ("code", "", extension.lstrip(".")):
        return "code", filename, CODE_EXTENSIONS[extension]
    if requested not in FORMATS:
        raise DeliverableRefused(
            "format_unsupported",
            f"'{requested or extension or 'no extension'}' is not a file type this runtime builds. "
            f"Supported: {', '.join(sorted(FORMATS))}, and source-code files by extension ({', '.join(sorted(CODE_EXTENSIONS)[:12])}, ...).",
        )
    canonical, media_type = FORMATS[requested]
    if not filename.lower().endswith(canonical) and not (requested == "yaml" and extension == ".yml") and not (
        requested == "tar.gz" and extension == ".tgz"
    ) and not (requested == "html" and extension == ".htm") and not (requested == "md" and extension == ".markdown"):
        stem = filename[: -len(extension)] if extension and extension.lstrip(".") in (*FORMATS, *_ALIASES) else filename
        filename = (stem or "vool-file") + canonical
    return requested, filename, media_type


# --- markdown, read just enough ------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)")


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    cells = re.split(r"(?<!\\)\|", stripped)
    return [cell.strip().replace("\\|", "|") for cell in cells]


def markdown_blocks(text: str) -> list[dict[str, Any]]:
    """Headings, paragraphs, list items, tables and code blocks, in order. Nothing is dropped:
    a line this reader does not recognise is kept as a paragraph."""
    blocks: list[dict[str, Any]] = []
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"type": "paragraph", "text": " ".join(part.strip() for part in paragraph)})
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            flush()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            blocks.append({"type": "code", "lines": code})
            index += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "text": heading.group(2)})
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and _TABLE_RULE_RE.match(lines[index + 1]):
            flush()
            rows = [_split_row(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_row(lines[index]))
                index += 1
            blocks.append({"type": "table", "rows": rows})
            continue
        bullet = _BULLET_RE.match(line)
        numbered = _NUMBERED_RE.match(line)
        if bullet or numbered:
            flush()
            if bullet:
                blocks.append({"type": "bullet", "level": len(bullet.group(1).expandtabs(2)) // 2, "text": bullet.group(2)})
            else:
                blocks.append({"type": "numbered", "level": len(numbered.group(1).expandtabs(2)) // 2, "number": numbered.group(2), "text": numbered.group(3)})
            index += 1
            continue
        if not line.strip() or re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            flush()
        else:
            paragraph.append(line)
        index += 1
    flush()
    return blocks


def _inline_runs(text: str) -> list[tuple[str, str]]:
    """(style, text) runs: '' plain, 'b' bold, 'i' italic, 'c' code."""
    runs: list[tuple[str, str]] = []
    for part in _INLINE_RE.split(str(text or "")):
        if not part:
            continue
        if (part.startswith("**") and part.endswith("**")) or (part.startswith("__") and part.endswith("__")):
            runs.append(("b", part[2:-2]))
        elif part.startswith("`") and part.endswith("`"):
            runs.append(("c", part[1:-1]))
        elif len(part) > 2 and ((part.startswith("*") and part.endswith("*")) or (part.startswith("_") and part.endswith("_"))):
            runs.append(("i", part[1:-1]))
        else:
            runs.append(("", part))
    # Links read as their text followed by the URL, so nothing the model wrote is lost.
    return [(style, re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", chunk)) for style, chunk in runs]


def _plain(text: str) -> str:
    return "".join(chunk for _, chunk in _inline_runs(text))


def _xml_text(text: str) -> str:
    """XML 1.0 forbids most control characters outright; they are dropped, never smuggled in."""
    cleaned = "".join(ch for ch in str(text) if ch in "\t\n\r" or ord(ch) >= 0x20)
    return _xml_escape(cleaned)


# --- DOCX ------------------------------------------------------------------------------------------

_W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)
_PACKAGE_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{main}"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    "</Relationships>"
)
_DOCX_DOCUMENT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    "</Relationships>"
)


def _docx_styles() -> str:
    def heading(level: int, size: int) -> str:
        return (
            f'<w:style w:type="paragraph" w:styleId="Heading{level}"><w:name w:val="heading {level}"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
            f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="{level - 1}"/></w:pPr>'
            f'<w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:styles {_W_NS}>"
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/>'
        '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        + heading(1, 36) + heading(2, 30) + heading(3, 26) + heading(4, 24) + heading(5, 22) + heading(6, 22)
        + '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="40"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/><w:sz w:val="20"/></w:rPr></w:style>'
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="999999"/><w:left w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="999999"/><w:right w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="999999"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        "</w:tblBorders></w:tblPr></w:style>"
        "</w:styles>"
    )


def _w_runs(text: str, *, code: bool = False) -> str:
    out = []
    for style, chunk in ([("c", text)] if code else _inline_runs(text)):
        props = {"b": "<w:b/>", "i": "<w:i/>", "c": '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/>'}.get(style, "")
        out.append(f'<w:r>{"<w:rPr>" + props + "</w:rPr>" if props else ""}<w:t xml:space="preserve">{_xml_text(chunk)}</w:t></w:r>')
    return "".join(out)


def _w_paragraph(text: str, *, style: str = "", indent: int = 0, code: bool = False) -> str:
    props = ""
    if style or indent:
        props = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + (f'<w:ind w:left="{indent}" w:hanging="240"/>' if indent else "") + "</w:pPr>"
    return f"<w:p>{props}{_w_runs(text, code=code)}</w:p>"


def _w_table(rows: list[list[str]]) -> str:
    width = max((len(row) for row in rows), default=1)
    grid = "".join('<w:gridCol w:w="2000"/>' for _ in range(width))
    body = []
    for row_index, row in enumerate(rows):
        cells = []
        for col in range(width):
            value = row[col] if col < len(row) else ""
            text = f"**{value}**" if row_index == 0 and value else value
            cells.append(f'<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>{_w_paragraph(text)}</w:tc>')
        body.append("<w:tr>" + "".join(cells) + "</w:tr>")
    # Borders are stated on the table itself as well as in its style: some viewers (Quick Look,
    # several web previewers) ignore table styles, and a borderless table reads as loose text.
    borders = "".join(f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="999999"/>' for edge in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return (
        f'<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{borders}</w:tblBorders></w:tblPr>'
        f'<w:tblGrid>{grid}</w:tblGrid>{"".join(body)}</w:tbl><w:p/>'
    )


def _core_properties(title: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_xml_text(title)}</dc:title><dc:creator>VOOL</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def build_docx(markdown: str, *, title: str) -> bytes:
    body: list[str] = []
    for block in markdown_blocks(markdown):
        kind = block["type"]
        if kind == "heading":
            body.append(_w_paragraph(block["text"], style=f"Heading{block['level']}"))
        elif kind == "paragraph":
            body.append(_w_paragraph(block["text"]))
        elif kind == "bullet":
            body.append(_w_paragraph("• " + block["text"], style="ListParagraph", indent=360 + 360 * block["level"]))
        elif kind == "numbered":
            body.append(_w_paragraph(f"{block['number']}. " + block["text"], style="ListParagraph", indent=360 + 360 * block["level"]))
        elif kind == "table":
            body.append(_w_table(block["rows"]))
        elif kind == "code":
            body.extend(_w_paragraph(line or " ", style="Code", code=True) for line in (block["lines"] or [""]))
    if not body:
        body.append("<w:p/>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document {_W_NS}><w:body>{"".join(body)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        package.writestr("_rels/.rels", _PACKAGE_RELS.format(main="word/document.xml"))
        package.writestr("word/document.xml", document)
        package.writestr("word/_rels/document.xml.rels", _DOCX_DOCUMENT_RELS)
        package.writestr("word/styles.xml", _docx_styles())
        package.writestr("docProps/core.xml", _core_properties(title))
    return buffer.getvalue()


# --- XLSX ------------------------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_SHEET_NAME_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


def _column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _sheet_name(raw: str, used: set[str]) -> str:
    name = _SHEET_NAME_FORBIDDEN.sub(" ", _plain(raw)).strip().strip("'")[:31] or f"Sheet{len(used) + 1}"
    candidate, suffix = name, 2
    while candidate.lower() in used:
        tail = f" ({suffix})"
        candidate = name[: 31 - len(tail)] + tail
        suffix += 1
    used.add(candidate.lower())
    return candidate


def spreadsheet_sheets(content: str) -> list[tuple[str, list[list[str]]]]:
    """Sheets from markdown tables (each under its nearest heading) or, failing that, CSV/TSV text."""
    blocks = markdown_blocks(content)
    sheets: list[tuple[str, list[list[str]]]] = []
    heading = ""
    for block in blocks:
        if block["type"] == "heading":
            heading = block["text"]
        elif block["type"] == "table":
            sheets.append((heading, [[_plain(cell) for cell in row] for row in block["rows"]]))
            heading = ""
    if sheets:
        return sheets
    text = str(content or "").strip("\n")
    if not text.strip():
        raise DeliverableRefused("empty_content", "There is nothing to put in the spreadsheet: the content is empty.")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if sample.count("\t") > sample.count(",") else csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if row]
    return [("", rows)]


def _xlsx_cell(ref: str, value: str) -> str:
    raw = str(value)
    stripped = raw.strip()
    if stripped.startswith("=") and len(stripped) > 1:
        return f'<c r="{ref}"><f>{_xml_text(stripped[1:])}</f></c>'
    compact = stripped.replace(",", "") if re.match(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$", stripped) else stripped
    if _NUMBER_RE.match(compact) and not (len(compact) > 1 and compact.lstrip("+-").startswith("0") and not compact.lstrip("+-").startswith("0.")) and len(compact) <= 15:
        return f'<c r="{ref}"><v>{compact}</v></c>'
    if stripped.lower() in ("true", "false"):
        return f'<c r="{ref}" t="b"><v>{1 if stripped.lower() == "true" else 0}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{_xml_text(raw)}</t></is></c>'


def build_xlsx(content: str, *, title: str) -> bytes:
    sheets = spreadsheet_sheets(content)
    if len(sheets) > MAX_SHEETS:
        raise DeliverableRefused("too_many_sheets", f"The workbook would have {len(sheets)} sheets; the limit is {MAX_SHEETS}.")
    used: set[str] = set()
    named: list[tuple[str, list[list[str]]]] = []
    for heading, rows in sheets:
        if len(rows) > MAX_SHEET_ROWS:
            raise DeliverableRefused("too_many_rows", f"A sheet would have {len(rows):,} rows; the limit is {MAX_SHEET_ROWS:,}.")
        if any(len(row) > MAX_SHEET_COLUMNS for row in rows):
            raise DeliverableRefused("too_many_columns", f"A row has more than {MAX_SHEET_COLUMNS} columns.")
        named.append((_sheet_name(heading or (title if len(sheets) == 1 else ""), used), rows))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(named) + 1)
        )
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            f"{overrides}</Types>",
        )
        package.writestr("_rels/.rels", _PACKAGE_RELS.format(main="xl/workbook.xml"))
        sheet_entries = "".join(f'<sheet name="{_xml_text(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(named, start=1))
        package.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheet_entries}</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>',
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(named) + 1)
        )
        rels += f'<Relationship Id="rId{len(named) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        package.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        package.writestr(
            "xl/styles.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        )
        for i, (_, rows) in enumerate(named, start=1):
            xml_rows = []
            for r_index, row in enumerate(rows, start=1):
                cells = "".join(_xlsx_cell(f"{_column_letter(c)}{r_index}", value) for c, value in enumerate(row))
                if r_index == 1 and len(rows) > 1:
                    cells = cells.replace("<c r=", '<c s="1" r=')
                xml_rows.append(f'<row r="{r_index}">{cells}</row>')
            freeze = (
                '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                if len(rows) > 1 else ""
            )
            package.writestr(
                f"xl/worksheets/sheet{i}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{freeze}<sheetData>{"".join(xml_rows)}</sheetData></worksheet>',
            )
        package.writestr("docProps/core.xml", _core_properties(title))
    return buffer.getvalue()


# --- PPTX ------------------------------------------------------------------------------------------

_P_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_REL_NS = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'
_REL_T = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _pptx_theme() -> str:
    scheme = "".join(
        f'<a:{slot}><a:srgbClr val="{color}"/></a:{slot}>'
        for slot, color in (
            ("dk1", "000000"), ("lt1", "FFFFFF"), ("dk2", "1F2937"), ("lt2", "F3F4F6"), ("accent1", "2563EB"), ("accent2", "059669"),
            ("accent3", "D97706"), ("accent4", "DC2626"), ("accent5", "7C3AED"), ("accent6", "0891B2"), ("hlink", "2563EB"), ("folHlink", "7C3AED"),
        )
    )
    fill = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    line = '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="VOOL">'
        f'<a:themeElements><a:clrScheme name="VOOL">{scheme}</a:clrScheme>'
        '<a:fontScheme name="VOOL"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
        f'<a:fmtScheme name="VOOL"><a:fillStyleLst>{fill}{fill}{fill}</a:fillStyleLst><a:lnStyleLst>{line}{line}{line}</a:lnStyleLst>'
        "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
        f"<a:bgFillStyleLst>{fill}{fill}{fill}</a:bgFillStyleLst></a:fmtScheme></a:themeElements>"
        "<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>"
    )


def _sp_tree(shapes: str) -> str:
    return (
        '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{shapes}</p:spTree></p:cSld>"
    )


def _a_runs(text: str, *, size: int, bold: bool = False) -> str:
    out = []
    for style, chunk in _inline_runs(text):
        attrs = f' lang="en-US" sz="{size}"' + (' b="1"' if bold or style == "b" else "") + (' i="1"' if style == "i" else "")
        font = '<a:latin typeface="Courier New"/>' if style == "c" else ""
        out.append(f"<a:r><a:rPr{attrs} dirty=\"0\">{font}</a:rPr><a:t>{_xml_text(chunk)}</a:t></a:r>")
    return "".join(out) or f'<a:endParaRPr lang="en-US" sz="{size}"/>'


def _text_box(shape_id: int, name: str, x: int, y: int, cx: int, cy: int, paragraphs: list[str]) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr><a:lstStyle/>{"".join(paragraphs)}</p:txBody></p:sp>'
    )


def presentation_slides(markdown: str) -> list[dict[str, Any]]:
    """Slides from markdown: each heading at the splitting level starts a slide.

    The splitting level is the top heading level -- unless the deck has exactly ONE top-level
    heading followed by sub-headings ("# Deck title / ## Point one / ## Point two"), in which case
    that heading is the title slide and the sub-headings split the rest. Text before any heading,
    or a deck with no headings at all, is a slide of its own; nothing is dropped.
    """
    blocks = markdown_blocks(markdown)
    levels = [b["level"] for b in blocks if b["type"] == "heading"]
    split = min(levels, default=1)
    title_slide = levels.count(split) == 1 and (split + 1) in levels
    if title_slide:
        split += 1
    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for block in blocks:
        if block["type"] == "heading" and (block["level"] == split or (title_slide and block["level"] == split - 1)):
            current = {"title": _plain(block["text"]), "body": []}
            slides.append(current)
            continue
        if current is None:
            current = {"title": "", "body": []}
            slides.append(current)
        current["body"].append(block)
    return [slide for slide in slides if slide["title"] or slide["body"]]


def _slide_paragraphs(body: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for block in body:
        kind = block["type"]
        if kind in ("bullet", "numbered"):
            marker = "• " if kind == "bullet" else f"{block['number']}. "
            indent = 342900 * (1 + block["level"])
            out.append(f'<a:p><a:pPr marL="{indent}" indent="-228600"/>{_a_runs(marker + block["text"], size=1800)}</a:p>')
        elif kind == "heading":
            out.append(f"<a:p>{_a_runs(block['text'], size=2000, bold=True)}</a:p>")
        elif kind == "table":
            for row_index, row in enumerate(block["rows"]):
                out.append(f"<a:p>{_a_runs('  |  '.join(row), size=1400, bold=row_index == 0)}</a:p>")
        elif kind == "code":
            for line in block["lines"]:
                out.append(f"<a:p>{_a_runs('`' + line + '`' if line.strip() else ' ', size=1200)}</a:p>")
        else:
            out.append(f"<a:p>{_a_runs(block['text'], size=1800)}</a:p>")
    return out or ['<a:p><a:endParaRPr lang="en-US" sz="1800"/></a:p>']


def build_pptx(markdown: str, *, title: str) -> bytes:
    slides = presentation_slides(markdown)
    if not slides:
        raise DeliverableRefused("empty_content", "There is nothing to put on slides: the content is empty.")
    if len(slides) > MAX_SLIDES:
        raise DeliverableRefused("too_many_slides", f"The deck would have {len(slides)} slides; the limit is {MAX_SLIDES}.")
    width, height = 12192000, 6858000
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        slide_overrides = "".join(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for i in range(1, len(slides) + 1)
        )
        package.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            f"{slide_overrides}</Types>",
        )
        package.writestr("_rels/.rels", _PACKAGE_RELS.format(main="ppt/presentation.xml"))
        slide_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, len(slides) + 1))
        package.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation {_P_NS} saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
            f'<p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="{width}" cy="{height}"/><p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>",
        )
        rels = f'<Relationship Id="rId1" Type="{_REL_T}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        rels += "".join(f'<Relationship Id="rId{i + 1}" Type="{_REL_T}/slide" Target="slides/slide{i}.xml"/>' for i in range(1, len(slides) + 1))
        rels += f'<Relationship Id="rId{len(slides) + 2}" Type="{_REL_T}/theme" Target="theme/theme1.xml"/>'
        package.writestr("ppt/_rels/presentation.xml.rels", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {_REL_NS}>{rels}</Relationships>')
        package.writestr(
            "ppt/slideMasters/slideMaster1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<p:sldMaster {_P_NS}>"
            '<p:cSld><p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
            '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            "<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz=\"3600\"/></a:lvl1pPr></p:titleStyle>"
            "<p:bodyStyle><a:lvl1pPr><a:defRPr sz=\"1800\"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz=\"1800\"/></a:lvl1pPr></p:otherStyle></p:txStyles>"
            "</p:sldMaster>",
        )
        package.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {_REL_NS}>'
            f'<Relationship Id="rId1" Type="{_REL_T}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
            f'<Relationship Id="rId2" Type="{_REL_T}/theme" Target="../theme/theme1.xml"/></Relationships>',
        )
        # Built outside the f-string: a backslash inside an f-string expression is a SyntaxError
        # before Python 3.12, and this package supports 3.10+ (pyproject requires-python).
        blank_layout_tree = _sp_tree("").replace("<p:cSld>", '<p:cSld name="Blank">')
        package.writestr(
            "ppt/slideLayouts/slideLayout1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sldLayout {_P_NS} type="blank" preserve="1">{blank_layout_tree}'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>',
        )
        package.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {_REL_NS}>'
            f'<Relationship Id="rId1" Type="{_REL_T}/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        )
        package.writestr("ppt/theme/theme1.xml", _pptx_theme())
        margin = 457200
        for i, slide in enumerate(slides, start=1):
            shapes = ""
            if slide["title"]:
                shapes += _text_box(2, "Title", margin, 304800, width - 2 * margin, 1143000, [f"<a:p>{_a_runs(slide['title'], size=3200, bold=True)}</a:p>"])
            top = 1600200 if slide["title"] else 457200
            shapes += _text_box(3, "Body", margin, top, width - 2 * margin, height - top - 457200, _slide_paragraphs(slide["body"]))
            package.writestr(
                f"ppt/slides/slide{i}.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld {_P_NS}>{_sp_tree(shapes)}<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>',
            )
            package.writestr(
                f"ppt/slides/_rels/slide{i}.xml.rels",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships {_REL_NS}>'
                f'<Relationship Id="rId1" Type="{_REL_T}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>',
            )
        package.writestr("docProps/core.xml", _core_properties(title))
    return buffer.getvalue()


# --- text formats -----------------------------------------------------------------------------------


def _validated_text(fmt: str, content: str) -> str:
    text = str(content or "")
    if fmt == "json":
        try:
            json.loads(text)
        except ValueError as exc:
            raise DeliverableRefused("invalid_json", f"The JSON does not parse ({exc}); fix it and try again.") from exc
    elif fmt == "yaml":
        import yaml

        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise DeliverableRefused("invalid_yaml", f"The YAML does not parse ({str(exc).splitlines()[0]}); fix it and try again.") from exc
    elif fmt == "xml":
        if re.search(r"<!(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
            raise DeliverableRefused("xml_doctype", "XML with a DOCTYPE or ENTITY declaration is not built here; remove the declaration.")
        from xml.etree import ElementTree

        try:
            ElementTree.fromstring(text.encode("utf-8"))
        except ElementTree.ParseError as exc:
            raise DeliverableRefused("invalid_xml", f"The XML does not parse ({exc}); fix it and try again.") from exc
    elif fmt in ("csv", "tsv"):
        delimiter = "\t" if fmt == "tsv" else ","
        sheets = spreadsheet_sheets(text) if "|" in text and re.search(r"\n\s*\|?\s*:?-{2,}", text) else None
        if sheets:  # a markdown table asked for as CSV becomes real CSV, not pipes
            out = io.StringIO()
            writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
            for row in sheets[0][1]:
                writer.writerow(row)
            text = out.getvalue()
    elif fmt == "html" and "<html" not in text.lower():
        text = f'<!doctype html>\n<html><head><meta charset="utf-8"><title>VOOL</title></head><body>\n{text}\n</body></html>\n'
    if text and not text.endswith("\n"):
        text += "\n"
    return text


# --- build + verify ---------------------------------------------------------------------------------


def _probe_terms(content: str) -> list[str]:
    """A few distinctive words the built file must still contain when read back."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-]{3,}", _plain(str(content or "")))
    seen: list[str] = []
    for word in sorted(set(words), key=lambda w: (-len(w), w)):
        if word.lower() not in {s.lower() for s in seen}:
            seen.append(word)
        if len(seen) >= 3:
            break
    return seen


def _verify_source(fmt: str, content: str) -> str:
    """The part of the content the built file is supposed to carry: a workbook carries its cells."""
    if fmt == "xlsx":
        return "\n".join(" ".join(row) for _, rows in spreadsheet_sheets(content) for row in rows)
    return content


def _verify(fmt: str, filename: str, data: bytes, content: str) -> str:
    """Read the built bytes back with this runtime's own readers. Unreadable = not delivered."""
    if fmt in ("md", "txt", "csv", "tsv", "json", "yaml", "xml", "html", "code"):
        data.decode("utf-8")
        return "utf-8 text"
    from core import artifact_readers
    from core.artifact_readers import _sandbox

    if fmt == "pdf" and not _sandbox.confinement_available():
        _require_own_content(fmt, _read_own_pdf(data), content)
        return _OWN_PDF_READER
    extension = ".gz" if fmt == "tar.gz" else os.path.splitext(filename)[1].lower()
    try:
        result = artifact_readers.read(data, name=filename, extension=extension, question=" ".join(_probe_terms(content)))
    except artifact_readers.ReaderError as exc:
        raise DeliverableRefused("verification_failed", f"The built {fmt} could not be read back ({exc.message}); it was not delivered.") from exc
    if fmt in ("zip", "tar.gz"):
        return result.extractor
    _require_own_content(fmt, result.rendered_text(), content)
    return result.extractor


_OWN_PDF_READER = "pypdf (this runtime's own render, read in-process)"


def _read_own_pdf(data: bytes) -> str:
    """Text of a PDF `build_file` rendered a moment ago, on a host that cannot confine a decoder.

    The confined readers exist for documents that arrive from outside. These bytes are this
    runtime's own reportlab render of the turn's text, so they are read back in-process; a PDF from
    anywhere else on the same host still goes through `artifact_readers` and is refused unconfined."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DeliverableRefused("verification_failed", "The built pdf could not be read back (no PDF reader in this runtime); it was not delivered.") from exc
    try:
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:
        raise DeliverableRefused("verification_failed", f"The built pdf could not be read back ({type(exc).__name__}); it was not delivered.") from exc


def _require_own_content(fmt: str, rendered: str, content: str) -> None:
    missing = [term for term in _probe_terms(content) if term.lower() not in rendered.lower()]
    if missing:
        raise DeliverableRefused(
            "verification_failed",
            f"The built {fmt} did not contain its own content when read back (missing: {', '.join(missing)}); it was not delivered.",
        )


def _bundle_members(files: list[Any]) -> list[tuple[str, bytes]]:
    if not files:
        raise DeliverableRefused("empty_bundle", "A zip or tar.gz needs at least one file in `files`.")
    if len(files) > MAX_BUNDLE_FILES:
        raise DeliverableRefused("too_many_files", f"A bundle may hold {MAX_BUNDLE_FILES} files; {len(files)} were given.")
    members: list[tuple[str, bytes]] = []
    used: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise DeliverableRefused("invalid_files", "Each entry in `files` must be an object with `filename` and `content`.")
        raw_path = str(entry.get("filename") or entry.get("path") or entry.get("name") or "").replace("\\", "/")
        parts = [part for part in raw_path.split("/") if part not in ("", ".")]
        if not parts or ".." in parts or raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
            raise DeliverableRefused("unsafe_member_name", f"Bundle member name '{raw_path}' must be a relative path inside the bundle.")
        member_dir = "/".join(safe_filename(part) for part in parts[:-1])
        member_name = safe_filename(parts[-1])
        member_extension = _extension_of(member_name)
        _, resolved_name, _ = resolve_format(member_name, entry.get("format"))
        known_extension = member_extension in CODE_EXTENSIONS or member_extension.lstrip(".") in (*FORMATS, *_ALIASES)
        if known_extension and resolved_name != member_name:
            raise DeliverableRefused(
                "member_format_mismatch",
                f"Archive member {member_name} conflicts with its format metadata. "
                "Keep its requested output extension and omit format so the filename determines its bytes."
            )
        built = build_file(str(entry.get("content") or ""), filename=member_name, fmt=str(entry.get("format") or ""), title=parts[-1], verify=True)
        path = (member_dir + "/" if member_dir else "") + built.filename
        stem, suffix, counter = path, "", 2
        if "." in built.filename:
            stem, suffix = path.rsplit(".", 1)[0], "." + path.rsplit(".", 1)[1]
        while path.lower() in used:
            path = f"{stem} ({counter}){suffix}"
            counter += 1
        used.add(path.lower())
        members.append((path, built.data))
    return members


def build_file(content: str, *, filename: str, fmt: str = "", title: str = "", files: list[Any] | None = None, verify: bool = True) -> Built:
    """Bytes for one deliverable, verified. Raises `DeliverableRefused` with a plain reason."""
    name = safe_filename(filename)
    resolved, name, media_type = resolve_format(name, fmt)
    text = str(content or "")
    total_chars = len(text) + sum(len(str((f or {}).get("content") or "")) for f in (files or []) if isinstance(f, dict))
    if total_chars > MAX_SOURCE_CHARS:
        raise DeliverableRefused("too_large", f"The content is {total_chars:,} characters; one file may be built from at most {MAX_SOURCE_CHARS:,}.")
    title = title or name.rsplit(".", 1)[0]
    if resolved not in ("zip", "tar.gz") and not text.strip():
        raise DeliverableRefused("empty_content", f"There is nothing to put in {name}: the content is empty.")
    if resolved == "docx":
        data = build_docx(text, title=title)
    elif resolved == "xlsx":
        data = build_xlsx(text, title=title)
    elif resolved == "pptx":
        data = build_pptx(text, title=title)
    elif resolved == "pdf":
        from core.presentation.render_pdf import PdfRefused, render_markdown_pdf

        try:
            data = render_markdown_pdf(text, title=title)
        except PdfRefused as exc:
            raise DeliverableRefused("pdf_refused", str(exc)) from exc
    elif resolved == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path, payload in _bundle_members(list(files or [])):
                bundle.writestr(zipfile.ZipInfo(path, date_time=time.localtime()[:6]), payload, compress_type=zipfile.ZIP_DEFLATED)
        data = buffer.getvalue()
    elif resolved == "tar.gz":
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for path, payload in _bundle_members(list(files or [])):
                info = tarfile.TarInfo(path)
                info.size = len(payload)
                info.mtime = int(time.time())
                info.mode = 0o644
                bundle.addfile(info, io.BytesIO(payload))
        data = buffer.getvalue()
    else:
        data = _validated_text(resolved, text).encode("utf-8")
    if len(data) > MAX_OUTPUT_BYTES:
        raise DeliverableRefused("too_large", f"{name} would be {len(data) / (1024 * 1024):.1f} MB; the limit is {MAX_OUTPUT_BYTES // (1024 * 1024)} MB.")
    verified_by = _verify(resolved, name, data, _verify_source(resolved, text)) if verify else ""
    notes: list[str] = []
    if resolved == "xlsx" and any(b["type"] == "table" for b in markdown_blocks(text)):
        prose = [b for b in markdown_blocks(text) if b["type"] in ("paragraph", "bullet", "numbered", "code")]
        if prose:
            notes.append("Only the tables were placed in the workbook (one sheet per table); the surrounding text was left out.")
    return Built(filename=name, fmt=resolved, media_type=media_type, data=data, verified_by=verified_by, notes=tuple(notes))


# --- storage ----------------------------------------------------------------------------------------


def store_dir() -> Path:
    return active_data_dir() / "deliverables"


def _store_path(deliverable_id: str, suffix: str) -> Path:
    """A file of one deliverable, which by construction stays inside the deliverable store."""
    if not _ID_RE.fullmatch(deliverable_id):
        raise ValueError("not a deliverable id")
    root = os.path.abspath(store_dir())
    candidate = os.path.normpath(os.path.join(root, deliverable_id + suffix))
    if not candidate.startswith(root + os.sep) or os.path.dirname(candidate) != root:
        raise ValueError("outside the deliverable store")
    return Path(candidate)


def _manifest_path(deliverable_id: str) -> Path:
    return _store_path(deliverable_id, ".json")


def _bytes_path(deliverable_id: str) -> Path:
    return _store_path(deliverable_id, ".bin")


def _write_private(path: Path, data: bytes) -> None:
    pending = path.with_name(path.name + f".{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        pending.unlink(missing_ok=True)
        raise
    os.replace(pending, path)


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not (info.st_mode & 0o170000 == 0o100000):
            raise OSError("not a regular file")
        return handle.read()


def sweep_expired(*, now: float | None = None) -> int:
    root = store_dir()
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - RETENTION_SECONDS
    removed = 0
    for manifest in root.glob("dlv_*.json"):
        try:
            record = json.loads(_read_regular(manifest).decode("utf-8"))
            created = float(record.get("created_epoch") or 0)
        except (OSError, ValueError):
            created = 0
        if created < cutoff:
            deliverable_id = manifest.stem
            if not _ID_RE.fullmatch(deliverable_id):
                continue  # Not a file this store wrote; leave it alone.
            _bytes_path(deliverable_id).unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            removed += 1
    return removed


def _canonical_session(session_id: Any) -> str:
    """The chat's one id, in the shape every chat door agrees on (see core.chat_session_identity)."""
    raw = str(session_id or "").strip()
    if not raw:
        return ""
    from core.chat_session_identity import canonical_chat_session_id

    return str(canonical_chat_session_id(raw) or "")


def download_url(record: dict[str, Any]) -> str:
    from urllib.parse import quote

    return f"/api/chat/deliverable?id={record['id']}&session={quote(str(record['session_id']), safe='')}"


def create_deliverable(
    *, session_id: str, filename: str, content: str = "", fmt: str = "", title: str = "", files: list[Any] | None = None
) -> dict[str, Any]:
    """Build, verify and store one file for this chat. Returns the public record."""
    session = _canonical_session(session_id)
    if not session:
        raise DeliverableRefused("no_session", "A file can only be delivered inside a chat.")
    built = build_file(content, filename=filename, fmt=fmt, title=title, files=files)
    deliverable_id = "dlv_" + secrets.token_hex(16)
    record = {
        "id": deliverable_id,
        "session_id": session,
        "filename": built.filename,
        "format": built.fmt,
        "media_type": built.media_type,
        "size_bytes": len(built.data),
        "sha256": hashlib.sha256(built.data).hexdigest(),
        "verified_by": built.verified_by,
        "notes": list(built.notes),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_epoch": time.time(),
    }
    with _LOCK:
        root = store_dir()
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        sweep_expired()
        _write_private(_bytes_path(deliverable_id), built.data)
        _write_private(_manifest_path(deliverable_id), json.dumps(record, sort_keys=True).encode("utf-8"))
    record["url"] = download_url(record)
    return record


def read_deliverable(*, deliverable_id: str, session_id: str) -> tuple[dict[str, Any], bytes] | None:
    """The record and bytes, only for the chat that created it; None for anything else."""
    if not _ID_RE.fullmatch(str(deliverable_id or "")):
        return None
    with _LOCK:
        try:
            record = json.loads(_read_regular(_manifest_path(deliverable_id)).decode("utf-8"))
            data = _read_regular(_bytes_path(deliverable_id))
        except (OSError, ValueError):
            return None
    if not isinstance(record, dict) or not str(session_id or "").strip() or str(record.get("session_id") or "") != _canonical_session(session_id):
        return None
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        return None
    if float(record.get("created_epoch") or 0) < time.time() - RETENTION_SECONDS:
        return None
    return record, data


#: Source-context key the tool loop stamps with `requested_formats(<this turn's text>)`.
REQUESTED_FORMATS_KEY = "delivery_formats_requested"
DOWNLOAD_ONLY_KEY = "delivery_without_workspace_target"


def downloadable_request_without_workspace_target(text: str) -> bool:
    """A downloadable artifact is not permission to write an incidental workspace file."""
    from core.execution.constants import explicit_path_in
    from core.tool_demand_signals import asks_for_a_file

    if not asks_for_a_file(text) or not requested_formats(text) or explicit_path_in(text):
        return False
    return re.search(
        r"\b(?:in|into|inside|within|under|to|on)\s+(?!each\b|every\b|any\b)(?:(?:my|the|this|our|current)\s+)?"
        r"(?:workspace|project|repo|repository|desktop|downloads?|documents?|"
        r"(?:[\w'-]+\s+){0,4}?(?:folder|directory))\b",
        text,
        re.I,
    ) is None

#: The formats a request NAMES for its file, as typed words -> format. Only phrasings that order a
#: file kind count: "a 500-word essay" names no Word document, "as a Word document" does.
_NAMED_FORMAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("docx", re.compile(r"\bword\s+(?:doc|docs|document|documents|file|files)\b|\bas\s+(?:a\s+)?word\b|\.?\bdocx?\b", re.I)),
    ("xlsx", re.compile(r"\bexcel\b|\.?\bxlsx?\b|\bspreadsheet\b|\bworkbook\b", re.I)),
    ("pptx", re.compile(r"\bpowerpoint\b|\.?\bpptx?\b|\bslide\s*deck\b|\bslides\b|\bpresentation\b", re.I)),
    ("pdf", re.compile(r"\.?\bpdfs?\b", re.I)),
    ("csv", re.compile(r"\.?\bcsv\b", re.I)),
    ("tsv", re.compile(r"\.?\btsv\b", re.I)),
    ("json", re.compile(r"\.?\bjson\b", re.I)),
    ("yaml", re.compile(r"\.?\bya?ml\b", re.I)),
    ("xml", re.compile(r"\.?\bxml\b", re.I)),
    ("html", re.compile(r"\.?\bhtml?\b", re.I)),
    ("md", re.compile(r"\bmarkdown\b|\.md\b|\bmd\s+file\b", re.I)),
    ("txt", re.compile(r"\.txt\b|\btext\s+file\b|\btxt\b", re.I)),
    ("zip", re.compile(r"\bzip(?:ped|ping)?\b|\.zip\b|\bcompress(?:ed)?\b", re.I)),
    ("tar.gz", re.compile(r"\btar(?:ball|\.gz)?\b|\.tgz\b", re.I)),
    # Named but never built (`_REFUSED_FORMATS`): counted so the order is refused, not rebuilt as text.
    ("rar", re.compile(r"\.?\brar\b", re.I)),
    ("7z", re.compile(r"\.?\b7z\b", re.I)),
)
#: Words naming a file that is already there ("the attached pdf"), not the one being ordered.
_PRESENT_FILE_RE = re.compile(
    r"\b(?:the|this|that|these|those|my|your|our|attached|uploaded)\s+(?:(?!to\b|as\b|in\b|and\b)\w+\s+){0,2}?"
    r"(?:pdf|docx?|word\s+(?:doc|document|file)|excel|xlsx?|spreadsheet|workbook|csv|tsv|json|ya?ml|xml|html?|zip|archive|"
    r"powerpoint|pptx?|deck|presentation|file|document)\b",
    re.I,
)


def requested_formats(text: str) -> tuple[str, ...]:
    """The formats this turn ORDERS its file in, or () when it orders no file or names no format.

    A model asked for "a short one-page PDF" delivered Benefits_of_Regular_Walking.md, and one asked
    to "zip them up" delivered two loose files (measured live, pinned gpt-4.1-mini, 2026-10-06). The
    tool description already asked for the named format; a description is advice. This is the
    request's own word, read once, so `file.deliver` can refuse a format nobody asked for and the
    model's correction round can deliver the one they did. Formats of files already present ("the
    attached pdf") are not ordered and are not counted.
    """
    raw = str(text or "")
    try:
        from core.tool_demand_signals import asks_for_a_file

        if not asks_for_a_file(raw):
            return ()
    except Exception:
        return ()
    ordered = _PRESENT_FILE_RE.sub(" ", raw)
    formats = tuple(fmt for fmt, pattern in _NAMED_FORMAT_PATTERNS if pattern.search(ordered))
    # In a bundle order the member formats describe contents, not alternative outputs.
    # Otherwise a named HTML member can satisfy the guard and end the turn before the ZIP exists.
    bundle_order = re.search(
        r"\b(?:zip|compress)\s+(?:up\s+)?(?:them|it|these|those|both|all|the\s+(?:files|csv|pdf|docx|xlsx))\b",
        raw, re.I,
    )
    outer_archive = re.search(
        r"\b(?:as|in|inside|into)\s+(?:(?:a|an|one|single|the)\s+)?\.?(zip|tar\.gz|tarball)\b",
        raw, re.I,
    )
    archive_order = bundle_order or outer_archive
    if archive_order:
        archive_format = "zip" if bundle_order else (
            "zip" if outer_archive.group(1).lower() == "zip" else "tar.gz"
        )
        # A following output clause belongs to the user independently of the bundle.
        # Keep preceding explicit output clauses too, but discard bundled member formats.
        separate = re.compile(
            r"(?:[,;.]\s*|\band\s+)(?:also\s+|then\s+)?"
            r"(?=(?:give\s+me|send\s+(?:me\s+)?|make|create|generate|export|produce|put|a\s+separate|separately)\b)",
            re.I,
        )
        parts = separate.split(ordered)
        outputs = {archive_format}
        for part in parts:
            if re.search(r"\b(?:zip|compress|tar(?:\.gz|ball)?)\b", part, re.I):
                # 'CSV in a zip and a separate PDF' has already been split above.
                continue
            if len(parts) > 1:
                outputs.update(fmt for fmt, pattern in _NAMED_FORMAT_PATTERNS if pattern.search(part))
        return tuple(fmt for fmt in formats if fmt in outputs)
    return formats


def refuse_unrequested_format(*, filename: str, fmt: str, wanted: tuple[str, ...] | list[str]) -> None:
    """Raise `format_not_requested` when the turn named its format(s) and this call builds another."""
    ordered = tuple(str(item) for item in (wanted or ()))
    named = tuple(item for item in ordered if item in FORMATS)
    if not named:
        refused = [item for item in ordered if item in _REFUSED_FORMATS]
        if refused:
            raise DeliverableRefused("format_unsupported", _REFUSED_FORMATS[refused[0]])
        return
    try:
        resolved, _name, _media = resolve_format(safe_filename(filename) if filename else "", fmt)
    except DeliverableRefused:
        return  # the build refuses it with its own, more specific reason
    if resolved in named:
        return
    shown = ", ".join("." + FORMATS[item][0].lstrip(".") for item in named)
    hint = " Several files to zip: ONE call with filename *.zip and `files`." if any(
        item in ("zip", "tar.gz") for item in named
    ) else ""
    raise DeliverableRefused(
        "format_not_requested",
        f"the request asked for {shown}, and this call builds .{resolved if resolved != 'code' else (filename.rsplit('.', 1)[-1] if '.' in filename else 'file')}. "
        f"Deliver it as {shown}.{hint}",
    )


#: Writers that put a file on disk instead of handing it to the user. When the turn ORDERED a file in a
#: named format, one of these writing another format is the wrong tool for the order.
DISK_WRITER_INTENTS = frozenset({"machine.write_file", "workspace.write_file"})


def ordered_file_writer_refusal(intent: str, raw_path: Any, wanted: tuple[str, ...] | list[str]) -> str:
    """The refusal text for a disk writer used on a turn that ordered a different file format, or "".

    Measured live 2026-10-06: "Write a shell script ... plus a short usage note, and zip both files." -- the
    model wrote backup_documents.sh with machine.write_file (refused by the machine lanes), never built
    the zip, and the turn ended "I wasn't able to turn that into a completed action". The order was a
    downloadable .zip; a writer producing a .sh on disk answers a different request. Writing a file
    in the ordered format ("save it as notes.md in the project") is not refused here.
    """
    if intent not in DISK_WRITER_INTENTS:
        return ""
    named = tuple(str(item) for item in (wanted or ()) if str(item) in FORMATS)
    if not named:
        return ""
    name = str(raw_path or "").strip().rsplit("/", 1)[-1]
    extension = _extension_of(name).lstrip(".")
    if extension == "tgz":
        extension = "tar.gz"
    extension = _ALIASES.get(extension, extension)
    if extension in named:
        return ""
    shown = ", ".join("." + FORMATS[item][0].lstrip(".") for item in named)
    bundle = " Several files to zip: ONE file.deliver call with filename *.zip and `files`." if any(
        item in ("zip", "tar.gz") for item in named
    ) else ""
    return (
        f"{intent} refused: this request orders a {shown} file to download, so it is delivered with file.deliver "
        f"(filename ending {shown}), not written to disk.{bundle} Nothing was written."
    )


def supported_formats_summary() -> str:
    return "docx, xlsx, pptx, pdf, md, txt, csv, tsv, json, yaml, xml, html, zip, tar.gz, and source files such as .py or .js"


__all__ = [
    "CODE_EXTENSIONS",
    "DISK_WRITER_INTENTS",
    "FORMATS",
    "MAX_BUNDLE_FILES",
    "MAX_OUTPUT_BYTES",
    "MAX_SOURCE_CHARS",
    "REQUESTED_FORMATS_KEY",
    "RETENTION_SECONDS",
    "Built",
    "DeliverableRefused",
    "build_file",
    "create_deliverable",
    "download_url",
    "markdown_blocks",
    "ordered_file_writer_refusal",
    "read_deliverable",
    "refuse_unrequested_format",
    "requested_formats",
    "resolve_format",
    "safe_filename",
    "supported_formats_summary",
    "sweep_expired",
]
