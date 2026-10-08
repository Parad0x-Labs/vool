"""Real-browser layout and typed-chat bubble regressions for both pet hosts."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core.companion_presentation_fragment import render_companion_fragment
from core.companion_world_fragment import render_desktop_companion_html
from installer.bundle import pet_native
from installer.bundle.vool_window import COMPANION_WINDOW_FLAGS
from tests.served_browser import launch_chromium


@pytest.fixture(scope="module")
def browser():
    manager, instance = launch_chromium()
    yield instance
    instance.close()
    manager.stop()


def inline(page, title="Market research chat"):
    page.set_content("<!doctype html><html><meta charset='utf-8'><body style='margin:0;"
                     "font:14px/1.5 system-ui;background:#101216;color:white'>"
                     "<script>let displayedChat='chat-a';window.__titles={'chat-a':"
                     + json.dumps(title).replace("<", "\\u003c")
                     + ", 'chat-b':'Another chat'};function chatTitleFor(id){return __titles[id]||id;}</script>"
                     + render_companion_fragment() + "</body></html>")
    page.wait_for_function("window.VoolCompanion && document.querySelector('#companionLayer canvas')")


def rect(page, selector):
    return page.locator(selector).evaluate("el => {const r=el.getBoundingClientRect();return "
                                           "{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};}")


def save_evidence(page, name):
    destination = os.environ.get("VOOL_PET_EVIDENCE")
    if destination:
        out = Path(destination)
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / name), omit_background=True)


@pytest.mark.parametrize("size", [(800, 600), (520, 620)])
def test_pet_and_wide_bubble_fit_at_every_corner(browser, size):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        inline(page, "A long named chat for checking bubble edges 🐦 " * 3)
        page.evaluate("VoolCompanion.consume('chat-a',{type:'task.started',seq:1});"
                      "VoolCompanion.consume('chat-a',{type:'tool.started',seq:2,tool:'web_search'});")
        for x, y in [(0, 0), (9999, 0), (0, 9999), (9999, 9999)]:
            page.evaluate("([x,y])=>{const p=VoolCompanion.pos();p.mode='free';p.x=x;p.y=y;"
                          "VoolCompanion.applyPos();VoolCompanion.paintNow();}", [x, y])
            pet = rect(page, "#companionLayer .vool-ninja")
            bubble = rect(page, ".vn-bubble")
            for shape in (pet, bubble):
                assert shape["x"] >= 0 and shape["y"] >= 0, shape
                assert shape["right"] <= size[0] and shape["bottom"] <= size[1], shape
            assert page.locator("#companionLayer canvas").evaluate("el=>getComputedStyle(el).imageRendering") == "pixelated"
        assert rect(page, "#companionLayer canvas")["width"] == 144
        save_evidence(page, f"inline-edge-{size[0]}.png")
        assert not errors
    finally:
        page.close()


def test_bubble_tracks_selected_chat_and_clears_previous_work(browser):
    page = browser.new_page(viewport={"width": 800, "height": 600})
    try:
        inline(page)
        page.evaluate("VoolCompanion.consume('chat-a',{type:'task.started',seq:1});"
                      "VoolCompanion.consume('chat-a',{type:'tool.started',seq:2,tool:'read_file'});"
                      "VoolCompanion.paintNow();")
        assert page.locator(".vn-bubble-title").inner_text() == "Market research chat"
        assert page.locator(".vn-bubble-activity").inner_text()
        assert page.evaluate("VoolCompanion.paintState().activity") == "read"
        page.evaluate("displayedChat='chat-b';VoolCompanion.paintNow();")
        assert page.locator(".vn-bubble-title").inner_text() == "Another chat"
        assert page.locator(".vn-bubble-activity").inner_text() == ""
        page.evaluate("displayedChat='chat-a';VoolCompanion.paintNow();")
        assert page.evaluate("VoolCompanion.paintState().activity") == "read"
        save_evidence(page, "inline-reading.png")
    finally:
        page.close()


def test_unbound_pet_has_no_invented_chat_title(browser):
    page = browser.new_page()
    try:
        inline(page)
        page.evaluate("displayedChat='';VoolCompanion.paintNow();")
        assert page.locator(".vn-bubble").is_hidden()
    finally:
        page.close()


def test_hostile_title_is_bounded_text_in_both_hosts(browser):
    title = "<img src=x onerror='window.injected=true'>\n" + "🐦" * 100
    page = browser.new_page()
    try:
        inline(page, title)
        assert page.locator(".vn-bubble-title img").count() == 0
        text = page.locator(".vn-bubble-title").inner_text()
        assert len(text) <= 42 and text.endswith("…")
        assert "\n" not in text and page.evaluate("window.injected || false") is False
        page.set_content(render_desktop_companion_html({"state": "tool", "chatTitle": title, "caption": "Reading files…"}))
        assert page.locator("#bubbleTitle img").count() == 0
        assert len(page.locator("#bubbleTitle").inner_text()) <= 42
        assert page.evaluate("window.injected || false") is False
    finally:
        page.close()


def test_native_document_fits_window_and_matches_hit_regions(browser):
    w, h = COMPANION_WINDOW_FLAGS["width"], COMPANION_WINDOW_FLAGS["height"]
    assert (w, h) == (pet_native.PET_WINDOW_WIDTH, pet_native.PET_WINDOW_HEIGHT)
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(render_desktop_companion_html({"state":"tool", "character":"beetle", "chatTitle":"Market research chat", "caption":"Reading files…", "activity":"read"}))
        page.wait_for_function("window.VoolDesktopCompanion")
        canvas, bubble, controls = [rect(page, s) for s in ("#pet", "#bubble", "#controls")]
        for shape in (canvas, bubble, controls):
            assert shape["x"] >= 0 and shape["y"] >= 0, shape
            assert shape["right"] <= w and shape["bottom"] <= h, shape
        assert controls["bottom"] <= bubble["y"]
        assert bubble["bottom"] < canvas["y"]
        assert canvas["width"] == canvas["height"] == 144
        for shape, hit in zip((canvas, bubble, controls), pet_native.pet_hit_rects(), strict=True):
            cx, cy = shape["x"] + shape["width"] / 2, h - shape["y"] - shape["height"] / 2
            assert hit[0] <= cx <= hit[0] + hit[2]
            assert hit[1] <= cy <= hit[1] + hit[3]
            assert pet_native.point_hits_pet(cx, cy, (0, 0, w, h))
        for label in ("#bubbleTitle", "#bubbleActivity"):
            bounds = rect(page, label)
            assert bubble["y"] < bounds["y"] and bounds["bottom"] < bubble["bottom"]
            assert page.locator(label).evaluate("el=>el.scrollHeight<=el.clientHeight")
        save_evidence(page, "desktop-document.png")
        page.evaluate("VoolDesktopCompanion.update({state:'idle',chatTitle:'Another chat'});")
        assert page.locator("#bubbleTitle").inner_text() == "Another chat"
        assert page.locator("#bubbleActivity").inner_text() == ""
        assert not errors
    finally:
        page.close()


@pytest.mark.parametrize("unbound", [
    {"state": "idle", "chatTitle": "", "activity": ""},
    {"state": "idle"},
])
def test_native_bridge_replaces_chat_and_activity_snapshot(browser, unbound):
    """An accepted empty snapshot clears the previous chat through the real bridge."""
    from installer.bundle.vool_window import _WindowApi

    page = browser.new_page(viewport={"width": 208, "height": 236})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_content(render_desktop_companion_html({"motion": "reduced"}))
        page.wait_for_function("window.VoolDesktopCompanion")
        # Use the production bridge/normalizer with a browser document in place of WKWebView.
        class BrowserWindow:
            def evaluate_js(self, script):
                return page.evaluate(script)

        api = _WindowApi()
        api._companion_window = BrowserWindow()

        def sync(payload):
            assert api.sync_companion(payload) == {"ok": True}

        sync({"state": "tool", "chatTitle": "Chat A", "activity": "read", "caption": "Reading files"})
        assert page.locator("#bubbleTitle").inner_text() == "Chat A"
        assert page.evaluate("vcwState.activity") == "read"
        sync(unbound)
        assert page.locator("#bubbleTitle").text_content() == ""
        assert page.locator("#bubbleActivity").text_content() == ""
        assert page.locator("#bubble").is_hidden()
        assert page.evaluate("vcwState.activity") == ""
        sync({"state": "thinking", "chatTitle": "Chat B"})
        assert page.locator("#bubbleTitle").inner_text() == "Chat B"
        assert page.locator("#bubble").is_visible()
        assert page.evaluate("vcwState.activity") == ""
        for activity in ("read", "search", "dig", "code", "exec", "test", "watch"):
            sync({"state": "tool", "chatTitle": "Chat B", "activity": activity})
            assert page.evaluate("vcwState.activity") == activity
        sync({"state": "tool", "chatTitle": "Chat B", "activity": "<img src=x>"})
        assert page.evaluate("vcwState.activity") == ""
        assert page.locator("#bubbleTitle").inner_text() == "Chat B"
        assert page.locator("#bubble img").count() == 0
        assert page.evaluate("vcwState.motion") == "reduced"
        sync(None)
        assert page.locator("#bubble").is_hidden()
        assert page.evaluate("vcwState.activity") == ""
        assert not errors
        save_evidence(page, "native-snapshot-cleared.png")
    finally:
        page.close()
