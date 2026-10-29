"""PDF reading on Linux (fresh-eyes test 2026-10-29: "No module named 'Foundation' ... needs the macOS
system Python"). Off macOS the text layer is read by the reader lane's pypdf fallback, confined by
bubblewrap the way the macOS sandbox confines it: no network, no home folder, only its scratch."""
from __future__ import annotations

import platform

import pytest

from core.artifact_readers import _sandbox

pytestmark = pytest.mark.skipif(platform.system() != "Linux", reason="Linux confinement")


def _pdf(text: str) -> bytes:
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
               b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
               b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
               b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % number + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


needs_bwrap = pytest.mark.skipif(not _sandbox.confinement_available(), reason="bubblewrap cannot run here")


@needs_bwrap
def test_a_pdf_is_read_on_linux(tmp_path):
    from core.pdf_tools import extract_text

    path = tmp_path / "invoice.pdf"
    path.write_bytes(_pdf("Invoice total 412 euros"))
    result = extract_text(str(path))
    assert result["status"] == "ok", result
    assert "Invoice total 412 euros" in result["text"]
    assert result["source"] == "pypdf"


@needs_bwrap
def test_the_linux_sandbox_sees_only_its_scratch(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    with _sandbox.Scratch("vool-test-") as scratch:
        (scratch.path / "inside.txt").write_text("given")
        inside = _sandbox.run_confined(["/bin/cat", str(scratch.path / "inside.txt")], scratch=scratch.path)
        assert inside.stdout == b"given"
        for path in (str(outside), "/etc/passwd"):
            denied = _sandbox.run_confined(["/bin/cat", path], scratch=scratch.path)
            assert denied.returncode != 0 and denied.stdout == b""


@needs_bwrap
def test_the_linux_sandbox_has_no_network():
    with _sandbox.Scratch("vool-test-") as scratch:
        probe = _sandbox.run_confined(["/bin/sh", "-c", "cat /sys/class/net/*/operstate 2>/dev/null; ls /sys/class/net"],
                                      scratch=scratch.path)
        assert probe.stdout.split()[-1:] == [b"lo"] or b"eth" not in probe.stdout


def test_without_bubblewrap_the_reason_says_what_to_install(tmp_path, monkeypatch):
    from core.pdf_tools import extract_text

    monkeypatch.setattr(_sandbox, "_bwrap", lambda: "")
    monkeypatch.setattr(_sandbox, "_BWRAP_WORKS", {})
    path = tmp_path / "invoice.pdf"
    path.write_bytes(_pdf("x"))
    result = extract_text(str(path))
    assert result["status"] == "missing_dependency"
    assert "bubblewrap" in result["reason"] and "macOS" not in result["reason"]
