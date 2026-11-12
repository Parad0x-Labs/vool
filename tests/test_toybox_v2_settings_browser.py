from __future__ import annotations

import json

from tests._toybox_v2_browser import finish_browser, network_guard, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_v2_contract import fixture_skin


def test_preview_cancel_apply_options_and_restore_on_served_settings(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 480, "height": 900})
        guard = network_guard(page, served)
        page.goto(served + "/settings#toybox")
        page.locator(".tb-card").first.wait_for()
        initial = page.request.get(served + "/api/toybox").json()
        page.locator(".tb-card").first.get_by_role("button", name="Preview", exact=True).click()
        page.locator("#tb-preview-box iframe").wait_for()
        page.get_by_role("button", name="Cancel preview", exact=True).click()
        after = page.request.get(served + "/api/toybox").json()
        assert after["revision"] == initial["revision"] and after["digest"] == initial["digest"]
        page.select_option("#tb-motion", "off")
        page.select_option("#tb-layout", "standard")
        page.get_by_role("button", name="Save options", exact=True).click()
        page.get_by_text("Options saved on this device.", exact=True).wait_for()
        page.reload()
        page.locator("#tb-motion").wait_for()
        page.wait_for_function("document.getElementById('tb-motion').value==='off'")
        assert page.locator("html").get_attribute("data-vool-motion") == "off"
        assert not page.evaluate("document.getAnimations().some(a=>a.playState==='running'&&a.effect.target.closest('.tb-root'))")
        page.locator(".tb-card").first.get_by_role("button", name="Preview", exact=True).click()
        page.get_by_role("button", name="Apply skin", exact=True).click()
        page.get_by_text("Saved for chat and Settings.", exact=True).wait_for()
        assert page.request.get(served + "/api/toybox").json()["selected"]
        page.get_by_role("button", name="Restore VOOL default", exact=True).click()
        page.get_by_text("VOOL default restored.", exact=True).wait_for()
        assert page.request.get(served + "/api/toybox").json()["css"] == ""
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        shoot(page, "gallery-narrow")
        assert not guard()
    finally:
        finish_browser(manager, browser)


def test_v2_gallery_is_same_renderer_and_demo_isolated_from_main_activity(served):
    from core.toybox_fragment import preview_html

    manifest = fixture_skin()
    manifest["stage"].update(controls="glass", reactive={"profile": "glow"})
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        assert page.request.post(served + "/api/toybox/apply", data={"manifest": json.dumps(manifest), "revision": 0, "confirmed": True}).status == 200
        state = page.request.get(served + "/api/toybox").json()
        choice = next(c for c in state["choices"] if c["manifest"]["id"] == manifest["id"])
        assert choice["preview"] == preview_html(choice["manifest"])
        page.goto(served + "/settings#toybox")
        card = page.locator('[data-skin-id="stage-probe"]')
        card.wait_for()
        assert card.locator("iframe").get_attribute("sandbox") == ""
        card.get_by_role("button", name="Preview", exact=True).click()
        page.wait_for_function("document.querySelector('#tb-preview-box iframe')")
        before = page.locator("html").get_attribute("data-vool-activity")
        page.get_by_role("button", name="Play preview", exact=True).click()
        page.wait_for_timeout(1600)
        assert page.locator("html").get_attribute("data-vool-activity") == before
    finally:
        finish_browser(manager, browser)


def test_translated_gallery_from_first_paint_and_bad_import_preserves_saved_look(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 480, "height": 900})
        page.goto(served + "/settings?ui_locale=ar#toybox")
        page.locator(".tb-card").first.wait_for()
        assert page.locator("html").get_attribute("dir") == "rtl"
        assert page.locator(".tb-root h2.tb-title").inner_text() == "اجعل VOOL يناسبك"
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.locator(".tb-import summary").click()
        page.fill("#tb-json", '{"renderer_contract":"2.0","stage":{"css":"body{display:none}"}}')
        page.locator(".tb-import button").click()
        page.locator("#tb-message.tb-error").wait_for()
        assert page.request.get(served + "/api/toybox").json()["digest"] == ""
        shoot(page, "gallery-arabic-narrow")
    finally:
        finish_browser(manager, browser)
