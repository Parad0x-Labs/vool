"""Creation requests with a workspace location cross the real HTTP/permission doors.

The provider is controlled: this proves routing, disk effects and reporting, not
live model competence. Every daemon and storage seed uses its own disposable home.
"""
import json
import pytest
from pathlib import Path
from urllib.request import Request, urlopen

from tests._blackbox_served_rig import SEED_MANIFEST, ServedDaemon, run_in_home, pending_session_approvals
from tests.test_task_board_reaches_the_model_served import _TaskBoardProvider, TASK_BOARD_HTML, _answer
from tests.test_workspace_location_is_not_search import DEMO_REQUEST

ROOT = Path(__file__).resolve().parents[1]


class BuildProvider(_TaskBoardProvider):
    """The builder uses the manifest's OpenAI dialect; certification uses Ollama."""
    def __init__(self, output_limited=False):
        super().__init__()
        self.artifact_calls = 0
        handler = self._server.RequestHandlerClass
        send = handler._send

        def send_both_dialects(instance, payload, status=200):
            is_generation = any('Generate the COMPLETE contents' in str(m.get('content', '')) for m in (self.calls[-1].get('messages', []) if self.calls else []))
            if is_generation and str(payload.get('message', {}).get('content', '')).startswith('<!DOCTYPE'):
                self.artifact_calls += 1
                if self.artifact_calls > 1:
                    payload = dict(payload, message={**payload['message'], 'content':TASK_BOARD_HTML.replace('</body>', '<!-- changed on regeneration --></body>')})
            if output_limited and is_generation and str(payload.get('message', {}).get('content', '')).startswith('<!DOCTYPE'):
                payload = dict(payload, message={'role':'assistant','content':'','thinking':'fixture reasoning'}, done_reason='length')
            if 'message' in payload:
                payload = dict(payload, choices=[{
                    'index': 0, 'finish_reason': 'stop', 'message': payload['message']}])
            return send(instance, payload, status)

        handler._send = send_both_dialects


