"""The isolated, packaged converter this runtime uses for OCR and for PDF page rendering.

Apple ships two decoders every Mac already has: **PDFKit**, which parses PDF, and **Vision**,
whose ``VNRecognizeTextRequest`` is a production OCR engine. Neither has a Python binding here,
so this module carries a ~150-line Swift program, compiles it once with ``/usr/bin/swiftc``, caches
the binary by the hash of its own source, and runs it under the reader sandbox.

Why a compiled helper and not a Python OCR package:

* It is the OCR engine the operator's machine already has, at its real quality -- no model
  download, no ``brew install``, nothing added to a machine that holds live keys.
* It runs as a separate process, so a malformed PDF crashes a sandboxed child that has no network
  and no write access outside one scratch directory, not the runtime.
* Absent ``swiftc`` (or a non-Apple platform), every entry point raises ``ReaderUnavailable``.
  The formats that depend on it are then BLOCKED and say so; nothing degrades into a silent empty
  read, and nothing pretends a scan was OCR'd when no OCR engine ran.

The helper speaks JSON on stdout and never takes a network path -- only a scratch file the caller
just wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ._limits import OCR_MAX_EDGE, SUBPROCESS_TIMEOUT_S
from ._sandbox import Scratch, run_confined
from ._types import ReaderFailed, ReaderRefused, ReaderUnavailable

SWIFTC = "/usr/bin/swiftc"
TOOL_NAME = "vool_vision_tool"
#: Bumped whenever the Swift source changes meaning. Recorded on every derivative this produces,
#: so an answer can be traced to the exact extractor that produced its evidence.
TOOL_VERSION = "1.0.0"

_SWIFT_SOURCE = r"""
import AppKit
import CoreGraphics
import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(message.data(using: .utf8)!)
    exit(2)
}

func emit(_ object: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
        fail("could not encode result")
    }
    FileHandle.standardOutput.write(data)
}

/// Text layer of every page, addressed by 1-based page number. A page whose text layer is empty
/// is where the caller decides to rasterise and OCR instead; that decision is not made here.
func pdfText(_ path: String, _ maxPages: Int) {
    guard let document = PDFDocument(url: URL(fileURLWithPath: path)) else {
        fail("unreadable-pdf")
    }
    if document.isEncrypted && document.isLocked {
        fail("password-protected")
    }
    var pages: [[String: Any]] = []
    let count = min(document.pageCount, maxPages)
    for index in 0..<count {
        guard let page = document.page(at: index) else { continue }
        let text = page.string ?? ""
        pages.append([
            "index": index + 1,
            "text": text,
            "characters": text.count,
        ])
    }
    emit([
        "engine": "PDFKit",
        "page_count": document.pageCount,
        "pages": pages,
        "encrypted": document.isEncrypted,
    ])
}

/// One page rasterised to PNG at a bounded edge, for OCR of a page with no text layer.
func pdfRender(_ path: String, _ pageNumber: Int, _ output: String, _ maxEdge: Int) {
    guard let document = PDFDocument(url: URL(fileURLWithPath: path)) else { fail("unreadable-pdf") }
    guard let page = document.page(at: pageNumber - 1) else { fail("no-such-page") }
    let bounds = page.bounds(for: .mediaBox)
    let longest = max(bounds.width, bounds.height)
    guard longest > 0 else { fail("empty-page") }
    // Render above 1:1 for small pages: OCR accuracy on body text depends on glyph height in
    // pixels, and a 612pt-wide page at 1:1 is well under what the recognizer wants.
    let scale = min(CGFloat(maxEdge) / longest, 4.0)
    let size = CGSize(width: bounds.width * scale, height: bounds.height * scale)
    let image = page.thumbnail(of: size, for: .mediaBox)
    guard let tiff = image.tiffRepresentation,
          let source = CGImageSourceCreateWithData(tiff as CFData, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else { fail("render-failed") }
    let url = URL(fileURLWithPath: output) as CFURL
    guard let destination = CGImageDestinationCreateWithURL(url, UTType.png.identifier as CFString, 1, nil) else {
        fail("render-write-failed")
    }
    CGImageDestinationAddImage(destination, cgImage, nil)
    guard CGImageDestinationFinalize(destination) else { fail("render-write-failed") }
    emit(["ok": true, "width": Int(size.width), "height": Int(size.height), "scale": Double(scale)])
}

/// Vision OCR. Every line carries its own confidence and its normalised box, because an OCR
/// result without confidence cannot be reported with the uncertainty it actually has.
func ocr(_ path: String) {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        fail("unreadable-image")
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
    } catch {
        fail("ocr-failed: \(error.localizedDescription)")
    }
    var lines: [[String: Any]] = []
    for observation in (request.results ?? []) {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let box = observation.boundingBox
        lines.append([
            "text": candidate.string,
            "confidence": Double(candidate.confidence),
            "box": [Double(box.origin.x), Double(box.origin.y), Double(box.width), Double(box.height)],
        ])
    }
    // Reading order: top of the page first, then left to right within a band.
    lines.sort { left, right in
        let lb = left["box"] as! [Double]
        let rb = right["box"] as! [Double]
        if abs(lb[1] - rb[1]) > 0.012 { return lb[1] > rb[1] }
        return lb[0] < rb[0]
    }
    emit([
        "engine": "Vision.VNRecognizeTextRequest",
        "width": image.width,
        "height": image.height,
        "lines": lines,
    ])
}

