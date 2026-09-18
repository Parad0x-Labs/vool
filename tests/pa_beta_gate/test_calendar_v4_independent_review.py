"""Independent v4 effect-boundary review: synthetic native/provider effects only.

Calendar ownership uses the real SQLite approval store, including a separate live
process. No AppleScript, live account, owner credential or network is used.
"""
import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.kas.contract import CalendarRefusedError
from core.operator import apple_notes, approvals, notes
from core.operator import calendar_provider as cp
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_v2_independent_review import event, staged


def _save_note(title, task='request-one'):
    return notes.handle_save_note(
        OperatorActionIntent(kind='save_note', raw_text=f'save a note to Apple Notes titled "{title}" with: keep the reviewed content'),
        task_id=task, session_id='review-session',
        evaluate_local_action_fn=lambda *a, **k: SimpleNamespace(mode='execute'),
        audit_log_fn=lambda *a, **k: None,
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('VOOL_WORKSPACE_ROOT', str(tmp_path / 'workspace'))


@pytest.mark.parametrize('title', ['Delivery notes', 'Novel equipment log'])
def test_notes_receipt_write_failure_does_not_allow_second_creation(workspace, monkeypatch, title):
    calls = []
    real_create = apple_notes.create_apple_note
    real_save = notes._save_apple_note_effects
    writes = []

    def runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=f'note id synthetic-{len(calls)}', stderr='')

    def fail_first_write(effects):
        writes.append(effects)
        if len(writes) == 1:
            raise OSError('Synthetic receipt disk failure')
        return real_save(effects)

    monkeypatch.setattr(apple_notes, 'create_apple_note', lambda **kw: real_create(**kw, runner=runner))
    monkeypatch.setattr(notes, '_save_apple_note_effects', fail_first_write)
    try:
        _save_note(title)
    except OSError:
        pass  # An honest pre-dispatch storage refusal is acceptable.
    _save_note(title)
    assert len(calls) <= 1, f'{len(calls)} native creates after one failed journal write'


@pytest.mark.parametrize('title', ['Inspection checklist', 'Novel orchard visit'])
def test_notes_overlap_has_one_native_dispatch(workspace, monkeypatch, title):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    real_create = apple_notes.create_apple_note

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5), 'review synchronization timeout'
        return SimpleNamespace(returncode=0, stdout=f'note id synthetic-{len(calls)}', stderr='')

    monkeypatch.setattr(apple_notes, 'create_apple_note', lambda **kw: real_create(**kw, runner=runner))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_save_note, title)
        assert entered.wait(5)
        try:
            second = pool.submit(_save_note, title)
            # A correct implementation may refuse immediately or wait on the owner.
            try:
                second.result(timeout=0.5)
            except TimeoutError:
                pass
        finally:
            release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert len(calls) == 1, f'overlapping replay dispatched {len(calls)} native creates'


@pytest.mark.parametrize('title', ['Shift handover', 'Novel workshop checklist'])
def test_notes_permission_repair_allows_explicit_new_request(workspace, monkeypatch, title):
    calls = []
    real_create = apple_notes.create_apple_note

    def runner(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout='', stderr='Not authorized (-1743)')
        return SimpleNamespace(returncode=0, stdout='note id synthetic-authorized', stderr='')

    monkeypatch.setattr(apple_notes, 'create_apple_note', lambda **kw: real_create(**kw, runner=runner))
    first = _save_note(title, task='before-permission')
    assert not first.ok and first.status == 'os_permission_denied'
    second = _save_note(title, task='explicit-new-request-after-permission')
    assert second.ok and len(calls) == 2, (second.status, len(calls), second.response_text)


def _connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return '2026-09-14T12:00:00+00:00'


def _load(path, aid):
    return cp.load_action_any_state(session_id='review-session', action_kind='provider_calendar_event',
                                    action_id=aid, get_connection_fn=lambda: _connection(path))


def _stage_db(tmp_path, provider='caldav', title='Packaging review', status='pending_approval'):
    config, original = staged(provider, title)
    path = tmp_path / 'approvals.sqlite'
    with _connection(path) as conn:
        conn.execute('CREATE TABLE operator_action_requests (action_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, action_kind TEXT, scope_json TEXT, result_json TEXT, status TEXT, created_at TEXT, updated_at TEXT, executed_at TEXT)')
    aid = approvals.create_pending_action(session_id='review-session', task_id='review-task',
        action_kind='provider_calendar_event', scope=json.loads(original['scope_json']),
        now_fn=_now, get_connection_fn=lambda: _connection(path))
    with _connection(path) as conn:
        conn.execute('UPDATE operator_action_requests SET status=? WHERE action_id=?', (status, aid))
    return path, aid, config


def _run(path, aid, config, adapter):
    kwargs = dict(now_fn=_now, get_connection_fn=lambda: _connection(path))
    return cp.execute_proposed_event(
        OperatorActionIntent(kind='approve_calendar_event', action_id=aid),
        task_id='review-task', session_id='review-session', adapter=adapter, config=config,
        pending_row=_load(path, aid), load_pending_action_fn=lambda **k: _load(path, aid),
        claim_action_fn=lambda key: approvals.claim_pending_action(key, **kwargs),
        mark_action_executed_fn=lambda key, **k: approvals.mark_action_executed(key, **kwargs, **k),
        mark_outcome_unproven_fn=lambda key, **k: approvals.mark_action_outcome_unproven(key, **kwargs, **k),
        mark_effect_unrecorded_fn=lambda key, **k: approvals.mark_action_effect_unrecorded(key, **kwargs, **k),
        requeue_interrupted_fn=lambda key, **k: approvals.requeue_interrupted_action(key, **kwargs, **k),
        audit_log_fn=lambda *a, **k: None,
    )


