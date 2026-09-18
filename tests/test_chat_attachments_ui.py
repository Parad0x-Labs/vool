"""The composer's attachment flow in a REAL browser, at desktop and narrow widths.

Two lanes. The hermetic lane routes every request through `page.route`, so the page's own JS is
driven end to end against controlled server answers: the Attach button, the native picker's
accept list, removable chips, limits shown up front, refusals rendered in place, a failed upload
retried without a duplicate message, chips restored after a reload, and two chats that cannot see
each other's files. The served lane starts the REAL application over HTTP (uvicorn on an
ephemeral loopback port) with a fake agent that records what the runtime handed it, so the whole
path -- picker, raw upload door, staging, turn binding, evidence hand-over, release, transcript
receipt -- is proven through the shipped UI rather than through a helper.

Every availability decision lives in `tests.served_browser.launch_chromium()` (fails under the
gate, skips outside it); there is deliberately no importorskip and no inline skip here.
"""

from __future__ import annotations

import functools
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
from tests.test_chat_attachments_authority import tiny_jpeg, tiny_png

HTML = render_vool_chat_html()

LIMITS = {
    "max_files_per_turn": 8,
    "max_bytes_per_file": 10 * 1024 * 1024,
    "max_bytes_per_turn": 25 * 1024 * 1024,
    "text_extensions": [".txt", ".md", ".py", ".json", ".csv"],
    "image_types": ["image/png", "image/jpeg", "image/gif", "image/webp"],
    "accept": ".txt,.md,.py,.json,.csv,image/png,image/jpeg,image/gif,image/webp",
}

NDJSON_ANSWER = "\n".join(
    [
        json.dumps({"model": "vool", "created_at": "2026-09-02T00:00:00Z", "message": {"role": "assistant", "content": "I read them."}, "done": False}),
        json.dumps({"model": "vool", "created_at": "2026-09-02T00:00:00Z", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}),
        "",
    ]
)


def _launch():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    return served_browser.launch_chromium()


