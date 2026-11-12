from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat


def streaming_provider(*, failure=False, pause=0.05, unknown=False, invalid=False, zero=False):
    provider = rig.CapturingProvider()
    provider.raw = []
    handler = provider._server.RequestHandlerClass
    def post(self):
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        provider.raw.append(body)
        parsed = json.loads(body)
        if failure:
            return self._send({'error': {'message': 'outside provider unavailable'}}, status=503)
        model = parsed['model']
        events = [
            {'model': model, 'choices': [{'delta': {'content': 'A repair workshop '}, 'finish_reason': None}]},
            {'model': model, 'choices': [{'delta': {'content': 'opens at noon.'}, 'finish_reason': 'stop'}]},
            {'model': model, 'choices': [], 'usage': {} if unknown else {'prompt_tokens':'40','completion_tokens':float('nan'),'cost':True} if invalid else {'prompt_tokens':0,'completion_tokens':0,'cost':0} if zero else {'prompt_tokens': 40, 'completion_tokens': 8, 'cost': 0.001}},
        ]
        chunks = [('data: '+json.dumps(e)+'\n\n').encode() for e in events] + [b'data: [DONE]\n\n']
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(sum(map(len, chunks))))
        self.end_headers()
        try:
            for chunk in chunks:
                time.sleep(pause)
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
    handler.do_POST = post
    return provider


