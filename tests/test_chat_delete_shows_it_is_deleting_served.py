"""A chat delete that takes a while (installed 0.8, owner's Mac, 2026-11-21).

With memory in swap, a confirmed delete took 10-47 s to answer and the row sat unchanged, so it looked
like nothing happened; it was confirmed four more times. The row now shows at once that it is being
deleted, a second delete of the same chat is not sent, and the row goes when the server answers.
"""

from __future__ import annotations

from tests import _reader_served_rig as rig
from tests import served_browser
from tests.test_chat_page_calm_delete_dialog_served import _session_ids


def test_a_slow_delete_shows_at_once_and_is_sent_once(tmp_path):
    with rig.CapturingProvider(default="The lantern glowed.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            daemon.certify(timeout=60)
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                page.locator("#input").fill("Tell me about the harbor lantern.")
                page.locator("#send").click()
                page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'", timeout=30000)
                chat = page.evaluate("displayedChat")
                page.wait_for_function("() => document.querySelectorAll('#sessions .session.active').length > 0", timeout=15000)

                held: list = []

                def hold_deletes(route):
                    body = route.request.post_data or ""
                    if '"delete": true' in body or '"delete":true' in body:
                        held.append(route)  # answered later, like a busy machine
                    else:
                        route.continue_()

                page.route("**/api/chat/session", hold_deletes)
                page.locator("#sessions .session.active").first.hover()
                page.locator("#sessions .session.active .s-act[title^='Delete chat']").first.click()
                page.wait_for_selector("#confirmPromptDialog[open]", timeout=5000)
                page.locator("#confirmPromptDialog .confirm-yes").click()
                page.wait_for_function("() => document.querySelector('#sessions .session.deleting') !== null", timeout=5000)
                # Asking again while the first delete is unanswered sends nothing more.
                page.evaluate("(id) => { deleteSession(id, 'again'); }", chat)
                page.wait_for_timeout(1000)
                assert page.locator("#confirmPromptDialog[open]").count() == 0
                assert len(held) == 1, len(held)
                held[0].continue_()
                page.wait_for_function("id => displayedChat !== id", arg=chat, timeout=15000)
                page.wait_for_function("() => document.querySelector('#sessions .session.deleting') === null", timeout=10000)
                assert chat not in _session_ids(daemon.base_url)
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