@pytest.mark.parametrize("auto_empty, output_limited", [(False, False), (True, False), (True, True)])
def test_creation_reaches_builder_and_manual_approval_precedes_any_write(tmp_path, auto_empty, output_limited):
    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    decoy = workspace / 'keep.txt'
    if not auto_empty:
        decoy.write_text('untouched')
    selected_model = '' if auto_empty else 'stub-chat:2b'
    # The stub declares coding the way a local coding lane does; Auto ranks by that declaration.
    coding_seed = SEED_MANIFEST.replace('"tool_intent"],', '"tool_intent", "code_basic", "code_complex", "long_context"],')
    assert coding_seed != SEED_MANIFEST, 'the seed manifest no longer has the capability list this test extends'
    with BuildProvider(output_limited=output_limited) as provider:
        run_in_home(home, coding_seed.format(root=ROOT, base_url=provider.base_url, registered=['stub-chat:2b']))
        with ServedDaemon(home, env_extra={
            'VOOL_WORKSPACE_ROOT':str(workspace),
            'OLLAMA_HOST':provider.base_url,
            'VOOL_RAW_OLLAMA_API_URL':provider.base_url,
            'VOOL_OLLAMA_URL':provider.base_url,
            'VOOL_OLLAMA_CHAT_URL':provider.base_url+'/api/chat',
        }) as daemon:
            req = Request(daemon.base_url+'/api/model-tool-certification/run',
                data=json.dumps({'provider_name':'ollama-local','model_name':'stub-chat:2b','timeout_seconds':30}).encode(),
                headers={'Content-Type':'application/json'},method='POST')
            with urlopen(req, timeout=60) as response:
                assert json.load(response)['state']=='verified'
            # Bind the actual project/chat doors; Auto must not bypass the front door.
            if auto_empty:
                def post(path, payload):
                    req = Request(daemon.base_url+path, data=json.dumps(payload).encode(),
                                  headers={'Content-Type':'application/json'}, method='POST')
                    with urlopen(req, timeout=30) as response:
                        return json.load(response)
                project = post('/api/projects', {'name':'Demo', 'root':str(workspace)})['project']
                post('/api/chat/session', {'session_id':'creation-manual', 'project_id':project['id']})
            provider.calls.clear()
            manual = daemon.chat_stream(DEMO_REQUEST,session_id='creation-manual',model=selected_model,mode='manual',timeout=120)
            (tmp_path/'manual.json').write_text(json.dumps(manual,indent=2))
            (tmp_path/'provider.json').write_text(json.dumps(provider.calls,indent=2))
            text = _answer(manual)
            assert not (workspace/'index.html').exists(), text
            if auto_empty:
                from core.chat_session_identity import canonical_chat_session_id
                with urlopen(daemon.base_url+'/api/runtime/events?session='+canonical_chat_session_id('creation-manual')+'&limit=200') as response:
                    selected_events = json.load(response)['events']
                builder_starts = [e for e in selected_events if e.get('provider_id')=='ollama:builder' and e.get('event_type')=='model.call_started']
                assert len(builder_starts)==1
                assert builder_starts[0]['model_id']=='stub-chat:2b', builder_starts
            if output_limited:
                assert 'output limit' in text and 'No automatic retry' in text, text
                assert 'Select a different model and resend' in text, text
                assert not pending_session_approvals(home, 'creation-manual')
                from core.chat_session_identity import canonical_chat_session_id
                with urlopen(daemon.base_url+'/api/runtime/events?session='+canonical_chat_session_id('creation-manual')+'&limit=200') as response:
                    events = json.load(response)['events']
                started = [e for e in events if e.get('provider_id')=='ollama:builder' and e.get('event_type')=='model.call_started']
                failures = [e for e in events if e.get('reason')=='builder_output_limit']
                assert len(started)==1 and len(failures)==1, events
                assert not list(workspace.iterdir())
                return
            assert 'approval' in text.lower(), text
            from core.chat_session_identity import canonical_chat_session_id
            with urlopen(daemon.base_url+'/api/runtime/events?session='+canonical_chat_session_id('creation-manual')+'&limit=200') as response:
                approval_events = json.load(response)['events']
            attempt_ends = [e for e in approval_events if e.get('event_type') in {'runtime_attempt_completed','runtime_attempt_updated'}]
            assert attempt_ends and 'WAITING_APPROVAL' in attempt_ends[-1]['message'], attempt_ends
            assert 'No text matches' not in text
            approvals = pending_session_approvals(home, 'creation-manual')
            assert len(approvals) == 1, approvals
            token = approvals[0]['approval_id']
            req = Request(daemon.base_url+'/api/mode', data=json.dumps({
                'op':'resolve_approval','session_id':'creation-manual',
                'approval_id':token,'decision':'allow','scope':'once',
            }).encode(), headers={'Content-Type':'application/json'},method='POST')
            with urlopen(req, timeout=30) as response:
                assert json.load(response)['ok']
            auto = daemon.chat_stream(DEMO_REQUEST,session_id='creation-manual',model=selected_model,
                                      mode='manual',approval_token=token,timeout=120)
            (tmp_path/'approved-resume.json').write_text(json.dumps(auto,indent=2))
            target = workspace/'index.html'
            assert provider.artifact_calls == 1, 'Approval must reuse reviewed bytes without a new generation'
            assert target.exists(), _answer(auto)
            assert target.read_text().strip()==TASK_BOARD_HTML.strip(), target.read_text()[:400]
            if not auto_empty:
                assert decoy.read_text()=='untouched'
            assert 'No text matches' not in _answer(auto)
            assert 'index.html' in _answer(auto)
            assert any('task board' in text.lower() for text in provider.user_texts())
            if auto_empty:
                from tests.test_named_file_edit_followup import EDIT
                # Same chat, real Auto selection and approval doors, now edit what was created.
                edited = daemon.chat_stream(EDIT, session_id='creation-manual', model='', mode='manual', timeout=120)
                for _ in range(2):  # read approval, then byte-bound write approval
                    waiting = pending_session_approvals(home, 'creation-manual')
                    if not waiting:
                        break
                    assert target.read_text().strip() == TASK_BOARD_HTML.strip()
                    token = waiting[-1]['approval_id']
                    req = Request(daemon.base_url+'/api/mode', data=json.dumps({
                        'op':'resolve_approval','session_id':'creation-manual',
                        'approval_id':token,'decision':'allow','scope':'once',
                    }).encode(), headers={'Content-Type':'application/json'},method='POST')
                    with urlopen(req,timeout=30) as response:
                        assert json.load(response)['ok']
                    edited = daemon.chat_stream(EDIT,session_id='creation-manual',model='',mode='manual',
                                                approval_token=token,timeout=120)
                (tmp_path/'followup-edit.json').write_text(json.dumps(edited,indent=2))
                assert 'remember' not in _answer(edited).lower(), _answer(edited)
                assert 'changed on regeneration' in target.read_text(), _answer(edited)
                assert provider.artifact_calls == 2, 'The edit resumes its reviewed proposal without regeneration'
                edit_prompts = [str(m.get('content','')) for call in provider.calls for m in call.get('messages',[])
                                if '--- current index.html ---' in str(m.get('content',''))]
                assert edit_prompts and TASK_BOARD_HTML.strip() in edit_prompts[-1]
                assert 'Keep the existing tasks and behavior intact' in edit_prompts[-1]
