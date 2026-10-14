"""Pet fixes from the fresh-install UX review (2026-10-13, ux-review-20261013).

Each test pins what a user saw going wrong:
* The pet's right-click menu let clicks fall through, so a row pressed the app control under it.
* The menu never closed: no Escape, no click outside, and it stayed up beside the status card.
* A chat without a title made the pet's first words a random 8-character id.
* After every ordinary answer a screen reader heard "Companion Unknown — Completed — not
  independently reviewed."
* The menu offered "Reset position" for a pet that was not on the desktop, and named the
  character by its raw id.
* "Customise pet" in the Settings window opened the drawer in the chat window behind Settings.
* A dropped evaluate in the desktop pet left its buttons latched in the last reported state.
"""

from __future__ import annotations

import re
import sys
import types

from core.companion_presentation_fragment import render_companion_fragment
from installer.bundle import pet_native
from installer.bundle.vool_window import _WindowApi
from tests.chat_page_js_harness import DOM, run_node
from tests.test_companion_one_drag_finality import page_scripts
from tests.test_native_companion_window import _FakeWebview, _FakeWindow


def _css_rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, selector
    return match.group(1)


def test_the_pet_menu_and_status_card_take_their_own_clicks() -> None:
    css = render_companion_fragment()
    # The layer they live in is click-through; without their own pointer-events every row
    # clicked whatever app control sat underneath it.
    assert re.search(r"#companionLayer\s*\{[^}]*pointer-events:\s*none", css)
    for selector in (".vool-ninja-pop", ".vool-ninja-menu"):
        assert re.search(r"pointer-events:\s*auto", _css_rule(css, selector)), selector


def _run(driver: str) -> dict:
    """Boot the real chat page under node with window AND document listeners recorded."""
    program = (
        DOM
        + "\nglobalThis.__winH = {}; globalThis.__docH = {};\n"
        + "window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };\n"
        + "document.addEventListener = (t, f) => { (__docH[t] = __docH[t] || []).push(f); };\n"
        # A real DOM node answers contains(); the stub needs the same tree walk.
        + "Object.getPrototypeOf(document.body).contains = function (n) {"
        + " for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; };\n"
        + "\n;await (async function(){\n" + "\n;\n".join(page_scripts()) + "\n" + driver + "\n})();\n"
    )
    result = run_node(program, timeout=120)
    assert not result.get("errors"), result["errors"]
    return result


