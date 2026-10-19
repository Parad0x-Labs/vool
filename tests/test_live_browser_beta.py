"""Owner approval resumes the real tool call; losing the view purges its context."""
from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.first_run_pact_rig import pact_rig as pact_rig
from tests.test_live_browser import world as world


def _until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    assert predicate()


def _execute(origin, *, cancel=None, chat='live-test'):
    from core.runtime_flags import override
    from core.tool_intent_executor import execute_tool_intent
    from core.vool_browser.live_requests import turn_scope
    from tests._toolchain_fixtures import executor_kwargs

    # The model's call runs inside the person's turn, as every chat turn does.
    with override('plugin_runtime_tools', True), turn_scope(f'Use the browser to open {origin}', chat):
        return execute_tool_intent(
            {'intent': 'vool-browser.session.open', 'arguments': {'session': 'beta', 'start_url': origin}},
            **executor_kwargs(chat, runtime_session_id=chat, surface='api',
                              platform='web_companion', mode='manual',
                              live_browser_wait_for_owner=True, cancel_event=cancel),
        )


@pytest.mark.parametrize('path, evidence', [('/', '14:36'), ('/other', '22:14')])
def test_owner_approval_continues_original_real_tool_call(world, path, evidence):
    rig, item, origin, requests = world
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, _execute, origin + path)
        _until(lambda: item.pending is not None)
        assert not future.done() and requests == [] and not item.profile.exists()
        proposal_id = item.pending['id']
        status, decision = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': proposal_id, 'approve': True})
        assert status == 200, decision
        execution = future.result(timeout=25)
    assert execution.ok and execution.status == 'executed', execution
    assert execution.details['observation']['receipt']['approved_proposal_id'] == proposal_id
    assert evidence in execution.details['observation']['page_text']
    assert execution.details['observation']['authority'] == 'none'
    assert requests == [('GET', path)]
    from core.vool_browser.live import tool_intent

    observed = tool_intent('vool-browser.inspect', {}, {'session_id': 'live-test'})
    assert observed['ok'] and evidence in observed['page_text']
    assert observed['authority'] == 'none'
    assert len(requests) == 1
    status, replay = rig.post('/api/browser/decide', {'chat': 'live-test', 'id': proposal_id, 'approve': True})
    assert status == 409 and replay['error'] == 'stale_approval'


@pytest.mark.parametrize('decision', ['refuse', 'cancel', 'takeover', 'off'])
def test_waiting_turn_refuses_without_effects(world, decision):
    rig, item, origin, requests = world
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, lambda: _execute(origin + '/', cancel=cancel))
        _until(lambda: item.pending is not None)
        assert not future.done()
        if decision == 'refuse':
            rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': False})
        elif decision == 'cancel':
            cancel.set()
        elif decision == 'takeover':
            rig.post('/api/browser/takeover', {'chat': 'live-test', 'value': True})
        else:
            rig.post('/api/browser/settings', {'enabled': False})
        execution = future.result(timeout=15)
    assert not execution.ok
    assert execution.status in {'refused', 'cancelled', 'paused', 'web_off', 'stopped'}
    assert requests == [] and not item.profile.exists()


def test_abandoned_private_view_expires_even_while_model_reads(world):
    from core.private_chat import set_private
    from core.vool_browser import live

    rig, old, origin, requests = world
    old.stop()
    set_private('live-test', True)
    item = live.LiveSession('live-test', fixture_origin=origin, lease_seconds=4)
    live._SESSIONS[live._key('live-test')] = item
    token = item.propose('navigate', {'url': origin + '/'}, actor='owner')['proposal']['id']
    rig.post('/api/browser/decide', {'chat': 'live-test', 'id': token, 'approve': True})
    deadline = time.monotonic() + 20
    while item.state == 'running' and time.monotonic() < deadline:
        rig.get('/api/browser/state?chat=live-test')
        time.sleep(.1)
    assert item.state == 'ready' and item.profile.exists()
    # A crashed view sends neither pagehide nor another owner heartbeat.
    # Model reads cannot keep the private context alive in its absence.
    deadline = time.monotonic() + 15
    while item.state != 'stopped' and time.monotonic() < deadline:
        live.tool_intent('vool-browser.session.status', {}, {'session_id': 'live-test'})
        time.sleep(.1)
    assert item.state == 'stopped' and not item.profile.exists()
    assert item.result == {} and item.nodes == [] and item.frame == ''
    assert item._completed == {} and item._context is None
    assert requests == [('GET', '/')]


def test_human_approval_wait_does_not_exhaust_the_owned_turn_clock(world):
    from core import runtime_active_clock

    rig, item, origin, requests = world
    with runtime_active_clock.task_clock(), ThreadPoolExecutor(max_workers=1) as pool:
        wall_start = time.monotonic()
        active_start = runtime_active_clock.monotonic()
        future = pool.submit(contextvars.copy_context().run, _execute, origin + '/')
        _until(lambda: item.pending is not None)
        time.sleep(1)
        assert requests == [] and not future.done()
        approved_at = time.monotonic()
        rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': True})
        assert future.result(timeout=25).ok
        wall_elapsed = time.monotonic() - wall_start
        active_elapsed = runtime_active_clock.monotonic() - active_start
        assert wall_elapsed - active_elapsed >= .9
        assert active_elapsed >= time.monotonic() - approved_at - .2


