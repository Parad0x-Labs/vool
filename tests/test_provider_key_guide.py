"""Categorized onboarding and the actual saved-key workflow; providers are loopback fixtures."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import FakeProviderServer, isolated_home, vault_home


def test_guide_projects_supported_catalogues_and_pins_official_links():
    from core.cloud_providers import PROVIDERS
    from core.credential_intelligence.provider_registry import default_registry
    from core.provider_key_guide import provider_key_guide
    from core.search_providers import SEARCH_PROVIDERS
    groups = provider_key_guide()
    assert [g['id'] for g in groups] == ['models', 'search', 'security']
    assert {r['provider'] for r in groups[0]['providers']} == set(PROVIDERS)
    assert {r['provider'] for r in groups[1]['providers']} == set(SEARCH_PROVIDERS)
    registry = default_registry()
    for group in groups:
        for row in group['providers']:
            pid = 'search.' + row['provider'] if group['id'] == 'search' else row['provider']
            assert registry.get(pid).credential_slot == row['slot']
            assert len(row['description']) < 100
            assert not row['url'] or row['url'].startswith('https://')
    assert groups[-1]['providers'][0]['url'] == 'https://eyebrow.cc/dashboard'
    assert registry.get('eyebrow').verify_endpoint == 'https://api.eyebrow.cc/v1/version'
    from core.vool_settings_page import render_vool_settings_html
    html = render_vool_settings_html()
    assert 'Getting a web-search key' not in html
    assert '__KEY_PROVIDER_GUIDE__' not in html
    assert 'core/credential_store.py' not in html


def test_served_guide_choose_save_restart_test_and_remove(vault_home, monkeypatch):
    from core import credential_store, eyebrow_client
    from core.runtime_paths import data_path
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium
    runtime = RuntimeServices(display_name='Guide fixture')
    errors = []
    with FakeProviderServer([(200, {'service':'eyebrow', 'engine':{'version':'fixture'}})] * 4) as provider:
        monkeypatch.setattr(eyebrow_client, 'API_ORIGIN', provider.url)
        policy = data_path('cloud_escalation.json')
        before_policy = policy.read_bytes() if policy.exists() else None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def send(self, res):
                self.send_response(res.status); self.send_header('Content-Type', res.content_type)
                self.end_headers(); self.wfile.write(res.body)
            def do_GET(self):
                u=urlsplit(self.path)
                if u.path == '/settings':
                    self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers()
                    self.wfile.write(render_vool_settings_html().encode()); return
                self.send(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime,
                                       model_name='fixture', client_host='127.0.0.1'))
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or '{}')
                self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                           runtime=runtime, model_name='fixture', client_host='127.0.0.1',
                           workspace_root_provider=lambda: str(vault_home)))
        server=ThreadingHTTPServer(('127.0.0.1',0), Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        manager,browser=launch_chromium()
        try:
            page=browser.new_page(viewport={'width':1120,'height':900})
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/settings#keys')
            page.get_by_role('searchbox',name='Find a provider',exact=True).wait_for()
            assert provider.request_count == 0
            assert page.locator('.key-guide-category').count() == 3
            assert page.locator('.key-guide-category[open]').count() == 0
            search=page.get_by_role('searchbox',name='Find a provider',exact=True)
            search.fill('Eyebrow')
            assert page.locator('.key-guide-category[open]').count() == 1
            assert page.get_by_role('link',name='Eyebrow official site').get_attribute('href') == 'https://eyebrow.cc/dashboard'
            page.get_by_role('button',name='Add Eyebrow key',exact=True).click()
            assert page.get_by_role('combobox',name='Provider',exact=True).input_value() == 'eyebrow'
            page.locator('.key-entry-form input[type=password]').fill('fixture-eyebrow-not-real-0123456789')
            page.get_by_role('button',name='Save key',exact=True).click()
            page.get_by_text('Ready for add-on checks; nothing has been scanned or enabled.',exact=False).wait_for()
            assert credential_store.has_credential('security.eyebrow')
            assert provider.request_count == 1
            assert provider.requests[0]['path'] == '/v1/version'
            assert (policy.read_bytes() if policy.exists() else None) == before_policy
            assert page.locator('.key-entry-form input[type=password]').input_value() == ''
            page.reload()
            page.locator('button.key-test[data-provider=eyebrow]').wait_for()
            assert provider.request_count == 1
            search=page.get_by_role('searchbox',name='Find a provider',exact=True)
            search.fill('eyebrow')
            assert page.get_by_text('Key saved',exact=True).is_visible()
            page.on('dialog',lambda d:d.accept())
            page.locator('button.key-test[data-provider=eyebrow]').click()
            page.locator('.key-test-state[data-provider=eyebrow]').filter(has_text='accepted').wait_for()
            assert provider.request_count == 2
            security_row=page.locator('.inline').filter(has=page.locator('button.key-test[data-provider=eyebrow]'))
            security_row.get_by_role('button',name='Remove',exact=True).click()
            page.locator('button.key-test[data-provider=eyebrow]').wait_for(state='detached')
            assert not credential_store.has_credential('security.eyebrow')
            assert provider.request_count == 2
            search=page.get_by_role('searchbox',name='Find a provider',exact=True)
            search.fill('no-such-provider')
            assert page.get_by_text('No matching provider.',exact=True).is_visible()
            search.fill('brave')
            page.get_by_role('button',name='Add Brave Search key',exact=True).click()
            assert page.get_by_role('combobox',name='Provider',exact=True).input_value() == 'brave'
            assert not errors, errors
        finally:
            browser.close(); manager.stop(); server.shutdown(); server.server_close()


@pytest.mark.parametrize('response', [(401, {'error':'unauthorized'}), (200, {'service':'eyebrow'})])
def test_security_key_refusal_stores_nothing(vault_home, monkeypatch, response):
    from core import credential_store, eyebrow_client
    from core.credential_intelligence.intake import CredentialIntake
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import IntakeRefusedError
    with FakeProviderServer([response]) as provider:
        monkeypatch.setattr(eyebrow_client, 'API_ORIGIN', provider.url)
        intake=CredentialIntake(registry=default_registry())
        intake.paste('fixture-rejected-key-0123456789')
        intake.select_provider('eyebrow')
        outcome=intake.verify()
        assert outcome.status != 'verified'
        with pytest.raises(IntakeRefusedError):
            intake.complete()
        assert not credential_store.has_credential('security.eyebrow')
        assert provider.request_count == 1


def test_security_category_cannot_make_runtime_keys_removable():
    from pathlib import Path

    from tests.chat_page_js_harness import run_node
    source=Path('core/vool_settings_page.py').read_text()
    start=source.index('const KEY_FAMILIES')
    helpers=source[start:source.index('function widgetKeys',start)]
    result=run_node(helpers + "\nconsole.log(JSON.stringify({eyebrow:isOperatorKey('security.eyebrow'),runtime:isOperatorKey('security.internal'),cas:isOperatorKey('blackbox.cas.keys')}));")
    assert result == {'eyebrow':True, 'runtime':False, 'cas':False}
