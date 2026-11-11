"""A chat row in the sidebar shows few, clear actions, on the served chat page in a real browser.

Before: seven controls per row (emoji/colour, move, a pin emoji AND a second pin mark, rename,
archive, delete), and pin/unpin were two different glyphs.
Now:
- pin, rename, archive and delete are on the row; the pin is ONE toggle whose state is the chat's
  (aria-pressed, title) and it is the only pin on the row -- no colour emoji mark beside it;
- a pinned chat keeps that pin showing when the row is idle; nothing else shows then;
- emoji/colour and move-to-project open from one chevron, IN PLACE of the four usual actions (so
  the title keeps its width), stay open across the sidebar's own re-renders, and close again from
  the same chevron;
- the Pinned section header uses the line pin icon, not a colour emoji;
- a refused or unreachable pin change says so and leaves the server state as it was;
- an archived chat has no pin and no move; its chevron holds only emoji/colour.
"""
from __future__ import annotations

import json
from urllib.request import urlopen

from tests import _reader_served_rig as rig
from tests import served_browser

QUESTION = "Write one short sentence about a lighthouse keeper's cat."


def _row(base_url, sid):
    with urlopen(base_url + "/api/chat/sessions?include_archived=1", timeout=5) as response:
        payload = json.load(response)
    rows = payload.get("sessions") if isinstance(payload, dict) else payload
    return next((r for r in rows or [] if isinstance(r, dict) and r.get("session_id") == sid), None)


VISIBLE_ACTIONS = """sel => [...document.querySelectorAll(sel + ' .s-act')]
    .filter((b) => b.offsetParent !== null && getComputedStyle(b).visibility !== 'hidden')
    .map((b) => b.title)"""