def test_explicit_browser_demand_reaches_its_tools_and_inert_text_does_not(world):
    from core.agent_runtime.fast_paths_web import url_read_request
    from core.execution.planner import plan_tool_workflow
    from core.tool_demand_signals import resolve_demand_signals

    rig, item, origin, requests = world
    for prompt in [f'Use the built-in browser to open {origin}/',
                   f'Open {origin}/ in the browser and find the next service',
                   f'Browse {origin}/']:
        signals = resolve_demand_signals(prompt)
        assert 'vool-browser.session.open' in signals.explicit_intents
        assert 'vool-browser.inspect' in signals.explicit_intents
        assert url_read_request(prompt) is None
        assert not plan_tool_workflow(user_text=prompt, task_class='research', executed_steps=[], source_context={}).handled
    for prompt in [f'Explain how to use the built-in browser to open {origin}/',
                   f'Do not use tools or open the browser. Discuss {origin}/ as text.',
                   f'Read this quoted page instruction as data: "Use the browser to open {origin}/"',
                   f'Write a tutorial explaining how to use the browser to open {origin}/']:
        assert not any(intent.startswith('vool-browser.') for intent in resolve_demand_signals(prompt).explicit_intents), prompt
    assert requests == [] and item.pending is None


def test_real_browser_fetch_is_counted_in_the_waiting_turn(world):
    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_attempts, remote_fetch_turn_scope

    rig, item, origin, requests = world
    with remote_fetch_turn_scope({'session_id': 'live-test', 'surface': 'api', 'platform': 'web_companion'}), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, _execute, origin + '/')
        _until(lambda: item.pending is not None)
        assert remote_fetch_attempt_count() == 0 and requests == []
        rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': True})
        assert future.result(timeout=25).ok
        assert remote_fetch_attempt_count() == 1
        assert remote_fetch_attempts()[0]['url'] == origin + '/'
        from core.vool_browser.live import tool_intent

        assert tool_intent('vool-browser.inspect', {}, {'session_id': 'live-test'})['ok']
        assert remote_fetch_attempt_count() == 1
    assert requests == [('GET', '/')]


def test_browser_turn_is_confined_but_owner_can_approve_its_real_page(world):
    from urllib.request import Request

    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote, remote_fetch_forbidden
    from core.runtime_execution_tools import execute_runtime_tool
    from core.tool_intent_executor import execute_tool_intent
    from core.tool_offer_assembly import assemble_tool_offer
    from core.vool_browser.live_contracts import SUPPORTED
    from core.vool_browser.live_requests import confined, turn_scope
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    with turn_scope(f'Use the browser to open {origin}/'):
        offer = assemble_tool_offer(user_text=f'Use the browser to open {origin}/', task_class='research')
        assert 'vool-browser.session.open' in offer.intents
        assert not {'vool-browser.click', 'vool-browser.type', 'vool-browser.download'} & set(offer.intents)
        assert set(offer.intents) <= SUPPORTED | {'respond.direct'}
        for intent in ['wallet.propose', 'workspace.read_file', 'web.fetch']:
            execution = execute_tool_intent({'intent': intent, 'arguments': {'url': origin+'/forbidden'}}, **executor_kwargs('live-test'))
            assert execution.status == 'browser_scope_required' and not execution.ok
            direct = execute_runtime_tool(intent, {}, source_context={'session_id': 'live-test'})
            assert direct.status == 'browser_scope_required' and not direct.ok
        with turn_scope('Search the web instead'):
            assert confined() and remote_fetch_forbidden()
        with named_background_effect_scope('browser.fence.test', source_context={'surface': 'api', 'platform': 'web_companion', 'allow_remote_fetch': True}):
            with pytest.raises(RemoteFetchRefusedError):
                open_remote(Request(origin+'/forbidden'), timeout=1)
    assert not confined() and not remote_fetch_forbidden() and requests == []
    # An independent owner HTTP context can approve the constrained model's
    # proposal. Its transport owns separate authority and only shares telemetry.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, lambda: _execute(origin+'/'))
        _until(lambda: item.pending is not None)
        assert requests == []
        rig.post('/api/browser/decide', {'chat': 'live-test', 'id': item.pending['id'], 'approve': True})
        assert future.result(timeout=25).ok
    assert requests == [('GET', '/')]


@pytest.mark.parametrize('context', [{'local_only_mode': True}, {'requested_model': 'vool-local-only'}, {'cloud_disabled': True}])
def test_turn_local_only_veto_is_preserved_by_browser_tools(world, context):
    from core.vool_browser.live import BrowserFault, tool_intent

    rig, item, origin, requests = world
    with pytest.raises(BrowserFault, match='web_off'):
        tool_intent('vool-browser.navigate', {'url': origin+'/'},
                    {'session_id': 'live-test', 'surface': 'api', 'platform': 'web_companion', **context})
    assert item.closed and not item.profile.exists() and requests == []


