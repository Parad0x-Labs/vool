"""Documented X operations and owner configuration on the production served API."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from tests._credential_intelligence_support import isolated_home, vault_home


@pytest.fixture
def priced_ledger(vault_home):
    from storage.db import active_default_db_path, configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    previous = active_default_db_path()
    configure_default_db_path(Path(vault_home) / 'data/x-priced-fixture.db')
    reset_default_connection()
    run_migrations()
    try:
        yield
    finally:
        configure_default_db_path(previous)
        reset_default_connection()


def _configuration(*, enabled=True, cost=0):
    from core.x_capabilities import defaults

    config = defaults()
    config.update(enabled=enabled, access_label='Local fixture access',
                  operations={'searchCommunities': cost, 'createPosts': cost})
    config['limits'] = {'per_call_microusd': cost, 'per_task_microusd': cost * 2,
                        'daily_microusd': cost * 3, 'monthly_microusd': cost * 4}
    return config


def _post(body, *, client='127.0.0.1', headers=None):
    from apps.vool_api_server import create_app
    if body.get('action') == 'save' and 'expected_fingerprint' not in body:
        from core import x_capabilities

        body = {**body, 'expected_fingerprint': x_capabilities.fingerprint(x_capabilities.load())}

    async def run():
        transport = httpx.ASGITransport(app=create_app(), client=(client, 40123))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as session:
            return await session.post('/api/x/capabilities', json=body, headers=headers or {})
    return asyncio.run(run())


def test_catalog_and_validated_new_read_and_mutation():
    from core.x_endpoint_catalog import match, operation, operations, prepare

    entries = operations()
    assert len({row['id'] for row in entries}) == len(entries)
    assert len({(row['method'], row['path']) for row in entries}) == len(entries)
    assert all(operation(row['id']) == row for row in entries)
    read = prepare('searchCommunities', query={'query': 'gardening', 'max_results': 10})
    assert read['method'] == 'GET' and read['path'] == '/2/communities/search'
    assert read['query'] == {'query': 'gardening', 'max_results': 10}
    write = prepare('createPosts', body={'text': 'A local fixture post'})
    assert write['method'] == 'POST' and write['body'] == {'text': 'A local fixture post'}
    assert match('DELETE', '/2/lists/123') == 'deleteLists'
    # A caller cannot mutate the process-cached catalog for a later approval.
    entries[0]['method'] = 'TRACE'
    assert operations()[0]['method'] != 'TRACE'


@pytest.mark.parametrize('operation_id,path,query,body', [
    ('notAnOperation', {}, {}, None),
    ('searchCommunities', {}, {'url': 'https://example.invalid'}, None),
    ('searchCommunities', {}, {'query': 'garden', 'max_results': True}, None),
    ('searchCommunities', {}, {'query': 'garden', 'max_results': -1}, None),
    ('deleteLists', {'id': '../account'}, {}, None),
    ('deleteLists', {'id': '%2faccount'}, {}, None),
    ('deleteLists', {'id': '123', 'host': 'example.invalid'}, {}, None),
    ('createPosts', {}, {}, None),
    ('createPosts', {}, {}, {'text': 123}),
    ('createPosts', {}, {}, {'text': 'ok', 'value': float('nan')}),
])
def test_invalid_request_refused_before_any_outbound_door(operation_id, path, query, body):
    from core.x_endpoint_catalog import InvalidXRequest, prepare

    with pytest.raises(InvalidXRequest):
        prepare(operation_id, path=path, query=query, body=body)


EXTRA_PERMISSION = {'getDeveloperAccount', 'ensureAccount', 'dmConversationsMediaDownload', 'getSpacesBuyers'}


def test_sign_in_and_bookmark_operations_need_their_credential_and_private_reads_wait(vault_home, monkeypatch):
    from core.x_capabilities import XCapabilityRefused, validate_policy
    from core.x_endpoint_catalog import APPROVAL_READS, InvalidXRequest, needs_approval, operation, operations, prepare

    # Owner decision (19 Oct): the sign-in keeps its current permissions, so the four features that
    # need more stay unavailable and say why; every other operation can join the profile.
    assert {entry['id']: entry['deferred'] for entry in operations() if entry['deferred']} == dict.fromkeys(
        EXTRA_PERMISSION, 'extra_permission')
    for name in EXTRA_PERMISSION:
        with pytest.raises(InvalidXRequest, match='extra X permission'):
            prepare(name)
        config = _configuration()
        config['operations'] = {name: 0}
        with pytest.raises(XCapabilityRefused, match='extra X permission') as refusal:
            validate_policy(config)
        assert refusal.value.status == 'needs_x_permission'
    assert {'getUsersBookmarks', 'getUsersBookmarkFolders', 'getUsersBookmarksByFolderId'} == APPROVAL_READS
    assert all(operation(name)['method'] == 'GET' and needs_approval(operation(name)) for name in APPROVAL_READS)
    assert not needs_approval(operation('searchCommunities'))
    _enable(monkeypatch)
    config = _configuration()
    config['operations'] = dict.fromkeys(['createUsersBookmark', 'getUsersBookmarks', 'searchCommunities'], 0)
    assert _post({'action': 'save', 'configuration': config}).status_code == 200
    from core import x_api

    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'getDeveloperAccount'})
        assert not result.ok and provider.requests == []
        result = _tool({'operation_id': 'createUsersBookmark', 'path': {'id': '123'}, 'body': {'tweet_id': '555'}})
        assert not result.ok and result.status == 'needs_account', result  # sign-in only; keys are not enough
        result = _tool({'operation_id': 'getUsersBookmarks', 'path': {'id': '123'}})
        assert result.ok and result.status == 'awaiting_approval', result
        assert provider.requests == []


def test_fresh_served_configuration_off_and_remote_and_cross_origin_refused(vault_home):
    response = _post({'action': 'overview'})
    assert response.status_code == 200
    data = response.json()
    assert data['configuration']['enabled'] is False
    assert data['configuration']['operations'] == {}
    assert any(row['id'] == 'searchCommunities' for row in data['features'])
    assert _post({'action': 'overview'}, client='192.0.2.10').status_code == 403
    assert _post({'action': 'overview'}, headers={'Origin': 'https://example.invalid'}).status_code == 403
    assert _post({'action': 'overview', 'owner': True}).status_code == 400


def test_served_enable_device_decline_preserves_state_then_accept_persists(vault_home, monkeypatch):
    from core import os_consent_gate, x_capabilities

    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: False)
    response = _post({'action': 'save', 'configuration': _configuration()})
    assert response.status_code == 400 and response.json()['state'] == 'approval_refused'
    assert x_capabilities.load()['enabled'] is False
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: True)
    response = _post({'action': 'save', 'configuration': _configuration(cost=100)})
    assert response.status_code == 200
    persisted = x_capabilities.load()
    assert persisted == _configuration(cost=100)
    assert x_capabilities.permitted('searchCommunities')[1] == 100
    with pytest.raises(x_capabilities.XCapabilityRefused, match='not included'):
        x_capabilities.permitted('deleteLists')
    previous = x_capabilities.fingerprint(persisted)
    disabled = {**persisted, 'enabled': False}
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: pytest.fail('Disabling must not prompt'))
    assert _post({'action': 'save', 'configuration': disabled}).status_code == 200
    with pytest.raises(x_capabilities.XCapabilityRefused):
        x_capabilities.permitted('searchCommunities', expected_fingerprint=previous)


def test_invalid_or_corrupt_configuration_never_falls_back_to_enabled(vault_home):
    from core import x_capabilities

    config = _configuration()
    config['operations']['searchCommunities'] = False
    with pytest.raises(x_capabilities.XCapabilityRefused):
        x_capabilities.validate_policy(config)
    path = x_capabilities._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'enabled': True}))
    with pytest.raises(x_capabilities.XCapabilityRefused) as refusal:
        x_capabilities.permitted('searchCommunities')
    assert refusal.value.status == 'configuration_unreadable'


def test_settings_revision_checked_before_and_after_device_approval(vault_home, monkeypatch):
    from core import os_consent_gate, x_capabilities

    initial = x_capabilities.fingerprint(x_capabilities.load())
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: pytest.fail('Stale saves must not prompt'))
    response = _post({'action': 'save', 'configuration': _configuration(), 'expected_fingerprint': '0' * 64})
    assert response.json()['state'] == 'configuration_changed'

    def another_tab(reason):
        x_capabilities.save({**x_capabilities.defaults(), 'access_label': 'Other tab'})
        return True

    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', another_tab)
    response = _post({'action': 'save', 'configuration': _configuration(), 'expected_fingerprint': initial})
    assert response.json()['state'] == 'configuration_changed'
    assert x_capabilities.load()['access_label'] == 'Other tab'
    assert x_capabilities.load()['enabled'] is False


@pytest.mark.parametrize('locale,price,unit', [('en', '0.100001', '1'), ('fr', '0,100001', '1'), ('ar', '٠٫١٠٠٠٠١', '١')])
def test_served_settings_prices_save_reload_disable_without_provider_calls(vault_home, monkeypatch, locale, price, unit):
    from urllib.parse import parse_qs, urlsplit

    from core import os_consent_gate, x_capabilities
    from core.i18n.catalog import catalog_for
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    prompts, errors = [], []
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: prompts.append(reason) or True)
    runtime = RuntimeServices(display_name='Feature settings fixture')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, response):
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/settings':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(render_vool_settings_html(ui_locale=locale).encode())
            else:
                self.send(dispatch_get(path=parsed.path, query=parse_qs(parsed.query), runtime=runtime,
                                       model_name='fixture', client_host='127.0.0.1'))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
            self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                                    runtime=runtime, model_name='fixture', client_host='127.0.0.1',
                                    workspace_root_provider=lambda: str(vault_home)))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={'width': 1120, 'height': 900})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}/settings#keys')
        host = page.locator('.x-features')
        host.locator('.x-features-save').wait_for()
        assert catalog_for(locale).text('xfull.title') in host.inner_text()
        assert not x_capabilities.load()['enabled'] and not prompts
        host.locator('.x-features-access').fill('My selected access')
        host.locator('.x-features-enabled').check()
        for limit in host.locator('[data-x-limit]').all():
            limit.fill(unit)
        host.locator('.x-features-search').fill('searchCommunities')
        row = host.locator('[data-operation-id="searchCommunities"]')
        row.locator('input[type=checkbox]').check()
        row.locator('.x-feature-price').fill('invalid')
        host.locator('.x-features-save').click()
        page.get_by_text(catalog_for(locale).text('xfull.invalid_money'), exact=True).wait_for()
        assert not prompts and not x_capabilities.load()['enabled']
        row.locator('.x-feature-price').fill(price)
        # Searching away and back must retain the proposed price.
        host.locator('.x-features-search').fill('createPosts')
        host.locator('.x-features-search').fill('searchCommunities')
        assert row.locator('.x-feature-price').input_value() == price
        host.locator('.x-features-save').click()
        host.locator('.x-features-result').wait_for()
        config = x_capabilities.load()
        assert config['enabled'] and config['operations'] == {'searchCommunities': 100001}
        assert set(config['limits'].values()) == {1000000}
        assert len(prompts) == 1 and 'My selected access' in prompts[0]
        assert prompts[0].startswith(catalog_for(locale).text('xfull.device_prompt').split('{')[0])
        page.reload()
        host.locator('.x-features-off').wait_for()
        host.locator('.x-features-search').fill('searchCommunities')
        assert row.locator('.x-feature-price').input_value() == price
        host.locator('.x-features-off').click()
        host.locator('.x-features-result').wait_for()
        assert x_capabilities.load()['enabled'] is False and len(prompts) == 1
        if locale in {'en', 'fr'}:
            from core import credential_store, x_api
            from tests.test_x_api_reads import BEARER, KEYS

            credential_store.store_credential('x.api.default', json.dumps({**KEYS, 'bearer_token': BEARER,
                                              'account': {'id': '123', 'username': 'fixture_owner'}}), label='Local X fixture')
            saved = page.request.post(f'http://127.0.0.1:{server.server_port}/api/x/capabilities',
                                      data={'action': 'save', 'configuration': _configuration(),
                                            'ui_locale': locale,
                                            'expected_fingerprint': x_capabilities.fingerprint(x_capabilities.load())})
            assert saved.status == 200
            with _LocalX() as provider:
                monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
                draft = 'The exact text from the chat request'
                assert _tool({'operation_id': 'createPosts', 'body': {'text': draft}}).ok
                assert provider.requests == []
                page.reload()
                card = page.locator('.x-action-card')
                card.locator('summary').click()
                assert draft in card.locator('.x-api-request-details').inner_text()
                card.locator('.x-action-approve').click()
                card.wait_for(state='detached')
                assert len(provider.requests) == 1 and provider.requests[0]['body'] == {'text': draft}
                assert catalog_for(locale).text('xact.state_sent') in page.locator('.x-activity').inner_text()
                assert page.locator('.x-activity').get_by_text(catalog_for(locale).text('xfull.receipt'), exact=True).count() == 1
                assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Decline this draft'}}).ok
                page.reload()
                page.locator('.x-action-decline').click()
                page.locator('.x-action-card').wait_for(state='detached')
                page.reload()
                host.locator('.x-features-save').wait_for()
                assert provider.requests == [provider.requests[0]]
        assert not errors, errors
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_x_control_translations_are_supplied_in_every_catalog():
    from core.i18n.catalog import CATALOGS_DIR, catalog_for, source_catalog_sha256

    keys = {key for key in catalog_for('en').keys if key.startswith('xfull.')}
    assert len(keys) == 42  # 41 X controls + extra_permission (19 Oct)
    for path in CATALOGS_DIR.glob('*.json'):
        data = json.loads(path.read_text())
        catalog = catalog_for(data['locale'])
        assert keys <= catalog.locale_keys, (data['locale'], keys - catalog.locale_keys)
        if data['locale'] != 'en':
            assert data['source_catalog_sha256'] == source_catalog_sha256()
            assert all(catalog.text(key) != catalog_for('en').text(key) for key in keys)


def _quota_process(home, start, results):
    from core import runtime_paths, usage_quota

    runtime_paths.configure_runtime_home(Path(home))
    start.wait()
    admitted = 0
    for _ in range(20):
        try:
            admitted += usage_quota.consume_quota('x.api', tier='free').allowed
        except usage_quota.QuotaUnavailable:
            pass
    results.put(admitted)


def test_shared_quota_never_overadmits_concurrent_processes(vault_home):
    from core import runtime_paths, usage_quota

    config = runtime_paths.active_config_home_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / 'quota_limits.json').write_text(json.dumps({'free': {'x.api': {'per_day': 10}}}))
    context = multiprocessing.get_context('spawn')
    start, results = context.Event(), context.Queue()
    children = [context.Process(target=_quota_process, args=(str(vault_home), start, results)) for _ in range(6)]
    for child in children:
        child.start()
    start.set()
    for child in children:
        child.join(timeout=30)
        assert child.exitcode == 0, (child.pid, child.exitcode)
    admitted = sum(results.get(timeout=2) for _ in children)
    assert 0 < admitted <= 10
    assert admitted == usage_quota.usage_today('x.api')


def test_unlimited_calls_still_count_and_multiunit_admission_is_atomic(vault_home):
    from core import usage_quota

    for _ in range(3):
        assert usage_quota.consume_quota('x.api', tier='operator').allowed
    assert usage_quota.usage_today('x.api') == 3
    assert not usage_quota.consume_quota('x.api', tier='free', units=48).allowed
    assert usage_quota.usage_today('x.api') == 3
    assert usage_quota.consume_quota('x.api', tier='free', units=47).allowed
    assert usage_quota.usage_today('x.api') == 50


class _LocalX:
    def __init__(self, responses=None):
        self.responses = list(responses or [(200, {'data': [{'id': '10', 'name': 'Gardens'}]})])
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self):
                size = int(self.headers.get('Content-Length', '0'))
                raw = self.rfile.read(size) if size else b''
                body = json.loads(raw) if raw and 'json' in self.headers.get('Content-Type', '') else raw or None
                outer.requests.append({'method': self.command, 'path': self.path,
                                       'body': body, 'authorization_present': bool(self.headers.get('Authorization'))})
                scripted = outer.responses[min(len(outer.requests) - 1, len(outer.responses) - 1)]
                status, body = scripted[:2]
                headers = scripted[2] if len(scripted) > 2 else {'Content-Type': 'application/json'}
                if len(scripted) > 3:
                    time.sleep(scripted[3])
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_PUT = do_DELETE = answer  # noqa: N815

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = 0.5
        self.server.socket.settimeout(0.5)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()


def _enable(monkeypatch, *, cost=0):
    from core import os_consent_gate
    from tests.test_x_api_reads import _connect

    _connect()
    monkeypatch.setenv('VOOL_TIER', 'free')
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: True)
    response = _post({'action': 'save', 'configuration': _configuration(cost=cost)})
    assert response.status_code == 200, response.text


def _tool(arguments, *, chat='x-full-chat'):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool('x.api.request', arguments,
                                source_context={'runtime_session_id': chat, 'surface': 'web', '_owner_local': True})


def _activity(body):
    from apps.vool_api_server import create_app

    async def run():
        transport = httpx.ASGITransport(app=create_app(), client=('127.0.0.1', 40123))
        async with httpx.AsyncClient(transport=transport, base_url='http://127.0.0.1') as session:
            return await session.post('/api/x/activity', json=body)
    return asyncio.run(run())


def test_catalog_available_without_keys_and_discovery_reflects_actual_access(vault_home):
    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool('x.api.catalog', {'query': 'communit'},
                                  source_context={'runtime_session_id': 'fresh-x'})
    assert result.ok, result
    assert result.details['access']['enabled'] is False
    assert any(row['id'] == 'searchCommunities' for row in result.details['features'])


def test_the_chat_model_can_propose_a_post_through_its_native_tool_call(vault_home):
    """The model's own call shape for a post passes the argument check before anything runs.

    Found in the running app: the offered schema left `body` untyped, the check before execution
    admits no value for an untyped field, and so every X post, reply or like the model proposed
    was refused there and never reached Settings for approval."""
    from core.cloud_tool_call_contract import build_cloud_tool_definitions, parse_native_tool_calls
    from core.runtime_execution_tools import runtime_execution_tool_specs
    from tests.test_x_api_reads import _connect

    _connect()
    definitions = build_cloud_tool_definitions(runtime_execution_tool_specs())
    request = next(item for item in definitions if item.intent == 'x.api.request')
    calls = parse_native_tool_calls([{'type': 'function', 'function': {'name': request.name, 'arguments': {
        'operation_id': 'createPosts', 'body': {'text': 'A garden update'}}}}], definitions=definitions)
    assert [(call.intent, call.arguments) for call in calls] == [
        ('x.api.request', {'operation_id': 'createPosts', 'body': {'text': 'A garden update'}})]


def test_new_read_uses_real_local_provider_and_quota_receipt(vault_home, monkeypatch):
    from core import usage_quota, x_api, x_request_gateway

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'gardening'}})
        assert result.ok, result
        assert result.details['result']['data'][0]['name'] == 'Gardens'
        assert provider.requests == [{'method': 'GET', 'path': '/2/communities/search?query=gardening', 'body': None,
                                      'authorization_present': True}]
        assert usage_quota.usage_today('x.api') == 1
        saved = x_request_gateway.receipts({'runtime_session_id': 'x-full-chat'})
        assert len(saved) == 1 and saved[0]['state'] == 'answered' and saved[0]['attempted'] == 1
        assert saved[0]['ceiling_microusd'] == 0
        assert x_request_gateway.receipts({'runtime_session_id': 'another-chat'}) == []


def test_new_mutation_waits_for_exact_served_settings_approval_and_sends_once(vault_home, monkeypatch):
    from core import x_actions, x_api

    _enable(monkeypatch)
    with _LocalX([(201, {'data': {'id': '900', 'text': 'A garden update'}})]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'createPosts', 'body': {'text': 'A garden update'}})
        assert result.ok and result.status == 'awaiting_approval', result
        assert provider.requests == []
        assert 'hash' not in result.details['action'] and 'payload' not in result.details['action']
        action_id = result.details['action']['id']
        assert _activity({'action': 'approve', 'action_id': action_id, 'hash': 'wrong'}).status_code == 400
        assert provider.requests == []
        pending = next(row for row in x_actions.pending() if row['id'] == action_id)
        response = _activity({'action': 'approve', 'action_id': action_id, 'hash': pending['hash']})
        assert response.status_code == 200, response.text
        assert provider.requests == [{'method': 'POST', 'path': '/2/tweets', 'body': {'text': 'A garden update'},
                                      'authorization_present': True}]
        assert _activity({'action': 'approve', 'action_id': action_id, 'hash': pending['hash']}).status_code == 400
        assert len(provider.requests) == 1


def test_declined_or_changed_configuration_never_sends_new_mutation(vault_home, monkeypatch):
    from core import x_actions, x_api, x_capabilities

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': 'Decline this'}})
        action_id = proposal.details['action']['id']
        pending = next(row for row in x_actions.pending() if row['id'] == action_id)
        assert _activity({'action': 'decline', 'action_id': action_id}).status_code == 200
        assert _activity({'action': 'approve', 'action_id': action_id, 'hash': pending['hash']}).status_code == 400
        other = _tool({'operation_id': 'createPosts', 'body': {'text': 'Profile changes'}})
        pending = next(row for row in x_actions.pending() if row['id'] == other.details['action']['id'])
        config = x_capabilities.load()
        config['enabled'] = False
        assert _post({'action': 'save', 'configuration': config}).status_code == 200
        response = _activity({'action': 'approve', 'action_id': pending['id'], 'hash': pending['hash']})
        assert response.status_code == 400 and response.json()['state'] == 'configuration_changed'
        assert provider.requests == []


@pytest.mark.parametrize('status,body,expected', [
    (429, {'detail': 'Slow down'}, 'rate_limited'),
    (403, {'detail': 'Access unavailable'}, 'plan_refused'),
    (500, {'detail': 'Unavailable'}, 'provider_unavailable'),
    (200, {'errors': [{'detail': 'No result'}]}, 'provider_error'),
    (200, {'data': [{'id': '1'}], 'errors': [{'title': 'One item failed'}]}, 'partial_result'),
])
def test_provider_errors_are_not_successes_and_are_never_retried(vault_home, monkeypatch, status, body, expected):
    from core import x_api

    _enable(monkeypatch)
    with _LocalX([(status, body)]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'gardening'}})
        assert not result.ok and result.status == expected, result
        assert len(provider.requests) == 1


def test_quota_exhaustion_and_missing_wallet_authority_refuse_before_network(vault_home, monkeypatch):
    from core import usage_quota, x_api

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        from core.runtime_paths import active_config_home_dir

        quota_path = active_config_home_dir() / 'quota_limits.json'
        quota_path.parent.mkdir(parents=True, exist_ok=True)
        quota_path.write_text(json.dumps({'free': {'x.api': {'per_day': 0}}}))
        assert usage_quota.config_path('quota_limits.json') == quota_path
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'gardening'}})
        assert not result.ok and result.status == 'quota_exhausted', result
        quota_path.unlink()
        assert _post({'action': 'save', 'configuration': _configuration(cost=100)}).status_code == 200
        from core import os_consent_gate

        monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: False)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'gardening'}})
        assert not result.ok and result.status == 'spend_approval_required', result
        assert provider.requests == []


def _private_files():
    from core.runtime_paths import active_data_dir

    root = active_data_dir() / 'x_private_calls'
    return sorted(path.name for path in root.rglob('*') if path.is_file()) if root.exists() else []


def test_private_snapshot_cleanup_failure_visible_and_retry_never_sends(vault_home, monkeypatch):
    from core import x_actions, x_api
    from storage.blackbox.cas import EncryptedBlobStore

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': 'Private snapshot cleanup'}})
        action_id = proposal.details['action']['id']
        pending = next(row for row in x_actions.pending() if row['id'] == action_id)
        original = EncryptedBlobStore.delete

        def unavailable(self, ref):
            raise OSError('fixture disk unavailable')

        # The owned erase boundary: the private copy's file in the encrypted store.
        monkeypatch.setattr(EncryptedBlobStore, 'delete', unavailable)
        declined = _activity({'action': 'decline', 'action_id': action_id})
        assert declined.status_code == 200 and declined.json()['action']['cleanup_pending'] is True
        assert _private_files() != []
        assert _activity({'action': 'approve', 'action_id': action_id, 'hash': pending['hash']}).status_code == 400
        assert _activity({'action': 'cleanup_request', 'action_id': action_id}).status_code == 400
        monkeypatch.setattr(EncryptedBlobStore, 'delete', original)
        retried = _activity({'action': 'cleanup_request', 'action_id': action_id})
        assert retried.status_code == 200 and retried.json()['state'] == 'cleaned'
        assert _private_files() == []
        assert provider.requests == []


def _allow_operations(monkeypatch, names, *, cost=0):
    policy = _configuration(cost=cost)
    policy['operations'] = {name: cost for name in names}
    assert _post({'action': 'save', 'configuration': policy}).status_code == 200


def test_paid_calls_hold_shared_reservations_and_cannot_reset_the_chat_cap(vault_home, monkeypatch, priced_ledger):
    from core import model_spend_ledger, x_api

    _enable(monkeypatch, cost=10000)
    args = {'operation_id': 'searchCommunities', 'query': {'query': 'fruit trees'}}
    with _LocalX() as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        first, second, third = [_tool(args, chat='same-priced-chat') for _ in range(3)]
        assert first.ok and second.ok and third.status == 'spend_cap_exhausted'
        assert len(fake.requests) == 2
        for outcome in (first, second):
            receipt = outcome.details['receipt']
            reservation = model_spend_ledger.get_spend_reservation(receipt['id'])
            assert reservation.status == 'billing_ambiguous'
            assert reservation.reserved_usd == 0.01 and reservation.task_id == 'same-priced-chat'
        assert third.details['receipt']['state'] == 'unsent'


def test_legacy_chat_read_and_trend_obey_opt_in_and_record_quota(vault_home, monkeypatch):
    from core import os_consent_gate, policy_engine, runtime_paths, usage_quota, x_api, x_capabilities
    from core.runtime_execution_tools import execute_runtime_tool
    from tests.test_x_api_reads import _connect

    _connect()
    policy_file = runtime_paths.active_config_home_dir() / 'default_policy.yaml'
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    policy_file.write_text(json.dumps({'x': {'trending_enabled': True}}))
    policy_engine.load(force_reload=True)
    monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', lambda reason: True)
    policy = x_capabilities.load()
    x_capabilities.save({**policy, 'enabled': False})
    with _LocalX() as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        context = {'runtime_session_id': 'legacy-chat', '_owner_local': True}
        for intent, args in [('x.search', {'query': 'garden'}), ('x.trending', {'woeid': 1})]:
            result = execute_runtime_tool(intent, args, source_context=context)
            assert not result.ok and result.status == 'disabled'
        assert fake.requests == []
        x_capabilities.save(policy)
        result = execute_runtime_tool('x.search', {'query': 'garden'}, source_context=context)
        assert result.ok and usage_quota.usage_today('x.api') == 1
        result = execute_runtime_tool('x.trending', {'woeid': 1}, source_context=context)
        assert result.ok and usage_quota.usage_today('x.api') == 2
        rows = execute_runtime_tool('x.api.receipts', {}, source_context=context).details['receipts']
        assert {row['operation_id'] for row in rows} == {'searchPostsRecent', 'getTrendsByWoeid'}


def test_generic_post_keeps_the_existing_daily_post_cap(vault_home, monkeypatch):
    from core import runtime_paths, x_api

    _enable(monkeypatch)
    config = runtime_paths.active_config_home_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / 'quota_limits.json').write_text(json.dumps({'free': {'x.post': {'per_day': 0}}}))
    with _LocalX() as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': 'Preserve the post cap'}})
        action = _activity({'action': 'overview'}).json()['pending'][0]
        assert proposal.ok
        result = _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}).json()
        assert not result['ok'] and result['state'] == 'daily_limit_reached'
        assert result['action']['state'] == 'pending'
        assert fake.requests == []


def test_sensitive_result_reference_is_scoped_to_its_originating_chat(vault_home, monkeypatch):
    from core import x_api

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['searchCommunities', 'uploadComplianceJobSubmission'])
    with _LocalX([(200, {'data': {'download_url': 'https://example.invalid/files?token=local-result-secret-value',
                                'token': 'local-result-secret-value'}})]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden'}})
        ref = result.details['result']['data']['token']
        assert 'credential_ref' in ref and 'local-result-secret-value' not in repr(result.details)
        url_ref = result.details['result']['data']['download_url']['query_credentials']['token']
        assert 'credential_ref' in url_ref
        args = {'operation_id': 'uploadComplianceJobSubmission', 'path': {'id': '123'},
                'query': {'token': url_ref}, 'body': {'base64': 'MTIzCg=='}}
        refused = _tool(args, chat='another-chat')
        assert not refused.ok and len(fake.requests) == 1
        proposal = _tool(args)
        assert proposal.ok and len(fake.requests) == 1
        action = _activity({'action': 'overview'}).json()['pending'][0]
        assert 'local-result-secret-value' not in action['text']
        assert _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}).status_code == 200
        assert fake.requests[-1]['method'] == 'PUT' and fake.requests[-1]['body'] == b'123\n'


def test_bounded_stream_and_binary_results_use_real_transport(vault_home, monkeypatch):
    import base64

    from core import x_api

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['activityStream', 'searchCommunities'])
    stream = b'{"data":{"id":"1"}}\n' * 105
    with _LocalX([(200, stream, {'Content-Type': 'application/x-ndjson'}),
                  (200, b'local-binary-result', {'Content-Type': 'application/octet-stream'})]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        result = _tool({'operation_id': 'activityStream'})
        assert result.ok and len(result.details['result']['records']) == 100
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden'}})
        assert result.ok and base64.b64decode(result.details['result']['base64']) == b'local-binary-result'


def test_lost_answer_keeps_the_billed_reservation_and_is_not_retried(vault_home, monkeypatch, priced_ledger):
    from core import model_spend_ledger, x_api

    _enable(monkeypatch, cost=10000)
    with _LocalX([(200, {'data': []}, {'Content-Type': 'application/json'}, 0.3)]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        monkeypatch.setattr(x_api, 'REQUEST_TIMEOUT_S', 0.05)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden'}})
        assert not result.ok and len(fake.requests) == 1
        receipt = result.details['receipt']
        assert receipt['state'] == 'unknown' and receipt['attempted'] == 1
        assert model_spend_ledger.get_spend_reservation(receipt['id']).status == 'billing_ambiguous'


def _approval_process(home, db_path, origin, action_id, action_hash, start, results):
    from core import runtime_paths, x_api
    from storage.db import configure_default_db_path

    runtime_paths.configure_runtime_home(Path(home))
    configure_default_db_path(db_path)
    x_api.API_ORIGIN = origin
    start.wait()
    response = _activity({'action': 'approve', 'action_id': action_id, 'hash': action_hash})
    results.put((response.status_code, response.json().get('state')))


def test_same_settings_action_is_sent_once_across_processes(vault_home, monkeypatch):
    import shutil

    from core import x_api
    from network import signer
    from storage.db import active_default_db_path

    _enable(monkeypatch)
    # The suite isolates its signer outside the runtime home. Real processes share the
    # persisted home identity, so copy this test's sealed record into that same location.
    shutil.copytree(signer._KEY_DIR, Path(vault_home) / 'data/keys', dirs_exist_ok=True)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'One approved request'}}).ok
        action = _activity({'action': 'overview'}).json()['pending'][0]
        context = multiprocessing.get_context('spawn')
        start, results = context.Event(), context.Queue()
        children = [context.Process(target=_approval_process,
                    args=(str(vault_home), str(active_default_db_path()), provider.url,
                          action['id'], action['hash'], start, results)) for _ in range(4)]
        for child in children:
            child.start()
        start.set()
        for child in children:
            child.join(timeout=40)
            assert child.exitcode == 0, (child.pid, child.exitcode)
        outcomes = [results.get(timeout=2) for _ in children]
        assert sum(status == 200 for status, _state in outcomes) == 1, outcomes
        assert len(provider.requests) == 1
        assert _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}).status_code == 400


def test_credentials_changed_during_wallet_approval_cancel_the_frozen_action(vault_home, monkeypatch, priced_ledger):
    from core import credential_store, os_consent_gate, x_api

    _enable(monkeypatch, cost=10000)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Keep the approved account'}}).ok
        action = _activity({'action': 'overview'}).json()['pending'][0]

        def change_credentials(reason):
            saved = x_api._saved()
            saved['access_token'] += '-changed'
            credential_store.store_credential('x.api.default', json.dumps(saved), label='Changed local fixture')
            return True

        monkeypatch.setattr(os_consent_gate, 'require_os_user_consent', change_credentials)
        response = _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']})
        assert response.status_code == 400 and response.json()['state'] == 'account_changed'
        assert provider.requests == []
        assert response.json()['action']['state'] == 'pending'


def test_redirect_keeps_the_first_request_accounted_and_never_forwards_credentials(vault_home, monkeypatch, priced_ledger):
    from core import model_spend_ledger, usage_quota, x_actions, x_api, x_usage

    _enable(monkeypatch, cost=10000)
    with _LocalX() as destination, _LocalX([(302, {}, {'Location': destination.url + '/other'})]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Never forward this request'}}).ok
        action = x_actions.pending()[0]
        response = _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']})
        outcome = response.json()
        assert not outcome['ok'] and outcome['action']['state'] == 'unknown'
        assert len(provider.requests) == 1 and destination.requests == []
        receipt = outcome['action']['result']['receipt']
        assert receipt['state'] == 'unknown' and receipt['attempted'] == 1
        assert model_spend_ledger.get_spend_reservation(receipt['id']).status == 'billing_ambiguous'
        assert usage_quota.usage_today('x.api') == usage_quota.usage_today('x.post') == 1
        assert x_usage.snapshot()['actions'] == 1
        assert _private_files() == [], 'An unknown outcome is never resent, so its request copy is erased'


def test_invalid_binary_is_refused_before_proposal_and_invisible_preview_controls_are_shown(vault_home, monkeypatch):
    from core import usage_quota, x_api
    from core.x_request_gateway import safe_preview

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['uploadComplianceJobSubmission'])
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'uploadComplianceJobSubmission', 'path': {'id': '123'},
                        'query': {'token': 'local-fixture-token'}, 'body': {'base64': '!invalid!'}})
        assert not result.ok and result.status == 'invalid_request'
        assert provider.requests == [] and usage_quota.usage_today('x.api') == 0
    preview = safe_preview({'body': {'text': 'prefix\u202esuffix العربية'}})
    assert '\u202e' not in preview and '\\u202e' in preview and 'العربية' in preview


def test_documented_public_operation_sends_no_credentials_and_unique_values_are_enforced(vault_home, monkeypatch):
    from core import usage_quota, x_api
    from core.x_endpoint_catalog import InvalidXRequest, validate

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['getOpenApiSpec'])
    monkeypatch.setattr(x_api, '_saved', lambda: pytest.fail('This public operation must not read the key vault'))
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        result = _tool({'operation_id': 'getOpenApiSpec'})
        assert result.ok and usage_quota.usage_today('x.api') == 1
        assert provider.requests[0]['path'] == '/2/openapi.json'
        assert provider.requests[0]['authorization_present'] is False
    with pytest.raises(InvalidXRequest, match='unique'):
        validate([{'id': 1}, {'id': 1.0}], {'type': 'array', 'uniqueItems': True}, 'items')
    validate([False, 0], {'type': 'array', 'uniqueItems': True}, 'items')


def test_legacy_approval_does_not_claim_success_for_an_error_body(vault_home, monkeypatch):
    from core import x_api
    from core.runtime_execution_tools import execute_runtime_tool

    _enable(monkeypatch)
    with _LocalX([(200, {'errors': [{'title': 'Error', 'detail': 'No confirmed result'}]})]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = execute_runtime_tool('x.action.propose', {'kind': 'post', 'text': 'Require a confirmed result'},
                                        source_context={'runtime_session_id': 'legacy-error-chat'})
        assert proposal.ok and provider.requests == []
        action = _activity({'action': 'overview'}).json()['pending'][0]
        request = {'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}
        result = _activity(request)
        assert result.status_code == 400 and result.json()['action']['state'] == 'unknown'
        assert _activity(request).status_code == 400 and len(provider.requests) == 1


def _home_files_containing(root, needle):
    data = needle.encode()
    return [str(path) for path in Path(root).rglob('*') if path.is_file() and data in path.read_bytes()]


def test_private_request_and_result_never_duplicate_into_the_action_file(vault_home, monkeypatch):
    from core import x_api
    from core.runtime_execution_tools import execute_runtime_tool
    from core.runtime_paths import data_path

    _enable(monkeypatch)
    private_text = 'Private fixture content for the approved call'
    private_answer = 'Private fixture answer only the chat may read'
    with _LocalX([(201, {'data': {'id': '999', 'text': private_answer}})]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': private_text}})
        assert proposal.ok and provider.requests == []
        assert private_text not in json.dumps(proposal.details)
        state_path = data_path('x_actions.json')
        assert private_text not in state_path.read_text(), 'The encrypted request must have no plaintext twin'
        assert state_path.stat().st_mode & 0o777 == 0o600
        assert _home_files_containing(vault_home, private_text) == []
        overview = _activity({'action': 'overview'}).json()
        action = overview['pending'][0]
        assert private_text in action['text'], 'Settings must still show the exact decrypted request'
        approved = _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']})
        assert approved.status_code == 200
        assert approved.json()['action']['result']['result']['data']['text'] == private_answer
        assert provider.requests[0]['body'] == {'text': private_text}
        for needle in (private_text, private_answer):
            assert _home_files_containing(vault_home, needle) == [], needle
        assert len(_private_files()) == 1, 'Only the sealed result remains; the request copy is erased'
        recent = _activity({'action': 'overview'}).json()['recent'][0]
        assert private_answer not in json.dumps(recent)
        context = {'runtime_session_id': 'x-full-chat'}
        result = execute_runtime_tool('x.action.status', {'action_id': action['id']}, source_context=context)
        assert result.details['action']['result']['result']['data']['text'] == private_answer
        other = execute_runtime_tool('x.action.status', {'action_id': action['id']},
                                     source_context={'runtime_session_id': 'another-chat'})
        assert not other.ok and private_answer not in json.dumps(other.details)
        assert _activity({'action': 'cleanup_request', 'action_id': action['id']}).status_code == 200
        assert _private_files() == []
        result = execute_runtime_tool('x.action.status', {'action_id': action['id']}, source_context=context)
        assert result.details['action']['result'].get('result_erased') is True
        assert 'result' not in result.details['action']['result']
        assert result.details['action']['result']['receipt']['state'] == 'answered'
        assert len(provider.requests) == 1


def test_private_store_failures_never_leave_a_sendable_or_plaintext_request(vault_home, monkeypatch):
    from core import x_actions, x_api
    from core.blackbox.coverage import cas_keys
    from core.runtime_paths import data_path
    from storage.blackbox.cas import EncryptedBlobStore

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        original_put = EncryptedBlobStore.put

        def disk_full(self, data, *, max_bytes=None):
            raise OSError('fixture disk full')

        monkeypatch.setattr(EncryptedBlobStore, 'put', disk_full)
        refused = _tool({'operation_id': 'createPosts', 'body': {'text': 'Write fails'}})
        assert not refused.ok and refused.status == 'storage_unavailable'
        assert x_actions.pending() == [] and 'Write fails' not in data_path('x_actions.json').read_text()
        [failed] = x_actions.recent()
        assert failed['state'] == 'failed'
        monkeypatch.setattr(EncryptedBlobStore, 'put', original_put)
        # Without a key there is no plaintext fallback: proposals and reads both refuse first.
        monkeypatch.setattr(cas_keys, 'resolve_keyring', lambda: None)
        refused = _tool({'operation_id': 'createPosts', 'body': {'text': 'No key'}})
        assert not refused.ok and refused.status == 'storage_unavailable'
        read = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden'}})
        assert not read.ok and read.status == 'storage_unavailable'
        assert provider.requests == []
        assert 'No key' not in data_path('x_actions.json').read_text()


def test_a_crash_while_preparing_or_after_sending_never_sends_or_keeps_copies(vault_home, monkeypatch):
    from core import x_actions, x_api, x_private_calls
    from core.runtime_paths import data_path
    from storage.blackbox.cas import EncryptedBlobStore

    _enable(monkeypatch)
    with _LocalX([(201, {'data': {'id': '901', 'text': 'Crash fixture answer'}})]) as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Crash while preparing'}}).ok
        path = data_path('x_actions.json')
        state = json.loads(path.read_text())
        [stored] = state['actions'].values()
        # The state a VOOL that stopped after journaling but before marking it pending leaves.
        stored['state'] = 'preparing'
        path.write_text(json.dumps(state))
        stray = x_private_calls.prepare('xa_0000000000000000', 'result', {'left': 'by a crash'})
        stray.write()
        assert len(_private_files()) == 2
        overview = _activity({'action': 'overview'}).json()
        assert overview['pending'] == [] and overview['recent'][0]['state'] == 'failed'
        assert _private_files() == [], 'Closed and unreferenced copies are erased'
        assert _activity({'action': 'approve', 'action_id': stored['id'], 'hash': stored['hash']}).status_code == 400

        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Result cannot be sealed'}}).ok
        action = _activity({'action': 'overview'}).json()['pending'][0]
        original_put = EncryptedBlobStore.put

        def fail_result(self, data, *, max_bytes=None):
            if b'"kind":"result"' in data:
                raise OSError('fixture disk full')
            return original_put(self, data, max_bytes=max_bytes)

        monkeypatch.setattr(EncryptedBlobStore, 'put', fail_result)
        outcome = _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}).json()
        assert outcome['ok'] and outcome['action']['state'] == 'sent'
        assert outcome['action']['result']['result_unavailable'] is True
        assert 'Crash fixture answer' not in path.read_text() and _private_files() == []
        assert _activity({'action': 'approve', 'action_id': action['id'], 'hash': action['hash']}).status_code == 400
        assert len(provider.requests) == 1


def test_a_private_copy_from_another_action_or_a_tampered_copy_is_refused(vault_home, monkeypatch):
    from core import x_actions, x_api
    from core.runtime_paths import data_path

    _enable(monkeypatch)
    with _LocalX() as provider:
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'First request'}}).ok
        assert _tool({'operation_id': 'createPosts', 'body': {'text': 'Second request'}}).ok
        first, second = x_actions.pending()
        path = data_path('x_actions.json')
        state = json.loads(path.read_text())
        # Point the first action at the second one's copy, keeping its approval hash consistent.
        moved = state['actions'][first['id']]
        moved['payload']['request_ref'] = state['actions'][second['id']]['payload']['request_ref']
        moved['hash'] = x_actions._digest(moved['payload'], moved['nonce'])
        path.write_text(json.dumps(state))
        [shown] = [row for row in x_actions.pending() if row['id'] == first['id']]
        assert shown.get('request_unavailable') is True and 'text' not in shown
        response = _activity({'action': 'approve', 'action_id': first['id'], 'hash': moved['hash']})
        assert response.status_code == 400 and response.json()['state'] == 'request_unavailable'
        ref = state['actions'][second['id']]['payload']['request_ref']
        blob = next(p for p in (Path(vault_home)).rglob(ref) if p.is_file())
        envelope = json.loads(blob.read_text())
        envelope['ct'] = envelope['ct'][:-4] + ('AAAA' if not envelope['ct'].endswith('AAAA') else 'BBBB')
        blob.write_text(json.dumps(envelope))
        response = _activity({'action': 'approve', 'action_id': second['id'], 'hash': second['hash']})
        assert response.status_code == 400 and response.json()['state'] == 'request_unavailable'
        assert provider.requests == []


def test_every_feature_name_is_translated_and_used_in_the_owner_language():
    from pathlib import Path

    from core.i18n.catalog import catalog_for
    from core.x_capabilities import feature_label
    from core.x_endpoint_catalog import operations

    english = catalog_for('en')
    for entry in operations():
        key = 'xop.' + entry['id']
        assert english.text(key) == entry['summary']
        for locale in sorted(p.stem for p in Path('core/i18n/catalogs').glob('*.json')):
            if locale != 'en':
                assert key in catalog_for(locale).locale_keys and catalog_for(locale).text(key).strip(), (locale, key)
    assert feature_label('getUsersBookmarks', 'de') == catalog_for('de').text('xop.getUsersBookmarks')
    assert feature_label('getUsersBookmarks', 'de') != feature_label('getUsersBookmarks', 'en')
    from core.x_settings_fragment import X_FEATURES_JS

    assert "T('xop.' + feature.id, feature.summary)" in X_FEATURES_JS


@pytest.mark.parametrize(("arguments", "shown"), [
    ({"operation_id": "getUsersByUsername", "path": {"username": "vool_garden"}},
     "X: Get Users by Username (username vool_garden)"),
    ({"operation_id": "createPosts", "body": {"text": "The first tomatoes are red."}}, "X: Create Posts"),
])
def test_the_device_approval_card_names_the_x_operation_and_no_folder(arguments, shown):
    """Measured 2026-11-12 in the running app: the card for an X account read said "Affects:
    <workspace folder>/{'username': 'vool_garden'}" (the request's path parameters read as a
    file), and the card for a post said "No specific path reported"."""
    from core.mode_permission_policy import OperatingMode, PermissionAction, _new_approval_request

    request = _new_approval_request(
        session_id="x-card", task_id="x-card", intent="x.api.request", arguments=arguments,
        actions=(PermissionAction.USE_NETWORK, PermissionAction.ACCESS_EXTERNAL_PROVIDERS), mode=OperatingMode.MANUAL, mode_revision=0,
        source_context={"session_id": "x-card", "workspace_root": "/srv/workspace"}, fingerprint="fp-x-card",
    )
    assert request["affected_resources"] == [shown]
    assert not any("{" in item or "/srv/workspace" in item for item in request["affected_resources"])


def test_a_structured_path_argument_is_never_shown_as_a_file():
    from core.mode_permission_policy import _affected_resources

    assert _affected_resources({"path": {"username": "bob"}, "url": "https://example.test/a"}) == ["https://example.test/a"]
    assert _affected_resources({"path": "notes/today.md", "paths": ["a.txt", {"x": 1}]}) == ["notes/today.md", "a.txt"]
