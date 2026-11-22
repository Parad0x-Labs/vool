"""A send on a machine that answers slowly (installed 0.8, owner's Mac, 2026-11-21).

With memory almost all in swap while a local model answered, every small read the page makes took
3-17 s. Before a message is sent the page reads the chat's model selection with a 5 s limit; a
slower answer ended the send with "The message could not be sent." and no reason, for "from whom ?"
and the next message alike. The slowness is simulated here by holding that one read in the browser;
what the person sees is checked, not any wording of what they typed.
"""

from __future__ import annotations

import json
import time

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_queue_cross_tab_served import _GatedProvider, _wait_for

ASKS = {
    "what is a harbor?": "HARBOR-ONE: A sheltered place for boats.",
    "and a lighthouse": "LIGHT-TWO: A tower with a light for ships.",
}


class _Provider(_GatedProvider):
    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if "The question to judge:" in rig._all_text(messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return super()._reply(body)


@pytest.fixture
def page(tmp_path):
    with _Provider(ASKS, hold_texts=set()) as provider:
        daemon = rig.ServedDaemon(
            tmp_path / "home", provider=provider,
            env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback", "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1"},
        )
        daemon.start(timeout=60)
        try:
            daemon.certify(timeout=30)
            manager, browser = served_browser.launch_chromium()
            try:
                p = browser.new_context().new_page()
                errors: list[str] = []
                p.on("pageerror", lambda error: errors.append(str(error)))
                p.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                p.wait_for_selector("#input", timeout=5000)
                _wait_for(lambda: p.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 5, "no chat")
                yield p, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def _slow_model_reads(p, delays: list[float]) -> None:
    left = list(delays)

    def handle(route):
        delay = left.pop(0) if left else 0.0
        if delay:
            time.sleep(delay)
        try:
            route.continue_()
        except Exception:
            pass  # the page already gave up on this request

    p.route("**/api/cloud/model?session_id=*", handle)


def _texts(p) -> list[str]:
    return p.evaluate("Array.from(document.querySelectorAll('#log .msg')).map((el) => (el.querySelector('.msg-text') || el).textContent.trim())")


@pytest.mark.parametrize("ask", list(ASKS))
def test_one_slow_read_does_not_fail_the_send(page, ask):
    p, errors = page
    _slow_model_reads(p, [6.5])
    p.locator("#input").fill(ask)
    p.locator("#send").click()
    _wait_for(lambda: ASKS[ask] in _texts(p), 60, "the message was not answered after one slow read")
    assert "The message could not be sent." not in _texts(p)
    assert not errors, errors


def test_a_machine_too_slow_to_answer_says_so_and_keeps_the_message(page):
    p, errors = page
    _slow_model_reads(p, [6.5, 21.0])
    p.locator("#input").fill("what is a harbor?")
    p.locator("#send").click()
    _wait_for(lambda: any("answering too slowly" in t and "back in the message box" in t for t in _texts(p)), 60, "no reason was given")
    assert "The message could not be sent." not in _texts(p)
    assert p.input_value("#input") == "what is a harbor?", "the message was not put back"
    assert not errors, errors
