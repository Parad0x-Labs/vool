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


@needs_bwrap
def test_a_page_asked_for_by_number_is_that_page(tmp_path):
    # The clean loop reads "page 3" of a long PDF with start_page; off macOS that read used to start
    # at page 1 and label it page 3.
    from core.pdf_tools import extract_text
    from tests.reader_fixtures import native_text_pdf

    path = tmp_path / "report.pdf"
    path.write_bytes(native_text_pdf(pages=3, marker="BASALT-2290"))

    third = extract_text(str(path), start_page=3, max_pages=1)
    assert third["status"] == "ok", third
    assert (third["page_start"], third["pages_read"], third["pages_total"]) == (3, 1, 3)
    assert third["text"].startswith("[page 3]") and "BASALT-2290" in third["text"]
    assert "Quarterly revenue" not in third["text"] and "Page 2 of 3" not in third["text"]

    second = extract_text(str(path), start_page=2, max_pages=1)
    assert second["text"].startswith("[page 2]") and "Page 2 of 3" in second["text"]
    assert "BASALT-2290" not in second["text"]

    past_the_end = extract_text(str(path), start_page=9, max_pages=1)  # as the PDFKit helper: the last page
    assert past_the_end["page_start"] == 3 and "BASALT-2290" in past_the_end["text"]