class _FakeServer:
    """The controlled server behind `page.route`: uploads, chat sends and history, all recorded."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.chat_bodies: list[dict[str, Any]] = []
        self.removed: list[dict[str, Any]] = []
        self.fail_upload_once: set[str] = set()
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.staged: dict[str, list[dict[str, Any]]] = {}
        self.counter = 0
        self.dictations: list[dict[str, Any]] = []
        #: What the dictation door answers. The default is the honest one: a machine with no
        #: speech dependency shows the typed reason.
        self.dictation_response = (503, {"ok": False, "error": "speech_recognizer_unauthorized", "message": "This machine has not granted Speech Recognition access, so audio was not transcribed.", "remediation": "Enable Speech Recognition for this app in System Settings."})

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
            return
        if "/api/chat/attachments/limits" in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps(LIMITS))
            return
        if "/api/chat/dictation" in url:
            if request.method == "POST":
                headers = {k.lower(): v for k, v in request.headers.items()}
                self.dictations.append({
                    "session": headers.get("x-vool-session-id", ""),
                    "audio_type": headers.get("x-vool-audio-type", ""),
                    "size": len(request.post_data_buffer or b""),
                })
            status, body = self.dictation_response
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            return
        if "/api/chat/attachments/upload" in url:
            headers = {k.lower(): v for k, v in request.headers.items()}
            name = urllib.parse.unquote(headers.get("x-vool-attachment-name", ""))
            session = headers.get("x-vool-session-id", "")
            data = request.post_data_buffer or b""
            record = {"name": name, "type": headers.get("x-vool-attachment-type", ""), "session": session, "size": len(data), "content_type": headers.get("content-type", "")}
            self.uploads.append(record)
            if name in self.fail_upload_once:
                self.fail_upload_once.discard(name)
                route.fulfill(status=500, content_type="application/json", body=json.dumps({"ok": False, "error": "upload_failed", "message": "The upload did not complete. Try again."}))
                return
            if name.startswith("huge"):
                route.fulfill(status=413, content_type="application/json", body=json.dumps({"ok": False, "error": "too_large", "message": "huge.txt is over the 10 MB per-file limit."}))
                return
            if name.startswith("forged"):
                route.fulfill(status=422, content_type="application/json", body=json.dumps({"ok": False, "error": "content_mismatch", "message": "forged.png is not a PNG: the bytes do not match the extension."}))
                return
            self.counter += 1
            att = {
                "id": "att_" + f"{self.counter:032x}",
                "name": name,
                "kind": "image" if record["type"].startswith("image/") else "text",
                "media_type": record["type"] or "text/plain",
                "size_bytes": len(data),
                "sha256": "0" * 64,
                "state": "staged",
            }
            self.staged.setdefault(session, []).append(att)
            route.fulfill(status=201, content_type="application/json", body=json.dumps({"ok": True, "attachment": att, "limits": LIMITS}))
            return
        if "/api/chat/attachments/remove" in url:
            body = json.loads(request.post_data or "{}")
            self.removed.append(body)
            items = self.staged.get(body.get("session_id", ""), [])
            self.staged[body.get("session_id", "")] = [a for a in items if a["id"] != body.get("attachment_id")]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "removed": True}))
            return
        if "/api/chat/attachments/preview" in url:
            route.fulfill(status=200, content_type="image/png", body=tiny_png())
            return
        if "/api/chat/attachments?" in url:
            session = urllib.parse.unquote(url.split("session=", 1)[1].split("&", 1)[0])
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_id": session, "attachments": self.staged.get(session, [])}))
            return
        if url.endswith("/api/chat") and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            self.chat_bodies.append(body)
            for att_id in body.get("attachments") or []:
                items = self.staged.get(body.get("session_id", ""), [])
                self.staged[body.get("session_id", "")] = [a for a in items if a["id"] != att_id]
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
    ctx, browser = _launch()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


@pytest.fixture()
def page(browser):
    server = _FakeServer()
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", server.route)
    page.set_viewport_size({"width": 1280, "height": 860})
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(120)
    yield page, server, errors
    page.close()


def _text_file(name: str, text: str) -> dict[str, Any]:
    return {"name": name, "mimeType": "text/plain", "buffer": text.encode("utf-8")}


def _png_file(name: str = "shot.png") -> dict[str, Any]:
    return {"name": name, "mimeType": "image/png", "buffer": tiny_png()}


def _chips(page) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#attachStrip .att-chip')].map((c) => ({
            name: (c.querySelector('.att-name') || {}).textContent || '',
            state: c.dataset.state || '',
            error: (c.querySelector('.att-error') || {}).textContent || '',
            hasThumb: !!c.querySelector('img.att-thumb'),
            hasRemove: !!c.querySelector('.att-x'),
            hasRetry: !!c.querySelector('.att-retry'),
        }))"""
    )


def _wait_chips(page, predicate: str, timeout: int = 5000) -> None:
    page.wait_for_function(
        "(pred) => { const chips = [...document.querySelectorAll('#attachStrip .att-chip')]; return (new Function('chips', 'return ' + pred))(chips); }",
        arg=predicate,
        timeout=timeout,
    )


def _attach(page, files: list[dict[str, Any]]) -> None:
    page.set_input_files("#attachInput", files)


def test_the_composer_has_a_visible_attach_button_wired_to_a_native_picker(page) -> None:
    page, _server, errors = page
    btn = page.query_selector("#attachBtn")
    assert btn is not None, "no Attach button in the real composer"
    assert btn.is_visible()
    box = btn.bounding_box()
    footer = page.query_selector("footer").bounding_box()
    assert footer["y"] <= box["y"] <= footer["y"] + footer["height"], "the Attach button is not in the composer"
    accept = page.get_attribute("#attachInput", "accept") or ""
    assert page.get_attribute("#attachInput", "multiple") is not None
    for token in LIMITS["accept"].split(","):
        assert token in accept, f"{token} missing from the picker's accept list"
    with page.expect_file_chooser() as chooser_info:
        btn.click()
    chooser = chooser_info.value
    assert chooser.is_multiple()
    hint = page.inner_text("#attachHint")
    assert "8" in hint and "10 MB" in hint and "25 MB" in hint
    assert errors == []


