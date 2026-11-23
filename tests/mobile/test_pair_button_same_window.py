"""Real Chromium: "Pair a phone" shows the code even where windows opened from script are dropped.

VOOL's own desktop window (pywebview on WKWebView) opens no window for a script's window.open, without
an error (sls 2026-11-23: the click showed nothing while the computer had made the code five times).
The page below drops script-opened windows the same way, so the button must show the code in place.
"""
import os

from test_phone_listener_served import desktop, http  # noqa: F401 (fixture)

DROP_SCRIPT_WINDOWS = "window.__opened = []; window.open = (...a) => { window.__opened.push(a); return null; };"


def _browser(driver):
    return driver.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))


def test_pair_a_phone_shows_the_code_in_the_same_window_and_comes_back(desktop):
    from playwright.sync_api import sync_playwright

    base, _runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
    assert status == 200, value
    with sync_playwright() as driver:
        browser = _browser(driver)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.add_init_script(DROP_SCRIPT_WINDOWS)
            page.goto(base + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            page.get_by_role("button", name="Pair a phone").click()
            page.wait_for_url("**/api/mobile/pairing/page?pairing_id=*", timeout=15000)
            assert page.evaluate("window.__opened.length") == 0
            page.locator("#qr table, #qr canvas, #qr svg, #qr img").first.wait_for(timeout=10000)
            code = page.locator(".code").inner_text().strip()
            assert len(code) == 8, code
            page.get_by_role("link", name="Back to Settings").click()
            page.wait_for_url("**/settings#phone", timeout=15000)
            page.locator('[data-group="phone"]').click()
            page.get_by_role("button", name="Pair a phone").wait_for()
        finally:
            browser.close()


def test_pair_a_phone_says_so_when_no_code_comes_back(desktop):
    from playwright.sync_api import sync_playwright

    base, _runtime, _home = desktop
    status, value = http(base, "/api/mobile/settings", {"enabled": True, "address": "127.0.0.1"})
    assert status == 200, value
    with sync_playwright() as driver:
        browser = _browser(driver)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.add_init_script(DROP_SCRIPT_WINDOWS)
            page.route("**/api/mobile/pairing/start", lambda route: route.fulfill(status=500, body="{}", content_type="application/json"))
            page.goto(base + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            pair = page.get_by_role("button", name="Pair a phone")
            pair.click()
            page.get_by_text("VOOL did not answer").first.wait_for(timeout=10000)
            assert "/settings" in page.url
            assert pair.is_enabled()
        finally:
            browser.close()
