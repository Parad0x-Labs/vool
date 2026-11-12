"""Imported context remains on this device and withdrawn metadata cannot return."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from core import auto_local_only_mode as mode
from storage.model_provider_manifest import ModelProviderManifest
from tests.test_chat_import_served import EXPORT, _call, _export_zip, _upload_and_preview
from tests.test_chat_import_served import app as import_app
from tests.test_chat_import_working_state import _move, _review, _workspace
from tests.test_chat_import_working_state import app as app


def _manifest(url):
    return ModelProviderManifest(provider_name='ollama-local',model_name='privacy-local',
        source_type='http',adapter_type='local_qwen_provider',license_name='Apache-2.0',
        license_reference='https://example.org/license',weight_location='external',
        runtime_dependency='ollama',capabilities=['summarize'],runtime_config={'base_url':url},
        metadata={'deployment_class':'local','cost_class':'free_local'})


@pytest.mark.parametrize('url',['http://192.168.5.2:11434','http://10.20.1.4:8080',
                                'http://100.80.0.9:11434','https://example.org/model'])
def test_imported_context_blocks_other_devices_at_manifest_and_endpoint_seams(url):
    context={'local_only':True,'_device_only':True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        for check in (lambda:mode.assert_manifest_allowed(_manifest(url),source_context=dict(context)),
                      lambda:mode.assert_endpoint_allowed(url,source_context=dict(context))):
            with pytest.raises(mode.CloudEgressBlockedError):
                pool.submit(check).result()


@pytest.mark.parametrize('url',['http://127.0.0.1:11434','http://[::1]:11434','http://localhost:8080'])
def test_device_context_preserves_configured_loopback_inference(url):
    context={'local_only':True,'_device_only':True}
    mode.assert_manifest_allowed(_manifest(url),source_context=context)
    mode.assert_endpoint_allowed(url,source_context=context)


def test_ordinary_local_only_still_permits_the_owners_private_model():
    context={'local_only':True}
    url='http://192.168.5.2:11434'
    mode.assert_manifest_allowed(_manifest(url),source_context=context)
    mode.assert_endpoint_allowed(url,source_context=context)


def test_forgotten_project_metadata_cannot_be_recreated_by_native_edit(app,tmp_path):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.project_store import create_project, projects_path

    root,_secret=_workspace(tmp_path)
    preview=_upload_and_preview(app,_export_zip(EXPORT))
    reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,instructions=True)
    sid=run['first_session_id']
    assert create_project('copperfable',str(root))[0]
    handled,reply=maybe_handle_memory_command('Forget copperfable.',session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled and 'no erasure step' not in reply,reply
    assert 'copperfable' not in projects_path().read_text()
    ok,result=create_project('copperfable',str(root))
    assert not ok,result
    assert 'copperfable' not in projects_path().read_text()
    assert 'copperfable' not in projects_path().with_name('projects.json.bak').read_text()


@pytest.mark.parametrize('resident',[False,True])
def test_forget_project_field_preserves_folder_and_erases_shared_metadata(app,tmp_path,resident):
    from core.context_namespace import load_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command
    from core.project_store import create_project, get_project, projects_path

    root,_secret=_workspace(tmp_path)
    if resident:
        assert create_project('Existing native project',str(root))[0]
    preview=_upload_and_preview(app,_export_zip(EXPORT))
    reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,instructions=True)
    sid=run['first_session_id'];pid=load_chat_namespace(sid).project_id
    assert create_project('silverfern',str(root))[0]
    handled,reply=maybe_handle_memory_command('Forget silverfern.',session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled and 'no erasure step' not in reply,reply
    assert 'silverfern' not in projects_path().read_text()
    assert 'silverfern' not in projects_path().with_name('projects.json.bak').read_text()
    assert get_project(pid)['root']==str(root)
    assert (root/'plan.txt').is_file()
    assert not create_project('silverfern',str(root))[0]


@pytest.mark.parametrize("context",["untrusted",42,object()])
def test_non_mapping_context_preserves_the_existing_policy_contract(context):
    assert mode.turn_is_local_only(context)==mode.turn_is_local_only(None)


@pytest.mark.parametrize('source_first',[False,True])
def test_queued_turn_refuses_a_changed_project_binding_before_running(app,tmp_path,source_first):
    import time

    from core.context_namespace import ensure_chat_namespace, set_chat_namespace_project
    from core.project_store import create_project
    from core.session_turn_gate import _GATES, _REGISTRY_LOCK, hold_session_turn_gate
    from core.web.api.runtime import RuntimeServices, run_agent

    folders=[tmp_path/'before',tmp_path/'after']
    for folder in folders:folder.mkdir()
    projects=[create_project(folder.name,str(folder))[1]['id'] for folder in folders]
    root,_secret=_workspace(tmp_path)
    preview=_upload_and_preview(app,_export_zip(EXPORT));reviewed=_review(app,preview,root)
    run=_move(app,preview,reviewed,instructions=True)
    from core.context_namespace import load_chat_namespace
    projects[1]=load_chat_namespace(run['first_session_id']).project_id
    if source_first:projects.reverse()
    sid='openclaw:binding-race'
    ensure_chat_namespace(sid,project_id=projects[0])
    # Actual gate and namespace, no patched runner or model. This tests refusal
    # before dispatch; separate live acceptance proves the configured model.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with hold_session_turn_gate(sid):
            with _REGISTRY_LOCK:
                initial_ticket = _GATES[sid].next_ticket
            pending=pool.submit(run_agent,RuntimeServices(display_name='VOOL'),'Continue.',session_id=sid,source_context={})
            deadline=time.monotonic()+5
            queued=False
            while time.monotonic()<deadline:
                with _REGISTRY_LOCK:
                    queued=_GATES[sid].next_ticket>initial_ticket
                if queued:break
                time.sleep(.01)
            assert queued,'runtime request never reached the session gate'
            set_chat_namespace_project(sid,projects[1])
        with pytest.raises(RuntimeError,match='Send your message again'):
            pending.result(timeout=10)


def test_project_grant_inherits_the_imported_context_device_ceiling(app,tmp_path):
    from core.context_namespace import ensure_chat_namespace, grant_context_import, load_chat_namespace
    from core.project_store import create_project
    from core.web.api.runtime import RuntimeServices, run_agent

    root,_secret=_workspace(tmp_path)
    preview=_upload_and_preview(app,_export_zip(EXPORT));reviewed=_review(app,preview,root)
    imported=_move(app,preview,reviewed,instructions=True,memories=True)
    source_project=load_chat_namespace(imported['first_session_id']).project_id
    native=tmp_path/'native-reader';native.mkdir()
    ok,project=create_project('Native reader',str(native));assert ok
    sid='openclaw:project-grant-reader'
    ensure_chat_namespace(sid,project_id=project['id'])
    grant_context_import(sid,scope='project',source_id='project:'+source_project,source_project_id=source_project)
    context={}
    run_agent(RuntimeServices(display_name='VOOL'),'Continue.',session_id=sid,source_context=context)
    assert context['_device_only'] and context['local_only'] and context['allow_remote_fetch'] is False


def test_cross_chat_source_lineage_has_a_device_ceiling_without_changing_native_grants(app):
    from core.context_namespace import ensure_chat_namespace, grant_context_import
    from core.web.api.runtime import RuntimeServices, run_agent
    from tests.test_chat_import_migration import _finish

    preview=_upload_and_preview(app,_export_zip(EXPORT))
    imported=_finish(app,preview)
    source_sid=imported['first_session_id']
    for source,native in ((source_sid,False),('openclaw:native-donor',True)):
        if native:ensure_chat_namespace(source)
        sid='openclaw:reader-'+str(native)
        ensure_chat_namespace(sid)
        grant_context_import(sid,scope='chat',source_id='chat:'+source)
        context={}
        run_agent(RuntimeServices(display_name='VOOL'),'Continue.',session_id=sid,source_context=context)
        assert bool(context.get('_device_only')) is (not native)


def test_pinned_context_policy_refuses_rebinding_and_resets_after_the_turn(app):
    from core.context_namespace import ensure_chat_namespace, set_chat_namespace_project
    from core.context_scope import ContextAccessPolicy, ContextBindingChangedError, pinned_context_access_policy

    sid='openclaw:pinned-reader'
    ensure_chat_namespace(sid,project_id='project-before')
    policy=ContextAccessPolicy.for_request(session_id=sid)
    with pinned_context_access_policy(policy):
        assert ContextAccessPolicy.for_request(session_id=sid) is policy
        set_chat_namespace_project(sid,'project-after')
        with pytest.raises(ContextBindingChangedError):
            ContextAccessPolicy.for_request(session_id=sid)
        with pytest.raises(ContextBindingChangedError):
            ContextAccessPolicy.for_request(session_id=sid,source_context={'_context_access_policy':policy})
    assert ContextAccessPolicy.for_request(session_id=sid).project_id=='project-after'


def test_policy_snapshot_never_admits_a_chat_that_became_active_later(app):
    from core.context_namespace import ensure_chat_namespace, grant_context_import, set_chat_namespace_state
    from core.context_scope import ContextAccessPolicy

    reader='openclaw:fixed-grant-reader';donor='openclaw:later-active-donor'
    ensure_chat_namespace(reader);ensure_chat_namespace(donor,project_id='old-project')
    grant_context_import(reader,scope='chat',source_id='chat:'+donor)
    set_chat_namespace_state(donor,'archived')
    policy=ContextAccessPolicy.for_request(session_id=reader)
    assert donor not in policy.imported_chat_ids
    set_chat_namespace_state(donor,'active')
    assert donor not in policy.imported_chat_ids
    fresh=ContextAccessPolicy.for_request(session_id=reader)
    assert donor in fresh.imported_chat_ids
    from core.context_namespace import set_chat_namespace_project
    set_chat_namespace_project(donor,'new-project')
    assert donor not in fresh.imported_chat_ids


def test_reviewed_metadata_still_requires_device_ceiling_after_move_and_grant_removal(app, tmp_path):
    from core.chat_import import store
    from core.context_namespace import load_chat_namespace, revoke_context_import, set_chat_namespace_project
    from core.context_scope import ContextAccessPolicy
    from core.web.api.runtime import RuntimeServices, run_agent
    from tests.test_chat_import_migration import _finish

    root, _secret = _workspace(tmp_path)
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    reviewed = _review(app, preview, root, instructions='Reference `plan.txt`.')
    run = _finish(app, preview, continuation=False, contexts={'0': {'plan_id': reviewed['plan_id'], 'options': {}}})
    sid = run['first_session_id']
    old_project = load_chat_namespace(sid).project_id
    set_chat_namespace_project(sid, 'new-native-project')
    revoke_context_import(sid, scope='project', source_id='project:'+old_project)  # Moving can already revoke it.
    assert not ContextAccessPolicy.for_request(session_id=sid).imported_project_ids
    assert store.reviewed_working_context(sid), 'Positive control: approved metadata still reaches assembly'
    context = {}
    run_agent(RuntimeServices(display_name='VOOL'), 'Continue.', session_id=sid, source_context=context)
    assert context.get('_device_only') is True, context
