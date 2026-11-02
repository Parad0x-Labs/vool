from __future__ import annotations

import json

import pytest

from tests._toybox_v2_browser import finish_browser, network_guard, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_v2_contract import fixture_skin


@pytest.mark.parametrize("layout", ["standard", "device", "dock", "spread", "console"])
def test_layout_and_truth_follow_real_companion_reducer(served, layout):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        guard = network_guard(page, served)
        manifest = fixture_skin()
        manifest["stage"]["layout"] = layout
        manifest["stage"]["reactive"] = {"profile": "meter"}
        response = page.request.post(served + "/api/toybox/apply", data={"manifest": json.dumps(manifest), "revision": 0, "confirmed": True})
        assert response.status == 200, response.text()
        page.goto(served + "/chat")
        page.wait_for_function("window.VoolStage && window.VoolCompanion")
        assert page.locator("#log").bounding_box()["width"] >= 640
        def toggle_dock():
            page.locator('#tbDockChats').click()
            page.wait_for_function('document.getElementById("tbDockChats").getAttribute("aria-expanded")===String(document.body.classList.contains("sidebar-open"))')
        if layout == 'dock':
            page.locator('#tbDockChats').wait_for()
            assert page.locator('#tb-dock').bounding_box()['width'] == 56
            toggle_dock()
        rail = page.locator("#sidebar").bounding_box()
        for selector in ("#newChat", "#newProject"):
            box = page.locator(selector).bounding_box()
            assert box["x"] >= rail["x"] and box["x"] + box["width"] <= rail["x"] + rail["width"]
        if layout == 'dock':
            toggle_dock()
        page.locator('#panelBtn').click()
        assert page.locator('#xpanel').bounding_box()['height'] > 500
        assert page.locator('#log').bounding_box()['width'] >= 640
        if layout == 'dock':
            toggle_dock()
        before_width = page.locator('#sidebar').bounding_box()['width']
        grip = page.locator('#sideResize').bounding_box()
        page.mouse.move(grip['x'] + 2, 400)
        page.mouse.down()
        page.mouse.move(grip['x'] + 42, 400, steps=5)
        page.mouse.up()
        assert page.locator('#sidebar').bounding_box()['width'] >= before_width + 20
        assert page.locator('#log').bounding_box()['width'] >= 640
        if layout == 'dock':
            toggle_dock()
        page.locator('#panelBtn').click()
        page.set_viewport_size({'width':1100,'height':900})
        page.locator('#panelBtn').click()
        assert page.locator('#xpanel').evaluate("el=>getComputedStyle(el).position") == 'fixed'
        assert page.locator('#xpanel').bounding_box()['y'] >= page.locator('#main>header').bounding_box()['height'] - 1
        page.locator('#panelBtn').click()
        page.set_viewport_size({'width':1280,'height':900})
        for event, expected in [({"type": "task.started"}, "thinking"),
                                ({"type": "tool.started", "tool": "read_file"}, "tool"),
                                ({"type": "permission.required"}, "approval"),
                                ({"type": "task.failed"}, "failure")]:
            page.evaluate("event=>{VoolCompanion.consume('probe',event);VoolStage.mirror('probe',false);}", event)
            assert page.locator("html").get_attribute("data-vool-activity") == expected
        page.evaluate("VoolStage.mirror('other',false)")
        assert page.locator("html").get_attribute("data-vool-activity") == "idle"
        page.evaluate("VoolCompanion.consume('pass',{type:'task.started'});VoolCompanion.consume('pass',{type:'verification.completed',review_state:'passed'});VoolCompanion.consume('pass',{type:'task.completed'});VoolStage.mirror('pass',false)")
        assert page.locator("html").get_attribute("data-vool-activity") == "success"
        leaves = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--tb-leaves')")
        page.evaluate("VoolStage.mirror('other',false);VoolStage.mirror('pass',false)")
        assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--tb-leaves')") == leaves
        # No unreviewed completion is allowed to light a success effect.
        page.evaluate("VoolCompanion.consume('plain',{type:'task.started'});VoolCompanion.consume('plain',{type:'task.completed'});VoolStage.mirror('plain',false)")
        assert page.locator("html").get_attribute("data-vool-activity") == "unknown"
        for width in (1280, 480):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            shoot(page, f"stage-{layout}-{width}")
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_timeout(30)
        assert page.locator("html").get_attribute("data-vool-motion") == "off"
        assert not guard()
    finally:
        finish_browser(manager, browser)
