"""Deleting a chat asks in VOOL's own dialog, on the served chat page in a real browser.

Before: the browser's plain confirm box (which the native shell's web view may not implement).
Now an in-page dialog names the chat, explains what is lost, and starts with focus on Cancel.

- Esc, Cancel and a stray Enter (focus starts on Cancel) all keep the chat on the server;
- only the explicit Delete button removes it;
- no native browser dialog is ever raised.
"""
from __future__ import annotations

import json
from urllib.request import urlopen

from tests import _reader_served_rig as rig
from tests import served_browser

QUESTION = "Write one friendly sentence about a harbor lantern."


def _session_ids(base_url):
    with urlopen(base_url + "/api/chat/sessions", timeout=5) as response:
        payload = json.load(response)
    rows = payload.get("sessions") if isinstance(payload, dict) else payload
    return {row.get("session_id") for row in rows or [] if isinstance(row, dict)}


def test_deleting_a_chat_asks_in_the_calm_dialog(tmp_path):
    with rig.CapturingProvider(default="The lantern glowed.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            assert daemon.certify(timeout=60).get("state") == "verified"
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                native: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(daemon.base_url) else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                page.locator("#input").fill(QUESTION)
                page.locator("#send").click()
                page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'", timeout=30000)
                chat = page.evaluate("displayedChat")
                page.wait_for_function("id => [...document.querySelectorAll('#sessions .s-act')].length > 0", arg=chat, timeout=15000)
                assert chat in _session_ids(daemon.base_url)

                trash = page.locator("#sessions .session.active .s-act[title^='Delete chat']").first

                def open_dialog():
                    page.locator("#sessions .session.active").first.hover()   # actions show on hover
                    trash.click()
                    page.wait_for_selector("#confirmPromptDialog[open]", timeout=5000)
                    return page.locator("#confirmPromptDialog")

                dialog = open_dialog()
                assert dialog.locator(".confirm-title").inner_text().startswith("Delete “")
                assert "everything VOOL learned" in dialog.locator(".confirm-detail").inner_text()
                assert page.evaluate("document.activeElement && document.activeElement.hasAttribute('data-cancel')")
                page.keyboard.press("Escape")
                page.wait_for_selector("#confirmPromptDialog", state="detached", timeout=5000)
                assert chat in _session_ids(daemon.base_url)
                # Focus goes somewhere useful after closing, never back to nothing.
                assert page.evaluate("document.activeElement && document.activeElement !== document.body")
                # The name drops its own trailing punctuation and is isolated for right-to-left text;
                # a long unbroken name wraps inside the dialog instead of running out of it.
                assert page.evaluate("confirmName('Plans.')") == "\u2068Plans\u2069"
                assert page.evaluate("confirmName('???')") == "\u2068???\u2069"   # never empty quotes
                width = page.evaluate("""() => { requestConfirm('Delete “' + 'x'.repeat(300) + '”?', 'd', 'Delete');
                    const d = document.getElementById('confirmPromptDialog'); const r = [d.scrollWidth, d.clientWidth];
                    d.querySelector('[data-cancel]').click(); return r; }""")
                assert width[0] <= width[1] + 1, width

                open_dialog()
                page.keyboard.press("Enter")   # focus is on Cancel
                page.wait_for_selector("#confirmPromptDialog", state="detached", timeout=5000)
                assert chat in _session_ids(daemon.base_url)

                dialog = open_dialog()
                dialog.locator("[data-cancel]").click()
                page.wait_for_selector("#confirmPromptDialog", state="detached", timeout=5000)
                assert chat in _session_ids(daemon.base_url)

                dialog = open_dialog()
                dialog.locator(".confirm-yes").click()
                page.wait_for_function("id => displayedChat !== id", arg=chat, timeout=10000)
                assert chat not in _session_ids(daemon.base_url)

                # Deleting a project removes the row whose button opened the dialog: focus still
                # lands in the message box, not on nothing.
                (tmp_path / "garden").mkdir()
                pid = page.evaluate("""async root => { const r = await fetch('/api/projects', {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'Garden', root})});
                    const d = await r.json(); await refreshProjects(); await loadSessions(); return d.project.id; }""",
                    str(tmp_path / "garden"))
                page.evaluate("pid => { deleteProject(pid); }", pid)
                page.wait_for_selector("#confirmPromptDialog .confirm-yes", timeout=5000).click()
                page.wait_for_function("pid => !_serverProjects[pid]", arg=pid, timeout=10000)
                page.wait_for_function("document.activeElement === inputEl", timeout=5000)
                assert native == [], native
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
