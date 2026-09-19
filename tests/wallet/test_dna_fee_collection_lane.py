"""The DNA service-fee COLLECTION riding a native x402 payment's ONE approval, through the Crypto Pilot lane itself:
accrued fees (written by the production money law) are planned into the payment's quote when economical, shown on its
sheet, claimed with the payment in one transaction, signed in the same signing session with the same PIN, sent to the
SIMULATION chain after the payment, confirmed, and retired exactly once. Deferrals keep every unit of the debt.

SIMULATION chain: the loopback Solana node answers as Mainnet and executes what it is sent. The treasury is a
DISPOSABLE FIXTURE RECIPIENT set through the operator override (a fresh key with its USDC account open). The provider
payment is a real x402 pilot proposal minted through the wallet's own UsePod door from a requirement; the accrual side
is driven through the money law's own doors; the conversion bound is the operator's own declaration (synthetic
figures, clearly labelled). No signing double, no HTTP double.

PREPARED, NOT RUN (execution pause, 2026-09-16): every assertion here is a stated expectation until the suite runs.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode
from tests.wallet.test_wallet_limits_and_approval_ui_served import (
    PIN,  # the wallet suites' disposable fixture PIN, defined once
)

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
MODEL = "meridian-synth-chat"
NOTE = "synthetic test funds: dna fee collection lane"
WRONG_PIN = "000000"  # the wallet suites' deliberately wrong PIN: it never unlocks anything


@pytest.fixture
def lane(monkeypatch, tmp_path):
    from storage.db import configure_default_db_path

    configure_default_db_path(os.path.join(tmp_path, "eb.db"))
    # ONE file for the wallet store and the money store, as in a served process: the collection's claim and
    # settlement write the fee ledger on the wallet's own connection, which is atomic only in one file
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core import effect_budget, runtime_paths

    effect_budget.reset_effect_budget_process_state()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR", "VOOL_DNA_FEE_COLLECT_MIN_ATOMIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from solders.keypair import Keypair

    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle
    from core.web.api import wallet_api

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    treasury = str(Keypair().pubkey())
    monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", treasury)
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: node.url}))
        environment.set_active_environment("mainnet")
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        node.fund_sol(treasury, 5_000_000)
        node.open_token_account(treasury, USDC_MAINNET_MINT)
        yield type("Lane", (), {"node": node, "treasury": treasury, "monkeypatch": monkeypatch})()
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    wallet_api.reset_caller_binding_for_tests()
    effect_budget.reset_effect_budget_process_state()
    configure_default_db_path(None)


def _payer(node, *, lamports: int = 20_000_000, usdc: int = 5_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="fee payer")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _accrue(payer: str, amount: int) -> str:
    """One accepted x402 provider payment of ``amount`` µUSDC, through the production money law: reserve, claim,
    dispatch, settle. Returns the operation id. (Prior debt: what a collection may move.)"""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
    from core.service_fee_ledger import fee_ceiling_atomic
    from core.usepod.monetary import EXACT_COST_NOT_SUPPLIED, ProviderLiability, SettlementEvidence
    from core.usepod.money_law import EffectBudgetMonetaryAuthority

    ceiling = amount + fee_ceiling_atomic(amount, 10)
    grant_money_authority(
        grant_operator_budget_authority(note=NOTE),
        MoneyGrantSpec(
            kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod", asset=AssetIdentity(network=SOLANA_MAINNET, asset="USDC", decimals=6),
            max_total_atomic=ceiling, per_operation_max_atomic=ceiling, models=(MODEL,), routes=("marketplace",), network=SOLANA_MAINNET, payer_account=payer,
            fee_asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9), max_fee_total_atomic=50_000, per_operation_max_fee_atomic=50_000,
            expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref=f"synthetic:{uuid.uuid4().hex[:8]}", note=NOTE,
        ),
    )
    liability = ProviderLiability(
        operation_id=f"upo_{uuid.uuid4().hex}", provider_id="usepod", transport_mode="x402", asset="USDC", unit="usdc_microunit", account_kind="x402_payer_wallet",
        account_ref=payer, network=SOLANA_MAINNET, max_amount_atomic=amount, model_id=MODEL, route_approval_id="apr", envelope_binding_sha256="c" * 64,
        basis={"route_class": "marketplace", "quote_id": f"q-{uuid.uuid4().hex[:6]}", "pay_to": "PayTo" + "9" * 39, "fee_network": SOLANA_MAINNET, "fee_asset": "SOL", "fee_decimals": 9,
               "fee_max_atomic": 5_000, "principal_balance_atomic": 500_000_000, "fee_balance_atomic": 20_000_000, "wallet_authority_label": "test"},
    )
    authority = EffectBudgetMonetaryAuthority()
    reservation = authority.reserve(liability)
    authority.claim(reservation, executor="test")
    authority.mark_dispatched(reservation)
    authority.settle(reservation, SettlementEvidence(
        operation_id=liability.operation_id, outcome="completed", http_status=200, usage={}, input_tokens=None, output_tokens=None, upper_bound_cost_atomic=None,
        exact_cost_atomic=None, exact_cost_state=EXACT_COST_NOT_SUPPLIED, route={}, balance_remaining_raw=None, usage_exceeds_liability_bound=False,
    ))
    return liability.operation_id


def _bound(numerator: int, denominator: int, *, seconds: float = 3600.0) -> dict:
    """The operator's conservative conversion bound: one lamport is worth AT MOST numerator/denominator µUSDC. A
    synthetic figure for the proof, declared through the money law's own operator door (never a price claim)."""
    from core.effect_budget import grant_operator_budget_authority
    from core.effect_budget_money import AssetIdentity, set_conversion_bound

    return set_conversion_bound(
        grant_operator_budget_authority(note=NOTE), from_asset=AssetIdentity(network=SOLANA_MAINNET, asset="SOL", decimals=9),
        to_asset=AssetIdentity(network=SOLANA_MAINNET, asset="USDC", decimals=6), numerator=numerator, denominator=denominator,
        expires_epoch=time.time() + seconds, note="synthetic fixture bound for the collection cost check",
    )


