"""The native DNA service fee through ONE served process, with the ECONOMICAL AUTOMATIC COLLECTION: the chat turn's
payment sheet states the exact fee and whether previously accrued fees ride this payment; the receipt shows the fee
accrued and owed; when accrued fees reach the threshold and the collection's network cost is within the bound, the
payment's ONE PIN also collects them (a second transaction to the treasury, sent after the payment); the ledger
retires exactly that amount once; a costly collection defers with its reason; everything survives a daemon restart.

SIMULATION CHAIN (``tests.wallet._simulated_solana.SimulatedSolanaNode`` answering as Mainnet) and the SYNTHETIC UsePod
stand-in that verifies each paid retry against that chain. The daemon is ``apps.vool_api_server`` unchanged with the
production authorities. The treasury is a DISPOSABLE FIXTURE RECIPIENT handed to the daemon through the operator
override (``VOOL_DNA_FEE_TREASURY_OWNER``), with its USDC account open on the simulation node; the collection
threshold is set to one atomic unit through the operator override. Prior debt and the conversion bound are declared
in the daemon's own store through the money law's operator doors (a child process with the daemon's home: the owner's
CLI, not an HTTP door -- setting a bound is never served). Nothing here proves anything about the live UsePod service
or the real mainnet.

PREPARED, NOT RUN (execution pause, 2026-09-16): every assertion here is a stated expectation until the suite runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import MARKET, MARKET_ID, MODEL, REPO_ROOT, _answer_text, _keep, _session
from tests.usepod.test_usepod_settings_ui import CENTRAL
from tests.usepod.test_usepod_x402_composed_served import (
    PIN,
    WalletServedDaemon,
    _allow_pending_consent,
    _chat_in_background,
    _finish,
    _pending_payment,
    _pin_for,
    _usepod_receipts,
    ready_pilot_wallet,
)
from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
X402_PATH = "/proxy/x402/v1/chat/completions"
DESIGNATED_OWNER = "9vDnXsPonRJa7yAmvwRGMAdxt8W13Qbm7HZuvauM3Ya3"
NOTE = "synthetic test funds: dna fee served proof"


class FeeServedDaemon(WalletServedDaemon):
    """The production-authority daemon with the fixture treasury override and a one-unit collection threshold."""

    def __init__(self, home: Path, chain_url: str, treasury: str) -> None:
        super().__init__(home, chain_url)
        self.treasury = treasury
        self.override_treasury = True

    def env(self) -> dict[str, str]:
        env = super().env()
        env["VOOL_DNA_FEE_COLLECT_MIN_ATOMIC"] = "1"
        if self.override_treasury:
            env["VOOL_DNA_FEE_TREASURY_OWNER"] = self.treasury
        else:
            env.pop("VOOL_DNA_FEE_TREASURY_OWNER", None)
        return env


@pytest.fixture(scope="module")
def fees(tmp_path_factory):
    from solders.keypair import Keypair

    root = tmp_path_factory.mktemp("usepod-dna-fees")
    node = SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS).start()
    service = daemon = None
    try:
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        pay_to = str(Keypair().pubkey())
        node.fund_sol(pay_to, 5_000_000)
        node.open_token_account(pay_to, USDC_MAINNET_MINT)
        treasury = str(Keypair().pubkey())  # DISPOSABLE FIXTURE RECIPIENT, never the designated production owner
        node.fund_sol(treasury, 5_000_000)
        node.open_token_account(treasury, USDC_MAINNET_MINT)
        service = StrictUsePodService(
            models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
            chain=node, pay_to=pay_to, usdc_mint=USDC_MAINNET_MINT,
        ).start()
        daemon = FeeServedDaemon(root / "home", node.url, treasury)
        daemon.start()
        yield SimpleNamespace(service=service, node=node, daemon=daemon, pay_to=pay_to, treasury=treasury, state={})
    finally:
        if daemon is not None:
            daemon.stop()
        if service is not None:
            service.stop()
        node.stop()


def _setup(fees) -> dict:
    daemon, service, node = fees.daemon, fees.service, fees.node
    status, switched = daemon.door("POST", "/api/wallet/environment", {"environment": "mainnet"})
    assert status == 200, switched
    wallet = ready_pilot_wallet(daemon)
    node.fund_sol(wallet["address"], 50_000_000)
    node.fund_token(wallet["address"], USDC_MAINNET_MINT, 5_000_000)
    status, lane = daemon.call("POST", "/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "x402", "origin": service.origin})
    assert status == 200 and lane["origin"] == service.origin, lane
    for path, body in (("/api/cloud/usepod/refresh", {}), ("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"}), ("/api/cloud/usepod/approve-route", {"model_id": MODEL})):
        status, answer = daemon.call("POST", path, body)
        assert status == 200, (path, answer)
    return wallet


def _consent(daemon, *, per_call: int = 50_000) -> dict:
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": per_call, "max_total_atomic": per_call, "asset": "USDC"})
    assert status == 200, proposed
    return _allow_pending_consent(daemon)


def _operator(daemon, script: str) -> str:
    """The owner's CLI against the daemon's OWN store: a child interpreter with the daemon's home and environment runs
    the money law's operator doors (an operator token, then the write). Never an HTTP door: the money API serves no
    widening, and this is how a bound or a prior accrual reaches a running daemon's ledger."""
    env = daemon.env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", "-c", script], env=env, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip()