def test_observed_page_confines_child_work_until_the_original_turn_ends(world):
    from urllib.request import Request

    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote, remote_fetch_forbidden
    from core.vool_browser import live
    from core.vool_browser.live_requests import confine, confined, turn_scope
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    assert finish(world, 'navigate', {'url': origin+'/other'})['state'] == 'ready'
    with turn_scope('What is on this page?'):
        assert not confined()
        result = live.tool_intent('vool-browser.inspect', {}, {'session_id': 'live-test'})
        assert result['authority'] == 'none' and '22:14' in result['page_text']
        assert confined() and remote_fetch_forbidden()
        with turn_scope('Search elsewhere'):
            assert confined()
    assert not confined() and not remote_fetch_forbidden()
    with turn_scope('A new task'):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(contextvars.copy_context().run, confine).result(timeout=2)
        assert confined() and remote_fetch_forbidden()
        with named_background_effect_scope('browser.child.test', source_context={'allow_remote_fetch': True}):
            with pytest.raises(RemoteFetchRefusedError):
                open_remote(Request(origin+'/forbidden'), timeout=1)
    assert not confined() and not remote_fetch_forbidden()
    assert requests == [('GET', '/other')]


@pytest.mark.parametrize('fresh', [False, True])
def test_explicit_browser_chat_request_is_refused_while_off(world, fresh):
    from core.vool_browser import live

    rig, item, origin, requests = world
    rig.post('/api/browser/settings', {'enabled': False})
    if fresh:
        (live._root() / 'setting.json').unlink()
    status, frames = rig.post('/api/chat', {
        'session_id': rig.canonical_session(), 'model': 'vool-local-only', 'stream': True,
        'messages': [{'role': 'user', 'content': f'Use the built-in browser to open {origin}/'}],
    })
    raw = json.dumps(frames)
    assert status == 200 and 'Stopped' in raw and 'Settings' in raw, raw
    assert 'stream_error' not in raw
    assert requests == [] and not item.profile.exists()