MENU_DRIVER = r"""
const res = { errors: [] };
try {
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  const menu = layer.children.find((c) => c.className === "vool-ninja-menu");
  const pop = layer.children.find((c) => c.className === "vool-ninja-pop");
  const fire = (type, ev) => (__docH[type] || []).forEach((f) => f(Object.assign(
    { preventDefault() {}, stopPropagation() {} }, ev)));
  const open = () => sprite.__on.contextmenu({ preventDefault() {}, stopPropagation() {}, clientX: 50, clientY: 50 });

  pop.style.display = "block";
  open();
  res.openShown = menu.style.display;
  res.popAfterMenu = pop.style.display;
  res.rows = menu.children.map((b) => b.textContent);
  fire("keydown", { key: "Escape" });
  res.afterEscape = menu.style.display;

  let focused = 0;
  sprite.focus = () => { focused++; };
  open();
  fire("keydown", { key: "Escape" });
  res.focusAfterMouseMenu = focused;
  sprite.__on.keydown({ key: " ", preventDefault() {} });
  fire("keydown", { key: "Escape" });
  res.focusAfterKeyMenu = focused;
  sprite.__on.keydown({ key: " ", preventDefault() {} });
  menu.children.find((b) => b.textContent.startsWith("Motion")).__click();
  res.focusAfterKeyRow = focused;
  res.afterRow = menu.style.display;

  open();
  fire("pointerdown", { target: menu.children[0] });
  res.afterInsidePress = menu.style.display;
  fire("pointerdown", { target: document.body });
  res.afterOutsidePress = menu.style.display;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_the_pet_menu_closes_like_any_menu_and_shows_only_what_works() -> None:
    out = _run(MENU_DRIVER)
    assert out["openShown"] == "block"
    assert out["popAfterMenu"] == "none", "one pet panel at a time"
    assert out["afterEscape"] == "none", "Escape closes the menu"
    assert out["afterInsidePress"] == "block", "pressing a row is not a click outside"
    assert out["afterOutsidePress"] == "none", "a click elsewhere closes the menu"
    assert out["focusAfterMouseMenu"] == 0, "a right-click menu must not pull typing focus onto the pet"
    assert out["focusAfterKeyMenu"] == 1, "a menu opened from the pet's key returns focus to the pet"
    assert out["focusAfterKeyRow"] == 2 and out["afterRow"] == "none", "choosing a row by key keeps focus"
    assert "Reset position" not in out["rows"], "no desktop pet here, so nothing to reset"
    assert any(row.startswith("Pet: ") and row != "Pet: spark" for row in out["rows"]), (
        f"the character is named, not shown as its id: {out['rows']}"
    )


TITLE_DRIVER = r"""
const res = { errors: [], payloads: [] };
try {
  window.pywebview = { api: {
    detach_companion: (p) => { res.payloads.push(p); return Promise.resolve({ ok: true }); },
    sync_companion: () => Promise.resolve({ ok: true }) } };
  _lastSessions = [{ session_id: "chat-0123456789abcdef", title: "" },
                   { session_id: "chat-named", title: "Trip plans" }];
  displayedChat = "chat-0123456789abcdef";
  await window.VoolCompanion.detachDesktop(true);
  displayedChat = "chat-named";
  await window.VoolCompanion.detachDesktop(true);
  // In the window: an untitled chat that is working still shows what the pet is doing.
  await window.VoolCompanion.returnToApp("docked");
  displayedChat = "chat-0123456789abcdef";
  window.VoolCompanion.consume(displayedChat, { type: "task.started", seq: 1 });
  window.VoolCompanion.consume(displayedChat, { type: "tool.started", seq: 2, tool: "web_search" });
  window.VoolCompanion.paintNow(Date.now() + 5000);
  const find = (n, cls) => n.className === cls ? n : (n.children || []).reduce((f, c) => f || find(c, cls), null);
  const bubble = find(document.body, "vn-bubble");
  res.bubble = { shown: bubble.style.display, title: find(bubble, "vn-bubble-title").textContent,
                 activity: find(bubble, "vn-bubble-activity").textContent };
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_an_untitled_chat_never_puts_a_random_code_in_the_pets_mouth() -> None:
    out = _run(TITLE_DRIVER)
    untitled, named = out["payloads"]
    assert "chatTitle" not in untitled, f"an id fragment reached the pet: {untitled.get('chatTitle')}"
    assert named["chatTitle"] == "Trip plans", "a real title still travels with the pet"
    bubble = out["bubble"]
    assert bubble["title"] == "New chat", f"no id in the in-app bubble either: {bubble['title']!r}"
    assert bubble["shown"] == "flex" and bubble["activity"], f"the activity still shows: {bubble}"


SPEECH_DRIVER = r"""
const res = { errors: [] };
try {
  const VC = window.VoolCompanion;
  const layer = document.body.children.find((c) => c.id === "companionLayer");
  const sr = layer.children.find((c) => c.className === "vn-sr");
  const sprite = layer.children.find((c) => c.attrs && c.attrs.role === "button");
  displayedChat = "done";
  VC.consume("done", { type: "task.started", seq: 1 });
  VC.consume("done", { type: "task.completed", seq: 2 });
  VC.paintNow(Date.now() + 20000);
  res.doneSpoken = sr.textContent;
  res.doneLabel = sprite.attrs["aria-label"];
  displayedChat = "bad";
  VC.consume("bad", { type: "task.started", seq: 1 });
  VC.consume("bad", { type: "task.failed", seq: 2 });
  VC.paintNow(Date.now() + 40000);
  res.failSpoken = sr.textContent;
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
"""


def test_an_ordinary_answer_is_not_announced_as_unknown() -> None:
    out = _run(SPEECH_DRIVER)
    assert "Unknown" not in out["doneSpoken"] and "not independently" not in out["doneSpoken"], (
        f"the conversation already announces the reply: {out['doneSpoken']!r}"
    )
    assert "Done" in out["doneLabel"] and "Unknown" not in out["doneLabel"], out["doneLabel"]
    assert out["failSpoken"], "a failure is still announced"


def test_customise_pet_from_settings_brings_the_drawer_to_the_front() -> None:
    class ChatWindow(_FakeWindow):
        def evaluate_js(self, script):
            self.scripts.append(script)
            return True

    class SettingsWindow(_FakeWindow):
        destroyed = 0

        def destroy(self) -> None:
            self.destroyed += 1

    api = _WindowApi()
    chat, settings = ChatWindow(), SettingsWindow()
    api.set_window(chat, _FakeWebview())
    api._settings_window = settings

    assert api.open_companion_appearance() == {"ok": True}
    assert settings.destroyed == 1, "Settings closes so the drawer is not opened behind it"
    assert chat.shown == 1

    # A drawer that did not open leaves Settings where it is.
    api = _WindowApi()
    settings = SettingsWindow()
    api.set_window(_FakeWindow(), _FakeWebview())
    api._settings_window = settings
    assert api.open_companion_appearance()["ok"] is False
    assert settings.destroyed == 0


def test_a_dropped_desktop_pet_update_is_sent_again(monkeypatch) -> None:
    sent: list = []

    class Webview:
        def evaluateJavaScript_completionHandler_(self, script, handler):
            sent.append((script, handler))

    class Loaded:
        def is_set(self) -> bool:
            return True

    window = types.SimpleNamespace(uid="pet", events=types.SimpleNamespace(loaded=Loaded()))
    cocoa = types.SimpleNamespace(BrowserView=types.SimpleNamespace(
        instances={"pet": types.SimpleNamespace(webview=Webview())}))
    platforms = types.ModuleType("webview.platforms")
    platforms.cocoa = cocoa
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)

    controller = pet_native.PetWindowController.__new__(pet_native.PetWindowController)
    controller._pywebview_window = window
    controller._tracker = types.SimpleNamespace(_interaction=(True, False))

    assert controller._report_interaction(True, False) is True
    script, handler = sent[-1]
    assert script == "window.VoolDesktopCompanion.interaction(true,false);true", (
        "a page that is not ready must fail the script, and one that ran must return a value"
    )
    assert callable(handler), "the result must be checked, not dropped"
    handler(None, None)
    assert controller._tracker._interaction == (True, False), "a script that ran stays reported"
    handler(None, "WKErrorDomain 4")
    assert controller._tracker._interaction is None, "a script that failed is re-sent next tick"
