"""A pay-kit payment from a Crypto Pilot wallet, paid from the Pilot sheet.

The owner's request meets a 402 -> pay-kit reads the offer -> ONE capped proposal bound to that exact request -> the
Pilot quote shows the request, the payee and who pays the network fee -> one PIN -> the Pilot claim holds the amount
and this wallet's fee ceiling -> pay-kit builds the payment inside the signing session over the blockhash the lane read
and the key signs only through the guard -> the stored request goes once with the payment -> the chain, never the
resource, decides: only a transaction carrying this wallet's own signature over exactly the signed bytes becomes the
transfer's id, and the observer settles the hold from it.

SIMULATED CHAIN: the scripted node answers as Solana Devnet; the mainnet drives run on the loopback node that executes
what lands (answering as Mainnet). Resources are loopback stand-ins built from pay-kit's own wire helpers. The wallet's
own custody signs with its real sealed key throughout.
"""
from __future__ import annotations

import json
import sys
import uuid

import pytest

from tests.wallet._rig_paykit import MAINNET_CAIP2, PAID_BODY, ScriptedMppResource, ScriptedPayKitResource
from tests.wallet._simulated_solana import MAINNET_GENESIS, USDC_MAINNET_MINT, SimulatedSolanaNode
from tests.wallet.test_crypto_pilot_dispatch import (
    PIN,
    SOLANA_DEVNET,
    WRONG_PIN,
    _hold,
    _ready_pilot_wallet,
    _sol_key,
    node,  # the scripted node the pilot fixture runs on
    pilot,  # Crypto on, Test networks, Solana Devnet's lane ready
)

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]
AMOUNT = 700_000  # lamports, under the cap the capped fixture sets
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
USDC_AMOUNT = 250_000  # 0.25 USDC


@pytest.fixture
def capped(pilot, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 2))
    return pilot


