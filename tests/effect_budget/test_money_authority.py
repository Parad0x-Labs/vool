"""THE MONETARY LAW — direct state transitions of `core.effect_budget_money`.

In-process by design: these pin the transition table, the evidence rules and
the fail-closed store behaviour one step at a time against the real store
(the per-test sqlite file of this package's conftest). Cross-process races and
kills live in `test_money_cross_process.py`; the gateway and provider seal in
`test_money_gateway_and_provider.py`.

Assets, accounts and provider ids here are SYNTHETIC (no real mint, account
or endpoint): identity is compared exactly, so a synthetic mint exercises the
same code as a real one without implying support for any real network.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest

from core import effect_budget as eb
from core import effect_budget_money as ebm
from core.effect_budget_money import (
    AssetIdentity,
    CreditLine,
    LiabilityRequest,
    MoneyGrantSpec,
    MoneyIdentity,
    MoneyLine,
    SettlementEvidence,
    UnsentEvidence,
)
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures

NETWORK = "solana:synthetic-cluster"
USDC = AssetIdentity(network=NETWORK, asset="SyntheticUsdcMint11111111111111111111111111", decimals=6, symbol="USDC")
SOL = AssetIdentity(network=NETWORK, asset="native", decimals=9, symbol="SOL")
PROVIDER = "usepod-synthetic"
ACCOUNT = "usepod-synthetic:acct-1"
X402_ACCOUNT = "usepod-synthetic:x402-payer-1"
PAYER = "wallet-synthetic-payer-1"


def _operator():
    return eb.grant_operator_budget_authority("money authority test")


def _prepaid_grant(token, **overrides):
    spec = MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_PREPAID,),
        provider_id=PROVIDER,
        provider_account=ACCOUNT,
        models=("model-x", "model-y"),
        routes=("marketplace-only",),
        asset=USDC,
        max_total_atomic=4_000_000,
        per_operation_max_atomic=3_000_000,
        expires_epoch=time.time() + 3600,
        task_id="task-1",
    )
    return ebm.grant_money_authority(token, replace(spec, **overrides))


def _x402_grant(token, **overrides):
    spec = MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_X402,),
        provider_id=PROVIDER,
        provider_account=X402_ACCOUNT,
        models=("model-x",),
        routes=("marketplace-only",),
        network=NETWORK,
        payer_account=PAYER,
        asset=USDC,
        max_total_atomic=2_000_000,
        per_operation_max_atomic=500_000,
        fee_asset=SOL,
        max_fee_total_atomic=50_000,
        per_operation_max_fee_atomic=10_000,
        expires_epoch=time.time() + 3600,
        task_id="task-1",
    )
    return ebm.grant_money_authority(token, replace(spec, **overrides))


def _prepaid(operation_id, grant, maximum, *, task="task-1", session="sess-1", model="model-x", route="marketplace-only", account=ACCOUNT, provider=PROVIDER):
    return LiabilityRequest(
        operation_id=operation_id,
        operation_kind=ebm.OP_INFERENCE_PREPAID,
        grant_id=grant.grant_id,
        lines=(
            MoneyLine(ebm.FLOW_INFERENCE_EXPENSE, USDC, maximum, account),
            MoneyLine(ebm.FLOW_PROVIDER_CREDIT_DEBIT, USDC, maximum, account),
        ),
        identity=MoneyIdentity(task_id=task, session_id=session, provider_id=provider),
        provider_account=account,
        model_id=model,
        route=route,
    )


def _x402(operation_id, grant, cap, *, fee=5_000):
    lines = [
        MoneyLine(ebm.FLOW_WALLET_OUTFLOW, USDC, cap, PAYER),
        MoneyLine(ebm.FLOW_INFERENCE_EXPENSE, USDC, cap, X402_ACCOUNT),
    ]
    if fee:
        lines.append(MoneyLine(ebm.FLOW_NETWORK_FEE, SOL, fee, PAYER))
    return LiabilityRequest(
        operation_id=operation_id,
        operation_kind=ebm.OP_INFERENCE_X402,
        grant_id=grant.grant_id,
        lines=tuple(lines),
        identity=MoneyIdentity(task_id="task-1", session_id="sess-1", provider_id=PROVIDER),
        provider_account=X402_ACCOUNT,
        model_id="model-x",
        route="marketplace-only",
        network=NETWORK,
        payer_account=PAYER,
    )


def _credit(balance, *, account=ACCOUNT, verified=True):
    ebm.record_liquidity_observation(
        account=account, asset=USDC, balance_atomic=balance, source="provider:x-balance-remaining", verified=verified
    )


def _wallet_funds(usdc, sol):
    ebm.record_liquidity_observation(account=PAYER, asset=USDC, balance_atomic=usdc, source="rpc:getTokenAccountBalance", verified=True)
    ebm.record_liquidity_observation(account=PAYER, asset=SOL, balance_atomic=sol, source="rpc:getBalance", verified=True)


def _refusal(call, *args, **kwargs) -> eb.EffectBudgetRefusedError:
    with pytest.raises(eb.EffectBudgetRefusedError) as caught:
        call(*args, **kwargs)
    return caught.value


def _db_rows(sql, params=()):
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _reconciliation_journal():
    """The automatic release/unknown receipts by liability: who judged, whom, on which proof."""
    journal = {}
    for row in _db_rows(
        "SELECT event_kind, reservation_id, instance_id, detail_json FROM effect_budget_events "
        "WHERE event_kind IN ('money_reconciled_release', 'money_reconciled_unknown') ORDER BY seq"
    ):
        assert row["reservation_id"] not in journal, "one automatic transition per liability"
        journal[row["reservation_id"]] = {"event_kind": row["event_kind"], "instance_id": row["instance_id"], "detail": json.loads(row["detail_json"])}
    return journal


def _table_rows(table):
    """Rows of a money table. A refused first write rolls back even the
    lazily created table, so an absent table is the strongest form of
    "nothing was written" — reported as no rows, and asserted explicitly."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if present is None:
            return []
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# exact money and identity
# ---------------------------------------------------------------------------


def test_exact_amounts_refuse_floats_and_excess_precision():
    assert ebm.parse_decimal_amount("0.20", decimals=6) == 200_000
    assert ebm.parse_decimal_amount("4", decimals=6) == 4_000_000
    assert ebm.parse_decimal_amount("0.000000001", decimals=9) == 1
    assert ebm.format_atomic(170_000, 6) == "0.17"
    for bad in ("0.0000001", "-1", "1e3", "0x10", "", " . "):
        assert _refusal(ebm.parse_decimal_amount, bad, decimals=6).code == ebm.MONEY_INVALID_REQUEST
    assert _refusal(ebm.parse_decimal_amount, 0.2, decimals=6).code == ebm.MONEY_INVALID_REQUEST
    assert _refusal(ebm.require_atomic, 3.0, what="amount").code == ebm.MONEY_INVALID_REQUEST
    assert _refusal(ebm.require_atomic, True, what="amount").code == ebm.MONEY_INVALID_REQUEST


