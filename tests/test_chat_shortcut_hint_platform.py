"""The chat page's shortcut hints name the modifier the viewer's keyboard carries.

Every chat shortcut handler accepts ``metaKey || ctrlKey``, but the hint bar under the composer
and the command palette's key column printed the macOS command glyph on every platform: a Linux
(WebKitGTK) or Windows (WebView2) window told the user to press a key their keyboard does not
have. The page served by the production ``/chat`` route is loaded in Chromium under each
platform's navigator identity. macOS must render exactly as before, every other platform must
name Ctrl, and pressing the hinted combination must reach the palette it advertises.

Off Apple platforms Ctrl+N is advertised only inside the desktop shell (pywebview present): in a
plain browser tab Ctrl+N belongs to the browser (it opens a window), so the hint withholds it
there while every other cap still names Ctrl. Both shells are loaded and pinned.

Availability lives in ``tests.served_browser.launch_chromium()`` (fails under the gate, skips
outside it); nothing here skips on its own.
"""
from __future__ import annotations

import json

import pytest

from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.web.api.service import dispatch_get
from tests import served_browser

HINT_KEYS = (
    ("K", "palette.hint_palette"),
    ("N", "palette.hint_new_chat"),
    ("J", "palette.hint_activity"),
    ("/", "palette.hint_pet"),
    ("F", "palette.hint_search_chats"),
    (",", "palette.hint_settings"),
)

# navigator identity of each shell the product ships in: (navigator.platform,
# navigator.userAgentData.platform or None where the engine has no such object, user agent).
PLATFORMS = {
    "macos-wkwebview": (
        "MacIntel",
        None,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)",
    ),
    "linux-webkitgtk": (
        "Linux x86_64",
        None,
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    ),
    "windows-webview2": (
        "Win32",
        "Windows",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    ),
}

# The macOS hint bar as it rendered before platform-aware labels: kept byte-exact.
MACOS_EN_HINT_HTML = (
    "<kbd>⌘K</kbd> palette · <kbd>⌘N</kbd> new chat · <kbd>⌘J</kbd> activity"
    " · <kbd>⌘/</kbd> pet appearance · <kbd>⌘F</kbd> search chats"
    " · <kbd>⌘,</kbd> settings · <kbd>Esc</kbd> stop"
)
CTRL_EN_HINT_TEXT = (
    "Ctrl+K palette · Ctrl+N new chat · Ctrl+J activity · Ctrl+/ pet appearance"
    " · Ctrl+F search chats · Ctrl+, settings · Esc stop"
)
# The same bar in a plain browser tab: Ctrl+N is the browser's own key there, so it is withheld.
CTRL_EN_BROWSER_TAB_HINT_TEXT = (
    "Ctrl+K palette · Ctrl+J activity · Ctrl+/ pet appearance"
    " · Ctrl+F search chats · Ctrl+, settings · Esc stop"
)
# What the desktop shell (pywebview) exposes before any page script runs: the page only asks
# whether a native bridge is present.
DESKTOP_SHELL_SCRIPT = "window.pywebview = { api: {} };"
SHELLS = ("desktop-shell", "browser-tab")


class _Stamp:
    runtime_version_stamp = {"commit": "t"}


def _served_chat(locale: str) -> str:
    """The /chat document exactly as the production route serves it for this UI locale."""
    clear_catalog_cache()
    response = dispatch_get(
        path="/chat", query={}, runtime=_Stamp(), model_name="m",
        headers={"cookie": f"vool_ui_locale={locale}"}, client_host="127.0.0.1",
    )
    assert response.status == 200
    body = response.body
    html = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    assert f'<html lang="{locale}"' in html
    return html


def _identity_script(platform: str, ua_data_platform: str | None) -> str:
    ua_data = (
        "undefined" if ua_data_platform is None
        else "({ platform: " + json.dumps(ua_data_platform) + ", mobile: false, brands: [] })"
    )
    return (
        "Object.defineProperty(Navigator.prototype, 'platform', { get: () => "
        + json.dumps(platform) + ", configurable: true });"
        "Object.defineProperty(Navigator.prototype, 'userAgentData', { get: () => "
        + ua_data + ", configurable: true });"
    )


