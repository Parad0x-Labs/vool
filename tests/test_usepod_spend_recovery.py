"""Expired spending requires fresh consent through the retained monetary owner."""
from types import SimpleNamespace

import pytest

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






def test_late_money_refusal_names_expiry_and_recovery_not_provider_failure():
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response
    agent=SimpleNamespace(_live_info_mode=lambda *a,**kw: '')
    execution=SimpleNamespace(source='selected_model_blocked',details={'requested_model':'usepod:gpt-6-astra','block_reason':'MONEY_AUTHORITY_EXPIRED'})
    text=chat_surface_honest_degraded_response(agent,execution)
    assert 'spending approval expired' in text and 'was not contacted' in text
    assert 'fresh budget' in text and 'switch it to Auto' not in text
