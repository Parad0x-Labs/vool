"""A turn the server refuses shows the server's reason, never "(no response)".

Sending in an archived chat is answered 409 {"error": "chat is not active; restore it before
sending messages", "lifecycle_state": "archived"} (measured on the live server). The page handled one
409 shape only (the council pin) and read every other refusal as an empty stream, so the bubble said
"(no response)" with no reason. Any non-2xx answer is now the turn's answer: its own error text.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()
REASON = "chat is not active; restore it before sending messages"


def _route_for(status: int, body: object):
    def _route(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
        elif path == "/api/cloud/model":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "model": "", "provider": "", "source": "server"}))
        elif path == "/api/chat" and request.method == "POST":
            payload = body if isinstance(body, str) else json.dumps(body)
            route.fulfill(status=status, content_type="application/json" if not isinstance(body, str) else "text/plain",
                          body=payload)
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")
    return _route


def _send(status: int, body: object) -> dict:
    ctx, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        page.route("**/*", _route_for(status, body))
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(500)
        page.click("#input")
        page.keyboard.type("hello from an archived chat")
        page.click("#send")
        page.wait_for_timeout(900)
        return page.evaluate("""() => {
            const bubbles = [...document.querySelectorAll('.msg.assistant')];
            const last = bubbles[bubbles.length - 1];
            return { text: last ? last.textContent : '', sendDisabled: document.getElementById('send').disabled };
        }""")
    finally:
        browser.close()
        ctx.stop()


def test_an_archived_chat_refusal_shows_its_reason():
    state = _send(409, {"error": REASON, "lifecycle_state": "archived"})
    assert REASON in state["text"], state
    assert "(no response)" not in state["text"], state
    assert state["sendDisabled"] is False, "the composer stayed locked after a refused turn"


def test_a_refusal_without_a_json_body_still_names_its_status():
    state = _send(503, "upstream unavailable")
    assert "503" in state["text"], state
    assert "(no response)" not in state["text"], state