let arguments = CommandLine.arguments
guard arguments.count >= 3 else { fail("usage: tool <pdftext|pdfrender|ocr> <path> [...]") }
switch arguments[1] {
case "pdftext":
    pdfText(arguments[2], arguments.count > 3 ? Int(arguments[3]) ?? 400 : 400)
case "pdfrender":
    guard arguments.count >= 6 else { fail("usage: tool pdfrender <pdf> <page> <out.png> <maxEdge>") }
    pdfRender(arguments[2], Int(arguments[3]) ?? 1, arguments[4], Int(arguments[5]) ?? 2000)
case "ocr":
    ocr(arguments[2])
default:
    fail("unknown-subcommand")
}
"""


def _source_digest() -> str:
    return hashlib.sha256(_SWIFT_SOURCE.encode("utf-8")).hexdigest()[:16]


def tools_dir() -> Path:
    """Where the compiled helper is cached. Overridable so a test never touches operator state."""
    override = os.environ.get("VOOL_READER_TOOLS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from core.runtime_paths import active_data_dir

        return Path(active_data_dir()) / "reader_tools"
    except Exception:
        return Path(tempfile.gettempdir()) / "vool_reader_tools"


def toolchain_available() -> bool:
    """Buildable AND confinable. A helper this runtime could not sandbox is not available to it."""
    from . import _sandbox

    return platform.system() == "Darwin" and os.access(SWIFTC, os.X_OK) and _sandbox.confinement_available()


def _binary_path() -> Path:
    return tools_dir() / f"{TOOL_NAME}-{TOOL_VERSION}-{_source_digest()}"


def ensure_tool() -> Path:
    """The compiled helper, built once and cached by the hash of the source above."""
    if not toolchain_available():
        raise ReaderUnavailable(
            "This machine has no Swift toolchain, so the built-in OCR and PDF page renderer cannot be built.",
            code="vision_toolchain_unavailable",
            remediation="Install the Xcode Command Line Tools (`xcode-select --install`) to enable OCR of scanned pages and images.",
        )
    binary = _binary_path()
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vool-vision-build-") as build_dir:
        source = Path(build_dir) / "tool.swift"
        source.write_text(_SWIFT_SOURCE, encoding="utf-8")
        staged = Path(build_dir) / "tool"
        try:
            completed = subprocess.run(
                [SWIFTC, "-O", "-swift-version", "5", str(source), "-o", str(staged)],
                capture_output=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReaderUnavailable(
                f"The built-in OCR helper could not be compiled ({exc}).",
                code="vision_build_failed",
                remediation="Check that the Xcode Command Line Tools are installed and working.",
            ) from exc
        if completed.returncode != 0 or not staged.exists():
            detail = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise ReaderUnavailable(
                "The built-in OCR helper could not be compiled: " + ("; ".join(detail[-3:]) or "unknown compiler error"),
                code="vision_build_failed",
                remediation="Check that the Xcode Command Line Tools are installed and working.",
            )
        # Atomic publish: a half-written binary is never visible to a concurrent extraction.
        pending = binary.with_name(binary.name + f".{os.getpid()}.tmp")
        shutil.copy2(staged, pending)
        os.chmod(pending, 0o700)
        os.replace(pending, binary)
    return binary


def _run(args: list[str], *, scratch: Path, fmt: str, timeout: int = SUBPROCESS_TIMEOUT_S, allow_gpu: bool = False) -> dict[str, Any]:
    binary = ensure_tool()
    # The compiled helper lives in the runtime's own data directory, which the profile denies by
    # default (it sits under the operator's home, or under the per-user temp container). Its
    # directory is therefore a DEMONSTRATED read dependency, granted explicitly and read-only:
    # without it the loader cannot map the binary and the decoder dies on SIGSEGV before `main`.
    result = run_confined(
        [str(binary), *args],
        scratch=scratch,
        timeout=timeout,
        fmt=fmt,
        allow_gpu=allow_gpu,
        extra_read=[str(binary.parent)],
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or f"exit {result.returncode}"
        if "password-protected" in detail:
            # A locked file is a fact about the FILE -- the decoder worked perfectly and told us
            # so. Reporting it as a decoder failure would send the operator to look at the wrong
            # thing, and the one action that actually helps (supply an unlocked copy) would go
            # unsaid.
            raise ReaderRefused(
                "The PDF is password-protected; its contents were not read.",
                code="password_protected",
                remediation="Remove the password, or attach an unprotected copy.",
                fmt=fmt,
            )
        raise ReaderFailed(f"The {fmt} decoder failed: {detail}", code="decoder_failed", fmt=fmt)
    try:
        payload = json.loads(result.stdout.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise ReaderFailed(f"The {fmt} decoder produced output this runtime could not read.", code="decoder_bad_output", fmt=fmt) from exc
    return payload if isinstance(payload, dict) else {}


def pdf_text_layer(pdf_path: Path, *, max_pages: int, scratch: Path) -> dict[str, Any]:
    return _run(["pdftext", str(pdf_path), str(int(max_pages))], scratch=scratch, fmt="pdf")


def render_pdf_page(pdf_path: Path, *, page_number: int, out_png: Path, max_edge: int, scratch: Path) -> dict[str, Any]:
    return _run(["pdfrender", str(pdf_path), str(int(page_number)), str(out_png), str(int(max_edge))], scratch=scratch, fmt="pdf")


def ocr_image(image_path: Path, *, scratch: Path) -> dict[str, Any]:
    """Recognised lines with per-line confidence, or a typed unavailability."""
    # `allow_gpu` ONLY here. PDF text extraction, rendering, archives and video never touch the
    # Neural Engine, so they never receive the grant that lets a process open those devices.
    return _run(["ocr", str(image_path)], scratch=scratch, fmt="image", allow_gpu=True)


def ocr_available() -> bool:
    """Whether OCR can actually run here -- checked without compiling anything."""
    return toolchain_available()


def ocr_text_with_confidence(image_path: Path, *, scratch: Path, min_confidence: float = 0.30) -> tuple[str, dict[str, Any]]:
    """OCR one image into text plus the uncertainty facts a caller must disclose.

    Lines under ``min_confidence`` are kept -- dropping them would silently delete evidence -- but
    counted, so the reader can state how much of what it returned the engine was unsure about.
    """
    payload = ocr_image(image_path, scratch=scratch)
    lines = [line for line in payload.get("lines") or [] if isinstance(line, dict)]
    texts: list[str] = []
    low = 0
    total = 0.0
    for line in lines:
        text = str(line.get("text") or "")
        if not text.strip():
            continue
        confidence = float(line.get("confidence") or 0.0)
        total += confidence
        if confidence < min_confidence:
            low += 1
        texts.append(text)
    mean = (total / len(texts)) if texts else 0.0
    facts = {
        "engine": str(payload.get("engine") or "Vision.VNRecognizeTextRequest"),
        "extractor_version": TOOL_VERSION,
        "lines": len(texts),
        "low_confidence_lines": low,
        "mean_confidence": round(mean, 3),
        "image_width": int(payload.get("width") or 0),
        "image_height": int(payload.get("height") or 0),
    }
    return "\n".join(texts), facts


__all__ = [
    "OCR_MAX_EDGE",
    "TOOL_VERSION",
    "Scratch",
    "ensure_tool",
    "ocr_available",
    "ocr_image",
    "ocr_text_with_confidence",
    "pdf_text_layer",
    "render_pdf_page",
    "toolchain_available",
    "tools_dir",
]