def test_asset_identity_is_mint_and_decimals_never_the_symbol():
    real = AssetIdentity(network=NETWORK, asset="MintA111", decimals=6, symbol="USDC")
    impostor = AssetIdentity(network=NETWORK, asset="MintB222", decimals=6, symbol="USDC")
    wrong_decimals = AssetIdentity(network=NETWORK, asset="MintA111", decimals=9, symbol="USDC")
    assert real.key != impostor.key and real.key != wrong_decimals.key
    assert real == AssetIdentity(network=NETWORK, asset="MintA111", decimals=6, symbol="anything")
    assert _refusal(AssetIdentity, network="https://rpc.example", asset="native", decimals=9).code == ebm.MONEY_INVALID_REQUEST


def test_mixed_assets_in_one_operation_are_refused_before_the_store():
    token = _operator()
    grant = _prepaid_grant(token)
    bad = LiabilityRequest(
        operation_id="op-mixed",
        operation_kind=ebm.OP_INFERENCE_PREPAID,
        grant_id=grant.grant_id,
        lines=(
            MoneyLine(ebm.FLOW_INFERENCE_EXPENSE, USDC, 1_000_000, ACCOUNT),
            MoneyLine(ebm.FLOW_PROVIDER_CREDIT_DEBIT, SOL, 1_000_000, ACCOUNT),
        ),
        identity=MoneyIdentity(task_id="task-1", provider_id=PROVIDER),
        provider_account=ACCOUNT,
        model_id="model-x",
        route="marketplace-only",
    )
    assert _refusal(ebm.reserve_liability, bad).code == ebm.MONEY_INVALID_REQUEST
    assert _table_rows("effect_budget_money_liabilities") == []


# ---------------------------------------------------------------------------
# operator authority and grants
# ---------------------------------------------------------------------------


def test_grants_need_a_durable_operator_token_outside_every_effect_scope():
    spec = MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_PREPAID,),
        provider_id=PROVIDER,
        provider_account=ACCOUNT,
        models=("model-x",),
        routes=("marketplace-only",),
        asset=USDC,
        max_total_atomic=1_000_000,
        per_operation_max_atomic=1_000_000,
        expires_epoch=time.time() + 600,
        task_id="task-1",
    )
    forged = eb.OperatorBudgetToken(token_id="obt:forged0000000000")
    assert _refusal(ebm.grant_money_authority, forged, spec).code == eb.REFUSAL_AUTHORITY
    token = _operator()
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    open_effect_receipt_scope({"session_id": "s", "turn_id": "t", "request_id": "r"})
    try:
        assert _refusal(ebm.grant_money_authority, token, spec).code == eb.REFUSAL_AUTHORITY
        assert _refusal(ebm.revoke_money_authority, token, "mga:any").code == eb.REFUSAL_AUTHORITY
    finally:
        close_effect_receipt_scope()
    assert _table_rows("effect_budget_money_grants") == []
    refused = [row for row in eb.budget_events("money_refused")]
    assert len(refused) >= 3, "every refused mint or revoke is a durable fact"
    grant = ebm.grant_money_authority(token, spec)
    assert grant.state == ebm.GRANT_ACTIVE and grant.version == 1


def test_grants_are_bounded_envelopes_not_standing_permissions():
    token = _operator()
    base = MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_PREPAID,),
        provider_id=PROVIDER,
        provider_account=ACCOUNT,
        models=("model-x",),
        routes=("marketplace-only",),
        asset=USDC,
        max_total_atomic=1_000_000,
        per_operation_max_atomic=1_000_000,
        expires_epoch=time.time() + 600,
        task_id="task-1",
    )
    cases = {
        "no expiry in the future": replace(base, expires_epoch=time.time() - 1),
        "beyond the lifetime cap": replace(base, expires_epoch=time.time() + ebm.MAX_GRANT_LIFETIME_SECONDS + 3600),
        "a task envelope bound to nothing": replace(base, task_id=""),
        "an inference grant without models": replace(base, models=()),
        "an inference grant without routes": replace(base, routes=()),
        "per-operation above the total": replace(base, per_operation_max_atomic=2_000_000),
        "a float total": replace(base, max_total_atomic=1.5),
        "auto top-up without a frequency limit": replace(
            base, kind=ebm.GRANT_AUTO_TOPUP, operation_kinds=(ebm.OP_PROVIDER_TOPUP,), network=NETWORK, payer_account=PAYER, models=(), routes=(), task_id=""
        ),
        "a task envelope authorizing top-ups": replace(base, operation_kinds=(ebm.OP_PROVIDER_TOPUP,)),
    }
    for label, spec in cases.items():
        refusal = _refusal(ebm.grant_money_authority, token, spec)
        assert refusal.code == ebm.MONEY_INVALID_REQUEST, label
    assert _table_rows("effect_budget_money_grants") == []


# ---------------------------------------------------------------------------
# reservation
# ---------------------------------------------------------------------------


def test_prepaid_reservation_holds_the_maximum_and_consumes_the_grant_in_one_act():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    first = ebm.reserve_liability(_prepaid("op-1", grant, 3_000_000))
    assert first.state == ebm.LIABILITY_RESERVED and not first.idempotent
    lines = _db_rows("SELECT flow, max_atomic, line_state FROM effect_budget_money_lines WHERE liability_id=?", (first.liability_id,))
    assert sorted((row["flow"], row["max_atomic"], row["line_state"]) for row in lines) == [
        ("inference_expense", "3000000", "held"),
        ("provider_credit_debit", "3000000", "held"),
    ]
    refusal = _refusal(ebm.reserve_liability, _prepaid("op-2", grant, 3_000_000))
    assert refusal.code == ebm.MONEY_AUTHORITY_EXHAUSTED, "3 held of a 4 envelope leaves 1"
    second = ebm.reserve_liability(_prepaid("op-3", grant, 1_000_000))
    assert second.state == ebm.LIABILITY_RESERVED
    assert _refusal(ebm.reserve_liability, _prepaid("op-4", grant, 1)).code == ebm.MONEY_AUTHORITY_EXHAUSTED
    assert len(_db_rows("SELECT * FROM effect_budget_money_liabilities")) == 2, "a refusal writes no liability"
    kinds = [row["event_kind"] for row in eb.budget_events()]
    assert kinds.count("money_reserved") == 2 and kinds.count("money_refused") == 2