def test_live_pane_in_chat_approves_the_original_tool_call(world, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    from core.vool_browser import live
    from core.vool_browser.engine import find_engine_binary, isolation_launch_flags
    from core.web.api.service import dispatch_get, dispatch_post

    rig, old, origin, requests = world
    old.stop()
    chat_id = rig.canonical_session()
    rig.post('/api/chat/session', {'session_id': chat_id, 'operation': 'create'})
    item = live.LiveSession(chat_id, fixture_origin=origin)
    live._SESSIONS[live._key(chat_id)] = item
    response = dispatch_get(path='/browser', query={'chat': ['live-test']}, runtime=rig.runtime,
                            model_name='vool', client_host='127.0.0.1')
    assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert "frame-ancestors 'self'" in response.headers['Content-Security-Policy']
    class App(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = urlsplit(self.path)
            response = dispatch_get(path=path.path, query=parse_qs(path.query), runtime=rig.runtime,
                                    model_name='vool', client_host='127.0.0.1', headers=dict(self.headers))
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(response.body or b'')))
            self.end_headers()
            self.wfile.write(response.body or b'')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            response = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=rig.runtime,
                                     model_name='vool', workspace_root_provider=lambda: tmp_path, client_host='127.0.0.1')
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            self.send_header('Content-Length', str(len(response.body or b'')))
            self.end_headers()
            self.wfile.write(response.body or b'')

    server = ThreadingHTTPServer(('127.0.0.1', 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from core.runtime_served_origins import register_served_port

    register_served_port(server.server_port)
    flags = [f for f in isolation_launch_flags(profile_dir=str(tmp_path/'viewer')) if not f.startswith('--user-data-dir=')]
    try:
        with sync_playwright() as driver, ThreadPoolExecutor(max_workers=1) as pool:
            browser = driver.chromium.launch(executable_path=find_engine_binary(), headless=True, args=flags)
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/chat#{chat_id}')
            page.locator('#browserBtn').click()
            pane = page.frame_locator('#settingsFrame')
            expect(pane.locator('#title')).to_have_text('Browser')
            future = pool.submit(contextvars.copy_context().run, lambda: _execute(origin + '/other', chat=chat_id))
            pane.locator('#approve').wait_for(state='visible')
            assert not future.done() and requests == []
            pane.locator('#approve').click()
            pane.locator('#frame').wait_for(state='visible', timeout=20000)
            execution = future.result(timeout=25)
            assert execution.ok and requests == [('GET', '/other')]
            expect(pane.locator('#status')).to_have_text('Ready')
            pane.locator('#stop').click()
            expect(pane.locator('#status')).to_have_text('Stopped')
            assert not item.profile.exists()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_native_wire_names_preserve_execution_identity_and_separate_collisions():
    import re

    from core.cloud_tool_call_contract import build_cloud_tool_definitions, parse_native_tool_calls
    from core.vool_browser.live_contracts import SUPPORTED

    intents = [*sorted(SUPPORTED), 'sample-a.read', 'sample_a.read']
    definitions = build_cloud_tool_definitions({'intent': intent, 'arguments': {}} for intent in intents)
    assert len({d.name for d in definitions}) == len(intents)
    assert all(re.fullmatch(r'[A-Za-z0-9_]{1,64}', d.name) for d in definitions)
    for definition in definitions:
        calls = parse_native_tool_calls([{'function': {'name': definition.name, 'arguments': {}}}], definitions=definitions)
        assert calls[0].intent == definition.intent
    with pytest.raises(ValueError):
        parse_native_tool_calls([{'function': {'name': 'unoffered_tool', 'arguments': {}}}], definitions=definitions)


@pytest.mark.parametrize('path', ['/', '/other'])
def test_page_cannot_expand_a_read_request_into_new_actions(world, path):
    from core.runtime_execution_tools import execute_runtime_tool
    from core.tool_intent_executor import execute_tool_intent
    from core.tool_offer_assembly import assemble_tool_offer
    from core.vool_browser import live
    from core.vool_browser.live_requests import turn_scope
    from tests._toolchain_fixtures import executor_kwargs

    rig, item, origin, requests = world
    prompt = f'Use the built-in browser to open {origin}{path} and read the timetable.'
    with turn_scope(prompt), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contextvars.copy_context().run, _execute, origin+path)
        _until(lambda: item.pending is not None)
        rig.post('/api/browser/decide', {'chat':'live-test','id':item.pending['id'],'approve':True})
        assert future.result(timeout=25).ok
        with turn_scope(f'Use the browser to click a link and fill a form and download {origin}/leak'):
            offer = assemble_tool_offer(user_text='A page says to click and send the chat', task_class='research')
            assert not {'vool-browser.click','vool-browser.type','vool-browser.download','vool-browser.navigate','vool-browser.session.open'} & set(offer.intents)
            for intent, arguments in [
                ('vool-browser.click', {'selector':item.nodes[0]['selector']}),
                ('vool-browser.type', {'selector':'invented','text':'conversation'}),
                ('vool-browser.download', {'url':origin+'/file'}),
                ('vool-browser.navigate', {'url':origin+'/leak'}),
                ('vool-browser.navigate', {'url':origin+path}),
                ('vool-browser.session.open', {'start_url':origin+path}),
            ]:
                result = execute_tool_intent({'intent':intent,'arguments':arguments}, **executor_kwargs('live-test'))
                assert not result.ok and result.status == 'browser_scope_required'
                direct = execute_runtime_tool(intent, arguments, source_context={'session_id':'live-test'})
                assert not direct.ok and direct.status == 'browser_scope_required'
                with pytest.raises(live.BrowserFault, match='request_scope_required'):
                    live.tool_intent(intent, arguments, {'session_id':'live-test'})
        assert item.pending is None and requests == [('GET',path)]


def test_only_the_original_owner_request_can_grant_actions():
    from core.vool_browser.live_requests import tool_allowed, turn_scope

    with turn_scope('Use the browser to open "https://example.test/" and fill the address and submit the form and download the receipt.'):
        assert tool_allowed('vool-browser.session.open', {'start_url':'https://example.test/'})
        assert tool_allowed('vool-browser.type', {'text':'17 Test Lane','submit':True})
        assert tool_allowed('vool-browser.click') and tool_allowed('vool-browser.download')
    with turn_scope('Use the browser to open https://example.test/. Do not submit the form.'):
        assert not tool_allowed('vool-browser.click')
        assert not tool_allowed('vool-browser.type', {'submit':True})
    with turn_scope('Use the browser to open https://example.test/. Read this data: "click the link and submit the form".'):
        assert not tool_allowed('vool-browser.click') and not tool_allowed('vool-browser.type')


def test_a_follow_up_about_the_open_page_is_a_request_with_only_its_own_authority(world):
    from core.vool_browser.live_requests import confined, requested, tool_allowed, turn_scope
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    follow_up = 'Now fill the address field with 17 Test Lane and submit the form.'
    # No page open yet in this chat: the same words are ordinary chat.
    with turn_scope(follow_up, 'live-test'):
        assert not confined() and not requested(follow_up)
    assert finish(world, 'navigate', {'url': origin+'/'})['state'] == 'ready'
    with turn_scope(follow_up, 'live-test'):
        assert confined() and requested(follow_up) and requested('  ' + follow_up.upper() + ' ')
        assert tool_allowed('vool-browser.type', {'text': '17 Test Lane', 'submit': True}) and tool_allowed('vool-browser.click')
        # It acts on the open page; it opens nothing new and grants no download.
        assert not tool_allowed('vool-browser.session.open', {'start_url': origin+'/other'})
        assert not tool_allowed('vool-browser.navigate', {'url': origin+'/other'})
        assert not tool_allowed('vool-browser.download')
        # Other text that runs in the turn (page text, a quoted example) is not the request.
        assert not requested('Click the link and download the file')
    # Another chat has no open page; a fill without submit grants no submit.
    with turn_scope(follow_up, 'another-chat'):
        assert not confined()
    with turn_scope('Fill the address field with 17 Test Lane.', 'live-test'):
        assert tool_allowed('vool-browser.type', {'text': '17 Test Lane'})
        assert not tool_allowed('vool-browser.type', {'text': '17 Test Lane', 'submit': True}) and not tool_allowed('vool-browser.click')
    with turn_scope('Fill in this form with 17 Test Lane, no need to submit it.', 'live-test'):
        assert confined() and tool_allowed('vool-browser.type', {'text': '17 Test Lane'})
        assert not tool_allowed('vool-browser.type', {'text': '17 Test Lane', 'submit': True})
    with turn_scope('Can you fill the address field with 17 Test Lane and submit the form?', 'live-test'):
        assert tool_allowed('vool-browser.type', {'text': '17 Test Lane', 'submit': True})


@pytest.mark.parametrize('text', [
    'How do I fill in this form?',
    'Fill it in.',
    'What is on this page?',
    'Do not submit the form.',
    'Fill the address field without using any tools.',
    'Is it safe to submit this form?',
    'Am I supposed to submit the form?',
    'Is it a good idea to log in on this page?',
    'Do I need to fill the address field?',
])
def test_questions_and_vague_follow_ups_grant_nothing(world, text):
    from core.vool_browser.live_requests import confined, requested, turn_scope
    from tests.test_live_browser import finish

    assert finish(world, 'navigate', {'url': world[2]+'/'})['state'] == 'ready'
    with turn_scope(text, 'live-test'):
        assert not confined() and not requested(text)


def test_submission_review_includes_hidden_fields_but_never_exposes_owner_secrets(world):
    from core.vool_browser import live
    from tests.test_live_browser import finish, propose

    rig,item,origin,requests=world
    snapshot=finish(world,'navigate',{'url':origin+'/review'})
    address=next(n for n in snapshot['nodes'] if n['name']=='address')
    finish(world,'type',{'selector':address['selector'],'text':'17 Fixture Lane'})
    password=next(n for n in item.nodes if n['type']=='password')
    finish(world,'type',{'selector':password['selector'],'text':'fixture-secret-291'})
    submit=next(n for n in item.nodes if n['type']=='submit')
    token=propose(world,'click',{'selector':submit['selector']})
    status,snapshot=rig.get('/api/browser/state?chat=live-test')
    assert status==200
    fields=snapshot['pending']['submission']
    assert {f['name']:f['value'] for f in fields} == {
        'destination':'fixture-destination','address':'17 Fixture Lane','password':'••••••','decision':'save'}
    assert 'fixture-secret-291' not in json.dumps(fields)
    model_status=live.tool_intent('vool-browser.session.status',{}, {'session_id':'live-test'})
    assert 'submission' not in model_status['pending'] and 'fixture-destination' not in json.dumps(model_status)
    assert requests==[('GET','/review')]
    rig.post('/api/browser/decide',{'chat':'live-test','id':token,'approve':False})
    assert not item._submission_previews and requests==[('GET','/review')]
    snapshot=finish(world,'click',{'selector':submit['selector']})
    assert snapshot['state']=='ready'
    assert len(requests)==2 and requests[-1][0:2]==('POST','/review-submit')
    assert b'fixture-destination' in requests[-1][2] and b'17+Fixture+Lane' in requests[-1][2]
    item.stop()
    assert item._forms==[] and not item._submission_previews and not item.profile.exists()


@pytest.mark.parametrize('kind',['card','standard','amount','marked','iban','amount_only','ccnum'])
def test_known_payment_forms_are_refused_before_any_approval_or_effect(world,kind):
    from tests.test_live_browser import finish

    rig,item,origin,requests=world
    snapshot=finish(world,'navigate',{'url':origin+'/payment?kind='+kind})
    node=next(n for n in snapshot['nodes'] if n['tag']=='input')
    status,result=rig.post('/api/browser/propose',{'chat':'live-test','op':'type','args':{'selector':node['selector'],'text':'fixture'}})
    assert status==409 and result['error']=='use_wallet'
    submit=next(n for n in snapshot['nodes'] if n['type']=='submit')
    status,result=rig.post('/api/browser/propose',{'chat':'live-test','op':'click','args':{'selector':submit['selector']}})
    assert status==409 and result['error']=='use_wallet'
    assert item.pending is None and requests==[('GET','/payment?kind='+kind)]


def test_unreviewable_submission_is_refused_without_truncating_the_approval(world):
    from tests.test_live_browser import finish

    rig,item,origin,requests=world
    snapshot=finish(world,'navigate',{'url':origin+'/unreviewable'})
    submit=next(n for n in snapshot['nodes'] if n['type']=='submit')
    status,result=rig.post('/api/browser/propose',{'chat':'live-test','op':'click','args':{'selector':submit['selector']}})
    assert status==409 and result['error']=='invalid_input'
    assert item.pending is None and requests==[('GET','/unreviewable')]


def test_live_form_review_is_visible_in_the_real_owner_pane(world,tmp_path):
    import os
    from pathlib import Path

    from playwright.sync_api import expect, sync_playwright

    from core.vool_browser.engine import find_engine_binary, isolation_launch_flags
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.test_live_browser import finish, propose

    rig,item,origin,requests=world
    snapshot=finish(world,'navigate',{'url':origin+'/review'})
    field=next(n for n in snapshot['nodes'] if n['name']=='address')
    finish(world,'type',{'selector':field['selector'],'text':'17 Fixture Lane'})
    field=next(n for n in item.nodes if n['type']=='password')
    finish(world,'type',{'selector':field['selector'],'text':'fixture-secret-572'})
    submit=next(n for n in item.nodes if n['type']=='submit')
    propose(world,'click',{'selector':submit['selector']})
    class App(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def send_result(self,response):
            self.send_response(response.status)
            self.send_header('Content-Type',response.content_type)
            for key,value in response.headers.items():self.send_header(key,value)
            self.send_header('Content-Length',str(len(response.body or b'')))
            self.end_headers();self.wfile.write(response.body or b'')
        def do_GET(self):
            parts=urlsplit(self.path)
            self.send_result(dispatch_get(path=parts.path,query=parse_qs(parts.query),runtime=rig.runtime,
                model_name='vool',client_host='127.0.0.1',headers=dict(self.headers)))
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_result(dispatch_post(path=self.path,body=body,headers=dict(self.headers),runtime=rig.runtime,
                model_name='vool',client_host='127.0.0.1',workspace_root_provider=lambda:tmp_path))
    server=ThreadingHTTPServer(('127.0.0.1',0),App)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    flags=[f for f in isolation_launch_flags(profile_dir=str(tmp_path/'viewer')) if not f.startswith('--user-data-dir=')]
    try:
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=find_engine_binary(),headless=True,args=flags)
            page=browser.new_page(viewport={'width':1100,'height':800})
            page.goto(f'http://127.0.0.1:{server.server_port}/browser?chat=live-test')
            expect(page.locator('#pending')).to_contain_text('destination = fixture-destination')
            expect(page.locator('#pending')).to_contain_text('address = 17 Fixture Lane')
            expect(page.locator('#pending')).to_contain_text('password = ••••••')
            expect(page.locator('#pending')).not_to_contain_text('fixture-secret-572')
            expect(page.locator('#wallet')).to_have_attribute('href','/settings#wallet')
            if os.getenv('VOOL_BROWSER_SHOTS'):
                shots=Path(os.environ['VOOL_BROWSER_SHOTS']);shots.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(shots/'form-review.png'),full_page=True)
            page.locator('#refuse').click()
            expect(page.locator('#pending')).to_be_hidden()
            assert requests==[('GET','/review')]
            page.locator('#stop').click()
            expect(page.locator('#status')).to_have_text('Stopped')
            assert item._forms==[] and not item.profile.exists()
            browser.close()
    finally:
        server.shutdown();server.server_close()


