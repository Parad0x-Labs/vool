"""Theme and text size, proven on the served chat and Settings pages in a real browser.

- With no choice made, both pages render exactly as before: dark, normal text, no theme attribute.
- Settings > Appearance switches the theme and the text size; the choice is stored in a cookie,
  the open chat window follows at once without a reload, and a fresh load is served already in the
  chosen look (no dark flash: the server writes the attribute on ``<html>``).
- Light and "Same as this computer" use real light colours, and the latter follows the operating system.
- Text size reaches the assistant's replies, not only the person's own bubbles.
- Light leaves no dark-only spot behind: no dark panel, and toasts stay readable.
- A tampered cookie falls back to the default instead of breaking the page; another app's cookie on
  the same host does not make the server and the page disagree; a broken value still shows the
  controls so the person can reset it.
"""
from __future__ import annotations

import os

from tests import _reader_served_rig as rig
from tests import served_browser

BG = "getComputedStyle(document.body).backgroundColor"
DARK_SPOTS = """() => [...document.querySelectorAll('body *')].filter(el => {
  if (el.tagName === 'IMG' || el.tagName === 'CANVAS') return false;
  const r = el.getBoundingClientRect(); if (!r.width || !r.height) return false;
  const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
  const m = (cs.backgroundColor.match(/[\\d.]+/g) || []).map(Number);
  if (m.length < 3 || (m.length > 3 && m[3] < 0.5)) return false;
  return 0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2] < 60;
}).map(el => el.tagName + (el.id ? '#' + el.id : '') + '.' + String(el.className || ''))"""
LOG_FONT = "parseFloat(getComputedStyle(document.getElementById('log')).fontSize)"
REPLY_FONT = "parseFloat(getComputedStyle(document.querySelector('.msg.assistant')).fontSize)"
TOAST_COLOURS = """() => { toast('Copied'); const cs = getComputedStyle(document.getElementById('nToast'));
  return [cs.color, cs.backgroundColor]; }"""


def _luma(css_rgb: str) -> float:
    parts = [float(p) for p in css_rgb[css_rgb.index("(") + 1:css_rgb.index(")")].split(",")[:3]]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def _contrast(fg: str, bg: str) -> float:
    def lum(css):
        parts = [float(p) / 255 for p in css[css.index("(") + 1:css.index(")")].split(",")[:3]]
        parts = [p / 12.92 if p <= 0.03928 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
        return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]
    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _guard(page, base_url):
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_()
               if route.request.url.startswith(base_url) else route.abort())
    return errors


def _shot(page, name):
    folder = os.environ.get("VOOL_APPEARANCE_SHOTS")
    if folder:
        page.screenshot(path=os.path.join(folder, name), full_page=False)