@pytest.fixture(scope="module")
def browser():
    manager, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


def _open_chat(browser, platform_name: str, html: str, *, desktop_shell: bool = False):
    platform, ua_data_platform, user_agent = PLATFORMS[platform_name]
    context = browser.new_context(user_agent=user_agent, viewport={"width": 1280, "height": 800})
    context.add_init_script(_identity_script(platform, ua_data_platform))
    if desktop_shell:
        context.add_init_script(DESKTOP_SHELL_SCRIPT)
    page = context.new_page()

    def route(route):
        if route.request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        else:
            route.fulfill(status=200, content_type="application/json", body="{}")

    page.route("**/*", route)
    page.goto("http://vool.test/chat", wait_until="domcontentloaded")
    page.wait_for_selector("#vpHint", state="attached", timeout=30_000)
    assert page.evaluate("navigator.platform") == platform
    assert page.evaluate("!!(window.pywebview && window.pywebview.api)") is desktop_shell
    return context, page


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("locale", ["en", "lt"])
@pytest.mark.parametrize("platform_name", sorted(PLATFORMS))
def test_footer_hint_names_the_platform_modifier(browser, platform_name: str, locale: str, shell: str) -> None:
    catalog = catalog_for(locale)
    apple = platform_name.startswith("macos")
    desktop_shell = shell == "desktop-shell"
    context, page = _open_chat(browser, platform_name, _served_chat(locale), desktop_shell=desktop_shell)
    try:
        hint = page.locator("#vpHint")
        # Ctrl+N is the browser's own key in a plain tab; ⌘N never collides, so a Mac keeps it.
        shown = [
            (key, message) for key, message in HINT_KEYS
            if apple or desktop_shell or key != "N"
        ]
        caps = [cap.text_content() for cap in hint.locator("kbd").all()]
        expected_caps = [("⌘" if apple else "Ctrl+") + key for key, _ in shown] + ["Esc"]
        assert caps == expected_caps

        words = [catalog.text(message) for _, message in shown] + [catalog.text("palette.hint_esc_stop")]
        assert hint.text_content() == " · ".join(f"{cap} {word}" for cap, word in zip(expected_caps, words, strict=True))
        if apple and locale == "en":
            assert hint.inner_html() == MACOS_EN_HINT_HTML
        if not apple:
            assert "⌘" not in hint.text_content()
            if desktop_shell:
                assert "Ctrl+N" in caps
            else:
                assert "Ctrl+N" not in hint.text_content()
                assert catalog.text("palette.hint_new_chat") not in hint.text_content()
            if locale == "en":
                assert hint.text_content() == (CTRL_EN_HINT_TEXT if desktop_shell else CTRL_EN_BROWSER_TAB_HINT_TEXT)
    finally:
        context.close()


@pytest.mark.parametrize("platform_name", sorted(PLATFORMS))
def test_the_hinted_combination_opens_the_palette_and_its_rows_agree(browser, platform_name: str) -> None:
    apple = platform_name.startswith("macos")
    context, page = _open_chat(browser, platform_name, _served_chat("en"))
    try:
        assert page.locator("#vpOverlay").is_hidden()
        page.locator("#input").focus()
        # The exact combination the hint names, typed from the composer.
        page.keyboard.press("Meta+k" if apple else "Control+k")
        page.wait_for_selector("#vpOverlay", state="visible", timeout=5_000)
        modifier = "⌘" if apple else "Ctrl+"
        assert page.locator('[data-vp="new-chat"] .vp-kbd').text_content() == modifier + "N"
        assert page.locator('[data-vp="toggle-activity"] .vp-kbd').text_content() == modifier + "J"
        assert page.locator('[data-vp="settings"] .vp-kbd').text_content() == modifier + ","
        # A row another fragment registers (the companion drawer) is labelled by the same owner.
        assert page.locator('[data-vp="companion-drawer"] .vp-kbd').text_content() == modifier + "/"
        if not apple:
            assert "⌘" not in page.locator("#vpList").text_content()
        page.keyboard.press("Escape")
        page.wait_for_selector("#vpOverlay", state="hidden", timeout=5_000)
    finally:
        context.close()
