"""The wallet x402 payment authority, composed with the production money law and the provider transport.

SIMULATION chain (a loopback Solana node answering as Mainnet, executing what it is sent) and a SYNTHETIC UsePod
stand-in that verifies each paid retry by reading what that chain executed. Everything between them is production:
the transport's quote → wallet facts → money-law reservation and claim → the authority's proposal, the owner's
approval through the pilot lane (typed quote + PIN), one real signature and one send → proof → the byte-identical
paid retry. No payment, signing or proof double exists in this file.
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from tests.usepod.strict_usepod_service import DOCUMENTED_MAINNET_NETWORK, Listing, StrictUsePodService
from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode

MODEL = "meridian-synth-chat"
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)
PIN = "482913"
NOTE = "synthetic test funds: usepod x402 wallet authority"


@pytest.fixture
def composed(monkeypatch, tmp_path):
    import json

    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "eb.db"))
    from core import effect_budget, runtime_paths

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_USEPOD_X402_APPROVAL_SECONDS", "30")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.usepod.monetary import install_monetary_authority, reset_monetary_authority
    from core.usepod.money_law import AUTHORITY_LABEL, EffectBudgetMonetaryAuthority
    from core.usepod.transport import reset_payment_authority
    from core.wallet import chains, environment, lifecycle, usepod_x402
    from core.web.api import wallet_api

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    from solders.keypair import Keypair

    pay_to = str(Keypair().pubkey())
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({DOCUMENTED_MAINNET_NETWORK: node.url}))
        environment.set_active_environment("mainnet")
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        node.fund_sol(pay_to, 5_000_000)
        node.open_token_account(pay_to, USDC_MAINNET_MINT)
        service = StrictUsePodService(models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]}, chain=node, pay_to=pay_to, usdc_mint=USDC_MAINNET_MINT).start()
        install_monetary_authority(EffectBudgetMonetaryAuthority(), label=AUTHORITY_LABEL)
        label = usepod_x402.install_at_boot()
        try:
            yield type("Composed", (), {"node": node, "service": service, "pay_to": pay_to, "label": label})()
        finally:
            service.stop()
            reset_payment_authority()
            reset_monetary_authority()
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    wallet_api.reset_caller_binding_for_tests()
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


def _pilot_wallet(node, *, lamports: int = 20_000_000, usdc: int = 2_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=DOCUMENTED_MAINNET_NETWORK, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"a-{uuid.uuid4().hex}", label="x402 payer")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    if usdc:
        node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _consent(payer: str, *, asset: str, per_operation: int) -> str:
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority

    decimals = 6 if asset == "USDC" else 9
    grant = grant_money_authority(
        grant_operator_budget_authority(note=NOTE),
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod",
            asset=AssetIdentity(network=DOCUMENTED_MAINNET_NETWORK, asset=asset, decimals=decimals),
            max_total_atomic=per_operation, per_operation_max_atomic=per_operation, models=(MODEL,), routes=("key_relay+marketplace",),
            network=DOCUMENTED_MAINNET_NETWORK, payer_account=payer,
            fee_asset=AssetIdentity(network=DOCUMENTED_MAINNET_NETWORK, asset="SOL", decimals=9), max_fee_total_atomic=50_000, per_operation_max_fee_atomic=50_000,
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref="synthetic:x402-authority", note=NOTE,
        ),
    )
    return grant.grant_id


def _envelope(service, text: str):
    from core.usepod.transport import seal_request_envelope

    return seal_request_envelope(
        payload={"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": 64},
        protocol="openai", transport_mode="x402", origin=service.origin, model_id=MODEL, route_approval_id="apr_x402_authority",
    )


def _execute_in_background(service, envelope, *, asset: str, bound: int) -> tuple[threading.Thread, dict]:
    from core.usepod.transport import UsePodHttpTransport, UsePodX402Client

    outcome: dict = {}

    def run() -> None:
        try:
            outcome["exchange"] = UsePodX402Client(transport=UsePodHttpTransport()).execute(
                envelope, origin=service.origin, allowed_assets=(asset,), local_bounds_atomic={asset: bound},
                liability_basis={"route_classes": ["marketplace", "key_relay"]}, read_timeout_seconds=60.0,
            )
        except BaseException as exc:  # recorded for the assertions below
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, outcome


def _pending_proposal(operation_id: str, *, timeout: float = 30.0) -> str:
    from core.wallet import usepod

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = usepod.status_for(operation_id, provider="usepod") or {}
        if record.get("proposal_id") and record.get("proposal_state") == "pending_approval":
            return str(record["proposal_id"])
        time.sleep(0.1)
    raise AssertionError(f"no pending pilot proposal appeared for {operation_id}")


def _owner_approves(proposal_id: str) -> dict:
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(proposal_id)
    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))


def _liability(operation_id: str) -> dict:
    from core.effect_budget_money import liability_for_operation

    return liability_for_operation(operation_id)


def _provider_settles(exchange) -> None:
    """The adapter's provider settlement for a paid retry: no exact charge supplied, so the lines stay bounded."""
    from core.usepod.monetary import EXACT_COST_NOT_SUPPLIED, OUTCOME_COMPLETED, SettlementEvidence, monetary_authority

    monetary_authority().settle(exchange.reservation, SettlementEvidence(
        operation_id=exchange.reservation.liability.operation_id, outcome=OUTCOME_COMPLETED, http_status=200, usage={}, input_tokens=None,
        output_tokens=None, upper_bound_cost_atomic=None, exact_cost_atomic=None, exact_cost_state=EXACT_COST_NOT_SUPPLIED, route={},
        balance_remaining_raw=None, usage_exceeds_liability_bound=False, detail="",
    ))


