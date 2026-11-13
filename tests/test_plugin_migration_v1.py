"""Upgrade fixtures use the current v0.7 policy, chat and encrypted credential schemas."""
from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path

import pytest

from core import cloud_escalation_policy as policy
from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core import plugin_migration as migration
from core.credential_store import get_credential, store_credential
from tests.test_plugin_manager_v1 import installed, package
from tests.test_plugin_services_v1 import https_service

RETIRED = Path(__file__).resolve().parent / 'fixtures/retired_first_party_plugins'


def reference(package, service):
    """The exact old first-party package bytes core still knows for this retired service.

    VOOL ships no UsePod or Eyebrow plugin any more (sls, Nov 12 and 13). Core keeps their
    digests for the retained UsePod lane and the saved Eyebrow key, so these tests install a
    test-only copy of those bytes.
    """
    shutil.rmtree(package)
    shutil.copytree(RETIRED / ('vool-plugin-' + service), package)


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
    # Owner decision 2026-11-12: both services are retired. Said once, calmly; nothing to install.
    said = [n['message'] for n in notices]
    assert 'UsePod is no longer part of VOOL. Your saved UsePod key stays on this computer and VOOL does not use it.' in said
    assert 'Eyebrow is no longer part of VOOL. Your saved Eyebrow key stays on this computer and VOOL does not use it.' in said
    assert not any('Install' in message or 'plugin' in message.lower() for message in said)
    assert all(n.get('dismissible') for n in notices)
    assert lifecycle.records() == []
    assert get_credential('llm.cloud.usepod') == key
    assert get_credential('security.eyebrow') == key
    raw = policy._read_raw()
    # Retired pins are cleared, never moved onto another paid key: the next paid choice
    # goes through the model picker and its spending confirmation.
    assert raw['policy']['provider'] == '' and raw['policy']['model'] == ''
    assert raw['policy']['mode'] == 'off'
    assert raw['chat_models']['openclaw:'+'a'*20] == {'model':'','provider':'','revision':3}
    assert raw['chat_models']['openclaw:'+'b'*20] == original['chat_models']['openclaw:'+'b'*20]
    assert raw['usage'] == original['usage']
    assert raw['plugin_migration']['previous_policy'] == original['policy']
    assert raw['plugin_migration']['previous_chats']['openclaw:'+'a'*20] == original['chat_models']['openclaw:'+'a'*20]
    assert not migration.migrate_selections()
    assert not https_service['log'].exists()


def test_cleared_model_notice_stays_until_the_owner_dismisses_it(package, https_service):
    upgrade_profile(secrets.token_urlsafe(32))
    assert any(n['message_key'] == 'plugin_migration_model_fallback' for n in migration.notices())
    assert any(n['message_key'] == 'plugin_migration_model_fallback' for n in migration.notices())
    assert manager.handle('plugin_notice_dismiss', {'action': 'plugin_notice_dismiss'}) == {'ok': True, 'dismissed': True}
    assert not any(n['message_key'] == 'plugin_migration_model_fallback' for n in migration.notices())
    assert not migration.migrate_selections()  # a dismissed notice never re-runs the migration
    assert not any(n['message_key'] == 'plugin_migration_model_fallback' for n in migration.notices())


def test_unselected_paid_key_is_never_a_migration_fallback(package, https_service):
    store_credential('llm.cloud.openrouter', secrets.token_urlsafe(32))
    assert policy._write_raw({'policy':{'provider':'usepod','model':'old','mode':'auto'}})
    assert migration.migrate_selections()
    assert policy.load_policy().provider == '' and policy.load_policy().model == ''
    assert policy.load_policy().mode == 'off'
    assert not https_service['log'].exists()


