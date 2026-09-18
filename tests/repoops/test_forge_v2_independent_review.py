"""Independent v2 regressions through real RepoOps/adapters and synthetic transports.

No real forge or credential is used. A False dispatch claim is the durable ledger's
documented ordinary losing-CAS result, distinct from an exception in that ledger.
"""
import pytest

from tests.repoops.test_forge_actions import world, _calls, _operator_authorizes, _pr_payload, _comment_payload
from tests.repoops.test_forge_integration_review import prepare
from tests.repoops._harness import door


@pytest.mark.parametrize('action',['create','comment'])
def test_losing_durable_dispatch_claim_prevents_the_write(world,monkeypatch,action):
    forge,ctx,sid,*_=prepare(world,action)
    import core.runtime_continuity as continuity
    claims=[]
    def another_executor_won(**kwargs):
        claims.append(kwargs)
        return False
    monkeypatch.setattr(continuity,'mark_effect_dispatched',another_executor_won)
    result=door('repo.pr.'+action,{'repo_session_id':sid},ctx)
    assert claims
    assert not _calls(forge,method='POST'),(result.status,result.details)
    assert not result.ok


@pytest.mark.parametrize('action',['create','comment'])
def test_accepted_object_with_invalid_nested_shape_stays_unknown(world,action):
    forge,ctx,sid,sha,base,action_hash=prepare(world,action)
    if action=='create':
        body=_pr_payload('31',base_sha=base,head_sha=sha,draft=True,title='Reviewed title',body='Reviewed exact text')
        body['head']=17  # Valid JSON object; cannot be decoded to the typed PR result.
        forge.route('POST /pulls',body,status=201)
    else:
        body=_comment_payload(9001,body='Reviewed exact text')
        body['user']=42  # Valid JSON; conversion fails AFTER HTTP success.
        forge.route('POST /issues/17/comments',body,status=201)
    first=door('repo.pr.'+action,{'repo_session_id':sid},ctx)
    assert not first.ok
    rearm=_operator_authorizes(world,sid,action_hash)
    assert not rearm['ok'],(first.status,rearm)
    retry=door('repo.pr.'+action,{'repo_session_id':sid},ctx)
    assert not retry.ok
    assert len(_calls(forge,method='POST'))==1


@pytest.mark.parametrize('action',['create','comment'])
def test_proven_unsent_marker_error_can_resume_after_journal_recovers(world,monkeypatch,action):
    forge,ctx,sid,sha,base,action_hash=prepare(world,action)
    import core.runtime_continuity as continuity
    original=continuity.mark_effect_dispatched
    def unavailable(**kwargs):raise RuntimeError('Synthetic dispatch-marker storage outage')
    monkeypatch.setattr(continuity,'mark_effect_dispatched',unavailable)
    first=door('repo.pr.'+action,{'repo_session_id':sid},ctx)
    assert not first.ok and first.status=='effect_journal_unavailable'
    assert not _calls(forge,method='POST')
    monkeypatch.setattr(continuity,'mark_effect_dispatched',original)
    # Reauthorization must be possible for this KNOWN UNSENT action, as its receipt states.
    # It must not require falsely asserting that an uncertain external write did not land.
    rearm=_operator_authorizes(world,sid,action_hash)
    assert rearm['ok'],rearm
    resumed=door('repo.pr.'+action,{'repo_session_id':sid},ctx)
    assert resumed.ok,resumed.response_text
    assert len(_calls(forge,method='POST'))==1


@pytest.mark.parametrize('provider',['github','gitlab'])
def test_both_forges_wrap_typed_conversion_after_success(provider):
    from core.kas.adapters.github import GitHubForgeAdapter
    from core.kas.adapters.gitlab import GitLabForgeAdapter
    from core.kas.contract import AdapterConfig,ForgeAcceptedUnreadableError,KasResponse
    import json
    body={'id':901,'body':'Novel release note','user':43,'author':47}
    cls=GitHubForgeAdapter if provider=='github' else GitLabForgeAdapter
    adapter=cls(config=AdapterConfig(provider_id=provider,base_url='https://forge.example.test/api',namespace='synthetic/project'),
                transport=lambda req:KasResponse(status=201,body=json.dumps(body).encode()))
    with pytest.raises(ForgeAcceptedUnreadableError):
        adapter.post_comment('23','Novel release note',subject='issue')
