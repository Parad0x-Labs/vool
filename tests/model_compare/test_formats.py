from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context
from tests.disclosure.test_served_rules import recording_daemon, reviewed_chat
from tests.model_compare.test_served_compare import (
    compare,
    register,
    streaming_provider,
)


@pytest.mark.parametrize('field,value', [('plain', None), ('native-plain', None),
    ('tools', [{'type': 'function', 'function': {'name': 'lookup_hours', 'description': 'Read public hours',
        'parameters': {'type': 'object', 'properties': {}}}}]),
    ('tool_choice', 'none'), ('response_format', {'type': 'json_object'}),
    ('format', {'type': 'object', 'properties': {'hours': {'type': 'string'}}}),
])
def test_prepared_replay_preserves_inert_controls_and_refuses_cross_protocol(tmp_path, monkeypatch, field, value):
    from adapters.base_adapter import ModelRequest
    from core.cloud_escalation_policy import set_free_cloud_enabled
    from core.context_namespace import ensure_chat_namespace
    from core.disclosure.wire import (
        close_chat,
        get_preview,
        note_response,
        note_sent,
        prepare,
        request_context,
    )
    from core.memory.revocation import initialize_memory_home
    from core.model_compare.prepared import PreparedReplay
    from core.policy_engine import set_operator_policy_values
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    home = tmp_path/'home'
    monkeypatch.setenv('VOOL_HOME', str(home))
    configure_runtime_home(home)
    configure_default_db_path(home/'data/test.db')
    initialize_memory_home()
    chat = 'compare-control-input'
    ensure_chat_namespace(chat, project_id='', grant_confirmed_profile=True)
    set_operator_policy_values({'system.local_only_mode': False, 'network.outbound_enabled': True})
    set_free_cloud_enabled(True)
    try:
        request = ModelRequest('summarize', 'Public workshop hours.', context={'chat_id': chat},
                               metadata={'privacy_class': 'public'})
        payload = {'model': 'source', 'messages': [{'role': 'user', 'content': request.prompt}]}
        if field not in {'plain', 'native-plain'}:
            payload[field] = value
        native = field in {'format', 'native-plain'}
        controls = {'temperature': .47, 'top_p': .81, 'stop': ['END']}
        if native:
            payload['options'] = {**controls, 'num_predict': 64}
        else:
            payload.update(controls, max_tokens=64)
        source_url = 'http://127.0.0.1:8123/api/chat' if native else 'https://source.invalid/v1/chat/completions'
        target_url = 'http://127.0.0.1:8124/api/chat' if native else 'https://target.invalid/v1/chat/completions'
        other_url = 'https://target.invalid/v1/chat/completions' if native else 'http://127.0.0.1:8124/api/chat'
        wire = prepare(payload, url=source_url, request=request,
                       context=request_context(request, provider='source-provider', cloud=True))
        if field == 'response_format':
            foreign_request = ModelRequest('summarize', request.prompt, context={'chat_id': chat},
                                           metadata={'privacy_class': 'public'})
            foreign_context = request_context(foreign_request, provider='messages-provider', cloud=True)
            foreign_context['request_format'] = 'anthropic'
            foreign = prepare({'model': 'messages-source', 'max_tokens': 128, 'system': 'Preserve this source instruction.',
                'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': request.prompt}]}]},
                url='https://source.invalid/v1/messages', request=foreign_request, context=foreign_context)
            foreign.consume_wire(foreign.destination)
            note_sent(foreign.preview_id)
            note_response(foreign_request, 'The workshop opens at noon.', usable=True)
            foreign_source = get_preview(foreign.preview_id)
            assert foreign_source.ready, 'Plain Messages input must retain its system text when replayed.'
            foreign_replay = PreparedReplay(foreign.preview_id, foreign_source.digest, 'target-provider', 'target')
            translated = foreign_replay.apply({'model': 'target', 'stream': False}, provider='target-provider',
                                              url='https://target.invalid/v1/chat/completions')
            assert translated['messages'][0] == {'role': 'system', 'content': 'Preserve this source instruction.'}
            assert translated['messages'][1]['content'] == [{'type': 'text', 'text': request.prompt}]
            assert translated['max_tokens'] == 128
            assert 'system' not in translated

        wire.consume_wire(wire.destination)
        note_sent(wire.preview_id)
        note_response(request, 'The workshop opens at noon.', usable=True)
        source = get_preview(wire.preview_id)
        assert source.ready, 'Completed text with inert request settings must remain comparable.'
        replay = PreparedReplay(source.preview_id, source.digest, 'target-provider', 'target')
        result = replay.apply({'model': 'target', 'stream': True}, provider='target-provider',
                              url=target_url)
        if field not in {'plain', 'native-plain'}:
            assert result[field] == value
        assert result['messages'] == payload['messages']
        assert result['model'] == 'target'
        if not native:
            assert result['stream_options'] == {'include_usage': True}
        if field in {'plain', 'native-plain'}:
            translated = replay.apply({'model': 'target', 'stream': False}, provider='target-provider', url=other_url)
            if native:
                assert {key: translated[key] for key in controls} == controls
                assert translated['max_tokens'] == 64
            else:
                assert {key: translated['options'][key] for key in controls} == controls
                assert translated['options']['num_predict'] == 64
        else:
            with pytest.raises(ValueError, match='same request format'):
                replay.apply({'model': 'target', 'stream': False}, provider='target-provider', url=other_url)
    finally:
        close_chat(chat)
        configure_default_db_path(None)
        configure_runtime_home(None)


