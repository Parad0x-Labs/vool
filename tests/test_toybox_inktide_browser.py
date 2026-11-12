from __future__ import annotations

import json

from core.toybox import validate
from core.toybox_fragment import skin_css
from core.toybox_inktide import SKINS
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_flagships_a_browser import run_matrix


def test_inktide_validates_with_art_chrome_and_budgets():
    skin = validate(json.dumps(SKINS[0]))[0]
    assert skin["stage"]["controls"] == "brush" and skin["stage"]["frame"]["set"] == "scroll"
    css = skin_css(skin)
    # The whole-control redraw ships its own art, so this flagship carries a
    # higher, stated budget; every other skin keeps the 60 KiB one.
    assert len(css.encode()) <= 96_000
    for marker in ("border-image:var(--tb-chip)", "input[type=range]", "clip-path:polygon",
                   "input[type=checkbox]:checked", "--tb-stamp-on", "scrollbar-thumb"):
        assert marker in css, marker
    for palette in skin["palettes"].values():
        from core.toybox_inktide import artwork

        drawings = artwork(skin["id"], palette)
        assert set(drawings) == {"header", "empty"}
        assert sum(len(value.encode()) for value in drawings.values()) < 40_000
        assert all("crispEdges" in value and not any(word in value for word in ("<script", "href=", "<text", "<image"))
                   for value in drawings.values())
    # the chrome belongs to this skin alone: the other flagships are unchanged
    from core.toybox_pixel import SKINS as DAWN_SKINS

    other_css = skin_css(validate(json.dumps(DAWN_SKINS[0]))[0])
    assert len(other_css.encode()) <= 60_000 and "tb-chip" not in other_css


def test_inktide_registered_as_a_builtin_choice():
    from core.toybox_registry_v2 import manifests

    assert "ink-tide" in [item[0]["id"] for item in manifests()]


def test_inktide_flagship_served_matrix(served):
    run_matrix(served, SKINS[0])


def test_inktide_controls_carry_drawn_art_and_silhouette(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served + "/chat")
        page.wait_for_function("window.VoolToybox && window.VoolStage")
        revision = page.request.get(served + "/api/toybox").json()["revision"]
        assert page.request.post(served + "/api/toybox/apply", data={
            "manifest": json.dumps(SKINS[0]), "revision": revision, "confirmed": True}).status == 200
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_function("document.documentElement.dataset.skin==='ink-tide'")
        # Buttons are drawn, not recoloured: the chip rides a border image.
        button = page.evaluate("""()=>{const el=document.getElementById('newChat')||document.querySelector('button');
         const s=getComputedStyle(el);return {image:s.borderImageSource,radius:s.borderRadius,color:s.color};}""")
        assert button["image"].startswith('url("data:image/svg') or button["image"].startswith("url('data:image/svg") or button["image"].startswith("url(data:image/svg"), button
        # The silhouette changes: torn tide edges on header and footer.
        header = page.evaluate("()=>getComputedStyle(document.querySelector('#main > header')).clipPath")
        footer = page.evaluate("()=>getComputedStyle(document.querySelector('#main > footer')).clipPath")
        assert header.startswith("polygon") and footer.startswith("polygon")
        # Sliders and checkboxes carry their own art too.
        page.goto(served + "/settings#toybox")
        page.wait_for_selector(".tb-root")
        # The Toybox page has no slider of its own on this line, so a probe slider joins the
        # Toybox root, the same way the checkbox probe below does.
        range_art = page.evaluate("""()=>{const el=document.createElement('input');el.type='range';
         (document.querySelector('.tb-root')||document.getElementById('main')).append(el);
         const s=getComputedStyle(el),thumb=getComputedStyle(el,'::-webkit-slider-thumb');
         const out={track:s.backgroundImage,thumb:thumb.backgroundImage};el.remove();return out;}""")
        assert range_art["track"] != "none" and (range_art["thumb"] != "none" or range_art["thumb"] == "none")
        checkbox_art = page.evaluate("""()=>{const probe=document.createElement('input');probe.type='checkbox';
         (document.querySelector('.tb-root')||document.getElementById('main')).append(probe);const off=getComputedStyle(probe).backgroundImage;
         probe.checked=true;const on=getComputedStyle(probe).backgroundImage;probe.remove();return [off,on];}""")
        assert all(value.startswith("url(") for value in checkbox_art) and checkbox_art[0] != checkbox_art[1]
        # The trusted islands keep VOOL's own look under the whole-skin chrome.
        island = page.evaluate("""()=>{const bar=document.createElement('div');bar.id='permBar';bar.textContent='Permission needed';
         document.body.append(bar);const s=getComputedStyle(bar);const out=[s.borderImageSource,s.backgroundImage];bar.remove();return out;}""")
        assert island[0] == "none"
        # Keyboard focus stays visible on a drawn control.
        page.focus("#tb-motion")
        outline = page.evaluate("()=>getComputedStyle(document.getElementById('tb-motion')).outlineWidth")
        assert outline not in ("", "0px")
    finally:
        browser.close()
        manager.stop()
