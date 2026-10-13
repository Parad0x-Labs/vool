"""An x402 payment on Base through the x402 Foundation SDK, paid from a Crypto Pilot wallet.

The owner's request meets a v2 402 offering USDC on Base -> ONE capped proposal bound to that exact request -> the
Pilot quote shows the request, the token and that the site's facilitator pays the network fee -> one PIN -> the Pilot
claim holds the amount (no fee) and takes no account nonce -> the SDK builds the payment inside the signing session
and the key signs ONE EIP-3009 authorization, only through the guard -> the stored request goes once with it -> the
chain, never the resource, decides: only a successful transaction carrying ``AuthorizationUsed(this wallet, this
nonce)`` and the exact Transfer becomes the id; an authorization never used ends, failed with nothing moved and no
fee, only once its window closed at a finalized block.

SIMULATED CHAIN: the loopback node answers as Base Sepolia or Base and runs USDC as the token runs it (the signature
recovered over the token's own EIP-712 domain, the window against the block's timestamp, a used nonce refused). The
resource builds and reads the x402 headers with the SDK's own encoders; its facilitator submits on that chain. The
wallet's own custody signs with its real sealed key throughout.
"""
from __future__ import annotations

import json
import uuid

import pytest

from tests.wallet._rig_x402_sdk import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    PAID_BODY,
    USDC,
    ScriptedUsdcChain,
    ScriptedX402SdkResource,
)

pytest.importorskip("x402", reason="the x402 SDK lane is the optional `x402` extra")

pytestmark = [pytest.mark.safety]

PIN = "482913"
WRONG_PIN = "593027"
AMOUNT = 250_000  # 0.25 USDC
FUNDED = 1_000_000
PAYEE = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


@pytest.fixture(params=[BASE_SEPOLIA, BASE_MAINNET], ids=["base-sepolia", "base"])
def lane(request, monkeypatch, tmp_path):
    """Crypto on, the row's environment active, the scripted Base node with its USDC as the row's endpoint."""
    network = request.param
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 2))
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with ScriptedUsdcChain(network=network) as chain:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({network: chain.url}))
        environment.set_active_environment("testnet" if network == BASE_SEPOLIA else "mainnet")
        yield chain
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _wallet(chain: ScriptedUsdcChain, *, usdc: int = FUNDED) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=chain.network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"x-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    chain.fund_token(wallet["address"], usdc)
    return wallet


def _park(resource: ScriptedX402SdkResource, wallet: dict):
    from core.wallet import x402

    parked = x402.fetch_paid_resource(resource.url, wallet_id=wallet["wallet_id"])
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


def _hold(proposal_id: str):
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row else None


# --- the working path ---------------------------------------------------------------------------------------------