def test_the_owner_pane_follows_the_vool_appearance_choice_not_only_the_system(world,tmp_path):
    from playwright.sync_api import sync_playwright

    from core.vool_browser.engine import find_engine_binary, isolation_launch_flags
    from core.web.api.service import dispatch_get

    rig=world[0]
    class App(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            parts=urlsplit(self.path)
            response=dispatch_get(path=parts.path,query=parse_qs(parts.query),runtime=rig.runtime,
                model_name='vool',client_host='127.0.0.1',headers=dict(self.headers))
            self.send_response(response.status);self.send_header('Content-Type',response.content_type)
            for key,value in response.headers.items():self.send_header(key,value)
            self.send_header('Content-Length',str(len(response.body or b'')));self.end_headers();self.wfile.write(response.body or b'')
    server=ThreadingHTTPServer(('127.0.0.1',0),App)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    flags=[f for f in isolation_launch_flags(profile_dir=str(tmp_path/'viewer')) if not f.startswith('--user-data-dir=')]
    luma="(()=>{const m=getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).map(Number);return .2126*m[0]+.7152*m[1]+.0722*m[2]})()"
    try:
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=find_engine_binary(),headless=True,args=flags)
            seen={}
            for choice,system in [(None,'light'),('light.100','dark'),('system.100','light'),('system.100','dark')]:
                context=browser.new_context(viewport={'width':390,'height':800},color_scheme=system)
                if choice:
                    context.add_cookies([{'name':'vool_appearance','value':choice,'url':f'http://127.0.0.1:{server.server_port}/'}])
                page=context.new_page()
                page.goto(f'http://127.0.0.1:{server.server_port}/browser?chat=live-test')
                seen[(choice,system)]=(page.evaluate(luma)>128,page.evaluate('document.documentElement.scrollWidth'))
                context.close()
            browser.close()
    finally:
        server.shutdown();server.server_close()
    # Dark is VOOL's default even when the computer is light; Light and "Same as this computer" are honoured.
    assert seen=={(None,'light'):(False,390),('light.100','dark'):(True,390),
                  ('system.100','light'):(True,390),('system.100','dark'):(False,390)}


