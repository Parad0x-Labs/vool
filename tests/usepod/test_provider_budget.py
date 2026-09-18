"""Provider-wide budgets run through the existing transactional monetary authority."""

from dataclasses import replace

import pytest

from core import effect_budget_money as money
from core.effect_budget import EffectBudgetRefusedError, grant_operator_budget_authority
from core.usepod.money_law import _liability_request
from tests.usepod.test_usepod_money_law import _liability
from tests.usepod.test_usepod_money_law import law as law

ACCOUNT = "upc_" + "1" * 32


def mint(mode="total", cap=1000):
    return money.grant_money_authority(
        grant_operator_budget_authority(note="synthetic provider budget"),
        money.MoneyGrantSpec(
            kind="provider_budget",
            operation_kinds=("inference_prepaid",),
            provider_id="usepod",
            provider_account=ACCOUNT,
            asset=money.AssetIdentity(network="usepod:account", asset="USDC", decimals=6),
            max_total_atomic=cap,
            per_operation_max_atomic=cap,
            expires_epoch=0,
            renewal_seconds=86400 if mode == "daily" else 0,
            credit_liquidity="not_required",
        ),
    )


def reserve(grant, op, amount, model="first", account=ACCOUNT):
    liability = replace(_liability(op, max_atomic=amount, account_ref=account), model_id=model)
    return money.reserve_liability(
        replace(_liability_request(liability, grant_id=grant.grant_id), expires_epoch=money._now() + 180)
    )


def settle(receipt, amount):
    claim = money.claim_dispatch(receipt.liability_id, executor="synthetic-test")
    money.settle_liability(
        receipt.liability_id,
        money.SettlementEvidence(
            evidence_kind="provider_usage_receipt",
            evidence_id=receipt.liability_id,
            source="provider",
            actuals={"inference_expense": amount, "provider_credit_debit": amount},
        ),
        claim_token=claim.claim_token,
    )


def test_two_models_share_total_and_account_is_bound(law):
    grant = mint()
    settle(reserve(grant, "one", 600), 600)
    settle(reserve(grant, "two", 300, "another-model"), 300)
    assert money.grant_headroom(grant.grant_id)["principal_left_atomic"] == 100
    with pytest.raises(EffectBudgetRefusedError, match=r"left|maximum"):
        reserve(grant, "over", 101)
    with pytest.raises(EffectBudgetRefusedError, match="account"):
        reserve(grant, "wrong", 1, account="different")


def test_daily_rollover_retains_unknown_and_restarts(law, monkeypatch):
    now = [money._now()]
    monkeypatch.setattr(money, "_now", lambda: now[0])
    grant = mint("daily")
    settle(reserve(grant, "paid", 600), 600)
    pending = reserve(grant, "unknown", 300)
    claim = money.claim_dispatch(pending.liability_id, executor="synthetic")
    money.record_unknown(pending.liability_id, claim.claim_token, reason="lost reply")
    assert money.grant_headroom(grant.grant_id)["principal_left_atomic"] == 100
    now[0] += 86401
    assert money.grant_headroom(grant.grant_id)["principal_left_atomic"] == 700
    assert money.money_grant(grant.grant_id).spec["renewal_seconds"] == 86400
    money.revoke_money_authority(grant_operator_budget_authority(note="disable"), grant.grant_id)
    with pytest.raises(EffectBudgetRefusedError, match="revoked"):
        reserve(grant, "disabled", 1)


def test_replacing_budget_revokes_old_and_keeps_holds(law):
    old = mint()
    reserve(old, "held", 700)
    new = mint(cap=800)
    assert money.money_grant(old.grant_id).state == "revoked"
    assert money.grant_headroom(new.grant_id)["principal_left_atomic"] == 100
    with pytest.raises(EffectBudgetRefusedError):
        reserve(new, "over", 101)


def test_bridge_requires_allow_and_does_not_widen_old_consent(law, monkeypatch):
    from core import mode_permission_policy as policy
    from core.usepod import spend_approval as s

    monkeypatch.setattr(policy, "_APPROVALS", {})
    monkeypatch.setattr(policy, "_PERSISTED_APPROVALS_RESTORED", True)
    monkeypatch.setattr(
        s,
        "_proposal_facts",
        lambda **kw: dict(account=ACCOUNT, network="usepod:account", models=["first"], routes=["marketplace"], **kw),
    )
    proposed = s.propose_spend_grant(per_call_atomic=1000, max_total_atomic=1000, budget_mode="daily")
    assert proposed["facts"]["models"] == []
    with pytest.raises(PermissionError):
        s.confirm_spend_grant(proposed["approval_id"])
    policy.resolve_approval(proposed["approval_id"], decision="allow")
    result = s.confirm_spend_grant(proposed["approval_id"])
    assert s.confirm_spend_grant(proposed["approval_id"])["grant_id"] == result["grant_id"]
    spec = money.money_grant(result["grant_id"]).spec
    assert spec["kind"] == "provider_budget" and spec["max_operations"] == 0
    assert not s.spend_consent_state()["grant"]["expired"]


def test_replacement_keeps_carried_cost_after_late_settlement(law):
    old = mint()
    pending = reserve(old, "lost-before-replace", 700)
    claim = money.claim_dispatch(pending.liability_id, executor="synthetic")
    money.record_unknown(pending.liability_id, claim.claim_token, reason="reply lost")
    new = mint(cap=800)
    assert money.grant_headroom(new.grant_id)["principal_left_atomic"] == 100
    money.settle_liability(
        pending.liability_id,
        money.SettlementEvidence(
            evidence_kind="provider_usage_receipt",
            evidence_id="late-exact",
            source="provider",
            actuals={"inference_expense": 650, "provider_credit_debit": 650},
        ),
    )
    assert money.grant_headroom(new.grant_id)["principal_left_atomic"] == 150