def test_a_base_usdc_request_is_quoted_approved_once_paid_once_and_proven_from_the_chain(lane):
    from core.wallet import proposals, status, x402
    from core.wallet.errors import WalletFault

    chain = lane
    wallet = _wallet(chain)
    token = USDC[chain.network][0]
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        parked = _park(resource, wallet)
        proposal = proposals.get_proposal(parked.proposal_id)
        assert (proposal.origin, proposal.state, proposal.amount_minor, proposal.asset, proposal.destination.lower()) == (
            proposals.ORIGIN_X402_SDK, proposals.STATE_PENDING_APPROVAL, AMOUNT, "USDC", PAYEE.lower())
        pending = [row for row in status.wallet_status()["pending"] if row["proposal_id"] == parked.proposal_id]
        assert pending and pending[0]["pilot_transfer"] is True, "the pending card opens the Pilot sheet"
        assert len(resource.requests) == 1 and resource.paid_requests == []

        quote = _quote(parked.proposal_id)
        fields = quote["fields"]
        block = fields["x402sdk"]
        assert (block["protocol"], block["built_by"], block["sponsored"], block["payer_fee_max_minor"], block["token"]) == ("x402", "x402 Foundation SDK", True, 0, token)
        assert (block["url"], block["method"], block["window_seconds"]) == (resource.url, "GET", 60)
        assert (fields["fee_estimate_minor"], fields["fee_max_minor"], fields["amount_minor"], fields["token_contract"]) == (0, 0, AMOUNT, token)
        review = fields["review"]
        assert review["headline"].startswith("Pay http://127.0.0.1:") and review["headline"].endswith("for one request")
        assert review["network_line"].startswith("Network fee: paid by the site") and review["primary_action"] == "Approve and pay"
        assert resource.paid_requests == [], "a quote sends nothing"

        with pytest.raises(WalletFault) as refused:
            _approve(parked.proposal_id, quote, pin=WRONG_PIN)
        assert refused.value.code == "wallet_pin_invalid"
        assert (_hold(parked.proposal_id), _row(parked.proposal_id), resource.paid_requests) == (None, {}, [])

        result = _approve(parked.proposal_id, quote)
        transfer = result["transfer"]
        assert result["duplicate"] is False
        assert result["x402_sdk"] == {"protocol": "x402", "resource_status": 200, "delivered": True, "unanswered": "", "claimed_transaction_unproven": False}
        assert len(resource.paid_requests) == 1 and len(resource.landed) == 1, "exactly one paid request, one authorization used"
        tx_id = resource.landed[0]
        assert (transfer["state"], transfer["tx_id"], transfer["charged_fee_minor"]) == ("confirmed", tx_id, "0")
        row = _row(parked.proposal_id)
        authorization = json.loads(row["authorization_json"])
        signed = resource.paid_requests[0]["payload"]["payload"]["authorization"]
        assert (authorization["kind"], authorization["token"], authorization["nonce"], authorization["valid_before"]) == ("eip3009", token, signed["nonce"], int(signed["validBefore"]))
        assert row["nonce"] is None and row["raw_b64"] is None, "an authorization takes no account nonce and keeps no transaction bytes"
        assert _hold(parked.proposal_id) == (AMOUNT, 0, "settled"), "the amount settles with no fee: the facilitator paid the gas"
        assert (chain.token_balance(wallet["address"]), chain.token_balance(PAYEE)) == (FUNDED - AMOUNT, AMOUNT)
        assert chain.send_count() == 0, "the wallet broadcasts nothing of its own"
        handed = x402.retry_paid_resource(parked.proposal_id)
        assert (handed.status, handed.body, handed.tx_signature) == (x402.OUTCOME_DELIVERED, PAID_BODY, tx_id), "the caller's retry gets the paid body, sending nothing"
        binding = x402.binding_for_proposal(parked.proposal_id)
        assert (binding["state"], binding["tx_signature"], binding["resource_status"]) == (x402.BINDING_DELIVERED, tx_id, 200)
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_CONFIRMED

        repeat = _approve(parked.proposal_id, quote)
        assert repeat["duplicate"] is True and len(resource.paid_requests) == 1, "the same approval again pays nothing"
        with pytest.raises(WalletFault) as again:
            x402.fetch_paid_resource(resource.url, wallet_id=wallet["wallet_id"])
        assert again.value.context["reason"] == "x402_sdk_request_already_paid"
        assert len(resource.paid_requests) == 1 and len(resource.requests) == 2


def _native_transfer(chain: ScriptedUsdcChain, wallet: dict) -> dict:
    """An ordinary native transfer from the same account, through the same Pilot sheet."""
    from core.wallet import lifecycle, proposals

    chain.fund(wallet["address"], 10**18)
    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination="0x" + "5" * 40, amount_minor=10**15, asset="ETH",
                                             origin=proposals.ORIGIN_USER, network=chain.network)
    assert lifecycle.default_lifecycle().prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    return _approve(proposal.proposal_id, _quote(proposal.proposal_id))


# --- what the chain decides when the site does not settle ---------------------------------------------------------

