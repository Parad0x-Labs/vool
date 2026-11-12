from __future__ import annotations

import pytest

from tests._toybox_v2_browser import finish_browser, network_guard
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium
from tests.test_pet_world_journey import wait


@pytest.mark.parametrize('reduced', [False, True])
def test_fetch_keyboard_target_commit_cancel_and_mode_change(served, reduced):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1100},
                                reduced_motion='reduce' if reduced else 'no-preference')
        outside = network_guard(page, served)
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller')
        # Use the actual focused controls, never the controller's fetch method.
        page.locator('#yardPetList button').nth(2).focus()
        page.keyboard.press('Enter')
        before = page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)')
        page.locator('#yardFetch').focus()
        page.keyboard.press('Enter')
        assert page.locator('#yardCanvas').evaluate('c=>c===document.activeElement')
        page.keyboard.press('ArrowRight')
        page.keyboard.press('ArrowUp')
        page.wait_for_timeout(900)
        assert page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)') == before
        assert page.evaluate('VoolYardPage.controller.game') == ''
        page.keyboard.press('Enter')
        wait(page, 'document.getElementById("yardResult").textContent===VoolPetCosmetics.t("fetch_done")')
        assert page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)') == before

        page.locator('#yardFetch').focus()
        page.keyboard.press('Enter')
        page.keyboard.press('ArrowUp')
        page.keyboard.press('Escape')
        page.keyboard.press('ArrowRight')
        page.wait_for_timeout(900)
        moved = page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)')
        assert moved['x'] == before['x']+1
        assert moved['y'] == before['y']
        assert page.evaluate('VoolYardPage.controller.game') == ''

        page.locator('#yardEmotes button').first.focus()
        page.keyboard.press('Enter')
        page.locator('#yardCanvas').press('ArrowRight')
        page.wait_for_timeout(900)
        walked = page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)')
        assert walked['x'] == moved['x']+1
        assert walked['y'] == moved['y']

        page.locator('#yardFetch').focus()
        page.keyboard.press('Enter')
        page.locator('[data-mode="build"]').focus()
        page.keyboard.press('Enter')
        columns = page.evaluate('VoolYardPage.yard.columns')
        page.locator('#yardCanvas').focus()
        page.keyboard.press('Enter')
        wait(page, 'VoolYardPage.diagnostics.history===1')
        changed = page.evaluate('VoolYardPage.yard.columns')
        def height(rows):
            return sum(n for column in rows for _, n in column)
        assert height(changed) == height(columns)+1
        assert page.evaluate('VoolYardPage.controller.game') == ''
        assert not outside()
    finally:
        finish_browser(manager, browser)


def test_fetch_pointer_completes_with_reduced_motion(served):
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1100}, reduced_motion='reduce')
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller')
        page.locator('#yardPetList button').nth(2).click()
        before = page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)')
        page.locator('#yardFetch').click()
        point = page.evaluate('VoolYardPage.renderer.project(15,11)')
        box = page.locator('#yardCanvas').bounding_box()
        size = page.locator('#yardCanvas').evaluate('c=>({w:c.width,h:c.height})')
        page.mouse.click(box['x']+point['x']*box['width']/size['w'],
                         box['y']+point['y']*box['height']/size['h'])
        wait(page, 'document.getElementById("yardResult").textContent===VoolPetCosmetics.t("fetch_done")')
        assert page.evaluate('VoolYardPage.controller.serialize().find(p=>p.id===VoolYardPage.controller.selected)') == before
        assert page.evaluate('VoolYardPage.controller.game') == ''
    finally:
        finish_browser(manager, browser)


@pytest.mark.parametrize('cause', ['checkbox', 'cancelled', 'refresh'])
def test_active_imported_sound_stops_on_mute_or_cancellation(served, cause):
    import base64
    import wave
    from io import BytesIO

    from tests.test_pet_cosmetics import sound

    with wave.open(BytesIO(sound()), 'rb') as sample:
        params, frames = sample.getparams(), sample.readframes(sample.getnframes())
    output = BytesIO()
    with wave.open(output, 'wb') as sample:
        sample.setparams(params)
        sample.writeframes(frames*5)
    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        page.goto(served+'/pet-yard')
        wait(page, 'window.VoolYardPage?.controller')
        lib = page.request.get(served+'/api/pet-cosmetics').json()
        result = page.request.post(served+'/api/pet-cosmetics/sound', data={
            'revision': lib['revision'], 'name': 'shutdown.wav',
            'wav': base64.b64encode(output.getvalue()).decode(),
        })
        assert result.status == 200
        lib = result.json()
        config = {**lib['config'], 'selected': 'lumen-fox', 'sound': True,
                  'bindings': {'lumen-fox': {'success': {
                      'expression': 'success', 'sound': lib['sounds'][0]['id'],
                  }}}}
        assert page.request.post(served+'/api/pet-cosmetics/configure', data={
            'revision': lib['revision'], 'config': config,
        }).status == 200
        page.reload()
        wait(page, 'window.VoolPetCosmetics?.library.config.sound')
        page.locator('#yardName').click()
        assert page.evaluate('()=>VoolPetCosmetics.play("lumen-fox","success",true)')
        assert page.evaluate('VoolPetCosmetics.diagnostics.active')
        if cause == 'checkbox':
            panel = page.locator('#pet-cosmetics')
            panel.locator('input[type=checkbox]').uncheck()
            assert page.evaluate('VoolPetCosmetics.diagnostics.active') is False
            panel.locator(':scope > button').first.click()
            wait(page, '!VoolPetCosmetics.library.config.sound')
        elif cause == 'cancelled':
            page.evaluate('()=>{VoolPetCosmetics.activity("tool","shutdown");VoolPetCosmetics.activity("cancelled","shutdown");}')
        else:
            lib = page.request.get(served+'/api/pet-cosmetics').json()
            assert page.request.post(served+'/api/pet-cosmetics/configure', data={
                'revision': lib['revision'], 'config': {**lib['config'], 'sound': False},
            }).status == 200
            page.evaluate('()=>VoolPetCosmetics.refresh()')
        assert page.evaluate('VoolPetCosmetics.diagnostics.active') is False
        page.wait_for_timeout(300)
        assert page.evaluate('VoolPetCosmetics.diagnostics.active') is False
    finally:
        finish_browser(manager, browser)
