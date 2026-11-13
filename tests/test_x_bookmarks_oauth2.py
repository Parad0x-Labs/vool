from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home

ORIGIN = 'http://127.0.0.1:18446'


def _settings(body, *, client_host='127.0.0.1', origin=ORIGIN):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    result = dispatch_post(path='/api/x/oauth', body=body,
        headers={'Content-Type': 'application/json', 'Origin': origin, 'Host': '127.0.0.1:18446'},
        runtime=RuntimeServices(display_name='fixture'), model_name='fixture',
        client_host=client_host, workspace_root_provider=lambda: '.')
    return result.status, json.loads(result.body)


def test_sign_in_starts_only_after_an_owner_settings_action(vault_home):
    code, status = _settings({'action': 'status'})
    assert code == 200 and status == {'ok': True, 'connected': False}
    code, started = _settings({'action': 'begin', 'client_id': 'public-client'})
    assert code == 200 and started['ok'] is True
    query = parse_qs(urlsplit(started['authorize_url']).query)
    assert query['code_challenge_method'] == ['S256']
    assert query['redirect_uri'] == [ORIGIN + '/api/x/oauth/callback']
    assert {'bookmark.read', 'bookmark.write', 'users.read', 'tweet.read'} <= set(query['scope'][0].split())
    assert len(query['state'][0]) >= 32
    assert 'code_verifier' not in started


@pytest.mark.parametrize('client_host,origin', [('192.0.2.1', ORIGIN), ('127.0.0.1', 'https://example.invalid')])
def test_untrusted_sign_in_requests_are_refused(vault_home, client_host, origin):
    code, _ = _settings({'action': 'begin', 'client_id': 'public-client'}, client_host=client_host, origin=origin)
    assert code == 403


class FakeX:
    def __init__(self):
        self.calls = []
        self.token_reply = None
        self.redirect = ''
        self.bookmark_redirect = ''
        self.me_truncated = False
        self.me_refused = False
        self.bookmark_reply = {'data': [{'id': '555', 'text': 'saved post'}], 'meta': {'next_token': 'page-two'}}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body):
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def redirect_bookmark(self):
                if '/bookmarks' in self.path and outer.bookmark_redirect:
                    self.send_response(302)
                    self.send_header('Location', outer.bookmark_redirect)
                    self.end_headers()
                    return True
                return False

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
                fields = parse_qs(raw.decode()) if 'urlencoded' in self.headers.get('Content-Type', '') else json.loads(raw)
                outer.calls.append({'method': 'POST', 'path': self.path, 'fields': fields,
                                    'auth': self.headers.get('Authorization')})
                if self.redirect_bookmark():
                    return
                if outer.redirect:
                    self.send_response(302)
                    self.send_header('Location', outer.redirect)
                    self.end_headers()
                    return
                if '/bookmarks' in self.path:
                    return self.respond(outer.bookmark_reply if outer.bookmark_reply is not None else {'data': {'bookmarked': True}})
                self.respond(outer.token_reply or {'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh',
                    'token_type': 'bearer', 'expires_in': 7200,
                    'scope': 'tweet.read users.read bookmark.read bookmark.write offline.access'})

            def do_GET(self):
                outer.calls.append({'method': 'GET', 'path': self.path, 'auth': self.headers.get('Authorization')})
                if self.redirect_bookmark():
                    return
                if '/bookmarks' in self.path:
                    return self.respond(outer.bookmark_reply)
                if outer.me_refused:
                    raw = json.dumps({'title': 'Forbidden'}).encode()
                    self.send_response(403)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if outer.me_truncated:
                    # The answer is cut off after X has served the account read.
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', '200')
                    self.end_headers()
                    self.wfile.write(b'{"data": {"id": "12')
                    self.close_connection = True
                    return
                self.respond({'data': {'id': '1234', 'username': 'fixture_account'}})

            def do_DELETE(self):
                outer.calls.append({'method': 'DELETE', 'path': self.path, 'auth': self.headers.get('Authorization')})
                if not self.redirect_bookmark():
                    self.respond(outer.bookmark_reply)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.socket.settimeout(0.5)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


