"""Answer identity, privacy and owner-chosen save boundaries for per-answer PDFs."""
import json
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pypdf import PdfReader

from tests import test_chat_export_api as export
from tests import test_chat_visuals_browser as visuals
from tests.test_chat_export_copy_ui import HTML

_isolated_home = export._isolated_home
browser = visuals.browser


@pytest.mark.parametrize("answer,expected", [
    ("| Asset | Amount |\n|---|---:|\n| GBP | 2362.80 |", "2362.80"),
    ("| Route | Parcels |\n|---|---:|\n| North | 763 |", "763"),
])
def test_pdf_is_only_the_selected_answer(answer, expected):
    export._seed(assistant="UNRELATED-ANSWER-831")
    with export._request_scope("chosen-answer"):
        export._seed(user="PRIVATE-QUESTION-452", assistant=answer)
    export._seed(session=export.OTHER, assistant="OTHER-CHAT-829")
    response = export._export(format="pdf", request_id="chosen-answer")
    assert response.status == 200, response.body
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)
    assert expected in text
    assert all(secret not in text for secret in ["UNRELATED-ANSWER-831", "PRIVATE-QUESTION-452", "OTHER-CHAT-829"])
    assert "vool-answer" in response.headers["Content-Disposition"]
    preview = json.loads(export._export(format="pdf", request_id="chosen-answer", preview="1").body)
    assert preview["user_messages"] == 0 and preview["assistant_messages"] == 1


def test_answer_export_rechecks_revocation_and_never_substitutes_a_neighbor():
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    commit = export._admit_finalize("REVOKED-ANSWER-451", request_id="revoked")
    with export._request_scope("revoked"):
        export._seed(assistant="REVOKED-ANSWER-451")
    export._seed(assistant="AVAILABLE-NEIGHBOR")
    assert set_availability(commit["finalization_id"], AVAILABILITY_WITHHELD, reason="test")
    assert export._export(format="pdf", request_id="revoked").status == 403
    assert export._export(format="pdf", request_id="missing").status == 404
    assert export._export(format="pdf", request_id="revoked", session=export.OTHER).status == 404


def test_duplicate_answer_identity_refuses_instead_of_picking_one():
    for text in ["First", "Second"]:
        with export._request_scope("duplicate"):
            export._seed(assistant=text)
    assert export._export(format="pdf", request_id="duplicate").status == 409


@pytest.mark.parametrize("cancel", [False, True])
def test_native_save_uses_only_picker_path_and_cancel_writes_nothing(tmp_path, monkeypatch, cancel):
    from installer.bundle import vool_window as window

    destination = tmp_path / "selected.pdf"
    body = b"%PDF-1.4\ncontrolled download"
    response = BytesIO(body)
    response.headers = Message()
    response.headers["Content-Type"] = "application/pdf"
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(window.urllib.request, "build_opener", lambda *_: opener)
    native = Mock()
    native.create_file_dialog.return_value = None if cancel else (str(destination),)
    api = window._WindowApi()
    api.set_window(native, SimpleNamespace(SAVE_DIALOG="save"))
    result = api.save_answer_pdf("chat-one", "answer-one")
    assert result == ({"ok": False, "cancelled": True} if cancel else {"ok": True})
    assert destination.exists() is not cancel
    if not cancel:
        assert destination.read_bytes() == body
    assert "request_id=answer-one" in opener.open.call_args.args[0]
    assert not list(tmp_path.glob(".vool-pdf-*"))


def test_native_save_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    from installer.bundle import vool_window as window

    destination = tmp_path / "existing.pdf"
    destination.write_bytes(b"OWNER-CONTENT")
    response = BytesIO(b"%PDF-1.4\nnew")
    response.headers = Message()
    response.headers["Content-Type"] = "application/pdf"
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(window.urllib.request, "build_opener", lambda *_: opener)
    native = Mock()
    native.create_file_dialog.return_value = (str(destination),)
    api = window._WindowApi()
    api.set_window(native, SimpleNamespace(SAVE_DIALOG="save"))
    assert api.save_answer_pdf("chat", "answer")["error"] == "destination_exists"
    assert destination.read_bytes() == b"OWNER-CONTENT"
    assert not list(tmp_path.glob(".vool-pdf-*"))


