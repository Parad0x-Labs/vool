from __future__ import annotations

import json
from contextlib import closing

from core.pet_world.creator import build
from tests._toybox_v2_browser import finish_browser, shoot
from tests._toybox_v2_browser import served as served
from tests.served_browser import launch_chromium


def test_native_world_preview_cancel_import_backup_and_stale_editor(served):
    from storage.db import get_connection

    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1400, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(served+'/pet-yard')
        page.wait_for_function('()=>window.VoolYardPage?.renderer && !document.querySelector("#worldExport").disabled')
        page.locator('#yardName').fill('Previous garden')
        page.locator('#yardName').dispatch_event('change')
        page.locator('#yardSave').click()
        page.wait_for_function('()=>VoolYardPage.yard.revision===1')
        old = page.evaluate('JSON.stringify(VoolYardPage.yard)')
        raw = build({'name': '月の庭', 'description': '小さな庭', 'base': [['sand', 1]], 'fills': [], 'decorations': [], 'pets': [{'id': 'lumen-fox', 'x': 12, 'y': 12}]})
        upload = {'name': 'moon.voolworld', 'mimeType': 'application/json', 'buffer': raw.encode()}
        page.locator('#worldFile').set_input_files(upload)
        page.locator('#worldPreview').wait_for(state='visible')
        assert page.locator('#worldName').inner_text() == '月の庭'
        assert page.locator('#worldCanvas').evaluate('c=>c.getContext("2d").getImageData(416,300,1,1).data[3]') == 255
        shoot(page, 'world-native-preview')
        page.locator('#worldCancel').click()
        assert page.evaluate('JSON.stringify(VoolYardPage.yard)') == old
        # The same file can be inspected again without reloading the page.
        page.locator('#worldFile').set_input_files([])
        page.locator('#worldFile').set_input_files(upload)
        page.locator('#worldPreview').wait_for(state='visible')
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#worldUse').click()
        page.wait_for_function('()=>VoolYardPage.yard.revision===2')
        assert page.evaluate('VoolYardPage.yard.columns[0]') == [['sand', 1]]
        assert page.evaluate('VoolYardPage.yard.name') == '月の庭'
        with closing(get_connection()) as conn:
            backup = conn.execute('SELECT document FROM pet_yard_backups_v1 ORDER BY id DESC LIMIT 1').fetchone()[0]
        assert json.loads(backup) == json.loads(old)
        # Old editor cannot overwrite the replacement or add another backup.
        result = page.request.post(served+'/api/pet-yard', data={'action': 'import_world', 'document': raw, 'revision': 1, 'stamp': '', 'confirmed': True})
        assert result.status == 409 and result.json()['error'] == 'conflict'
        corrupt = json.loads(raw)
        corrupt['columns'][0] = [['water', 1]]
        result = page.request.post(served+'/api/pet-yard', data={'action': 'preview_world', 'document': json.dumps(corrupt)})
        assert result.status == 400 and result.json()['error'] == 'corrupt'
        assert page.request.get(served+'/api/pet-yard').json()['yard']['revision'] == 2
        page.locator('#worldInvitation').fill('https://world.invalid/steal#invite=wrong')
        page.locator('#worldJoin').click()
        assert page.locator('#worldStatus').inner_text() == 'This file or change is not supported. Previous choices are kept.'
        assert not errors
        shoot(page, 'world-native-imported')
    finally:
        finish_browser(manager, browser)
