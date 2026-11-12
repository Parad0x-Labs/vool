"""Browser sign-in and Settings approval with local X endpoints and consent fixture."""
from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_bookmarks_oauth2 import fake_x


@pytest.mark.parametrize('initial_reads', [1000, 0])
def test_sign_in_bookmark_approval_and_disconnect_in_served_settings(fake_x, vault_home, initial_reads):
    from core import x_oauth2, x_usage
    from core.runtime_execution_tools import execute_runtime_tool
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    x_usage.set_limits(monthly_reads=initial_reads)
    runtime = RuntimeServices(display_name='bookmark fixture')
    errors = []
    outside = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, res):
            self.send_response(res.status)
            self.send_header('Content-Type', res.content_type)
            for key, value in (res.headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(res.body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == '/settings':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(render_vool_settings_html().encode())
                return
            self.send(dispatch_get(path=url.path, query=parse_qs(url.query), runtime=runtime,
                                   model_name='fixture', client_host='127.0.0.1'))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or '{}')
            self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                                    runtime=runtime, model_name='fixture', client_host='127.0.0.1',
                                    workspace_root_provider=lambda: str(vault_home)))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    context = browser.new_context(viewport={'width': 1120, 'height': 900})

    def guard(route):
        url = urlsplit(route.request.url)
        if url.hostname == '127.0.0.1':
            return route.continue_()
        if url.hostname == 'x.com' and url.path == '/i/oauth2/authorize':
            query = parse_qs(url.query)
            callback = query['redirect_uri'][0] + '?' + urlencode({'state': query['state'][0], 'code': 'fixture-code'})
            return route.fulfill(status=200, content_type='text/html',
                                 body='<a href="' + html.escape(callback, quote=True) + '">Allow fixture</a>')
        outside.append(route.request.url)
        route.abort()

    context.route('**/*', guard)
    try:
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        origin = f'http://127.0.0.1:{server.server_port}'
        page.goto(origin + '/settings#keys')
        box = page.locator('.x-bookmark-setup')
        box.locator('.x-bookmark-state').filter(has_text='off until you sign in').wait_for()
        assert fake_x.calls == [] and outside == []
        if initial_reads == 0:
            reads = page.get_by_label('Most reads a month', exact=False)
            assert reads.input_value() == '0'
            reads.fill('1000')
            page.get_by_role('button', name='Save limits', exact=True).click()
            page.locator('.x-activity-note').filter(has_text='Limits saved.').wait_for()
            assert fake_x.calls == []
        box.get_by_role('button', name='Prepare sign-in', exact=True).click()
        box.locator('.x-bookmark-state').filter(has_text='Could not complete sign-in').wait_for()
        assert fake_x.calls == []
        box.get_by_label('X Client ID', exact=True).fill('public-client')
        box.get_by_role('button', name='Prepare sign-in', exact=True).click()
        link = box.get_by_role('link', name='Continue on X', exact=True)
        link.wait_for()
        assert fake_x.calls == [], 'preparation must not ask X or read bookmarks'
        with page.expect_popup() as popup:
            link.click()
        consent = popup.value
        consent.get_by_role('link', name='Allow fixture', exact=True).click()
        consent.locator('.x-bookmark-state').filter(has_text='Bookmarks are connected').wait_for()
        assert len(fake_x.calls) == 2 and x_oauth2.connected()
        assert 'fixture-code' not in consent.url
        assert 'fixture-access' not in consent.content()
        # A model proposal is local; only the real Settings button sends the frozen read.
        proposed = execute_runtime_tool('x.bookmarks', {'operation': 'read', 'max_results': 2},
                                        source_context={'session_id': 'browser-bookmarks'})
        assert proposed.ok and len(fake_x.calls) == 2
        consent.reload()
        card = consent.locator('.x-action-card')
        card.wait_for()
        assert 'Read up to 2 of your X bookmarks' in card.inner_text()
        assert '@fixture_account' in card.inner_text() and 'First page' in card.inner_text()
        card.get_by_role('button', name='Approve and send: Read up to 2 of your X bookmarks · First page · @fixture_account', exact=True).click()
        card.wait_for(state='detached')
        assert len(fake_x.calls) == 3 and '/bookmarks?' in fake_x.calls[-1]['path']
        status = execute_runtime_tool('x.action.status', {'action_id': proposed.details['action']['id']},
                                      source_context={'session_id': 'browser-bookmarks'})
        assert status.details['action']['result']['posts'][0]['id'] == '555'
        consent.get_by_role('button', name='Disconnect bookmarks', exact=True).click()
        consent.locator('.x-bookmark-state').filter(has_text='off until you sign in').wait_for()
        assert not x_oauth2.connected() and len(fake_x.calls) == 3
        assert consent.get_by_label('Most reads a month', exact=False).input_value() == '1000'
        assert errors == [] and outside == []
    finally:
        context.close()
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
