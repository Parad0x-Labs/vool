"""The native DNA service fee through the PRODUCTION money law and the UsePod monetary authority: the fee line is
reserved with the x402 liability on the treasury-bound row, the exact fraction accrues in the settlement transaction
once the payment is accepted, never for an unknown outcome, never twice, never for a prepaid call that spends
provider credit, and the consent's own ceilings include it.

Isolated money store per test; no chain, no provider HTTP, no signing double -- only the money law's own doors. The
treasury is a clearly labelled disposable fixture owner set through the operator override; the designated production
owner is checked separately (test_usepod_dna_fee_treasury_binding).
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
MODEL = "meridian-synth-chat"
PAYER = "PayerFixture" + "1" * 32
#: DISPOSABLE FIXTURE RECIPIENT: a fresh key that decodes to 32 bytes and controls nothing
FIXTURE_TREASURY = "FeeTreasuryFixture" + "3" * 26
NOTE = "synthetic test funds: dna fee money law"


@pytest.fixture
def law(tmp_path, monkeypatch):
    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "effect_budget.db"))
    from core import effect_budget, runtime_paths

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    from solders.keypair import Keypair

    treasury = str(Keypair().pubkey())
    monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", treasury)
    from core.usepod.money_law import EffectBudgetMonetaryAuthority

    try:
        yield EffectBudgetMonetaryAuthority(), treasury
    finally:
        effect_budget.reset_effect_budget_process_state()
        configure_default_db_path(None)


def _consent(*, per_operation: int, max_total: int | None = None, operations: int = 1) -> list[str]:
    """One single-payment consent per operation: the shape the Settings consent surface mints (a multi-call grant
    binds a task or session, which a test outside a turn cannot truthfully name)."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority

    grants = []
    for _ in range(operations):
        grant = grant_money_authority(
            grant_operator_budget_authority(note=NOTE),
            MoneyGrantSpec(
                kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod",
                asset=AssetIdentity(network=SOLANA_MAINNET, asset="USDC", decimals=6),
                max_total_atomic=max_total if max_total is not None else per_operation, per_operation_max_atomic=per_operation, models=(MODEL,), routes=("marketplace",),
                network=SOLANA_MAINNET, payer_account=PAYER,
                fee_asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9), max_fee_total_atomic=50_000, per_operation_max_fee_atomic=50_000,
                expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref=f"synthetic:dna-fee:{uuid.uuid4().hex[:8]}", note=NOTE,
            ),
        )
        grants.append(grant.grant_id)
    return grants


def _liability(amount: int, *, operation_id: str = ""):
    from core.usepod.monetary import ProviderLiability

    return ProviderLiability(
        operation_id=operation_id or f"upo_{uuid.uuid4().hex}", provider_id="usepod", transport_mode="x402", asset="USDC", unit="usdc_microunit",
        account_kind="x402_payer_wallet", account_ref=PAYER, network=SOLANA_MAINNET, max_amount_atomic=amount, model_id=MODEL, route_approval_id="apr_fee",
        envelope_binding_sha256="f" * 64,
        basis={
            "route_class": "marketplace", "quote_id": f"q-{uuid.uuid4().hex[:8]}", "pay_to": "PayTo" + "9" * 39, "fee_network": SOLANA_MAINNET, "fee_asset": "SOL",
            "fee_decimals": 9, "fee_max_atomic": 5_000, "principal_balance_atomic": 50_000_000, "fee_balance_atomic": 20_000_000, "wallet_authority_label": "test",
        },
    )


def _settlement(liability, *, outcome: str = "completed"):
    from core.usepod.monetary import EXACT_COST_NOT_SUPPLIED, SettlementEvidence

    return SettlementEvidence(
        operation_id=liability.operation_id, outcome=outcome, http_status=200, usage={}, input_tokens=None, output_tokens=None, upper_bound_cost_atomic=None,
        exact_cost_atomic=None, exact_cost_state=EXACT_COST_NOT_SUPPLIED, route={}, balance_remaining_raw=None, usage_exceeds_liability_bound=False,
    )


def _lines(operation_id: str) -> dict:
    from core.effect_budget_money import liability_for_operation

    row = liability_for_operation(operation_id)
    return {line["flow"]: line for line in row["lines"]}


def _fee(operation_id: str) -> dict:
    from core.usepod.money_law import service_fee_summary

    return service_fee_summary(operation_id) or {}