def _declare_bound(daemon, numerator: int, denominator: int) -> dict:
    """The operator's conservative conversion bound in the daemon's store: one lamport is worth AT MOST
    numerator/denominator µUSDC (a synthetic figure for the proof, never a price claim), expiring in an hour."""
    out = _operator(daemon, f"""
import json, time
from core.effect_budget import grant_operator_budget_authority
from core.effect_budget_money import AssetIdentity, set_conversion_bound
token = grant_operator_budget_authority(note={NOTE!r})
bound = set_conversion_bound(token, from_asset=AssetIdentity(network={SOLANA_MAINNET!r}, asset="SOL", decimals=9), to_asset=AssetIdentity(network={SOLANA_MAINNET!r}, asset="USDC", decimals=6),
                             numerator={int(numerator)}, denominator={int(denominator)}, expires_epoch=time.time() + 3600.0, note="synthetic served proof bound")
print(json.dumps(bound))
""")
    return json.loads(out)


def _accrue_prior_debt(daemon, payer: str, amount: int) -> str:
    """Prior debt in the daemon's store: one accepted x402 provider payment of ``amount`` µUSDC through the production
    money law (reserve, claim, dispatch, settle) under the fixture treasury, so the next served payment finds
    previously accrued fees to collect. Returns the operation id."""
    return _operator(daemon, f"""
import time, uuid
from core.effect_budget import grant_operator_budget_authority
from core.effect_budget_money import AssetIdentity, MoneyGrantSpec, grant_money_authority
from core.service_fee_ledger import fee_ceiling_atomic
from core.usepod.money_law import EffectBudgetMonetaryAuthority
from core.usepod.monetary import EXACT_COST_NOT_SUPPLIED, ProviderLiability, SettlementEvidence
amount = {int(amount)}
ceiling = amount + fee_ceiling_atomic(amount, 10)
grant_money_authority(grant_operator_budget_authority(note={NOTE!r}), MoneyGrantSpec(
    kind="single_payment", operation_kinds=("inference_x402",), provider_id="usepod", asset=AssetIdentity(network={SOLANA_MAINNET!r}, asset="USDC", decimals=6),
    max_total_atomic=ceiling, per_operation_max_atomic=ceiling, models=({MODEL!r},), routes=("marketplace",), network={SOLANA_MAINNET!r}, payer_account={payer!r},
    fee_asset=AssetIdentity(network={SOLANA_MAINNET!r}, asset="SOL", decimals=9), max_fee_total_atomic=50_000, per_operation_max_fee_atomic=50_000,
    expires_epoch=time.time() + 3600.0, credit_liquidity="not_required", approval_ref="synthetic:" + uuid.uuid4().hex[:8], note={NOTE!r}))
liability = ProviderLiability(
    operation_id="upo_" + uuid.uuid4().hex, provider_id="usepod", transport_mode="x402", asset="USDC", unit="usdc_microunit", account_kind="x402_payer_wallet",
    account_ref={payer!r}, network={SOLANA_MAINNET!r}, max_amount_atomic=amount, model_id={MODEL!r}, route_approval_id="apr", envelope_binding_sha256="c" * 64,
    basis={{"route_class": "marketplace", "quote_id": "q-" + uuid.uuid4().hex[:6], "pay_to": "PayTo" + "9" * 39, "fee_network": {SOLANA_MAINNET!r}, "fee_asset": "SOL", "fee_decimals": 9,
           "fee_max_atomic": 5_000, "principal_balance_atomic": 500_000_000, "fee_balance_atomic": 20_000_000, "wallet_authority_label": "synthetic"}})
authority = EffectBudgetMonetaryAuthority()
reservation = authority.reserve(liability)
authority.claim(reservation, executor="synthetic")
authority.mark_dispatched(reservation)
authority.settle(reservation, SettlementEvidence(operation_id=liability.operation_id, outcome="completed", http_status=200, usage={{}}, input_tokens=None, output_tokens=None,
                                                 upper_bound_cost_atomic=None, exact_cost_atomic=None, exact_cost_state=EXACT_COST_NOT_SUPPLIED, route={{}}, balance_remaining_raw=None, usage_exceeds_liability_bound=False))
print(liability.operation_id)
""")


