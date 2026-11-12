from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import provider_http
from core.cloud_escalation_policy import set_free_cloud_enabled
from core.cloud_provider_contract import PrivacyClass
from core.disclosure.sources import SourceCatalog
from core.disclosure.wire import DisclosureCancelled, DisclosureRefused, close_chat, decide, previews
from core.provider_invocation_gateway import seal_direct_provider_invocation
from core.remote_fetch_policy import remote_fetch_policy_scope
from tests.disclosure.test_served_preview import _resource


@contextmanager
def receiver(*, redirect=False, response=None, connections=None):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            received.append({'path':self.path,'body':b'', 'method':'GET'})
            body=json.dumps({'data':[{'id':'outside-scripted'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):
            received.append({'path':self.path,'body':self.rfile.read(int(self.headers['Content-Length']))})
            self.send_response(307 if redirect else 200)
            if redirect:self.send_header('Location','http://other-model.invalid/chat')
            body=json.dumps(response if response is not None else {'ok':True}).encode()
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    class CapturingServer(ThreadingHTTPServer):
        def get_request(self):
            result=super().get_request()
            if connections is not None:connections.append(result[1])
            return result
    server=CapturingServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    _resource('fake-provider',os.getpid(),server.server_port,'running')
    try:yield 'http://127.0.0.1:'+str(server.server_port),received
    finally:
        server.shutdown();server.server_close();worker.join()
        _resource('fake-provider',os.getpid(),server.server_port,'stopped')
        close_chat('direct-chat')


def waiting():
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        found=[p for p in previews(chat_id='direct-chat') if p['state']=='waiting']
        if found:return found[-1]
        time.sleep(.02)
    raise AssertionError('Direct helper never presented its actual request')


@pytest.mark.parametrize('choice',['send','cancel'])
def test_direct_helper_exact_bytes_and_cancel_before_dispatch(tmp_path,monkeypatch,choice):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    catalog=SourceCatalog();catalog.add('Summarize the file.','conversation','*');catalog.add('Public specification opens at noon.','attachment','att-public')
    with receiver() as (base,received),ThreadPoolExecutor() as executor:
        def call():
            with remote_fetch_policy_scope({'chat_id':'direct-chat','request_id':'direct-'+choice,'disclosure_review':'wait','_disclosure_sources':catalog}):
                payload={'model':'outside-scripted','messages':[{'role':'user','content':'Public specification opens at noon.'}]}
                permit=seal_direct_provider_invocation(provider_id='direct-provider',model_id='outside-scripted',operation='summary',payload=payload,request_id='direct-'+choice,destination_url=base+'/chat',cloud_destination=True)
                return provider_http.post(base+'/chat',json=permit.consume(),timeout=5).json()
        future=executor.submit(call);preview=waiting()
        assert not received and preview['can_transform'] is False
        with pytest.raises(ValueError,match='internal request'):
            decide(preview['id'],preview['sha256'],'replace')
        decide(preview['id'],preview['sha256'],choice)
        if choice=='cancel':
            with pytest.raises(DisclosureCancelled):future.result(timeout=10)
            assert received==[]
        else:
            assert future.result(timeout=10)=={'ok':True}
            assert received[0]['body']==preview['body'].encode()


def test_unscoped_data_helper_refuses_but_synthetic_probe_keeps_exact_unicode_bytes(tmp_path,monkeypatch):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (base,received):
        payload={'model':'outside-scripted','prompt':'A task without a reviewable chat.'}
        with pytest.raises(DisclosureRefused,match='open task'):
            seal_direct_provider_invocation(provider_id='direct-provider',model_id='outside-scripted',operation='summary',payload=payload,request_id='unscoped',destination_url=base+'/chat',cloud_destination=True)
        payload={'model':'outside-scripted','messages':[{'role':'user','content':'Synthetic unicode echo: žąsis.'}]}
        permit=seal_direct_provider_invocation(provider_id='direct-provider',model_id='outside-scripted',operation='tool_certification',payload=payload,request_id='probe',destination_url=base+'/chat',cloud_destination=True,control_only=True,privacy_class=PrivacyClass.PUBLIC)
        wire=permit.consume()
        assert provider_http.post(base+'/chat',json=wire,timeout=5).json()=={'ok':True}
        assert received[0]['body']==wire.wire_body
        assert json.loads(received[0]['body'])==payload
        with pytest.raises(DisclosureRefused):provider_http.post(base+'/chat',json=wire,timeout=5)


def test_reviewed_nonlocal_request_cannot_follow_a_redirect_through_an_outside_proxy(tmp_path,monkeypatch):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver(redirect=True) as (proxy,received):
        url='http://fake-model.invalid/chat'
        permit=seal_direct_provider_invocation(provider_id='direct-provider',model_id='outside-scripted',operation='tool_certification',payload={'model':'outside-scripted','messages':[{'role':'user','content':'Synthetic probe.'}]},request_id='redirect-probe',destination_url=url,control_only=True,privacy_class=PrivacyClass.PUBLIC)
        with pytest.raises(provider_http.exceptions.TooManyRedirects):
            provider_http.post(url,json=permit.consume(),proxies={'http':proxy},timeout=5)
        assert len(received)==1 and received[0]['path']==url


@pytest.mark.parametrize('privacy_class',[None,'unknown','wallet','secrets'])
def test_direct_probe_cannot_upgrade_a_never_remote_class(tmp_path,monkeypatch,privacy_class):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (base,received):
        with pytest.raises(ValueError,match='Privacy controls'):
            seal_direct_provider_invocation(provider_id='outside',model_id='outside-scripted',operation='generation_probe',payload={'messages':[{'role':'user','content':'Synthetic probe.'}]},request_id='never-remote',destination_url=base+'/chat',cloud_destination=True,control_only=True,privacy_class=privacy_class)
        assert received==[]


def test_fresh_home_cloud_off_refuses_synthetic_remote_probe(tmp_path,monkeypatch):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    with receiver() as (base,received):
        with pytest.raises(DisclosureRefused,match='Cloud models are off'):
            seal_direct_provider_invocation(provider_id='outside',model_id='outside-scripted',operation='generation_probe',payload={'messages':[{'role':'user','content':'Synthetic probe.'}]},request_id='fresh-off',destination_url=base+'/chat',cloud_destination=True,control_only=True,privacy_class=PrivacyClass.PUBLIC)
        assert received==[]


@pytest.mark.parametrize('choice',['send','cancel'])
def test_actual_craft_helper_reviews_before_its_outside_provider_receives_bytes(tmp_path,monkeypatch,choice):
    from core.craft_upgrade import ollama_client
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    catalog=SourceCatalog();catalog.add('Improve this specification.','conversation','*');catalog.add('Public specification opens at noon.','attachment','att-public')
    with receiver(response={'response':'The specification is ready.'}) as (proxy,received),ThreadPoolExecutor() as executor:
        monkeypatch.setenv('HTTP_PROXY',proxy)
        def call():
            with remote_fetch_policy_scope({'chat_id':'direct-chat','request_id':'craft-'+choice,'disclosure_review':'wait','_disclosure_sources':catalog}):
                return ollama_client(model='outside-scripted',ollama_url='http://craft-provider.invalid',timeout=5)('Public specification opens at noon.')
        future=executor.submit(call)
        try:
            preview=waiting()
            assert received==[]
            decide(preview['id'],preview['sha256'],choice)
            if choice=='cancel':
                with pytest.raises(DisclosureCancelled):future.result(timeout=10)
                assert received==[]
            else:
                assert future.result(timeout=10)=='The specification is ready.'
                assert len(received)==1 and received[0]['body']==preview['body'].encode()
        finally:close_chat('direct-chat')


def test_prepared_attachment_is_classified_before_any_provider_rendering(tmp_path,monkeypatch):
    from core.chat_attachments import bind_to_turn, evidence_items_for_turn, stage_attachment
    from core.disclosure.wire import request_context
    from core.provider_invocation_gateway import DirectProviderRequest
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    item=stage_attachment(session_id='direct-chat',declared_name='workshop.txt',declared_type='text/plain',data=b'Public specification opens at noon.')
    bind_to_turn(session_id='direct-chat',turn_id='early-files',attachment_ids=[item['id']])
    catalog=SourceCatalog();catalog.add('Summarize this file.','conversation','*')
    with remote_fetch_policy_scope({'chat_id':'direct-chat','request_id':'early-files','attachment_turn_id':'early-files','runtime_session_id':'direct-chat','_disclosure_sources':catalog}):
        evidence=evidence_items_for_turn(session_id='direct-chat',turn_id='early-files')
        request=DirectProviderRequest(trace_id='early-files',context={},metadata={},max_output_tokens=100)
        context=request_context(request,provider='outside-provider',cloud=True)
    assert evidence and context['has_files']
    assert PrivacyClass.PRIVATE_FILES in context['privacy_scope'].classes
    assert ('Public specification opens at noon.','attachment',item['id']) in catalog.entries
    assert context['local_context']['attachments'][0]['attachment_id']==item['id']
    assert context['attachments'][0]['size']==len(b'Public specification opens at noon.')


@pytest.mark.parametrize('choice',['send','cancel'])
def test_actual_summary_helper_reviews_its_remote_body_before_sending(tmp_path,monkeypatch,choice):
    from core.conversation_summarizer import _call_ollama
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    monkeypatch.setenv('VOOL_LOCAL_MODELS_ENABLED','1')
    monkeypatch.setenv('VOOL_OLLAMA_URL','http://summary-provider.invalid')
    set_free_cloud_enabled(True)
    catalog=SourceCatalog();catalog.add('Summarize this specification.','conversation','*');catalog.add('The specification opens at noon.','attachment','att-summary')
    with receiver(response={'message':{'content':'The specification opens at noon.'}}) as (proxy,received),ThreadPoolExecutor() as executor:
        monkeypatch.setenv('HTTP_PROXY',proxy)
        def call():
            with remote_fetch_policy_scope({'chat_id':'direct-chat','request_id':'summary-'+choice,'disclosure_review':'wait','_disclosure_sources':catalog}):
                return _call_ollama('outside-scripted',[{'role':'user','content':'The specification opens at noon.'}],timeout=5)
        future=executor.submit(call)
        try:
            preview=waiting()
            assert received==[]
            decide(preview['id'],preview['sha256'],choice)
            if choice=='cancel':
                with pytest.raises(DisclosureCancelled):future.result(timeout=10)
                assert received==[]
            else:
                assert future.result(timeout=10)=='The specification opens at noon.'
                assert len(received)==1 and received[0]['body']==preview['body'].encode()
        finally:close_chat('direct-chat')


def test_fresh_home_backend_probe_sends_no_inventory_or_generation_request(tmp_path,monkeypatch):
    from core.backend_acceleration_truth import _probe_llamacpp_generation
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    with receiver() as (proxy,received):
        monkeypatch.setenv('HTTP_PROXY',proxy)
        outcome=_probe_llamacpp_generation(base_url='http://probe-provider.invalid',model='outside-scripted',timeout_seconds=1)
        assert received==[]
        assert outcome['ok'] is False



def test_local_model_bytes_never_use_an_ambient_proxy_for_a_resolved_local_alias(tmp_path,monkeypatch):
    from adapters.base_adapter import ModelRequest
    from core.policy_engine import set_operator_policy_values
    from core.provider_invocation_gateway import seal_provider_invocation
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_operator_policy_values({'system.local_only_mode':True})
    with receiver() as (base,direct),receiver() as (proxy,proxied):
        endpoint=base.replace('127.0.0.1','127.1')+'/chat'
        from core.auto_local_only_mode import endpoint_is_local
        assert endpoint_is_local(endpoint) is True
        monkeypatch.setenv('HTTP_PROXY',proxy);monkeypatch.setenv('NO_PROXY','')
        request=ModelRequest('answer','Synthetic local-only packet.',trace_id='local-proxy',metadata={})
        permit=seal_provider_invocation(request=request,provider_id='local-scripted',model_id='outside-scripted',operation='chat',payload={'model':'outside-scripted','messages':[{'role':'user','content':'Synthetic local-only packet.'}]},destination_url=endpoint)
        assert provider_http.post(endpoint,json=permit.consume(),timeout=5).json()=={'ok':True}
        assert len(direct)==1,{'direct':direct,'proxy':proxied}
        assert proxied==[]


def test_local_summary_uses_its_direct_urllib_transport_without_an_ambient_proxy(tmp_path,monkeypatch):
    from core.conversation_summarizer import _call_ollama
    from core.policy_engine import set_operator_policy_values
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    monkeypatch.setenv('VOOL_LOCAL_MODELS_ENABLED','1')
    set_operator_policy_values({'system.local_only_mode':True})
    with receiver(response={'message':{'content':'Synthetic local summary.'}}) as (base,direct),receiver() as (proxy,proxied):
        monkeypatch.setenv('VOOL_OLLAMA_URL',base.replace('127.0.0.1','127.1'))
        monkeypatch.setenv('HTTP_PROXY',proxy);monkeypatch.setenv('NO_PROXY','')
        result=_call_ollama('outside-scripted',[{'role':'user','content':'Synthetic local summary.'}],timeout=5)
        assert result=='Synthetic local summary.'
        assert len(direct)==1,{'direct':direct,'proxy':proxied}
        assert proxied==[]
