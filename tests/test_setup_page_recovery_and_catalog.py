"""Setup page: a save that lands clears the stale failure, and every word on the page is in the catalog.

Pins two real regressions:
- after VOOL came back, a successful choice showed the green Done next to the red "VOOL isn't running
  right now, so nothing was saved" note from the failed attempt before it;
- the page's new words were missing from every catalog, so German and Arabic step 1 fell back to
  English where the base had been translated, and English logged a missing-key warning per word.
"""
from __future__ import annotations

import json

from tests.served_browser import launch_chromium

ORIGIN = "http://setup.test"


def _state(autonomy_done: bool) -> dict:
    step = lambda sid, done: {"id": sid, "title": sid, "sentence": "sentence", "later": "Settings", "done": done, "skipped": False}  # noqa: E731
    return {
        "ok": True, "total": 4, "done_count": 1 if autonomy_done else 0, "skipped": [], "complete": False,
        "dismissed": False, "show_entry": True, "show_chat_line": True, "first_undone": "thinking",
        "autonomy_mode": "balanced" if autonomy_done else "",
        "steps": [step("thinking", False), step("folder", False), step("permissions", autonomy_done), step("name", False)],
        "cloud_default": {"local_model_running": False, "local_model_reason": "", "model": "", "decision": "",
                          "decided_at": "", "candidates": [], "needs_choice": False},
    }


def _open(browser, html: str, prefs):
    page = browser.new_page()
    saved = {"done": False}
    warnings: list[str] = []
    page.on("console", lambda m: warnings.append(m.text) if "missing key" in m.text else None)

    def handle(route):
        url = route.request.url
        if url.startswith(ORIGIN + "/setup"):
            return route.fulfill(status=200, content_type="text/html", body=html)
        if url.startswith(ORIGIN + "/api/setup/state"):
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(_state(saved["done"])))
        if url.startswith(ORIGIN + "/api/settings/prefs"):
            return prefs(route, saved)
        return route.abort()

    page.add_init_script("window.open = () => null;")
    page.route(lambda url: url.startswith("http"), handle)
    return page, warnings


def test_a_save_that_lands_after_an_offline_attempt_clears_the_offline_note() -> None:
    from core.vool_setup_page import render_vool_setup_html

    attempts = {"n": 0}

    def prefs(route, saved):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return route.abort()  # VOOL is not running: the fetch itself fails
        saved["done"] = True
        return route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

    manager, browser = launch_chromium()
    try:
        page, _ = _open(browser, render_vool_setup_html(), prefs)
        page.goto(ORIGIN + "/setup#step=permissions")
        page.wait_for_selector("[data-autonomy=strict]")
        page.click("[data-autonomy=strict]")
        page.wait_for_function("() => document.querySelector('#stepNote').className.includes('failed')")
        page.click("[data-autonomy=balanced]")
        page.wait_for_function("() => document.querySelector('.dot.done') !== null")
        assert page.inner_text("#stepNote") == "", page.inner_text("#stepNote")
        assert attempts["n"] == 2
        page.close()
    finally:
        browser.close()
        manager.stop()


def test_every_setup_word_resolves_in_english_german_and_arabic() -> None:
    from core.i18n.catalog import catalog_for
    from core.vool_setup_page import render_vool_setup_html

    manager, browser = launch_chromium()
    try:
        for locale in ("en", "de", "ar"):
            page, warnings = _open(browser, render_vool_setup_html(ui_locale=locale), lambda route, saved: route.abort())
            for step in ("thinking", "folder", "permissions", "name"):
                page.goto(f"{ORIGIN}/setup#step={step}")
                page.wait_for_function("() => document.querySelectorAll('.dot').length === 4 && document.querySelector('#nextBtn').textContent")
                page.wait_for_timeout(200)
            assert warnings == [], (locale, warnings[:5])
            if locale != "en":
                # Step 1's private-model card speaks the locale, as it did before the card's wording changed.
                page.goto(f"{ORIGIN}/setup#step=thinking")
                page.wait_for_selector("#chooseLocal")
                card = page.inner_text("#chooseLocal")
                assert catalog_for(locale).text("setup.thinking.local_not_ready") in card, (locale, card)
            page.close()
    finally:
        browser.close()
        manager.stop()
