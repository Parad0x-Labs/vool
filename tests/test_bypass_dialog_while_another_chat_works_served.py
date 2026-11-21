"""The Bypass permissions dialog while another chat's task is working (installed 0.8, 3078b8cb4).

Measured on the owner's Mac 2026-11-21: another chat was running a task; in a second chat "hi" showed
"The message could not be sent.", and the Bypass permissions dialog (This chat session, 15 minutes)
stayed on "Checking this chat's folder..." while "Enable bypass" did nothing. The server answered
every /api/mode call in about half a second.
"""

from __future__ import annotations

import json
import time

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_queue_cross_tab_http_served import _hold_turn, _sid
from tests.test_chat_queue_cross_tab_served import _GatedProvider, _queue_state, _wait_for

BUSY = "Write one friendly sentence about a harbor lantern."
BUSY_ANSWER = "LANTERN-ONE: The lantern kept its small promise."
HI_ANSWER = "HELLO-TWO: Hello there."


class _Provider(_GatedProvider):
    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if "The question to judge:" in rig._all_text(messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return super()._reply(body)


@pytest.fixture
def busy_elsewhere(tmp_path):
    with _Provider({BUSY: BUSY_ANSWER, "what is a harbor?": HI_ANSWER}, hold_texts={BUSY}) as provider:
        daemon = rig.ServedDaemon(
            tmp_path / "home", provider=provider,
            env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback", "VOOL_RAW_OLLAMA_API_URL": "http://127.0.0.1:1"},
        )
        daemon.start(timeout=60)
        try:
            daemon.certify(timeout=30)
            other = _sid("other-chat-busy")
            thread, box = _hold_turn(daemon, BUSY, other, turn_id="t-busy-1")
            _wait_for(lambda: _queue_state(daemon, other).get("turn_active"), 30, "the other chat never got busy")
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_context().new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=5000)
                _wait_for(lambda: page.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 5, "no chat")
                yield provider, daemon, page, errors
            finally:
                provider.release(BUSY)
                thread.join(timeout=60)
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def _open_bypass(page) -> None:
    page.click("#modeBtn")
    page.click('#modePop .pop-item[data-mode="bypass_permissions"]')
    page.wait_for_selector("#bypassOverlay:not([hidden])", timeout=5000)


def test_the_dialog_reads_this_chats_folder_while_another_chat_works(busy_elsewhere):
    provider, daemon, page, errors = busy_elsewhere
    page.locator("#input").fill("what is a harbor?")
    page.locator("#send").click()
    time.sleep(3)
    texts = page.evaluate("Array.from(document.querySelectorAll('#log .msg')).map((el) => (el.querySelector('.msg-text') || el).textContent.trim())")
    print("log after send:", texts)
    _open_bypass(page)
    deadline = time.monotonic() + 10
    hint = ""
    while time.monotonic() < deadline:
        hint = page.text_content("#bypassReadiness") or ""
        if "Checking" not in hint:
            break
        time.sleep(0.2)
    print("hint:", hint, "confirm disabled:", page.is_disabled("#bypassConfirm"), "scope:", page.input_value("#bypassScope"))
    assert "Checking" not in hint, hint
    page.select_option("#bypassScope", "session")
    page.click("#bypassConfirm")
    _wait_for(lambda: page.evaluate("view.mode") == "bypass_permissions" or not page.is_hidden("#bypassError"), 10, "Enable bypass did nothing")
    print("mode:", page.evaluate("view.mode"), "error:", page.text_content("#bypassError"))
    assert page.evaluate("view.mode") == "bypass_permissions"
    assert not errors, errors