def register(daemon, providers, *, cap=False, timeout=3):
    values = [(p.base_url, 'compare-'+str(n), 'compare-model-'+str(n), timeout[n] if isinstance(timeout, list) else timeout) for n, p in enumerate(providers)]
    script = '''
from core.model_registry import ModelRegistry
from storage.model_provider_manifest import get_provider_manifest
for base,provider,model,call_timeout in VALUES:
    m=get_provider_manifest('disclosure-recording','disclosure-recording:scripted').model_copy(deep=True,
        update={'provider_name':provider,'model_name':model})
    m.runtime_config.update(base_url=base,timeout_seconds=call_timeout)
    m.metadata.update(cost_class='paid_cloud')
    ModelRegistry().register_manifest(m)
if CAP:
    from core.paid_call_reservation import spend_limits
    from core.model_spend_ledger import reserve_spend
    limits=spend_limits()
    for n in range(int(limits.daily_usd/limits.per_call_usd)):
        reserve_spend(model_call_id='fixture-hold-'+str(n),task_id='fixture-task-'+str(n),
                      subtask_id='fixture',model_id='fixture-model',maximum_usd=limits.per_call_usd,limits=limits)
'''.replace('VALUES', repr(values)).replace('CAP', repr(cap))
    result = subprocess.run([sys.executable, '-c', script], env=daemon.env(), cwd=rig.REPO_ROOT,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def compare(daemon, source, models):
    started = _json(daemon.base_url + '/api/disclosure/compare/start', {
        'id': source['id'], 'sha256': source['sha256'], 'models': models})
    previews = []
    deadline = time.monotonic() + 30
    job = None
    while time.monotonic() < deadline:
        for item in _json(daemon.base_url + '/api/disclosure/pending')['requests']:
            if item['state'] == 'waiting':
                previews.append(item)
                # Deliberate owner reading time must not appear in TTFT.
                time.sleep(.7)
                _json(daemon.base_url + '/api/disclosure/decision', {
                    'id': item['id'], 'sha256': item['sha256'], 'action': 'send'})
                item['_test_approved_at'] = time.monotonic()
        job = _json(daemon.base_url + '/api/disclosure/compare/job?id=' + started['id'])
        if job['state'] != 'running':
            return job, previews
        time.sleep(.05)
    pytest.fail({'job': job, 'previews': previews, 'log': daemon.log_tail()})


@pytest.mark.parametrize('mode', ['success', 'failure', 'timeout', 'cap', 'unknown', 'invalid', 'zero'])
def test_served_compare_preserves_input_previews_every_destination_and_reads_real_ledgers(tmp_path, mode):
    first = streaming_provider(unknown=mode == 'unknown', invalid=mode == 'invalid', zero=mode == 'zero')
    second = streaming_provider(failure=mode == 'failure', pause=2 if mode == 'timeout' else .05)
    with _provider_context(first), _provider_context(second), recording_daemon(tmp_path) as (daemon, _, _raw):
        session = rig.canonical_session('compare-'+mode)
        attachment = daemon.upload(data=b'A repair workshop opens at noon.', name='workshop.txt',
                                   declared_type='text/plain', session_id=session)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment['attachment']['id']
        response, _ = reviewed_chat(daemon, 'Summarize the attached workshop document.', session_id=session, attachments=[ident])
        sources = [p for p in _json(daemon.base_url + '/api/disclosure/pending')['requests'] if p.get('can_compare')]
        assert sources, {'response': response, 'log': daemon.log_tail()}
        source = sources[-1]
        register(daemon, [first, second], cap=mode == 'cap', timeout=[3,.2] if mode == 'timeout' else 3)
        choices = _json(daemon.base_url + '/api/disclosure/compare/models?id='+source['id'])['models']
        selected = [c['id'] for c in choices if c['provider'].startswith('compare-')]
        assert len(selected) == 2 and all(c['available'] for c in choices if c['id'] in selected), choices
        job, previews = compare(daemon, source, selected)
        evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
        if evidence:
            Path(evidence, 'served-compare-'+mode+'-'+os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')+'.json').write_text(
                json.dumps({'job': job, 'previews': previews, 'first': [b.decode() for b in first.raw],
                            'second': [b.decode() for b in second.raw]}, indent=2))
        assert job['state'] == 'done' and len(job['results']) == 2, job
        if mode == 'cap':
            assert first.raw == second.raw == previews == []
            assert all(r['state'] == 'refused' and 'Spending limit' in r['error'] for r in job['results']), job
            return
        assert len(previews) == 2, job
        for provider in (first, second):
            assert len(provider.raw) == 1
            received = provider.raw[0]
            assert received in [p['body'].encode() for p in previews]
            assert json.loads(received)['messages'] == json.loads(source['body'])['messages']
        if mode == 'success':
            assert job['results'][-1]['measured']['first_token_ms'] <= (time.monotonic()-previews[-1]['_test_approved_at'])*1000 + 50, job
        first_result = job['results'][0]
        assert first_result['state'] == 'completed' and first_result['same_input'] is True, job
        assert first_result.get('spend_state') == ('billing_ambiguous' if mode in {'unknown','invalid'} else 'settled'), job
        metrics = first_result['measured']
        assert metrics is not None and metrics['first_token_ms'] is not None and metrics['total_ms'] >= metrics['first_token_ms'], job
        if mode in {'unknown','invalid'}:
            assert metrics['billed_usd'] is None and metrics['prompt_tokens'] is None and metrics['output_tokens'] is None
        elif mode == 'zero':
            assert metrics['billed_usd'] == metrics['prompt_tokens'] == metrics['output_tokens'] == 0
        else:
            assert metrics['billed_usd'] == .001 and metrics['prompt_tokens'] == 40 and metrics['output_tokens'] == 8
        if mode in {'failure', 'timeout'}:
            assert job['results'][1]['state'] == 'failed', job
        preferred = _json(daemon.base_url + '/api/disclosure/compare/prefer', {'id': job['id'], 'model': first_result['model']})
        assert preferred == {'ok': True, 'routing_changed': False}
        note = json.loads((daemon.home/'config/model-comparison-preference.json').read_text())
        assert set(note) == {'model', 'chosen_at'}


def test_comparison_replay_is_idempotent_cancel_sends_nothing_and_forget_revokes_source(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from urllib.error import HTTPError
    first, second = streaming_provider(), streaming_provider()
    with _provider_context(first), _provider_context(second), recording_daemon(tmp_path) as (daemon, _, _):
        session = rig.canonical_session('compare-cancel')
        attachment = daemon.upload(data=b'Cobalt Workshop opens at noon.', name='workshop.txt',
                                   declared_type='text/plain',session_id=session)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment['attachment']['id']
        reviewed_chat(daemon, 'Summarize the attached workshop document.', session_id=session, attachments=[ident])
        source = [p for p in _json(daemon.base_url+'/api/disclosure/pending')['requests'] if p.get('can_compare')][-1]
        register(daemon, [first, second])
        choices = _json(daemon.base_url+'/api/disclosure/compare/models?id='+source['id'])['models']
        selected = [c['id'] for c in choices if c['provider'].startswith('compare-')]
        payload = {'id':source['id'],'sha256':source['sha256'],'models':selected}
        with ThreadPoolExecutor(max_workers=2) as pool:
            starts = list(pool.map(lambda _: _json(daemon.base_url+'/api/disclosure/compare/start',payload),range(2)))
        assert starts[0]['id'] == starts[1]['id']
        job_id = starts[0]['id']
        deadline = time.monotonic()+10
        while time.monotonic()<deadline:
            pending = [p for p in _json(daemon.base_url+'/api/disclosure/pending')['requests'] if p['state']=='waiting']
            if pending:
                break
            time.sleep(.05)
        assert pending
        _json(daemon.base_url+'/api/disclosure/compare/cancel',{'id':job_id})
        while time.monotonic()<deadline:
            job = _json(daemon.base_url+'/api/disclosure/compare/job?id='+job_id)
            if job['state']!='running':
                break
            time.sleep(.05)
        assert job['state']=='cancelled', job
        assert first.raw == second.raw == []
        assert job['results'][0]['spend_state']=='pin_not_dispatched',job
        daemon.chat('Forget Cobalt Workshop.',session_id=session)
        with pytest.raises(HTTPError) as expired:
            _json(daemon.base_url+'/api/disclosure/compare/start',payload)
        assert expired.value.code in {409,410}
        assert first.raw == second.raw == []