def test_actual_engine_profile_sandbox_and_network_rejector_are_owned(world):
    import socket

    import psutil

    from tests.test_live_browser import finish

    rig,item,origin,requests=world
    snapshot=finish(world,'navigate',{'url':origin+'/'})
    assert snapshot['state']=='ready'
    commands=[]
    for pid,created in item._owned_processes.items():
        try:
            process=psutil.Process(pid)
            if process.create_time()==created:commands.append(process.cmdline())
        except psutil.NoSuchProcess:pass
    main=next(command for command in commands if '--user-data-dir='+str(item.profile) in command)
    assert '--no-sandbox' not in main
    port=item._proxy_server.sockets[0].getsockname()[1]
    assert '--proxy-server=http://127.0.0.1:'+str(port) in main
    assert '--proxy-bypass-list=<-loopback>' in main
    assert '--disable-quic' in main
    before=item.engine_network_refusals
    with socket.create_connection(('127.0.0.1',port),timeout=2) as connection:
        connection.sendall(('GET '+origin+'/unapproved HTTP/1.1\r\nHost: fixture\r\n\r\n').encode())
        assert b'502' in connection.recv(4096)
    assert item.engine_network_refusals>before and requests==[('GET','/')]
    assert (item.profile/'Default').is_dir()
    owned=list(item._owned_processes.items())
    item.stop()
    assert not item.profile.exists() and not item._proxy_clients and item._proxy_server is None
    for pid,created in owned:
        try:assert psutil.Process(pid).create_time()!=created
        except psutil.NoSuchProcess:pass