@pytest.mark.parametrize("answer", ["GBP received: 2362.80 GBP.", "Retained shipment: 763 parcels."])
def test_shipped_answer_control_downloads_its_own_persisted_answer(browser, answer):
    with export._request_scope("selected-answer"):
        export._seed(user="Do not export this question", assistant=answer)
    export._seed(assistant="Do not export this neighboring answer")
    script = HTML[HTML.index("function mountAnswerExport("):HTML.index("async function mountProofChip(")]
    page_source = ('<div id="answer"><div class="msg-actions"></div></div><script>' + script
        + 'mountAnswerExport(document.getElementById("answer"), ' + json.dumps(export.SESSION)
        + ', "selected-answer");</script>')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body, mime, status, headers = page_source.encode(), "text/html", 200, {}
            else:
                response = export._get(self.path)
                body, mime, status, headers = response.body, response.content_type, response.status, response.headers
            self.send_response(status)
            self.send_header("Content-Type", mime)
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    page = browser.new_page(accept_downloads=True)
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}")
        with page.expect_download() as download:
            page.get_by_role("button", name="Download this answer as PDF").click()
        text = "\n".join(p.extract_text() for p in PdfReader(download.value.path()).pages)
        assert answer in text
        assert "Do not export" not in text
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize('fmt,mime,body', [
    ('md', 'text/markdown; charset=utf-8', '## Žinutė\nAmber — 18 samples.\n'.encode()),
    ('txt', 'text/plain; charset=utf-8', 'Résumé: Violet → 42 samples.\n'.encode()),
    ('pdf', 'application/pdf', b'%PDF-1.4\nwhole chat'),
])
@pytest.mark.parametrize('outcome', ['save', 'cancel', 'exists', 'wrong_type'])
def test_native_transcript_export_preserves_bytes_and_picker_authority(tmp_path, monkeypatch, fmt, mime, body, outcome):
    from urllib.parse import parse_qs, urlparse

    from installer.bundle import vool_window as window

    destination = tmp_path / ('chosen.' + fmt)
    if outcome == 'exists':
        destination.write_bytes(b'OWNER-CONTENT')
    response = BytesIO(body)
    response.headers = Message()
    response.headers['Content-Type'] = 'text/html' if outcome == 'wrong_type' else mime
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(window.urllib.request, 'build_opener', lambda *_: opener)
    native = Mock()
    native.create_file_dialog.return_value = None if outcome == 'cancel' else (str(destination),)
    api = window._WindowApi()
    api.set_window(native, SimpleNamespace(SAVE_DIALOG='save'))
    result = api.save_chat_export('chat & chosen', fmt, True, False)
    query = parse_qs(urlparse(opener.open.call_args.args[0]).query)
    assert query == {'session': ['chat & chosen'], 'format': [fmt], 'timestamps': ['1'], 'no_attachments': ['1']}
    if outcome == 'save':
        assert result == {'ok': True}
        assert destination.read_bytes() == body
    elif outcome == 'cancel':
        assert result == {'ok': False, 'cancelled': True}
        assert not destination.exists()
    elif outcome == 'exists':
        assert result['error'] == 'destination_exists'
        assert destination.read_bytes() == b'OWNER-CONTENT'
    else:
        assert result['ok'] is False
        assert not destination.exists()
        native.create_file_dialog.assert_not_called()
    assert not list(tmp_path.glob('.vool-*'))


@pytest.mark.parametrize('args', [
    ('', 'md', False, True),
    ('chat', '../../escape', False, True),
    ('chat', 'html', False, True),
    ('chat', 'md', 'false', True),
    ('chat', 'txt', False, {'path': '/tmp/injected'}),
])
def test_native_transcript_export_rejects_untyped_requests_before_io(monkeypatch, args):
    from installer.bundle import vool_window as window

    opener = Mock()
    monkeypatch.setattr(window.urllib.request, 'build_opener', opener)
    api = window._WindowApi()
    native = Mock()
    api.set_window(native, SimpleNamespace(SAVE_DIALOG='save'))
    assert api.save_chat_export(*args)['ok'] is False
    opener.assert_not_called()
    native.create_file_dialog.assert_not_called()