def test_the_daemon_registration_names_the_wallet_and_its_verified_network(composed) -> None:
    from core.usepod.discovery import authority_status
    from core.usepod.transport import payment_authority
    from core.wallet import usepod_x402

    assert payment_authority().label == composed.label == usepod_x402.AUTHORITY_LABEL
    assert authority_status()["payment"] == {"installed": True, "label": usepod_x402.AUTHORITY_LABEL, "verified_networks": []}, "no pilot wallet yet: nothing verified"
    _pilot_wallet(composed.node)
    assert authority_status()["payment"]["verified_networks"] == [DOCUMENTED_MAINNET_NETWORK]


def test_an_original_usdc_payment_pays_once_after_the_owner_approves(composed) -> None:
    from core.wallet import usepod

    wallet = _pilot_wallet(composed.node)
    _consent(wallet["address"], asset="USDC", per_operation=5_000_000)
    envelope = _envelope(composed.service, "Name three Baltic ports.")
    worker, outcome = _execute_in_background(composed.service, envelope, asset="USDC", bound=5_000_000)
    proposal_id = _pending_proposal(envelope.operation_id)
    # the operation is claimed before anything can be signed
    assert _liability(envelope.operation_id)["state"] == "dispatching"
    approved = _owner_approves(proposal_id)
    worker.join(timeout=60)
    assert "error" not in outcome, outcome.get("error")
    exchange = outcome["exchange"]
    assert exchange.response.status == 200 and exchange.proof.signature == approved["transfer"]["tx_id"]
    assert exchange.proof.authority_label == composed.label and exchange.proof.payer_wallet == wallet["address"]
    paid = exchange.option.amount_atomic
    assert composed.node.token_balance(composed.pay_to, USDC_MAINNET_MINT) == paid and composed.node.distinct_sends() == 1
    assert len(composed.service.requests_to("/proxy/x402/v1/chat/completions")) == 2, "one quote, one paid retry"
    assert usepod.status_for(envelope.operation_id, provider="usepod")["operation"]["proof_eligible"] is True
    row = _liability(envelope.operation_id)
    assert row["state"] == "pending"
    assert {line["flow"]: line["asset_key"] for line in row["lines"]} == {
        "wallet_outflow": f"{DOCUMENTED_MAINNET_NETWORK}|USDC|6", "inference_expense": f"{DOCUMENTED_MAINNET_NETWORK}|USDC|6", "network_fee": f"{DOCUMENTED_MAINNET_NETWORK}|SOL|9",
        # the native DNA service fee's whole-unit ceiling, in the paid asset (core.wallet.dna_fees; accrued exactly at settlement)
        "service_fee": f"{DOCUMENTED_MAINNET_NETWORK}|USDC|6",
    }
    # The adapter's two money steps after the paid retry: the provider's receipt settles, then the chain's receipt makes
    # what left the wallet exact under the transaction signature.
    from dataclasses import replace

    from core.usepod.monetary import MonetaryAuthorityRefusedError, monetary_authority
    from core.usepod.transport import payment_authority

    confirmation = payment_authority().chain_confirmation(proof=exchange.proof)
    assert (confirmation.signature, confirmation.wallet_outflow_atomic, confirmation.network_fee_atomic, confirmation.fee_asset) == (approved["transfer"]["tx_id"], paid, 5_000, "SOL")
    with pytest.raises(MonetaryAuthorityRefusedError) as early:
        monetary_authority().record_chain_confirmation(exchange.reservation, confirmation)
    assert early.value.code == "MONEY_STATE_ERROR", "chain evidence never closes a liability the provider has not settled"
    _provider_settles(exchange)
    recorded = monetary_authority().record_chain_confirmation(exchange.reservation, confirmation)
    assert recorded["state"] == "settled" and sorted(recorded["flows"]) == ["network_fee", "wallet_outflow"], recorded
    lines = {line["flow"]: line for line in _liability(envelope.operation_id)["lines"]}
    assert (lines["wallet_outflow"]["line_state"], int(lines["wallet_outflow"]["actual_atomic"])) == ("exact", paid), lines["wallet_outflow"]
    assert (lines["network_fee"]["line_state"], int(lines["network_fee"]["actual_atomic"])) == ("exact", 5_000), lines["network_fee"]
    assert monetary_authority().record_chain_confirmation(exchange.reservation, confirmation)["idempotent"] is True
    with pytest.raises(MonetaryAuthorityRefusedError) as conflict:
        monetary_authority().record_chain_confirmation(exchange.reservation, replace(confirmation, network_fee_atomic=6_000))
    assert conflict.value.code == "MONEY_SETTLEMENT_CONFLICT"