def test_only_accepting_reference_enable_reuses_saved_scanner_key_without_online_calls(package, https_service):
    key = https_service['access']
    store_credential('security.eyebrow', key)
    reference(package, 'eyebrow')
    info = installed(package)
    own = 'plugin.vool-plugin-eyebrow.access'
    assert not get_credential(own)
    manager.enable(info['id'], accepted=False, approval_digest=info['approval_digest'])
    assert not get_credential(own) and get_credential('security.eyebrow') == key
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    assert get_credential(own) == key and get_credential('security.eyebrow') == key
    assert not https_service['log'].exists()
    assert not any(n['plugin_id'] == info['id'] for n in migration.notices())


def test_a_saved_usepod_key_is_never_copied_into_the_old_usepod_package(package, https_service):
    key = https_service['access']
    store_credential('llm.cloud.usepod', key)
    reference(package, 'usepod')
    info = installed(package)
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    # UsePod left VOOL in 0.8. Its saved token stays in its own slot and nothing uses it: switching
    # the old package on copies nothing, opens no payment lane and lists no UsePod model.
    assert not get_credential('plugin.vool-plugin-usepod.access')
    assert get_credential('llm.cloud.usepod') == key
    from core.model_registry import ModelRegistry

    assert not [m for m in ModelRegistry().list_manifests(enabled_only=False) if m.adapter_type == 'usepod']
    assert not any(n['plugin_id'] == info['id'] for n in migration.notices())
    assert not https_service['log'].exists()


@pytest.mark.parametrize('service,slot', [('usepod', 'llm.cloud.usepod'), ('eyebrow', 'security.eyebrow')])
def test_a_package_that_only_claims_a_first_party_name_is_refused(package, https_service, service, slot):
    store_credential(slot, https_service['access'])
    reference(package, service)
    path = package / '.codex-plugin/plugin.json'
    raw = json.loads(path.read_text())
    raw['vool']['network'] = ['api.example.org']
    block = raw['vool']['model_provider' if service == 'usepod' else 'scanner']
    block['base_url'] = 'https://api.example.org'
    if 'pricing_source' in block:
        block['pricing_source'] = 'https://api.example.org/v1/marketplace/models'
    path.write_text(json.dumps(raw))
    # The names stay reserved for VOOL's own package bytes, so a look-alike never reaches a
    # saved key.
    with pytest.raises(lifecycle.LifecycleError, match='belongs to a plugin made by VOOL'):
        manager.prepare_local(str(package))
    assert not lifecycle.record_for('vool-plugin-' + service)
    assert not get_credential(f'plugin.vool-plugin-{service}.access')
    assert get_credential(slot) == https_service['access']
    assert not https_service['log'].exists()


def test_first_party_package_digests_match_the_retired_package_copies():
    from core.plugin_package import capture_package

    for service in migration.SERVICES:
        assert capture_package(RETIRED / service['id']).digest in service['digests'], service['id']


def test_vool_ships_no_plugin_for_a_retired_service():
    """No example package carries a retired service's id or reaches its service."""
    from urllib.parse import urlsplit

    examples = Path(__file__).resolve().parents[1] / 'examples/plugins'
    retired = [service for service in migration.SERVICES if service.get('retired')]
    assert {service['id'] for service in retired} == {'vool-plugin-usepod', 'vool-plugin-eyebrow'}
    ids = {service['id'] for service in retired}
    hosts = {urlsplit(service['origin']).hostname for service in retired}
    manifests = sorted(examples.glob('*/.codex-plugin/plugin.json')) + sorted(examples.glob('*/.claude-plugin/plugin.json'))
    assert manifests
    for path in manifests:
        raw = json.loads(path.read_text())
        block = raw.get('vool') or {}
        assert raw.get('name') not in ids and block.get('id') not in ids, path
        assert not hosts & set(block.get('network') or []), path
    assert not any((examples / identity).exists() for identity in ids)


def path_token_provider(package):
    """A neutral provider that carries its key in the request path, not a wallet-funded one."""
    from tests.test_plugin_manifest_v1 import path_token_manifest

    (package / '.codex-plugin/plugin.json').write_text(json.dumps(path_token_manifest()))


