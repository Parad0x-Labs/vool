from __future__ import annotations

import os
from pathlib import Path

from core.i18n.catalog import catalog_for
from tests import served_browser
from tests.disclosure.test_served_preview import _json
from tests.disclosure.test_served_rules import recording_daemon


def test_settings_readback_and_owner_confirmed_project_rules(tmp_path):
    with recording_daemon(tmp_path) as (daemon, _provider, raw):
        folder = tmp_path / 'workshop'
        folder.mkdir()
        (folder / 'public-specs').mkdir()
        project = _json(daemon.base_url+'/api/projects', {'name':'Workshop', 'root':str(folder)})
        project_id = project.get('project',project)['id']
        assert _json(daemon.base_url+'/api/disclosure/settings')['preview_enabled'] is True
        manager, browser = served_browser.launch_chromium()
        page = browser.new_page(viewport={'width':1280,'height':900})
        errors = []
        page.on('pageerror',lambda error:errors.append(str(error)))
        try:
            page.goto(daemon.base_url+'/settings',wait_until='networkidle')
            page.locator('#search').fill('Before files go to cloud')
            check = page.locator('#disclosurePreviewSetting')
            check.wait_for(state='visible')
            assert check.is_checked()
            check.uncheck()
            page.wait_for_function("() => document.querySelector('.disclosure-settings [role=status]').textContent === 'Saved.'")
            assert _json(daemon.base_url+'/api/disclosure/settings')['preview_enabled'] is False
            check.check()
            page.wait_for_function("() => !document.getElementById('disclosurePreviewSetting').disabled")
            assert _json(daemon.base_url+'/api/disclosure/settings')['preview_enabled'] is True
            copies = page.locator('#disclosureCopySetting')
            assert copies.is_disabled() and not copies.is_checked()
            rules = page.locator('[data-disclosure-project="'+project_id+'"]')
            rules.locator('summary').click()
            rules.locator('input[type=text]').fill('../outside')
            rules.get_by_role('button',name='Add rule',exact=True).click()
            rules.get_by_role('button',name='Review changes',exact=True).click()
            rules.get_by_text('Choose a folder inside this project.',exact=True).wait_for()
            assert _json(daemon.base_url+'/api/disclosure/projects')['projects'][0]['rules'] == []
            rules.get_by_role('button',name='Remove',exact=True).click()
            rules.locator('input[type=text]').fill('public-specs')
            rules.locator('select').last.select_option('allow')
            rules.get_by_role('button',name='Add rule',exact=True).click()
            rules.get_by_role('button',name='Review changes',exact=True).click()
            rules.get_by_role('button',name='Cancel changes',exact=True).click()
            assert _json(daemon.base_url+'/api/disclosure/projects')['projects'][0]['rules'] == []
            rules.get_by_role('button',name='Review changes',exact=True).click()
            rules.get_by_role('button',name='Confirm',exact=True).click()
            rules.get_by_text('Project rules saved.',exact=True).wait_for()
            assert _json(daemon.base_url+'/api/disclosure/projects')['projects'][0]['rules'] == [
                {'kind':'folder','source':'public-specs','mode':'allow'}]
            page.reload(wait_until='networkidle')
            page.locator('#search').fill('Before files go to cloud')
            page.wait_for_function("() => { const input=document.getElementById('disclosurePreviewSetting'); return input && !input.disabled; }")
            assert page.locator('#disclosurePreviewSetting').is_checked()
            for language in ['ar','es','vi','he','ko','hi','fr','zh-Hans','pt','uk','pl','ru','de','ja','lt','tr','en']:
                with page.expect_navigation(wait_until='networkidle'):
                    page.locator('#uiLocaleSelect').select_option(language)
                label=catalog_for(language).format('settings.row.cloud_disclosure.label')
                page.locator('#search').fill(label)
                page.wait_for_function("() => { const input=document.getElementById('disclosurePreviewSetting'); return input && !input.disabled; }")
                assert page.locator('.disclosure-settings').get_by_text(catalog_for(language).format('disclosure.setting_preview'),exact=True).is_visible()
                assert page.locator('#disclosurePreviewSetting').is_checked()
                if language in {'ar','he','lt'}:
                    page.set_viewport_size({'width':415,'height':896})
                    evidence=os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
                    if evidence:
                        page.screenshot(path=str(Path(evidence).parent/'shots'/('M6-settings-'+language+'-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG','run')+'.png')),full_page=True)
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                    page.set_viewport_size({'width':1280,'height':900})
            assert errors == []
            assert raw == []
        finally:
            evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
            if evidence:
                shots = Path(evidence).parent/'shots'
                shots.mkdir(exist_ok=True)
                page.screenshot(path=str(shots/('M6-settings-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG','run')+'.png')),full_page=True)
            browser.close()
            manager.stop()