def _x402_payment(node, wallet: dict, *, amount: int) -> dict:
    """One native x402 provider payment of ``amount`` µUSDC as the wallet's UsePod door mints it: a pending pilot proposal
    to a fresh provider pay-to key whose USDC account is open on the simulation chain."""
    from solders.keypair import Keypair

    from core.wallet import usepod

    pay_to = str(Keypair().pubkey())
    node.fund_sol(pay_to, 5_000_000)
    node.open_token_account(pay_to, USDC_MAINNET_MINT)
    requirement = usepod.parse_requirement({
        "correlation_id": f"op-{uuid.uuid4().hex[:12]}", "provider": "usepod", "network": SOLANA_MAINNET, "asset": "USDC", "pay_to": pay_to, "amount_minor": amount,
        "expires_at": time.time() + 600.0, "resource": "https://usepod.test/proxy/x402/v1/chat/completions", "payer_wallet": wallet["wallet_id"],
    })
    minted = usepod.validate_x402_payment(requirement)
    assert minted["state"] == "pending_approval", minted
    return {**minted, "pay_to": pay_to}


def _quote(proposal_id: str) -> dict:
    from core.wallet import quotes

    return quotes.mint_quote(proposal_id)


def _approve_with(proposal_id: str, quote: dict, *, pin: str = PIN) -> dict:
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(pin))


def _companion_row(payment_proposal_id: str) -> dict | None:
    from core import service_fee_ledger as ledger
    from core.wallet import dna_fees

    with dna_fees._ledger_read() as conn:
        return ledger.collection_for_payment(conn, payment_proposal_id, open_only=False)


