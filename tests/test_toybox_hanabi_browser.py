from __future__ import annotations

import json

from core.toybox import validate
from core.toybox_fragment import skin_css
from core.toybox_hanabi import SKINS
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_flagships_a_browser import run_matrix


def test_hanabi_validates_with_wallpaper_chrome_and_budgets():
    skin = validate(json.dumps(SKINS[0]))[0]
    assert skin["stage"]["controls"] == "brush" and skin["stage"]["scene"]["id"] == "stars"
    css = skin_css(skin)
    # A whole-app wallpaper is the heaviest skin class; its stated budget is its own.
    assert len(css.encode()) <= 128_000
    for marker in ("body::before", "--tb-wall:", "clip-path:polygon(0 0,calc(100% - 12px)",
                   "box-shadow:0 4px 0", "input[type=checkbox]:checked", "scrollbar-thumb"):
        assert marker in css, marker
    for palette in skin["palettes"].values():
        from core.toybox_hanabi import artwork

        drawings = artwork(skin["id"], palette)
        assert set(drawings) == {"header", "empty"}
        assert sum(len(value.encode()) for value in drawings.values()) < 40_000
        assert all("svg" in value and not any(word in value for word in ("<script", "href=", "<text", "<image"))
                   for value in drawings.values())
    # the wallpaper chrome belongs to this skin alone
    from core.toybox_pixel import SKINS as DAWN_SKINS

    other_css = skin_css(validate(json.dumps(DAWN_SKINS[0]))[0])
    assert len(other_css.encode()) <= 60_000 and "tb-wall" not in other_css


def test_hanabi_registered_as_a_builtin_choice():
    from core.toybox_registry_v2 import manifests

    assert "hanabi-sky" in [item[0]["id"] for item in manifests()]


def test_hanabi_flagship_served_matrix(served):
    run_matrix(served, SKINS[0])


def test_hanabi_wallpaper_cel_buttons_and_firework(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served + "/chat")
        page.wait_for_function("window.VoolToybox && window.VoolStage && window.VoolCompanion")
        revision = page.request.get(served + "/api/toybox").json()["revision"]
        assert page.request.post(served + "/api/toybox/apply", data={
            "manifest": json.dumps(SKINS[0]), "revision": revision, "confirmed": True}).status == 200
        page.evaluate("window.dispatchEvent(new Event('focus'))")
        page.wait_for_function("document.documentElement.dataset.skin==='hanabi-sky'")
        # The wallpaper is behind the whole app (fixed, z-index -1, real art).
        wallpaper = page.evaluate("""()=>{const s=getComputedStyle(document.body,'::before');
         return {image:s.backgroundImage.slice(0,40),pos:s.position,z:s.zIndex};}""")
        assert wallpaper["image"].startswith("url(") and wallpaper["pos"] == "fixed" and wallpaper["z"] == "-1"
        assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgba(0, 0, 0, 0)"
        # Cel buttons: cut corners, hard offset shadow, bold weight.
        button = page.evaluate("""()=>{const el=document.getElementById('newChat');
         const s=getComputedStyle(el);return {clip:s.clipPath,shadow:s.boxShadow,weight:s.fontWeight,bg:s.backgroundColor};}""")
        assert button["clip"].startswith("polygon") and "0px 4px" in button["shadow"] and int(button["weight"]) >= 700
        # The app floats: the main column is an outlined rounded card with margin.
        card = page.evaluate("""()=>{const s=getComputedStyle(document.getElementById('main'));
         return {radius:s.borderRadius,margin:s.margin,shadow:s.boxShadow.slice(0,30)};}""")
        assert card["margin"] not in ("0px", "") and card["radius"] != "0px" and "0px 10px" in card["shadow"]
        # The live header draws the firework: it animates while streaming and bursts on success.
        page.wait_for_selector("#tb-effect")
        page.evaluate("VoolCompanion.consume('run',{type:'task.started'});VoolStage.mirror('run',true)")
        page.wait_for_timeout(400)
        streaming = page.locator("#tb-effect").evaluate("el=>el.toDataURL()")
        page.evaluate("""VoolCompanion.consume('done',{type:'task.started'});VoolCompanion.consume('done',{type:'verification.completed',review_state:'passed'});
         VoolCompanion.consume('done',{type:'task.completed'});VoolStage.mirror('done',false)""")
        page.wait_for_function("document.documentElement.dataset.voolActivity==='success'")
        page.wait_for_timeout(150)
        success = page.locator("#tb-effect").evaluate("el=>el.toDataURL()")
        assert streaming != success, "the firework must burst on verified completion"
        # Trusted islands keep VOOL's own look under the wallpaper skin.
        island = page.evaluate("""()=>{const bar=document.createElement('div');bar.id='permBar';bar.textContent='Permission needed';
         document.body.append(bar);const out=getComputedStyle(bar).boxShadow;bar.remove();return out;}""")
        assert island == "none"
    finally:
        browser.close()
        manager.stop()
