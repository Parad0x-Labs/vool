"""Installed skill to native tools to owner approval through the real daemon.

The external provider is a scripted protocol fixture; editorial quality is unmeasured.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests._served_skill_rig import ProviderState, ServedDaemon, make_provider_server
from tests.x_social.support import resource
from tests.x_social.test_skill_reach import TOOLS

DECLARED_TOOLS = tuple(t for t in TOOLS if not os.environ.get("SOCIAL_M6_LEGACY") or t not in {"x.feedback.save", "x.feedback.list", "x.results"})


def test_installed_skill_reaches_native_x_flow_and_owner_card(vault_home, tmp_path, monkeypatch):
    import tests._served_skill_rig as provider
    from tests.served_browser import launch_chromium

    requests = []
    lose_answer = [False]
    class XHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def answer(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            path = urlsplit(self.path).path
            requests.append(('GET', path))
            post = {'id': '555', 'author_id': '22', 'text': 'Recovering interrupted builds.', 'created_at': '2026-10-20T01:00:00Z', 'public_metrics': {'like_count': 7}}
            if path == '/2/tweets/search/recent' or path == '/2/tweets':
                return self.answer(200, {'data': [post], 'includes': {'users': [{'id': '22', 'username': 'builder', 'name': 'Builder'}]}})
            return self.answer(404, {'title': 'Not Found Error'})
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)))
            requests.append(('POST', urlsplit(self.path).path, body))
            if lose_answer[0]:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            self.answer(201, {'data': {'id': '999', 'text': body['text']}})
    xserver = ThreadingHTTPServer(('127.0.0.1', 0), XHandler)
    threading.Thread(target=xserver.serve_forever, daemon=True).start()
    resource('port', xserver.server_port, 'started')
    state = ProviderState()
    cursor = [0]
    offered = []
    script = [
        ('x.usage', {}),
        ('x.search', {'query': 'checkpoint resume', 'max_results': 10}),
        ('x.post', {'post': '555', 'kind': 'post'}),
        ('x.action.propose', {'kind': 'quote', 'post': '555', 'text': 'Checkpoint resume makes interrupted work recoverable.', 'username': '', 'list_id': '', 'name': '', 'description': None, 'private': None}),
    ]
    def native_response(body, state, model):
        if not body.get('tools'):
            return provider._completion(content='Protocol fixture continuation.')
        names = {t['function']['name'] for t in body['tools']}
        offered.append(names)
        if cursor[0] >= len(script):
            return provider._completion(content='Proposal ready for owner review.')
        intent, args = script[cursor[0]]
        cursor[0] += 1
        name = intent.replace('.', '__')
        if name not in names:
            name = 'respond__direct'
            args = {'message': 'Requested tool unavailable: ' + intent}
        return provider._completion(tool_calls=[provider._tool_call('skill-flow-' + str(cursor[0]), name, args)], finish='tool_calls')
    monkeypatch.setattr(provider, '_turn_response', native_response)
    server, provider_port = make_provider_server(state)
    resource('port', provider_port, 'started')
    bootstrap = tmp_path / 'bootstrap'
    bootstrap.mkdir()
    skill = tmp_path / 'social-manager' / 'SKILL.md'
    skill.parent.mkdir()
    skill.write_text('---\nname: social-manager\ndescription: orchard social-manager workflow\ntriggers: [orchard, social-manager]\nallowed-tools: [' + ', '.join(DECLARED_TOOLS) + ']\n---\nCheck usage, search, read post context, then propose one quote for owner approval.\n')
    # Only external X routing changes. Skill activation uses the production validator/installer.
    (bootstrap / 'sitecustomize.py').write_text('''import os
import socket
if os.environ.get('SOCIAL_SKILL_FIXTURE'):
 from core import x_api, policy_engine
 x_api.API_ORIGIN=os.environ['SOCIAL_SKILL_FIXTURE']
 from tests.test_x_api_reads import _connect
 _connect()
 from core.skill_tools import install_skill
 verdict=install_skill(os.environ['SOCIAL_SKILL_FILE'])
 assert verdict['status']=='ok',verdict
''')
    config = vault_home / 'config' / 'default_policy.yaml'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('x:\n  trending_enabled: true\n')
    daemon = ServedDaemon(vault_home, provider_port, extra_env={
        'PYTHONPATH': str(bootstrap) + os.pathsep + str(Path(__file__).resolve().parents[2]),
        'SOCIAL_SKILL_FIXTURE': f'http://127.0.0.1:{xserver.server_port}',
        'SOCIAL_SKILL_FILE': str(skill),
        'VOOL_PLUGINS_DIR': str(vault_home / 'plugin-tree'),
        'VOOL_PLUGIN_LIFECYCLE_PATH': str(vault_home / 'config' / 'plugin_lifecycle.json'),
        'VOOL_PLUGIN_SCRATCH_ROOT': str(vault_home / 'plugin-scratch'),
        'VOOL_LOCAL_MODELS_ENABLED': '1', 'VOOL_REGISTER_INSTALLED_OLLAMA_MODELS': '0',
        'VOOL_SKIP_PROVIDER_PREWARM': '1',
    })
    manager = browser = None
    try:
        daemon.start(wait=False)
        proc = daemon.proc
        resource('process', proc.pid, 'started')
        resource('port', daemon.port, 'started')
        daemon.wait_ready()
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        assert (cert.get('result') or cert).get('state') == 'verified', cert
        state.requests.clear()
        payload = {'model': 'custom-byok:probe-model', 'messages': [{'role': 'user', 'content': 'Check my X API usage and research checkpoint discussions for orchard social-manager; read the post and propose one quote.'}], 'surface': 'desktop', 'stream': False, 'mode': 'auto', 'session_id': 'skill-social-flow', 'turn_id': 'skill-social-origin'}
        reply = daemon.post('/v1/chat/completions', payload, timeout=90)
        debug = Path(os.environ['SOCIAL_SHOTS']); debug.mkdir(parents=True, exist_ok=True)
        (debug / 'provider-wire.json').write_text(json.dumps({'reply': reply, 'requests': state.requests, 'events': daemon.session_events(reply.get('vool_session_id', 'skill-social-flow'))}, indent=2))
        assert cursor[0] == 4, (reply, cursor, offered)
        required = {t.replace('.', '__') for t in DECLARED_TOOLS}
        assert offered and all(required <= names for names in offered), offered
        overview = daemon.post('/api/x/activity', {'action': 'overview'})
        assert len(overview['pending']) == 1, (reply, overview)
        action = overview['pending'][0]
        assert action['kind'] == 'quote' and action['text'] == script[-1][1]['text']
        assert not [r for r in requests if r[0] == 'POST']
        manager, browser = launch_chromium()
        page = browser.new_page(viewport={'width': 1180, 'height': 1000})
        page.goto(daemon.base_url + '/chat#' + reply['vool_session_id'])
        card = page.locator('.vx-approval').filter(has_text=action['text'])
        card.wait_for(timeout=30000)
        shots = Path(os.environ['SOCIAL_SHOTS']); shots.mkdir(parents=True, exist_ok=True)
        card.screenshot(path=str(shots / 'installed-skill-card.png'))
        card.get_by_role('button', name='Approve and send', exact=True).click()
        page.wait_for_function("document.querySelector('.vx-approval [role=status]').textContent === 'Sent'")
        assert len([r for r in requests if r[0] == 'POST']) == 1
        with pytest.raises(urllib.error.HTTPError) as duplicate:
            daemon.post('/api/x/activity', {'action': 'approve', 'action_id': action['id'], 'hash': action['hash']})
        assert duplicate.value.code == 400
        assert json.load(duplicate.value)['state'] == 'not_pending'
        assert len([r for r in requests if r[0] == 'POST']) == 1
        failure_receipts = {}
        for outcome in ('decline', 'expiry', 'unknown'):
            script[:] = [('x.action.propose', {
                'kind': 'quote', 'post': '555', 'text': 'Checkpoint detail for ' + outcome + '.',
                'username': '', 'list_id': '', 'name': '', 'description': None, 'private': None,
            })]
            cursor[0] = 0
            payload['turn_id'] = 'skill-social-' + outcome
            payload['session_id'] = reply['vool_session_id']
            payload['messages'] = [{'role': 'user', 'content': 'For orchard social-manager, propose one quote on X about checkpoint recovery.'}]
            turn = daemon.post('/v1/chat/completions', payload, timeout=90)
            pending = daemon.post('/api/x/activity', {'action': 'overview'})['pending']
            assert len(pending) == 1, (outcome, turn, pending)
            candidate = pending[0]
            before = len([r for r in requests if r[0] == 'POST'])
            if outcome == 'decline':
                result = daemon.post('/api/x/activity', {'action': 'decline', 'action_id': candidate['id']})
                assert result['ok']
            else:
                if outcome == 'expiry':
                    # A persisted old pending row, not a stubbed clock or approval implementation.
                    path = vault_home / 'data' / 'x_actions.json'
                    stored = json.loads(path.read_text())
                    stored['actions'][candidate['id']]['created_at'] = time.time() - 86401
                    path.write_text(json.dumps(stored))
                else:
                    lose_answer[0] = True
                with pytest.raises(urllib.error.HTTPError) as denied:
                    daemon.post('/api/x/activity', {'action': 'approve', 'action_id': candidate['id'], 'hash': candidate['hash']})
                result = json.load(denied.value)
                assert result['state'] == ('not_pending' if outcome == 'expiry' else 'unknown'), result
            after = len([r for r in requests if r[0] == 'POST'])
            assert after == before + (outcome == 'unknown')
            with pytest.raises(urllib.error.HTTPError):
                daemon.post('/api/x/activity', {'action': 'approve', 'action_id': candidate['id'], 'hash': candidate['hash']})
            assert len([r for r in requests if r[0] == 'POST']) == after
            failure_receipts[outcome] = {'posts_before': before, 'posts_after': after, 'result': result}
        assert daemon.post('/api/x/setup', {'action': 'remove'})['ok']
        script[:] = [('x.usage', {})]
        cursor[0] = 0
        before = len(requests)
        offers_before = len(offered)
        payload['turn_id'] = 'skill-social-disconnected'
        payload['session_id'] = 'skill-social-new-disconnected-chat'
        payload['messages'] = [{'role': 'user', 'content': 'Check my X API usage and research checkpoint discussions for orchard social-manager; read the post and propose one quote.'}]
        disconnected = daemon.post('/v1/chat/completions', payload, timeout=90)
        assert cursor[0] in {0, 1}, disconnected
        assert all('x__usage' not in names and 'x__search' not in names for names in offered[offers_before:])
        assert any(word in disconnected['choices'][0]['message']['content'].lower() for word in ('unavailable', 'not connected', 'disabled', 'connect your'))
        assert len(requests) == before
        assert not daemon.post('/api/x/activity', {'action': 'overview'})['pending']
        failure_receipts['disconnected'] = {'x_requests': len(requests) - before, 'reply': disconnected}
        (shots / 'failure-receipts.json').write_text(json.dumps(failure_receipts, indent=2))
        (shots / 'served-proof.json').write_text(json.dumps({'reply': reply, 'offers': [sorted(n) for n in offered], 'requests': requests}, indent=2))
    finally:
        if browser: browser.close()
        if manager: manager.stop()
        if daemon.proc:
            proc = daemon.proc
            proc.terminate()
            try: proc.wait(timeout=15)
            except Exception:
                proc.kill(); proc.wait(timeout=5)
            resource('process', proc.pid, 'closed')
            resource('port', daemon.port, 'closed')
        server.shutdown(); server.server_close(); resource('port', provider_port, 'closed')
        xserver.shutdown(); xserver.server_close(); resource('port', xserver.server_port, 'closed')
