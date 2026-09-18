"""Read a PDF: its embedded text, and its scanned pages.

A PDF was the one document type the runtime could not open. Every workspace and machine read tool
returns bytes or lines, so a PDF reached the model as binary noise and the turn either refused or
invented a summary — measured on the owner's own workspace, where "the website displays most of its
data in PDF format so the agent needs to read it" was the request that started this.

No dependency is installed to fix that, and none needs to be. macOS ships PDFKit and Vision, and the
system interpreter at ``/usr/bin/python3`` ships the PyObjC bindings for both. This module runs a
small self-contained helper under THAT interpreter and reads JSON back, so the project's own
virtualenv gains nothing and no package is added on a machine that holds live keys.

Two different jobs, deliberately two tools:

``extract_text``  the text layer a PDF already carries. Exact, instant, and what almost every
                  generated or exported PDF has.
``ocr``           on-device text recognition for a SCANNED page, which has no text layer at all.
                  Slower and approximate, so it is never used when ``extract_text`` finds text.

Both degrade honestly. On a machine without the frameworks they return ``missing_dependency`` with
the reason, exactly like ``web.fetch`` returns ``disabled_by_policy`` — never an empty string that
reads as "this PDF is blank".
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# The system interpreter, not the venv one: this is where PyObjC lives on macOS.
_SYSTEM_PYTHON = "/usr/bin/python3"
_DEFAULT_TIMEOUT = 60.0
_MAX_TEXT_CHARS = 200_000
_MAX_PAGES = 200

_EXTRACT_HELPER = r'''
import json, sys
path, max_pages = sys.argv[1], int(sys.argv[2])
try:
    from Foundation import NSURL
    import Quartz
    try:
        from Quartz import PDFDocument
    except Exception:
        from Quartz.PDFKit import PDFDocument
except Exception as exc:
    print(json.dumps({"status": "missing_dependency", "reason": type(exc).__name__ + ": " + str(exc)}))
    raise SystemExit(0)
url = NSURL.fileURLWithPath_(path)
doc = PDFDocument.alloc().initWithURL_(url)
if doc is None:
    print(json.dumps({"status": "unreadable", "reason": "not a readable PDF"}))
    raise SystemExit(0)
total = int(doc.pageCount())
wanted = total if max_pages <= 0 else min(total, max_pages)
parts = []
for index in range(wanted):
    page = doc.pageAtIndex_(index)
    parts.append(str(page.string() or "") if page is not None else "")
print(json.dumps({
    "status": "ok",
    "pages_total": total,
    "pages_read": wanted,
    "page_text": parts,
}))
'''

_OCR_HELPER = r'''
import json, sys
path, max_pages, scale = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
try:
    import objc, Quartz
    from Foundation import NSURL
    try:
        from Quartz import PDFDocument
    except Exception:
        from Quartz.PDFKit import PDFDocument
    objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")
    VNRecognizeTextRequest = globals()["VNRecognizeTextRequest"]
    VNImageRequestHandler = globals()["VNImageRequestHandler"]
except Exception as exc:
    print(json.dumps({"status": "missing_dependency", "reason": type(exc).__name__ + ": " + str(exc)}))
    raise SystemExit(0)
doc = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
if doc is None:
    print(json.dumps({"status": "unreadable", "reason": "not a readable PDF"}))
    raise SystemExit(0)
total = int(doc.pageCount())
wanted = total if max_pages <= 0 else min(total, max_pages)
parts = []
for index in range(wanted):
    page = doc.pageAtIndex_(index)
    if page is None:
        parts.append("")
        continue
    box = page.boundsForBox_(Quartz.kPDFDisplayBoxMediaBox)
    width = max(1, int(box.size.width * scale))
    height = max(1, int(box.size.height * scale))
    space = Quartz.CGColorSpaceCreateDeviceRGB()
    ctx = Quartz.CGBitmapContextCreate(
        None, width, height, 8, 0, space, Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetRGBFillColor(ctx, 1.0, 1.0, 1.0, 1.0)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, width, height))
    Quartz.CGContextScaleCTM(ctx, scale, scale)
    page.drawWithBox_toContext_(Quartz.kPDFDisplayBoxMediaBox, ctx)
    image = Quartz.CGBitmapContextCreateImage(ctx)
    handler = VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1)  # accurate
    # PyObjC returns a bare bool here, not the (ok, error) tuple the ObjC signature suggests.
    handler.performRequests_error_([request], None)
    lines = []
    for observation in (request.results() or []):
        candidates = observation.topCandidates_(1)
        if candidates and len(candidates):
            lines.append(str(candidates[0].string()))
    parts.append("\n".join(lines))
print(json.dumps({
    "status": "ok",
    "pages_total": total,
    "pages_read": wanted,
    "page_text": parts,
}))
'''


def _run_helper(helper: str, args: list[str], *, timeout: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [_SYSTEM_PYTHON, "-c", helper, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {
            "status": "missing_dependency",
            "reason": f"{_SYSTEM_PYTHON} is not present; PDF reading needs the macOS system Python",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": f"PDF read exceeded {timeout:.0f}s"}
    if proc.returncode != 0:
        # The helper's stderr can name a path, so only its last line is carried, and only as a
        # reason string the operator sees alongside the file they asked about.
        tail = (proc.stderr or "").strip().splitlines()
        return {"status": "error", "reason": tail[-1] if tail else "PDF helper failed"}
    try:
        return dict(json.loads(proc.stdout or "{}"))
    except (TypeError, ValueError):
        return {"status": "error", "reason": "PDF helper returned no JSON"}


def _resolve(path: str) -> tuple[Path | None, dict[str, Any] | None]:
    candidate = Path(str(path or "").strip()).expanduser()
    if not str(candidate):
        return None, {"status": "invalid_arguments", "reason": "path is required"}
    if not candidate.is_file():
        return None, {"status": "not_found", "reason": f"no file at {candidate}"}
    if candidate.suffix.lower() != ".pdf":
        return None, {"status": "unreadable", "reason": f"{candidate.name} is not a .pdf"}
    return candidate, None


def _assemble(result: dict[str, Any], *, path: Path, source: str) -> dict[str, Any]:
    if str(result.get("status")) != "ok":
        return {**result, "path": str(path), "source": source, "text": "", "characters": 0}
    pages = [str(part or "") for part in list(result.get("page_text") or [])]
    text = "\n\n".join(f"[page {index + 1}]\n{body}".rstrip() for index, body in enumerate(pages))
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS]
    return {
        "status": "ok",
        "path": str(path),
        "source": source,
        "pages_total": int(result.get("pages_total") or 0),
        "pages_read": int(result.get("pages_read") or 0),
        "text": text,
        "characters": len(text),
        "truncated": truncated,
    }


def extract_text(path: str, *, max_pages: int = 0, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any]:
    """The text layer a PDF already carries. Empty text means a scanned page, not a blank one."""

    resolved, problem = _resolve(path)
    if problem is not None:
        return {**problem, "path": str(path), "source": "pdfkit", "text": "", "characters": 0}
    bounded = max(0, min(int(max_pages or 0), _MAX_PAGES))
    result = _run_helper(_EXTRACT_HELPER, [str(resolved), str(bounded)], timeout=timeout)
    assembled = _assemble(result, path=resolved, source="pdfkit")
    if assembled.get("status") == "ok" and not assembled["text"].replace("[page 1]", "").strip():
        # Saying "ok, 0 characters" reads as "this PDF is blank", which is the one thing it is not.
        assembled["status"] = "no_text_layer"
        assembled["reason"] = (
            "this PDF carries no text layer, so it is almost certainly scanned - run pdf.ocr on it"
        )
    return assembled


def ocr(path: str, *, max_pages: int = 0, scale: float = 2.0, timeout: float = 180.0) -> dict[str, Any]:
    """On-device text recognition for a scanned page. Slower and approximate; no data leaves the Mac."""

    resolved, problem = _resolve(path)
    if problem is not None:
        return {**problem, "path": str(path), "source": "vision", "text": "", "characters": 0}
    bounded = max(0, min(int(max_pages or 0), _MAX_PAGES))
    clamped_scale = max(1.0, min(float(scale or 2.0), 4.0))
    result = _run_helper(
        _OCR_HELPER, [str(resolved), str(bounded), str(clamped_scale)], timeout=timeout
    )
    return _assemble(result, path=resolved, source="vision")
