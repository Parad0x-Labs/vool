"""Try again and Edit on chat messages, proven against a real daemon and a real browser.

- Try again under an answer asks the question that produced it again, as a NEW turn through the
  ordinary send path: the earlier question and answer stay in the chat exactly as they happened.
- Edit on your own message puts its words back in the box and sends nothing until you do.
- Neither ever overwrites what is already in the box: a busy box refuses and is left untouched.
- A question made only of attachments has no words to resend, and says so instead of sending.
- A question that carried files is never resent without them: its words wait in the box.
"""
from __future__ import annotations

from tests import _reader_served_rig as rig
from tests import served_browser

QUESTION = "Write one friendly sentence about a harbor lantern."
REPLY = "The harbor lantern glowed like a patient friend."


def _wait_done(page, turns):
    page.wait_for_function(
        "n => view.run && view.run.released && view.run.status === 'completed'"
        " && document.querySelectorAll('.msg.user').length >= n",
        arg=turns, timeout=30000,
    )


def _click_action(page, bubble, cls):
    bubble.hover()
    bubble.locator(".msg-actions ." + cls).click()


def test_try_again_and_edit_go_through_the_ordinary_send(tmp_path):
    with rig.CapturingProvider(default=REPLY) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=60).get("state") == "verified"
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                sends: list[str] = []
                page.on("request", lambda r: sends.append(r.post_data or "")
                        if r.url == daemon.base_url + "/api/chat" else None)
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(daemon.base_url) else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)

                page.locator("#input").fill(QUESTION)
                page.locator("#send").click()
                _wait_done(page, 1)
                assert len(sends) == 1
                answer = page.locator(".msg.assistant").last
                assert REPLY in answer.locator(".msg-text").inner_text()
                actions = answer.locator(".msg-actions button")
                assert "msg-retry" in " ".join(actions.evaluate_all("bs => bs.map(b => b.className)"))
                assert answer.locator(".msg-retry").inner_text() == "Try again"

                # A busy box is never overwritten and nothing is sent.
                page.locator("#input").fill("half typed")
                _click_action(page, answer, "msg-retry")
                page.wait_for_timeout(400)
                assert page.locator("#input").input_value() == "half typed"
                assert len(sends) == 1
                page.locator("#input").fill("")

                # Try again: a new turn with the same words; the first turn stays as it was.
                _click_action(page, answer, "msg-retry")
                _wait_done(page, 2)
                assert len(sends) == 2 and QUESTION in sends[1]
                users = page.locator(".msg.user .msg-text").all_inner_texts()
                assert users == [QUESTION, QUESTION]
                assert page.locator(".msg.assistant").count() >= 2
                assert REPLY in page.locator(".msg.assistant .msg-text").first.inner_text()
                assert page.locator("#input").input_value() == ""

                # Edit: the words come back to the box; nothing is sent until the person sends.
                mine = page.locator(".msg.user").first
                _click_action(page, mine, "msg-edit")
                assert page.locator("#input").input_value() == QUESTION
                page.wait_for_timeout(400)
                assert len(sends) == 2

                # A question that was only an attachment marker has no words to resend.
                page.locator("#input").fill("")
                note = page.evaluate("""() => {
                    const el = addMsg('user', '[document: plan.pdf #la-abc123]');
                    const reply = addMsg('assistant', 'Read it.');
                    let shown = '';
                    const original = toast; toast = (m) => { shown = m; };
                    try { resendFromBubble(reply, true); } finally { toast = original; }
                    return {shown, box: inputEl.value};
                }""")
                assert note["box"] == "" and "only attachments" in note["shown"]
                assert len(sends) == 2

                # A question that carried a file: Try again puts the words back but never sends
                # them without the file, and says so.
                withfile = page.evaluate("""() => {
                    const q = addMsg('user', 'What is in this plan? [document: plan.pdf #la-abc123]');
                    const reply = addMsg('assistant', 'It is a plan.');
                    let shown = '';
                    const original = toast; toast = (m) => { shown = m; };
                    try { resendFromBubble(reply, true); } finally { toast = original; }
                    return {shown, box: inputEl.value};
                }""")
                assert withfile["box"] == "What is in this plan?", withfile
                assert "Attach the files again" in withfile["shown"]
                page.wait_for_timeout(400)
                assert len(sends) == 2
                page.locator("#input").fill("")
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