def test_idempotent_replay_returns_the_liability_and_different_terms_are_refused():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    first = ebm.reserve_liability(_prepaid("op-same", grant, 2_000_000))
    again = ebm.reserve_liability(_prepaid("op-same", grant, 2_000_000))
    assert again.liability_id == first.liability_id and again.idempotent
    changed = _refusal(ebm.reserve_liability, _prepaid("op-same", grant, 2_000_001))
    assert changed.code == ebm.MONEY_IDEMPOTENCY_CONFLICT
    other_model = _refusal(ebm.reserve_liability, _prepaid("op-same", grant, 2_000_000, model="model-y"))
    assert other_model.code == ebm.MONEY_IDEMPOTENCY_CONFLICT
    assert len(_db_rows("SELECT * FROM effect_budget_money_liabilities")) == 1


def test_the_grant_binds_every_economic_fact_and_nothing_else_passes():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    mismatches = {
        "model": _prepaid("op-m", grant, 1_000, model="model-z"),
        "route": _prepaid("op-r", grant, 1_000, route="centralized-only"),
        "provider": _prepaid("op-p", grant, 1_000, provider="someone-else"),
        "account": _prepaid("op-a", grant, 1_000, account="usepod-synthetic:acct-2"),
        "task": _prepaid("op-t", grant, 1_000, task="task-2"),
    }
    for label, request in mismatches.items():
        refusal = _refusal(ebm.reserve_liability, request)
        assert refusal.code == ebm.MONEY_AUTHORITY_INVALID, label
    missing = replace(_prepaid("op-none", grant, 1_000), grant_id="")
    assert _refusal(ebm.reserve_liability, missing).code == ebm.MONEY_AUTHORITY_REQUIRED
    unknown = replace(_prepaid("op-unknown", grant, 1_000), grant_id="mga:doesnotexist")
    assert _refusal(ebm.reserve_liability, unknown).code == ebm.MONEY_AUTHORITY_INVALID
    # the control: the exact authorized facts pass
    assert ebm.reserve_liability(_prepaid("op-ok", grant, 1_000)).state == ebm.LIABILITY_RESERVED


def test_nested_scope_ceilings_intersect_and_the_refusal_names_the_binding_rule():
    token = _operator()
    grant = _prepaid_grant(token, task_id="", session_id="sess-1", max_total_atomic=100_000_000, per_operation_max_atomic=10_000_000)
    _credit(100_000_000)
    ebm.apply_operator_money_adjustment(
        token,
        [
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_TASK, new_limit_atomic=4_000_000),
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_SESSION, new_limit_atomic=6_000_000),
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_PROVIDER, new_limit_atomic=7_000_000),
        ],
    )
    ebm.reserve_liability(_prepaid("a-1", grant, 3_000_000, task="task-a"))
    task_refusal = _refusal(ebm.reserve_liability, _prepaid("a-2", grant, 3_000_000, task="task-a"))
    assert task_refusal.code == ebm.MONEY_BUDGET_EXCEEDED and "|task|" in task_refusal.rule
    ebm.reserve_liability(_prepaid("b-1", grant, 3_000_000, task="task-b"))
    session_refusal = _refusal(ebm.reserve_liability, _prepaid("c-1", grant, 1_000_000, task="task-c"))
    assert session_refusal.code == ebm.MONEY_BUDGET_EXCEEDED and "|session|" in session_refusal.rule
    other_session = _prepaid_grant(token, task_id="", session_id="sess-2", max_total_atomic=100_000_000, per_operation_max_atomic=10_000_000)
    provider_refusal = _refusal(ebm.reserve_liability, _prepaid("d-1", other_session, 2_000_000, task="task-d", session="sess-2"))
    assert provider_refusal.code == ebm.MONEY_BUDGET_EXCEEDED and "|provider|" in provider_refusal.rule
    assert ebm.reserve_liability(_prepaid("d-2", other_session, 1_000_000, task="task-d", session="sess-2")).state == ebm.LIABILITY_RESERVED


# ---------------------------------------------------------------------------
# dispatch ownership
# ---------------------------------------------------------------------------


def test_exactly_one_dispatch_claim_wins():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    receipt = ebm.reserve_liability(_prepaid("op-claim", grant, 1_000_000))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="executor-a")
    assert claim.claim_token and claim.attempt == 1
    assert _refusal(ebm.claim_dispatch, receipt.liability_id, executor="executor-b").code == ebm.MONEY_CLAIM_CONFLICT
    assert _refusal(ebm.record_dispatched, receipt.liability_id, "not-the-token").code == ebm.MONEY_CLAIM_CONFLICT
    assert ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="req-1") == ebm.LIABILITY_PENDING
    row = _db_rows("SELECT state, executor, attempt FROM effect_budget_money_liabilities WHERE liability_id=?", (receipt.liability_id,))[0]
    assert row == {"state": "pending", "executor": "executor-a", "attempt": 1}


def test_revocation_before_claim_releases_the_never_sent_reservation_and_refuses():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    receipt = ebm.reserve_liability(_prepaid("op-revoke", grant, 1_000_000))
    revoked = ebm.revoke_money_authority(token, grant.grant_id, reason="owner stopped the task")
    assert revoked.state == ebm.GRANT_REVOKED and revoked.version == 2
    assert _refusal(ebm.claim_dispatch, receipt.liability_id, executor="x").code == ebm.MONEY_AUTHORITY_REVOKED
    assert ebm.liability(receipt.liability_id)["state"] == ebm.LIABILITY_RELEASED
    assert _refusal(ebm.reserve_liability, _prepaid("op-after", grant, 1)).code == ebm.MONEY_AUTHORITY_REVOKED


def test_a_pending_payment_survives_revocation_and_expiry_and_still_reconciles():
    token = _operator()
    grant = _prepaid_grant(token, expires_epoch=time.time() + 3)
    _credit(10_000_000)
    receipt = ebm.reserve_liability(_prepaid("op-pending", grant, 2_000_000))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="x")
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="provider-req-9")
    ebm.revoke_money_authority(token, grant.grant_id, reason="revoked while the call was in flight")
    time.sleep(3.2)
    assert ebm.liability(receipt.liability_id)["state"] == ebm.LIABILITY_PENDING, "revocation and expiry never erase a possible payment"
    projection = ebm.money_projection(grant_id=grant.grant_id)
    assert projection["inference_expense"][f"{ACCOUNT}|{USDC.key}"]["held"] == "2000000"
    assert _refusal(ebm.reserve_liability, _prepaid("op-new", grant, 1)).code in (ebm.MONEY_AUTHORITY_REVOKED, ebm.MONEY_AUTHORITY_EXPIRED)
    settled = ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(
            evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT,
            evidence_id="provider-receipt-9",
            source="provider",
            actuals={ebm.FLOW_INFERENCE_EXPENSE: 30_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 30_000},
        ),
    )
    assert settled["state"] == ebm.LIABILITY_SETTLED
    projection = ebm.money_projection(grant_id=grant.grant_id)
    assert projection["inference_expense"][f"{ACCOUNT}|{USDC.key}"]["settled_exact"] == "30000"