@pytest.mark.parametrize('status_code', [429, 503])
def test_post_create_read_refusal_never_reopens_plain_pending(tmp_path, monkeypatch, status_code):
    path, aid, config = _stage_db(tmp_path, provider='graph')
    monkeypatch.setattr(cp, '_connection_default', lambda: _connection(path))
    created = []

    def create(cal, value):
        created.append(replace(value, uid='graph-assigned-accepted'))
        return created[-1]

    def get(cal, uid):
        raise CalendarRefusedError(status_code, reason='rate_limited' if status_code == 429 else 'http_503')

    result = _run(path, aid, config, SimpleNamespace(provider_id='graph', events_in_range=lambda *a, **k: [], create_event=create, get_event=get))
    row = _load(path, aid)
    assert len(created) == 1
    assert row['status'] in {'outcome_unproven', 'effect_unrecorded'}, (result.status, row['status'], result.response_text)
    assert 'graph-assigned-accepted' in row['result_json'], 'known provider identity lost after accepted create'


@pytest.mark.parametrize('title', ['Recorded supplier call', 'Novel recorded laboratory session'])
def test_effect_unrecorded_is_verify_only_even_if_event_is_absent(tmp_path, title):
    path, aid, config = _stage_db(tmp_path, title=title, status='effect_unrecorded')
    scope = json.loads(_load(path, aid)['scope_json'])
    with _connection(path) as conn:
        conn.execute('UPDATE operator_action_requests SET result_json=? WHERE action_id=?',
                     (json.dumps({'uid': scope['intent_uid'], 'provider_uid': scope['intent_uid'], 'receipt_error': 'earlier write failed'}), aid))
    created = []

    def get(cal, uid):
        if created:
            return created[-1]
        raise CalendarRefusedError(404, reason='not_found')

    result = _run(path, aid, config, SimpleNamespace(provider_id='caldav', get_event=get,
        events_in_range=lambda *a, **k: [], create_event=lambda cal, value: (created.append(value) or value)))
    assert not created, ('verify-only state recreated an already-proven effect', result.status)


@pytest.mark.parametrize('title', ['Team briefing', 'Novel maintenance briefing'])
def test_graph_matching_content_is_not_proof_of_effect_identity(tmp_path, title):
    path, aid, config = _stage_db(tmp_path, provider='graph', title=title, status='outcome_unproven')
    # This is an unrelated pre-existing event with the same visible fields. Its
    # provider identity is known here and is NOT bound to this pending action.
    unrelated = replace(event('unrelated-provider-event', 'graph', title), tz_name='UTC')
    result = _run(path, aid, config, SimpleNamespace(provider_id='graph',
        durable_to_provider_id=lambda uid: '', events_in_range=lambda *a, **k: [unrelated],
        get_event=lambda *a, **k: unrelated))
    assert not result.ok and _load(path, aid)['status'] != 'executed', (result.status, result.details)


def _live_calendar_worker(path, aid, config, entered, release, dispatch_log):
    cp._connection_default = lambda: _connection(path)

    def ranges(*a, **k):
        entered.set()
        if not release.wait(10):
            raise RuntimeError('review synchronization timeout')
        return []

    def create(cal, value):
        with open(dispatch_log, 'a') as f:
            f.write('worker-create\n')
        return value

    scope = json.loads(_load(path, aid)['scope_json'])
    exact = replace(event(scope['intent_uid'], 'caldav', scope['title']), tz_name='UTC')
    _run(path, aid, config, SimpleNamespace(provider_id='caldav', events_in_range=ranges,
        create_event=create, get_event=lambda *a, **k: exact))


@pytest.mark.parametrize('title', ['Owner overlap', 'Novel parallel approval'])
def test_separate_process_cannot_reclaim_a_live_calendar_owner(tmp_path, title):
    path, aid, config = _stage_db(tmp_path, title=title)
    ctx = multiprocessing.get_context('spawn')
    entered, release = ctx.Event(), ctx.Event()
    dispatch_log = tmp_path / 'synthetic-dispatches.txt'
    child = ctx.Process(target=_live_calendar_worker, args=(path, aid, config, entered, release, dispatch_log))
    child.start()
    parent_created = []
    try:
        assert entered.wait(8), f'child failed to enter claim, exit={child.exitcode}'
        assert child.is_alive() and _load(path, aid)['status'] == 'executing'

        def get(cal, uid):
            if parent_created:
                return parent_created[-1]
            raise CalendarRefusedError(404, reason='not_found')

        def create(cal, value):
            parent_created.append(value)
            with dispatch_log.open('a') as f:
                f.write('parent-create\n')
            return value

        result = _run(path, aid, config, SimpleNamespace(provider_id='caldav', get_event=get,
            events_in_range=lambda *a, **k: [], create_event=create))
    finally:
        release.set()
        child.join(8)
        if child.is_alive():
            child.terminate()
            child.join(3)
    assert child.exitcode == 0, child.exitcode
    dispatched = dispatch_log.read_text().splitlines()
    assert len(dispatched) == 1, (dispatched, result.status, 'a live worker was declared dead by another process')