@pytest.fixture
def mainnet(monkeypatch, tmp_path):
    """Crypto on, Mainnet selected, the executing node as Solana Mainnet's endpoint, USDC minted on it."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(USDC_AMOUNT * 2))
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as chain:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: chain.url}))
        environment.set_active_environment("mainnet")
        chain.create_mint(USDC_MAINNET_MINT, decimals=6)
        yield chain
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _mainnet_wallet(chain, *, usdc: int = 1_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"m-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    chain.fund_sol(wallet["address"], 20_000_000)
    chain.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _site_account(chain) -> str:
    owner = _sol_key()
    chain.fund_sol(owner, 5_000_000)
    chain.open_token_account(owner, USDC_MAINNET_MINT)
    return owner


def _park(resource, wallet, *, method="GET", body=b""):
    from core.wallet import paykit_x402, x402

    parked = paykit_x402.fetch_paid(resource.url, wallet_id=wallet["wallet_id"], method=method, body=body)
    assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED and parked.http_status == 402, parked
    return parked


def _quote(proposal_id: str) -> dict:
    from core.wallet import quotes

    return quotes.mint_quote(proposal_id)


def _approve(proposal_id: str, quote: dict, pin: str = PIN) -> dict:
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(pin))


def _row(proposal_id: str) -> dict:
    from core.wallet import transfers

    return transfers.get_transfer_by_id(proposal_id) or {}


def _message_blockhash(rpc_transactions: dict, tx_id: str) -> str:
    from solders.transaction import VersionedTransaction

    return str(VersionedTransaction.from_bytes(rpc_transactions[tx_id]).message.recent_blockhash)


# --- the working path ---------------------------------------------------------------------------------------------

def test_an_x402_request_is_quoted_approved_once_paid_once_and_settled_from_the_chain(capped):
    from core.wallet import paykit_x402, proposals, status, x402
    from core.wallet.errors import WalletFault

    node = capped
    wallet = _ready_pilot_wallet()
    payee = _sol_key()
    with ScriptedPayKitResource(node, amount_minor=AMOUNT, pay_to=payee) as resource:
        parked = _park(resource, wallet)
        proposal = proposals.get_proposal(parked.proposal_id)
        assert (proposal.origin, proposal.state, proposal.amount_minor, proposal.destination) == (proposals.ORIGIN_X402_PAYKIT, proposals.STATE_PENDING_APPROVAL, AMOUNT, payee)
        pending = [row for row in status.wallet_status()["pending"] if row["proposal_id"] == parked.proposal_id]
        assert pending and pending[0]["pilot_transfer"] is True, "the pending card opens the Pilot sheet"

        quote = _quote(parked.proposal_id)
        fields = quote["fields"]
        block = fields["paykit"]
        assert (block["protocol"], block["sponsored"], block["fee_payer"], block["payer_fee_max_minor"]) == ("x402", True, str(resource.fee_payer.pubkey()), 0)
        assert block["url"] == resource.url and block["method"] == "GET"
        assert (fields["fee_estimate_minor"], fields["fee_max_minor"], fields["to_address"], fields["amount_minor"]) == (0, 0, payee, AMOUNT)
        review = fields["review"]
        assert review["headline"].startswith("Pay http://127.0.0.1:") and review["network_line"].startswith("Network fee: paid by the site")
        assert review["actions"][0]["kind"] == "paid_request" and review["primary_action"] == "Approve and pay"
        assert resource.paid_requests == [], "a quote sends nothing"

        result = _approve(parked.proposal_id, quote)
        transfer = result["transfer"]
        assert result["duplicate"] is False and result["paykit"]["delivered"] is True and result["paykit"]["resource_status"] == 200
        assert len(resource.paid_requests) == 1 and resource.paid_requests[0]["settled"], "exactly one paid request"
        tx_id = resource.landed[0]
        assert (transfer["state"], transfer["tx_id"], transfer["charged_fee_minor"]) == ("confirmed", tx_id, "0")
        row = _row(parked.proposal_id)
        assert row["payer_signature"] and row["payer_signature"] != tx_id, "the id is the fee payer's signature; this wallet's own is kept"
        assert _message_blockhash(node.transactions, tx_id) == row["blockhash"], "the payment was signed over the blockhash the claim recorded"
        assert _hold(parked.proposal_id) == (AMOUNT, 0, "settled"), "the amount settles, and no fee: the site paid it"
        assert node.send_count() == 0, "the wallet broadcasts nothing of its own for a pay-kit payment"
        handed = x402.retry_paid_resource(parked.proposal_id)
        assert (handed.status, handed.body, handed.tx_signature) == (x402.OUTCOME_DELIVERED, PAID_BODY, tx_id), "the caller's retry gets the paid body, sending nothing"
        assert len(resource.paid_requests) == 1
        binding = x402.binding_for_proposal(parked.proposal_id)
        assert (binding["state"], binding["tx_signature"], binding["resource_status"]) == (x402.BINDING_DELIVERED, tx_id, 200)
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_CONFIRMED

        repeat = _approve(parked.proposal_id, quote)
        assert repeat["duplicate"] is True and len(resource.paid_requests) == 1, "the same approval again pays nothing"
        with pytest.raises(WalletFault) as again:
            paykit_x402.fetch_paid(resource.url, wallet_id=wallet["wallet_id"], method="GET", body=b"")
        assert again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.paid_requests) == 1


@pytest.mark.parametrize("sponsored", [True, False], ids=["site-pays-fee", "wallet-pays-fee"])
def test_an_mpp_charge_is_paid_from_the_pilot_sheet_and_the_fee_settles_at_what_the_chain_charged(capped, sponsored):
    from core.wallet import paykit_x402, proposals, x402

    node = capped
    wallet = _ready_pilot_wallet()
    with ScriptedMppResource(node, amount_minor=AMOUNT, recipient=_sol_key(), sponsored=sponsored) as resource:
        parked = _park(resource, wallet, method="POST", body=b'{"q": 1}')
        assert proposals.get_proposal(parked.proposal_id).origin == proposals.ORIGIN_MPP_PAYKIT
        quote = _quote(parked.proposal_id)
        block = quote["fields"]["paykit"]
        binding = x402.binding_for_proposal(parked.proposal_id)
        fee_max = 0 if sponsored else int(binding["max_network_fee_minor"])
        assert (block["protocol"], block["sponsored"], block["payer_fee_max_minor"]) == ("mpp", sponsored, fee_max)
        assert quote["fields"]["fee_max_minor"] == fee_max and (sponsored or fee_max > 0)
        if not sponsored:
            assert not quote["fields"]["review"]["network_line"].startswith("Network fee: paid by the site")

        result = _approve(parked.proposal_id, quote)
        assert result["paykit"]["delivered"] is True and result["paykit"]["protocol"] == "mpp"
        assert len(resource.paid_requests) == 1 and resource.paid_requests[0]["body"] == b'{"q": 1}'
        tx_id = resource.landed[0]
        row = _row(parked.proposal_id)
        assert (row["state"], row["tx_id"]) == ("confirmed", tx_id)
        assert (row["payer_signature"] == tx_id) is not sponsored, "unsponsored: the id is this wallet's own signature"
        assert _message_blockhash(node.transactions, tx_id) == row["blockhash"]
        assert _hold(parked.proposal_id) == (AMOUNT, 0 if sponsored else node.transaction_fee, "settled")
        assert paykit_x402.delivered_body(parked.proposal_id) == PAID_BODY and node.send_count() == 0


@pytest.mark.parametrize("protocol", ["x402", "mpp"])
def test_a_mainnet_usdc_payment_moves_exactly_the_approved_amount_and_the_site_pays_the_fee(mainnet, protocol):
    from core.wallet.store import connection

    chain = mainnet
    wallet = _mainnet_wallet(chain)
    site = _site_account(chain)
    if protocol == "x402":
        resource = ScriptedPayKitResource(chain, amount_minor=USDC_AMOUNT, asset=USDC_MAINNET_MINT, pay_to=site, network=MAINNET_CAIP2)
    else:
        resource = ScriptedMppResource(chain, amount_minor=USDC_AMOUNT, currency=USDC_MAINNET_MINT, recipient=site, network="mainnet", decimals=6)
    chain.fund_sol(str(resource.fee_payer.pubkey()), 5_000_000)
    with resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        fields = quote["fields"]
        assert (fields["asset"], fields["amount_minor"], fields["fee_max_minor"], fields["paykit"]["protocol"]) == ("USDC", USDC_AMOUNT, 0, protocol)
        result = _approve(parked.proposal_id, quote)
        assert result["transfer"]["state"] == "confirmed" and result["paykit"]["delivered"] is True, result
        tx_id = resource.landed[0]
        assert result["transfer"]["tx_id"] == tx_id and chain.payment_to(tx_id) is not None
        assert (chain.token_balance(site, USDC_MAINNET_MINT), chain.token_balance(wallet["address"], USDC_MAINNET_MINT)) == (USDC_AMOUNT, 1_000_000 - USDC_AMOUNT)
        assert chain.sol_balance(wallet["address"]) == 20_000_000, "the site's fee payer paid the network fee"
        assert chain.distinct_sends() == 0 and len(resource.paid_requests) == 1
    with connection() as conn:
        holds = [tuple(r) for r in conn.execute("SELECT asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
                                                 (parked.proposal_id, f"{parked.proposal_id}:fee")).fetchall()]
    assert holds[0] == ("USDC", USDC_AMOUNT, 0, "settled") and all(fee == 0 for _a, _m, fee, _s in holds), holds


# --- refused before anything is held or sent ----------------------------------------------------------------------

class _CountingApprover:
    def __init__(self, pin: str = PIN) -> None:
        from core.wallet import approval

        self._inner = approval.PinApprover(pin)
        self.asked = 0

    def approve(self, challenge):
        self.asked += 1
        return self._inner.approve(challenge)


def _approve_counting(proposal_id: str, quote: dict, approver: _CountingApprover) -> dict:
    from core.wallet import lifecycle

    return lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approver)


@pytest.mark.parametrize("protocol", ["x402", "mpp"])
@pytest.mark.parametrize("why", ["setup_unfinished", "row_not_ready"])
def test_a_pilot_wallet_the_sheet_cannot_pay_from_is_refused_before_any_proposal(capped, monkeypatch, protocol, why):
    from core.wallet import capabilities, paykit_x402, pilot_custody, proposals
    from core.wallet.errors import WalletFault

    if why == "setup_unfinished":
        wallet = pilot_custody.create_pilot_wallet(network=SOLANA_DEVNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"u-{uuid.uuid4().hex}")
    else:
        wallet = _ready_pilot_wallet()
        monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset())
    payee = _sol_key()
    resource = ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=payee) if protocol == "x402" else ScriptedMppResource(capped, amount_minor=AMOUNT, recipient=payee)
    with resource:
        with pytest.raises(WalletFault) as exc:
            paykit_x402.fetch_paid(resource.url, wallet_id=wallet["wallet_id"], method="GET", body=b"")
        assert (exc.value.code, exc.value.context["reason"]) == ("x402_scheme_unavailable", "paykit_pilot_lane_not_supported")
        assert proposals.list_proposals() == [] and resource.paid_requests == []
    assert capped.send_count() == 0


def test_a_paykit_proposal_without_its_request_is_paid_by_no_door(capped):
    """However a pay-kit proposal reached a Pilot wallet without the request it pays for, no door pays it: the quote
    sheet mints nothing, the Pilot approval refuses before the credential, and the legacy door names the sheet."""
    from core.wallet import approval, capabilities, lifecycle, proposals
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=AMOUNT, asset="SOL",
                                             origin=proposals.ORIGIN_X402_PAYKIT, network=SOLANA_DEVNET)
    engine = lifecycle.default_lifecycle()
    assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    with pytest.raises(WalletFault) as quote:
        _quote(proposal.proposal_id)
    assert (quote.value.code, quote.value.context["reason"]) == ("wallet_quote_unavailable", "paykit_binding_missing")
    approver = _CountingApprover()
    with pytest.raises(WalletFault):
        engine.approve_pilot_transfer(proposal.proposal_id, quote_id="q-none", quote_digest="0" * 64, approver=approver)
    assert approver.asked == 0
    with pytest.raises(WalletFault) as legacy:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert legacy.value.context["reason"] == capabilities.REASON_PILOT_TRANSFER_NEEDS_QUOTE_APPROVAL
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert _hold(proposal.proposal_id) is None and capped.send_count() == 0


def test_a_wrong_pin_holds_and_sends_nothing_and_the_right_one_then_pays_once(capped):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        with pytest.raises(WalletFault) as refused:
            _approve(parked.proposal_id, quote, pin=WRONG_PIN)
        assert refused.value.code == "wallet_pin_invalid"
        assert proposals.approval_refusals(parked.proposal_id) == 1
        assert (_hold(parked.proposal_id), _row(parked.proposal_id), resource.paid_requests) == (None, {}, [])
        result = _approve(parked.proposal_id, quote)
        assert result["paykit"]["delivered"] is True and len(resource.paid_requests) == 1


@pytest.mark.parametrize("change", ["fee_payer", "url", "max_network_fee_minor"])
def test_a_binding_changed_after_the_quote_is_refused_before_the_pin(capped, change):
    from core.wallet import paykit_x402, x402
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        binding = paykit_x402.binding_for(parked.proposal_id)
        value = {"fee_payer": "", "url": resource.url + "?other", "max_network_fee_minor": 9_999}[change]
        x402._update_binding(binding["request_digest"], **{change: value})
        if change == "max_network_fee_minor":
            x402._update_binding(binding["request_digest"], fee_payer="")  # an unsponsored payment's fee ceiling is in the block
        approver = _CountingApprover()
        with pytest.raises(WalletFault) as refused:
            _approve_counting(parked.proposal_id, quote, approver)
        assert (refused.value.code, refused.value.context["reason"]) == ("wallet_quote_mismatch", "paykit_binding_changed_since_the_quote")
        assert approver.asked == 0, "no credential is asked for a payment that is not the one the sheet showed"
        assert (_hold(parked.proposal_id), _row(parked.proposal_id), resource.paid_requests) == (None, {}, [])


def test_without_paykit_the_quote_and_the_approval_refuse_typed(capped, monkeypatch):
    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        # the extra goes missing (an install without it): its import fails as a missing package does; nothing in the
        # wallet is patched
        monkeypatch.setitem(sys.modules, "solana_pay_kit.protocols.x402.client.exact.payment", None)
        assert paykit_x402.availability() == (False, "paykit_not_installed")
        with pytest.raises(WalletFault) as quoted:
            _quote(parked.proposal_id)
        assert quoted.value.code == "wallet_paykit_unavailable"
        approver = _CountingApprover()
        with pytest.raises(WalletFault) as refused:
            _approve_counting(parked.proposal_id, quote, approver)
        assert refused.value.code == "wallet_paykit_unavailable" and approver.asked == 0
        assert (_hold(parked.proposal_id), resource.paid_requests) == (None, [])


def test_with_its_channel_off_the_payment_is_refused_before_the_pin(capped):
    from core.wallet import channels
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        channels.set_on("paykit_x402", False)
        approver = _CountingApprover()
        with pytest.raises(WalletFault) as refused:
            _approve_counting(parked.proposal_id, quote, approver)
        assert refused.value.code == "wallet_payment_channel_off" and approver.asked == 0
        assert (_hold(parked.proposal_id), resource.paid_requests) == (None, [])


def test_an_mpp_challenge_that_expired_after_the_quote_is_refused_before_the_pin(capped):
    from core.wallet import paykit_x402, x402
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedMppResource(capped, amount_minor=AMOUNT, recipient=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        binding = paykit_x402.binding_for(parked.proposal_id)
        offer = json.loads(binding["offer_json"])
        offer["challenge"]["expires"] = "2020-01-01T00:00:00Z"
        x402._update_binding(binding["request_digest"], offer_json=json.dumps(offer))
        approver = _CountingApprover()
        with pytest.raises(WalletFault) as refused:
            _approve_counting(parked.proposal_id, quote, approver)
        assert (refused.value.code, refused.value.context["reason"], approver.asked) == ("wallet_quote_expired", "mpp_challenge_expired", 0)
        with pytest.raises(WalletFault) as requoted:
            _quote(parked.proposal_id)
        assert requoted.value.context["reason"] == "mpp_challenge_expired", "a fresh sheet says why it cannot be paid"
        assert (_hold(parked.proposal_id), resource.paid_requests) == (None, [])


# --- after the claim: the guard, the send guard, and the chain deciding --------------------------------------------

def test_a_stored_offer_tampered_after_the_quote_is_never_signed_and_the_hold_returns(capped):
    from core.wallet import paykit_x402, proposals, x402
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        binding = paykit_x402.binding_for(parked.proposal_id)
        offer = json.loads(binding["offer_json"])
        offer["requirement"]["amount"] = str(AMOUNT + 1)
        x402._update_binding(binding["request_digest"], offer_json=json.dumps(offer))
        with pytest.raises(WalletFault) as refused:
            _approve(parked.proposal_id, quote)
        assert (refused.value.code, refused.value.context["reason"]) == ("wallet_signature_invalid", "paykit_transfer_amount_differs")
        row = _row(parked.proposal_id)
        assert (row["state"], row["tx_id"], row["payer_signature"]) == ("released", None, ""), "nothing was signed"
        assert _hold(parked.proposal_id)[2] == "released" and proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
        assert resource.paid_requests == [] and resource.landed == []


def test_a_channel_turned_off_after_the_signature_sends_nothing_and_the_hold_returns_once_the_blockhash_expires(capped, monkeypatch):
    from core.wallet import channels, paykit_x402, proposals, settlement
    from core.wallet.errors import WalletFault

    node = capped
    wallet = _ready_pilot_wallet()
    real_build = paykit_x402.build_payment_header

    def build_then_turn_off(*args, **kwargs):
        built = real_build(*args, **kwargs)
        channels.set_on("paykit_x402", False)  # the owner turns the channel off while the payment is being signed
        return built

    monkeypatch.setattr(paykit_x402, "build_payment_header", build_then_turn_off)
    with ScriptedPayKitResource(node, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        with pytest.raises(WalletFault) as refused:
            _approve(parked.proposal_id, _quote(parked.proposal_id))
        assert refused.value.code == "wallet_payment_channel_off"
        row = _row(parked.proposal_id)
        assert (row["state"], bool(row["payer_signature"]), row["attempts_sent"]) == ("signed_revoked", True, 0)
        assert _hold(parked.proposal_id)[2] == "reserved", "a signed authorization keeps its hold until it provably cannot land"
        assert resource.requests and not any(r["paid"] for r in resource.requests), "the payment never left"
        node.block_height = int(row["last_valid_block_height"]) + 1
        settlement.observe_open_transfers()
        assert _row(parked.proposal_id)["state"] == "released"
        assert _hold(parked.proposal_id)[2] == "released" and proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED


@pytest.mark.parametrize("mode", ["no_settlement_header", "wrong_settlement"])
def test_a_payment_the_site_does_not_name_is_found_on_chain_by_this_wallets_own_signature(capped, mode):
    from core.wallet import proposals, x402

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key(), mode=mode) as resource:
        parked = _park(resource, wallet)
        result = _approve(parked.proposal_id, _quote(parked.proposal_id))
        tx_id = resource.landed[0]
        assert (result["transfer"]["state"], result["transfer"]["tx_id"]) == ("confirmed", tx_id)
        assert result["paykit"]["claimed_transaction_unproven"] is (mode == "wrong_settlement"), "a named id the chain does not prove is never believed"
        evidence = [entry["kind"] for entry in json.loads(_row(parked.proposal_id)["evidence_json"])]
        assert "paykit_transaction_found" in evidence and "paykit_transaction_proven" not in evidence
        assert _hold(parked.proposal_id) == (AMOUNT, 0, "settled") and x402.binding_for_proposal(parked.proposal_id)["tx_signature"] == tx_id
        assert proposals.get_proposal(parked.proposal_id).tx_signature == tx_id, "the record names the transaction the chain proved"


def test_an_earlier_payment_the_site_names_again_is_never_taken_for_this_one(capped):
    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT // 2, pay_to=_sol_key()) as resource:
        first = _park(resource, wallet)
        assert _approve(first.proposal_id, _quote(first.proposal_id))["transfer"]["tx_id"] == resource.landed[0]
        resource.mode = "names_earlier_payment"
        second = _park(resource, wallet, method="POST", body=b'{"page": 2}')
        result = _approve(second.proposal_id, _quote(second.proposal_id))
        assert len(resource.landed) == 2
        assert result["paykit"]["claimed_transaction_unproven"] is True
        assert (result["transfer"]["state"], result["transfer"]["tx_id"]) == ("confirmed", resource.landed[1]), "this payment's id is the one carrying its own signature"
        assert _row(first.proposal_id)["tx_id"] == resource.landed[0]


def test_a_payment_the_site_never_lands_stays_counted_until_the_owner_stops_waiting(capped):
    from solders.keypair import Keypair

    from core.wallet import settlement

    node = capped
    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(node, amount_minor=AMOUNT, pay_to=_sol_key()) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        resource.fee_payer = Keypair()  # the site no longer cosigns as the fee payer it offered: nothing lands
        result = _approve(parked.proposal_id, quote)
        assert (result["transfer"]["state"], result["transfer"]["tx_id"], result["paykit"]["resource_status"]) == ("unknown", "", 402), result
        assert resource.landed == [] and len(resource.paid_requests) == 1
        assert _hold(parked.proposal_id)[2] == "reserved", "a payment that left is never released on the site's word"
        assert result["transfer"]["offered_exit"] == ""
        node.block_height = int(_row(parked.proposal_id)["last_valid_block_height"]) + 1
        settlement.observe_open_transfers()
        assert _row(parked.proposal_id)["state"] == "unknown"
        stopped = settlement.owner_stop_waiting(parked.proposal_id)
        assert stopped["state"] == "stopped_waiting" and _hold(parked.proposal_id) == (AMOUNT, 0, "settled"), "the liability keeps counting"


def test_a_payment_that_failed_on_chain_returns_the_amount_once_final_and_the_request_can_be_paid_again(capped):
    from core.wallet import proposals, settlement

    node = capped
    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(node, amount_minor=AMOUNT, pay_to=_sol_key(), mode="settle_fails") as resource:
        parked = _park(resource, wallet)
        result = _approve(parked.proposal_id, _quote(parked.proposal_id))
        assert (result["transfer"]["state"], result["transfer"]["tx_id"], result["paykit"]["delivered"]) == ("pending", resource.landed[0], False)
        assert _hold(parked.proposal_id)[2] == "reserved", "a failure seen at confirmed is not final: nothing is released yet"
        node.status_mode = "finalized"
        settlement.observe_open_transfers()
        assert _row(parked.proposal_id)["state"] == "failed_on_chain"
        assert _hold(parked.proposal_id) == (0, 0, "settled"), "the amount returns; the site's fee payer paid the failed transaction's fee"
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
        again = _park(resource, wallet)
        assert again.proposal_id != parked.proposal_id, "the same request after a failed payment needs a new approval"


def test_a_payment_whose_answer_was_lost_is_settled_from_the_chain_and_not_delivered(capped):
    from core.wallet import paykit_x402, x402

    wallet = _ready_pilot_wallet()
    with ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=_sol_key(), paid_answer="drop") as resource:
        parked = _park(resource, wallet)
        result = _approve(parked.proposal_id, _quote(parked.proposal_id))
        assert result["paykit"]["unanswered"].startswith("submit_unknown:") and result["paykit"]["delivered"] is False
        assert (result["transfer"]["state"], result["transfer"]["tx_id"]) == ("confirmed", resource.landed[0])
        assert x402.binding_for_proposal(parked.proposal_id)["state"] == x402.BINDING_PAID and paykit_x402.delivered_body(parked.proposal_id) is None
        assert len(resource.paid_requests) == 1
