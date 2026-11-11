"""The browser's approval card shows up in the chat that asked, in the real served chat page.

Real Chromium on the served /chat page, the real owner HTTP door and the real model tool path
(execute_tool_intent waiting for the owner). Approve and Refuse on the card answer the exact
proposal the model's call is waiting on; nothing is fetched before Approve.
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_live_browser import ROOT_LOAD, world as world


def _serve(rig, tmp_path):
    from core.web.api.service import dispatch_get, dispatch_post

    class App(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, response):
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            for key, value in (response.headers or {}).items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(response.body or b'')))
            self.end_headers()
            self.wfile.write(response.body or b'')

        def do_GET(self):
            path = urlsplit(self.path)
            self._send(dispatch_get(path=path.path, query=parse_qs(path.query), runtime=rig.runtime,
                                    model_name='vool', client_host='127.0.0.1', headers=dict(self.headers)))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self._send(dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=rig.runtime,
                                     model_name='vool', workspace_root_provider=lambda: tmp_path, client_host='127.0.0.1'))

    server = ThreadingHTTPServer(('127.0.0.1', 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from core.runtime_served_origins import register_served_port

    register_served_port(server.server_port)
    return server


def _model_asks(chat, url):
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope
    from tests._toolchain_fixtures import executor_kwargs

    with override('plugin_runtime_tools', True), turn_scope(f'Use the browser to open {url}', chat):
        return execute_tool_intent(
            {'intent': 'vool-browser.navigate', 'arguments': {'url': url}},
            **executor_kwargs(chat, runtime_session_id=chat, surface='api', platform='web_companion',
                              mode='manual', live_browser_wait_for_owner=True),
        )


def test_served_chat_page_wires_the_card_into_its_run_clock_and_log():
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert 'window.VoolBrowserCard = Object.freeze' in html
    # Polled by the run clock of every turn in flight, and put back after the log is rebuilt.
    assert 'window.VoolBrowserCard.tick(run.chatId, isDisplayed(run.chatId))' in html
    assert 'window.VoolBrowserCard.repaint(chatId)' in html


def test_card_in_chat_answers_the_waiting_model_call(world, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    from core.vool_browser import live
    from core.vool_browser.engine import find_engine_binary, isolation_launch_flags

    rig, old, origin, requests = world
    old.stop()
    chat_id = rig.canonical_session()
    rig.post('/api/chat/session', {'session_id': chat_id, 'operation': 'create'})
    item = live.LiveSession(chat_id, fixture_origin=origin)
    live._SESSIONS[live._key(chat_id)] = item
    server = _serve(rig, tmp_path)
    flags = [f for f in isolation_launch_flags(profile_dir=str(tmp_path / 'viewer')) if not f.startswith('--user-data-dir=')]
    try:
        with sync_playwright() as driver, ThreadPoolExecutor(max_workers=1) as pool:
            browser = driver.chromium.launch(executable_path=find_engine_binary(), headless=True, args=flags)
            page = browser.new_page(viewport={'width': 1200, 'height': 860})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/chat#{chat_id}')
            page.wait_for_function('!!window.VoolBrowserCard && !!window.VoolPageActions')
            # Stand-in for the run clock of a turn in flight (no model in this test): the same
            # once-a-second call the page makes while a reply is being written.
            page.evaluate("cid => { window.__clock = setInterval(() => window.VoolBrowserCard.tick(cid, true), 1000); }", chat_id)

            # 1. The model asks; the card appears in the chat; Refuse answers the waiting call.
            future = pool.submit(contextvars.copy_context().run, _model_asks, chat_id, origin + '/')
            card = page.locator('#log .vbc-msg .vbc').last
            expect(card).to_be_visible(timeout=10000)
            expect(card.locator('.vbc-head')).to_contain_text('VOOL wants to use the browser')
            expect(card.locator('.vbc-target')).to_have_text(origin + '/')
            expect(card.locator('.vbc-timer')).to_contain_text('s left')
            assert requests == [] and not future.done()
            shots = os.environ.get('VOOL_BROWSER_SHOTS')
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(shots) / 'chat-card-waiting.png'))
            card.locator('.vbc-btn', has_text='Refuse').click()
            outcome = future.result(timeout=20)
            assert not outcome.ok and requests == []
            expect(card).to_have_attribute('data-state', 'refused', timeout=5000)
            expect(card.locator('.vbc-head')).to_have_text('You said no')
            expect(card.locator('.vbc-btn')).to_have_count(2)
            expect(card.locator('.vbc-btn').first).to_be_hidden()

            # 2. The model asks again; Approve on the card runs exactly that request.
            future = pool.submit(contextvars.copy_context().run, _model_asks, chat_id, origin + '/')
            expect(page.locator('#log .vbc[data-state=waiting]')).to_have_count(1, timeout=10000)
            card = page.locator('#log .vbc[data-state=waiting]')
            assert requests == []
            card.locator('.vbc-btn', has_text='Approve').click()
            outcome = future.result(timeout=30)
            assert outcome.ok and requests == ROOT_LOAD, requests
            done = page.locator('#log .vbc[data-state=done]')
            expect(done).to_have_count(1, timeout=10000)
            expect(done.locator('.vbc-head')).to_have_text('Done')
            expect(done.locator('img.vbc-shot')).to_be_visible()
            assert done.locator('img.vbc-shot').get_attribute('src').startswith('data:image/jpeg;base64,')
            if shots:
                page.screenshot(path=str(Path(shots) / 'chat-card-done.png'))
            # 3. "Open browser" on the card opens the pane over the chat on the same page.
            done.locator('.vbc-link').click()
            pane = page.frame_locator('#settingsFrame')
            expect(pane.locator('#hot button[aria-label="Later trains"]')).to_be_visible(timeout=10000)
            if shots:
                page.screenshot(path=str(Path(shots) / 'chat-card-pane.png'))
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
