"""PDF: the text layer where there is one, OCR of the rendered page where there is not.

A PDF is two different documents wearing one extension. A born-digital export carries real
characters and needs no OCR; a scan carries only pixels and its text exists nowhere in the file.
Treating them the same is how "uploaded" comes to mean "read" without anything being read: a
text-layer extractor run over a scan returns an empty string, and an empty string is
indistinguishable from a blank page unless someone says so.

So this reader decides per page, on evidence:

1. Extract the text layer (PDFKit, or pypdf where PDFKit is absent).
2. A page whose text layer is empty *while the page carries ink* is a scanned page. It is
   rendered and OCR'd, and the resulting unit is stamped with the engine and its confidence.
3. A page that is genuinely blank is reported as blank -- not as OCR that found nothing.

Layout is preserved rather than reflowed, because a table that loses its columns loses its
meaning. PDFKit's ``page.string`` keeps the column runs of a text-layer table; the OCR path
rebuilds rows from the recognizer's own line boxes. Either way arithmetic over the result is
arithmetic over the document's real numbers.

Nothing in a PDF is executed and nothing is fetched: no JavaScript action, no embedded file, no
remote resource. The decoder runs in the reader sandbox, which has no network at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from . import _sandbox
from ._limits import MAX_PDF_PAGES, MAX_UNIT_CHARS, OCR_MAX_EDGE
from ._sandbox import Scratch, extraction_budget, require_confinement, run_confined
from ._types import UNIT_PAGE, ReaderRefused, ReaderResult, ReaderUnavailable, ReaderUnit
from .vision_tool import (
    TOOL_VERSION,
    ocr_available,
    ocr_text_with_confidence,
    pdf_text_layer,
    render_pdf_page,
    toolchain_available,
)

PDF_MAGIC = b"%PDF-"
#: Below this many characters a page's "text layer" is punctuation and page furniture, not prose.
_TEXT_LAYER_MIN_CHARS = 12
#: An OCR mean confidence at or under this is reported to the model as low-confidence text.
_LOW_CONFIDENCE_MEAN = 0.55


def sniff(data: bytes) -> bool:
    """A PDF header may sit behind up to 1KB of junk; that is legal and common."""
    return PDF_MAGIC in bytes(data[:1024])


def _pypdf_importable() -> bool:
    """Whether the portable fallback exists AT ALL -- checked in a subprocess, never here.

    Importing pypdf in this process would be harmless; *parsing with it* would not, which is why
    the fallback runs confined (below) rather than inline.
    """
    if not _sandbox.confinement_available():
        return False
    try:
        import importlib.util

        return importlib.util.find_spec("pypdf") is not None
    except (ImportError, ValueError):
        return False


def decoders_available() -> dict[str, bool]:
    return {"text_layer": bool(toolchain_available() or _pypdf_importable()), "ocr": ocr_available()}


#: The portable fallback, run as a CONFINED subprocess. A hostile PDF meets pypdf inside the same
#: sandbox every other decoder gets -- no network, reads scoped to the scratch directory, bounded
#: CPU and output, killed as a process group. Parsing untrusted PDF structure in THIS process
#: would have been the one unbounded hostile-parsing path left in the reader lane; a pure-Python
#: parser is not safer than a C one, it just fails differently (unbounded memory, quadratic loops).
_PYPDF_SCRIPT = r"""
import json, sys
for _entry in json.loads(sys.argv[3]):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
try:
    import pypdf
except Exception as exc:
    print(json.dumps({"error": "pypdf_unavailable", "detail": str(exc)[:200]})); raise SystemExit(0)
path, max_pages = sys.argv[1], int(sys.argv[2])
try:
    reader = pypdf.PdfReader(path)
    if getattr(reader, "is_encrypted", False):
        try:
            opened = reader.decrypt("")
        except Exception:
            opened = 0
        if not opened:
            print(json.dumps({"error": "password_protected"})); raise SystemExit(0)
    total = len(reader.pages)
    pages = []
    for index in range(min(total, max_pages)):
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception:
            text = ""
        pages.append({"index": index + 1, "text": text, "characters": len(text)})
except SystemExit:
    raise
except Exception as exc:
    print(json.dumps({"error": "pdf_unreadable", "detail": type(exc).__name__})); raise SystemExit(0)
