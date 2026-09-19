"""Expired spending fails before draft consumption and recovers via the existing consent owner."""
import json
from types import SimpleNamespace

import pytest

from tests.chat_page_js_harness import DOM, run_node, script
from tests.usepod.test_usepod_money_law import MODEL, _approved_consent, _mint_grant, law


@pytest.fixture(autouse=True)
def isolated_approval_memory(monkeypatch):
    from core import mode_permission_policy as policy
    monkeypatch.setattr(policy, '_APPROVALS', {})
    monkeypatch.setattr(policy, '_PERSISTED_APPROVALS_RESTORED', True)


def _prepaid(monkeypatch, account):
    monkeypatch.setattr('core.usepod.discovery.resolve_credential', lambda: SimpleNamespace(fingerprint=account))
    monkeypatch.setattr('core.usepod.lane.load_lane_preference', lambda: (SimpleNamespace(transport_mode='prepaid_token'), ''))


def test_expired_real_grant_requires_fresh_explicit_consent(law, monkeypatch):
    from core.effect_budget_money import money_grants
    from core.mode_permission_policy import resolve_approval
    from core.usepod import spend_approval as s
    aid = _approved_consent(law, monkeypatch)
    first = s.confirm_spend_grant(aid)
    old = next(g for g in money_grants() if g.grant_id == first['grant_id'])
    _prepaid(monkeypatch, old.spec['provider_account'])
    assert s.prepaid_spend_readiness(MODEL)['available']
    now = float(old.spec['expires_epoch']) + 1
    real_time = s.time
    monkeypatch.setattr(s, 'time', SimpleNamespace(time=lambda: now, strftime=real_time.strftime, gmtime=real_time.gmtime))
    assert s.prepaid_spend_readiness(MODEL) == {'available': False, 'state': 'expired'}
    assert s.confirm_spend_grant(aid)['grant_id'] == first['grant_id']  # old consent never renews
    new = s.propose_spend_grant(per_call_atomic=1000, max_total_atomic=1000, expiry_epoch=now+3600)
    with pytest.raises(PermissionError):
        s.confirm_spend_grant(new['approval_id'])
    assert not s.prepaid_spend_readiness(MODEL)['available']
    resolve_approval(new['approval_id'], decision='allow')
    fresh = s.confirm_spend_grant(new['approval_id'])
    assert fresh['grant_id'] != first['grant_id']
    assert s.prepaid_spend_readiness(MODEL)['available']
    assert len(money_grants()) == 2


def test_preflight_uses_account_model_revocation_and_real_remaining_envelope(law, monkeypatch):
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import revoke_money_authority
    from core.usepod import spend_approval as s
    grant = _mint_grant()
    _prepaid(monkeypatch, 'different-account')
    assert s.prepaid_spend_readiness(MODEL)['state'] == 'missing'
    _prepaid(monkeypatch, grant.spec['provider_account'])
    assert s.prepaid_spend_readiness('other-model')['state'] == 'missing'
    assert s.prepaid_spend_readiness(MODEL)['available']
    monkeypatch.setattr('core.effect_budget_money.grant_headroom', lambda _: {'operations_left':0,'principal_left_atomic':1000})
    assert s.prepaid_spend_readiness(MODEL)['state'] == 'spent'
    revoke_money_authority(grant_operator_budget_authority(note='test recovery'),grant.grant_id)
    assert s.prepaid_spend_readiness(MODEL)['state'] == 'revoked'


@pytest.mark.parametrize('state', ['expired','spent','revoked','missing','unavailable'])
def test_send_preserves_draft_and_opens_recovery_without_chat_or_mint(state):
    result = run_node(DOM + script() + '\nawait new Promise(r=>setImmediate(r));\nconst state=' + json.dumps(state) + ';\n' + r'''
setDisplayedChat('openclaw:aaaaaaaaaaaaaaaaaaaa');setModelValue('usepod:gpt-6-astra');
inputEl.value='what is better Python or Java?';
const requests=[], opened=[];openSettingsInFrame=s=>opened.push(s);
fetch=async (url,opts)=>{requests.push({url,method:opts?.method||'GET'});return {ok:true,json:async()=>({spend_readiness:{available:false,state}})}}; await send();
out({draft:inputEl.value,requests,opened});
''')
    assert result['draft'] == 'what is better Python or Java?'
    assert result['opened'] == ['models/usepod/spend']
    assert len(result['requests']) == 1 and result['requests'][0]['method'] == 'GET'
    assert '/discovery?q=gpt-6-astra' in result['requests'][0]['url']


def test_other_providers_skip_preflight_and_fresh_grant_resumes_normal_path():
    result = run_node(DOM + script() + '\nawait new Promise(r=>setImmediate(r));\n' + r'''
const requests=[];fetch=async (url,opts)=>{requests.push({url,method:(opts&&opts.method)||'GET'});
 if(url==='/api/cloud/usepod/balance')return {ok:true,json:async()=>({state:'reported',usdc_balance_microunits:3000000,usdc_balance:'3.000000',evidence:'cache'})};
 return {ok:true,json:async()=>({spend_readiness:{available:true,state:'available'}})}};
const other=await checkUsePodSpending('z-ai/glm:free'), available=await checkUsePodSpending('usepod:gpt-6-astra');out({other,available,requests});
''')
    assert result['other'] and result['available']
    # A ready grant is half of readiness; the verified balance the money law will judge is the other half.
    assert [r['method'] for r in result['requests']] == ['GET', 'POST']
    assert result['requests'][1]['url'] == '/api/cloud/usepod/balance'


def test_late_money_refusal_names_expiry_and_recovery_not_provider_failure():
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response
    agent=SimpleNamespace(_live_info_mode=lambda *a,**kw: '')
    execution=SimpleNamespace(source='selected_model_blocked',details={'requested_model':'usepod:gpt-6-astra','block_reason':'MONEY_AUTHORITY_EXPIRED'})
    text=chat_surface_honest_degraded_response(agent,execution)
    assert 'spending approval expired' in text and 'was not contacted' in text
    assert 'fresh budget' in text and 'switch it to Auto' not in text
