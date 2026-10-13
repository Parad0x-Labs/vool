"""Small comforts on the served chat page, proven against a real daemon and a real browser.

Each check pins one thing a person notices:

- a starter on the empty screen fills an EMPTY message box (never replacing a draft) and never sends;
- reading an earlier answer is not interrupted: a reply that lands while the reader is scrolled up
  leaves the view where it is and offers "Latest", while the reader's own send always follows;
- Up in an empty box brings back the last message, and never replaces typed text;
- Enter that confirms an input-method candidate is typing, not a send;
- a tab in the background says the answer is ready, and goes back to "VOOL" when looked at;
- the pet names an untitled chat "New chat", never a raw id tail;
- shortcut key caps read Ctrl+ off a Mac and keep the command sign on a Mac.
"""
from __future__ import annotations

import time

from tests import _reader_served_rig as rig
from tests import served_browser

LONG_REPLY = "\n\n".join(f"Paragraph {n}: a calm, ordinary line of answer text." for n in range(40))
DELAY_MARK = "please take a moment"
_DELAYED: list[bool] = []


def _delay(body):
    """Hold the answer to the delayed turn long enough for the reader to scroll away first."""
    text = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
    if DELAY_MARK in text and not _DELAYED:
        _DELAYED.append(True)
        time.sleep(2.5)
    return None   # the scripted default answers


def _open(browser, base_url, init_script=None):
    page = browser.new_page(viewport={"width": 1280, "height": 760})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    sends: list[str] = []
    page.on("request", lambda r: sends.append(r.post_data or "") if r.url == base_url + "/api/chat" else None)
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base_url) else route.abort())
    if init_script:
        page.add_init_script(init_script)
    page.goto(base_url + "/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=10000)
    return page, errors, sends


def _wait_done(page):
    try:
        page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'", timeout=30000)
    except Exception:
        print("DIAG", page.evaluate("({run: view.run && {status: view.run.status, released: view.run.released}, msgs: [...document.querySelectorAll('.msg .msg-text')].map(e => e.textContent.slice(0, 100))})"), flush=True)
        raise


def _send_and_wait(page, text):
    page.locator("#input").fill(text)
    page.locator("#send").click()
    _wait_done(page)


def test_small_comforts_on_the_served_chat_page(tmp_path):
    with rig.CapturingProvider(default=LONG_REPLY, reply_fn=_delay) as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=60).get("state") == "verified"
            manager, browser = served_browser.launch_chromium()
            try:
                page, errors, sends = _open(browser, daemon.base_url)

                # The pet names the fresh, untitled chat in words.
                page.wait_for_function("document.querySelector('.vn-bubble-title') && document.querySelector('.vn-bubble-title').textContent", timeout=10000)
                assert page.locator(".vn-bubble-title").inner_text().strip() == "New chat"

                # Off a Mac every shortcut cap reads Ctrl+ (the handlers accept Cmd or Ctrl).
                page.wait_for_selector("#vpHint", timeout=5000)
                hint = page.locator("#vpHint").inner_text()
                assert "Ctrl+K" in hint and "⌘" not in hint, hint
                assert page.locator("#newChat kbd").inner_text() == "Ctrl+N"

                # A starter fills the box and sends nothing.
                starters = page.locator(".empty-start")
                assert starters.count() == 3
                # A draft is never replaced by a starter, and the starters step back while it is there.
                page.locator("#input").fill("my half written draft")
                assert starters.first.is_hidden()
                page.evaluate("document.querySelector('.empty-start').click()")
                assert page.locator("#input").input_value() == "my half written draft"
                page.locator("#input").fill("")
                assert starters.first.is_visible()
                first = starters.first.inner_text()
                starters.first.click()
                assert page.locator("#input").input_value() == first
                page.wait_for_timeout(300)
                assert sends == []

                # Enter confirming an input-method candidate does not send.
                page.evaluate("""() => inputEl.dispatchEvent(new KeyboardEvent('keydown',
                    {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}))""")
                page.wait_for_timeout(300)
                assert sends == [] and page.locator("#input").input_value() == first

                # A real turn: the reader's own send lands at the bottom.
                _send_and_wait(page, "Write one friendly sentence about a harbor lantern.")
                assert page.evaluate("logAtBottom()")
                assert page.locator("#jumpLatest").is_hidden()

                # A reply that arrives while the reader scrolled up leaves them where they are,
                # and the tab, if in the background, says the answer is ready.
                page.locator("#input").fill("Write one more friendly sentence about a harbor lantern, " + DELAY_MARK + ".")
                page.locator("#send").click()
                page.wait_for_function("view.run && !view.run.released", timeout=10000)
                page.evaluate("""() => {
                    Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
                    logEl.scrollTop = 0; logEl.dispatchEvent(new Event('scroll'));
                }""")
                _wait_done(page)
                assert "Paragraph 39" in page.locator(".msg.assistant .msg-text").last.inner_text()
                assert page.evaluate("logEl.scrollTop") < 40
                assert page.locator("#jumpLatest").is_visible()
                assert page.evaluate("document.title") == "● Answer ready · VOOL"
                page.evaluate("""() => {
                    Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
                    document.dispatchEvent(new Event('visibilitychange'));
                }""")
                assert page.evaluate("document.title") == "VOOL"
                page.locator("#jumpLatest").click()
                assert page.evaluate("logAtBottom()")
                assert page.locator("#jumpLatest").is_hidden()

                # Up in an empty box recalls the last message; typed text is never replaced.
                page.locator("#input").fill("")
                page.locator("#input").focus()
                page.keyboard.press("ArrowUp")
                assert page.locator("#input").input_value() == "Write one more friendly sentence about a harbor lantern, " + DELAY_MARK + "."
                page.locator("#input").fill("half typed")
                page.keyboard.press("ArrowUp")
                assert page.locator("#input").input_value() == "half typed"
                assert len(sends) == 2
                assert not errors, errors
                page.close()

                # On a Mac the caps keep the command sign.
                mac, mac_errors, _ = _open(browser, daemon.base_url, init_script=(
                    "Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});"
                    "Object.defineProperty(navigator, 'userAgentData', {get: () => undefined});"))
                mac.wait_for_selector("#vpHint", timeout=5000)
                assert "⌘K" in mac.locator("#vpHint").inner_text()
                assert not mac_errors, mac_errors
                mac.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
