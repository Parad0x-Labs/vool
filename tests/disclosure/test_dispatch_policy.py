import pytest

from core import provider_http
from core.cloud_escalation_policy import set_free_cloud_enabled
from core.cloud_provider_contract import PrivacyClass
from core.disclosure.wire import DisclosureRefused
from core.policy_engine import set_operator_policy_values
from core.provider_invocation_gateway import seal_direct_provider_invocation
from tests.disclosure.test_direct_helpers import receiver


@pytest.mark.parametrize('switch',['local_only','cloud_off'])
def test_final_dispatch_honors_a_switch_changed_after_sealing(tmp_path,monkeypatch,switch):
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (proxy,received):
        endpoint='http://provider-policy.invalid/chat'
        permit=seal_direct_provider_invocation(provider_id='outside',model_id='outside-scripted',operation='generation_probe',payload={'messages':[{'role':'user','content':'Synthetic probe.'}]},request_id='switch-'+switch,destination_url=endpoint,control_only=True,privacy_class=PrivacyClass.PUBLIC)
        if switch=='local_only':set_operator_policy_values({'system.local_only_mode':True})
        else:set_free_cloud_enabled(False)
        with pytest.raises(DisclosureRefused):
            provider_http.post(endpoint,json=permit.consume(),proxies={'http':proxy},timeout=5)
        assert received==[]

@pytest.mark.parametrize('switch',['local_only','cloud_off'])
def test_main_review_cannot_outlive_the_operator_network_switch(tmp_path,monkeypatch,switch):
    from concurrent.futures import ThreadPoolExecutor

    from adapters.base_adapter import ModelRequest
    from core.disclosure.wire import decide
    from core.provider_invocation_gateway import seal_provider_invocation
    from tests.disclosure.test_direct_helpers import waiting
    monkeypatch.setenv('VOOL_HOME',str(tmp_path/'home'))
    set_free_cloud_enabled(True)
    with receiver() as (proxy,received),ThreadPoolExecutor() as executor:
        monkeypatch.setenv('HTTP_PROXY',proxy)
        endpoint='http://provider-policy.invalid/chat'
        def send():
            request=ModelRequest('answer','Review this specification.',trace_id='main-policy-'+switch,context={'chat_id':'direct-chat'},metadata={'_disclosure_sensitive':True})
            permit=seal_provider_invocation(request=request,provider_id='outside',model_id='outside-scripted',operation='chat',payload={'model':'outside-scripted','messages':[{'role':'user','content':'Review this specification.'}]},destination_url=endpoint,cloud_destination=True)
            return provider_http.post(endpoint,json=permit.consume(),timeout=5)
        future=executor.submit(send);preview=waiting()
        if switch=='local_only':set_operator_policy_values({'system.local_only_mode':True})
        else:set_free_cloud_enabled(False)
        decide(preview['id'],preview['sha256'],'send')
        with pytest.raises(DisclosureRefused):future.result(timeout=10)
        assert received==[]