def test_selected_files_appear_as_removable_chips_with_previews_before_send(page) -> None:
    page, server, errors = page
    _attach(page, [_text_file("notes.txt", "hello"), _png_file()])
    _wait_chips(page, "chips.length === 2 && chips.every((c) => c.dataset.state === 'ready')")
    chips = _chips(page)
    assert [c["name"] for c in chips] == ["notes.txt", "shot.png"]
    assert chips[1]["hasThumb"] and not chips[0]["hasThumb"]
    assert all(c["hasRemove"] for c in chips)
    assert [u["name"] for u in server.uploads] == ["notes.txt", "shot.png"]
    assert server.uploads[0]["content_type"].startswith("application/octet-stream")
    assert all(u["session"] == page.evaluate("displayedChat") for u in server.uploads)
    # Nothing was sent: chips are a draft, not a message.
    assert server.chat_bodies == []
    assert page.query_selector_all(".msg.user") == []
    page.click("#attachStrip .att-chip:nth-child(1) .att-x")
    _wait_chips(page, "chips.length === 1")
    assert [c["name"] for c in _chips(page)] == ["shot.png"]
    assert server.removed and server.removed[-1]["attachment_id"].startswith("att_")
    assert errors == []


def test_sending_binds_the_chips_to_one_turn_and_clears_the_draft(page) -> None:
    page, server, errors = page
    _attach(page, [_text_file("a.txt", "A"), _text_file("b.md", "B"), _png_file("c.png")])
    _wait_chips(page, "chips.length === 3 && chips.every((c) => c.dataset.state === 'ready')")
    page.fill("#input", "what do these say?")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    assert len(server.chat_bodies) == 1
    body = server.chat_bodies[0]
    assert body["turn_id"] and len(body["attachments"]) == 3
    assert all(a.startswith("att_") for a in body["attachments"])
    assert body["messages"][-1] == {"role": "user", "content": "what do these say?"}  # the model boundary stays role/content
    # The user bubble carries the chips; the draft strip is empty for the next message.
    bubble_chips = page.evaluate("() => [...document.querySelectorAll('.msg.user .msg-att .att-chip .att-name')].map((n) => n.textContent)")
    assert bubble_chips == ["a.txt", "b.md", "c.png"]
    assert _chips(page) == []
    assert page.evaluate("() => (chatState(displayedChat).history.slice(-2)[0].attachments || []).map((a) => a.name)") == ["a.txt", "b.md", "c.png"]
    assert errors == []


def test_text_only_sends_carry_no_attachment_field_at_all(page) -> None:
    page, server, errors = page
    page.fill("#input", "plain text")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    assert "attachments" not in server.chat_bodies[0]
    assert page.query_selector_all(".msg.user .msg-att") == []
    assert errors == []


def test_refusals_are_shown_on_the_chip_and_never_reach_a_send(page) -> None:
    page, server, errors = page
    _attach(page, [_text_file("huge.txt", "x"), _png_file("forged.png"), _text_file("fine.txt", "ok")])
    _wait_chips(page, "chips.length === 3 && chips.filter((c) => c.dataset.state === 'ready').length === 1")
    chips = {c["name"]: c for c in _chips(page)}
    assert chips["huge.txt"]["state"] == "failed" and "10 MB" in chips["huge.txt"]["error"]
    assert chips["forged.png"]["state"] == "failed" and "not a PNG" in chips["forged.png"]["error"]
    assert chips["fine.txt"]["state"] == "ready"
    page.fill("#input", "go")
    page.click("#send")
    page.wait_for_timeout(400)
    assert server.chat_bodies == [], "a send went out with refused attachments still on the strip"
    assert page.query_selector_all(".msg.user") == []
    toast = page.inner_text("#nToast") if page.query_selector("#nToast") else ""
    assert "attachment" in toast.lower()
    # Removing the refused chips unblocks the send with exactly the accepted file.
    page.click("#attachStrip .att-chip[data-state='failed'] .att-x")
    page.click("#attachStrip .att-chip[data-state='failed'] .att-x")
    _wait_chips(page, "chips.length === 1")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    assert len(server.chat_bodies) == 1 and len(server.chat_bodies[0]["attachments"]) == 1
    assert page.evaluate("() => document.querySelectorAll('.msg.user').length") == 1
    assert errors == []


