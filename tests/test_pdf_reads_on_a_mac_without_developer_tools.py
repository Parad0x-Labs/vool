"""A PDF reads on a stock Mac: no developer tools, no PyObjC, nothing in the owner's user folder.

Benchmark, 2026-11-28: ``pdf.extract_text`` ran its PDFKit helper under /usr/bin/python3 first. On a
Mac that is Apple's launcher for the developer tools, so a Mac without them got the "install the
command line developer tools" dialog, and PDFKit's Python bindings are not part of macOS, so every
other Mac got "No module named 'Foundation'". It read only on the one Mac whose owner had PyObjC in
~/Library/Python. The shipped pypdf reader, under VOOL's own Python, now reads first everywhere, and
no /usr/bin developer launcher is started where the tool behind it is missing (read from disk).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core import apple_developer_tools, pdf_tools
from tests.test_invoice_folder_follow_ups_served import _pdf

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def stock_mac(tmp_path, monkeypatch):
    """macOS as this process sees it, with no developer folder anywhere the launcher would look."""
    # Only the two readings of "is this a Mac" these laws depend on: the reader sandbox keeps the
    # real platform, so the shipped reader runs confined exactly as it does on this machine.
    monkeypatch.setattr(apple_developer_tools, "_on_macos", lambda: True)
    monkeypatch.setattr(pdf_tools, "_uses_pdfkit", lambda: True)
    monkeypatch.delenv("DEVELOPER_DIR", raising=False)
    monkeypatch.setattr(apple_developer_tools, "_SELECTED_LINK", tmp_path / "no-link")
    monkeypatch.setattr(apple_developer_tools, "_XCODE_DEVELOPER", tmp_path / "no-xcode")
    monkeypatch.setattr(apple_developer_tools, "_COMMAND_LINE_TOOLS", tmp_path / "no-clt")
    return tmp_path


def _tool(folder: Path, sub: str, name: str) -> None:
    target = folder / sub / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    target.chmod(0o755)


@pytest.mark.parametrize("name", ["/usr/bin/python3", "/usr/bin/swiftc", "/usr/bin/git", "/usr/bin/clang"])
def test_a_developer_launcher_without_its_tools_is_not_installed(stock_mac, name):
    assert apple_developer_tools.developer_folder() is None
    assert apple_developer_tools.installed(name) is False


@pytest.mark.parametrize("where, sub, name", [
    ("no-clt", "usr/bin", "python3"),
    ("no-clt", "usr/bin", "swiftc"),
    ("no-xcode", "usr/bin", "git"),
    ("no-xcode", "Toolchains/XcodeDefault.xctoolchain/usr/bin", "swiftc"),
])
def test_a_launcher_whose_tool_is_in_the_developer_folder_is_installed(stock_mac, where, sub, name):
    _tool(stock_mac / where, sub, name)
    assert apple_developer_tools.installed(f"/usr/bin/{name}") is True
    # Another launcher stays missing: a folder holding one tool does not make every tool real.
    assert apple_developer_tools.installed("/usr/bin/lldb") is False


def test_the_chosen_developer_folder_wins(stock_mac, monkeypatch):
    chosen = stock_mac / "chosen"
    _tool(chosen, "usr/bin", "python3")
    link = stock_mac / "link"
    link.symlink_to(chosen)
    monkeypatch.setattr(apple_developer_tools, "_SELECTED_LINK", link)
    assert apple_developer_tools.developer_folder() == chosen
    assert apple_developer_tools.installed("/usr/bin/python3") is True


def test_a_real_program_in_usr_bin_needs_no_developer_folder(stock_mac):
    # /usr/bin/env is the program itself on every system, never a developer launcher.
    assert apple_developer_tools.installed("/usr/bin/env") is True


def test_a_stock_mac_reads_a_pdf_with_the_shipped_reader_and_never_starts_the_launcher(stock_mac):
    pdf = stock_mac / "invoice.pdf"
    pdf.write_bytes(_pdf(["INVOICE 7731", "Total: 42.00 EUR"], 10))
    with mock.patch.object(pdf_tools, "_run_helper", side_effect=AssertionError("started /usr/bin/python3")):
        result = pdf_tools.extract_text(str(pdf))
    assert result["status"] == "ok" and result["source"] == "pypdf", result
    assert "Total: 42.00 EUR" in result["text"], result


def test_a_stock_mac_offers_no_scan_reading_and_never_starts_the_launcher(stock_mac):
    pdf = stock_mac / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    assert pdf_tools.ocr_available() is False
    with mock.patch.object(pdf_tools, "_run_helper", side_effect=AssertionError("started /usr/bin/python3")):
        result = pdf_tools.ocr(str(pdf))
    assert result["status"] == "missing_dependency" and result["cause"] == "ocr_not_available", result


def test_where_the_shipped_reader_has_no_answer_pdfkit_runs_only_with_a_real_python(stock_mac):
    pdf = stock_mac / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    no_decoder = {"status": "missing_dependency", "reason": "no decoder"}
    pdfkit = {"status": "ok", "pages_total": 1, "pages_read": 1, "page_text": ["read by PDFKit"]}
    with mock.patch.object(pdf_tools, "_extract_portable", return_value=no_decoder), \
            mock.patch.object(pdf_tools, "_run_helper", side_effect=AssertionError("started the launcher")):
        assert pdf_tools.extract_text(str(pdf))["reason"] == "no decoder"
    _tool(stock_mac / "no-clt", "usr/bin", "python3")
    with mock.patch.object(pdf_tools, "_extract_portable", return_value=no_decoder), \
            mock.patch.object(pdf_tools, "_run_helper", return_value=pdfkit):
        read = pdf_tools.extract_text(str(pdf))
    assert read["status"] == "ok" and read["source"] == "pdfkit" and "read by PDFKit" in read["text"], read


@pytest.mark.parametrize("module, words", [
    ("core.artifact_readers.vision_tool", "toolchain_unavailable_words"),
    ("core.artifact_readers.speech_tool", "_toolchain_unavailable_words"),
])
def test_a_stock_mac_does_not_compile_its_helpers_and_names_the_missing_tools(stock_mac, monkeypatch, module, words):
    import importlib

    from core.artifact_readers import _sandbox

    tool = importlib.import_module(module)
    monkeypatch.setattr(_sandbox, "confinement_available", lambda: True)  # only the compiler is missing
    monkeypatch.setattr(tool.platform, "system", lambda: "Darwin")
    assert tool.toolchain_available() is False
    assert "Command Line Tools" in getattr(tool, words)()[0]


def test_a_stock_mac_never_asks_git_for_the_build_stamp(stock_mac):
    from core import build_provenance, proof_manifest
    from core.web.api import runtime

    with mock.patch("subprocess.run", side_effect=AssertionError("started /usr/bin/git")), \
            mock.patch("subprocess.check_output", side_effect=AssertionError("started /usr/bin/git")):
        assert runtime.git_output(REPO, "rev-parse", "HEAD") == ""
        assert build_provenance.worktree_diff_sha256(REPO) == ""
        assert proof_manifest.repo_source_snapshot(REPO)["source_kind"] != "git"


def test_a_pdf_reads_in_a_home_with_no_user_site_packages(tmp_path):
    """The whole read in a fresh process whose HOME holds nothing: no ~/Library/Python, no user site,
    no PYTHONPATH beyond the app itself. The reader's text comes from what VOOL ships."""
    home = tmp_path / "home"
    home.mkdir()
    pdf = tmp_path / "statement.pdf"
    pdf.write_bytes(_pdf(["STATEMENT 2026-11", "Balance: 318.40 EUR"], 10))
    program = (
        "import json, sys, site\n"
        "from core import pdf_tools\n"
        "result = pdf_tools.extract_text(sys.argv[1])\n"
        "print(json.dumps({'user_site_on_path': any(str(p).startswith(sys.argv[2]) for p in sys.path),"
        " 'status': result['status'], 'source': result['source'], 'text': result['text']}))\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env.update({"HOME": str(home), "PYTHONPATH": str(REPO), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    proc = subprocess.run([sys.executable, "-c", program, str(pdf), str(home)], capture_output=True, text=True,
                          env=env, cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    assert seen["user_site_on_path"] is False, seen
    assert seen["status"] == "ok" and seen["source"] == "pypdf", seen
    assert "Balance: 318.40 EUR" in seen["text"], seen
