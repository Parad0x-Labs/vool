"""Shared action text across every served locale and the approval surfaces."""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_actions_approval import fake_x
from tests.x_social.support import resource


def test_action_summaries_use_every_catalog_on_served_settings(fake_x, vault_home):
    from core import x_actions
    from core.i18n.catalog import CATALOGS_DIR, catalog_for
    from core.runtime_execution_tools import execute_runtime_tool
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium
    from tests.x_social.test_cards import _context

    action = execute_runtime_tool('x.action.propose', {'kind': 'quote', 'post': '555',
        'text': 'Keep the exact proposed words.'}, source_context=_context()).details['action']
    runtime = RuntimeServices(display_name='Social fixture')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def answer(self, result):
            self.send_response(result.status)
            self.send_header('Content-Type', result.content_type)
            self.end_headers()
            self.wfile.write(result.body)
        def do_GET(self):
            u = urlsplit(self.path)
            self.answer(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime,
                                     model_name='fixture', client_host='127.0.0.1'))
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or '{}')
            self.answer(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                runtime=runtime, model_name='fixture', client_host='127.0.0.1',
                workspace_root_provider=lambda: str(vault_home)))
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    resource('port', server.server_port, 'started')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1100, 'height': 1050})
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        base = f'http://127.0.0.1:{server.server_port}'
        folder = Path(os.environ['SOCIAL_SHOTS']); folder.mkdir(parents=True, exist_ok=True)
        evidence = {}
        for locale in sorted(path.stem for path in CATALOGS_DIR.glob('*.json')):
            catalog = catalog_for(locale)
            page.goto(base + '/settings?ui_locale=' + locale + '#keys')
            def isolate(value, selected_locale=locale):
                return '\u2068' + value + '\u2069' if selected_locale in {'ar', 'he'} else value
            expected = catalog.text('xs.summary_quote').replace('@{author}', isolate('@late_whale'))
            card = page.locator('.x-action-card').filter(has_text='Keep the exact proposed words.')
            card.locator('strong').filter(has_text=expected).wait_for()
            assert card.locator('strong').text_content() == expected
            rows = [{'kind': kind, 'target': {'username': 'morgan', 'name': 'Morgan Lee',
                'author': 'late_whale', 'list_name': 'Launch board', 'private': True,
                'description': 'A curated list.'}} for kind in x_actions.KINDS]
            rows.append({'kind': 'list_create', 'target': {'name': 'Launch board', 'private': False}})
            actual = page.evaluate('(rows)=>rows.map(a=>VoolXSocial.summary(a))', rows)
            for row, rendered in zip(rows, actual, strict=True):
                key = 'xs.summary_' + row['kind']; assert key in catalog.locale_keys, (locale, key)
                target = row['target']
                values = {'author': 'late_whale', 'who': isolate('Morgan Lee (@morgan)'),
                    'name': isolate(target.get('list_name', target['name'])),
                    'privacy': catalog.text('xs.private' if target['private'] else 'xs.public'),
                    'description': catalog.text('xs.description').replace('{description}', isolate(target['description'])) if target.get('description') else ''}
                expected_row = catalog.text(key).replace('@{author}', isolate('@{author}'))
                for key, value in values.items(): expected_row = expected_row.replace('{' + key + '}', value)
                assert rendered == expected_row, (locale, row, rendered, expected_row)
                if locale != 'en': assert rendered != x_actions.summary(row), (locale, row)
            unknown = page.evaluate("VoolXSocial.state({state:'unknown',message:'An inherited English gateway message'})")
            assert unknown == catalog.text('xs.unknown_state')
            evidence[locale] = {'summaries': actual, 'unknown': unknown}
            if locale in {'en', 'de', 'ar'}:
                card.screenshot(path=str(folder / ('summary-settings-' + locale + '.png')))
                # Mount the real owner-only card on this served page; it calls the real card API.
                page.evaluate("""() => {const host=document.createElement('div');host.id='summary-card';document.body.appendChild(host);VoolXSocial.mount(host,'cards-chat','request-card');}""")
                chat = page.locator('#summary-card .vx-approval')
                chat.locator('strong').filter(has_text=expected).wait_for()
                assert chat.locator('strong').text_content() == expected
                words = chat.get_by_text('Keep the exact proposed words.', exact=True)
                assert words.is_visible() and words.get_attribute('dir') == 'auto'
                assert chat.locator('p[dir=ltr]').text_content().startswith('@fixture_owner')
                chat.screenshot(path=str(folder / ('summary-chat-' + locale + '.png')))
        # Hostile target text is displayed literally, and hidden display-name controls are removed.
        hostile = {'kind': 'follow', 'target': {'username': 'morgan', 'name': '\u202e<img src=x onerror=window.bad=1>'}}
        result = page.evaluate('(a)=>VoolXSocial.summary(a)', hostile)
        assert '\u202e' not in result and '<img' in result and '@morgan' in result
        page.evaluate('(text)=>{const n=document.createElement("strong");n.textContent=text;document.body.appendChild(n)}', result)
        assert page.evaluate('window.bad || 0') == 0
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate("(text)=>{const card=document.querySelector('.x-action-card');card.querySelector('strong').textContent=text.repeat(12);}", result)
        assert page.locator('.x-action-card').first.evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
        page.locator('.x-action-card').first.screenshot(path=str(folder / 'summary-hostile-narrow.png'))
        assert fake_x.writes() == []
        # The same pending action can still be declined once, without a send.
        response = page.request.post(base + '/api/x/activity', data={'action': 'decline', 'action_id': action['id']})
        assert response.ok and fake_x.writes() == []
        page.reload()
        translated = catalog_for('zh-Hans').text('xs.summary_quote').replace('{author}', 'late_whale')
        state = catalog_for('zh-Hans').text('xact.state_declined')
        page.locator('.x-activity').get_by_text(translated + ' · ' + state, exact=True).wait_for()
        assert not errors, errors
        (folder / 'summary-locales.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close(); thread.join(3)
        resource('port', server.server_port, 'closed')