def _pay(authority, amount: int) -> tuple:
    liability = _liability(amount)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="test")
    authority.mark_dispatched(reservation)
    return liability, reservation


def test_the_fee_line_is_reserved_and_the_exact_fraction_accrues_only_once_the_payment_is_accepted(law) -> None:
    authority, treasury = law
    _consent(per_operation=5_000_000)
    liability, reservation = _pay(authority, 112)
    lines = _lines(liability.operation_id)
    assert lines["service_fee"]["max_atomic"] == "1" and lines["service_fee"]["account"] == PAYER and lines["service_fee"]["asset_key"] == f"{SOLANA_MAINNET}|USDC|6"
    before = _fee(liability.operation_id)
    assert (before["state"], before["rate_bps"], before["treasury_owner"], before["fee_numerator"]) == ("reserved_not_owed", 10, treasury, None), before
    authority.settle(reservation, _settlement(liability))
    after = _fee(liability.operation_id)
    assert (after["state"], after["basis_atomic"], after["fee_numerator"], after["fee_exact"], after["fee_exact_atomic"]) == ("accrued_owed", 112, 1_120, "0.000000112", "0.112"), after
    assert after["position"]["owed_atomic"] == 0 and after["position"]["carry_numerator"] == 1_120, "sub-atomic: nothing collectible yet, the fraction is kept"
    assert _lines(liability.operation_id)["service_fee"]["actual_atomic"] == "0", "no whole unit became collectible with this settlement"
    # a replay of the same settlement evidence changes nothing
    authority.settle(reservation, _settlement(liability))
    assert _fee(liability.operation_id)["position"]["carry_numerator"] == 1_120