# ---------------------------------------------------------------------------
# settlement evidence
# ---------------------------------------------------------------------------


def _pending_prepaid(operation_id="op-settle", maximum=1_000_000):
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    receipt = ebm.reserve_liability(_prepaid(operation_id, grant, maximum))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="x")
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="req")
    return receipt, grant


def test_settlement_is_exact_where_proven_bounded_where_not_and_never_silent_over_cap():
    receipt, _grant = _pending_prepaid()
    result = ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_USAGE_PRICED, evidence_id="usage-1", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 40_000}),
    )
    assert result["state"] == ebm.LIABILITY_SETTLED
    lines = {row["flow"]: row for row in _db_rows("SELECT * FROM effect_budget_money_lines WHERE liability_id=?", (receipt.liability_id,))}
    assert lines["inference_expense"]["line_state"] == "exact" and lines["inference_expense"]["actual_atomic"] == "40000"
    assert lines["provider_credit_debit"]["line_state"] == "bounded", "a priced usage estimate does not prove what the provider debited"
    over, _ = _pending_prepaid("op-over", maximum=100_000)
    overage = ebm.settle_liability(
        over.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, evidence_id="bill-over", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 150_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 150_000}),
    )
    assert overage["over_cap"] is True
    assert ebm.liability(over.liability_id)["over_cap"] is True
    assert eb.budget_events("money_overage"), "an overage is a durable fact"


def test_duplicate_evidence_is_a_no_op_and_conflicting_evidence_changes_nothing():
    receipt, _grant = _pending_prepaid()
    evidence = SettlementEvidence(
        evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT,
        evidence_id="bill-1",
        source="provider",
        actuals={ebm.FLOW_INFERENCE_EXPENSE: 25_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 25_000},
    )
    assert ebm.settle_liability(receipt.liability_id, evidence)["idempotent"] is False
    assert ebm.settle_liability(receipt.liability_id, evidence)["idempotent"] is True
    same_id_other_amount = replace(evidence, actuals={ebm.FLOW_INFERENCE_EXPENSE: 26_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 26_000})
    assert _refusal(ebm.settle_liability, receipt.liability_id, same_id_other_amount).code == ebm.MONEY_SETTLEMENT_CONFLICT
    other_id_other_amount = replace(same_id_other_amount, evidence_id="bill-2")
    assert _refusal(ebm.settle_liability, receipt.liability_id, other_id_other_amount).code == ebm.MONEY_SETTLEMENT_CONFLICT
    lines = {row["flow"]: row["actual_atomic"] for row in _db_rows("SELECT flow, actual_atomic FROM effect_budget_money_lines WHERE liability_id=?", (receipt.liability_id,))}
    assert lines == {"inference_expense": "25000", "provider_credit_debit": "25000"}
    assert len(_db_rows("SELECT * FROM effect_budget_money_evidence WHERE liability_id=?", (receipt.liability_id,))) == 1


def test_a_balance_delta_or_a_model_is_never_settlement_evidence():
    receipt, _grant = _pending_prepaid()
    delta = SettlementEvidence(evidence_kind=ebm.EVIDENCE_BALANCE_DELTA, evidence_id="delta-1", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 1})
    assert _refusal(ebm.settle_liability, receipt.liability_id, delta).code == ebm.MONEY_EVIDENCE_INSUFFICIENT
    model = SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, evidence_id="m-1", source="model", actuals={ebm.FLOW_INFERENCE_EXPENSE: 1})
    assert _refusal(ebm.settle_liability, receipt.liability_id, model).code == ebm.MONEY_EVIDENCE_INSUFFICIENT
    wrong_kind = SettlementEvidence(evidence_kind=ebm.EVIDENCE_CHAIN_CONFIRMATION, evidence_id="sig-1", source="mechanical", actuals={ebm.FLOW_INFERENCE_EXPENSE: 1})
    assert _refusal(ebm.settle_liability, receipt.liability_id, wrong_kind).code == ebm.MONEY_EVIDENCE_INSUFFICIENT
    assert ebm.liability(receipt.liability_id)["state"] == ebm.LIABILITY_PENDING


def test_an_x402_surplus_is_provider_credit_and_never_a_wallet_refund():
    token = _operator()
    grant = _x402_grant(token)
    _wallet_funds(usdc=5_000_000, sol=100_000)
    receipt = ebm.reserve_liability(_x402("x402-1", grant, 200_000, fee=5_000))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="crypto-signer")
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="SynthSig111")
    ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_CHAIN_CONFIRMATION, evidence_id="SynthSig111", source="mechanical", actuals={ebm.FLOW_WALLET_OUTFLOW: 200_000, ebm.FLOW_NETWORK_FEE: 5_000}),
    )
    ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(
            evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT,
            evidence_id="usepod-receipt-1",
            source="provider",
            actuals={ebm.FLOW_INFERENCE_EXPENSE: 30_000},
            credits=(CreditLine(asset=USDC, account=X402_ACCOUNT, amount_atomic=170_000, verified=True),),
        ),
    )
    projection = ebm.money_projection(task_id="task-1")
    assert projection["gross_wallet_outflow"][f"{PAYER}|{USDC.key}"]["settled_exact"] == "200000"
    assert projection["network_fees"][f"{PAYER}|{SOL.key}"]["settled_exact"] == "5000"
    assert projection["inference_expense"][f"{X402_ACCOUNT}|{USDC.key}"]["settled_exact"] == "30000"
    credit = projection["provider_credit"][f"{X402_ACCOUNT}|{USDC.key}"]
    assert credit["verified_credited"] == "170000" and credit["usable_verified_remaining"] == "170000"
    assert f"{PAYER}|{USDC.key}" not in projection["provider_credit"], "provider credit never lands in the wallet"
    too_much = SettlementEvidence(
        evidence_kind=ebm.EVIDENCE_PROVIDER_BILLING_STATEMENT,
        evidence_id="usepod-statement-x",
        source="provider",
        credits=(CreditLine(asset=USDC, account=X402_ACCOUNT, amount_atomic=180_000, verified=True),),
    )
    assert _refusal(ebm.settle_liability, receipt.liability_id, too_much).code == ebm.MONEY_SETTLEMENT_CONFLICT