print(json.dumps({"engine": "pypdf " + getattr(pypdf, "__version__", "?"), "page_count": total, "pages": pages}))
"""


def _fallback_read_scope() -> list[str]:
    """The narrowest scope that lets the fallback interpreter import pypdf and nothing else."""
    import sysconfig

    scope: list[str] = []
    # The interpreter's OWN installation tree. It is a runtime dependency in exactly the sense
    # /usr/lib is for a C binary -- without data access to it the interpreter aborts before it can
    # run anything (measured: SIGABRT). It is not "the user's files": nothing outside this tree,
    # and in particular not the repository, becomes readable.
    for entry in (sys.base_prefix, sysconfig.get_paths().get("stdlib"), sysconfig.get_paths().get("platstdlib")):
        if entry and os.path.isdir(entry):
            scope.append(str(Path(entry).resolve()))
    try:
        import importlib.util

        spec = importlib.util.find_spec("pypdf")
        origin = getattr(spec, "origin", None) if spec else None
        if origin:
            # The directory CONTAINING the package, so `import pypdf` resolves -- not site-packages
            # at large where every other installed distribution lives.
            package_root = Path(origin).resolve().parent
            scope.append(str(package_root))
            scope.append(str(package_root.parent))
    except (ImportError, ValueError, OSError):
        pass
    return [entry for index, entry in enumerate(scope) if entry and entry not in scope[:index]]


def _package_paths() -> list[str]:
    """The single directory that must be on the child's `sys.path` for `import pypdf` to work."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("pypdf")
        origin = getattr(spec, "origin", None) if spec else None
        return [str(Path(origin).resolve().parent.parent)] if origin else []
    except (ImportError, ValueError, OSError):
        return []


