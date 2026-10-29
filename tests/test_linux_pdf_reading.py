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


@needs_bwrap
def test_the_linux_sandbox_writes_nowhere_but_its_scratch():
    # Review 2026-10-29: the root, /dev and a /tmp tmpfs took 2.3 GB of memory-backed files that the
    # scratch quota never saw.
    with _sandbox.Scratch("vool-test-") as scratch:
        probe = _sandbox.run_confined(
            ["/bin/sh", "-c", "for p in /tmp/x /x /dev/x /dev/shm/x /usr/x; do echo z > $p 2>/dev/null && echo WROTE $p; done; "
             "echo z > /dev/null && echo z > ./mine && echo SCRATCH"],
            scratch=scratch.path,
        )
        assert probe.stdout.split() == [b"SCRATCH"], probe.stdout
        assert (scratch.path / "mine").read_text() == "z\n"


@needs_bwrap
def test_the_linux_sandbox_cannot_open_a_socket_to_anywhere(tmp_path):
    import socket
    import sys

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    with _sandbox.Scratch("vool-test-") as scratch:
        script = scratch.path / "net.py"
        script.write_text(
            "import socket\n"
            f"for target in (('127.0.0.1', {port}), ('1.1.1.1', 443)):\n"
            "    try:\n"
            "        socket.create_connection(target, timeout=3).close(); print('CONNECTED', target)\n"
            "    except OSError:\n"
            "        print('REFUSED')\n"
        )
        probe = _sandbox.run_confined([sys.executable, "-I", "-S", str(script)], scratch=scratch.path, extra_read=_interpreter_scope())
    listener.close()
    assert probe.stdout.split() == [b"REFUSED", b"REFUSED"], probe.stdout


def _interpreter_scope() -> list[str]:
    from core.artifact_readers import pdf

    return pdf._fallback_read_scope()


@needs_bwrap
def test_a_decoder_cannot_make_itself_a_user_namespace():
    if not _sandbox._bwrap_supports("--disable-userns"):
        pytest.skip("this bubblewrap is older than 0.8.0 and cannot refuse it")
    with _sandbox.Scratch("vool-test-") as scratch:
        probe = _sandbox.run_confined(["/bin/sh", "-c", "unshare -Ur true 2>/dev/null && echo NESTED || echo REFUSED"], scratch=scratch.path)
    assert probe.stdout.strip() == b"REFUSED"


@needs_bwrap
def test_a_decoder_cannot_take_more_memory_than_its_ceiling():
    import sys

    with _sandbox.Scratch("vool-test-") as scratch:
        script = scratch.path / "mem.py"
        script.write_text("try:\n    b = bytearray(6 * 1024 ** 3); print('GOT')\nexcept MemoryError:\n    print('REFUSED')\n")
        probe = _sandbox.run_confined([sys.executable, "-I", "-S", str(script)], scratch=scratch.path, extra_read=_interpreter_scope())
    assert probe.stdout.strip() == b"REFUSED", probe.stdout


@needs_bwrap
def test_a_frame_the_decoder_links_elsewhere_is_not_read(tmp_path, monkeypatch):
    from core.artifact_readers import video

    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    fake = tmp_path / "bin" / "ffmpeg"
    fake.parent.mkdir()
    fake.write_text(f'#!/bin/sh\nfor last; do :; done\nln -sf "{secret}" "$last"\n')
    fake.chmod(0o755)
    monkeypatch.setattr(video, "_require_tools", lambda: (str(fake), "/bin/true"))
    with _sandbox.Scratch("vool-test-") as scratch, _sandbox.extraction_budget(scratch=scratch.path):
        source = scratch.path / "input.bin"
        source.write_bytes(b"\0" * 64)
        frames = video._extract_frames(source, [1.0], scratch=scratch.path, max_edge=512)
    assert frames == []


@needs_bwrap
def test_installing_bubblewrap_works_without_a_restart(monkeypatch):
    import time

    real = _sandbox._bwrap
    monkeypatch.setattr(_sandbox, "_BWRAP_WORKS", {})
    monkeypatch.setattr(_sandbox, "_BWRAP_OPTIONS", {})
    monkeypatch.setattr(_sandbox, "_BWRAP_CHECKED_AT", {})
    monkeypatch.setattr(_sandbox, "_bwrap", lambda: "")
    assert not _sandbox.confinement_available()
    monkeypatch.setattr(_sandbox, "_bwrap", real)  # the person installs it, as the reply told them
    _sandbox._BWRAP_CHECKED_AT["ok"] = time.monotonic() - _sandbox._BWRAP_RECHECK_SECONDS - 1
    assert _sandbox.confinement_available()