def test_a_topup_is_outflow_plus_credit_and_its_credit_waits_for_provider_verification():
    token = _operator()
    grant = ebm.grant_money_authority(
        token,
        MoneyGrantSpec(
            kind=ebm.GRANT_SINGLE_PAYMENT,
            operation_kinds=(ebm.OP_PROVIDER_TOPUP,),
            provider_id=PROVIDER,
            provider_account=ACCOUNT,
            network=NETWORK,
            payer_account=PAYER,
            asset=USDC,
            max_total_atomic=5_000_000,
            per_operation_max_atomic=5_000_000,
            fee_asset=SOL,
            max_fee_total_atomic=10_000,
            per_operation_max_fee_atomic=10_000,
            expires_epoch=time.time() + 600,
            approval_ref="challenge-digest-synthetic",
        ),
    )
    _wallet_funds(usdc=6_000_000, sol=50_000)
    request = LiabilityRequest(
        operation_id="topup-1",
        operation_kind=ebm.OP_PROVIDER_TOPUP,
        grant_id=grant.grant_id,
        lines=(
            MoneyLine(ebm.FLOW_WALLET_OUTFLOW, USDC, 5_000_000, PAYER),
            MoneyLine(ebm.FLOW_PROVIDER_CREDIT_CREDIT, USDC, 5_000_000, ACCOUNT),
            MoneyLine(ebm.FLOW_NETWORK_FEE, SOL, 5_000, PAYER),
        ),
        identity=MoneyIdentity(provider_id=PROVIDER),
        provider_account=ACCOUNT,
        network=NETWORK,
        payer_account=PAYER,
    )
    receipt = ebm.reserve_liability(request)
    claim = ebm.claim_dispatch(receipt.liability_id, executor="crypto-deposit")
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="SynthDepositSig")
    ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_CHAIN_CONFIRMATION, evidence_id="SynthDepositSig", source="mechanical", actuals={ebm.FLOW_WALLET_OUTFLOW: 5_000_000, ebm.FLOW_NETWORK_FEE: 5_000}),
    )
    projection = ebm.money_projection(provider_id=PROVIDER)
    assert projection["gross_wallet_outflow"][f"{PAYER}|{USDC.key}"]["settled_exact"] == "5000000"
    assert projection["inference_expense"] == {}, "a top-up is never expense"
    credit_line = _db_rows("SELECT line_state, verified FROM effect_budget_money_lines WHERE liability_id=? AND flow='provider_credit_credit'", (receipt.liability_id,))[0]
    assert credit_line == {"line_state": "bounded", "verified": 0}, "chain confirmation is not provider-account credit"
    assert f"{ACCOUNT}|{USDC.key}" not in projection["provider_credit"], "unproven credit is not usable credit"
    ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(
            evidence_kind=ebm.EVIDENCE_PROVIDER_BILLING_STATEMENT,
            evidence_id="usepod-deposit-credited",
            source="provider",
            credits=(CreditLine(asset=USDC, account=ACCOUNT, amount_atomic=5_000_000, verified=True),),
        ),
    )
    projection = ebm.money_projection(provider_id=PROVIDER)
    assert projection["provider_credit"][f"{ACCOUNT}|{USDC.key}"]["verified_credited"] == "5000000"
    # a second, identical grant use is refused: single_payment means one
    assert _refusal(ebm.reserve_liability, replace(request, operation_id="topup-2")).code == ebm.MONEY_AUTHORITY_EXHAUSTED


# ---------------------------------------------------------------------------
# unknown, unsent, retry
# ---------------------------------------------------------------------------


def test_unknown_holds_the_maximum_and_only_external_proof_releases_it():
    receipt, grant = _pending_prepaid("op-unknown", maximum=3_000_000)
    claim_row = _db_rows("SELECT claim_token FROM effect_budget_money_liabilities WHERE liability_id=?", (receipt.liability_id,))[0]
    assert ebm.record_unknown(receipt.liability_id, claim_row["claim_token"], reason="stream died after 400 tokens") == ebm.LIABILITY_UNKNOWN
    assert _refusal(ebm.reserve_liability, _prepaid("op-next", grant, 2_000_000)).code == ebm.MONEY_AUTHORITY_EXHAUSTED, "unknown is not free"
    local = UnsentEvidence(proof_kind=ebm.UNSENT_LOCAL_REFUSAL, evidence_id="local", source="mechanical")
    assert _refusal(ebm.record_unsent, receipt.liability_id, claim_row["claim_token"], evidence=local).code == ebm.MONEY_EVIDENCE_INSUFFICIENT
    by_user = UnsentEvidence(proof_kind=ebm.UNSENT_PROVIDER_NOT_RECEIVED, evidence_id="user-says", source="user")
    assert _refusal(ebm.record_unsent, receipt.liability_id, evidence=by_user).code == ebm.MONEY_EVIDENCE_INSUFFICIENT
    provider = UnsentEvidence(proof_kind=ebm.UNSENT_PROVIDER_NOT_RECEIVED, evidence_id="usepod-no-such-request", source="provider")
    assert ebm.record_unsent(receipt.liability_id, evidence=provider) == ebm.LIABILITY_UNSENT
    assert ebm.reserve_liability(_prepaid("op-next", grant, 2_000_000)).state == ebm.LIABILITY_RESERVED


def test_proven_unsent_work_is_retried_under_the_same_operation_with_every_check_rerun():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)
    receipt = ebm.reserve_liability(_prepaid("op-retry", grant, 3_000_000))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="x")
    ebm.record_unsent(
        receipt.liability_id,
        claim.claim_token,
        evidence=UnsentEvidence(proof_kind=ebm.UNSENT_CONNECTION_NOT_ESTABLISHED, evidence_id="ECONNREFUSED", source="mechanical"),
    )
    other = ebm.reserve_liability(_prepaid("op-other", grant, 3_000_000))
    assert _refusal(ebm.retry_unsent, receipt.liability_id).code == ebm.MONEY_AUTHORITY_EXHAUSTED, "capacity taken meanwhile stays taken"
    ebm.release_unclaimed(other.liability_id, reason="test frees capacity")
    retried = ebm.retry_unsent(receipt.liability_id)
    assert retried.state == ebm.LIABILITY_RESERVED and retried.liability_id == receipt.liability_id
    again = ebm.claim_dispatch(receipt.liability_id, executor="x")
    assert again.attempt == 2
    ebm.record_dispatched(receipt.liability_id, again.claim_token, evidence_id="req-2")
    settled = ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, evidence_id="bill-retry", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 900_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 900_000}),
    )
    assert settled["state"] == ebm.LIABILITY_SETTLED