@pytest.mark.parametrize("mode", ["never", "names_other"])
def test_an_authorization_never_used_keeps_its_hold_until_its_window_closed_at_a_finalized_block(lane, mode):
    """The site refuses (or names a transaction that is not this payment): nothing proves a use, so the row stays
    unknown with the amount held. It ends only once a finalized block past the window says the token never
    used it; a finalized block that lags behind the window releases nothing. No resend or stop-waiting exit exists.
    Meanwhile the row holds no account slot: a native transfer from the same account goes through."""
    from core.wallet import proposals, settlement, transfers
    from core.wallet.errors import WalletFault

    chain = lane
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode=mode) as resource:
        parked = _park(resource, wallet)
        pid = parked.proposal_id
        result = _approve(pid, _quote(pid))
        assert len(resource.paid_requests) == 1 and resource.landed == []
        outcome = result["x402_sdk"]
        assert (outcome["delivered"], outcome["resource_status"], outcome["claimed_transaction_unproven"]) == (False, 402 if mode == "never" else 200, mode == "names_other")
        assert (result["transfer"]["state"], result["transfer"]["tx_id"]) == ("unknown", ""), "a transaction the site names is never the id unless the chain proves it"
        assert _hold(pid) == (AMOUNT, 0, "reserved")

        native = _native_transfer(chain, wallet)
        assert native["transfer"]["state"] == "confirmed", "an authorization takes no account nonce, so the account's next transfer does not wait"

        valid_before = json.loads(_row(pid)["authorization_json"])["valid_before"]
        settlement.observe_once(pid)
        assert (_row(pid)["state"], _hold(pid)) == ("unknown", (AMOUNT, 0, "reserved")), "the window is open: the amount stays counted"
        assert _row(pid)["offered_exit_json"] in ("", "{}") and settlement.resend_available(pid) == "signed_bytes_not_kept"
        with pytest.raises(settlement.ExitUnavailableError):
            settlement.owner_stop_waiting(pid)

        chain.advance(120)
        chain.finalized_block = chain.block_number - 90  # finality lags: its block is still inside the window
        assert chain.timestamp_of(chain.finalized_block) <= valid_before
        settlement.observe_once(pid)
        assert (_row(pid)["state"], _hold(pid)) == ("unknown", (AMOUNT, 0, "reserved"))

        chain.finalized_block = None
        view = settlement.observe_once(pid)
        assert view["state"] == transfers.STATE_FAILED_ON_CHAIN and _hold(pid) == (0, 0, "settled"), "it left, so it fails; it is never 'not sent'"
        assert (view["state_label"], view["charged_fee_minor"]) == ("Expired unused", "0")
        assert proposals.get_proposal(pid).state == proposals.STATE_FAILED
        assert (chain.token_balance(wallet["address"]), chain.token_balance(PAYEE)) == (FUNDED, 0), "nothing moved"
        assert "authorization_expired_unused" in _row(pid)["evidence_json"]
        assert len(resource.paid_requests) == 1, "the request is never sent again"
        with pytest.raises(WalletFault):
            _approve(pid, _quote(pid))


def test_a_lost_answer_is_settled_from_the_chain_by_the_authorizations_own_use(lane):
    """The facilitator landed the authorization but the connection closed unanswered: the observer finds the use by
    the token's AuthorizationUsed(this wallet, this nonce) log and proves the receipt before it becomes the id."""
    from core.wallet import x402

    chain = lane
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode="drop") as resource:
        parked = _park(resource, wallet)
        result = _approve(parked.proposal_id, _quote(parked.proposal_id))
        assert result["x402_sdk"]["unanswered"].startswith("submit_unknown:") and result["x402_sdk"]["delivered"] is False
        tx_id = resource.landed[0]
        assert (result["transfer"]["state"], result["transfer"]["tx_id"], result["transfer"]["charged_fee_minor"]) == ("confirmed", tx_id, "0")
        assert _hold(parked.proposal_id) == (AMOUNT, 0, "settled") and chain.token_balance(PAYEE) == AMOUNT
        binding = x402.binding_for_proposal(parked.proposal_id)
        assert (binding["state"], binding["tx_signature"]) == (x402.BINDING_PAID, tx_id), "paid, and the body was never delivered"
        assert len(resource.paid_requests) == 1


def test_a_receipt_lost_to_a_reorg_goes_back_to_unknown_and_the_next_use_on_chain_becomes_the_id(lane):
    from core.wallet import settlement

    chain = lane
    chain.safe_block = 1  # the display tag lags: the first receipt stays pending
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        parked = _park(resource, wallet)
        pid = parked.proposal_id
        result = _approve(pid, _quote(pid))
        first = resource.landed[0]
        assert (result["transfer"]["state"], result["transfer"]["tx_id"]) == ("pending", first)
        payload = resource.paid_requests[0]["payload"]["payload"]

        chain.drop_facilitated(first)
        settlement.observe_once(pid)
        assert _row(pid)["state"] == "pending"
        settlement.observe_once(pid)
        assert _row(pid)["state"] == "unknown" and _hold(pid) == (AMOUNT, 0, "reserved"), "a lost receipt never releases while the window is open"

        again, refused = chain.execute_authorization(payload["authorization"], payload["signature"])
        assert again and again != first and not refused
        chain.safe_block = None
        view = settlement.observe_once(pid)
        assert (view["state"], view["tx_id"]) == ("confirmed", again), "the transaction the chain proves is the id"
        assert _hold(pid) == (AMOUNT, 0, "settled") and chain.token_balance(PAYEE) == AMOUNT


