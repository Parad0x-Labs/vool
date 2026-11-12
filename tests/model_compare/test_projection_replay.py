from __future__ import annotations

import time
from dataclasses import asdict

import pytest

from adapters.base_adapter import ModelRequest
from core.disclosure.sources import SourceCatalog
from core.disclosure.wire import close_chat, decide, previews
from core.egress_gate import EgressSegment
from core.model_compare import service
from core.model_registry import ModelRegistry
from core.remote_fetch_policy import remote_fetch_policy_scope
from storage.model_provider_manifest import ModelProviderManifest
from tests import _reader_served_rig as rig
from tests.disclosure.test_served_preview import _provider_context
from tests.model_compare.test_served_compare import streaming_provider


@pytest.mark.parametrize('privacy,exposure,native',[
    (None,'LOCAL_ONLY',False),(None,'SECRET',False),(None,'UNCLASSIFIED',False),
    (None,'LOCAL_ONLY',True),('unknown','PUBLIC',False),('wallet','PUBLIC',False),
    ('secrets','PUBLIC',False),('null','PUBLIC',False),('private-files','PUBLIC',False)])
def test_local_prepared_segments_cannot_be_reintroduced_into_a_cloud_comparison(tmp_path, monkeypatch, privacy, exposure, native):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.revocation import initialize_memory_home
    from core.policy_engine import set_operator_policy_values
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    home = tmp_path/'home'
    monkeypatch.setenv('VOOL_HOME',str(home))
    monkeypatch.setenv('VOOL_LOCAL_MODELS_ENABLED','1')
    configure_runtime_home(home)
    configure_default_db_path(home/'data/test.db')
    initialize_memory_home()
    chat = 'projection-replay'
    ensure_chat_namespace(chat,project_id='',grant_confirmed_profile=True)
    set_operator_policy_values({'system.local_only_mode':False,'network.outbound_enabled':True})
    local = rig.CapturingProvider(default='The workshop opens at noon.')
    original_get = local._server.RequestHandlerClass.do_GET
    def inventory(connection):
        if connection.path.startswith('/api/ps'):
            # A scripted external endpoint already serving requests: no weights
            # will be loaded, and the real governor must see that residency.
            return connection._send({'models':[{'name':'prepared-local','size':0,'size_vram':0}]})
        return original_get(connection)
    local._server.RequestHandlerClass.do_GET = inventory
    cloud = streaming_provider()
    job_id = None
    try:
        with _provider_context(local),_provider_context(cloud):
            manifest = ModelProviderManifest(provider_name='prepared-local',model_name='prepared-local',source_type='http',
                adapter_type='openai_compatible',license_name='scripted',license_reference='fixture',
                weight_location='external',weights_bundled=False,runtime_dependency='http',capabilities=['summarize'],
                runtime_config={'base_url':local.base_url,'api_path':'/v1/chat/completions','timeout_seconds':3},
                metadata={'input_modalities':['text'],'cost_class':'free_local'},enabled=True)
            if native:
                manifest.metadata['runtime_family']='ollama'
                manifest.runtime_config.pop('api_path')
            target = manifest.model_copy(deep=True,update={'provider_name':'prepared-cloud','model_name':'prepared-cloud'})
            target.runtime_config.update(base_url=cloud.base_url,api_path='/v1/chat/completions')
            target.metadata['runtime_family']='openai-compatible'
            target.metadata.update(deployment_class='cloud',cost_class='paid_cloud')
            ModelRegistry().register_manifest(manifest)
            ModelRegistry().register_manifest(target)
            catalog = SourceCatalog()
            catalog.add('What are the workshop hours?','conversation',chat)
            request = ModelRequest('summarize','',max_output_tokens=100,messages=[
                {'role':'user','content':'What are the workshop hours?'},
                {'role':'system','content':'PRIVATE_LOCAL_MARKER_7198',
                 '_egress_segment':asdict(EgressSegment('local-context','memory',chat,exposure))}],
                context={'chat_id':chat},metadata={'privacy_class':None if privacy=='null' else privacy} if privacy else {})
            with remote_fetch_policy_scope({'chat_id':chat,'project_id':'','request_id':'local-source',
                                           '_disclosure_sources':catalog}):
                response=ModelRegistry().build_adapter(manifest).run_text_task(request)
            assert response.output_text
            assert any('PRIVATE_LOCAL_MARKER_7198' in c['text'] for c in local.calls)
            source=[p for p in previews(chat_id=chat) if p.get('can_compare')][-1]
            available=service.models_for(source['id'])
            assert target.provider_id in {m['id'] for m in available if m['available']}, [(m['id'],m['reason']) for m in available]
            job=service.start(source['id'],source['sha256'],[target.provider_id])
            job_id=job['id']
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                waiting=[p for p in previews(chat_id=chat) if p['state']=='waiting']
                if waiting or service.read(job_id)['state']!='running':
                    break
                time.sleep(.02)
            if privacy in {'unknown','wallet','secrets','null'} or exposure=='UNCLASSIFIED':
                result=service.read(job_id)
                assert not waiting and not cloud.raw and result['results'][0]['state']=='refused',result
                return
            assert waiting, service.read(job_id)
            assert ('PRIVATE_LOCAL_MARKER_7198' in waiting[0]['body']) is (privacy=='private-files')
            assert 'What are the workshop hours?' in waiting[0]['body']
            decide(waiting[0]['id'],waiting[0]['sha256'],'send')
            while time.monotonic()<deadline and service.read(job_id)['state']=='running':
                time.sleep(.02)
            result=service.read(job_id)
            assert result['state']=='done' and result['results'][0]['state']=='completed',result
            assert cloud.raw
            assert all((b'PRIVATE_LOCAL_MARKER_7198' in b) is (privacy=='private-files') for b in cloud.raw)
            assert result['results'][0]['same_input'] is (privacy=='private-files')
    finally:
        if job_id is not None:
            try:
                service.cancel(job_id)
                deadline=time.monotonic()+5
                while service.read(job_id)['state']=='running' and time.monotonic()<deadline:
                    time.sleep(.02)
            except (KeyError,ValueError):
                pass
        close_chat(chat)
        configure_default_db_path(None)
        configure_runtime_home(None)