def test_prompt_catalog_names_the_actual_native_functions_and_parameters(world):
    from core.cloud_tool_call_contract import build_cloud_tool_definitions
    from core.prompt_normalizer import _tool_intent_catalog_text
    from core.tool_offer_assembly import assemble_tool_offer
    from core.vool_browser.live_requests import turn_scope

    rig,item,origin,requests=world
    prompt=f'Use the browser to open {origin}/ and fill the address and submit the form.'
    with turn_scope(prompt):
        offer=assemble_tool_offer(user_text=prompt,task_class='research')
        rendered=_tool_intent_catalog_text(user_text=prompt,task_class='research',offer=offer)
        definitions=build_cloud_tool_definitions(offer.specs)
    assert 'Canonical operation names in guidance are not native function names' in rendered
    for definition in definitions:
        signature=definition.name+'('+', '.join(definition.parameters.get('properties',{}))+')'
        assert signature in rendered
    assert 'respond__direct(message)' in rendered
    assert 'vool_browser__type(selector, text, submit, session)' in rendered
    assert requests==[] and item.pending is None


def test_a_fill_sent_with_enter_shows_the_button_the_browser_will_send(world):
    from tests.test_live_browser import finish, propose

    rig, item, origin, requests = world
    snapshot = finish(world, 'navigate', {'url': origin+'/enter'})
    note = next(n for n in snapshot['nodes'] if n['name'] == 'note')
    token = propose(world, 'type', {'selector': note['selector'], 'text': 'hello', 'submit': True})
    status, state = rig.get('/api/browser/state?chat=live-test')
    # Enter sends the form's first submit button; the owner sees it before approving.
    assert {f['name']: f['value'] for f in state['pending']['submission']} == {'note': 'hello', 'op': 'delete_account'}
    rig.post('/api/browser/decide', {'chat': 'live-test', 'id': token, 'approve': True})
    _until(lambda: item.state != 'running')
    assert item.state == 'ready' and requests[-1][:2] == ('POST', '/review-submit')
    assert requests[-1][2] == b'note=hello&op=delete_account'


def test_a_submission_that_differs_from_the_review_is_not_sent():
    from core.vool_browser.live import _sent_as_reviewed

    reviewed = [{'name': 'note', 'value': 'hello', 'secret': False, 'file': False},
                {'name': 'password', 'value': '••••••', 'secret': True, 'file': False}]
    form = 'application/x-www-form-urlencoded'
    assert _sent_as_reviewed(form, b'note=hello&password=anything', reviewed)
    assert _sent_as_reviewed(form + '; charset=UTF-8', b'note=hello&password=', reviewed)
    for body in [b'note=hello&password=x&op=delete_account', b'note=bye&password=x', b'password=x&note=hello', b'note=hello']:
        assert not _sent_as_reviewed(form, body, reviewed)
    assert not _sent_as_reviewed('text/plain', b'note=hello\r\npassword=x\r\n', reviewed)
    assert not _sent_as_reviewed(form, b'note=hello&password=x', None)
    multipart = (b'--b\r\nContent-Disposition: form-data; name="note"\r\n\r\nline one\r\nline two\r\n'
                 b'--b\r\nContent-Disposition: form-data; name="doc"; filename="a.txt"\r\nContent-Type: text/plain\r\n\r\nbytes\r\n--b--\r\n')
    assert _sent_as_reviewed('multipart/form-data; boundary=b', multipart, [
        {'name': 'note', 'value': 'line one\nline two', 'secret': False, 'file': False},
        {'name': 'doc', 'value': 'a.txt', 'secret': False, 'file': True}])
    assert not _sent_as_reviewed('multipart/form-data; boundary=b', multipart, [
        {'name': 'note', 'value': 'line one\nline two', 'secret': False, 'file': False},
        {'name': 'doc', 'value': 'b.txt', 'secret': False, 'file': True}])