def test_a_payment_proven_after_unsent_is_recorded_as_a_loud_contradiction():
    receipt, _grant = _pending_prepaid("op-contra", maximum=500_000)
    ebm.record_unsent(receipt.liability_id, evidence=UnsentEvidence(proof_kind=ebm.UNSENT_PROVIDER_NOT_RECEIVED, evidence_id="nr-1", source="provider"))
    result = ebm.settle_liability(
        receipt.liability_id,
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_BILLING_STATEMENT, evidence_id="late-bill", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 500_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 500_000}),
    )
    assert result["contradiction"] is True and result["state"] == ebm.LIABILITY_SETTLED
    assert eb.budget_events("money_contradiction")


# ---------------------------------------------------------------------------
# liquidity, conversion, freeze
# ---------------------------------------------------------------------------


def test_a_fee_asset_shortage_refuses_even_when_the_principal_is_funded():
    token = _operator()
    grant = _x402_grant(token)
    _wallet_funds(usdc=5_000_000, sol=4_999)
    refusal = _refusal(ebm.reserve_liability, _x402("fee-short", grant, 100_000, fee=5_000))
    assert refusal.code == ebm.MONEY_LIQUIDITY_INSUFFICIENT and "network fee asset" in refusal.detail
    _wallet_funds(usdc=5_000_000, sol=10_000)
    first = ebm.reserve_liability(_x402("fee-ok-1", grant, 100_000, fee=5_000))
    second = ebm.reserve_liability(_x402("fee-ok-2", grant, 100_000, fee=5_000))
    assert first.state == second.state == ebm.LIABILITY_RESERVED
    third = _refusal(ebm.reserve_liability, _x402("fee-ok-3", grant, 100_000, fee=5_000))
    assert third.code == ebm.MONEY_LIQUIDITY_INSUFFICIENT, "two held fees consume the observed SOL"


def test_a_stale_or_missing_balance_never_authorizes_a_debit():
    token = _operator()
    grant = _x402_grant(token)
    assert _refusal(ebm.reserve_liability, _x402("no-obs", grant, 100_000)).code == ebm.MONEY_LIQUIDITY_UNVERIFIED
    old = time.time() - ebm.LIQUIDITY_OBSERVATION_TTL_SECONDS - 30
    ebm.record_liquidity_observation(account=PAYER, asset=USDC, balance_atomic=5_000_000, source="rpc:getTokenAccountBalance", verified=True, observed_epoch=old)
    ebm.record_liquidity_observation(account=PAYER, asset=SOL, balance_atomic=100_000, source="rpc:getBalance", verified=True, observed_epoch=old)
    assert _refusal(ebm.reserve_liability, _x402("stale-obs", grant, 100_000)).code == ebm.MONEY_LIQUIDITY_UNVERIFIED
    prepaid_grant = _prepaid_grant(token, provider_account=ACCOUNT)
    _credit(10_000_000, verified=False)
    assert _refusal(ebm.reserve_liability, _prepaid("unverified-credit", prepaid_grant, 1_000)).code == ebm.MONEY_LIQUIDITY_UNVERIFIED
    opted_out = _prepaid_grant(token, credit_liquidity=ebm.CREDIT_LIQUIDITY_NOT_REQUIRED, task_id="task-2")
    assert ebm.reserve_liability(_prepaid("opted-out", opted_out, 1_000, task="task-2")).state == ebm.LIABILITY_RESERVED


def test_a_cross_asset_ceiling_needs_an_operator_bound_and_rounds_up():
    token = _operator()
    grant = _x402_grant(token)
    _wallet_funds(usdc=50_000_000, sol=10_000_000)
    usd = AssetIdentity(network="iso4217:USD", asset="USD", decimals=8, symbol="USD")
    rule = ebm.MoneyRuleAdjustment(flow=ebm.FLOW_NETWORK_FEE, asset=usd, scope=eb.SCOPE_GLOBAL, new_limit_atomic=400_000, cross_asset=True)
    ebm.apply_operator_money_adjustment(token, [rule])
    assert _refusal(ebm.reserve_liability, _x402("no-bound", grant, 100_000, fee=5_000)).code == ebm.MONEY_CONVERSION_UNAVAILABLE
    # operator bound: one lamport is worth AT MOST 30 USD atomic units (SOL <= 300 USD)
    ebm.set_conversion_bound(token, from_asset=SOL, to_asset=usd, numerator=30, denominator=1, expires_epoch=time.time() + 600)
    ebm.reserve_liability(_x402("bound-1", grant, 100_000, fee=5_000))  # 150_000 of 400_000
    over = _refusal(ebm.reserve_liability, _x402("bound-2", grant, 100_000, fee=10_000))  # +300_000 -> 450_000
    assert over.code == ebm.MONEY_BUDGET_EXCEEDED and "|global|" in over.rule and over.rule.endswith("|x1")
    ebm.reserve_liability(_x402("bound-3", grant, 100_000, fee=8_000))  # +240_000 -> 390_000
    # rounding is UP, per line: at 1/3 the held fees count ceil(5000/3) + ceil(8000/3) = 1667 + 2667
    ebm.set_conversion_bound(token, from_asset=SOL, to_asset=usd, numerator=1, denominator=3, expires_epoch=time.time() + 600)
    ebm.apply_operator_money_adjustment(token, [replace(rule, new_limit_atomic=4_335)])
    ebm.reserve_liability(_x402("round-1", grant, 100_000, fee=1))  # ceil(1/3) = 1 -> 4_335
    rounded = _refusal(ebm.reserve_liability, _x402("round-2", grant, 100_000, fee=1))
    assert rounded.code == ebm.MONEY_BUDGET_EXCEEDED, "a fraction of a unit rounds up, never down to free"
    expired = time.time() + 1
    ebm.set_conversion_bound(token, from_asset=SOL, to_asset=usd, numerator=1, denominator=3, expires_epoch=expired)
    time.sleep(1.2)
    assert _refusal(ebm.reserve_liability, _x402("after-expiry", grant, 100_000, fee=1)).code == ebm.MONEY_CONVERSION_UNAVAILABLE


