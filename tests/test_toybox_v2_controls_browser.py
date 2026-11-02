from __future__ import annotations

import json

import pytest

from tests._toybox_v2_browser import finish_browser, network_guard, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_v2_contract import fixture_skin


@pytest.mark.parametrize("controls", ["keys", "dials", "glass", "ink"])
def test_existing_controls_keep_keyboard_access_and_hit_area(served, controls):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        guard = network_guard(page, served)
        manifest = fixture_skin()
        manifest["stage"].update(controls=controls)
        assert page.request.post(served + "/api/toybox/apply", data={"manifest": json.dumps(manifest), "revision": 0, "confirmed": True}).status == 200
        page.goto(served + "/chat")
        page.locator("#modelBtn").wait_for()
        for selector in ("#modelBtn", "#send"):
            box = page.locator(selector).bounding_box()
            assert box["width"] >= 32 and box["height"] >= 32
        page.focus("#modelBtn")
        page.keyboard.press("Enter")
        assert page.locator("#modelBtn").get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert page.locator("#modelBtn").get_attribute("aria-expanded") == "false"
        page.emulate_media(reduced_motion="reduce")
        assert page.locator("#modelBtn").evaluate("el=>getComputedStyle(el).outlineStyle") != "none"
        shoot(page, "controls-" + controls)
        assert not guard()
    finally:
        finish_browser(manager, browser)