def test_client_side_limits_refuse_a_ninth_file_before_any_upload(page) -> None:
    page, server, errors = page
    files = [_text_file(f"f{i}.txt", "x") for i in range(9)]
    _attach(page, files)
    _wait_chips(page, "chips.length === 8")
    page.wait_for_timeout(300)
    assert len(server.uploads) == 8
    assert "8" in (page.inner_text("#nToast") if page.query_selector("#nToast") else "")
    assert errors == []


def test_a_failed_upload_is_retried_in_place_without_duplicating_the_message(page) -> None:
    page, server, errors = page
    server.fail_upload_once.add("flaky.txt")
    _attach(page, [_text_file("flaky.txt", "retry me")])
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'failed'")
    chip = _chips(page)[0]
    assert chip["hasRetry"] and "did not complete" in chip["error"]
    page.fill("#input", "read it")
    page.click("#send")
    page.wait_for_timeout(300)
    assert server.chat_bodies == [] and page.query_selector_all(".msg.user") == []
    page.click("#attachStrip .att-chip .att-retry")
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    assert [u["name"] for u in server.uploads] == ["flaky.txt", "flaky.txt"]
    assert page.input_value("#input") == "read it", "the draft must survive a failed send attempt"
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    assert len(server.chat_bodies) == 1
    assert page.evaluate("() => document.querySelectorAll('.msg.user').length") == 1
    assert errors == []


def test_a_failed_send_keeps_the_same_turn_for_its_attachments_on_resend(page) -> None:
    page, server, errors = page
    _attach(page, [_text_file("keep.txt", "keep")])
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.route("**/api/chat", lambda route: route.abort("connectionfailed"))
    page.fill("#input", "first try")
    page.click("#send")
    page.wait_for_function("() => { const r = chatState(displayedChat).run; return r && r.ended; }", timeout=10000)
    first_turn = page.evaluate("() => chatState(displayedChat).run.turnId")
    attachments_after_failure = page.evaluate("() => (chatState(displayedChat).run.attachments || []).map((a) => a.id)")
    assert attachments_after_failure and first_turn
    page.unroute("**/api/chat")
    page.evaluate("() => resendLastTurn && resendLastTurn(displayedChat)")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length >= 1", timeout=10000)
    resend = server.chat_bodies[-1]
    assert resend["turn_id"] == first_turn and resend["attachments"] == attachments_after_failure
    assert page.evaluate("() => document.querySelectorAll('.msg.user').length") == 1
    assert errors == []


def test_chips_survive_a_reload_before_send_and_ride_the_transcript_after(page) -> None:
    page, server, errors = page
    chat_id = page.evaluate("displayedChat")
    _attach(page, [_text_file("draft.txt", "draft")])
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.reload()
    page.wait_for_timeout(400)
    assert page.evaluate("displayedChat") == chat_id
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    assert _chips(page)[0]["name"] == "draft.txt"
    server.history[chat_id] = [
        {"role": "user", "content": "what is in it?", "attachments": [{"id": "att_" + "9" * 32, "name": "sent.png", "kind": "image", "media_type": "image/png", "size_bytes": 12, "outcome": "sent"}]},
        {"role": "assistant", "content": "A picture.", "ts": "2026-09-02T00:00:00Z"},
    ]
    page.evaluate("() => { chatState(displayedChat).history.length = 0; }")
    page.reload()
    page.wait_for_timeout(500)
    bubble = page.evaluate("() => [...document.querySelectorAll('.msg.user .msg-att .att-chip')].map((c) => ({name: c.querySelector('.att-name').textContent, outcome: c.dataset.outcome || ''}))")
    assert bubble == [{"name": "sent.png", "outcome": "sent"}]
    assert errors == []