def test_an_image_submit_button_is_refused_before_any_approval(world):
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    snapshot = finish(world, 'navigate', {'url': origin+'/image'})
    image = next(n for n in snapshot['nodes'] if n['type'] == 'image')
    assert image['navigates'], image
    status, result = rig.post('/api/browser/propose', {'chat': 'live-test', 'op': 'click', 'args': {'selector': image['selector']}})
    assert status == 409 and result['error'] == 'invalid_input', (image, result)
    address = next(n for n in snapshot['nodes'] if n['name'] == 'address')
    status, result = rig.post('/api/browser/propose', {'chat': 'live-test', 'op': 'type', 'args': {'selector': address['selector'], 'text': 'x', 'submit': True}})
    assert status == 409 and result['error'] == 'invalid_input'
    assert item.pending is None and requests == [('GET', '/image')]


def test_hidden_one_time_and_owner_typed_values_never_reach_the_model(world):
    from core.vool_browser import live
    from core.vool_browser.live_requests import turn_scope
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    snapshot = finish(world, 'navigate', {'url': origin+'/private-fields'})
    nodes = {n['name']: n for n in snapshot['nodes'] if n['name']}
    # Form controls named "action" and "method" do not hide the form's own address.
    assert nodes['code']['target'] == origin + '/review-submit' and nodes['code']['method'] == 'POST'
    finish(world, 'type', {'selector': nodes['code']['selector'], 'text': '918273'})
    finish(world, 'type', {'selector': next(n for n in item.nodes if n['name'] == 'nickname')['selector'], 'text': 'owner-typed-55'})
    with turn_scope(f'Use the browser to open {origin}/private-fields', 'live-test'):
        seen = json.dumps(live.tool_intent('vool-browser.inspect', {}, {'session_id': 'live-test'}))
    assert 'hidden-token-417' in json.dumps(item.nodes)  # the owner's pane still shows the full address
    for private in ['hidden-token-417', '918273', 'owner-typed-55']:
        assert private not in seen
    assert 'kept' in seen  # a visible value nobody typed stays readable


def test_a_model_proposal_needs_the_persons_turn(world):
    from core.vool_browser.live import BrowserFault, tool_intent

    rig, item, origin, requests = world
    with pytest.raises(BrowserFault, match='request_scope_required'):
        tool_intent('vool-browser.navigate', {'url': origin+'/'}, {'session_id': 'live-test', 'surface': 'api', 'platform': 'web_companion'})
    assert item.pending is None and requests == []


def test_following_a_link_never_opens_an_address_from_the_page(world):
    from core.vool_browser.live_requests import tool_allowed, turn_scope
    from tests.test_live_browser import finish

    rig, item, origin, requests = world
    finish(world, 'navigate', {'url': origin+'/'})
    with turn_scope(f'Use the browser to open {origin}/ and click the Later trains link', 'live-test'):
        assert tool_allowed('vool-browser.click')
        assert tool_allowed('vool-browser.navigate', {'url': origin+'/'})
        assert not tool_allowed('vool-browser.navigate', {'url': origin+'/secret'})


def test_a_second_stop_waits_for_the_first_cleanup(world):
    from core.vool_browser.live import BrowserFault

    rig, item, origin, requests = world
    from tests.test_live_browser import finish
    finish(world, 'navigate', {'url': origin+'/'})
    close, released = item._close, threading.Event()

    async def slow_close():
        await asyncio.to_thread(released.wait, 5)
        await close()

    item._close = slow_close
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(item.stop)
        _until(lambda: item.state == 'stopping')
        second = pool.submit(item.stop)
        time.sleep(.3)
        assert not second.done() and item.profile.exists()
        released.set()
        first.result(timeout=20)
        second.result(timeout=20)
    assert item.state == 'stopped' and not item.profile.exists()

    class Stuck:
        def stop(self):
            raise BrowserFault('cleanup_failed')

    class Clean:
        stopped = False

        def stop(self):
            self.stopped = True

    from core.vool_browser import live

    clean = Clean()
    live._SESSIONS.update({('x', 'stuck'): Stuck(), ('x', 'clean'): clean})
    try:
        with pytest.raises(BrowserFault, match='cleanup_failed'):
            live._stop_each([(('x', 'stuck'), live._SESSIONS[('x', 'stuck')]), (('x', 'clean'), clean)])
        assert clean.stopped and ('x', 'clean') not in live._SESSIONS and ('x', 'stuck') in live._SESSIONS
    finally:
        live._SESSIONS.pop(('x', 'stuck'), None)
