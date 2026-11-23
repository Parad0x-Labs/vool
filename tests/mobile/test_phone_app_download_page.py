"""Real Chromium: the one download page for the phone app picks the right phone and never invents a download.

The QR code in Settings opens site/app/index.html. iPhone and Android get their own answer; a computer sees
both. Android gets a button only when a published release of this repository carries an Android app.
"""
import json
import os
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / "site" / "app" / "index.html").as_uri()
ANDROID_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"
IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
APK_URL = "https://github.com/Parad0x-Labs/vool/releases/download/v0.8.0/VOOL-0.8.0-android.apk"


def _visible(page, selector):
    return page.locator(selector).is_visible()


def _open(driver, ua, releases, locale="en-US"):
    browser = driver.chromium.launch(executable_path=os.environ.get("VOOL_TEST_CHROMIUM"))
    context = browser.new_context(user_agent=ua, locale=locale, viewport={"width": 390, "height": 800})
    page = context.new_page()

    def answer(route):
        if releases is None:
            route.fulfill(status=503, body="{}")
        else:
            route.fulfill(status=200, body=json.dumps(releases), content_type="application/json",
                          headers={"Access-Control-Allow-Origin": "*"})

    page.route("https://api.github.com/**", answer)
    page.goto(PAGE)
    return browser, page


def test_android_with_a_released_app_gets_the_download_button():
    from playwright.sync_api import sync_playwright

    releases = [{"draft": True, "assets": [{"name": "draft.apk", "browser_download_url": APK_URL + "x"}]},
                {"draft": False, "assets": [{"name": "VOOL-0.8.0-macos-arm64.dmg", "browser_download_url": "https://github.com/a.dmg"},
                                            {"name": "VOOL-0.8.0-android.apk", "browser_download_url": APK_URL}]}]
    with sync_playwright() as driver:
        browser, page = _open(driver, ANDROID_UA, releases)
        try:
            button = page.get_by_role("link", name="Download for Android")
            button.wait_for(timeout=10000)
            assert button.get_attribute("href") == APK_URL
            assert not _visible(page, "#iphone")
            assert not _visible(page, "#android-none")
            assert "Pair a phone" in page.locator("#then").inner_text()
        finally:
            browser.close()


def test_android_without_a_released_app_is_told_so():
    from playwright.sync_api import sync_playwright

    releases = [{"draft": False, "assets": [{"name": "VOOL-0.7.7-beta-macos-arm64.dmg", "browser_download_url": "https://github.com/a.dmg"}]}]
    with sync_playwright() as driver:
        browser, page = _open(driver, ANDROID_UA, releases)
        try:
            page.locator("#android-none").wait_for(state="visible", timeout=10000)
            assert not _visible(page, "#android-get")
        finally:
            browser.close()


def test_iphone_is_told_plainly_and_nothing_is_offered():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser, page = _open(driver, IPHONE_UA, [])
        try:
            assert _visible(page, "#iphone")
            assert "not ready" in page.locator("#iphone-text").inner_text()
            assert not _visible(page, "#android")
        finally:
            browser.close()


def test_no_answer_points_to_the_release_list_in_the_reader_language():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser, page = _open(driver, ANDROID_UA, None, locale="de-DE")
        try:
            page.locator("#fallback").wait_for(state="visible", timeout=10000)
            assert page.locator("#fallback-link").get_attribute("href") == "https://github.com/Parad0x-Labs/vool/releases"
            assert page.locator("html").get_attribute("lang") == "de"
            assert "Telefon koppeln" in page.locator("#then").inner_text()
        finally:
            browser.close()


def test_every_language_has_every_line():
    text = Path(PAGE.replace("file://", "")).read_text(encoding="utf-8")
    words = json.loads(text.split("var WORDS = ", 1)[1].split(";\n", 1)[0])
    catalogs = Path(__file__).resolve().parents[2] / "core" / "i18n" / "catalogs"
    langs = sorted(p.stem for p in catalogs.glob("*.json"))
    assert sorted(words) == langs
    for lang in langs:
        assert set(words[lang]) == set(words["en"]), lang
        assert all(str(v).strip() for v in words[lang].values()), lang
