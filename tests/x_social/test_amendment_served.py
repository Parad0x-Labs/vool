"""Real served regression checks; only the external provider is scripted."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._credential_intelligence_support import isolated_home, vault_home
from tests._served_skill_rig import ProviderState, ServedDaemon, make_provider_server
from tests.x_social.support import resource


@pytest.fixture
def served(vault_home, monkeypatch):
    import tests._served_skill_rig as provider

    state = ProviderState()
    cursor = [0]
    original = provider._turn_response
    def response(body, state, model):
        if not body.get('tools'):
            if 'Output mode: summary_block' in str(body.get('messages', [])):
                return provider._completion(content=json.dumps({'summary': state.script[-1].get('final', 'Finished.'), 'bullets': []}))
            return original(body, state, model)
        step = state.script[min(cursor[0], len(state.script) - 1)]
        cursor[0] += 1
        name = step.get('tool', 'respond.direct').replace('.', '__')
        args = step.get('arguments', {'message': step.get('final', 'Finished.')})
        return provider._completion(tool_calls=[provider._tool_call('amendment-' + str(cursor[0]), name, args)], finish='tool_calls')
    monkeypatch.setattr(provider, '_turn_response', response)
    server, port = make_provider_server(state)
    resource('port', port, 'started')
    daemon = ServedDaemon(vault_home, port, extra_env={
        'VOOL_REGISTER_INSTALLED_OLLAMA_MODELS': '0', 'VOOL_SKIP_PROVIDER_PREWARM': '1',
        'VOOL_BLACKBOX_DIR': str(vault_home / 'data/blackbox'),
    })
    try:
        daemon.start(wait=False)
        resource('process', daemon.proc.pid, 'started')
        resource('port', daemon.port, 'started')
        daemon.wait_ready()
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        assert (cert.get('result') or cert).get('state') == 'verified', cert
        def chat(text, script, label, session='amendment-chat'):
            state.script = script
            state.requests.clear()
            cursor[0] = 0
            reply = daemon.post('/v1/chat/completions', {
                'model': 'custom-byok:probe-model', 'messages': [{'role': 'user', 'content': text}],
                'surface': 'desktop', 'stream': False, 'mode': 'auto',
                'session_id': session, 'turn_id': label,
            }, timeout=90)
            folder = Path(os.environ['SOCIAL_SHOTS'])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / (label + '.json')).write_text(json.dumps({'reply': reply,
                'requests': state.requests, 'events': daemon.session_events(reply.get('vool_session_id', session))}, indent=2))
            return reply
        yield SimpleNamespace(home=vault_home, daemon=daemon, state=state, chat=chat, cursor=cursor)
    finally:
        if daemon.proc:
            proc = daemon.proc
            proc.terminate()
            proc.wait(timeout=20)
            resource('process', proc.pid, 'closed')
            resource('port', daemon.port, 'closed')
            daemon.proc = None
        server.shutdown()
        server.server_close()
        resource('port', port, 'closed')


def test_bare_chat_rating_reaches_native_save_with_server_stored_draft(served):
    from core import x_feedback

    draft = 'Checkpoint recovery keeps completed work safe.'
    first = served.chat('Hello.', [{'final': 'Hello.'}], 'rating-open')
    # Seed a prior assistant turn through its public persistence writer. The rating
    # request still crosses real HTTP ingress, planning, native offer and execution.
    from core.persistent_memory import append_conversation_event
    from core.chat_housekeeping import last_assistant_message
    append_conversation_event(session_id=first['vool_session_id'], user_input='Draft a caption.',
        assistant_output='Draft:\n' + draft + '\n\nHow would you rate this draft from 1 to 10?',
        source_context={'surface': 'desktop'})
    assert draft in last_assistant_message(first['vool_session_id'])['text']
    rated = served.chat('7 - hook too long', [
        {'tool': 'x.feedback.save', 'arguments': {'rating': 7, 'comment': 'hook too long',
          'text': 'invented provider text', 'action_id': None, 'kind': None}},
        {'final': 'Rating saved.'},
    ], 'rating-answer', session=first['vool_session_id'])
    [row] = x_feedback.inspect('global', home=served.home)['entries']
    assert row['rating'] == 7 and row['comment'] == 'hook too long' and row['text'] == draft, rated
    assert any('x__feedback__save' == tool['function']['name']
               for body in served.state.requests for tool in body.get('tools', []))