def test_two_chats_never_see_each_others_drafts_or_send_each_others_files(page) -> None:
    page, server, errors = page
    chat_a = page.evaluate("displayedChat")
    _attach(page, [_text_file("for-a.txt", "A")])
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.evaluate("() => newChat()")
    page.wait_for_timeout(150)
    chat_b = page.evaluate("displayedChat")
    assert chat_b != chat_a
    assert _chips(page) == [], "chat B shows chat A's draft"
    _attach(page, [_png_file("for-b.png")])
    _wait_chips(page, "chips.length === 1 && chips[0].dataset.state === 'ready'")
    page.fill("#input", "b question")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    page.evaluate("(id) => openSession(id)", chat_a)
    page.wait_for_timeout(200)
    _wait_chips(page, "chips.length === 1")
    assert _chips(page)[0]["name"] == "for-a.txt"
    page.fill("#input", "a question")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=10000)
    by_session = {b["session_id"]: b for b in server.chat_bodies}
    a_ids = {a["id"] for a in server.staged.get(chat_a, [])} | set(by_session[chat_a]["attachments"])
    b_ids = set(by_session[chat_b]["attachments"])
    assert by_session[chat_a]["attachments"] and by_session[chat_b]["attachments"]
    assert not (a_ids & b_ids)
    assert all(u["session"] in {chat_a, chat_b} for u in server.uploads)
    a_upload = next(u for u in server.uploads if u["name"] == "for-a.txt")
    b_upload = next(u for u in server.uploads if u["name"] == "for-b.png")
    assert a_upload["session"] == chat_a and b_upload["session"] == chat_b
    assert errors == []


@pytest.mark.parametrize("width,height", [(1280, 860), (520, 620), (360, 780)])
def test_the_strip_and_button_fit_at_desktop_and_narrow_widths(page, width: int, height: int) -> None:
    page, _server, errors = page
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(120)
    _attach(page, [_text_file("a-rather-long-file-name-for-a-narrow-composer.txt", "x"), _png_file("photo.png"), _text_file("third.md", "y")])
    _wait_chips(page, "chips.length === 3 && chips.every((c) => c.dataset.state === 'ready')")
    report = page.evaluate(
        """() => {
            const R = (b) => ({ l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top), b: Math.round(b.bottom), w: Math.round(b.width) });
            const de = document.documentElement;
            const footer = R(document.querySelector('footer').getBoundingClientRect());
            const btn = R(document.getElementById('attachBtn').getBoundingClientRect());
            const strip = R(document.getElementById('attachStrip').getBoundingClientRect());
            const chips = [...document.querySelectorAll('#attachStrip .att-chip')].map((c) => R(c.getBoundingClientRect()));
            const input = R(document.getElementById('input').getBoundingClientRect());
            return { overflowX: de.scrollWidth > de.clientWidth, vw: de.clientWidth, footer, btn, strip, chips, input,
                     btnVisible: document.getElementById('attachBtn').offsetParent !== null };
        }"""
    )
    assert report["overflowX"] is False, report
    assert report["btnVisible"] and report["btn"]["w"] > 0
    assert report["btn"]["l"] >= 0 and report["btn"]["r"] <= report["vw"]
    for chip in report["chips"]:
        assert chip["l"] >= report["footer"]["l"] - 1 and chip["r"] <= report["footer"]["r"] + 1, (chip, report["footer"])
        assert chip["w"] > 0
    assert report["strip"]["b"] <= report["input"]["t"] + 1, "the strip must sit above the composer row"
    assert report["input"]["w"] >= 120
    assert errors == []