def test_accrued_fees_ride_the_next_native_payment_under_one_pin_and_retire_exactly_once(lane) -> None:
    from core.wallet import dna_fees, transfers, usepod
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    node, treasury = lane.node, lane.treasury
    wallet = _payer(node)
    payer = wallet["address"]
    for _ in range(3):
        _accrue(payer, 400_000)  # 3 x 400 µUSDC of fees = 1,200 µUSDC owed
    _accrue(payer, 112)  # plus 0.112 µUSDC of carry
    position = dna_fees.position_for(payer=payer, network=SOLANA_MAINNET, asset="USDC")
    assert (position["owed_atomic"], position["carry_numerator"], position["collectible_atomic"]) == (1_200, 1_120, 1_200)
    assert dna_fees.set_collect_min_atomic("USDC", 1_000) == 1_000
    _bound(1, 1_000)  # 5,000 lamports valued at 5 µUSDC: 42 bps of 1,200, under the 1% bound
    payment = _x402_payment(node, wallet, amount=50_000)
    pid = payment["proposal_id"]
    # the quote plans the collection INTO this payment's approval: the companion, the actions, the compact review
    quote = _quote(pid)
    fields = quote["fields"]
    companion = fields["companion"]
    assert companion is not None and (companion["amount_minor"], companion["asset"], companion["treasury_owner"], companion["treasury_token_account"]) == (1_200, "USDC", treasury, node.token_account_address(treasury, USDC_MAINNET_MINT)), companion
    assert companion["economics"]["ok"] is True and companion["economics"]["cost_bps_of_amount"] == 42 and companion["fee_max_minor"] == 5_000
    assert fields["dna_fee"]["collection"]["with_this_payment"] is True and fields["dna_fee"]["collection"]["amount_atomic"] == 1_200
    assert [action["kind"] for action in fields["actions"]] == ["provider_payment", "fee_collection"]
    review = fields["review"]
    assert review["headline"] == "Pay UsePod for this AI response" and review["primary_action"] == "Approve and pay"
    assert review["collection_line"].startswith("Previously accrued fees collected now: 0.0012 USDC")
    assert "for both transactions" in review["network_line"] and "0.00001 SOL" in review["network_line"]
    assert [(row["asset"], row["minor"]) for row in review["max_debits"]] == [("USDC", 51_200), ("SOL", 10_000)], "per asset: principal plus the collected amount; both network-fee ceilings; never one sum"
    assert review["warnings"] and review["warnings"][0]["code"] == "treasury_override"
    # the companion proposal exists, is pending, bound to this payment, and NEVER shows as its own card
    from core.wallet import status as wallet_status

    pending = wallet_status.wallet_status()["pending"]
    assert [row["proposal_id"] for row in pending] == [pid] and pending[0]["companion_collection"]["state"] == "offered"
    held = dna_fees.position_for(payer=payer, network=SOLANA_MAINNET, asset="USDC")
    assert (held["held_by_open_collections_atomic"], held["collectible_atomic"], held["owed_atomic"]) == (1_200, 0, 1_200), "planning the collection never erases the debt"
    # a wrong PIN refuses before anything is claimed or signed: nothing sent, the plan stands
    with pytest.raises(WalletFault) as wrong:
        _approve_with(pid, quote, pin=WRONG_PIN)
    assert wrong.value.code == "wallet_pin_invalid" and node.distinct_sends() == 0
    assert _companion_row(pid)["state"] == "offered"
    # ONE PIN: the payment and the collection, claimed together, signed in one session, sent payment first
    result = _approve_with(pid, quote)
    transfer = result["transfer"]
    assert transfer["state"] == "confirmed" and node.distinct_sends() == 2, transfer
    outcome = result["companion"]
    assert outcome["collected"] is True and outcome["state"] == "confirmed" and outcome["amount_atomic"] == 1_200 and outcome["tx_signature"], outcome
    assert outcome["tx_signature"] != transfer["tx_id"], "two transactions, two signatures"
    assert node.token_balance(treasury, USDC_MAINNET_MINT) == 1_200 and node.token_balance(payment["pay_to"], USDC_MAINNET_MINT) == 50_000
    assert node.token_balance(payer, USDC_MAINNET_MINT) == 5_000_000 - 50_000 - 1_200
    retired = dna_fees.position_for(payer=payer, network=SOLANA_MAINNET, asset="USDC")
    assert (retired["retired_numerator"], retired["owed_atomic"], retired["carry_numerator"], retired["collectible_atomic"], retired["open_collection"]) == (1_200 * 10_000, 0, 1_120, 0, None)
    # both transactions have their own transfer record and receipt; the collection's names the payment it rode
    companion_transfer = transfers.latest_receipt(companion["proposal_id"])
    assert (companion_transfer["state"], companion_transfer["origin"], companion_transfer["dna_fee_collection"]["payment_proposal_id"]) == ("confirmed", "dna_fee", pid)
    assert companion_transfer["explorer_url"].endswith(outcome["tx_signature"])
    with connection() as conn:
        rows = conn.execute("SELECT payload_json FROM wallet_receipts WHERE proposal_id = ? ORDER BY rowid", (companion["proposal_id"],)).fetchall()
    payloads = [json.loads(row[0]) for row in rows]
    assert payloads[-1]["origin"] == "dna_fee" and payloads[-1]["dna_fee_collection"]["state"] == "confirmed", payloads[-1]
    # the payment's own record is the provider's proof of payment, untouched by the collection
    operation = usepod.status_for(payment["correlation_id"], provider="usepod")
    assert operation["operation"]["paid"] is True and operation["transfer"]["tx_id"] == transfer["tx_id"]
    # the same approval again (a retried request) answers with both outcomes and sends nothing more
    again = _approve_with(pid, quote)
    assert again["duplicate"] is True and again["companion"]["collected"] is True and node.distinct_sends() == 2
    assert dna_fees.companion_outcome(pid)["state"] == "confirmed"


