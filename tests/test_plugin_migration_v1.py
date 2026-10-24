"""Upgrade fixtures use the current v0.7 policy, chat and encrypted credential schemas."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from core import cloud_escalation_policy as policy
from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core import plugin_migration as migration
from core.credential_store import get_credential, store_credential
from tests.test_plugin_manager_v1 import installed, package
from tests.test_plugin_services_v1 import https_service


def reference(package, service):
    path = Path(__file__).resolve().parents[1] / 'examples/plugins' / ('vool-plugin-' + service) / '.codex-plugin/plugin.json'
    (package / '.codex-plugin/plugin.json').write_bytes(path.read_bytes())


def upgrade_profile(key):
    store_credential('llm.cloud.usepod', key, label='UsePod')
    store_credential('llm.cloud.usepod_origin', 'https://api.usepod.ai')
    store_credential('security.eyebrow', key, label='Eyebrow')
    store_credential('llm.cloud.openai', secrets.token_urlsafe(32))
    raw = {'policy': {'mode':'auto','model':'old-model','provider':'usepod','daily_cap':25,'free_cloud_enabled':False},
           'chat_models': {'openclaw:' + 'a'*20: {'model':'old-model','provider':'usepod','revision':2},
                           'openclaw:' + 'b'*20: {'model':'chosen-model','provider':'openai','revision':4}},
           'usage': {'date':'2026-10-23','count':3}}
    assert policy._write_raw(raw)
    return raw


def test_fresh_profile_stays_empty_without_notices_or_implicit_provider(package, https_service):
    assert migration.notices() == []
    assert lifecycle.records() == []
    assert not policy._store_path().exists()
    assert not https_service['log'].exists()


def test_upgrade_notice_and_fallback_preserve_original_keys_and_explicit_choices(package, https_service):
    key = secrets.token_urlsafe(32)
    original = upgrade_profile(key)
    notices = migration.notices()
    assert any('UsePod now comes as a plugin' in n['message'] for n in notices)
    assert any('Eyebrow now comes as a plugin' in n['message'] for n in notices)
    assert lifecycle.records() == []
    assert get_credential('llm.cloud.usepod') == key
    assert get_credential('security.eyebrow') == key
    raw = policy._read_raw()
    assert raw['policy']['provider'] == 'openai' and raw['policy']['model'] == 'chosen-model'
    assert raw['policy']['mode'] == 'off'
    assert raw['chat_models']['openclaw:'+'a'*20] == {'model':'chosen-model','provider':'openai','revision':3}
    assert raw['usage'] == original['usage']
    assert raw['plugin_migration']['previous_policy'] == original['policy']
    assert not migration.migrate_selections()
    assert not https_service['log'].exists()


def test_unselected_paid_key_is_never_a_migration_fallback(package, https_service):
    store_credential('llm.cloud.openrouter', secrets.token_urlsafe(32))
    assert policy._write_raw({'policy':{'provider':'usepod','model':'old','mode':'auto'}})
    assert migration.migrate_selections()
    assert policy.load_policy().provider == '' and policy.load_policy().model == ''
    assert policy.load_policy().mode == 'off'
    assert not https_service['log'].exists()


@pytest.mark.parametrize('service,old_slot', [('usepod','llm.cloud.usepod'),('eyebrow','security.eyebrow')])
def test_only_accepting_reference_enable_reuses_saved_key_without_online_calls(package, https_service, service, old_slot):
    key = https_service['access']
    store_credential(old_slot, key)
    reference(package, service)
    info = installed(package)
    own = 'plugin.vool-plugin-' + service + '.access'
    assert not get_credential(own)
    manager.enable(info['id'], accepted=False, approval_digest=info['approval_digest'])
    assert not get_credential(own) and get_credential(old_slot) == key
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    assert get_credential(own) == key and get_credential(old_slot) == key
    assert not https_service['log'].exists()
    assert not any(n['plugin_id'] == info['id'] for n in migration.notices())


def test_changed_destination_cannot_rebind_retired_service_key(package, https_service):
    store_credential('llm.cloud.usepod', https_service['access'])
    reference(package, 'usepod')
    path = package / '.codex-plugin/plugin.json'
    raw = json.loads(path.read_text())
    raw['vool']['network'] = ['api.example.org']
    raw['vool']['model_provider'].update(base_url='https://api.example.org', pricing_source='models')
    path.write_text(json.dumps(raw))
    info = installed(package)
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    assert not get_credential('plugin.vool-plugin-usepod.access')
    assert get_credential('llm.cloud.usepod') == https_service['access']
    assert not https_service['log'].exists()


def test_reference_usepod_reuses_key_lists_and_streams_through_existing_pin_path(package, https_service, monkeypatch):
    from adapters.base_adapter import ModelRequest
    from core import plugin_providers, plugin_services
    from core.cloud_model_control import set_cloud_model
    from core.model_registry import ModelRegistry
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    reference(package, 'usepod')
    store_credential('llm.cloud.usepod', https_service['access'])
    info = installed(package)
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    native = plugin_services.native_request
    def routed(*args, **kwargs):
        return native(*args, **kwargs, _connector=https_service['connector'], _tls_context=https_service['context'])
    monkeypatch.setattr(plugin_services, 'native_request', routed)
    monkeypatch.setattr(plugin_providers, 'native_request', routed)
    plugin_providers.refresh_models(info['id'])
    ok, _message, _chosen = set_cloud_model('fixture-model', provider='plugin-vool-plugin-usepod', owner_local=True, confirm_paid=True)
    assert ok, _message
    assert policy.load_policy().provider == 'plugin-vool-plugin-usepod'
    response = dispatch_get(path='/api/cloud/models',query={'provider':['plugin-vool-plugin-usepod']},runtime=RuntimeServices(display_name='Plugin fixture'),model_name='fixture',client_host='127.0.0.1')
    assert response.status == 200 and json.loads(response.body)['models'][0]['id'] == 'fixture-model'
    registry = ModelRegistry()
    model = registry.get_manifest('plugin-vool-plugin-usepod', 'fixture-model')
    prompt = secrets.token_hex(12)
    chunks = list(registry.build_adapter(model).stream_text_task(ModelRequest(task_kind='chat',prompt=prompt,messages=[{'role':'user','content':prompt}])))
    assert ''.join(c.delta_text for c in chunks) == prompt[::-1]
    records = [json.loads(line) for line in https_service['log'].read_text().splitlines()]
    assert records[0]['path_auth'] and not records[0]['authorization_header']
    assert any(r['path'] == '/v1/marketplace/models' and not r['authorized'] for r in records)
    assert https_service['access'] not in json.dumps(manager.inventory()) + https_service['log'].read_text()
