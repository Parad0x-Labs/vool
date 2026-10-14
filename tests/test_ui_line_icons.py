"""VOOL's own controls use one line-icon style, never a mix of colour emoji, glyphs and nothing.

Before: in the Home menu "Files" had a colour folder emoji, "Report a problem" a speech bubble,
"Settings" a gear glyph and "Skills & Plugins" and "Contacts" nothing; the Settings sections mixed
glyphs with colour emoji (calendar, bell); the header's pin and bell were colour emoji.

- every icon name the pages use has a drawing (a missing one would render as an empty square);
- the Home rows carry no icon inside their translated words, in any of the 17 languages;
- in a real browser every Home row and every Settings section shows the same masked line icon.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.ui_icons import ICON_CSS, ICON_NAMES
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html
from tests import _reader_served_rig as rig
from tests import served_browser

CATALOGS = Path(__file__).resolve().parents[1] / "core" / "i18n" / "catalogs"
HOME_KEYS = ("sidebar.addons", "sidebar.files", "sidebar.contacts", "sidebar.settings", "sidebar.report", "sidebar.home")
PICTOGRAPH = re.compile("[⌀-⏿☀-➿\U0001F000-\U0001FAFF]")


def test_every_icon_the_pages_name_has_a_drawing():
    used = set()
    for html in (render_vool_chat_html(), render_vool_settings_html()):
        used |= set(re.findall(r'data-ico="([a-z-]+)"', html))
        used |= set(re.findall(r"dataset\.ico = '([a-z-]+)'", html))
        used |= set(re.findall(r"\b[a-z]+: '([a-z-]+)'", html.split("const SETTINGS_NAV_ICONS = {", 1)[-1].split("};", 1)[0]))
    assert used, "no icons found"
    missing = sorted(name for name in used if name not in ICON_NAMES)
    assert missing == [], missing
    for name in ICON_NAMES:
        assert f'.vi[data-ico="{name}"]' in ICON_CSS


def test_home_rows_carry_no_icon_in_their_words_in_any_language():
    found = {}
    for path in sorted(CATALOGS.glob("*.json")):
        messages = json.loads(path.read_text(encoding="utf-8"))["messages"]
        for key in HOME_KEYS:
            if PICTOGRAPH.search(str(messages.get(key, ""))):
                found[f"{path.stem}:{key}"] = messages[key]
    assert found == {}, found


ICON_OF = """els => els.map(el => {
  const target = el.classList.contains('vi') ? el : el.querySelector('.vi');
  if (!target) return null;
  const before = getComputedStyle(target, '::before');
  const mask = before.webkitMaskImage || before.maskImage || '';
  return {ico: target.dataset.ico, mask: mask.startsWith('url('), w: Math.round(parseFloat(before.width))};
})"""


def test_home_menu_and_settings_sections_show_the_same_line_icons(tmp_path):
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", lambda route: route.continue_()
                           if route.request.url.startswith(daemon.base_url) else route.abort())
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#homeToggle", timeout=10000)
                page.locator("#homeToggle").click()
                rows = page.locator("#homeOptions .side-foot-btn").evaluate_all(ICON_OF)
                assert len(rows) >= 5
                assert all(r and r["mask"] for r in rows), rows
                assert len({r["w"] for r in rows}) <= 2, rows   # report row is a size smaller
                texts = page.locator("#homeOptions .side-foot-btn").all_inner_texts()
                assert not any(PICTOGRAPH.search(t) for t in texts), texts

                page.goto(daemon.base_url + "/settings#general", wait_until="domcontentloaded")
                page.wait_for_selector(".nav-item .ico", timeout=10000)
                sections = page.locator(".nav-item").evaluate_all(ICON_OF)
                assert len(sections) >= 10
                assert all(s and s["mask"] for s in sections), sections
                assert len({s["w"] for s in sections}) == 1, sections
                assert not errors, errors
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