def test_a_novel_sol_payment_pays_in_lamports_with_its_fee_on_the_same_balance(composed) -> None:
    wallet = _pilot_wallet(composed.node, usdc=0)
    _consent(wallet["address"], asset="SOL", per_operation=900_000_000)
    envelope = _envelope(composed.service, "Suggest a name for a lighthouse café.")
    before = composed.node.sol_balance(composed.pay_to)
    worker, outcome = _execute_in_background(composed.service, envelope, asset="SOL", bound=900_000_000)
    _owner_approves(_pending_proposal(envelope.operation_id))
    worker.join(timeout=60)
    assert "error" not in outcome, outcome.get("error")
    exchange = outcome["exchange"]
    assert exchange.response.status == 200 and exchange.option.asset == "SOL" and exchange.option.atomic_unit == "lamport"
    assert composed.node.sol_balance(composed.pay_to) - before == exchange.option.amount_atomic
    keys = {line["flow"]: line["asset_key"] for line in _liability(envelope.operation_id)["lines"]}
    assert keys["wallet_outflow"] == keys["network_fee"] == f"{DOCUMENTED_MAINNET_NETWORK}|SOL|9"
    from core.usepod.monetary import monetary_authority
    from core.usepod.transport import payment_authority

    _provider_settles(exchange)
    monetary_authority().record_chain_confirmation(exchange.reservation, payment_authority().chain_confirmation(proof=exchange.proof))
    lines = {line["flow"]: line for line in _liability(envelope.operation_id)["lines"]}
    # principal and fee in the same asset, each exact from the chain: the lamports sent and the lamports charged
    assert (int(lines["wallet_outflow"]["actual_atomic"]), int(lines["network_fee"]["actual_atomic"])) == (exchange.option.amount_atomic, 5_000), lines


def test_an_owner_rejection_refuses_and_nothing_is_signed_or_retried(composed) -> None:
    from core.wallet import settlement

    wallet = _pilot_wallet(composed.node)
    _consent(wallet["address"], asset="USDC", per_operation=5_000_000)
    envelope = _envelope(composed.service, "Write one line about harbours at dawn.")
    worker, outcome = _execute_in_background(composed.service, envelope, asset="USDC", bound=5_000_000)
    proposal_id = _pending_proposal(envelope.operation_id)
    # the owner's Reject on the card: the same function the wallet door calls for a pilot transfer
    assert settlement.request_cancel(proposal_id).get("cancelled") is True
    worker.join(timeout=60)
    assert getattr(outcome.get("error"), "code", "") == "wallet_approval_rejected", outcome
    assert composed.node.distinct_sends() == 0
    assert len(composed.service.requests_to("/proxy/x402/v1/chat/completions")) == 1, "the quote only; no paid retry"
    assert _liability(envelope.operation_id)["state"] in {"unsent", "released"}


def test_a_closed_approval_window_expires_the_proposal_and_refuses(composed, monkeypatch) -> None:
    from core.wallet import proposals

    monkeypatch.setenv("VOOL_USEPOD_X402_APPROVAL_SECONDS", "1")
    wallet = _pilot_wallet(composed.node)
    _consent(wallet["address"], asset="USDC", per_operation=5_000_000)
    envelope = _envelope(composed.service, "Describe a quiet library in one sentence.")
    worker, outcome = _execute_in_background(composed.service, envelope, asset="USDC", bound=5_000_000)
    proposal_id = _pending_proposal(envelope.operation_id)
    worker.join(timeout=60)
    assert getattr(outcome.get("error"), "code", "") == "x402_approval_window_closed", outcome
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_EXPIRED
    assert composed.node.distinct_sends() == 0 and _liability(envelope.operation_id)["state"] in {"unsent", "released"}


def test_a_test_networks_selection_verifies_no_mainnet_network_and_refuses_before_reserving(composed) -> None:
    from core.usepod.transport import UsePodTransportError
    from core.wallet import environment

    wallet = _pilot_wallet(composed.node)
    _consent(wallet["address"], asset="USDC", per_operation=5_000_000)
    environment.set_active_environment("testnet")
    envelope = _envelope(composed.service, "Give one tip for keeping basil alive.")
    worker, outcome = _execute_in_background(composed.service, envelope, asset="USDC", bound=5_000_000)
    worker.join(timeout=60)
    error = outcome.get("error")
    assert isinstance(error, UsePodTransportError) and error.code == "quote_network_not_verified_by_payment_authority", outcome
    assert _liability(envelope.operation_id) is None and composed.node.distinct_sends() == 0