def test_provider_run_tools_are_not_inert_comparison_definitions():
    from core.model_compare.prepared import format_compatible
    from core.model_compare.tool_observation import requested_tool

    assert format_compatible({'tools': [{'type': 'web_search'}]}, source_native=False, target_native=False)
    assert requested_tool({'tool_calls': [{'function': {'name': 'write_something'}}]})
    assert not requested_tool({'tool_calls': [], 'message': {'content': 'Ordinary text'}})


def test_served_comparison_uses_configured_cloud_fallback_adapter_and_real_caps(tmp_path):
    provider = streaming_provider()
    with _provider_context(provider), recording_daemon(tmp_path) as (daemon, _, _):
        chat = rig.canonical_session('compare-fallback-adapter')
        attachment = daemon.upload(data=b'A repair workshop opens at noon.', name='workshop.txt',
                                   declared_type='text/plain', session_id=chat)
        ident = attachment.get('attachment_id') or attachment.get('id') or attachment['attachment']['id']
        reviewed_chat(daemon, 'Summarize the attached workshop document.', session_id=chat, attachments=[ident])
        source = [p for p in _json(daemon.base_url+'/api/disclosure/pending')['requests']
                  if p['chat_id'] == chat and p.get('can_compare')][-1]
        register(daemon, [provider])
        script = '''
from core.model_registry import ModelRegistry
r=ModelRegistry()
m=next(m for m in r.list_manifests() if m.provider_name=='compare-0')
m.adapter_type='cloud_fallback_provider'
r.register_manifest(m)
'''
        changed = subprocess.run([sys.executable, '-c', script], env=daemon.env(), cwd=rig.REPO_ROOT,
                                 capture_output=True, text=True, timeout=20, check=False)
        assert changed.returncode == 0, changed.stdout+changed.stderr
        choices = _json(daemon.base_url+'/api/disclosure/compare/models?id='+source['id'])['models']
        target = next(m for m in choices if m['provider'] == 'compare-0')
        assert target['available'] and target['paid'], target
        job, previews = compare(daemon, source, [target['id']])
        result = job['results'][0]
        assert result['state'] == 'completed' and result['same_input'] is True, job
        assert result['measured']['billed_usd'] == .001 and result['spend_state'] == 'settled', result
        assert len(provider.raw) == len(previews) == 1
        assert provider.raw[0] == previews[0]['body'].encode()
        assert json.loads(provider.raw[0])['messages'] == json.loads(source['body'])['messages']


@pytest.mark.parametrize('change', ['unknown-field', 'tools', 'image', 'system-blocks', 'tool-role', 'unknown-format'])
def test_messages_conversion_refuses_any_input_it_cannot_preserve(change):
    from core.disclosure.wire import Preview, encode_body
    from core.model_compare.prepared import source_payload

    body = {'model': 'source', 'max_tokens': 64, 'system': 'Public instructions.',
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Public workshop hours.'}]}]}
    protocol = 'anthropic'
    if change == 'unknown-field':
        body['future_control'] = {'must_preserve': True}
    elif change == 'tools':
        body['tools'] = [{'name': 'lookup_hours', 'input_schema': {'type': 'object'}}]
    elif change == 'image':
        body['messages'][0]['content'] = [{'type': 'image', 'source': {'type': 'base64', 'data': 'AAAA'}}]
    elif change == 'system-blocks':
        body['system'] = [{'type': 'text', 'text': 'Public instructions.'}]
    elif change == 'tool-role':
        body['messages'][0]['role'] = 'tool'
    else:
        protocol = 'unknown-format'
    source = Preview('', '', '', 'source', 'https://source.invalid/v1/messages', encode_body(body), request_format=protocol)
    with pytest.raises(ValueError):
        source_payload(source)


def test_messages_roundtrip_preserves_canonical_text_and_generation_controls():
    from core.anthropic_messages_protocol import openai_request_to_anthropic
    from core.disclosure.wire import Preview, encode_body
    from core.model_compare.prepared import input_digest, source_payload

    original = {'model': 'source', 'max_tokens': 64, 'temperature': .4, 'top_p': .9, 'stop': ['END'],
                'messages': [{'role': 'system', 'content': 'Keep the public specification precise.'},
                             {'role': 'user', 'content': 'The workshop opens at noon.'}]}
    converted = openai_request_to_anthropic(original)
    source = Preview('', '', '', 'source', 'https://source.invalid/v1/messages', encode_body(converted), request_format='anthropic')
    prepared = source_payload(source)
    assert input_digest(prepared) == input_digest(original)
    assert {key: prepared[key] for key in ('model', 'max_tokens', 'temperature', 'top_p', 'stop')} == {
        key: original[key] for key in ('model', 'max_tokens', 'temperature', 'top_p', 'stop')}
    assert json.loads(source.body) == converted


@pytest.mark.parametrize('native,payload', [
    (False, {'reasoning': {'enabled': False}}),
    (True, {'think': True}),
    (True, {'options': {'seed': 123}}),
])
def test_unknown_generation_controls_require_the_same_request_format(native, payload):
    from core.model_compare.prepared import format_compatible

    assert format_compatible(payload, source_native=native, target_native=not native)