def _fee_status(daemon) -> dict:
    status, view = daemon.door("GET", "/api/wallet/dna-fees")
    assert status == 200, view
    return view["dna_fees"]


def _pending_state(daemon) -> dict:
    status, view = daemon.door("GET", "/api/wallet/status")
    assert status == 200, view
    return view["status"]


def _paid_turn(fees, text: str, *, expect_confirmed: bool = True) -> dict:
    """One chat turn paid through the wallet: the pending card, its quote (the compact review), the ONE approval, the
    receipt. The approval answer carries the payment's transfer and the companion collection's outcome."""
    daemon, pay_to = fees.daemon, fees.pay_to
    _consent(daemon)
    session_id = _session(f"dna-fee-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    turn = _chat_in_background(daemon, text, session_id)
    pending = _pending_payment(daemon, turn, pay_to)
    view = _pending_state(daemon)
    assert not [row for row in view["pending"] if row.get("origin") == "dna_fee"], "a planned collection never gets a card of its own"
    status, quoted = daemon.door("POST", "/api/wallet/quote", {"proposal_id": pending["proposal_id"]})
    assert status == 200, quoted
    quote = quoted["quote"]
    status, approved = daemon.door("POST", "/api/wallet/approve", {"proposal_id": pending["proposal_id"], "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
    assert status == 200 and (not expect_confirmed or approved["transfer"]["state"] == "confirmed"), approved
    status, answer = _finish(turn)
    events, receipts = _usepod_receipts(daemon, session_id)
    assert status == 200 and _answer_text(answer).strip() and len(receipts) == 1, (status, answer, receipts)
    return {"session_id": session_id, "pending": pending, "quote": quote, "approved": approved, "receipt": receipts[0], "events": events, "paid": int(pending["amount_minor"])}


def test_the_first_paid_turn_states_the_exact_fee_defers_collection_with_its_reason_and_accrues_on_the_receipt(fees) -> None:
    daemon, treasury = fees.daemon, fees.treasury
    wallet = _setup(fees)
    fees.state["wallet"] = wallet
    first = _paid_turn(fees, "Name one reason harbours build breakwaters.")
    paid, fields = first["paid"], first["quote"]["fields"]
    fee = fields["dna_fee"]
    _keep("dna_fee_first_turn.json", {"pending": first["pending"], "quote": first["quote"], "approved": first["approved"], "receipt": first["receipt"]})
    # the preview: the exact fee on THIS payment (never zero), its whole-unit ceiling, the basis, the treasury, the exposure
    assert (fee["rate_bps"], fee["rate_label"], fee["basis_atomic"], fee["fee_numerator"]) == (10, "0.1%", paid, paid * 10), fee
    assert fee["fee_exact"] != "0" and fee["fee_exact_atomic"] != "0" and fee["state"] == "reserved_not_owed_until_accepted"
    assert fee["fee_reserved_ceiling_atomic"] == -(-paid * 10 // 10_000)
    assert (fee["treasury_owner"], fee["treasury_source"], fee["collect_min_atomic"], fee["collect_min_source"]) == (treasury, "operator_override_disposable_recipient", 1, "operator_override")
    assert fields["authorized_exposure_minor"] == paid + fee["fee_reserved_ceiling_atomic"] and fields["fee_max_minor"] == 5_000, "exposure per asset: principal plus fee ceiling in USDC; the network fee in SOL"
    # nothing accrued before this payment: no collection rides it, and the sheet says why
    assert fee["collection"]["with_this_payment"] is False and fee["collection"]["reason"] == "nothing_collectible" and fields["companion"] is None
    review = fields["review"]
    assert review["headline"] == "Pay UsePod for this AI response" and review["primary_action"] == "Approve and pay"
    assert review["fee_line"].startswith("DNA fee: 0.1% — accumulates until economical to collect") and review["collection_line"].startswith("No fees are collected with this payment (nothing collectible)")
    assert [action["kind"] for action in fields["actions"]] == ["provider_payment"] and first["approved"]["companion"] is None
    # the receipt: the fee accrued and OWED, distinct from the payment, the inference and any provider credit; no collection with it
    receipt = first["receipt"]
    block = receipt["dna_fee"]
    assert (block["state"], block["basis_atomic"], block["fee_numerator"], block["rate_bps"], block["treasury_owner"]) == ("accrued_owed", paid, paid * 10, 10, treasury), block
    assert block["fee_exact"] == fee["fee_exact"] and block["state_label"].startswith("accrued: owed")
    assert block["collection"] is not None and block["collection"]["planned"] is False and block["collection"]["reason"] == "not_planned_with_this_payment"
    assert receipt["x402"]["chain_confirmation"]["state"] == "recorded" and receipt["inference"]["state"] == "completed"
    status, found = daemon.call("GET", f"/api/money/liabilities/{receipt['reservation_id']}")
    assert status == 200, found
    lines = {line["flow"]: line for line in found["liability"]["lines"]}
    assert lines["service_fee"]["line_state"] == "exact" and int(lines["service_fee"]["actual_atomic"]) == paid * 10 // 10_000
    ledger = _fee_status(daemon)
    identity = ledger["identities"][0]
    assert (identity["payer_account"], identity["treasury_owner"], identity["asset"], identity["owed_atomic"]) == (wallet["address"], treasury, "USDC", paid * 10 // 10_000), identity
    assert ledger["collection"]["mode"] == "collected_with_the_next_native_payment_when_economical" and ledger["collection"]["cost_bound_bps"] == 100
    assert ledger["collection"]["conversion_bounds"]["USDC"] is None, "no operator bound declared yet: a USDC collection would defer"
    fees.state["first"] = first


def test_previously_accrued_fees_ride_the_next_payment_under_one_pin_and_the_treasury_receives_exactly_them(fees) -> None:
    daemon, node, treasury = fees.daemon, fees.node, fees.treasury
    wallet = fees.state["wallet"]
    # prior debt through the money law in the daemon's own store (0.1% of 200 USDC of accepted payments = 0.2 USDC),
    # and the operator's bound: 5,000 lamports valued at 5 µUSDC, far under 1% of what is collectible
    _accrue_prior_debt(daemon, wallet["address"], 200_000_000)
    bound = _declare_bound(daemon, 1, 1_000)
    before = _fee_status(daemon)
    collectible = int(before["identities"][0]["collectible_atomic"])
    assert collectible >= 200_000 and before["collection"]["conversion_bounds"]["USDC"]["numerator"] == 1
    sends, treasury_before = node.distinct_sends(), node.token_balance(treasury, USDC_MAINNET_MINT)
    turn = _paid_turn(fees, "Name one thing a harbour master checks at dawn.")
    fields = turn["quote"]["fields"]
    companion = fields["companion"]
    _keep("dna_fee_composed_turn.json", {"pending": turn["pending"], "quote": turn["quote"], "approved": turn["approved"], "receipt": turn["receipt"], "bound": bound, "before": before})
    # the sheet: both actions under one approval, the collection's own network fee, per-asset maxima
    assert companion is not None and (companion["amount_minor"], companion["asset"], companion["treasury_owner"]) == (collectible, "USDC", treasury), companion
    assert companion["treasury_token_account"] == node.token_account_address(treasury, USDC_MAINNET_MINT) and companion["economics"]["ok"] is True
    assert fields["dna_fee"]["collection"]["with_this_payment"] is True and [a["kind"] for a in fields["actions"]] == ["provider_payment", "fee_collection"]
    review = fields["review"]
    assert review["collection_line"].startswith("Previously accrued fees collected now:") and "for both transactions" in review["network_line"]
    assert [(row["asset"], row["minor"]) for row in review["max_debits"]] == [("USDC", turn["paid"] + collectible), ("SOL", 10_000)]
    # ONE PIN: the payment confirmed, the collection confirmed, two transactions, exactly the collected amount at the treasury
    approved = turn["approved"]
    outcome = approved["companion"]
    assert approved["transfer"]["state"] == "confirmed" and outcome["collected"] is True and outcome["state"] == "confirmed" and outcome["amount_atomic"] == collectible, approved
    assert outcome["tx_signature"] and outcome["tx_signature"] != approved["transfer"]["tx_id"]
    assert node.distinct_sends() == sends + 2 and node.token_balance(treasury, USDC_MAINNET_MINT) - treasury_before == collectible
    # the receipt of the turn says what was collected with it; the ledger retired exactly that, once; the carry stays
    block = turn["receipt"]["dna_fee"]
    assert block["state"] == "accrued_owed" and block["collection"]["planned"] is True and block["collection"]["collected"] is True and block["collection"]["amount_atomic"] == collectible
    assert block["collection"]["tx_signature"] == outcome["tx_signature"]
    after = _fee_status(daemon)
    identity = after["identities"][0]
    from core.service_fee_ledger import SCALE, numerator_decimal

    assert identity["retired_exact"] == numerator_decimal(collectible * SCALE, 6) and identity["open_collection"] is None
    assert int(identity["owed_atomic"]) == turn["paid"] * 10 // 10_000 + (int(before["identities"][0]["owed_atomic"]) - collectible), "this payment's own fee stays accrued; the prior debt was collected"
    # both transactions have their own record; the collection's names the payment it rode; no card of its own ever
    status, transfers = daemon.door("GET", f"/api/wallet/transfers?proposal_id={companion['proposal_id']}")
    assert status == 200 and transfers["transfer"]["state"] == "confirmed" and transfers["transfer"]["origin"] == "dna_fee"
    assert transfers["transfer"]["dna_fee_collection"]["payment_proposal_id"] == turn["pending"]["proposal_id"]
    status, receipts = daemon.door("GET", "/api/wallet/receipts")
    fee_receipts = [row for row in receipts["receipts"] if row.get("proposal_id") == companion["proposal_id"]]
    assert fee_receipts and fee_receipts[0]["origin"] == "dna_fee" and fee_receipts[0]["dna_fee_collection"]["state"] == "confirmed"
    # the same approval again answers with both outcomes and sends nothing more
    status, again = daemon.door("POST", "/api/wallet/approve", {"proposal_id": turn["pending"]["proposal_id"], "quote_id": turn["quote"]["quote_id"], "quote_digest": turn["quote"]["digest"], "pin": PIN})
    assert status == 200 and again["duplicate"] is True and again["companion"]["collected"] is True and node.distinct_sends() == sends + 2
    fees.state["composed"] = turn


def test_a_costly_collection_defers_with_its_reason_and_the_ledger_survives_a_restart(fees) -> None:
    daemon, node, treasury = fees.daemon, fees.node, fees.treasury
    wallet = fees.state["wallet"]
    _accrue_prior_debt(daemon, wallet["address"], 3_000_000)  # 3,000 µUSDC more owed
    _declare_bound(daemon, 1, 1)  # an expensive bound: 5,000 lamports valued at 5,000 µUSDC, far over 1% of what is owed
    sends, treasury_before = node.distinct_sends(), node.token_balance(treasury, USDC_MAINNET_MINT)
    turn = _paid_turn(fees, "Name one bird that follows fishing boats.")
    fields = turn["quote"]["fields"]
    _keep("dna_fee_deferred_turn.json", {"quote": turn["quote"], "approved": turn["approved"], "receipt": turn["receipt"]})
    collection = fields["dna_fee"]["collection"]
    assert collection["with_this_payment"] is False and collection["reason"] == "collection_cost_exceeds_bound" and collection["economics"]["cost_bps_of_amount"] > 100
    assert fields["companion"] is None and turn["approved"]["companion"] is None
    assert node.distinct_sends() == sends + 1 and node.token_balance(treasury, USDC_MAINNET_MINT) == treasury_before, "the payment alone; the debt stays"
    assert turn["receipt"]["dna_fee"]["collection"]["planned"] is False
    before = _fee_status(daemon)
    daemon.stop()
    daemon.start()
    after = _fee_status(daemon)
    _keep("dna_fee_restart.json", {"before": before, "after": after})
    assert after["identities"] == before["identities"] and after["collections"] == before["collections"], "the fraction, the retired amount and every collection record are on disk"
    # the daemon restarted WITHOUT the override: the designated production owner is bound; nothing accrued under its
    # identity, so a payment's quote defers with that reason and sends only the payment
    daemon.stop()
    daemon.override_treasury = False
    try:
        daemon.start()
        designated = _fee_status(daemon)
        assert (designated["treasury"]["owner"], designated["treasury"]["source"], designated["treasury"]["state"]) == (DESIGNATED_OWNER, "designated_production_owner", "configured")
        _consent(daemon)
        session_id = _session(f"dna-fee-designated-{uuid.uuid4()}")
        _pin_for(daemon, session_id)
        pending_turn = _chat_in_background(daemon, "Name one tool a rope maker uses.", session_id)
        pending = _pending_payment(daemon, pending_turn, fees.pay_to)
        status, quoted = daemon.door("POST", "/api/wallet/quote", {"proposal_id": pending["proposal_id"]})
        assert status == 200, quoted
        _keep("dna_fee_designated_quote.json", {"quote": quoted["quote"], "ledger": designated})
        plan = quoted["quote"]["fields"]["dna_fee"]["collection"]
        assert plan["with_this_payment"] is False and plan["reason"] == "nothing_collectible" and quoted["quote"]["fields"]["dna_fee"]["treasury_owner"] == DESIGNATED_OWNER
        status, rejected = daemon.door("POST", "/api/wallet/reject", {"proposal_id": pending["proposal_id"]})
        assert status == 200 and rejected.get("rejected") is True, rejected
        _finish(pending_turn)
        assert node.distinct_sends() == sends + 1
        assert any(row["treasury_owner"] == fees.treasury for row in designated["identities"]), "accrual bound to the fixture treasury never migrates"
    finally:
        daemon.stop()
        daemon.override_treasury = True
        daemon.start()
    assert _fee_status(daemon)["treasury"]["owner"] == fees.treasury


def test_the_settings_door_owns_the_threshold_and_rejecting_the_payment_releases_the_planned_collection(fees) -> None:
    daemon, node = fees.daemon, fees.node
    status, policy = daemon.door("POST", "/api/wallet/dna-fees/policy", {"asset": "USDC"})
    assert status == 200 and policy["saved"] is None and policy["override_in_force"] is True, policy
    assert policy["collect_min"] == {"asset": "USDC", "atomic": 1, "source": "operator_override", "settings_atomic": None, "default_atomic": 100_000}, policy
    status, saved = daemon.door("POST", "/api/wallet/dna-fees/policy", {"asset": "USDC", "collect_min_atomic": 7})
    assert status == 200 and saved["saved"] == {"asset": "USDC", "atomic": 7, "source": "settings"} and saved["override_in_force"] is True, saved
    status, bad = daemon.door("POST", "/api/wallet/dna-fees/policy", {"asset": "USDC", "collect_min_atomic": 0})
    assert status != 200 and bad.get("error") == "wallet_amount_invalid"
    # a plan rides the next payment's quote; rejecting that payment on its card releases the plan and its proposal
    _declare_bound(daemon, 1, 1_000)
    _consent(daemon)
    session_id = _session(f"dna-fee-reject-{uuid.uuid4()}")
    _pin_for(daemon, session_id)
    turn = _chat_in_background(daemon, "Name one knot sailors tie first.", session_id)
    pending = _pending_payment(daemon, turn, fees.pay_to)
    status, quoted = daemon.door("POST", "/api/wallet/quote", {"proposal_id": pending["proposal_id"]})
    assert status == 200, quoted
    companion = quoted["quote"]["fields"]["companion"]
    assert companion is not None and companion["amount_minor"] >= 3_000, quoted["quote"]["fields"]["dna_fee"]["collection"]
    row = next(r for r in _pending_state(daemon)["pending"] if r["proposal_id"] == pending["proposal_id"])
    assert row["companion_collection"]["state"] == "offered" and row["companion_collection"]["collection_id"] == companion["collection_id"]
    sends = node.distinct_sends()
    status, rejected = daemon.door("POST", "/api/wallet/reject", {"proposal_id": pending["proposal_id"]})
    assert status == 200 and rejected.get("rejected") is True and rejected["dna_fee_collection"]["state"] == "released", rejected
    _finish(turn)
    after = _fee_status(daemon)
    _keep("dna_fee_rejected_plan.json", {"pending": pending, "quote": quoted["quote"], "rejected": rejected, "ledger": after})
    released = [r for r in after["collections"] if r["collection_id"] == companion["collection_id"]]
    assert released and released[0]["state"] == "released" and after["identities"][0]["open_collection"] is None
    assert int(after["identities"][0]["collectible_atomic"]) == int(after["identities"][0]["owed_atomic"]) and node.distinct_sends() == sends, "nothing sent; the debt is back"
    status, gone = daemon.door("GET", f"/api/wallet/proposals/{companion['proposal_id']}")
    assert status == 200 and gone["proposal"]["state"] == "expired"
