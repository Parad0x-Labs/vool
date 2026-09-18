"""Plain clipboard text and explicit documents in the real composer.

The browser lane proves that even long clipboard text remains an editable message and sends
without an extra document instruction. Explicit documents retain exact bytes, previews, markers,
chat ownership, refusal/retry behavior, and a user-controlled conversion back to message text.
The served document lane exercises the actual upload, retention, restart and deletion doors
against a stub agent. No live model requests are made.
"""

from __future__ import annotations

import functools
import hashlib
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

LIMITS = {
    "max_files_per_turn": 8,
    "max_bytes_per_file": 64 * 1024,          # small on purpose: the oversized paste stays fast
    "max_bytes_per_turn": 25 * 1024 * 1024,
    "document_threshold_chars": 4000,
    "max_text_chars_per_file": 120_000,
    "max_carried_text_chars_per_turn": 60_000,
    "text_extensions": [".txt", ".md", ".py", ".json", ".csv"],
    "image_types": ["image/png", "image/jpeg", "image/gif", "image/webp"],
    "accept": ".txt,.md,.py,.json,.csv,image/png,image/jpeg,image/gif,image/webp",
}

LINE = "2026-09-02T10:00:00Z INFO service ready ✅ 中文\ttab\n"
LONG_PASTE = (LINE * 120) + "tail without newline"     # ~5,600 chars, past the 4,000 threshold
SHORT_PASTE = "hello world"

NDJSON_ANSWER = "\n".join(
    [
        json.dumps({"model": "vool", "created_at": "2026-09-02T00:00:00Z", "message": {"role": "assistant", "content": "I read the document."}, "done": False}),
        json.dumps({"model": "vool", "created_at": "2026-09-02T00:00:00Z", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}),
        "",
    ]
)