@pytest.mark.parametrize("node", ["caps-log-ranges", "refuses-log-reads"])
@pytest.mark.parametrize("used", [True, False], ids=["used", "never-used"])
def test_a_payment_read_hours_after_its_window_is_proven_or_ends_unused_through_a_node_that_limits_log_reads(lane, used, node):
    """VOOL was closed while the site settled late (or never). Hours of blocks later, through a node that refuses log
    ranges over 500 blocks as public endpoints do, the observer still finds the use inside the window and confirms it,
    or reads the window closed unused at finality and ends it with nothing counted; the search ends at the window, so
    it costs the same however long VOOL was closed. A node that answers no log read at all still ends an unused
    authorization (finality says so without a search); a used one it cannot find keeps its hold until a search finds
    it."""
    from core.wallet import settlement, transfers

    chain = lane
    chain.max_log_span = 500 if node == "caps-log-ranges" else 0
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE, mode="hold") as resource:
        parked = _park(resource, wallet)
        pid = parked.proposal_id
        result = _approve(pid, _quote(pid))
        assert (result["transfer"]["state"], _hold(pid)) == ("unknown", (AMOUNT, 0, "reserved"))
        landed = resource.settle_held() if used else []
        assert len(landed) == (1 if used else 0)
        chain.advance(6 * 3600)  # six hours: 10,800 blocks
        before = len(chain.calls)
        view = settlement.observe_once(pid)
        if node == "caps-log-ranges":
            log_reads = [call for call in chain.calls[before:] if call["method"] == "eth_getLogs"]
            assert 1 <= len(log_reads) <= 2, "the search ends at the window, never walks the hours since"
        elif used:
            assert (view["state"], _hold(pid)) == ("unknown", (AMOUNT, 0, "reserved")), "used but not found: the amount stays counted"
            assert "authorization_used" in _row(pid)["evidence_json"] and chain.token_balance(PAYEE) == AMOUNT
            chain.max_log_span = 500
            view = settlement.observe_once(pid)
        if used:
            assert (view["state"], view["tx_id"]) == ("confirmed", landed[0]) and _hold(pid) == (AMOUNT, 0, "settled")
            assert chain.token_balance(PAYEE) == AMOUNT
        else:
            assert view["state"] == transfers.STATE_FAILED_ON_CHAIN and _hold(pid) == (0, 0, "settled")
            assert chain.token_balance(PAYEE) == 0


# --- refused before anything is held or sent ----------------------------------------------------------------------

class _CountingApprover:
    def __init__(self, pin: str = PIN) -> None:
        from core.wallet import approval

        self._inner = approval.PinApprover(pin)
        self.asked = 0

    def approve(self, challenge):
        self.asked += 1
        return self._inner.approve(challenge)


@pytest.mark.parametrize("change", ["amount", "payTo", "extra", "maxTimeoutSeconds"])
def test_an_authorization_the_sdk_builds_for_other_terms_is_refused_at_the_signature_and_nothing_is_sent(lane, change):
    """The stored offer is altered after the quote (the binding's facts the sheet showed stay the same), so the SDK asks
    to sign another amount, payee, token domain or window: the guard refuses before the key signs, the claim is
    released and the request never leaves."""
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    chain = lane
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        parked = _park(resource, wallet)
        pid = parked.proposal_id
        quote = _quote(pid)
        binding = x402.binding_for_proposal(pid)
        offer = json.loads(binding["offer_json"])
        offer["requirement"][change] = {"amount": str(AMOUNT + 1), "payTo": "0x" + "6" * 40, "extra": {"name": "Other Coin", "version": "2"}, "maxTimeoutSeconds": 3600}[change]
        x402._update_binding(binding["request_digest"], offer_json=json.dumps(offer))
        with pytest.raises(WalletFault) as refused:
            _approve(pid, quote)
        assert refused.value.code == "wallet_signature_invalid"
        assert refused.value.context["reason"] == {"amount": "x402_sdk_amount_not_approved", "payTo": "x402_sdk_payee_not_approved",
                                                   "extra": "x402_sdk_domain_not_the_registry_token", "maxTimeoutSeconds": "x402_sdk_window_not_approved"}[change]
        assert resource.paid_requests == [] and _row(pid)["state"] == "released" and _hold(pid) == (AMOUNT, 0, "released")
        assert proposals.get_proposal(pid).state == proposals.STATE_FAILED and chain.token_balance(wallet["address"]) == FUNDED


