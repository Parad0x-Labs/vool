"""Disposable-key probes of the real store and intake dispatcher, never owner credentials."""
import hashlib

import pytest

from tests._credential_intelligence_support import FakeProviderServer
from tests.first_run_pact_rig import pact_rig
from tests.test_quarantine_destination_isolation import (
    LATER_KEY,
    THIRD_KEY,
    _begin_classify,
    _openai_compatible,
    _point_custom_at,
)


def test_failed_index_commit_cannot_send_new_key_to_old_endpoint(pact_rig, monkeypatch):
    import core.credential_intelligence.store as module
    from core.credential_intelligence.provider_registry import default_registry
    store=module.CredentialStore(default_registry())
    descriptor=default_registry().get('custom')
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as old, FakeProviderServer(_openai_compatible([THIRD_KEY])) as new:
        _point_custom_at(monkeypatch,old.url+'/v1')
        store.save_quarantined(descriptor,LATER_KEY,endpoint=old.url+'/v1')
        real=module.save_index
        def fail(rows): raise OSError('injected index commit failure')
        monkeypatch.setattr(module,'save_index',fail)
        with pytest.raises(OSError):
            store.save_quarantined(descriptor,THIRD_KEY,endpoint=new.url+'/v1')
        monkeypatch.setattr(module,'save_index',real)
        pact_rig.post('/api/intake/quarantine/retry',{'provider_id':'custom'})
        forbidden=hashlib.sha256(('Bearer '+THIRD_KEY).encode()).hexdigest()
        assert not any(row['auth_sha256']==forbidden for row in old.requests), 'new secret reached old destination after failed index write'


def test_completed_delete_recreate_rejects_old_snapshot(pact_rig):
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore, IntakeRefusedError
    store=CredentialStore(default_registry());descriptor=default_registry().get('custom')
    store.save_quarantined(descriptor,LATER_KEY,endpoint='http://127.0.0.1:9/v1')
    old=store.quarantine_snapshot('custom')
    assert store.delete_quarantined('custom',snapshot=old).removed
    store=CredentialStore(default_registry())
    store.save_quarantined(descriptor,LATER_KEY,endpoint='http://127.0.0.1:9/v1')
    current=store.quarantine_snapshot('custom')
    assert current is not None
    with pytest.raises(IntakeRefusedError):
        store.delete_quarantined('custom',snapshot=old)
    assert store.quarantine_snapshot('custom')==current


def test_verified_complete_keeps_key_and_endpoint_paired(pact_rig,monkeypatch):
    from core import credential_store
    from core.credential_intelligence.store import CredentialStore
    with FakeProviderServer(_openai_compatible([LATER_KEY])) as a, FakeProviderServer(_openai_compatible([THIRD_KEY])) as b:
        _point_custom_at(monkeypatch,a.url+'/v1')
        sa=_begin_classify(pact_rig,LATER_KEY,base_url=a.url+'/v1')
        sb=_begin_classify(pact_rig,THIRD_KEY,base_url=b.url+'/v1')
        for sid,url in [(sa,a.url),(sb,b.url)]:
            status,result=pact_rig.post('/api/intake/verify',{'session_id':sid,'provider_id':'custom','base_url':url+'/v1'})
            assert status==200 and result['outcome']=='verified',result
        real=CredentialStore.save_verified
        armed=[True]
        def interleave(self,descriptor,secret,outcome,**kw):
            # This hook runs BEFORE save_verified acquires the lock. The first request has
            # already written CUSTOM_BASE_URL_SLOT outside that transaction.
            if armed[0] and secret==LATER_KEY:
                armed[0]=False
                status,result=pact_rig.post('/api/intake/complete',{'session_id':sb})
                assert status==200,result
            return real(self,descriptor,secret,outcome,**kw)
        monkeypatch.setattr(CredentialStore,'save_verified',interleave)
        status,result=pact_rig.post('/api/intake/complete',{'session_id':sa})
        assert status==200,result
        assert credential_store.get_credential('llm.cloud.custom')==LATER_KEY
        assert credential_store.get_credential('llm.cloud.custom_base_url')==a.url+'/v1'