class _DocServer:
    """The controlled server behind `page.route`: the document door, previews, chat and history."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.removed: list[dict[str, Any]] = []
        self.abort_uploads = False
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.staged: dict[str, list[dict[str, Any]]] = {}
        self.bytes: dict[str, bytes] = {}
        self.counter = 0

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
            return
        if "/api/chat/attachments/limits" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(LIMITS))
            return
        if "/api/chat/attachments/upload" in url:
            if self.abort_uploads:
                route.abort("connectionfailed")
                return
            headers = {k.lower(): v for k, v in request.headers.items()}
            data = request.post_data_buffer or b""
            session = headers.get("x-vool-session-id", "")
            record = {
                "session": session,
                "source": headers.get("x-vool-attachment-source", ""),
                "name": urllib.parse.unquote(headers.get("x-vool-attachment-name", "")),
                "type": headers.get("x-vool-attachment-type", ""),
                "content_type": headers.get("content-type", ""),
                "data": data,
            }
            self.uploads.append(record)
            if len(data) > LIMITS["max_bytes_per_file"]:
                route.fulfill(status=413, content_type="application/json", body=json.dumps({"ok": False, "error": "too_large", "message": "pasted text is over the 64 KB per-file limit."}))
                return
            self.counter += 1
            text = data.decode("utf-8")
            att = {
                "id": "att_" + f"{self.counter:032x}",
                "name": f"pasted-{self.counter}.txt",
                "kind": "text",
                "media_type": "text/plain",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "state": "staged",
                "document": True,
                "chars": len(text),
                "lines": text.count("\n") + 1,
            }
            self.bytes[att["id"]] = data
            self.staged.setdefault(session, []).append(att)
            route.fulfill(status=201, content_type="application/json", body=json.dumps({"ok": True, "attachment": att, "limits": LIMITS}))
            return
        if "/api/chat/attachments/remove" in url:
            body = json.loads(request.post_data or "{}")
            self.removed.append(body)
            session = body.get("session_id", "")
            self.staged[session] = [a for a in self.staged.get(session, []) if a["id"] != body.get("attachment_id")]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "removed": True}))
            return
        if "/api/chat/attachments/preview" in url:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            att_id = (query.get("id") or [""])[0]
            data = self.bytes.get(att_id)
            if data is None:
                route.fulfill(status=410, content_type="application/json", body=json.dumps({"error": "erased"}))
                return
            route.fulfill(status=200, content_type="text/plain; charset=utf-8", body=data)
            return
        if "/api/chat/attachments/documents" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"documents": []}))
            return
        if "/api/chat/attachments?" in url:
            session = urllib.parse.unquote(url.split("session=", 1)[1].split("&", 1)[0])
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_id": session, "attachments": self.staged.get(session, [])}))
            return
        if url.endswith("/api/chat") and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            self.chat_bodies.append(body)
            session = body.get("session_id", "")
            for att_id in body.get("attachments") or []:
                self.staged[session] = [a for a in self.staged.get(session, []) if a["id"] != att_id]
            route.fulfill(status=200, content_type="application/x-ndjson", body=NDJSON_ANSWER)
            return
        if "/api/chat/history" in url:
            session = urllib.parse.unquote(url.split("session=", 1)[1].split("&", 1)[0])
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_id": session, "messages": self.history.get(session, [])}))
            return
        if "/api/cloud/model" in url and "models" not in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "model": "", "cost_state": "free"}))
            return
        route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


def _open(browser, *, viewport: dict[str, int] | None = None, reduced_motion: str | None = None):
    server = _DocServer()
    kwargs: dict[str, Any] = {}
    if reduced_motion:
        kwargs["reduced_motion"] = reduced_motion
    context = browser.new_context(**kwargs)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", server.route)
    page.set_viewport_size(viewport or {"width": 1280, "height": 860})
    page.goto("http://localhost/chat")
    page.wait_for_timeout(250)
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(120)
    return context, page, server, errors


@pytest.fixture()
def page(browser):
    context, page, server, errors = _open(browser)
    yield page, server, errors
    context.close()


def _paste(page, text: str, *, before: str = "before ") -> bool:
    """Fire a paste event with text/plain on the composer, caret after `before`.

    Returns whether the page's handler claimed the paste (`preventDefault`). A synthetic event
    never performs the browser's own insertion, so for a short paste the proof is exactly that
    the handler left the default alone; the served lane pastes through the real clipboard.
    """
    return page.evaluate(
        """([text, before]) => {
            const input = document.getElementById('input');
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            input.value = before; input.focus();
            input.setSelectionRange(before.length, before.length);
            const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
            input.dispatchEvent(ev);
            return ev.defaultPrevented;
        }""",
        [text, before],
    )


def _stage_document(page, text: str, *, before: str = "before ") -> bool:
    """Explicit document staging; plain clipboard text now remains a message."""
    page.evaluate("""([text, before]) => {
        inputEl.value = before; inputEl.focus();
        inputEl.setSelectionRange(before.length, before.length);
        addPastedDocument(text, displayedChat, {atCaret: true});
    }""", [text, before])
    return True


def _clipboard_paste(page, text: str) -> None:
    """A TRUSTED paste: the text goes onto the real clipboard and the keyboard shortcut pastes it."""
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.focus("#input")
    page.evaluate("(t) => navigator.clipboard.writeText(t)", text)
    page.keyboard.press("ControlOrMeta+V")


def _chips(page, scope: str = "#attachStrip") -> list[dict[str, Any]]:
    return page.evaluate(
        """(scope) => [...document.querySelectorAll(scope + ' .att-chip')].map((c) => ({
            id: c.dataset.id || '',
            name: (c.querySelector('.att-name') || {}).textContent || '',
            meta: (c.querySelector('.att-meta') || {}).textContent || '',
            state: c.dataset.state || '',
            document: c.dataset.document === '1',
            error: (c.querySelector('.att-error') || {}).textContent || '',
            hasExpand: !!c.querySelector('.att-expand'),
            hasRemove: !!c.querySelector('.att-x'),
            hasRetry: !!c.querySelector('.att-retry'),
            previewShown: !!(c.querySelector('.att-preview') && !c.querySelector('.att-preview').hidden),
            previewText: (c.querySelector('.att-preview') || {}).textContent || '',
        }))""",
        scope,
    )


def _wait_chips(page, predicate: str, scope: str = "#attachStrip", timeout: int = 5000) -> None:
    page.wait_for_function(
        "([scope, pred]) => { const chips = [...document.querySelectorAll(scope + ' .att-chip')]; return (new Function('chips', 'return ' + pred))(chips); }",
        arg=[scope, predicate],
        timeout=timeout,
    )


# --------------------------------------------------------------------------- hermetic lane


def test_plain_pastes_stay_text_and_explicit_documents_keep_exact_bytes(page) -> None:
    page, server, errors = page
    assert _paste(page, SHORT_PASTE) is False, "a short paste was intercepted"
    page.wait_for_timeout(150)
    assert _chips(page) == [] and server.uploads == []
    assert _paste(page, LONG_PASTE) is False
    assert _chips(page) == [] and server.uploads == []
    _stage_document(page, LONG_PASTE)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    chip = _chips(page)[0]
    assert chip["document"] and chip["name"] == "pasted-1.txt" and chip["hasExpand"] and chip["hasRemove"]
    assert "text/plain" in chip["meta"] and "KB" in chip["meta"] and "121 lines" in chip["meta"], chip["meta"]
    value = page.input_value("#input")
    assert value.startswith("before "), "the paste changed the user's own words"
    assert value.count("[document: pasted-1.txt (") == 1 and f"#{chip['id']}]" in value, "the message carries exactly one marker with the chip's durable id"
    assert LINE not in value, "the pasted text entered the composer"
    assert len(server.uploads) == 1, "one paste, one upload"
    upload = server.uploads[0]
    assert upload["source"] == "paste" and upload["content_type"].startswith("application/octet-stream")
    assert upload["data"] == LONG_PASTE.encode("utf-8"), "the bytes at the door are not the pasted bytes"
    assert errors == []


def test_the_chip_preview_expands_to_the_exact_text_with_whitespace_kept_and_collapses(page) -> None:
    page, _server, _errors = page
    _stage_document(page, LONG_PASTE)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.click("#attachStrip .att-chip .att-expand")
    page.wait_for_function("() => { const p = document.querySelector('#attachStrip .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
    chip = _chips(page)[0]
    assert chip["previewShown"]
    assert chip["previewText"].startswith(LINE * 3), "the preview is not the exact head of the text"
    assert "\t" in chip["previewText"] and "✅ 中文" in chip["previewText"]
    white_space = page.evaluate("() => getComputedStyle(document.querySelector('#attachStrip .att-preview')).whiteSpace")
    assert white_space in {"pre", "pre-wrap"}, white_space
    assert page.get_attribute("#attachStrip .att-chip .att-expand", "aria-expanded") == "true"
    page.click("#attachStrip .att-chip .att-expand")
    page.wait_for_timeout(100)
    assert not _chips(page)[0]["previewShown"]
    assert page.get_attribute("#attachStrip .att-chip .att-expand", "aria-expanded") == "false"


def test_sending_carries_the_document_id_and_the_bubble_chip_previews_from_the_server(page) -> None:
    page, server, errors = page
    chat_id = page.evaluate("displayedChat")
    _stage_document(page, LONG_PASTE, before="")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.click("#send")
    page.wait_for_timeout(300)
    assert server.chat_bodies == [], "a document-only send went out without a message saying what to do"
    assert "message" in page.inner_text("#nToast").lower()
    # The user types their words after the marker (typing, not a fill that would replace it).
    page.focus("#input")
    page.keyboard.press("End")
    page.keyboard.type("summarise this log")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=15000)
    assert len(server.chat_bodies) == 1
    body = server.chat_bodies[0]
    assert body["attachments"] == ["att_" + f"{1:032x}"] and body["session_id"] == chat_id
    content = body["messages"][-1]["content"]
    assert "summarise this log" in content, "the user's own words must ride the message"
    assert content.count("[document: pasted-1.txt (") == 1 and "#att_" in content, "the message carries the document's marker with its durable id"
    assert LONG_PASTE[:200] not in json.dumps(body), "the document's text leaked into the message"
    assert _chips(page) == []
    bubble = _chips(page, ".msg.user .msg-att")
    assert len(bubble) == 1 and bubble[0]["document"] and bubble[0]["name"] == "pasted-1.txt" and bubble[0]["hasExpand"]
    assert not bubble[0]["hasRemove"]
    page.click(".msg.user .msg-att .att-chip .att-expand")
    page.wait_for_function("() => { const p = document.querySelector('.msg.user .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
    assert _chips(page, ".msg.user .msg-att")[0]["previewText"].startswith(LINE * 3)
    assert errors == []


def test_a_reload_restores_the_document_chip_from_the_transcript_receipt(page) -> None:
    page, server, errors = page
    chat_id = page.evaluate("displayedChat")
    data = LONG_PASTE.encode("utf-8")
    server.bytes["att_" + "7" * 32] = data
    server.history[chat_id] = [
        {
            "role": "user",
            "content": "summarise this log",
            "attachments": [
                {"id": "att_" + "7" * 32, "name": "pasted-1.txt", "kind": "text", "media_type": "text/plain", "size_bytes": len(data), "outcome": "read", "document": True, "chars": len(LONG_PASTE), "lines": 121, "sha256": hashlib.sha256(data).hexdigest()},
            ],
        },
        {"role": "assistant", "content": "It is a service log."},
    ]
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.msg.user .msg-att .att-chip').length === 1", timeout=10000)
    bubble = _chips(page, ".msg.user .msg-att")[0]
    assert bubble["document"] and bubble["name"] == "pasted-1.txt" and "read" in bubble["meta"]
    page.click(".msg.user .msg-att .att-chip .att-expand")
    page.wait_for_function("() => { const p = document.querySelector('.msg.user .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
    assert _chips(page, ".msg.user .msg-att")[0]["previewText"].startswith(LINE * 3)
    # Erased on the server: the chip keeps its identity and says so instead of showing nothing.
    del server.bytes["att_" + "7" * 32]
    page.reload()
    page.wait_for_function("() => document.querySelectorAll('.msg.user .msg-att .att-chip').length === 1", timeout=10000)
    page.click(".msg.user .msg-att .att-chip .att-expand")
    page.wait_for_function("() => { const p = document.querySelector('.msg.user .att-preview'); return p && !p.hidden && p.textContent.length > 0; }")
    assert "erased" in _chips(page, ".msg.user .msg-att")[0]["previewText"].lower()
    assert errors == []


def test_invalid_and_oversized_pastes_fail_visibly_without_leaving_the_machine(page) -> None:
    page, server, _errors = page
    _stage_document(page, "x" * 4100 + "\ud800")   # a lone surrogate: not representable as UTF-8 bytes
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'failed'")
    chip = _chips(page)[0]
    assert chip["document"] and "invalid" in chip["error"].lower() and not chip["hasRetry"] and chip["hasRemove"]
    assert server.uploads == [], "malformed text was uploaded"
    assert "xxxx" not in page.input_value("#input")
    _stage_document(page, "y" * (LIMITS["max_bytes_per_file"] + 10))
    _wait_chips(page, "chips.length === 2 && chips[1].dataset.state === 'failed'")
    chip = _chips(page)[1]
    assert "64 KB" in chip["error"] and not chip["hasRetry"]
    assert server.uploads == [], "an oversized paste left the machine"
    assert "yyyy" not in page.input_value("#input")
    page.fill("#input", "go")
    page.click("#send")
    page.wait_for_timeout(200)
    assert server.chat_bodies == [] and "failed" in page.inner_text("#nToast").lower()
    page.click("#attachStrip .att-chip:nth-child(1) .att-x")
    page.click("#attachStrip .att-chip:nth-child(1) .att-x")
    _wait_chips(page, "chips.length === 0")


def test_an_offline_paste_is_retried_in_place_and_blocks_the_send_until_it_lands(page) -> None:
    page, server, _errors = page
    server.abort_uploads = True
    _stage_document(page, LONG_PASTE)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'failed'", timeout=10000)
    chip = _chips(page)[0]
    assert chip["hasRetry"] and "connection" in chip["error"].lower()
    page.fill("#input", "summarise")
    page.click("#send")
    page.wait_for_timeout(200)
    assert server.chat_bodies == []
    server.abort_uploads = False
    page.click("#attachStrip .att-chip .att-retry")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'", timeout=10000)
    assert server.uploads[-1]["data"] == LONG_PASTE.encode("utf-8"), "the retry did not resend the exact bytes"
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=15000)
    assert len(server.chat_bodies) == 1 and len(server.chat_bodies[0]["attachments"]) == 1


def test_two_chats_keep_their_documents_apart(page) -> None:
    page, server, _errors = page
    first = page.evaluate("displayedChat")
    _stage_document(page, LONG_PASTE)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.evaluate("() => newChat()")
    page.wait_for_timeout(150)
    second = page.evaluate("displayedChat")
    assert second != first and _chips(page) == []
    _stage_document(page, ("second chat line\n" * 400))
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.fill("#input", "what is this")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=15000)
    body = server.chat_bodies[-1]
    assert body["session_id"] == second and body["attachments"] == ["att_" + f"{2:032x}"]
    page.evaluate("(id) => openSession(id)", first)
    page.wait_for_timeout(200)
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.id === 'att_' + '1'.padStart(32, '0')")


def test_the_same_contract_at_a_phone_width(browser) -> None:
    context, page, server, errors = _open(browser, viewport={"width": 375, "height": 812})
    try:
        _stage_document(page, LONG_PASTE)
        _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
        page.click("#attachStrip .att-chip .att-expand")
        page.wait_for_function("() => { const p = document.querySelector('#attachStrip .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
        geometry = page.evaluate(
            """() => { const de = document.documentElement; const R = (b) => ({ l: Math.round(b.left), r: Math.round(b.right), w: Math.round(b.width), h: Math.round(b.height) });
                 const pre = document.querySelector('#attachStrip .att-preview');
                 return { overflowX: de.scrollWidth > de.clientWidth, vw: de.clientWidth, vh: de.clientHeight,
                          chip: R(document.querySelector('#attachStrip .att-chip').getBoundingClientRect()),
                          preview: R(pre.getBoundingClientRect()), previewOverflowY: getComputedStyle(pre).overflowY,
                          send: R(document.getElementById('send').getBoundingClientRect()) }; }"""
        )
        assert not geometry["overflowX"], geometry
        assert geometry["chip"]["r"] <= geometry["vw"] and geometry["preview"]["r"] <= geometry["vw"], geometry
        assert geometry["preview"]["h"] <= geometry["vh"] * 0.5, geometry
        assert geometry["previewOverflowY"] in {"auto", "scroll"}
        assert geometry["send"]["l"] >= 0 and geometry["send"]["r"] <= geometry["vw"], geometry
        page.fill("#input", "summarise")
        page.click("#send")
        page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=15000)
        assert server.chat_bodies[-1]["attachments"] == ["att_" + f"{1:032x}"]
        after = page.evaluate("() => { const de = document.documentElement; return de.scrollWidth > de.clientWidth; }")
        assert not after, "the sent bubble's chip overflowed the phone width"
        assert errors == []
    finally:
        context.close()


def test_the_preview_toggle_works_without_motion_when_the_user_asked_for_none(browser) -> None:
    context, page, _server, errors = _open(browser, reduced_motion="reduce")
    try:
        assert page.evaluate("() => window.matchMedia('(prefers-reduced-motion: reduce)').matches")
        _stage_document(page, LONG_PASTE)
        _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
        page.click("#attachStrip .att-chip .att-expand")
        page.wait_for_function("() => { const p = document.querySelector('#attachStrip .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
        motion = page.evaluate(
            """() => { const p = document.querySelector('#attachStrip .att-preview'); const s = getComputedStyle(p);
                 return { transition: s.transitionDuration, animation: s.animationDuration }; }"""
        )
        # Chromium reports the page's reduced-motion rule (.01ms) as "1e-05s": anything under a
        # millisecond is no motion.
        assert float(motion["transition"].rstrip("s")) <= 0.001 and float(motion["animation"].rstrip("s")) <= 0.001, motion
        page.click("#attachStrip .att-chip .att-expand")
        page.wait_for_timeout(50)
        assert not _chips(page)[0]["previewShown"]
        assert errors == []
    finally:
        context.close()


# --------------------------------------------------------------------------- served lane


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Served:
    """The real application over HTTP on one home; can be stopped and started again on that home."""

    def __init__(self) -> None:
        self.captured: list[dict[str, Any]] = []
        self.port = _free_port()
        self.server: Any = None
        self.thread: threading.Thread | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        import uvicorn

        from apps.vool_api_server import create_app
        from core.web.api.runtime import RuntimeServices, stream_agent_with_events
        from core.web.api.service import dispatch_post

        captured = self.captured

        def stub(runtime, text, *, session_id=None, source_context=None, **_):
            captured.append({"text": text, "session_id": session_id, "source_context": dict(source_context or {})})
            from core.persistent_memory import append_conversation_event
            from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

            append_conversation_event(session_id=str(session_id), user_input=text, assistant_output="I read the pasted document.", source_context=source_context)
            reset_admission()
            return admit_semantic_result({"response": "I read the pasted document.", "success": True, "confidence": 0.9, "route_reason": "model_lane", "mode": "advice_only"})

        app = create_app(RuntimeServices(display_name="VOOL"))
        app.state.post_dispatcher = functools.partial(
            dispatch_post,
            run_agent_provider=stub,
            stream_agent_with_events_provider=functools.partial(stream_agent_with_events, run_agent_provider=stub),
        )
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, access_log=False, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base}/healthz", timeout=0.5) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(0.05)
        raise AssertionError("served app never became healthy")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None


@pytest.fixture()
def served(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    app = _Served()
    app.start()
    try:
        yield app
    finally:
        app.stop()
        runtime_paths.configure_runtime_home(None)


def _get_json(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read())


def test_served_end_to_end_paste_door_retained_bytes_receipt_restart_retry_and_deletion(browser, served) -> None:
    from core import chat_attachments

    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 860})
    page.goto(f"{served.base}/chat", wait_until="networkidle")
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(150)
    chat_id = page.evaluate("displayedChat")
    _stage_document(page, LONG_PASTE, before="")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'", timeout=10000)
    chip = _chips(page)[0]
    doc_id = chip["id"]
    assert chip["document"] and chip["name"] == "pasted-1.log", "the type authority names timestamped lines a log"
    on_disk = (chat_attachments.stage_dir() / f"{doc_id}.bin").read_bytes()
    assert on_disk == LONG_PASTE.encode("utf-8"), "the real door did not keep the pasted bytes exactly"
    page.fill("#input", "summarise this log")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=30000)
    assert len(served.captured) == 1
    context = served.captured[0]["source_context"]
    evidence = context["external_evidence"]
    assert len(evidence) == 1 and evidence[0]["text"] == LONG_PASTE and evidence[0]["document"] is True
    assert context["attachment_turn_id"]
    turn_id = context["attachment_turn_id"]
    page.wait_for_timeout(500)
    assert (chat_attachments.stage_dir() / f"{doc_id}.bin").read_bytes() == LONG_PASTE.encode("utf-8"), "the bytes went with the turn"
    history = _get_json(served.base, f"/api/chat/history?session={urllib.parse.quote(chat_id)}")["messages"]
    receipt = [m for m in history if m["role"] == "user"][-1]["attachments"]
    assert receipt[0]["id"] == doc_id and receipt[0]["document"] is True and receipt[0]["sha256"] == hashlib.sha256(on_disk).hexdigest()
    # Retry: the last turn sent again (the page's own resend path, same turn id) re-binds the
    # released document and the runtime is handed the same bytes; nothing is bound twice.
    page.evaluate("() => resendLastTurn(displayedChat)")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1 && document.querySelectorAll('.msg.user').length === 1", timeout=30000)
    page.wait_for_timeout(500)
    assert len(served.captured) == 2, "the retry never reached the runtime"
    retried = served.captured[1]["source_context"]
    assert retried["attachment_turn_id"] == turn_id, "a retry must be the same turn"
    assert retried["external_evidence"][0]["text"] == LONG_PASTE and retried["external_evidence"][0]["attachment_id"] == doc_id
    assert (chat_attachments.stage_dir() / f"{doc_id}.bin").read_bytes() == LONG_PASTE.encode("utf-8")
    # Reload: the bubble chip comes from the transcript and previews the retained bytes.
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('.msg.user .msg-att .att-chip').length >= 1", timeout=10000)
    page.click(".msg.user .msg-att .att-chip .att-expand")
    page.wait_for_function("() => { const p = document.querySelector('.msg.user .att-preview'); return p && !p.hidden && p.textContent.length > 100; }")
    assert _chips(page, ".msg.user .msg-att")[0]["previewText"].startswith(LINE * 3)
    # Restart the server on the same home (resume): identity, bytes and transcript survive the process.
    served.stop()
    served.start()
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => document.querySelectorAll('.msg.user .msg-att .att-chip').length >= 1", timeout=10000)
    with urllib.request.urlopen(f"{served.base}/api/chat/attachments/preview?session={urllib.parse.quote(chat_id)}&id={doc_id}", timeout=5) as response:
        assert response.read() == LONG_PASTE.encode("utf-8")
    listed = _get_json(served.base, f"/api/chat/attachments/documents?session={urllib.parse.quote(chat_id)}")["documents"]
    assert [d["id"] for d in listed] == [doc_id] and listed[0]["turn_id"] == turn_id
    # Deleting the chat erases the bytes and the manifest; the identity was already in the receipt.
    request = urllib.request.Request(
        f"{served.base}/api/chat/session",
        data=json.dumps({"session_id": chat_id, "delete": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert json.loads(response.read())["deleted"] is True
    assert not (chat_attachments.stage_dir() / f"{doc_id}.bin").exists()
    assert chat_attachments.get_record(doc_id) is None
    events = _get_json(served.base, f"/api/runtime/events?session={urllib.parse.quote(chat_id)}&limit=200")["events"]
    assert "service ready" not in json.dumps(events), "document contents leaked into Activity"
    assert errors == []
    page.close()


def test_long_clipboard_prompt_can_send_without_a_document_instruction(page) -> None:
    page, server, errors = page
    page.fill("#input", "")
    _clipboard_paste(page, LONG_PASTE)
    page.wait_for_function("(text) => inputEl.value === text", arg=LONG_PASTE)
    assert _chips(page) == [] and server.uploads == []
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1")
    assert server.chat_bodies[-1]["messages"][-1]["content"] == LONG_PASTE
    assert errors == []


def test_document_can_be_explicitly_moved_back_into_editable_message(page) -> None:
    page, server, errors = page
    _stage_document(page, LONG_PASTE, before="")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.click("#attachStrip .att-as-message")
    page.wait_for_function("(text) => inputEl.value === text", arg=LONG_PASTE)
    assert _chips(page) == [] and server.chat_bodies == []
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1")
    assert server.chat_bodies[-1]["messages"][-1]["content"] == LONG_PASTE
    assert errors == []