BOOKMARK_OPERATIONS = ('getUsersBookmarks', 'createUsersBookmark', 'deleteUsersBookmark')


def _owner_profile(monkeypatch, operations=BOOKMARK_OPERATIONS):
    from core import os_consent_gate, x_capabilities
    config = x_capabilities.defaults()
    config.update(enabled=True, access_label='Local fixture access', operations=dict.fromkeys(operations, 0))
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: True)
    x_capabilities.save(config)
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: pytest.fail('No device prompt expected'))


@pytest.fixture
def fake_x(vault_home, monkeypatch):
    from core import x_oauth2
    _owner_profile(monkeypatch)
    with FakeX() as fake:
        monkeypatch.setattr(x_oauth2, 'TOKEN_URL', fake.url + '/2/oauth2/token')
        monkeypatch.setattr(x_oauth2, 'API_ORIGIN', fake.url)
        yield fake


def _callback(state, code='fixture-code', **extra):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get
    return dispatch_get(path='/api/x/oauth/callback', query={'state': [state], 'code': [code], **extra},
        runtime=RuntimeServices(display_name='fixture'), model_name='fixture', client_host='127.0.0.1')


def _start():
    status, out = _settings({'action': 'begin', 'client_id': 'public-client'})
    assert status == 200
    return parse_qs(urlsplit(out['authorize_url']).query)


