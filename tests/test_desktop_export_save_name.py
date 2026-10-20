"""The desktop app's save dialog offers the same chat-based file name the browser saves under.

Driven through the real ``_WindowApi.save_chat_export`` / ``save_answer_pdf`` code in
``installer/bundle/vool_window.py``: the app fetches the export over real HTTP from a local
server that answers with the real ``/api/chat/export`` door (``dispatch_get``), exactly as the
packaged app fetches it from its own API. Only the native file dialog (pywebview) is faked, so
the test can read the name the dialog was opened with and pick where the file goes.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import unquote

import pytest

from tests import test_chat_export_api as export

_isolated_home = export._isolated_home

FORBIDDEN = set('/\\:*?"<>|')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


class _Server:
    """A local HTTP server in front of the real export door, or one that sends forged headers."""

    def __init__(self, forged_headers: dict[str, str] | None = None, body: bytes = b"## forged\n",
                 content_type: str = "text/markdown; charset=utf-8"):
        forged = forged_headers

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if forged is None:
                    response = export._get(self.path)
                    status, mime, payload, headers = (response.status, response.content_type,
                                                      response.body, dict(response.headers))
                else:
                    status, mime, payload, headers = 200, content_type, body, forged
                self.send_response(status)
                self.send_header("Content-Type", mime)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _desktop_save(monkeypatch, origin: str, destination: Path | None, call):
    """Run one desktop save; return (result, the name the save dialog offered)."""
    from installer.bundle import vool_window as window

    monkeypatch.setattr(window, "_API_ORIGIN", origin)
    native = Mock()
    native.create_file_dialog.return_value = None if destination is None else (str(destination),)
    api = window._WindowApi()
    api.set_window(native, SimpleNamespace(SAVE_DIALOG="save"))
    result = call(api)
    native.create_file_dialog.assert_called_once()
    assert native.create_file_dialog.call_args.args == ("save",)
    return result, native.create_file_dialog.call_args.kwargs["save_filename"]


def _assert_safe_everywhere(name: str, fmt: str) -> None:
    assert name.endswith("." + fmt)
    assert Path(name).name == name and "/" not in name and "\\" not in name
    assert not set(name) & FORBIDDEN
    assert not any(unicodedata.category(ch)[0] == "C" and ch not in "‌‍" for ch in name)
    assert not name.startswith((".", " ", "-"))
    stem = name[: -len(fmt) - 1]
    assert stem and stem == stem.rstrip(". ")
    assert stem.split(".")[0].strip().upper() not in WINDOWS_RESERVED
    assert len(name.encode("utf-8")) <= 255


@pytest.mark.parametrize("fmt", ["md", "txt", "pdf"])
def test_desktop_save_dialog_offers_the_chats_name(tmp_path, monkeypatch, fmt) -> None:
    export._seed(user="can you write a note about the fence", assistant="Here is the note.")
    from core.persistent_memory import set_session_meta

    set_session_meta(export.SESSION, title="Fence note")
    destination = tmp_path / ("chosen." + fmt)
    with _Server() as origin:
        result, offered = _desktop_save(
            monkeypatch, origin, destination, lambda api: api.save_chat_export(export.SESSION, fmt))
    assert offered == f"Fence note - VOOL chat - {_today()}.{fmt}"
    assert result == {"ok": True}
    # The bytes on disk are the server's export, saved where the person chose.
    saved = destination.read_bytes()
    if fmt == "pdf":
        assert saved.startswith(b"%PDF-")
    else:
        assert saved == export._export(format=fmt).body


@pytest.mark.parametrize("title", [
    "CON", "NUL", "aux", "COM1", "lpt9",
    "../../etc/passwd", "..", "...", ". .",
    'a/b\\c:d*e?f"g<h>i|j',
    "trailing dots and spaces...   ",
    "   ", "\t\n",
    "Žinutė apie tvorą 🏡 日本語 ملاحظة",
    "photo‮gpj.exe",
    "line one\nline two\x00\x1f",
    "garden " * 60 + "🌻" * 50,
])
def test_desktop_name_is_safe_on_every_system_and_matches_the_browser(tmp_path, monkeypatch, title) -> None:
    from core.persistent_memory import set_session_meta

    # No first message either, so a blank or all-dots name leaves the chat with no usable title.
    export._seed(user="", assistant="answer")
    set_session_meta(export.SESSION, title=title)
    with _Server() as origin:
        result, offered = _desktop_save(
            monkeypatch, origin, None, lambda api: api.save_chat_export(export.SESSION, "md"))
    assert result == {"ok": False, "cancelled": True}
    _assert_safe_everywhere(offered, "md")
    # The same exact UTF-8 name (filename*) the server's Content-Disposition gives the browser.
    disposition = export._export().headers["Content-Disposition"]
    exact = re.search(r"filename\*=UTF-8''([^;\s]+)", disposition)
    assert exact, disposition
    assert offered == unquote(exact.group(1), errors="strict")
    if not title.strip(" .\t\n"):
        assert offered == f"VOOL chat - {_today()}.md"


def test_desktop_answer_pdf_is_named_like_the_browsers(tmp_path, monkeypatch) -> None:
    export._seed(user="FIRST-QUESTION about my bank", assistant="Earlier answer.")
    with export._request_scope("answer-two"):
        export._seed(user="PRIVATE-QUESTION-452", assistant="The fence is 2 m.")
    with _Server() as origin:
        _, unrenamed = _desktop_save(
            monkeypatch, origin, None, lambda api: api.save_answer_pdf(export.SESSION, "answer-two"))
        from core.persistent_memory import set_session_meta

        set_session_meta(export.SESSION, title="Fence note")
        destination = tmp_path / "answer.pdf"
        result, renamed = _desktop_save(
            monkeypatch, origin, destination, lambda api: api.save_answer_pdf(export.SESSION, "answer-two"))
    # A single answer never carries its question in the name.
    assert unrenamed == f"VOOL answer - {_today()}.pdf"
    assert renamed == f"Fence note - VOOL answer - {_today()}.pdf"
    assert result == {"ok": True} and destination.read_bytes().startswith(b"%PDF-")


@pytest.mark.parametrize("disposition", [
    None,                                                          # no header at all
    "attachment",                                                  # no file name
    "attachment; filename*=UTF-8''..%2F..%2Fescape.md",            # a path
    "attachment; filename*=UTF-8''C%3A%5CUsers%5Cx%5Cescape.md",   # a Windows path
    "attachment; filename*=UTF-8''..md",                           # only dots
    'attachment; filename="CON.md"',                               # reserved on Windows
    'attachment; filename="nul .md"',                              # reserved, with a space
    'attachment; filename="note.exe"',                             # not the asked format
    'attachment; filename="note.md.exe"',
    "attachment; filename*=UTF-8''photo%E2%80%AEdm.exe.md",        # direction override
    "attachment; filename*=UTF-8''line%0Abreak.md",                # control character
    "attachment; filename*=UTF-8''trailing.%20.md",                # trailing dot and space
    "attachment; filename*=UTF-8''.hidden.md",                     # hidden file
    "attachment; filename*=UTF-8''" + "a" * 300 + ".md",           # too long
    "attachment; filename*=UTF-8''%FF%FE.md",                      # not UTF-8
])
def test_a_name_the_desktop_cannot_trust_falls_back_to_a_neutral_one(tmp_path, monkeypatch, disposition) -> None:
    """Unknown or unsafe names fail closed to the neutral name; the save itself still works."""
    headers = {} if disposition is None else {"Content-Disposition": disposition}
    destination = tmp_path / "chosen.md"
    with _Server(forged_headers=headers) as origin:
        result, offered = _desktop_save(
            monkeypatch, origin, destination, lambda api: api.save_chat_export(export.SESSION, "md"))
    assert offered == "VOOL chat.md"
    assert result == {"ok": True} and destination.read_bytes() == b"## forged\n"
    assert [p.name for p in tmp_path.iterdir()] == ["chosen.md"]


def test_a_forged_answer_name_falls_back_to_the_neutral_answer_name(monkeypatch) -> None:
    with _Server(forged_headers={"Content-Disposition": "attachment; filename*=UTF-8''..%2Fx.pdf"},
                 body=b"%PDF-1.4\nforged", content_type="application/pdf") as origin:
        _, offered = _desktop_save(
            monkeypatch, origin, None, lambda api: api.save_answer_pdf(export.SESSION, "answer"))
    assert offered == "VOOL answer.pdf"
