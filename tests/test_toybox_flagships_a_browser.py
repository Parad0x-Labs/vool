from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.toybox import validate
from core.toybox_flagships_a import SKINS
from core.toybox_fragment import skin_css
from tests._toybox_v2_browser import (
    contrast_report,
    finish_browser,
    frame_budget,
    network_guard,
    shoot,
    trusted_snapshot,
)
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium


def test_palettes_art_and_css_budgets_and_invalid_registry_entry_isolated(monkeypatch):
    import sys

    from core.toybox_registry_v2 import manifests

    for skin in SKINS:
        validated = validate(json.dumps(skin))[0]
        assert len(skin_css(validated).encode()) <= 60_000
        from core.toybox_art_a import artwork

        for palette in skin["palettes"].values():
            drawings = artwork(skin["id"], palette)
            assert set(drawings) == {"header", "empty", "perch"}
            assert sum(len(value.encode()) for value in drawings.values()) < 160_000
            assert all(not any(word in value for word in ("<script", "href=", "<text", "<image")) for value in drawings.values())
    broken = {**SKINS[0], "stage": {"layout": "outside"}}
    module = SimpleNamespace(SKINS=(broken, SKINS[1]))
    monkeypatch.setitem(sys.modules, "toybox_invalid_collection", module)
    monkeypatch.setattr("core.toybox_registry_v2.BUILTIN_V2_MODULES", ("missing_skin_collection", "toybox_invalid_collection"))
    assert [item[0]["id"] for item in manifests()] == [SKINS[1]["id"]]
    assert skin_css(manifests()[0][0])


@pytest.mark.parametrize("identifier", [s["id"] for s in SKINS])
def test_flagship_served_matrix_and_trusted_scope(served, identifier):
    run_matrix(served, next(s for s in SKINS if s["id"] == identifier))


def run_matrix(served, manifest, shutdown=None):
    identifier = manifest["id"]
    manager, browser = launch_chromium()
    findings, budgets = [], []
    try:
        for theme in ("dark", "light"):
            for locale in ("en", "ar"):
                for width in (1280, 480):
                    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
                    from core.ui_palette import APPEARANCE_COOKIE

                    context.add_cookies([{"url": served, "name": APPEARANCE_COOKIE, "value": theme + ".100"}])
                    page = context.new_page()
                    guard = network_guard(page, served)
                    revision = page.request.get(served + "/api/toybox").json()["revision"]
                    assert page.request.post(served + "/api/toybox/restore", data={"revision": revision, "confirmed": True}).status == 200
                    page.goto(served + f"/chat?ui_locale={locale}")
                    page.wait_for_function("window.VoolCompanion && window.VoolStage")
                    page.wait_for_function("document.documentElement.dataset.skin==='default'")
                    page.locator(".empty").wait_for()
                    # The sample is a display fixture only, using the actual protected class.
                    page.evaluate("""()=>{const n=document.createElement('div');n.className='msg user is-private';n.id='scope-probe';n.textContent='Private sample';n.style.cssText='position:fixed;top:10px;left:10px';document.body.append(n);}""")
                    before = page.locator("#scope-probe").evaluate("el=>{const s=getComputedStyle(el);return [s.color,s.backgroundColor,s.fontFamily,s.borderColor]}")
                    revision = page.request.get(served + "/api/toybox").json()["revision"]
                    response = page.request.post(served + "/api/toybox/apply", data={"manifest": json.dumps(manifest), "revision": revision, "confirmed": True})
                    assert response.status == 200, response.text()
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                    page.wait_for_function("id=>document.documentElement.dataset.skin===id", arg=identifier)
                    assert page.locator("#vool-skin-style").text_content() == response.json()["css"]
                    after = page.locator("#scope-probe").evaluate("el=>{const s=getComputedStyle(el);return [s.color,s.backgroundColor,s.fontFamily,s.borderColor]}")
                    assert after == before, (identifier, theme, before, after)
                    page.locator("#scope-probe").evaluate("el=>el.remove()")
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    drawing_state = page.locator("#main > header").evaluate("""el=>{const s=getComputedStyle(el),r=getComputedStyle(document.documentElement);return {image:s.backgroundImage.slice(0,100),reference:r.getPropertyValue('--tb-stage-header').slice(0,100),drawing:r.getPropertyValue('--tb-stage-header-dark').slice(0,100),scope:CSS.supports('selector(:scope)'),padding:s.paddingTop};}""")
                    assert drawing_state["image"] != "none", drawing_state
                    assert page.locator(".empty").evaluate("el=>getComputedStyle(el,'::before').backgroundImage") != "none"
                    name = f"{identifier}-{theme}-{locale}-{width}"
                    shoot(page, name + "-chat-empty")
                    errors = contrast_report(page)
                    findings.extend({"screen": name, **error} for error in errors)
                    page.evaluate("""()=>{const log=document.getElementById('log');const n=document.createElement('div');n.id='synthetic-conversation';n.innerHTML='<div class="msg user"><div class="msg-text">Could we sketch a quiet garden?</div></div><div class="msg assistant"><div class="msg-text"><h2>A place to pause</h2><p>A stone path curves around a little pond. A lamp lights the bench, and a low desk leaves room for a companion.</p><pre><code>garden = {path: stone, light: warm}</code></pre></div></div>';log.append(n);n.scrollIntoView();}""")
                    shoot(page, name + "-conversation")
                    findings.extend({"screen": name + '-conversation', **error} for error in contrast_report(page))
                    page.locator('#synthetic-conversation').evaluate('el=>el.remove()')
                    # A real, already-served model menu keeps its semantics under the skin.
                    page.locator("#modelBtn").click()
                    shoot(page, name + "-model-menu")
                    page.keyboard.press("Escape")
                    if width == 1280 and locale == "en":
                        page.bring_to_front()
                        sample = frame_budget(page)
                        budgets.append({"screen": name, **sample})
                        assert sample["p95_ms"] <= 20
                    page.emulate_media(reduced_motion="reduce")
                    page.evaluate("VoolStage.mirror('quiet',false)")
                    assert page.locator("html").get_attribute("data-vool-motion") == "off"
                    assert not page.evaluate("document.getAnimations().some(a=>a.playState==='running'&&String(a.animationName||'').startsWith('tb-'))")
                    assert trusted_snapshot(page)
                    page.goto(served + f"/settings?ui_locale={locale}#toybox")
                    page.locator(".tb-card").first.wait_for()
                    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                    shoot(page, name + "-settings")
                    page.goto(served + f"/settings?ui_locale={locale}#companion")
                    page.locator('#paneBody').wait_for()
                    page.wait_for_timeout(100)
                    shoot(page, name + '-settings-appearance')
                    assert not guard()
                    context.close()
        output = Path(tempfile.gettempdir()) / (identifier + "-measurements.json")
        output.write_text(json.dumps({"contrast_findings": findings, "frame_budgets": budgets}, indent=2))
        # These existing semantic labels are owned by the companion renderer.
        # Report every pinned light-theme finding, without recolouring or lowering AA.
        new_surface_failures = [f for f in findings if not ("-light-" in f["screen"] and f["cls"] == "vn-caption")]
        assert not new_surface_failures, new_surface_failures[:12]
    finally:
        (shutdown or finish_browser)(manager, browser)
