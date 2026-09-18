"""Served DOM proof: with popups blocked and no native bridge, every Settings entry path in the chat
page frames the real /settings surface, the legacy in-chat panel never appears, and closing from
inside the frame leaves the chat's draft and location untouched."""
from __future__ import annotations

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

DRAFT = "an unsent draft that must survive opening Settings"
BLOCK_POPUPS = (
    "window.open = () => null;"
    "try { Object.defineProperty(window, 'pywebview', { value: undefined, configurable: true }); } catch (e) {}"
)


def _frame_href(page) -> str:
    return page.evaluate("document.getElementById('settingsFrame').contentWindow.location.href")


def _wait_frame_ready(page) -> None:
    page.wait_for_function(
        "(() => { const f = document.getElementById('settingsFrame'); const d = f && f.contentDocument;"
        " return !!(d && d.readyState === 'complete' && d.getElementById('back')); })()",
        timeout=30_000,
    )


def test_popup_blocked_browser_fallback_frames_settings_and_keeps_the_draft(tmp_path):
    with rig.CapturingProvider() as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=180)
            manager, browser = launch_chromium()
            try:
                page = browser.new_page()
                page.add_init_script(BLOCK_POPUPS)
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_selector("#input", timeout=30_000)
                page.fill("#input", DRAFT)
                href_before = page.evaluate("location.href")

                # Entry 1: the sidebar button.
                page.click("#settingsBtn")
                page.wait_for_selector("#settingsFrameOverlay:not([hidden])", timeout=15_000)
                assert page.is_hidden("#settingsOverlay"), "the legacy panel must never show"
                _wait_frame_ready(page)
                assert _frame_href(page).endswith("/settings")
                # Close from INSIDE the framed page, through its own Back control.
                page.frame_locator("#settingsFrame").locator("#back").click()
                page.wait_for_selector("#settingsFrameOverlay", state="hidden", timeout=15_000)
                assert page.input_value("#input") == DRAFT
                assert page.evaluate("location.href") == href_before

                # Entry 2: the platform shortcut (Ctrl+, is accepted alongside Cmd+,).
                page.keyboard.press("Control+,")
                page.wait_for_selector("#settingsFrameOverlay:not([hidden])", timeout=15_000)
                assert page.is_hidden("#settingsOverlay")
                page.evaluate("window.VoolPageActions.closeSettingsFrame()")
                page.wait_for_selector("#settingsFrameOverlay", state="hidden", timeout=15_000)

                # Entry 3: the page-actions surface (command palette) and the tour's deep link.
                page.evaluate("window.VoolPageActions.openSettings()")
                page.wait_for_selector("#settingsFrameOverlay:not([hidden])", timeout=15_000)
                page.evaluate("window.VoolPageActions.closeSettingsFrame()")
                page.evaluate("openSettings('memory')")
                page.wait_for_selector("#settingsFrameOverlay:not([hidden])", timeout=15_000)
                _wait_frame_ready(page)
                assert _frame_href(page).endswith("/settings#memory")
                assert page.is_hidden("#settingsOverlay")
                page.keyboard.press("Escape")
                page.wait_for_selector("#settingsFrameOverlay", state="hidden", timeout=15_000)

                assert page.input_value("#input") == DRAFT
                assert page.evaluate("location.href") == href_before
                assert page.is_hidden("#settingsOverlay")
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