@pytest.mark.parametrize("change", ["url", "max_timeout_seconds", "asset_address"])
def test_a_binding_changed_after_the_quote_is_refused_before_the_pin(lane, change):
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    chain = lane
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        parked = _park(resource, wallet)
        quote = _quote(parked.proposal_id)
        binding = x402.binding_for_proposal(parked.proposal_id)
        x402._update_binding(binding["request_digest"], **{change: {"url": resource.url + "?other", "max_timeout_seconds": 120, "asset_address": "0x" + "7" * 40}[change]})
        approver = _CountingApprover()
        from core.wallet import lifecycle

        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().approve_pilot_transfer(parked.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approver)
        assert (refused.value.code, refused.value.context["reason"]) == ("wallet_quote_mismatch", "x402_sdk_binding_changed_since_the_quote")
        assert approver.asked == 0 and _hold(parked.proposal_id) is None and resource.paid_requests == []


def test_the_channel_turned_off_refuses_before_the_pin_and_after_the_signature_sends_nothing(lane, monkeypatch):
    """Off when the 402 arrives: nothing is parked. Off before the approval: refused before the credential, nothing
    held. Off while the SDK builds (after the signature): the send guard refuses under its write lock, the signed
    authorization never leaves, and its hold returns once the window closed at finality."""
    from core.wallet import channels, proposals, settlement, x402, x402_sdk
    from core.wallet.errors import WalletFault

    chain = lane
    wallet = _wallet(chain)
    with ScriptedX402SdkResource(chain, amount_minor=AMOUNT, pay_to=PAYEE) as resource:
        channels.set_on(channels.CHANNEL_X402_SDK, False)
        with pytest.raises(WalletFault) as closed:
            x402.fetch_paid_resource(resource.url, wallet_id=wallet["wallet_id"])
        assert (closed.value.code, closed.value.context["channel"]) == ("wallet_payment_channel_off", "x402_sdk")
        assert proposals.list_proposals() == [] and resource.paid_requests == []
        channels.set_on(channels.CHANNEL_X402_SDK, True)
        parked = _park(resource, wallet)
        pid = parked.proposal_id
        quote = _quote(pid)
        channels.set_on(channels.CHANNEL_X402_SDK, False)
        approver = _CountingApprover()
        from core.wallet import lifecycle

        with pytest.raises(WalletFault) as off:
            lifecycle.default_lifecycle().approve_pilot_transfer(pid, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approver)
        assert off.value.code == "wallet_payment_channel_off" and approver.asked == 0 and _hold(pid) is None

        channels.set_on(channels.CHANNEL_X402_SDK, True)
        real_build = x402_sdk.build_payment_header

        def build_then_switch_off(binding, signer):
            header = real_build(binding, signer)
            channels.set_on(channels.CHANNEL_X402_SDK, False)
            return header

        monkeypatch.setattr(x402_sdk, "build_payment_header", build_then_switch_off)
        with pytest.raises(WalletFault) as during:
            _approve(pid, quote)
        assert during.value.code == "wallet_payment_channel_off"
        assert resource.paid_requests == [] and _row(pid)["state"] == "signed_revoked" and _hold(pid) == (AMOUNT, 0, "reserved")
        with pytest.raises(settlement.ExitUnavailableError):
            settlement.owner_discard(pid)
        settlement.observe_once(pid)
        assert _row(pid)["state"] == "signed_revoked", "signed and unsent, but usable until its window closes"
        chain.advance(120)
        settlement.observe_once(pid)
        assert _row(pid)["state"] == "released" and _hold(pid) == (AMOUNT, 0, "released")
        assert resource.paid_requests == [] and chain.token_balance(wallet["address"]) == FUNDED