def test_chat_row_has_one_pin_toggle_and_a_chevron_for_the_rest(tmp_path):
    with rig.CapturingProvider(default="The cat watched the beam.") as provider:
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
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(daemon.base_url) else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=10000)
                page.locator("#input").fill(QUESTION)
                page.locator("#send").click()
                page.wait_for_function("view.run && view.run.released && view.run.status === 'completed'", timeout=30000)
                sid = page.evaluate("displayedChat")
                sel = f"#sessions .session[data-id='{sid}']"
                page.wait_for_selector(sel + " .s-pin", state="attached", timeout=15000)
                row = page.locator(sel).first

                # Idle, unpinned: nothing shows. Hovered: chevron, pin, rename, archive, delete.
                page.locator("#input").hover()
                assert page.evaluate(VISIBLE_ACTIONS, sel) == []
                row.hover()
                shown = page.evaluate(VISIBLE_ACTIONS, sel)
                assert shown == ["More: emoji, colour, move to project", "Pin chat to the top", "Rename",
                                 "Archive", "Delete chat (removes it and its memory)"], shown

                # The chevron slides out emoji/colour and move; a sidebar re-render keeps it open.
                chevron = row.locator(".s-more-toggle")
                assert chevron.get_attribute("aria-expanded") == "false"
                title_w = "s => document.querySelector(s + ' .s-title').getBoundingClientRect().width"
                closed_w = page.evaluate(title_w, sel)
                chevron.click()
                assert page.evaluate(title_w, sel) >= closed_w   # opening never squeezes the title
                assert chevron.get_attribute("aria-expanded") == "true"
                shown = page.evaluate(VISIBLE_ACTIONS, sel)
                # Open, the chevron swaps the four usual actions for these two, so the title keeps its room.
                assert shown == ["Set chat emoji or colour", "Move to project", "Fewer actions"], shown
                page.evaluate("loadSessions()")
                page.wait_for_function("s => document.querySelector(s + '.more-open')", arg=sel, timeout=5000)
                row = page.locator(sel).first
                row.hover()
                row.locator(".s-more-toggle").click()
                assert "Move to project" not in page.evaluate(VISIBLE_ACTIONS, sel)
                assert page.evaluate("_sessionMoreOpen") == ""

                # Pin: one toggle; the server state flips and the same control now unpins.
                row.locator(".s-pin").click()
                page.wait_for_function("s => document.querySelector(s + '.pinned .s-pin.on')", arg=sel, timeout=10000)
                assert _row(daemon.base_url, sid).get("pinned") is True
                pinned_rows = page.locator(sel)
                for i in range(pinned_rows.count()):
                    r = pinned_rows.nth(i)
                    assert r.locator(".s-pin").count() == 1
                    pin = r.locator(".s-pin")
                    assert pin.get_attribute("title") == "Unpin chat"
                    assert pin.get_attribute("aria-pressed") == "true"
                    assert pin.get_attribute("data-ico") == "pin-filled"
                head = page.locator("#sessions .pin-head .proj-name")
                assert head.get_attribute("data-ico") == "pin" and "\U0001F4CC" not in head.inner_text()
                assert page.evaluate("getComputedStyle(document.querySelector('#sessions .pin-head .proj-name'), '::before').content") == '""'
                for i in range(pinned_rows.count()):
                    text = pinned_rows.nth(i).inner_text()
                    assert "\U0001F4CC" not in text and "\U0001F4CD" not in text, text
                # Idle pinned row: only its pin shows (the "pinned" mark is the toggle itself).
                page.locator("#input").hover()
                idle = page.evaluate(VISIBLE_ACTIONS, sel)
                assert idle and set(idle) == {"Unpin chat"}, idle

                # A refused change and an unreachable server both say so; the state is kept.
                page.route("**/api/chat/pin-chat", lambda route: route.fulfill(
                    status=400, content_type="application/json", body='{"ok": false, "error": "x"}'))
                page.locator(sel).first.hover()
                page.locator(sel).first.locator(".s-pin").click()
                page.wait_for_function("document.body.innerText.includes(\"Couldn't change this chat's pin\")", timeout=5000)
                assert _row(daemon.base_url, sid).get("pinned") is True
                page.unroute("**/api/chat/pin-chat")
                page.route("**/api/chat/pin-chat", lambda route: route.abort())
                page.locator(sel).first.hover()
                page.locator(sel).first.locator(".s-pin").click()
                page.wait_for_function("document.body.innerText.includes(\"VOOL couldn't be reached, so the pin didn't change\")", timeout=5000)
                assert _row(daemon.base_url, sid).get("pinned") is True
                page.unroute("**/api/chat/pin-chat")

                # Unpin with the same control.
                page.locator(sel).first.hover()
                page.locator(sel).first.locator(".s-pin").click()
                page.wait_for_function("s => { const p = document.querySelector(s + ' .s-pin');"
                                       " return p && p.getAttribute('aria-pressed') === 'false'; }", arg=sel, timeout=10000)
                assert _row(daemon.base_url, sid).get("pinned") is False
                pin = page.locator(sel).first.locator(".s-pin")
                assert pin.get_attribute("title") == "Pin chat to the top" and pin.get_attribute("data-ico") == "pin"

                # Archived: no pin, no move; the chevron holds only emoji/colour.
                page.evaluate("id => archiveSession(id, true)", sid)
                page.wait_for_function("id => (_lastSessions || []).some((s) => s.session_id === id && s.archived)", arg=sid, timeout=10000)
                page.evaluate("""() => { const h = [...document.querySelectorAll('#sessions .arch-head')][0];
                    if (h) h.click(); }""")
                page.wait_for_selector(sel + " .s-more-toggle", state="attached", timeout=5000)
                arch = page.locator(sel).first
                assert arch.locator(".s-pin").count() == 0
                assert arch.locator(".s-act[data-ico='move']").count() == 0
                arch.locator(".s-more-toggle").click()
                page.wait_for_function("id => _sessionMoreOpen === id", arg=sid, timeout=5000)
                # The sidebar may re-render under the pointer (archive refreshes it); hover the row as
                # it stands now and wait for what it shows.
                want = ["Set chat emoji or colour", "Fewer actions"]
                page.locator(sel).first.hover()
                page.wait_for_function("([s, w]) => JSON.stringify((" + VISIBLE_ACTIONS + ")(s)) === JSON.stringify(w)",
                                       arg=[sel, want], timeout=5000)

                # A mouse click on an action does not keep the row's actions showing after the
                # pointer leaves; keyboard focus does show them.
                page.locator(sel).first.locator(".s-more-toggle").click()
                page.locator("#input").hover()
                assert page.evaluate(VISIBLE_ACTIONS, sel) == []
                page.keyboard.press("Shift")   # the last interaction is the keyboard, so focus is :focus-visible
                page.locator(sel).first.locator(".s-title").focus()
                assert "Rename" in page.evaluate(VISIBLE_ACTIONS, sel)
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