# --- served lane: the real application over HTTP, the real door, the real authority ------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture()
def served(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    import uvicorn

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices, stream_agent_with_events
    from core.web.api.service import dispatch_post

    captured: list[dict[str, Any]] = []

    def stub(runtime, text, *, session_id=None, source_context=None, **_):
        captured.append({"text": text, "session_id": session_id, "source_context": dict(source_context or {})})
        # Production persists the turn from inside the agent (chat_surface); the stub does the same
        # so the reload path reads a transcript the real writer produced.
        from core.persistent_memory import append_conversation_event
        from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

        append_conversation_event(session_id=str(session_id), user_input=text, assistant_output="I looked at what you attached.", source_context=source_context)
        reset_admission()
        return admit_semantic_result({"response": "I looked at what you attached.", "success": True, "confidence": 0.9, "route_reason": "model_lane", "mode": "advice_only"})

    app = create_app(RuntimeServices(display_name="VOOL"))
    app.state.post_dispatcher = functools.partial(
        dispatch_post,
        run_agent_provider=stub,
        stream_agent_with_events_provider=functools.partial(stream_agent_with_events, run_agent_provider=stub),
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.05)
    else:
        raise AssertionError("served app never became healthy")
    try:
        yield f"http://127.0.0.1:{port}", captured
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        runtime_paths.configure_runtime_home(None)


def test_served_end_to_end_the_real_door_stages_binds_hands_over_and_releases(browser, served) -> None:
    base, captured = served
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1280, "height": 860})
    page.goto(f"{base}/chat", wait_until="networkidle")
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(150)
    chat_id = page.evaluate("displayedChat")
    _attach(page, [_text_file("facts.txt", "the capital of the fixture is Testville\n"), _png_file("cam.png")])
    _wait_chips(page, "chips.length === 2 && chips.every((c) => c.dataset.state === 'ready')", timeout=10000)
    from core import chat_attachments

    staged_before = sorted(p.name for p in chat_attachments.stage_dir().glob("*.bin"))
    assert len(staged_before) == 2
    page.fill("#input", "what is the capital?")
    page.click("#send")
    page.wait_for_function("() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1", timeout=30000)
    assert len(captured) == 1
    context = captured[0]["source_context"]
    evidence = context["external_evidence"]
    assert [e["kind"] for e in evidence] == ["text", "image"]
    assert evidence[0]["text"] == "the capital of the fixture is Testville\n"
    assert all(e["origin"] == "chat_attachment" for e in evidence)
    assert str(chat_attachments.stage_dir()) not in json.dumps(context, default=str)
    # Released after the turn: no bytes remain, the receipt and the transcript both name the files.
    page.wait_for_timeout(500)
    assert sorted(p.name for p in chat_attachments.stage_dir().glob("*.bin")) == []
    with urllib.request.urlopen(f"{base}/api/chat/history?session={urllib.parse.quote(chat_id)}", timeout=5) as response:
        history = json.loads(response.read())["messages"]
    user_rows = [m for m in history if m["role"] == "user"]
    assert [a["name"] for a in user_rows[-1]["attachments"]] == ["facts.txt", "cam.png"]
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(500)
    bubble = page.evaluate("() => [...document.querySelectorAll('.msg.user .msg-att .att-chip .att-name')].map((n) => n.textContent)")
    assert bubble == ["facts.txt", "cam.png"]
    # Activity names what happened, without contents.
    with urllib.request.urlopen(f"{base}/api/runtime/events?session={urllib.parse.quote(chat_id)}&limit=200", timeout=5) as response:
        events = json.loads(response.read())["events"]
    kinds = {e.get("event_type") for e in events}
    assert {"attachment_staged", "attachment_bound", "attachment_released"} <= kinds, kinds
    assert "Testville" not in json.dumps(events)
    assert errors == []
    page.close()


