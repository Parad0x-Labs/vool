from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat
from tests.model_compare.test_served_compare import compare, register, streaming_provider


def test_tool_only_stream_frame_cannot_be_reported_as_a_completed_comparison(tmp_path):
    provider = streaming_provider()
    marker = tmp_path / 'comparison-must-not-write'
    handler = provider._server.RequestHandlerClass

    def post(connection):
        raw = connection.rfile.read(int(connection.headers.get('Content-Length') or 0))
        provider.raw.append(raw)
        model = json.loads(raw)['model']
        frames = [
            {'model': model, 'choices': [{'delta': {'content': 'An incomplete draft.'}, 'finish_reason': None}]},
            {'model': model, 'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-action', 'type': 'function',
                'function': {'name': 'sandbox__run_command', 'arguments': json.dumps({'command': 'touch ' + shlex.quote(str(marker))})}}]},
                'finish_reason': None}]},
            # A later "stop" must not erase the tool request in a prior frame.
            {'model': model, 'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 40, 'completion_tokens': 8, 'cost': .001}},
        ]
        data = b''.join(('data: ' + json.dumps(frame) + '\n\n').encode() for frame in frames) + b'data: [DONE]\n\n'
        connection.send_response(200)
        connection.send_header('Content-Type', 'text/event-stream')
        connection.send_header('Content-Length', str(len(data)))
        connection.end_headers()
        connection.wfile.write(data)

    handler.do_POST = post
    with _provider_context(provider), recording_daemon(tmp_path) as (daemon, _, _raw):
        session = rig.canonical_session('tool-signal-comparison')
        attachment = daemon.upload(data=b'A repair workshop opens at noon.', name='workshop.txt',
                                   declared_type='text/plain', session_id=session)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment['attachment']['id']
        reviewed_chat(daemon, 'Summarize the attached workshop document.', session_id=session, attachments=[ident])
        source = [item for item in _json(daemon.base_url + '/api/disclosure/pending')['requests']
                  if item.get('can_compare') and item['chat_id'] == session][-1]
        register(daemon, [provider])
        choices = _json(daemon.base_url + '/api/disclosure/compare/models?id=' + source['id'])['models']
        selected = [model['id'] for model in choices if model['provider'] == 'compare-0']
        job, _ = compare(daemon, source, selected)
        result = job['results'][0]
        assert result['state'] in {'refused', 'failed'}, result
        assert result['partial'] is True and result['answer'] == 'An incomplete draft.', result
        assert result['measured']['billed_usd'] == .001 and result['spend_state'] == 'settled', result
        assert not marker.exists()

        evidence = os.environ.get('VOOL_DISCLOSURE_EVIDENCE_ROOT')
        if evidence:
            tag = os.environ.get('VOOL_DISCLOSURE_RUN_TAG', 'run')
            Path(evidence, 'comparison-tool-signal-' + tag + '.json').write_text(json.dumps({
                'job': job, 'received': [body.decode() for body in provider.raw],
                'marker_exists': marker.exists()}, indent=2))
