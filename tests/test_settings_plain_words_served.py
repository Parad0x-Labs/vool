"""Settings say "when this applies" once per page, in the UI language, on the served page.

Before: every row of a page repeated the same two lines ("Applies everywhere" / "Applies from your
next message. The turn in flight keeps the old value."), the scope words were English in every
language, and English users saw a "1849 strings" count they could do nothing with.

- the General page states the shared line once, in plain words under its title, and its rows
  repeat nothing; only a line true of EVERY setting on a page is lifted to the top;
- a page whose rows differ keeps the line on each row (Usage & Budgets);
- the scope words come from the catalog (German shows German);
- the English language box carries no string count.
"""
from __future__ import annotations

from core.i18n.catalog import catalog_for
from core.vool_settings_page import render_vool_settings_html
from tests import _reader_served_rig as rig
from tests import served_browser


def _open(browser, base_url, html=None):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def handle(route):
        url = route.request.url
        if html is not None and url.split("#")[0] == base_url + "/settings":
            return route.fulfill(status=200, content_type="text/html", body=html)
        return route.continue_() if url.startswith(base_url) else route.abort()

    page.route("**/*", handle)
    page.goto(base_url + "/settings#general", wait_until="domcontentloaded")
    page.wait_for_selector("#paneBody .row", timeout=15000)
    return page, errors


def test_settings_state_the_shared_effect_once_per_page(tmp_path):
    with rig.CapturingProvider() as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider)
        try:
            daemon.start(timeout=120)
            manager, browser = served_browser.launch_chromium()
            try:
                page, errors = _open(browser, daemon.base_url)
                page.locator(".nav-item[data-group='general']").click()
                page.wait_for_selector("#paneBody [data-row='humor_percent']", timeout=10000)
                meta = page.locator("#paneMeta")
                assert meta.is_visible()
                assert meta.inner_text() == "Changes here apply everywhere, from your next message."
                general_rows = page.locator("#paneBody .row").count()
                assert general_rows >= 5
                assert page.locator("#paneBody .row-meta").count() == 0
                assert "Injected" not in page.locator("#paneBody").inner_text()

                # Usage & Budgets: its two rows differ ("immediately" / "next message"), so nothing is
                # summarised at the top and each row keeps its own line.
                page.locator(".nav-item[data-group='budgets']").click()
                page.wait_for_selector("#paneBody [data-row='ram_reserve_pct']", timeout=10000)
                assert page.locator("#paneMeta").is_hidden()
                row_lines = page.locator("#paneBody .row-meta").all_inner_texts()
                assert any("Applies immediately." in line for line in row_lines), row_lines
                assert any("Applies from your next message" in line for line in row_lines), row_lines

                # English: no string count under the language picker, only the source-language line.
                assert page.locator("#uiLocaleCoverage").inner_text().strip() == "English (the source language)"
                assert not errors, errors
                page.close()

                # German: the scope words come from the catalog.
                de = catalog_for("de")
                page, errors = _open(browser, daemon.base_url, html=render_vool_settings_html(ui_locale="de"))
                page.locator(".nav-item[data-group='general']").click()
                page.wait_for_selector("#paneBody [data-row='humor_percent']", timeout=10000)
                assert page.locator("#paneMeta").inner_text() == de.text("settings.pane_meta.global_next")
                # A complete catalog gets no line; a partial one says how much falls back to English.
                missing = len(set(de.keys) - (de.locale_keys & set(de.keys)))
                coverage = page.locator("#uiLocaleCoverage").inner_text().strip()
                assert (coverage == "") if missing == 0 else (str(missing) in coverage), coverage
                assert not errors, errors
                page.close()
            finally:
                browser.close()
                manager.stop()
        finally:
            daemon.stop()
