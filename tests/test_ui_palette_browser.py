"""Rendered contrast and navigation checks for the shared dark UI palette."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    with rig.CapturingProvider() as provider:
        daemon = rig.ServedDaemon(tmp_path_factory.mktemp("palette") / "home", provider=provider)
        daemon.start(timeout=120)
        manager, browser = launch_chromium()
        try:
            yield daemon, browser
        finally:
            browser.close()
            manager.stop()
            daemon.stop()


def rgb(color):
    return tuple(int(n) for n in re.findall(r"[0-9]+", color)[:3])


def luminance(color):
    channels = [v / 255 for v in color]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722), strict=True))


def contrast(a, b):
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


def colors(page):
    return page.evaluate("""() => {
      const style=getComputedStyle(document.documentElement);
      const sample=name=>{
        const el=document.createElement('span');el.style.color='var('+name+')';
        document.body.appendChild(el);const c=getComputedStyle(el).color;el.remove();return c;
      };
      return Object.fromEntries(['--bg','--panel','--field','--ink','--muted','--accent',
        '--accent2','--accent-ink','--ok','--bad','--warn'].map(n=>[n,sample(n)]));
    }""")


def evidence(page, name):
    folder = os.environ.get("VOOL_THEME_EVIDENCE")
    if folder:
        out=Path(folder);out.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(out/name))


@pytest.mark.parametrize("path,ready", [
    ("/chat", "#input"), ("/settings#keys", ".nav-item"), ("/setup", "#nextBtn"),
])
def test_served_pages_share_readable_neutral_dark_chrome(served, path, ready):
    daemon, browser = served
    page = browser.new_page(viewport={"width":1280,"height":800})
    errors=[]
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(daemon.base_url+path,wait_until="domcontentloaded")
        page.wait_for_selector(ready)
        c={n:rgb(v) for n,v in colors(page).items()}
        for surface in ("--bg", "--panel", "--field"):
            assert max(c[surface])-min(c[surface]) <= 8
            assert max(c[surface]) < 50
            for text in ("--ink", "--muted", "--accent"):
                assert contrast(c[text],c[surface]) >= 4.5, (path,text,surface)
        assert c["--accent"][0] >= c["--accent"][1] >= c["--accent"][2]
        for fill in ("--accent", "--accent2"):
            assert contrast(c["--accent-ink"],c[fill]) >= 4.5
        assert c["--ok"] != c["--bad"] != c["--warn"]
        if path=="/chat":
            page.locator("#input").fill("A draft that stays here while opening Settings")
            button=page.locator("#send").evaluate("el=>getComputedStyle(el).color")
            assert rgb(button)==c["--accent-ink"]
            page.locator("#input").focus()
            page.evaluate("VoolCompanion.chooseCharacter('beetle');VoolCompanion.paintNow();")
        evidence(page, path.split('#')[0].strip('/')+"-dark.png")
        assert not errors
    finally:
        page.close()


def test_settings_frame_keeps_theme_and_unsent_chat_draft(served):
    daemon,browser=served
    page=browser.new_page(viewport={"width":1280,"height":800})
    page.add_init_script("window.open=()=>null;")
    try:
        page.goto(daemon.base_url+"/chat",wait_until="domcontentloaded")
        page.wait_for_selector("#input")
        draft="Compare providers later; do not send this draft"
        page.locator("#input").fill(draft)
        page.evaluate("VoolPageActions.openSettings()")
        frame=page.frame_locator("#settingsFrame")
        frame.locator("#back").wait_for()
        assert frame.locator("body").evaluate("el=>getComputedStyle(el).backgroundColor")==page.locator("body").evaluate("el=>getComputedStyle(el).backgroundColor")
        frame.locator("#back").click()
        page.wait_for_selector("#settingsFrameOverlay",state="hidden")
        assert page.locator("#input").input_value()==draft
        page.evaluate("VoolCompanion.openCharacterLab()")
        selected=page.locator(".vn-character-card.selected")
        selected.wait_for()
        assert rgb(selected.evaluate("el=>getComputedStyle(el).borderColor"))==rgb(colors(page)["--accent"])
        evidence(page,"character-picker-dark.png")
    finally:
        page.close()


def test_host_selected_light_setup_stays_readable(served):
    daemon,browser=served
    page=browser.new_page(viewport={"width":520,"height":620})
    try:
        page.goto(daemon.base_url+"/setup?theme=light",wait_until="domcontentloaded")
        page.wait_for_selector("#nextBtn")
        c={n:rgb(v) for n,v in colors(page).items()}
        assert min(c["--bg"])>220
        assert contrast(c["--ink"],c["--bg"])>=4.5
        assert contrast(c["--accent"],c["--panel"])>=4.5
        assert contrast(c["--accent-ink"],c["--accent"])>=4.5
        evidence(page,"setup-light-compact.png")
    finally:
        page.close()


@pytest.mark.parametrize("surface", ["earnings", "web0"])
def test_utility_documents_inherit_the_shared_palette(served, surface):
    from core.earnings_page import render_earnings_html
    from core.null_browser_page import render_null_browser_html

    _daemon,browser=served
    page=browser.new_page()
    # These are rendered-document CSS checks; no utility API or provider is contacted.
    page.route("**/*",lambda route:route.abort())
    try:
        page.set_content(render_earnings_html() if surface=="earnings" else render_null_browser_html())
        assert "__VOOL_PALETTE_CSS__" not in page.content()
        c={n:rgb(v) for n,v in colors(page).items()}
        assert contrast(c["--accent"],c["--bg"])>=4.5
        assert contrast(c["--muted"],c["--panel"])>=4.5
        assert c["--accent"][0]>=c["--accent"][1]>=c["--accent"][2]
    finally:
        page.close()
