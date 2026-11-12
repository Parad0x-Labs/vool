"""Every X read counts against the owner's monthly read limit: streams, lost answers and reads held
by other VOOL processes; and an X result is never reported as unsent because the list was busy."""
from __future__ import annotations

import json
import os
import stat
import threading
import time

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_bookmarks_oauth2 import _approve, _bookmarks, _pending, _sign_in, fake_x
from tests.test_x_full_integration import _activity, _allow_operations, _enable, _LocalX, _tool


def _reads():
    from core import x_usage

    return x_usage.snapshot()['reads']


def _holds():
    from core.runtime_paths import data_path

    return json.loads(data_path('x_api_usage.json').read_text()).get('holds') or {}


def test_a_stream_holds_its_whole_ceiling_and_counts_every_record(vault_home, monkeypatch):
    from core import x_api, x_usage

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['streamPostsSample'])
    stream = b'{"data":{"id":"1","text":"a post"}}\n' * 100
    with _LocalX([(200, stream, {'Content-Type': 'application/x-ndjson'})]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        x_usage.set_limits(monthly_reads=5)
        refused = _tool({'operation_id': 'streamPostsSample'})
        assert not refused.ok and fake.requests == []
        assert 'up to 100' in refused.response_text and _reads() == 0
        x_usage.set_limits(monthly_reads=1000)
        result = _tool({'operation_id': 'streamPostsSample'})
        assert result.ok and len(result.details['result']['records']) == 100 and len(fake.requests) == 1
    assert x_usage.snapshot()['posts_read'] == 100 and _reads() == 100 and _holds() == {}


@pytest.mark.parametrize('scripted', [
    (200, {'data': []}, {'Content-Type': 'application/json'}, 0.3),  # the answer is lost
    (200, b'{"data": [{"id": "1", "name": "cut', {'Content-Type': 'application/json'}),  # cut off
    (503, {'title': 'Unavailable'}),
])
def test_a_documented_read_with_an_unclear_outcome_keeps_its_held_reads(vault_home, monkeypatch, scripted):
    from core import x_api, x_usage

    _enable(monkeypatch)
    with _LocalX([scripted]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        monkeypatch.setattr(x_api, 'REQUEST_TIMEOUT_S', 0.05)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden', 'max_results': 10}})
        assert not result.ok and len(fake.requests) == 1
    assert _reads() == 10 and _holds() == {}
    assert x_usage.snapshot()['uncertain_read'] == 10


@pytest.mark.parametrize('status', [400, 404])
def test_a_documented_read_x_refused_outright_counts_no_reads(vault_home, monkeypatch, status):
    from core import x_api

    _enable(monkeypatch)
    with _LocalX([(status, {'title': 'Refused'})]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        result = _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden', 'max_results': 10}})
        assert not result.ok and len(fake.requests) == 1
    assert _reads() == 0 and _holds() == {}


# A search page of 10 asks for each post's author too, so it holds 10 posts plus 10 accounts.
@pytest.mark.parametrize('scripted, counted', [((503, {'title': 'Unavailable'}), 20), ((404, {'title': 'Not Found'}), 0)])
def test_chat_reads_keep_held_reads_only_when_x_may_have_served_them(vault_home, monkeypatch, scripted, counted):
    from core import x_api
    from core.runtime_execution_tools import execute_runtime_tool
    from tests.test_x_api_reads import _connect

    _connect()
    with _LocalX([scripted]) as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        result = execute_runtime_tool('x.search', {'query': 'garden', 'max_results': 10},
                                      source_context={'runtime_session_id': 'chat-reads', '_owner_local': True})
        assert not result.ok and len(fake.requests) == 1
    assert _reads() == counted and _holds() == {}


def _hold_in_another_process(home, start, results, amount):
    from pathlib import Path

    from core import runtime_paths, x_usage

    runtime_paths.configure_runtime_home(Path(home))
    start.wait()
    results.put(x_usage.reserve_reads(amount) or 'held')
    time.sleep(1.0)  # ends without settling, like a process that stops mid-read


def test_reads_held_by_one_process_are_held_for_every_process(vault_home):
    import multiprocessing

    from core import x_usage

    x_usage.set_limits(monthly_reads=10)
    context = multiprocessing.get_context('spawn')
    start, results = context.Event(), context.Queue()
    kids = [context.Process(target=_hold_in_another_process, args=(str(vault_home), start, results, 10))
            for _ in range(3)]
    for kid in kids:
        kid.start()
    start.set()
    outcomes = [results.get(timeout=60) for _ in kids]
    for kid in kids:
        kid.join(timeout=60)
    assert outcomes.count('held') == 1, outcomes
    # The holder ended without settling: X may have served it, so the hold became a counted read.
    assert x_usage.reserve_reads(1).startswith('Your X read limit')
    assert x_usage.snapshot()['uncertain_read'] == 10 and _holds() == {}


def _hold_list_lock_while_x_answers(fake, seconds):
    from core.cross_process_lock import PublicationLock
    from core.runtime_paths import data_path

    original = fake.server.RequestHandlerClass.answer

    def answer(handler):
        lock = PublicationLock(data_path('x_actions.json').with_suffix('.lock'))
        lock.__enter__()  # another VOOL process reads the list at the moment X answers
        threading.Timer(seconds, lambda: lock.__exit__(None, None, None)).start()
        original(handler)

    fake.server.RequestHandlerClass.do_POST = answer


def test_a_busy_action_list_waits_and_records_the_sent_post(vault_home, monkeypatch):
    from core import x_actions, x_api
    from core.runtime_paths import data_path

    _enable(monkeypatch)
    with _LocalX([(201, {'data': {'id': '900', 'text': 'A garden update'}})]) as provider:
        _hold_list_lock_while_x_answers(provider, 0.5)
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': 'A garden update'}})
        pending = next(row for row in x_actions.pending() if row['id'] == proposal.details['action']['id'])
        response = _activity({'action': 'approve', 'action_id': pending['id'], 'hash': pending['hash']})
        assert response.status_code == 200 and response.json()['ok'], response.text
        assert len(provider.requests) == 1
    stored = json.loads(data_path('x_actions.json').read_text())['actions'][pending['id']]
    assert stored['state'] == 'sent'


def test_a_list_locked_past_the_wait_still_reports_what_x_did(vault_home, monkeypatch):
    from core import x_actions, x_api

    _enable(monkeypatch)
    monkeypatch.setattr(x_actions, 'LOCK_WAIT_S', 0.2, raising=False)
    with _LocalX([(201, {'data': {'id': '900', 'text': 'A garden update'}})]) as provider:
        _hold_list_lock_while_x_answers(provider, 1.5)
        monkeypatch.setattr(x_api, 'API_ORIGIN', provider.url)
        proposal = _tool({'operation_id': 'createPosts', 'body': {'text': 'A garden update'}})
        pending = next(row for row in x_actions.pending() if row['id'] == proposal.details['action']['id'])
        response = _activity({'action': 'approve', 'action_id': pending['id'], 'hash': pending['hash']})
        assert len(provider.requests) == 1
        assert response.status_code == 200 and response.json()['ok'], response.text
        assert 'Nothing was sent' not in response.text and 'could not save this result' in response.text
        time.sleep(1.6)
        # The send is never repeated: the action is not pending again.
        assert all(row['id'] != pending['id'] for row in x_actions.pending())


@pytest.mark.parametrize('refusal', ['status_400', 'connection_refused'])
def test_a_bookmark_read_that_x_never_served_counts_no_reads(fake_x, monkeypatch, refusal):
    from core import x_oauth2, x_usage

    _sign_in()
    before = _reads()
    assert _bookmarks(max_results=5).ok
    action = _pending()
    if refusal == 'status_400':
        def refuse(handler):
            fake_x.calls.append({'method': 'GET', 'path': handler.path})
            raw = json.dumps({'title': 'Invalid Request'}).encode()
            handler.send_response(400)
            handler.send_header('Content-Type', 'application/json')
            handler.send_header('Content-Length', str(len(raw)))
            handler.end_headers()
            handler.wfile.write(raw)

        fake_x.server.RequestHandlerClass.do_GET = refuse
    else:
        import socket

        closed = socket.socket()
        closed.bind(('127.0.0.1', 0))
        port = closed.getsockname()[1]
        closed.close()  # nothing listens here
        monkeypatch.setattr(x_oauth2, 'API_ORIGIN', f'http://127.0.0.1:{port}')
    outcome = _approve(action)
    assert not outcome['ok']
    assert x_usage.snapshot()['reads'] == before and _holds() == {}


def test_receipts_are_owner_only_and_the_action_list_keeps_no_path_ids(vault_home, monkeypatch):
    from core import x_api
    from core.runtime_paths import active_data_dir, data_path

    _enable(monkeypatch)
    _allow_operations(monkeypatch, ['createDirectMessagesByParticipantId', 'searchCommunities'])
    with _LocalX() as fake:
        monkeypatch.setattr(x_api, 'API_ORIGIN', fake.url)
        assert _tool({'operation_id': 'searchCommunities', 'query': {'query': 'garden'}}).ok
        proposal = _tool({'operation_id': 'createDirectMessagesByParticipantId',
                          'path': {'participant_id': '7770001'}, 'body': {'text': 'hello'}})
        assert proposal.ok and fake.requests[1:] == []
    receipts = active_data_dir() / 'x_call_receipts.sqlite3'
    assert stat.S_IMODE(os.stat(receipts).st_mode) == 0o600
    listed = data_path('x_actions.json').read_text()
    assert '7770001' not in listed and '/2/dm_conversations/with/{participant_id}/messages' in listed