def test_the_collection_is_deferred_below_the_threshold_over_the_cost_bound_or_without_a_bound_and_the_payment_goes_alone(lane) -> None:
    from core.wallet import dna_fees

    node, treasury = lane.node, lane.treasury
    wallet = _payer(node)
    _accrue(wallet["address"], 2_000_000)  # 2,000 µUSDC owed
    payment = _x402_payment(node, wallet, amount=40_000)
    pid = payment["proposal_id"]
    # (a) under the documented default threshold: not planned, the debt stays
    below = _quote(pid)["fields"]["dna_fee"]["collection"]
    assert below["with_this_payment"] is False and below["reason"] == "below_threshold"
    # (b) at the threshold but without any conversion bound: the cost cannot be compared, so it defers
    dna_fees.set_collect_min_atomic("USDC", 1_000)
    missing = _quote(pid)["fields"]["dna_fee"]["collection"]
    assert missing["with_this_payment"] is False and missing["reason"] == "conversion_bound_missing"
    # (c) with an expensive bound (5,000 lamports valued at 5,000 µUSDC against 2,000 owed): far over 1%, deferred
    _bound(1, 1)
    quote = _quote(pid)
    costly = quote["fields"]["dna_fee"]["collection"]
    assert costly["with_this_payment"] is False and costly["reason"] == "collection_cost_exceeds_bound" and costly["economics"]["cost_bps_of_amount"] == 25_000
    assert quote["fields"]["companion"] is None and len(quote["fields"]["actions"]) == 1
    assert quote["fields"]["review"]["collection_line"].startswith("No fees are collected with this payment (collection cost exceeds bound)")
    assert [(row["asset"], row["minor"]) for row in quote["fields"]["review"]["max_debits"]] == [("USDC", 40_000), ("SOL", 5_000)]
    # the payment alone: one transaction, the treasury receives nothing, every unit of the debt remains
    result = _approve_with(pid, quote)
    assert result["transfer"]["state"] == "confirmed" and result["companion"] is None and node.distinct_sends() == 1
    assert node.token_balance(treasury, USDC_MAINNET_MINT) == 0
    position = dna_fees.position_for(payer=wallet["address"], network=SOLANA_MAINNET, asset="USDC")
    assert (position["owed_atomic"], position["collectible_atomic"], position["open_collection"]) == (2_000, 2_000, None)
    # a stale bound is a missing bound
    _bound(1, 1_000, seconds=0.5)
    time.sleep(0.6)
    later = _x402_payment(node, wallet, amount=10_000)
    assert _quote(later["proposal_id"])["fields"]["dna_fee"]["collection"]["reason"] == "conversion_bound_missing"