def _text_layer_pypdf(path: Path, *, max_pages: int, scratch: Path) -> dict[str, Any]:
    """Run the fallback parser confined, and translate its typed outcome back into ours."""
    if not _pypdf_importable():
        raise ReaderUnavailable(
            "No PDF decoder is available on this machine, so the PDF was not read.",
            code="pdf_decoder_unavailable",
            remediation="Install the Xcode Command Line Tools, or add the `pypdf` package to this runtime.",
            fmt="pdf",
        )
    script = scratch / "pypdf_reader.py"
    script.write_text(_PYPDF_SCRIPT, encoding="utf-8")
    interpreter = sys.executable
    if not interpreter or not os.path.isabs(interpreter):
        raise ReaderUnavailable(
            "No usable interpreter was found to run the PDF fallback decoder; the PDF was not read.",
            code="pdf_decoder_unavailable",
            fmt="pdf",
        )
    # READ scope for the fallback interpreter, deliberately NOT `sys.path`. sys.path contains the
    # repository root, so granting it would hand a hostile PDF read access to the entire working
    # tree -- exactly the "broad extra_read" a review flagged. Instead: the interpreter's own
    # standard library, and the single directory that actually holds pypdf. Nothing else.
    extra_read = _fallback_read_scope()
    # `-I` isolates the child from the environment, so PYTHONPATH cannot carry the package in;
    # the import path is passed as an argument and inserted explicitly instead. Isolation is the
    # point -- the child must not inherit whatever the server happened to be started with.
    result = run_confined(
        [interpreter, "-I", "-S", str(script), str(path), str(int(max_pages)), json.dumps(_package_paths())],
        scratch=scratch,
        fmt="pdf",
        extra_read=extra_read,
    )
    if result.returncode != 0:
        raise ReaderUnavailable(
            "The PDF fallback decoder could not run; the PDF was not read.",
            code="pdf_decoder_unavailable",
            fmt="pdf",
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ReaderRefused("The PDF could not be read by the fallback decoder.", code="pdf_unreadable", fmt="pdf") from exc
    error = str(payload.get("error") or "")
    if error == "password_protected":
        raise ReaderRefused(
            "The PDF is password-protected; its contents were not read.",
            code="password_protected",
            remediation="Remove the password, or attach an unprotected copy.",
            fmt="pdf",
        )
    if error:
        raise ReaderRefused(
            "The PDF could not be opened; it may be corrupt.",
            code="pdf_unreadable",
            remediation="Check that the file is a complete, undamaged PDF.",
            fmt="pdf",
        )
    return payload


def _ocr_lines_to_rows(text: str) -> str:
    """OCR output is already one line per recognized line; keep it, trimming only trailing space."""
    return "\n".join(line.rstrip() for line in text.splitlines())


def read(data: bytes, *, name: str = "document.pdf", ocr: bool = True, max_pages: int = MAX_PDF_PAGES) -> ReaderResult:
    """Extract one PDF into page-addressed units. Raises a typed error when nothing can read it."""
    if not sniff(data):
        raise ReaderUnavailable(f"{name} does not carry a PDF header; it was not read as a PDF.", code="not_a_pdf", fmt="pdf")
    require_confinement("pdf")
    digest = hashlib.sha256(data).hexdigest()
    warnings: list[str] = []
    omitted: list[str] = []
    with Scratch("vool-pdf-") as scratch, extraction_budget(scratch=scratch.path):
        source = scratch.path / "input.pdf"
        source.write_bytes(data)
        if toolchain_available():
            payload = pdf_text_layer(source, max_pages=max_pages, scratch=scratch.path)
        else:
            payload = _text_layer_pypdf(source, max_pages=max_pages, scratch=scratch.path)
        engine = str(payload.get("engine") or "unknown")
        page_count = int(payload.get("page_count") or 0)
        raw_pages = [page for page in payload.get("pages") or [] if isinstance(page, dict)]
        if page_count > len(raw_pages):
            omitted.append(f"pages {len(raw_pages) + 1}–{page_count} (over the {max_pages}-page reading limit)")
            warnings.append(f"This PDF has {page_count} pages; the first {len(raw_pages)} were read.")

        ocr_engine = ""
        ocr_pages = 0
        units: list[ReaderUnit] = []
        for page in raw_pages:
            number = int(page.get("index") or 0)
            text = str(page.get("text") or "")
            locator = f"page {number}"
            page_warnings: list[str] = []
            meta: dict[str, Any] = {"page": number, "source": "text_layer"}
            if len(text.strip()) < _TEXT_LAYER_MIN_CHARS:
                # No usable text layer. Either a scan, or a genuinely blank page -- and the reader
                # is not allowed to guess which. Rendering and OCR settle it with evidence.
                if not ocr:
                    page_warnings.append("no text layer; OCR was not requested for this read")
                    meta["source"] = "none"
                elif not ocr_available():
                    page_warnings.append(
                        "no text layer, and no OCR engine is available on this machine, so this page was NOT read"
                    )
                    meta["source"] = "blocked"
                    meta["blocked_reason"] = "ocr_unavailable"
                else:
                    rendered = scratch.path / f"page-{number}.png"
                    try:
                        render_pdf_page(source, page_number=number, out_png=rendered, max_edge=OCR_MAX_EDGE, scratch=scratch.path)
                        recognized, facts = ocr_text_with_confidence(rendered, scratch=scratch.path)
                    except Exception as exc:  # a decoder fault on ONE page must not lose the rest
                        page_warnings.append(f"no text layer, and this page could not be rendered for OCR ({type(exc).__name__})")
                        meta["source"] = "failed"
                    else:
                        ocr_engine = facts["engine"]
                        if recognized.strip():
                            ocr_pages += 1
                            text = _ocr_lines_to_rows(recognized)
                            meta.update({"source": "ocr", "ocr": facts})
                            confidence_note = f"OCR (mean confidence {facts['mean_confidence']:.2f})"
                            if facts["mean_confidence"] <= _LOW_CONFIDENCE_MEAN or facts["low_confidence_lines"]:
                                confidence_note += f"; {facts['low_confidence_lines']} of {facts['lines']} lines were low-confidence and may be misread"
                            page_warnings.append("no text layer — text below is " + confidence_note)
                        else:
                            meta["source"] = "blank"
                            page_warnings.append("no text layer and OCR found no text: this page appears blank")
                    finally:
                        rendered.unlink(missing_ok=True)
            if len(text) > MAX_UNIT_CHARS:
                text = text[:MAX_UNIT_CHARS]
                page_warnings.append(f"this page was cut at {MAX_UNIT_CHARS:,} characters")
            units.append(ReaderUnit(locator=locator, kind=UNIT_PAGE, text=text, warnings=tuple(page_warnings), meta=meta))

    if ocr_pages:
        warnings.append(f"{ocr_pages} of {len(units)} pages had no text layer and were read by OCR; OCR can misread characters.")
    extractor = engine + (f" + {ocr_engine} {TOOL_VERSION}" if ocr_pages else "")
    return ReaderResult(
        fmt="pdf",
        extractor=extractor,
        units=tuple(units),
        warnings=tuple(warnings),
        omitted=tuple(omitted),
        source_sha256=digest,
        meta={
            "page_count": page_count,
            "pages_read": len(units),
            "ocr_pages": ocr_pages,
            "text_layer_engine": engine,
            "ocr_engine": ocr_engine,
        },
    )


_WHITESPACE = re.compile(r"[ \t]+")


def page_numbers(result: ReaderResult) -> list[int]:
    return [int(unit.meta.get("page") or 0) for unit in result.units]
