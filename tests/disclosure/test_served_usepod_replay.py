"""Deny-only paid-recovery lineage, without creating a payment operation."""
from __future__ import annotations

import json
import select
import subprocess
import sys

import pytest

from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _json, _provider_context, _resource
from tests.disclosure.test_served_rules import recording_daemon
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService

_SCRIPT = '''
import json,sys
from adapters.base_adapter import ModelRequest
from core.disclosure.usepod_replay import ReplayBinding,ReplayCheckedTransport
from core.disclosure.wire import DisclosureRefused
from core.model_registry import ModelRegistry
from core.runtime_provider_defaults import register_chat_cloud_model
from core.remote_fetch_policy import remote_fetch_policy_scope
import threading
from core.usepod.transport import UsePodTransportError
model,chat,project,operation=sys.argv[1:]
register_chat_cloud_model('usepod',model)
registry=ModelRegistry()
adapter=registry.build_adapter(registry.get_manifest('usepod-byok',model))
request=ModelRequest('chat','Synthetic public specification.',max_output_tokens=64,
 context={'chat_id':chat,'project_id':project},metadata={'privacy_class':'public'})
event=threading.Event()
with remote_fetch_policy_scope({'cancel_event':event}):
 plan=adapter._prepare(request,force_json=False,stream=False)
wire=plan.disclosure_wire
wire.consume_wire(wire.destination)
binding=ReplayBinding.capture(wire,plan.envelope)
record=binding.public_record()
recovered=ReplayBinding.from_record(json.loads(json.dumps(record)))
if operation!='cancel':
 binding=recovered
binding.verify(plan.envelope)
assert binding.scope_kind=='chat'
assert 'Synthetic public specification' not in json.dumps(record)
try:
 ReplayBinding.from_record(None)
except DisclosureRefused:
 missing_refused=True
else:
 missing_refused=False
print(json.dumps({'ready':True,'missing_refused':missing_refused}),flush=True)
sys.stdin.readline()
from core.policy_engine import set_operator_policy_values
from core.remote_fetch_policy import remote_fetch_policy_scope
if operation=='cancel':
 event.set()
if operation=='local_only':
 set_operator_policy_values({'system.local_only_mode':True})
context={'allow_remote_fetch':False} if operation=='turn_veto' else {}
with remote_fetch_policy_scope(context):
 try:
  ReplayCheckedTransport(adapter._http_transport(),binding).post_envelope(
   plan.envelope,origin=plan.origin,token=None,read_timeout_seconds=5)
 except UsePodTransportError as exc:
  result={'code':exc.code,'dispatch_state':exc.dispatch_state}
 else:
  result={'code':'SENT'}
print(json.dumps(result),flush=True)
'''


@pytest.mark.parametrize('operation', ['route', 'rules', 'local_only', 'turn_veto', 'forget', 'delete', 'cancel'])
def test_served_privacy_change_refuses_the_identical_reviewed_usepod_envelope(tmp_path, operation):
    model = 'recovery-privacy-scripted'
    outside = StrictUsePodService(models={model: [Listing('marketplace', 'recovery-market', 420000, 1260000)]})
    process = None
    with _provider_context(outside), recording_daemon(tmp_path) as (daemon, _, _):
        _json(daemon.base_url+'/api/cloud/usepod/lane',
              {'protocol': 'openai', 'transport_mode': 'x402', 'origin': outside.origin})
        _json(daemon.base_url+'/api/cloud/usepod/route-policy', {'mode': 'marketplace-only'})
        _json(daemon.base_url+'/api/cloud/usepod/approve-route', {'model_id': model})
        folder = tmp_path/'project'
        folder.mkdir()
        project = _json(daemon.base_url+'/api/projects', {'name': 'Recovery', 'root': str(folder)})
        project_id = project.get('project', project)['id']
        chat = rig.canonical_session('disclosure-recovery-'+operation)
        _json(daemon.base_url+'/api/chat/session', {'session_id': chat, 'project_id': project_id})
        seed = daemon.chat('Remember Cobalt Workshop repairs clocks.', session_id=chat, turn_id='recovery-seed')
        assert 'remember' in seed.get('message', {}).get('content', '').lower(), seed
        try:
            process = subprocess.Popen([sys.executable, '-c', _SCRIPT, model, chat, project_id, operation],
                env=daemon.env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            _resource('recovery-probe', process.pid, None, 'running')
            ready, _, _ = select.select([process.stdout], [], [], 30)
            assert ready, 'The recovery privacy probe did not become ready.'
            line = process.stdout.readline()
            assert line, process.stderr.read()
            assert json.loads(line) == {'ready': True, 'missing_refused': True}
            if operation == 'route':
                _json(daemon.base_url+'/api/cloud/usepod/forget-route', {'model_id': model})
            elif operation == 'rules':
                proposal = _json(daemon.base_url+'/api/disclosure/rules/propose', {
                    'project_id': project_id, 'rules': [{'kind': 'attachment', 'source': '*', 'mode': 'local'}]})
                _json(daemon.base_url+'/api/disclosure/rules/confirm', {'id': proposal['id'], 'sha256': proposal['sha256']})
            elif operation == 'forget':
                erased = daemon.chat('Forget Cobalt Workshop.', session_id=chat, turn_id='recovery-forget')
                assert 'please retry the forget' not in erased.get('message', {}).get('content', '').lower(), erased
            elif operation == 'delete':
                erased = _json(daemon.base_url+'/api/chat/session', {'session_id': chat, 'delete': True})
                assert erased.get('lifecycle_state') == 'deleted', erased
            output, errors = process.communicate('\n', timeout=20)
            assert process.returncode == 0, errors
            assert json.loads(output) == {'code': 'disclosure_replay_refused', 'dispatch_state': 'not_sent'}
            assert not outside.requests_to('/proxy/x402/v1/chat/completions')
            assert not outside.requests_to('/proxy/x402/v1/messages')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                _resource('recovery-probe', process.pid, None, 'stopped')