def test_pkce_callback_stores_tokens_only_after_verification_and_is_single_use(fake_x):
    from core import x_oauth2
    from core.credential_store import get_credential
    flow = _start()
    assert not x_oauth2.connected()
    result = _callback(flow['state'][0])
    assert result.status == 303
    assert result.headers['Cache-Control'] == 'no-store'
    assert result.headers['Referrer-Policy'] == 'no-referrer'
    assert result.headers['Location'] == '/settings#keys/x'  # back to API Keys with the X fold open
    assert len(fake_x.calls) == 2
    fields = fake_x.calls[0]['fields']
    verifier = fields['code_verifier'][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    assert challenge == flow['code_challenge'][0]
    assert fields['redirect_uri'] == flow['redirect_uri']
    assert fields['code'] == ['fixture-code']
    assert fake_x.calls[1]['auth'] == 'Bearer fixture-access'
    saved = json.loads(get_credential(x_oauth2.SLOT))
    assert saved['account']['id'] == '1234'
    assert saved['access_token'] == 'fixture-access'
    assert 'verifier' not in saved and 'code' not in saved
    assert _callback(flow['state'][0]).status == 303
    assert len(fake_x.calls) == 2, 'callback replay must not exchange the code again'
    assert _settings({'action': 'status'})[1] == {'ok': True, 'connected': True}


def test_wrong_state_denial_and_disconnect_cancel_pending_sign_in(fake_x):
    from core import x_oauth2
    flow = _start()
    _callback('wrong-state')
    assert fake_x.calls == [] and not x_oauth2.connected()
    _callback(flow['state'][0], error=['access_denied'])
    assert fake_x.calls == [] and not x_oauth2.connected()
    flow = _start()
    assert _settings({'action': 'disconnect'})[0] == 200
    _callback(flow['state'][0])
    assert fake_x.calls == [] and not x_oauth2.connected()


def test_missing_scope_and_token_redirect_never_connect(fake_x):
    from core import x_oauth2
    fake_x.token_reply = {'access_token': 'fixture-access', 'token_type': 'bearer',
                         'expires_in': 7200, 'scope': 'tweet.read users.read'}
    _callback(_start()['state'][0])
    assert not x_oauth2.connected() and len(fake_x.calls) == 1
    fake_x.redirect = fake_x.url + '/stolen'
    _callback(_start()['state'][0])
    assert len(fake_x.calls) == 2 and not x_oauth2.connected()


def _bookmarks(operation='read', **args):
    from core.runtime_execution_tools import execute_runtime_tool
    return execute_runtime_tool('x.bookmarks', {'operation': operation, **args}, source_context={'session_id': 'bookmark-chat'})


def _activity(body, *, client_host='127.0.0.1'):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post
    response = dispatch_post(path='/api/x/activity', body=body, headers={'Content-Type': 'application/json'},
        runtime=RuntimeServices(display_name='fixture'), model_name='fixture', client_host=client_host,
        workspace_root_provider=lambda: '.')
    return response.status, json.loads(response.body)


def _pending():
    return _activity({'action': 'overview'})[1]['pending'][0]


def _approve(action=None):
    action = action or _pending()
    return _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']})[1]


def _sign_in():
    from core import x_oauth2
    _callback(_start()['state'][0])
    assert x_oauth2.connected()


def test_bookmarks_stay_off_until_sign_in_without_outbound_calls(fake_x):
    from core.runtime_tool_contracts import _x_contracts
    assert not next(c for c in _x_contracts(False) if c.intent == 'x.bookmarks').supported
    out = _bookmarks()
    assert not out.ok and fake_x.calls == []


def test_bookmark_read_approval_returns_posts_and_each_page_needs_approval(fake_x):
    from core import x_usage
    from core.runtime_execution_tools import execute_runtime_tool
    _sign_in()
    fake_x.calls.clear()
    before = x_usage.snapshot()
    proposed = _bookmarks(max_results=2)
    assert proposed.ok and proposed.status == 'awaiting_approval'
    assert 'hash' not in proposed.details['action']
    assert fake_x.calls == []
    pending = _pending()
    assert _activity({'action': 'approve', 'action_id': pending['id'], 'hash': 'wrong'})[1]['state'] == 'changed'
    assert _activity({'action': 'approve', 'action_id': pending['id'], 'hash': pending['hash']}, client_host='192.0.2.1')[0] == 403
    assert fake_x.calls == []
    assert _approve(pending)['ok']
    assert len(fake_x.calls) == 1
    assert fake_x.calls[0]['auth'] == 'Bearer fixture-access'
    assert parse_qs(urlsplit(fake_x.calls[0]['path']).query)['max_results'] == ['2']
    result = execute_runtime_tool('x.action.status', {'action_id': pending['id']}, source_context={'session_id': 'bookmark-chat'})
    assert result.details['action']['result']['posts'][0]['id'] == '555'
    assert result.details['action']['result']['next_token'] == 'page-two'
    assert not _approve(pending)['ok'] and len(fake_x.calls) == 1
    assert x_usage.snapshot()['reads'] == before['reads'] + 1
    assert x_usage.snapshot()['requests'] == before['requests'] + 1
    assert _bookmarks(max_results=2, next_token='page-two').ok
    assert len(fake_x.calls) == 1
    assert _approve()['ok']
    assert parse_qs(urlsplit(fake_x.calls[-1]['path']).query)['pagination_token'] == ['page-two']


@pytest.mark.parametrize('operation,method,answer', [('add', 'POST', True), ('remove', 'DELETE', False)])
def test_bookmark_changes_send_the_frozen_target_once_after_settings_approval(fake_x, operation, method, answer):
    _sign_in()
    fake_x.calls.clear()
    fake_x.bookmark_reply = {'data': {'bookmarked': answer}}
    assert _bookmarks(operation, post='https://x.com/fixture/status/555').ok
    assert fake_x.calls == []
    action = _pending()
    assert action['target']['post_id'] == '555'
    assert _approve(action)['ok']
    assert len(fake_x.calls) == 1 and fake_x.calls[0]['method'] == method
    assert fake_x.calls[0]['path'] == '/2/users/1234/bookmarks' + ('/555' if operation == 'remove' else '')
    if operation == 'add':
        assert fake_x.calls[0]['fields'] == {'tweet_id': '555'}
    assert not _approve(action)['ok'] and len(fake_x.calls) == 1


def test_decline_disconnect_and_account_change_never_send(fake_x):
    from core import x_oauth2
    _sign_in()
    fake_x.calls.clear()
    assert _bookmarks('add', post='555').ok
    action = _pending()
    assert _activity({'action': 'decline', 'action_id': action['id']})[1]['ok']
    assert not _approve(action)['ok']
    assert _bookmarks('add', post='555').ok
    action = _pending()
    saved = x_oauth2._load()
    saved['account']['id'] = '999'
    x_oauth2._save(saved)
    assert _approve(action)['state'] == 'account_changed'
    x_oauth2.disconnect()
    assert _approve(action)['state'] == 'needs_account'
    assert fake_x.calls == []


@pytest.mark.parametrize('reply', [{'data': [] , 'meta': 'bad'}, {'data': [{'id': '555'}]}, {'errors': [{'detail': 'fixture'}]}, {'data': [{'id': '1', 'text': 'a'}, {'id': '2', 'text': 'b'}]}])
def test_unconfirmed_reads_count_delivered_items_or_the_ceiling_and_never_retry(fake_x, reply):
    from core import x_usage
    _sign_in()
    before = x_usage.snapshot()['reads']
    fake_x.calls.clear()
    fake_x.bookmark_reply = reply
    assert _bookmarks(max_results=1).ok
    action = _pending()
    assert _approve(action)['state'] == 'unknown'
    assert len(fake_x.calls) == 1
    delivered = len(reply['data']) if isinstance(reply.get('data'), list) else 0
    assert x_usage.snapshot()['reads'] == before + max(1, delivered)
    assert not _approve(action)['ok'] and len(fake_x.calls) == 1


def test_expired_state_and_denial_without_code_are_single_use(fake_x):
    from core import x_oauth2
    flow = _start()
    x_oauth2._flows[flow['state'][0]]['started'] -= 301
    _callback(flow['state'][0])
    flow = _start()
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get
    dispatch_get(path='/api/x/oauth/callback', query={'state': flow['state'], 'error': ['access_denied']},
        runtime=RuntimeServices(display_name='fixture'), model_name='fixture', client_host='127.0.0.1')
    _callback(flow['state'][0])
    assert not x_oauth2.connected() and fake_x.calls == []


def test_refresh_rotates_through_sealed_store_only_after_approval(fake_x):
    from core import x_oauth2
    _sign_in()
    saved = x_oauth2._load()
    saved['expires_at'] = 1
    x_oauth2._save(saved)
    fake_x.calls.clear()
    fake_x.token_reply = {'access_token': 'fixture-rotated', 'refresh_token': 'fixture-refresh-rotated',
                         'token_type': 'bearer', 'expires_in': 7200}
    assert _bookmarks(max_results=2).ok and fake_x.calls == []
    assert _approve()['ok']
    assert len(fake_x.calls) == 2
    assert fake_x.calls[0]['fields']['grant_type'] == ['refresh_token']
    assert fake_x.calls[1]['auth'] == 'Bearer fixture-rotated'
    assert x_oauth2._load()['refresh_token'] == 'fixture-refresh-rotated'


def test_limits_and_credential_write_failure_send_no_bookmark(fake_x, monkeypatch):
    from core import credential_store, x_oauth2, x_usage
    _sign_in()
    fake_x.calls.clear()
    assert _bookmarks(max_results=2).ok
    action = _pending()
    x_usage.set_limits(monthly_reads=1)
    assert _approve(action)['state'] == 'usage_limit_reached'
    assert fake_x.calls == []
    x_usage.set_limits(monthly_reads=100)
    saved = x_oauth2._load()
    saved['expires_at'] = 1
    x_oauth2._save(saved)
    def fail(*args, **kwargs):
        raise OSError('fixture sealed store failure')
    monkeypatch.setattr(credential_store, 'store_credential', fail)
    assert _approve(action)['state'] == 'needs_account'
    assert len(fake_x.calls) == 1 and fake_x.calls[0]['path'] == '/2/oauth2/token'
    assert x_usage.snapshot()['actions'] == 0


@pytest.mark.parametrize('text', ['show my X bookmarks', 'add this to my X bookmarks', 'remove this from my X bookmarks', 'bookmark this tweet'])
def test_bookmark_requests_offer_the_bookmark_tool(text):
    from core.tool_demand_signals import resolve_demand_signals
    assert 'x.bookmarks' in resolve_demand_signals(text).explicit_intents


def test_incomplete_refresh_credentials_release_all_holds_without_sending(fake_x):
    from core import x_oauth2, x_usage

    _sign_in()
    saved = x_oauth2._load()
    saved.pop('client_id')
    saved['expires_at'] = 1
    x_oauth2._save(saved)
    fake_x.calls.clear()
    before = x_usage.snapshot()
    assert _bookmarks(max_results=2).ok
    assert _approve()['state'] == 'needs_account'
    assert fake_x.calls == []
    after = x_usage.snapshot()
    assert after['actions'] == before['actions']
    assert after['reads'] == before['reads']
    assert _pending()['state'] == 'pending'


@pytest.mark.parametrize('operation', ['read', 'add', 'remove'])
def test_approval_never_follows_a_redirect_even_within_the_same_origin(fake_x, operation):
    _sign_in()
    fake_x.calls.clear()
    fake_x.bookmark_redirect = fake_x.url + '/unapproved'
    assert _bookmarks(operation, max_results=2, post='555').ok
    action = _pending()
    assert _approve(action)['state'] == 'unknown'
    assert len(fake_x.calls) == 1
    assert '/bookmarks' in fake_x.calls[0]['path']
    assert not _approve(action)['ok']
    assert len(fake_x.calls) == 1


def test_usage_tool_works_with_only_bookmark_sign_in_and_stays_local(fake_x):
    from core.runtime_execution_tools import execute_runtime_tool

    _sign_in()
    fake_x.calls.clear()
    result = execute_runtime_tool('x.usage', {})
    assert result.ok and result.status == 'executed'
    assert result.details['usage']['reads'] == 1
    assert result.details['usage']['actions'] == 0
    assert fake_x.calls == []


def test_truncated_account_checks_keep_their_reads_used_and_never_free_the_limit(fake_x):
    from core import x_oauth2, x_usage
    fake_x.me_truncated = True
    before = x_usage.snapshot()['reads']
    for _ in range(2):
        _callback(_start()['state'][0])
        assert not x_oauth2.connected()
    account_reads = [call for call in fake_x.calls if call['method'] == 'GET' and call['path'] == '/2/users/me']
    assert len(account_reads) == 2
    assert x_usage.snapshot()['reads'] == before + 2
    x_usage.set_limits(monthly_reads=before + 2)
    _callback(_start()['state'][0])
    assert not x_oauth2.connected()
    assert [call for call in fake_x.calls if call['method'] == 'GET'] == account_reads
    assert x_usage.snapshot()['reads'] == before + 2


def test_refused_account_check_is_not_counted_as_a_read(fake_x):
    from core import x_oauth2, x_usage
    fake_x.me_refused = True
    before = x_usage.snapshot()['reads']
    _callback(_start()['state'][0])
    assert not x_oauth2.connected()
    assert [call['path'] for call in fake_x.calls if call['method'] == 'GET'] == ['/2/users/me']
    assert x_usage.snapshot()['reads'] == before


def test_bookmarked_posts_are_kept_only_in_the_sealed_store(fake_x, vault_home):
    from core.runtime_execution_tools import execute_runtime_tool
    from tests.test_x_full_integration import _home_files_containing
    _sign_in()
    fake_x.bookmark_reply = {'data': [{'id': '555', 'text': 'private bookmarked fixture words'}], 'meta': {}}
    assert _bookmarks(max_results=1).ok
    action = _pending()
    assert _approve(action)['ok']
    shown = execute_runtime_tool('x.action.status', {'action_id': action['id']}, source_context={'session_id': 'bookmark-chat'})
    assert shown.details['action']['result']['posts'][0]['text'] == 'private bookmarked fixture words'
    assert _home_files_containing(vault_home, 'private bookmarked fixture words') == []
