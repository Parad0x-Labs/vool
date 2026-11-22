"""Timer polls that the machine answers slowly never take the connections a send needs.

Gate v3 on the installed d2dacb04a (2026-11-22, owner's Mac with swap nearly full): GET
/api/disclosure/pending took 4-11 s per answer while the page asked for it every second without
waiting for the last one, so 10-20 were in flight at once. A browser keeps only a few connections to
one host; the page's read of the chat's model before a send never left the browser (the server log
has no trace of it), and two messages came back "answering too slowly ... it wasn't sent".

Here those polls are held in the browser, never answered, the way a slow machine holds them. The
page must keep at most one of each in flight, and a message sent meanwhile must be answered.
"""

from __future__ import annotations

import json
import threading

import pytest

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_queue_cross_tab_served import _GatedProvider, _wait_for

ASK = "what is a harbor?"
ANSWER = "HARBOR-ONE: A sheltered place for boats."
HELD = ("**/api/disclosure/pending*", "**/api/update/status*")


class _Provider(_GatedProvider):
    def _reply(self, body):
        messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
        if "The question to judge:" in rig._all_text(messages):
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        return super()._reply(body)


@pytest.fixture
def page(tmp_path):
    with _Provider({ASK: ANSWER}, hold_texts=set()) as provider:
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
                yield daemon, p, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


def test_held_polls_stay_one_at_a_time_and_a_message_still_goes_out(page):
    daemon, p, errors = page
    held: dict[str, list] = {pattern: [] for pattern in HELD}
    lock = threading.Lock()

    def holder(pattern):
        def hold(route):
            with lock:
                held[pattern].append(route)  # never answered: a machine too slow to answer
        return hold

    for pattern in HELD:
        p.route(pattern, holder(pattern))
    p.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
    p.wait_for_selector("#input", timeout=5000)
    _wait_for(lambda: p.evaluate("typeof displayedChat === 'string' && !!displayedChat"), 5, "no chat")
    p.wait_for_timeout(6000)  # six disclosure ticks, one or two update ticks

    with lock:
        in_flight = {pattern: len(routes) for pattern, routes in held.items()}
    assert in_flight["**/api/disclosure/pending*"] == 1, in_flight
    assert in_flight["**/api/update/status*"] <= 1, in_flight

    p.locator("#input").fill(ASK)
    p.locator("#send").click()
    _wait_for(
        lambda: ANSWER in p.evaluate(
            "Array.from(document.querySelectorAll('#log .msg')).map((el) => (el.querySelector('.msg-text') || el).textContent.trim()).join('\\n')"
        ),
        40,
        "the message was not answered while the polls were held",
    )
    with lock:
        assert len(held["**/api/disclosure/pending*"]) <= 2, "polls piled up behind the held one"
        for routes in held.values():
            for route in routes:
                try:
                    route.continue_()
                except Exception:
                    pass
    assert not errors, errors
