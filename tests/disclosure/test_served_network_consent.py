from __future__ import annotations

import json
import select
import subprocess
import sys

from tests.disclosure.test_served_preview import _json, _provider_context, _resource
from tests.disclosure.test_served_rules import recording_daemon
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService


def test_served_route_revocation_invalidates_prepared_network_consent_without_a_model_call(tmp_path):
    model = 'network-preview-scripted'
    outside = StrictUsePodService(models={model: [Listing('marketplace', 'network-market', 420000, 1260000)]})
    process = None
    with _provider_context(outside), recording_daemon(tmp_path) as (daemon, _, _):
        _json(daemon.base_url+'/api/cloud/usepod/lane',
              {'protocol': 'openai', 'transport_mode': 'x402', 'origin': outside.origin})
        _json(daemon.base_url+'/api/cloud/usepod/route-policy', {'mode': 'marketplace-only'})
        approved = _json(daemon.base_url+'/api/cloud/usepod/approve-route', {'model_id': model})
        assert approved['approved_route']['model_id'] == model
        script = '''
import json, sys
from adapters.base_adapter import ModelRequest
from core.disclosure.wire import DisclosureRefused
from core.model_registry import ModelRegistry
from core.runtime_provider_defaults import register_chat_cloud_model
model = sys.argv[1]
register_chat_cloud_model('usepod', model)
registry = ModelRegistry()
manifest = registry.get_manifest('usepod-byok', model)
adapter = registry.build_adapter(manifest)
request = ModelRequest('chat', 'Synthetic specification.', max_output_tokens=64,
                       metadata={'privacy_class':'public'})
plan = adapter._prepare(request, force_json=False, stream=False)
wire = plan.disclosure_wire
consent = wire.network_consent
from core.policy_engine import set_operator_policy_values
from core.remote_fetch_policy import remote_fetch_policy_scope
set_operator_policy_values({'system.local_only_mode': True})
try:
    wire.consume_wire(wire.destination)
except DisclosureRefused:
    local_refused = True
else:
    local_refused = False
set_operator_policy_values({'system.local_only_mode': False})
with remote_fetch_policy_scope({'allow_remote_fetch': False}):
    try:
        wire.consume_wire(wire.destination)
    except DisclosureRefused:
        turn_refused = True
    else:
        turn_refused = False
print(json.dumps({'allowed':consent.allowed(wire.destination,wire),
                  'other_origin':consent.allowed('https://other.invalid/chat',wire),
                  'other_model':consent.allowed(wire.destination,{**wire,'model':'other'}),
                  'local_only_refused':local_refused,'turn_veto_refused':turn_refused}),flush=True)
sys.stdin.readline()
try:
    wire.consume_wire(wire.destination)
except DisclosureRefused:
    print(json.dumps({'refused':True}),flush=True)
else:
    print(json.dumps({'refused':False}),flush=True)
'''
        try:
            process = subprocess.Popen([sys.executable, '-c', script, model], env=daemon.env(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            _resource('probe-client', process.pid, None, 'running')
            ready, _, _ = select.select([process.stdout], [], [], 30)
            assert ready, 'The prepared route probe did not become ready.'
            line = process.stdout.readline()
            assert line, process.stderr.read()
            assert json.loads(line) == {'allowed': True, 'other_origin': False, 'other_model': False, 'local_only_refused': True, 'turn_veto_refused': True}
            _json(daemon.base_url+'/api/cloud/usepod/forget-route', {'model_id': model})
            output, errors = process.communicate('\n', timeout=20)
            assert process.returncode == 0, errors
            assert json.loads(output) == {'refused': True}
            assert not outside.requests_to('/proxy/x402/v1/chat/completions')
            assert not outside.requests_to('/proxy/x402/v1/messages')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                _resource('probe-client', process.pid, None, 'stopped')