def test_a_thousand_accepted_dust_payments_owe_112_whole_units_through_the_money_law(law) -> None:
    """ONE session-bound task envelope of 1,000 operations, the reservations made inside that session's effect scope
    (the identity the law binds a multi-call grant to); each payment is 112 µUSDC, each fee 0.112 µUSDC."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    authority, _treasury = law
    session = f"fees-{uuid.uuid4().hex[:8]}"
    grant_money_authority(
        grant_operator_budget_authority(note=NOTE),
        MoneyGrantSpec(
            kind="task_envelope", operation_kinds=("inference_x402",), provider_id="usepod", asset=AssetIdentity(network=SOLANA_MAINNET, asset="USDC", decimals=6),
            max_total_atomic=113 * 1_000, per_operation_max_atomic=113, models=(MODEL,), routes=("marketplace",), network=SOLANA_MAINNET, payer_account=PAYER,
            max_operations=1_000, session_id=session, fee_asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9), max_fee_total_atomic=5_000 * 1_000,
            per_operation_max_fee_atomic=5_000, expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref="synthetic:dna-fee-envelope", note=NOTE,
        ),
    )
    made_collectible = 0
    open_effect_receipt_scope({"session_id": session})
    try:
        for _ in range(1_000):
            liability, reservation = _pay(authority, 112)
            authority.settle(reservation, _settlement(liability))
            made_collectible += int(_lines(liability.operation_id)["service_fee"]["actual_atomic"])
    finally:
        close_effect_receipt_scope()
    position = _fee(liability.operation_id)["position"]
    assert (position["owed_atomic"], position["carry_numerator"], position["collectible_atomic"]) == (112, 0, 112)
    assert made_collectible == 112, "the settled fee lines sum to exactly the aggregate fee, never to a thousand ceilings"


def test_an_unknown_outcome_holds_the_fee_ceiling_and_accrues_nothing_until_evidence_settles_it(law) -> None:
    authority, _treasury = law
    _consent(per_operation=5_000_000)
    liability, reservation = _pay(authority, 250_000)
    authority.retain_unknown(reservation, _settlement(liability, outcome="outcome_unknown"))
    fee = _fee(liability.operation_id)
    assert fee["state"] == "reserved_not_owed" and fee["fee_numerator"] is None and fee["position"] is None, "reservation is not revenue"
    from core.effect_budget_money import AssetIdentity, SettlementEvidence as LawEvidence, liability_for_operation, settle_liability

    # the law's own recovery route: chain evidence on an UNKNOWN outcome proves the payment reached the provider
    liability_id = liability_for_operation(liability.operation_id)["liability_id"]
    settle_liability(liability_id, LawEvidence(evidence_kind="chain_confirmation", evidence_id="sig" + "4" * 84, source="mechanical", actuals={"wallet_outflow": 250_000}))
    after = _fee(liability.operation_id)
    assert (after["state"], after["basis_atomic"], after["position"]["owed_atomic"]) == ("accrued_owed", 250_000, 250), after
    del AssetIdentity


def test_a_never_sent_and_a_released_operation_owe_nothing(law) -> None:
    authority, _treasury = law
    _consent(per_operation=5_000_000, operations=2)
    liability = _liability(300_000)
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="test")
    authority.release_unsent(reservation, reason="wallet_approval_rejected")
    fee = _fee(liability.operation_id)
    assert fee["state"] == "released_not_owed" and fee["fee_numerator"] is None


def test_the_consent_ceiling_includes_the_fee_ceiling(law) -> None:
    from core.usepod.monetary import MonetaryAuthorityRefusedError

    authority, _treasury = law
    _consent(per_operation=112)  # exactly the quote: the 1-unit fee ceiling no longer fits
    with pytest.raises(MonetaryAuthorityRefusedError) as refused:
        authority.reserve(_liability(112))
    assert refused.value.code == "MONEY_AUTHORITY_EXHAUSTED" and "service fee" in str(refused.value)
    _consent(per_operation=113)
    reservation = authority.reserve(_liability(112))
    assert reservation.reservation_id


def test_provider_credit_spent_later_by_a_prepaid_call_is_never_charged_again(law) -> None:
    authority, _treasury = law
    _consent(per_operation=5_000_000)
    liability, reservation = _pay(authority, 200_000)
    from dataclasses import replace

    authority.settle(reservation, replace(_settlement(liability), provider_credit_atomic=150_000, provider_credit_account="PayTo" + "9" * 39))
    assert _fee(liability.operation_id)["basis_atomic"] == 200_000, "the fee basis is the payment, surplus credit included, charged once"
    # the credit is spent later through the PREPAID lane: no fee line exists on that operation kind
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority, liability_for_operation
    from core.usepod.money_law import USEPOD_ACCOUNT_NETWORK
    from core.usepod.monetary import ProviderLiability

    grant_money_authority(
        grant_operator_budget_authority(note=NOTE),
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_prepaid",), provider_id="usepod", asset=AssetIdentity(network=USEPOD_ACCOUNT_NETWORK, asset="USDC", decimals=6),
            max_total_atomic=100_000, per_operation_max_atomic=100_000, models=(MODEL,), routes=("marketplace",), expires_epoch=time.time() + 3600.0,
            provider_account="upc_" + "1" * 32, credit_liquidity="not_required", approval_ref="synthetic:prepaid", note=NOTE,
        ),
    )
    prepaid = ProviderLiability(
        operation_id=f"upo_{uuid.uuid4().hex}", provider_id="usepod", transport_mode="prepaid_token", asset="USDC", unit="usdc_microunit",
        account_kind="usepod_prepaid_token_balance", account_ref="upc_" + "1" * 32, network="usepod_internal_account_ledger", max_amount_atomic=100_000,
        model_id=MODEL, route_approval_id="apr_p", envelope_binding_sha256="e" * 64, basis={"route_class": "marketplace"},
    )
    prepaid_reservation = authority.reserve(prepaid)
    authority.claim(prepaid_reservation, executor="test")
    authority.mark_dispatched(prepaid_reservation)
    authority.settle(prepaid_reservation, _settlement(prepaid))
    assert "service_fee" not in {line["flow"] for line in liability_for_operation(prepaid.operation_id)["lines"]}
    assert _fee(prepaid.operation_id) == {}


def test_a_proven_refund_reverses_only_that_operations_fee_and_the_law_journals_it(law) -> None:
    authority, _treasury = law
    _consent(per_operation=5_000_000, operations=2)
    first, first_reservation = _pay(authority, 4_000_000)
    authority.settle(first_reservation, _settlement(first))
    second, second_reservation = _pay(authority, 1_000_000)
    authority.settle(second_reservation, _settlement(second))
    from core.effect_budget import EffectBudgetRefusedError
    from core.effect_budget_money import SettlementEvidence as LawEvidence, liability_for_operation, reverse_service_fee, service_fee_position

    key = _fee(first.operation_id)["identity_key"]
    assert service_fee_position(key)["owed_atomic"] == 5_000
    liability_id = liability_for_operation(first.operation_id)["liability_id"]
    refund = LawEvidence(evidence_kind="provider_billing_statement", evidence_id="refund-stmt-1", source="provider")
    reverse_service_fee(liability_id, refund, refunded_basis_atomic=4_000_000)
    reverse_service_fee(liability_id, refund, refunded_basis_atomic=4_000_000)  # idempotent
    assert service_fee_position(key)["owed_atomic"] == 1_000, "the second operation's 1,000 stays owed"
    with pytest.raises(EffectBudgetRefusedError) as beyond:
        reverse_service_fee(liability_id, LawEvidence(evidence_kind="provider_billing_statement", evidence_id="refund-stmt-2", source="provider"), refunded_basis_atomic=1)
    assert beyond.value.code == "MONEY_SETTLEMENT_CONFLICT"
    with pytest.raises(EffectBudgetRefusedError) as inferred:
        reverse_service_fee(liability_id, LawEvidence(evidence_kind="usage_priced", evidence_id="guess", source="mechanical"), refunded_basis_atomic=1)
    assert inferred.value.code == "MONEY_EVIDENCE_INSUFFICIENT", "a refund is never inferred"
    summary = _fee(first.operation_id)
    assert summary["reversed_numerator"] == -4_000_000 * 10 and summary["net_numerator"] == 0


def test_a_sol_payment_accrues_its_fee_in_lamports_under_its_own_identity(law) -> None:
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.usepod.monetary import ProviderLiability

    authority, treasury = law
    grant_money_authority(
        grant_operator_budget_authority(note=NOTE),
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod", asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9),
            max_total_atomic=5_000_000, per_operation_max_atomic=5_000_000, models=(MODEL,), routes=("marketplace",), network=SOLANA_MAINNET, payer_account=PAYER,
            fee_asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9), max_fee_total_atomic=50_000, per_operation_max_fee_atomic=50_000,
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref="synthetic:sol", note=NOTE,
        ),
    )
    liability = ProviderLiability(
        operation_id=f"upo_{uuid.uuid4().hex}", provider_id="usepod", transport_mode="x402", asset="SOL", unit="lamport", account_kind="x402_payer_wallet",
        account_ref=PAYER, network=SOLANA_MAINNET, max_amount_atomic=784, model_id=MODEL, route_approval_id="apr_sol", envelope_binding_sha256="a" * 64,
        basis={"route_class": "marketplace", "quote_id": "q-sol", "pay_to": "PayTo" + "9" * 39, "fee_network": SOLANA_MAINNET, "fee_asset": "SOL", "fee_decimals": 9,
               "fee_max_atomic": 5_000, "principal_balance_atomic": 20_000_000, "fee_balance_atomic": 20_000_000, "wallet_authority_label": "test"},
    )
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="test")
    authority.mark_dispatched(reservation)
    authority.settle(reservation, _settlement(liability))
    fee = _fee(liability.operation_id)
    assert (fee["asset"], fee["fee_numerator"], fee["fee_exact"], fee["treasury_owner"]) == ("SOL", 7_840, "0.000000000784", treasury)
    assert fee["identity_key"].split(" // ")[2] == f"{SOLANA_MAINNET}|SOL|9", "lamport fees never mix with USDC fees"


# --- REVIEW F1: a payment proof arriving after a weaker settlement admits the fee exactly once -------------------------


def _law_evidence(kind: str, evidence_id: str, *, source: str, actuals: dict):
    from core.effect_budget_money import SettlementEvidence as LawEvidence

    return LawEvidence(evidence_kind=kind, evidence_id=evidence_id, source=source, actuals=actuals)


def test_a_late_chain_proof_after_a_usage_priced_settlement_accrues_the_fee_once_and_survives_replay_and_restart(law, tmp_path) -> None:
    """The finding's path: claimed x402 fee-bearing liability -> usage-priced settlement (the inference expense at the
    approved ceiling, NOT a payment proof: the liability settles, nothing accrues) -> the chain's exact confirmation
    of the outflow -> the fee accrues, once, under the demonstrated basis; a replay of the same evidence is idempotent,
    a second stronger evidence item never accrues again, and the accrual is on disk after a fresh connection."""
    from core.effect_budget import reset_effect_budget_process_state
    from core.effect_budget_money import liability_for_operation, settle_liability

    authority, treasury = law
    _consent(per_operation=250_250)
    liability, reservation = _pay(authority, 250_000)
    liability_id = str(reservation.reservation_id)
    settle_liability(liability_id, _law_evidence("usage_priced", "usage-1", source="mechanical", actuals={"inference_expense": 200_000}), claim_token=reservation.claim_token)
    row = liability_for_operation(liability.operation_id)
    assert row["state"] == "settled"
    fee = _fee(liability.operation_id)
    assert fee["state"] == "settled_without_accrual" and fee["fee_numerator"] is None, "usage priced at the ceiling is a payment CAP, not a demonstrated payment: nothing owed yet"
    lines = _lines(liability.operation_id)
    assert lines["service_fee"]["line_state"] == "bounded" and lines["wallet_outflow"]["line_state"] == "bounded"
    # the stronger proof arrives later: the chain's own record of the outflow
    signature = "sig" + "5" * 84
    outcome = settle_liability(liability_id, _law_evidence("chain_confirmation", signature, source="mechanical", actuals={"wallet_outflow": 250_000, "network_fee": 5_000}))
    assert outcome["state"] == "settled" and "service_fee" in outcome["flows"] and outcome["service_fee"]["late_admission"] is True and outcome["service_fee"]["idempotent"] is False
    fee = _fee(liability.operation_id)
    assert (fee["state"], fee["basis_atomic"], fee["fee_numerator"], fee["treasury_owner"]) == ("accrued_owed", 250_000, 2_500_000, treasury)
    assert _lines(liability.operation_id)["service_fee"]["line_state"] == "exact" and int(_lines(liability.operation_id)["service_fee"]["actual_atomic"]) == 250
    # replay of the same evidence: idempotent, nothing added
    again = settle_liability(liability_id, _law_evidence("chain_confirmation", signature, source="mechanical", actuals={"wallet_outflow": 250_000, "network_fee": 5_000}))
    assert again["idempotent"] is True and _fee(liability.operation_id)["fee_numerator"] == 2_500_000
    # a second stronger item (the provider's receipt, no outflow actual): already accrued, never re-run
    third = settle_liability(liability_id, _law_evidence("provider_usage_receipt", "receipt-1", source="provider", actuals={"inference_expense": 200_000}))
    assert third["service_fee"]["idempotent"] is True and "service_fee" not in third["flows"]
    assert _fee(liability.operation_id)["fee_numerator"] == 2_500_000 and _fee(liability.operation_id)["position"]["accrued_numerator"] == 2_500_000
    # restart: a fresh process state reads the same accrual from disk
    reset_effect_budget_process_state()
    assert _fee(liability.operation_id)["fee_numerator"] == 2_500_000 and _fee(liability.operation_id)["state"] == "accrued_owed"


def test_usage_priced_alone_never_accrues_and_the_ordinary_provider_first_path_still_accrues_once(law) -> None:
    from core.effect_budget_money import liability_for_operation, settle_liability

    authority, _treasury = law
    _consent(per_operation=250_250, operations=2)
    # control: a usage-priced settlement with no payment proof ever arriving owes nothing, however long it stays settled
    liability, reservation = _pay(authority, 250_000)
    settle_liability(str(reservation.reservation_id), _law_evidence("usage_priced", "usage-only", source="mechanical", actuals={"inference_expense": 100_000}), claim_token=reservation.claim_token)
    assert liability_for_operation(liability.operation_id)["state"] == "settled" and _fee(liability.operation_id)["state"] == "settled_without_accrual"
    # the ordinary path (the adapter's provider receipt first): accrues in the settling transaction, once; the chain's
    # later confirmation with a different demonstrated basis is NOT re-run, only noted
    other, other_reservation = _pay(authority, 250_000)
    authority.settle(other_reservation, _settlement(other))
    first = _fee(other.operation_id)
    assert first["state"] == "accrued_owed" and first["basis_atomic"] == 250_000
    later = settle_liability(str(other_reservation.reservation_id), _law_evidence("chain_confirmation", "sig" + "6" * 84, source="mechanical", actuals={"wallet_outflow": 240_000}))
    assert later["service_fee"]["idempotent"] is True and later["service_fee"]["basis_differs"] is True and "service_fee" not in later["flows"]
    assert _fee(other.operation_id)["basis_atomic"] == 250_000 and _fee(other.operation_id)["fee_numerator"] == 2_500_000, "exactly once: the first accrual stands"