@needs_bwrap
def test_a_pdf_over_the_size_ceiling_is_refused_before_it_is_copied(tmp_path, monkeypatch):
    # Review 2026-11-01 follow-up 1: the whole file was read into the serving process before any size
    # check, and one over the scratch quota was then refused as "expands to far more than its size".
    from core import pdf_tools
    from core.artifact_readers import _limits
    from core.artifact_readers import pdf as pdf_reader

    def never(*_args, **_kwargs):
        raise AssertionError("the decoder must not start for a PDF over the ceiling")

    monkeypatch.setattr(_limits, "MAX_PORTABLE_PDF_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(pdf_reader, "_text_layer_pypdf", never)
    path = tmp_path / "scan-archive.pdf"
    path.write_bytes(_pdf("big") + b"%" + b"x" * (3 * 1024 * 1024) + b"\n")

    result = pdf_tools.extract_text(str(path))
    # Its own status: "unreadable" makes the chat call the file a renamed text file.
    assert result["status"] == "too_large", result
    assert "4 MB" in result["reason"] and "2 MB" in result["reason"] and "split it" in result["reason"]


@needs_bwrap
def test_the_timeout_the_caller_gives_bounds_the_linux_read(tmp_path, monkeypatch):
    # Review 2026-11-01 follow-up 2: extract_text(timeout=...) bounded only the macOS helper.
    import time

    from core import pdf_tools
    from core.artifact_readers import pdf as pdf_reader

    monkeypatch.setattr(pdf_reader, "_PYPDF_SCRIPT", "import time\ntime.sleep(60)\n")
    path = tmp_path / "slow.pdf"
    path.write_bytes(_pdf("slow"))

    started = time.monotonic()
    result = pdf_tools.extract_text(str(path), timeout=2)
    elapsed = time.monotonic() - started
    assert result["status"] == "timeout", result
    assert elapsed < 20, elapsed


# --- what the owner reads in chat on Linux --------------------------------------------------------
# Real chat on a Linux build (2026-11-12, gated 3db84aac) read a 3-page PDF correctly, and every
# failure came back in the wrong words: the macOS system Python named on Linux, a 300 MB PDF called
# a renamed text file, a scan answered with "ModuleNotFoundError: No module named 'objc'", and a
# doubled full stop. These drive the same chat lane with the same reader, on Linux.

#: Words written for macOS, for a log or for a developer. None of them belongs in a Linux reply.
_NOT_FOR_THE_OWNER = ("macOS", "PyObjC", "PDFKit", "/usr/bin/python3", "ModuleNotFoundError", "objc",
                      "decoder", "unconfined", "..")


class _Agent:
    def _fast_path_result(self, *, response: str, reason: str, **_: object) -> dict[str, object]:
        return {"response": response, "reason": reason}

    def _emit_runtime_event(self, _source_context: object, **_: object) -> None:
        return None


@pytest.fixture
def documents(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("Desktop", "Downloads", "Documents"):
        (home / name).mkdir(parents=True)
    monkeypatch.setattr("core.runtime_execution_tools._machine_home", lambda: home)
    return home / "Documents"


def _chat(documents, name: str) -> str:
    from core.agent_runtime.fast_paths_pdf import maybe_handle_pdf_read

    result = maybe_handle_pdf_read(_Agent(), f"read {documents / name}", session_id="t", source_context={})
    assert result is not None, "the PDF lane did not take a named PDF"
    reply = str(result["response"])
    for words in _NOT_FOR_THE_OWNER:
        assert words not in reply, (words, reply)
    return reply


@needs_bwrap
def test_the_chat_reads_a_pdf_on_linux(documents):
    (documents / "invoice.pdf").write_bytes(_pdf("Invoice total 412 euros"))
    reply = _chat(documents, "invoice.pdf")
    assert "Invoice total 412 euros" in reply and "1 of 1 page(s)" in reply, reply


@needs_bwrap
def test_a_renamed_text_file_is_called_not_a_pdf_on_linux(documents):
    (documents / "renamed.pdf").write_text("a plain text note with a .pdf name\n")
    reply = _chat(documents, "renamed.pdf")
    assert "not a readable PDF" in reply and "renamed" in reply, reply


@needs_bwrap
def test_a_pdf_over_the_ceiling_is_told_its_size_not_called_renamed(documents, monkeypatch):
    from core.artifact_readers import _limits

    monkeypatch.setattr(_limits, "MAX_PORTABLE_PDF_BYTES", 2 * 1024 * 1024)
    (documents / "archive.pdf").write_bytes(_pdf("big") + b"%" + b"x" * (3 * 1024 * 1024) + b"\n")
    reply = _chat(documents, "archive.pdf")
    assert "too big" in reply and "4 MB" in reply and "2 MB" in reply, reply
    assert "renamed" not in reply, reply


@needs_bwrap
def test_a_scanned_pdf_on_linux_is_named_without_claiming_recognition(documents):
    (documents / "scan.pdf").write_bytes(_pdf(""))  # a page with no text on it: no text layer
    reply = _chat(documents, "scan.pdf")
    assert "scanned PDF" in reply and "not available on this computer" in reply, reply
    assert "OCR" not in reply, reply


def test_without_bubblewrap_the_chat_says_what_to_install(documents, monkeypatch):
    monkeypatch.setattr(_sandbox, "_bwrap", lambda: "")
    monkeypatch.setattr(_sandbox, "_BWRAP_WORKS", {})
    (documents / "invoice.pdf").write_bytes(_pdf("x"))
    reply = _chat(documents, "invoice.pdf")
    assert "bubblewrap" in reply and "sudo apt install bubblewrap" in reply, reply


def test_a_blocked_bubblewrap_is_not_told_to_install_it_again(documents, monkeypatch):
    monkeypatch.setattr(_sandbox, "_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(_sandbox, "_bwrap_works", lambda: False)
    (documents / "invoice.pdf").write_bytes(_pdf("x"))
    reply = _chat(documents, "invoice.pdf")
    assert "does not let it start" in reply and "install" not in reply.lower(), reply


@needs_bwrap
def test_a_decoder_missing_inside_the_sandbox_is_not_called_a_damaged_file(tmp_path, monkeypatch):
    from core import pdf_tools
    from core.artifact_readers import pdf as pdf_reader

    monkeypatch.setattr(pdf_reader, "_PYPDF_SCRIPT", 'print(\'{"error": "pypdf_unavailable"}\')\n')
    path = tmp_path / "invoice.pdf"
    path.write_bytes(_pdf("x"))
    result = pdf_tools.extract_text(str(path))
    assert result["status"] == "missing_dependency", result


def test_the_tool_reply_has_one_full_stop():
    from core.runtime_execution_tools import _pdf_result

    failed = _pdf_result("pdf.extract_text", {"status": "error", "path": "/x.pdf", "reason": "It was not read."},
                         verb="Read")
    assert failed.response_text == "Could not read `/x.pdf`: It was not read.", failed.response_text
