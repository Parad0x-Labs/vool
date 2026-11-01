from __future__ import annotations

import json

from tests._toybox_v2_browser import finish_browser
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_toybox_v2_contract import fixture_skin


def test_saved_stage_options_survive_an_actual_http_server_restart(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(reduced_motion='no-preference')
        manifest = fixture_skin()
        reply = page.request.post(served + '/api/toybox/apply', data={
            'manifest': json.dumps(manifest), 'revision': 0, 'confirmed': True,
            'motion': 'calm', 'layout_override': 'standard',
        })
        assert reply.status == 200
        original = reply.json()
        page.goto(served + '/chat')
        page.wait_for_function('window.VoolStage && window.VoolCompanion')
        page.wait_for_load_state('networkidle')
        served.restart()
        page.reload()
        page.wait_for_function("id=>document.documentElement.dataset.skin===id", arg=manifest['id'])
        page.wait_for_function('window.VoolCompanion')
        page.evaluate("VoolStage.mirror('quiet',false)")
        assert page.locator('html').get_attribute('data-vool-motion') == 'calm'
        assert page.locator('html').get_attribute('data-vool-layout') == 'standard'
        restored = page.request.get(served + '/api/toybox').json()
        assert (restored['revision'], restored['digest'], restored['motion'], restored['layout_override']) == (
            original['revision'], original['digest'], 'calm', 'standard')
    finally:
        finish_browser(manager, browser)