def test_an_expiry_read_on_a_snapshot_never_releases_a_collection_the_approval_claimed(lane) -> None:
    """REVIEW F2 at the wallet's transaction boundary: the plan is read as a lapsed snapshot, the approval claims and
    sends it in between, and the stale expiry changes nothing the winner owns -- not the ledger row, not the proposal,
    not the transfer. Then the ordinary lapse: a plan nobody approves expires typed, and the next quote plans afresh."""
    from core.wallet import dna_fees, proposals, transfers
    from core.wallet.errors import WalletFault

    node, treasury = lane.node, lane.treasury
    wallet = _payer(node)
    _accrue(wallet["address"], 2_000_000)
    dna_fees.set_collect_min_atomic("USDC", 1_000)
    _bound(1, 1_000)
    payment = _x402_payment(node, wallet, amount=30_000)
    pid = payment["proposal_id"]
    quote = _quote(pid)
    snapshot = _companion_row(pid)
    assert snapshot["state"] == "offered"
    result = _approve_with(pid, quote)  # the claim lands (and the sends follow) between the snapshot and the expiry
    assert result["companion"]["collected"] is True and node.distinct_sends() == 2
    # a stale expiry on the snapshot, at a moment past the offer's own expiry: the compare-and-set retires nothing
    assert dna_fees._expire_companion(snapshot, now=time.time() + 10_000.0) is None
    assert dna_fees.reconcile_offers(now=time.time() + 10_000.0) == []
    row = _companion_row(pid)
    assert (row["state"], row["tx_signature"], row["state_version"] > snapshot["state_version"]) == ("confirmed", result["companion"]["tx_signature"], True)
    assert proposals.get_proposal(snapshot["proposal_id"]).state == "confirmed" and transfers.latest_receipt(snapshot["proposal_id"])["state"] == "confirmed"
    assert node.token_balance(treasury, USDC_MAINNET_MINT) == 2_000
    # the ordinary lapse: a plan nobody approves in time expires typed under the fence, and the next quote re-plans
    _accrue(wallet["address"], 3_000_000)
    lane.monkeypatch.setattr(dna_fees, "COMPANION_OFFER_TTL_SECONDS", 0.2)
    later = _x402_payment(node, wallet, amount=20_000)
    stale_quote = _quote(later["proposal_id"])
    first_companion = stale_quote["fields"]["companion"]["proposal_id"]
    time.sleep(0.3)
    with pytest.raises(WalletFault) as lapsed:
        _approve_with(later["proposal_id"], stale_quote)
    assert (lapsed.value.code, lapsed.value.context.get("reason")) == ("wallet_quote_expired", "dna_fee_companion_expired")
    assert proposals.get_proposal(first_companion).state == "expired" and dna_fees.receipt_view(first_companion)["state"] == "released"
    assert node.distinct_sends() == 2, "a lapsed plan sends nothing, and neither does the payment it was bound to"
    lane.monkeypatch.setattr(dna_fees, "COMPANION_OFFER_TTL_SECONDS", 900.0)
    fresh = _quote(later["proposal_id"])["fields"]["companion"]
    assert fresh is not None and fresh["proposal_id"] != first_companion and fresh["amount_minor"] == 3_000


def test_a_treasury_change_after_the_plan_refuses_the_approval_and_rejecting_the_payment_gives_the_debt_back(lane) -> None:
    from solders.keypair import Keypair

    from core.wallet import dna_fees, proposals, settlement
    from core.wallet.errors import WalletFault

    node, treasury = lane.node, lane.treasury
    wallet = _payer(node)
    _accrue(wallet["address"], 2_000_000)
    dna_fees.set_collect_min_atomic("USDC", 1_000)
    _bound(1, 1_000)
    payment = _x402_payment(node, wallet, amount=25_000)
    pid = payment["proposal_id"]
    quote = _quote(pid)
    companion_id = quote["fields"]["companion"]["proposal_id"]
    other = str(Keypair().pubkey())
    node.fund_sol(other, 5_000_000)
    node.open_token_account(other, USDC_MAINNET_MINT)
    lane.monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", other)
    with pytest.raises(WalletFault) as stale:
        _approve_with(pid, quote)
    assert (stale.value.code, stale.value.context.get("reason")) == ("wallet_quote_mismatch", "dna_fee_companion_binding_changed"), stale.value.context
    assert node.distinct_sends() == 0 and node.token_balance(other, USDC_MAINNET_MINT) == 0, "old accrual never migrates to a new recipient; nothing at all is sent"
    lane.monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", treasury)
    # the owner rejects the payment on its card: the planned collection is released and its proposal expires
    answer = settlement.request_cancel(pid)
    assert answer["cancelled"] is True and answer["dna_fee_collection"]["state"] == "released"
    assert proposals.get_proposal(pid).state == "rejected" and proposals.get_proposal(companion_id).state == "expired"
    restored = dna_fees.position_for(payer=wallet["address"], network=SOLANA_MAINNET, asset="USDC")
    assert (restored["owed_atomic"], restored["collectible_atomic"], restored["open_collection"]) == (2_000, 2_000, None)