def test_the_wallet_panic_freeze_stops_wallet_debits_and_not_prepaid_credit():
    from core.wallet import limits as wallet_limits

    token = _operator()
    x402_grant = _x402_grant(token)
    prepaid_grant = _prepaid_grant(token)
    _wallet_funds(usdc=5_000_000, sol=100_000)
    _credit(10_000_000)
    held = ebm.reserve_liability(_x402("frozen-held", x402_grant, 100_000))
    wallet_limits.set_frozen(True)
    try:
        assert _refusal(ebm.reserve_liability, _x402("frozen-new", x402_grant, 100_000)).code == ebm.MONEY_FROZEN
        assert _refusal(ebm.claim_dispatch, held.liability_id, executor="x").code == ebm.MONEY_FROZEN
        assert ebm.liability(held.liability_id)["state"] == ebm.LIABILITY_RELEASED, "never dispatched: returned"
        assert ebm.reserve_liability(_prepaid("prepaid-during-freeze", prepaid_grant, 1_000)).state == ebm.LIABILITY_RESERVED
    finally:
        wallet_limits.set_frozen(False)


# ---------------------------------------------------------------------------
# store failure and corruption
# ---------------------------------------------------------------------------


def test_corrupt_rows_fail_closed_are_reported_and_never_read_as_zero():
    receipt, grant = _pending_prepaid("op-corrupt", maximum=3_000_000)
    from storage.db import get_connection

    conn = get_connection()
    conn.execute("UPDATE effect_budget_money_lines SET max_atomic='3e6' WHERE liability_id=? AND flow='inference_expense'", (receipt.liability_id,))
    conn.commit()
    conn.close()
    refusal = _refusal(ebm.reserve_liability, _prepaid("op-after-corrupt", grant, 500_000))
    assert refusal.code == ebm.MONEY_STORE_CORRUPT
    assert receipt.liability_id in ebm.money_projection()["corrupt_liabilities"]
    conn = get_connection()
    conn.execute("UPDATE effect_budget_money_grants SET spec_json=replace(spec_json, '4000000', '9000000') WHERE grant_id=?", (grant.grant_id,))
    conn.execute("UPDATE effect_budget_money_lines SET max_atomic='3000000' WHERE liability_id=?", (receipt.liability_id,))
    conn.commit()
    conn.close()
    widened = _refusal(ebm.reserve_liability, _prepaid("op-widened", grant, 500_000))
    assert widened.code == ebm.MONEY_STORE_CORRUPT, "an edited grant fails its digest instead of widening"


def test_an_unavailable_store_refuses_with_a_typed_code_and_writes_nothing():
    token = _operator()
    grant = _prepaid_grant(token)
    _credit(10_000_000)

    def broken():
        raise OSError("disk I/O error")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(eb, "_budget_connection", broken)
        assert _refusal(ebm.reserve_liability, _prepaid("op-io", grant, 1_000)).code == ebm.MONEY_STORE_UNAVAILABLE
        assert _refusal(ebm.claim_dispatch, "mli:any", executor="x").code == ebm.MONEY_STORE_UNAVAILABLE
    assert _table_rows("effect_budget_money_liabilities") == []
    assert eb.budget_store_status()["count"] >= 2, "store failures are counted, never silent"


def test_a_dead_reserver_releases_and_a_dead_claimant_becomes_unknown_in_process_simulation():
    token = _operator()
    grant = _prepaid_grant(token, max_total_atomic=10_000_000)
    _credit(10_000_000)
    never_claimed = ebm.reserve_liability(_prepaid("dead-reserve", grant, 1_000_000))
    claimed = ebm.reserve_liability(_prepaid("dead-claim", grant, 1_000_000))
    ebm.claim_dispatch(claimed.liability_id, executor="x")
    dead = eb.current_budget_instance_id()
    eb.reset_effect_budget_process_state()
    from storage.db import get_connection

    conn = get_connection()
    conn.execute("UPDATE effect_budget_instances SET last_seen_epoch=? WHERE instance_id=?", (time.time() - eb.INSTANCE_STALE_SECONDS - 60, dead))
    conn.commit()
    conn.close()
    changes = ebm.reconcile_money_liabilities(force=True)
    assert {c["liability_id"]: c["to"] for c in changes} == {never_claimed.liability_id: "released", claimed.liability_id: "unknown"}
    judge = eb.current_budget_instance_id()
    assert judge and judge != dead, "the judging instance put itself on record before acting"
    for change in changes:
        assert (change["judged_instance_id"], change["proof"], change["reconciler_instance_id"]) == (dead, eb.INSTANCE_DEATH_STALE, judge), change
    journal = _reconciliation_journal()
    assert {liability_id: entry["event_kind"] for liability_id, entry in journal.items()} == {
        never_claimed.liability_id: "money_reconciled_release",
        claimed.liability_id: "money_reconciled_unknown",
    }
    for entry in journal.values():
        assert entry["instance_id"] == entry["detail"]["reconciler_instance_id"] == judge, entry
        assert (entry["detail"]["judged_instance_id"], entry["detail"]["proof"]) == (dead, eb.INSTANCE_DEATH_STALE), entry
    assert ebm.reconcile_money_liabilities(force=True) == [], "reconciliation is idempotent"


# ---------------------------------------------------------------------------
# funding policy and auto top-up authority
# ---------------------------------------------------------------------------


def test_each_automatic_release_or_unknown_cites_its_own_proof_and_spares_live_holdings():
    """A clean shutdown and an expired quote window are different proofs from a
    stale heartbeat. Each automatic transition journals the instance that
    judged it, the instance it judged and that proof, while a live instance's
    claimed payment is left exactly as it was."""
    token = _operator()
    grant = _prepaid_grant(token, max_total_atomic=10_000_000, per_operation_max_atomic=2_000_000)
    _credit(10_000_000)
    parked = ebm.reserve_liability(_prepaid("closed-parked", grant, 400_000))
    in_flight = ebm.reserve_liability(_prepaid("closed-in-flight", grant, 900_000))
    ebm.claim_dispatch(in_flight.liability_id, executor="shutdown-test")
    closed = eb.current_budget_instance_id()
    eb.shutdown_effect_budget_instance()
    eb.reset_effect_budget_process_state()

    quoted = ebm.reserve_liability(replace(_prepaid("short-quote", grant, 700_000), expires_epoch=time.time() + 0.8))
    judge = eb.current_budget_instance_id()
    assert judge and judge != closed
    live = ebm.reserve_liability(_prepaid("live-claim", grant, 300_000))
    ebm.claim_dispatch(live.liability_id, executor="live-executor")
    time.sleep(1.0)
    changes = ebm.reconcile_money_liabilities(force=True)
    assert [(c["liability_id"], c["to"], c["proof"], c["judged_instance_id"], c["reconciler_instance_id"]) for c in changes] == [
        (quoted.liability_id, ebm.LIABILITY_RELEASED, "quote_window_expired", judge, judge)
    ]
    journal = _reconciliation_journal()
    assert set(journal) == {parked.liability_id, in_flight.liability_id, quoted.liability_id}
    assert journal[parked.liability_id]["event_kind"] == "money_reconciled_release"
    assert journal[in_flight.liability_id]["event_kind"] == "money_reconciled_unknown"
    for liability_id in (parked.liability_id, in_flight.liability_id):
        detail = journal[liability_id]["detail"]
        assert (detail["judged_instance_id"], detail["proof"]) == (closed, eb.INSTANCE_DEATH_CLOSED), detail
    assert all(entry["instance_id"] == entry["detail"]["reconciler_instance_id"] == judge for entry in journal.values()), journal
    assert ebm.liability(in_flight.liability_id)["state"] == ebm.LIABILITY_UNKNOWN
    assert ebm.liability(live.liability_id)["state"] == ebm.LIABILITY_DISPATCHING, "a live claimant's payment is never judged"
    assert ebm.reconcile_money_liabilities(force=True) == []