def test_the_retired_usepod_package_never_becomes_a_provider_lane(package, https_service, monkeypatch):
    """UsePod left VOOL in 0.8. Its old package paid from a wallet-funded balance, so even with a key of the
    person's own it lists nothing, is never usable and reaches nothing."""
    from core import plugin_providers, plugin_services
    from core.plugin_services import ServiceRefused

    reference(package, 'usepod')
    info = installed(package)
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    manager.connect(info['id'], 'access', https_service['access'])
    native = plugin_services.native_request
    def routed(*args, **kwargs):
        return native(*args, **kwargs, _connector=https_service['connector'], _tls_context=https_service['context'])
    monkeypatch.setattr(plugin_services, 'native_request', routed)
    monkeypatch.setattr(plugin_providers, 'native_request', routed)
    with pytest.raises(ServiceRefused, match='no longer part of VOOL. VOOL does not use this plugin'):
        plugin_providers.refresh_models(info['id'])
    assert not plugin_providers.usable_provider('plugin-vool-plugin-usepod')
    assert not https_service['log'].exists()


def test_path_token_provider_lists_and_streams_through_existing_pin_path(package, https_service, monkeypatch):
    from adapters.base_adapter import ModelRequest
    from core import plugin_providers, plugin_services
    from core.cloud_model_control import set_cloud_model
    from core.model_registry import ModelRegistry
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    path_token_provider(package)
    info = installed(package)
    manager.enable(info['id'], accepted=True, approval_digest=info['approval_digest'])
    manager.connect(info['id'], 'access', https_service['access'])
    native = plugin_services.native_request
    def routed(*args, **kwargs):
        return native(*args, **kwargs, _connector=https_service['connector'], _tls_context=https_service['context'])
    monkeypatch.setattr(plugin_services, 'native_request', routed)
    monkeypatch.setattr(plugin_providers, 'native_request', routed)
    plugin_providers.refresh_models(info['id'])
    ok, _message, _chosen = set_cloud_model('fixture-model', provider='plugin-sample-path-provider', owner_local=True, confirm_paid=True)
    assert ok, _message
    assert policy.load_policy().provider == 'plugin-sample-path-provider'
    response = dispatch_get(path='/api/cloud/models',query={'provider':['plugin-sample-path-provider']},runtime=RuntimeServices(display_name='Plugin fixture'),model_name='fixture',client_host='127.0.0.1')
    assert response.status == 200 and json.loads(response.body)['models'][0]['id'] == 'fixture-model'
    registry = ModelRegistry()
    model = registry.get_manifest('plugin-sample-path-provider', 'fixture-model')
    prompt = secrets.token_hex(12)
    chunks = list(registry.build_adapter(model).stream_text_task(ModelRequest(task_kind='chat',prompt=prompt,messages=[{'role':'user','content':prompt}])))
    assert ''.join(c.delta_text for c in chunks) == prompt[::-1]
    records = [json.loads(line) for line in https_service['log'].read_text().splitlines()]
    assert records[0]['path_auth'] and not records[0]['authorization_header']
    assert any(r['path'] == '/v1/marketplace/models' and not r['authorized'] for r in records)
    assert https_service['access'] not in json.dumps(manager.inventory()) + https_service['log'].read_text()


def test_got_it_closes_the_retired_service_notices_and_keeps_every_key(package, https_service):
    key = secrets.token_urlsafe(32)
    upgrade_profile(key)
    assert len(migration.notices()) == 3            # two retired services and the cleared model choice
    assert migration.dismiss_selection_notice()
    assert migration.notices() == []
    assert not migration.dismiss_selection_notice()
    assert get_credential('llm.cloud.usepod') == key and get_credential('security.eyebrow') == key
    assert not https_service['log'].exists()


def test_a_cleared_model_choice_is_said_on_the_chat_page_until_closed(package, https_service):
    from core import setup_progress

    assert setup_progress._retired_model_notice() is False
    upgrade_profile(secrets.token_urlsafe(32))
    assert setup_progress._retired_model_notice() is True
    migration.dismiss_selection_notice()
    assert setup_progress._retired_model_notice() is False