def test_the_designated_production_owner_is_bound_and_a_missing_treasury_account_defers_collection(lane) -> None:
    from core.vool_wallet import b58decode
    from core.wallet import dna_fees

    node = lane.node
    wallet = _payer(node)
    _accrue(wallet["address"], 2_000_000)  # accrued under the FIXTURE treasury's identity
    dna_fees.set_collect_min_atomic("USDC", 1_000)
    _bound(1, 1_000)
    lane.monkeypatch.delenv("VOOL_DNA_FEE_TREASURY_OWNER")
    binding = dna_fees.treasury_binding(SOLANA_MAINNET)
    assert binding == {"owner": "9vDnXsPonRJa7yAmvwRGMAdxt8W13Qbm7HZuvauM3Ya3", "source": "designated_production_owner", "state": "configured", "reason": "", "network": SOLANA_MAINNET}
    assert binding["owner"] == dna_fees.DESIGNATED_TREASURY_OWNER and len(b58decode(binding["owner"])) == 32
    assert dna_fees.treasury_binding("solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")["state"] == "unconfigured", "no other row is designated"
    # the fixture identity's debt never migrates to the designated owner: nothing is collectible under it yet
    payment = _x402_payment(node, wallet, amount=15_000)
    nothing = _quote(payment["proposal_id"])["fields"]["dna_fee"]["collection"]
    assert nothing["with_this_payment"] is False and nothing["reason"] == "nothing_collectible"
    # a payment accepted under the designated binding accrues to the designated owner; on the SIMULATION chain that
    # owner has no USDC account, so its collection defers before any plan exists
    _accrue(wallet["address"], 2_000_000)
    deferred = _quote(payment["proposal_id"])["fields"]["dna_fee"]["collection"]
    assert deferred["with_this_payment"] is False and deferred["reason"] == "treasury_token_account_missing"
    assert node.distinct_sends() == 0
    identities = {row["treasury_owner"]: row for row in dna_fees.status()["identities"]}
    assert identities[lane.treasury]["owed_atomic"] == 2_000 and identities[binding["owner"]]["owed_atomic"] == 2_000, "two identities, two debts, neither migrated"
    # a malformed override refuses typed: at the fee terms, at the money law's reservation, and at the payment's quote
    lane.monkeypatch.setenv("VOOL_DNA_FEE_TREASURY_OWNER", "not-a-key")
    from core.usepod.monetary import MonetaryAuthorityRefusedError
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault) as bad:
        dna_fees.fee_terms(SOLANA_MAINNET, "USDC")
    assert (bad.value.code, bad.value.context.get("reason")) == ("wallet_recipient_refused", "dna_fee_treasury_misconfigured")
    with pytest.raises(MonetaryAuthorityRefusedError) as reserve:
        _accrue(wallet["address"], 100)
    assert reserve.value.code == "MONEY_INVALID_REQUEST"
    with pytest.raises(WalletFault) as quoted:
        _quote(payment["proposal_id"])
    assert quoted.value.context.get("reason") == "dna_fee_treasury_misconfigured" and node.distinct_sends() == 0


def test_a_model_cannot_propose_a_fee_collection_the_rate_is_a_constant_and_a_collection_has_no_sheet_of_its_own(lane) -> None:
    from core.wallet import dna_fees, proposals, quotes
    from core.wallet.errors import WalletFault

    wallet = _payer(lane.node)
    # the token lane on Mainnet stays closed to the model's own origin; a fee collection is minted only by the runtime
    with pytest.raises(WalletFault) as closed:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=5, asset="USDC", origin=proposals.ORIGIN_MODEL, network=SOLANA_MAINNET)
    assert closed.value.context.get("reason") == "mainnet_tokens_move_only_for_usepod_x402"
    assert dna_fees.RATE_BPS == 10 and dna_fees.POLICY_ID == "dna_native_x402_fee:v1" and dna_fees.COLLECTION_COST_BOUND_BPS == 100
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("USDC", 0)
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("USDC", 1.5)  # type: ignore[arg-type]
    with pytest.raises(WalletFault):
        dna_fees.set_collect_min_atomic("DOGE", 1)
    # nobody volunteers to pay fees through a separate card: a collection proposal has no quote and no sheet of its own
    standalone = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=lane.treasury, amount_minor=5, asset="USDC", origin=proposals.ORIGIN_DNA_FEE, network=SOLANA_MAINNET)
    with pytest.raises(WalletFault) as no_sheet:
        quotes.mint_quote(standalone.proposal_id)
    assert (no_sheet.value.code, no_sheet.value.context.get("reason")) == ("wallet_quote_unavailable", "dna_fee_collection_rides_its_payment")
    assert lane.node.distinct_sends() == 0