def test_funding_policy_asks_below_threshold_and_auto_needs_its_own_bounded_grant():
    token = _operator()
    ebm.set_funding_policy(token, provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC, mode=ebm.FUNDING_ASK_BELOW_THRESHOLD, threshold_atomic=1_000_000, topup_atomic=5_000_000)
    assert ebm.evaluate_funding_policy(provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC).action == "none", "no verified balance: nothing decided"
    _credit(900_000)
    assert ebm.evaluate_funding_policy(provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC).action == "ask"
    auto = ebm.grant_money_authority(
        token,
        MoneyGrantSpec(
            kind=ebm.GRANT_AUTO_TOPUP,
            operation_kinds=(ebm.OP_PROVIDER_TOPUP,),
            provider_id=PROVIDER,
            provider_account=ACCOUNT,
            network=NETWORK,
            payer_account=PAYER,
            asset=USDC,
            max_total_atomic=10_000_000,
            per_operation_max_atomic=5_000_000,
            fee_asset=SOL,
            max_fee_total_atomic=20_000,
            per_operation_max_fee_atomic=10_000,
            min_interval_seconds=3600,
            expires_epoch=time.time() + 86_400,
        ),
    )
    assert auto.spec["max_concurrent"] == 1
    with pytest.raises(eb.EffectBudgetRefusedError):
        ebm.set_funding_policy(token, provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC, mode=ebm.FUNDING_AUTO, threshold_atomic=1_000_000, topup_atomic=5_000_000, auto_grant_id="")
    ebm.set_funding_policy(token, provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC, mode=ebm.FUNDING_AUTO, threshold_atomic=1_000_000, topup_atomic=5_000_000, auto_grant_id=auto.grant_id)
    assert ebm.evaluate_funding_policy(provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC).action == "auto_eligible"
    _wallet_funds(usdc=20_000_000, sol=100_000)

    def topup(operation_id):
        return LiabilityRequest(
            operation_id=operation_id,
            operation_kind=ebm.OP_PROVIDER_TOPUP,
            grant_id=auto.grant_id,
            lines=(
                MoneyLine(ebm.FLOW_WALLET_OUTFLOW, USDC, 5_000_000, PAYER),
                MoneyLine(ebm.FLOW_PROVIDER_CREDIT_CREDIT, USDC, 5_000_000, ACCOUNT),
                MoneyLine(ebm.FLOW_NETWORK_FEE, SOL, 5_000, PAYER),
            ),
            identity=MoneyIdentity(provider_id=PROVIDER),
            provider_account=ACCOUNT,
            network=NETWORK,
            payer_account=PAYER,
        )

    first = ebm.reserve_liability(topup("auto-1"))
    assert ebm.evaluate_funding_policy(provider_id=PROVIDER, provider_account=ACCOUNT, asset=USDC).action == "auto_blocked"
    concurrent = _refusal(ebm.reserve_liability, topup("auto-2"))
    assert concurrent.code == ebm.MONEY_AUTHORITY_RATE_LIMITED
    claim = ebm.claim_dispatch(first.liability_id, executor="crypto-auto-topup")
    ebm.record_dispatched(first.liability_id, claim.claim_token, evidence_id="AutoSig1")
    ebm.settle_liability(first.liability_id, SettlementEvidence(evidence_kind=ebm.EVIDENCE_CHAIN_CONFIRMATION, evidence_id="AutoSig1", source="mechanical", actuals={ebm.FLOW_WALLET_OUTFLOW: 5_000_000, ebm.FLOW_NETWORK_FEE: 5_000}))
    frequency = _refusal(ebm.reserve_liability, topup("auto-3"))
    assert frequency.code == ebm.MONEY_AUTHORITY_RATE_LIMITED, "one auto top-up per interval, settled or not"


def test_the_contract_is_executable_and_names_every_refusal_code():
    contract = ebm.money_contract()
    assert contract["contract_id"] == "effect-budget-money-contract/v1"
    assert set(contract["refusal_codes"]) >= {
        ebm.MONEY_BUDGET_EXCEEDED,
        ebm.MONEY_AUTHORITY_REQUIRED,
        ebm.MONEY_LIQUIDITY_INSUFFICIENT,
        ebm.MONEY_SETTLEMENT_CONFLICT,
        ebm.MONEY_STORE_CORRUPT,
    }
    reconciliation = contract["reconciliation"]
    assert reconciliation["unknown_when_claimant_gone"] == [eb.INSTANCE_DEATH_CLOSED, eb.INSTANCE_DEATH_STALE, eb.INSTANCE_DEATH_PROCESS_GONE, "instance_absent"]
    assert reconciliation["released_when_never_claimed"] == [*reconciliation["unknown_when_claimant_gone"], "quote_window_expired"]
    assert reconciliation["journal_detail"] == ["reason", "judged_instance_id", "proof", "reconciler_instance_id"]
    assert json.loads(json.dumps(contract)) == contract


def test_this_test_runs_on_its_own_isolated_store(request, tmp_path):
    """The package's store isolation and real-home guard hold for every money test whatever order the
    run collected files in (tests/effect_budget/conftest.py exports them for exactly this)."""
    from pathlib import Path

    from storage.db import active_default_db_path

    assert {"_isolated_budget_store", "_real_home_residue_guard"} <= set(request.fixturenames), request.fixturenames
    # Goal 2 stage 2 (2026-09-17): with the store in its own tmp subdir (see conftest), isolation means
    # this test's store lives inside THIS test's tmp tree and nowhere else.
    assert Path(active_default_db_path()).resolve().is_relative_to(Path(tmp_path).resolve())
