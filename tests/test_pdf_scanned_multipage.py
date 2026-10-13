"""A scanned PDF of any length is reported as scanned, so the model is told to run pdf.ocr.

`pdf.extract_text` assembles its text with a "[page N]" marker per page. The scanned-PDF check
used to strip only "[page 1]", so a two-page scan came back "ok" with nothing but markers and
the model was never told to recognise it. These tests feed `extract_text` the exact reply the
PDFKit helper gives for blank pages (that helper only runs on macOS), and, where PDFKit exists,
a real two-page scan.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core import pdf_tools
from core.runtime_execution_tools import execute_runtime_tool

SYSTEM_PYTHON = "/usr/bin/python3"
_HAS_PDFKIT = (
    Path(SYSTEM_PYTHON).exists()
    and subprocess.run(
        [SYSTEM_PYTHON, "-c", "import Quartz, Foundation"], capture_output=True
    ).returncode == 0
)


def _helper_reply(pages: list[str]) -> dict:
    return {"status": "ok", "pages_total": len(pages), "pages_read": len(pages), "page_text": pages}


@pytest.fixture
def scan(tmp_path):
    target = tmp_path / "scan.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    return target


@pytest.mark.parametrize("page_count", [1, 2, 3, 12])
def test_a_scan_of_any_length_is_reported_as_scanned(scan, page_count: int) -> None:
    pages = ["", " \n", "\n\n\t"][:page_count] + [""] * max(0, page_count - 3)
    with mock.patch.object(pdf_tools, "_run_helper", return_value=_helper_reply(pages)):
        result = pdf_tools.extract_text(str(scan))

    assert result["status"] == "no_text_layer"
    assert "pdf.ocr" in result["reason"]
    assert result["pages_read"] == page_count


def test_a_scan_with_one_text_page_is_still_read(scan) -> None:
    """Only a PDF with no text on any page is called scanned; one real line keeps it "ok"."""

    with mock.patch.object(
        pdf_tools, "_run_helper", return_value=_helper_reply(["", "Invoice 7731 total 42.00", ""])
    ):
        result = pdf_tools.extract_text(str(scan))

    assert result["status"] == "ok"
    assert "Invoice 7731 total 42.00" in result["text"]


def test_a_page_that_only_reads_like_a_marker_is_text(scan) -> None:
    """Text that happens to say "[page 1]" is still text on the page, not an empty scan."""

    with mock.patch.object(pdf_tools, "_run_helper", return_value=_helper_reply(["[page 1]", ""])):
        result = pdf_tools.extract_text(str(scan))

    assert result["status"] == "ok"


def test_the_model_is_told_to_run_ocr_on_a_two_page_scan(scan) -> None:
    """The tool result the model reads: not ok, and it names pdf.ocr as the next step."""

    with mock.patch.object(pdf_tools, "_run_helper", return_value=_helper_reply(["", ""])):
        result = execute_runtime_tool(
            "pdf.extract_text", {"path": str(scan)}, source_context={"surface": "api"}
        )

    assert result is not None and not result.ok
    assert result.status == "no_text_layer"
    assert "pdf.ocr" in result.response_text
    assert "[page 2]" not in result.response_text


_MAKE_BLANK_PAGES = r'''
import sys, Quartz, CoreFoundation
path, count = sys.argv[1], int(sys.argv[2])
url = CoreFoundation.CFURLCreateWithFileSystemPath(None, path, CoreFoundation.kCFURLPOSIXPathStyle, False)
ctx = Quartz.CGPDFContextCreateWithURL(url, Quartz.CGRectMake(0, 0, 612, 792), None)
for _ in range(count):
    Quartz.CGPDFContextBeginPage(ctx, None)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(60, 600, 200, 100))
    Quartz.CGPDFContextEndPage(ctx)
Quartz.CGPDFContextClose(ctx)
'''


@pytest.mark.skipif(not _HAS_PDFKIT, reason="macOS PDFKit bindings not present")
def test_a_real_two_page_scan_is_reported_as_scanned(tmp_path) -> None:
    target = tmp_path / "two-page-scan.pdf"
    proc = subprocess.run(
        [SYSTEM_PYTHON, "-c", _MAKE_BLANK_PAGES, str(target), "2"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr

    result = pdf_tools.extract_text(str(target))

    assert result["pages_read"] == 2
    assert result["status"] == "no_text_layer"
    assert "pdf.ocr" in result["reason"]
