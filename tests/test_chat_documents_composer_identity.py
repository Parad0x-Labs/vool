"""Chip <-> marker identity, byte-identical composer restoration, visible refusals beside the
composer, one upload per document, and dropped text files -- in a REAL browser.

Ported from build/vool-working-mark-20260902 (31a37a40) onto the canonical interceptor: a
generated marker `[document: <name> (<size>) #<id>]` carries its chip's identity in the user's own
words; it is rewritten to the server's name and durable id once staged; a final refusal (a live
credential) removes exactly that marker so the composer returns to its pre-paste bytes while the
chip stays red beside the composer with the door's reason; removing a chip removes only its own
marker; hand-typed text that merely looks like a marker is never touched. A dropped text file goes
through the same interceptor with its own name; a paste is uploaded exactly once.
"""

from __future__ import annotations

import json

import pytest

from tests import served_browser
from tests.test_chat_documents_ui import LIMITS, LONG_PASTE, _chips, _paste, _stage_document, _wait_chips
from tests.test_chat_documents_ui import _DocServer as _BaseServer

SECRET_PASTE = ("deploy log line\n" * 300) + "AWS_ACCESS_KEY_ID = AKIA4T7NCXKDTL5TQX3P\n" + ("more lines\n" * 100)


class _Server(_BaseServer):
    """The document door with the real refusal shape for a credential-shaped paste."""

    def route(self, route) -> None:
        request = route.request
        if "/api/chat/attachments/upload" in request.url and not self.abort_uploads:
            data = request.post_data_buffer or b""
            if b"AKIA4T7NCXKDTL5TQX3P" in data:
                self.uploads.append({"refused": True, "size": len(data)})
                route.fulfill(status=422, content_type="application/json", body=json.dumps({"ok": False, "error": "secret_detected", "message": "the pasted text looks like it carries a live credential or token (rule: aws_access_key); nothing was saved. Remove the secret, then paste or attach again."}))
                return
        super().route(route)


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


@pytest.fixture()
def page(browser):
    server = _Server()
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", server.route)
    page.set_viewport_size({"width": 1280, "height": 860})
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(120)
    yield page, server, errors
    context.close()


def _marker_count(page) -> int:
    return page.evaluate("() => (document.getElementById('input').value.match(/\\[document: /g) || []).length")


def test_the_marker_carries_the_chips_identity_and_is_rewritten_to_the_durable_id(page) -> None:
    page, server, errors = page
    _stage_document(page, LONG_PASTE, before="before ")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    value = page.input_value("#input")
    chip = _chips(page)[0]
    assert value.startswith("before "), "the user's own words come first"
    assert f"[document: {chip['name']} (" in value and f"#{chip['id']}]" in value, value
    assert _marker_count(page) == 1 and "la-" not in value, "the marker must point at the server id once staged"
    assert len(server.uploads) == 1, "one paste, one upload"
    assert errors == []


def test_a_refused_paste_leaves_the_composer_byte_identical_and_the_reason_beside_it(page) -> None:
    page, server, _errors = page
    before = "check this\nand that"
    _stage_document(page, SECRET_PASTE, before=before)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'failed'", timeout=10000)
    chip = _chips(page)[0]
    assert "credential" in chip["error"] and "nothing was saved" in chip["error"], "the door's reason must be on the chip"
    assert not chip["hasRetry"] and chip["hasRemove"], "a refusal is final: no retry, a remove"
    assert page.input_value("#input") == before, "the composer must return to its pre-paste bytes"
    assert server.uploads == [{"refused": True, "size": len(SECRET_PASTE.encode("utf-8"))}]
    # The strip stays visible until the user removes the chip; removing it changes nothing else.
    page.click("#attachStrip .att-chip .att-x")
    _wait_chips(page, "chips.length === 0")
    assert page.input_value("#input") == before


def test_removing_one_of_two_documents_removes_only_its_marker_and_restores_the_words(page) -> None:
    page, _server, _errors = page
    _stage_document(page, "plain words " * 500, before="prose here")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.evaluate("() => { const i = document.getElementById('input'); i.setSelectionRange(i.value.length, i.value.length); }")
    page.evaluate(
        """(text) => { const input = document.getElementById('input'); const dt = new DataTransfer(); dt.setData('text/plain', text);
            input.focus(); input.setSelectionRange(input.value.length, input.value.length);
            addPastedDocument(text, displayedChat, {atCaret: true}); }""",
        "2026-09-02T10:00:00 INFO another log\n" * 300,
    )
    _wait_chips(page, "chips.length === 2 && chips.every((c) => c.dataset.state === 'ready')")
    assert _marker_count(page) == 2
    first_id = _chips(page)[0]["id"]
    page.click("#attachStrip .att-chip:nth-child(1) .att-x")
    _wait_chips(page, "chips.length === 1")
    value = page.input_value("#input")
    assert _marker_count(page) == 1 and f"#{first_id}]" not in value and "prose here" in value
    page.click("#attachStrip .att-chip:nth-child(1) .att-x")
    _wait_chips(page, "chips.length === 0")
    assert page.input_value("#input") == "prose here", repr(page.input_value("#input"))


def test_a_hand_typed_marker_lookalike_is_never_deleted(page) -> None:
    page, _server, _errors = page
    typed = "look: [document: notes.txt (1 KB)] stays"
    _stage_document(page, "word " * 1200, before=typed)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.click("#attachStrip .att-chip .att-x")
    _wait_chips(page, "chips.length === 0")
    assert page.input_value("#input") == typed


def test_a_marker_only_message_is_not_a_message(page) -> None:
    page, server, _errors = page
    _stage_document(page, LONG_PASTE, before="")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    assert _marker_count(page) == 1
    page.click("#send")
    page.wait_for_timeout(300)
    assert server.chat_bodies == [] and "message" in page.inner_text("#nToast").lower()


def test_a_dropped_text_file_is_a_document_with_its_own_name_and_an_image_stays_an_attachment(page) -> None:
    page, server, errors = page
    page.evaluate(
        """() => {
            const dt = new DataTransfer();
            dt.items.add(new File([('config line\\n').repeat(400)], 'settings.yaml', { type: 'application/yaml' }));
            const footer = document.querySelector('footer');
            footer.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
        }"""
    )
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'", timeout=10000)
    upload = server.uploads[-1]
    assert upload["source"] == "drop" and upload["name"] == "settings.yaml", upload
    assert upload["data"] == ("config line\n" * 400).encode("utf-8")
    chip = _chips(page)[0]
    assert chip["document"] and chip["hasExpand"]
    assert "#" in page.input_value("#input") and "[document: " in page.input_value("#input")
    assert errors == []


def test_short_dropped_text_and_a_short_paste_are_ordinary(page) -> None:
    page, server, _errors = page
    prevented = page.evaluate(
        """() => { const dt = new DataTransfer(); dt.setData('text/plain', 'just a phrase');
            const ev = new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true });
            document.querySelector('footer').dispatchEvent(ev); return ev.defaultPrevented; }"""
    )
    assert prevented is False and _chips(page) == [] and server.uploads == []
    assert _paste(page, "hello") is False and LIMITS["document_threshold_chars"] == 4000