def test_served_forged_and_oversized_uploads_are_refused_by_the_real_door(browser, served) -> None:
    base, _captured = served
    page = browser.new_page()
    page.set_viewport_size({"width": 1100, "height": 800})
    page.goto(f"{base}/chat", wait_until="networkidle")
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(150)
    from core import chat_attachments

    _attach(
        page,
        [
            {"name": "forged.png", "mimeType": "image/png", "buffer": tiny_jpeg()},
            {"name": "huge.txt", "mimeType": "text/plain", "buffer": b"x" * (chat_attachments.MAX_BYTES_PER_FILE + 1)},
            _text_file("ok.txt", "fine"),
        ],
    )
    _wait_chips(page, "chips.length === 3 && chips.filter((c) => c.dataset.state !== 'uploading').length === 3", timeout=20000)
    chips = {c["name"]: c for c in _chips(page)}
    assert chips["forged.png"]["state"] == "failed" and "png" in chips["forged.png"]["error"].lower()
    assert chips["huge.txt"]["state"] == "failed" and "MB" in chips["huge.txt"]["error"]
    assert chips["ok.txt"]["state"] == "ready"
    assert sorted(p.name for p in chat_attachments.stage_dir().glob("*.bin")).__len__() == 1
    page.close()


_FAKE_MIC_INIT = """
(() => {
  const fakeStream = { getTracks: () => [{ stop() {} }] };
  try {
    Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: () => Promise.resolve(fakeStream) }, configurable: true });
  } catch (e) {
    navigator.mediaDevices = { getUserMedia: () => Promise.resolve(fakeStream) };
  }
  class FakeMediaRecorder {
    constructor(stream, opts) { this.mimeType = (opts && opts.mimeType) || 'audio/mp4'; this.state = 'inactive'; this.ondataavailable = null; this.onstop = null; }
    static isTypeSupported() { return true; }
    start() {
      this.state = 'recording';
      setTimeout(() => {
        if (this.ondataavailable) this.ondataavailable({ data: { size: 2048 } });
        if (this.onstop) this.onstop();
      }, 120);
    }
    stop() {}
  }
  window.MediaRecorder = FakeMediaRecorder;
})();
"""


def test_dictation_places_the_transcript_in_the_composer_as_draft_text(page) -> None:
    """The mic writes DRAFT text into the input; it never sends a message by itself."""
    page, server, errors = page
    server.dictation_response = (200, {"ok": True, "text": "the meeting starts at noon", "complete": True, "engine": "stub", "on_device": True})
    page.add_init_script(_FAKE_MIC_INIT)
    page.reload()
    page.wait_for_timeout(200)
    # The mic button ships disabled in this beta (non-reactive by owner decision); the wiring
    # beneath it must survive intact for the re-enable, so this drives the handler directly.
    page.locator("#dictateBtn").dispatch_event("click")
    page.wait_for_function("() => document.getElementById('input').value.includes('the meeting starts at noon')", timeout=8000)
    assert len(server.dictations) == 1
    assert server.dictations[0]["session"], "the recording must name the chat it composes into"
    assert server.dictations[0]["size"] > 0
    # A draft is not a send: no chat request may have carried the transcript on its own.
    assert server.chat_bodies == []
    assert errors == []


def test_dictation_without_the_speech_dependency_shows_the_typed_reason(page) -> None:
    """A machine with no recogniser says so, with the remediation — never a fake attempt."""
    page, server, errors = page
    server.dictation_response = (
        503,
        {"ok": False, "error": "speech_recognizer_unauthorized", "message": "This machine has not granted Speech Recognition access, so audio was not transcribed.", "remediation": "Enable Speech Recognition for this app in System Settings."},
    )
    page.add_init_script(_FAKE_MIC_INIT)
    page.reload()
    page.wait_for_timeout(200)
    # Disabled-button note as above: drive the kept handler wiring directly.
    page.locator("#dictateBtn").dispatch_event("click")
    page.wait_for_function("() => !document.getElementById('dictationNote').hidden", timeout=8000)
    note = page.evaluate("() => document.getElementById('dictationNote').textContent")
    assert "Speech Recognition" in note and "System Settings" in note
    # The composer input is untouched: no text was invented, nothing was sent.
    assert page.evaluate("() => document.getElementById('input').value") == ""
    assert server.chat_bodies == []
    assert errors == []
