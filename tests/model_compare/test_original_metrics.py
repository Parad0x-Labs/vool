from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat
from tests.model_compare.test_served_compare import compare, register, streaming_provider


@pytest.mark.parametrize('mode,original_cost,extra,expected', [
    ('reported', .0021, {}, .0021), ('zero', 0, {}, 0),
    ('byok-missing', .0021, {'is_byok': True}, None),
    ('byok-complete', .0021, {'is_byok': True, 'cost_details': {'upstream_inference_cost': .0009}}, .0021+.0009),
    ('byok-invalid', .0021, {'is_byok': 'true', 'cost_details': {'upstream_inference_cost': .0009}}, None),
])
def test_original_column_reads_its_exact_call_after_another_turn_is_billed(tmp_path, mode, original_cost, extra, expected):
    other = streaming_provider()
    with _provider_context(other), recording_daemon(tmp_path) as (daemon, provider, raw):
        usage = {'prompt_tokens': 91, 'completion_tokens': 11, 'cost': original_cost, **extra}
        handler = provider._server.RequestHandlerClass

        def post(connection):
            body = connection.rfile.read(int(connection.headers.get('Content-Length') or 0))
            raw.append(body)
            parsed = json.loads(body)
            message = rig._probe_reply(parsed) or {'role': 'assistant', 'content': 'The repair workshop opens at noon.'}
            if parsed.get('stream'):
                events = [{'model': parsed['model'], 'choices': [{'delta': {'content': message['content']}, 'finish_reason': 'stop'}]},
                          {'choices': [], 'usage': dict(usage)}]
                data = b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in events) + b'data: [DONE]\n\n'
                connection.send_response(200)
                connection.send_header('Content-Type', 'text/event-stream')
                connection.send_header('Content-Length', str(len(data)))
                connection.end_headers()
                connection.wfile.write(data)
            else:
                connection._send({'model': parsed['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}],
                                  'usage': dict(usage)})

        handler.do_POST = post
        session = rig.canonical_session('original-metrics-source')
        attachment = daemon.upload(data=b'A repair workshop opens at noon.', name='workshop.txt',
                                   declared_type='text/plain', session_id=session)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment['attachment']['id']
        response, _ = reviewed_chat(daemon, 'Summarize the attached workshop document.',
                                   session_id=session, attachments=[ident])
        sources = [item for item in _json(daemon.base_url + '/api/disclosure/pending')['requests']
                   if item.get('can_compare') and item['chat_id'] == session]
        assert sources, response
        source = sources[-1]
        before = len(raw)
        usage.update(prompt_tokens=7, completion_tokens=2, cost=.09)
        reviewed_chat(daemon, 'Explain how a museum display differs from a repair workshop.',
                      session_id=rig.canonical_session('other-metrics-turn'))
        assert len(raw) > before
        def original_rows(call_id=None):
            script = """
import json
from storage.db import get_connection
from core.usage_meter import measured_call
conn = get_connection()
try:
    count = conn.execute('SELECT COUNT(*) FROM token_usage WHERE provider_id=?', (PROVIDER,)).fetchone()[0]
finally:
    conn.close()
print(json.dumps({'rows': count, 'timing': measured_call(CALL) if CALL else None}))
""".replace('\n+', '\n').replace('PROVIDER', repr(source['provider'])).replace('CALL', repr(call_id))
            result = subprocess.run([sys.executable, '-c', script], env=daemon.env(), cwd=rig.REPO_ROOT,
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stdout + result.stderr
            return json.loads(result.stdout.splitlines()[-1])

        original_count = original_rows()['rows']
        assert original_count > 0
        register(daemon, [other])
        models = _json(daemon.base_url + '/api/disclosure/compare/models?id=' + source['id'])['models']
        selected = [model['id'] for model in models if model['provider'] == 'compare-0']
        job, _ = compare(daemon, source, selected)
        metrics = job.get('original_measured')
        assert metrics is not None, job
        assert metrics['prompt_tokens'] == 91 and metrics['output_tokens'] == 11, job
        assert metrics['billed_usd'] == expected, job
        assert metrics['total_ms'] > 0, job

        persisted = original_rows(metrics['call_id'])
        assert persisted['rows'] == original_count
        assert persisted['timing']['total_ms'] == metrics['total_ms']
        assert persisted['timing']['first_token_ms'] == metrics['first_token_ms']
        evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
        if evidence:
            tag = os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')
            Path(evidence, 'original-call-metrics-' + mode + '-' + tag + '.json').write_text(json.dumps({
                'job': job, 'persisted': persisted, 'original_usage_rows': original_count,
                'received': [body.decode() for body in raw]}, indent=2))