def test_theme_and_text_size_switch_live_and_survive_a_reload(tmp_path):
    with rig.CapturingProvider(default="The lantern glowed.") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider,
                                  env_extra={"VOOL_INSTALL_PROFILE": "hybrid-fallback"})
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme="dark")
                chat = context.new_page()
                chat_errors = _guard(chat, daemon.base_url)
                chat.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                chat.wait_for_selector("#input", timeout=10000)
                # Default: unchanged dark page.
                assert chat.evaluate("document.documentElement.getAttribute('data-theme')") is None
                dark_bg = chat.evaluate(BG)
                assert _luma(dark_bg) < 60, dark_bg
                normal_font = chat.evaluate(LOG_FONT)
                assert normal_font == 14

                settings = context.new_page()
                settings_errors = _guard(settings, daemon.base_url)
                settings.goto(daemon.base_url + "/settings#companion", wait_until="domcontentloaded")
                settings.locator(".nav-item[data-group='companion']").click()
                theme = settings.locator("select[name='appearanceTheme']")
                text = settings.locator("select[name='appearanceText']")
                theme.wait_for(timeout=10000)
                assert theme.input_value() == "dark" and text.input_value() == "100"

                theme.select_option("light")
                text.select_option("125")
                assert "Saved on this computer." in settings.locator("[data-row='appearance_theme']").inner_text()
                assert _luma(settings.evaluate(BG)) > 180
                cookies = {c["name"]: c["value"] for c in context.cookies(daemon.base_url)}
                assert cookies.get("vool_appearance") == "light.125"
                _shot(settings, "settings-light.png")

                # The open chat window follows without a reload.
                chat.wait_for_function("document.documentElement.getAttribute('data-theme') === 'light'", timeout=5000)
                assert _luma(chat.evaluate(BG)) > 180
                assert chat.evaluate(LOG_FONT) == normal_font * 1.25

                # A fresh load is served already light: the attribute is in the HTML itself.
                with urlopen_cookie(daemon.base_url + "/chat", "vool_appearance=light.125") as html:
                    assert '<html lang="en" dir="ltr" data-theme="light" style="--vool-text-scale:1.25">' in html
                chat.reload(wait_until="domcontentloaded")
                chat.wait_for_selector("#input", timeout=10000)
                assert chat.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
                chat.locator("#input").fill("Write one friendly sentence about a harbor lantern.")
                chat.locator("#send").click()
                chat.wait_for_function("view.run && view.run.released", timeout=30000)
                _shot(chat, "chat-light.png")
                # Larger text reaches the reply itself, not only the person's own words.
                assert chat.evaluate(REPLY_FONT) == 15 * 1.25
                fg, bg = chat.evaluate(TOAST_COLOURS)
                assert _contrast(fg, bg) >= 4.5, (fg, bg)
                # No part of the light page keeps a dark-theme panel (setup line, model lane, pet
                # caption). Images keep their own colours.
                dark_spots = chat.evaluate(DARK_SPOTS)
                assert dark_spots == [], dark_spots

                # "Same as this computer" follows the operating system's choice.
                theme.select_option("system")
                chat.wait_for_function("document.documentElement.getAttribute('data-theme') === 'system'", timeout=5000)
                assert _luma(chat.evaluate(BG)) < 60          # the OS says dark
                chat.emulate_media(color_scheme="light")
                assert _luma(chat.evaluate(BG)) > 180         # the OS says light

                # Back to dark and normal: the page is exactly the default again.
                theme.select_option("dark")
                text.select_option("100")
                chat.wait_for_function("document.documentElement.getAttribute('data-theme') === null", timeout=5000)
                assert chat.evaluate(BG) == dark_bg
                assert chat.evaluate(LOG_FONT) == normal_font
                assert chat.evaluate(REPLY_FONT) == 15
                assert not chat_errors and not settings_errors, (chat_errors, settings_errors)
                context.close()

                # A tampered cookie is ignored, not trusted into the markup.
                with urlopen_cookie(daemon.base_url + "/chat", 'vool_appearance="><script>x</script>') as html:
                    assert "<script>x" not in html
                    assert '<html lang="en" dir="ltr">' in html
                # Another local app's cookie (a JSON value) ahead of ours: the server still serves
                # the person's choice, as the page script reads it.
                with urlopen_cookie(daemon.base_url + "/chat", 'prefs={"a":1}; vool_appearance=light.125') as html:
                    assert '<html lang="en" dir="ltr" data-theme="light" style="--vool-text-scale:1.25">' in html

                # A broken value in the cookie still shows the controls, at the default, so the
                # person can put it right from Settings.
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                host = daemon.base_url.split("//", 1)[1].split(":", 1)[0]
                context.add_cookies([{"name": "vool_appearance", "value": "light%E0.125", "domain": host, "path": "/"}])
                settings = context.new_page()
                settings_errors = _guard(settings, daemon.base_url)
                settings.goto(daemon.base_url + "/settings#companion", wait_until="domcontentloaded")
                settings.locator(".nav-item[data-group='companion']").click()
                theme = settings.locator("select[name='appearanceTheme']")
                theme.wait_for(timeout=10000)
                assert theme.input_value() == "dark"
                theme.select_option("light")
                assert settings.evaluate("document.documentElement.getAttribute('data-theme')") == "light"
                assert not settings_errors, settings_errors
                context.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()


class urlopen_cookie:
    def __init__(self, url, cookie):
        from urllib.request import Request
        self._request = Request(url, headers={"Cookie": cookie})

    def __enter__(self):
        from urllib.request import urlopen
        with urlopen(self._request, timeout=10) as response:
            return response.read().decode("utf-8")

    def __exit__(self, *exc):
        return False


def test_cookie_reading_ignores_other_cookies_and_bad_values():
    from core.ui_palette import appearance_from_cookie_header

    assert appearance_from_cookie_header(None) == ("dark", 100)
    assert appearance_from_cookie_header("vool_appearance=system.112") == ("system", 112)
    for other in ('x={"a":1}', "foo=bar baz", "bad cookie", 'x="unterminated'):
        assert appearance_from_cookie_header(f"{other}; vool_appearance=light.125") == ("light", 125), other
    assert appearance_from_cookie_header("my_vool_appearance=light.125") == ("dark", 100)
    assert appearance_from_cookie_header("vool_appearance=light%E0.125") == ("dark", 100)


def test_setup_light_uses_the_same_warm_colours_as_the_chat():
    # The first screen of the product must not look like a different app in Light: no later rule in
    # the setup page may override the shared warm tokens with the old cool grey-blue ones.
    from core.ui_palette import LIGHT_ONLY_PALETTE_CSS
    from core.vool_setup_page import render_vool_setup_html

    html = render_vool_setup_html(appearance=("light", 100))
    assert LIGHT_ONLY_PALETTE_CSS in html
    assert html.count(':root[data-theme="light"] {') == 1
    assert "--bg:#f3f1ed" in html.split(':root[data-theme="light"] {')[1].split("}", 1)[0]
