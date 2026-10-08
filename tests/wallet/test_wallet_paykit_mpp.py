"""MPP charges on Solana through Solana pay-kit, with this wallet as the only money authority.

The owner's request meets a 402 carrying ``WWW-Authenticate: Payment`` (method solana, intent charge) -> ONE capped
proposal bound to that exact request -> the ordinary approval -> pay-kit builds the charge through the wallet's
guarded signer (``pubkey()`` + ``sign_message()``: the key never leaves) -> the SAME request goes once with
``Authorization: Payment`` -> settlement is believed from the chain. Every guard below is pinned by a sabotage in the
series README.
"""
from __future__ import annotations

import json

import pytest

from tests.wallet._rig import DESTINATION, OTHER_DESTINATION
from tests.wallet._rig_paykit import PAID_BODY, ScriptedMppResource

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]
PIN = "246810"
REQUEST_BODY = json.dumps({"question": "q3 revenue"}).encode()


@pytest.fixture
def env(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    return wallet_env


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _park(resource, profile, *, body=REQUEST_BODY, method="POST", headers=None):
    from core.wallet import paykit_x402

    return paykit_x402.fetch_paid(resource.url, wallet_id=profile.wallet_id, method=method, body=body,
                                  headers=headers if headers is not None else {"Content-Type": "application/json"})


def _approve(proposal_id, pin=PIN):
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_and_execute(proposal_id, approver=approval.PinApprover(pin))


def _receipt(proposal_id):
    from core.wallet import receipts

    return [r for r in receipts.list_receipts() if r.get("mpp_paykit") and r["proposal_id"] == proposal_id][-1]["mpp_paykit"]


# --- the working path ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sponsored", [True, False], ids=["server-pays-fee", "wallet-pays-fee"])
def test_an_mpp_charge_is_paid_once_and_the_same_request_is_delivered(env, sponsored):
    from core.wallet import limits, paykit_x402, proposals, x402

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=sponsored) as resource:
        parked = _park(resource, profile, headers={"Content-Type": "application/json", "Authorization": "Bearer caller-secret", "Cookie": "sid=1"})
        assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED
        proposal = proposals.get_proposal(parked.proposal_id)
        assert proposal.origin == proposals.ORIGIN_MPP_PAYKIT and proposal.state == proposals.STATE_PENDING_APPROVAL
        assert proposal.amount_minor == 1500 and proposal.destination == OTHER_DESTINATION
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED and receipt.tx_signature in env["rpc"].transactions
        assert len(resource.paid_requests) == 1
        paid = resource.paid_requests[0]
        assert paid["method"] == "POST" and paid["body"] == REQUEST_BODY and paid["content_type"] == "application/json"
        assert paid["authorization"] == "" and paid["cookie"] == "", "caller credentials are never stored or replayed"
        assert paykit_x402.delivered_body(parked.proposal_id) == PAID_BODY
    binding = x402.binding_for_proposal(parked.proposal_id)
    assert binding["version"] == x402.BINDING_VERSION_PAYKIT_MPP and binding["state"] == x402.BINDING_DELIVERED
    assert "caller-secret" not in json.dumps(binding)
    assert _receipt(parked.proposal_id)["settlement"] == "settled"
    assert limits.reservation_state(parked.proposal_id) == "settled"
    from core.wallet import paykit_mpp
    from core.wallet.store import connection

    with connection() as conn:
        fee = conn.execute("SELECT COALESCE(SUM(fee_minor), 0) FROM wallet_spend_ledger WHERE proposal_id IN (?, ?)",
                           (parked.proposal_id, limits._fee_hold_id(parked.proposal_id))).fetchone()[0]
    assert fee == (0 if sponsored else paykit_mpp.payer_fee_minor(proposals.get_proposal(parked.proposal_id))), "the fee this wallet pays is held with the amount"
    assert env["rpc"].send_count() == 0, "the resource broadcasts; the wallet never sends a transfer of its own"


def test_the_approval_shows_the_fee_an_unsponsored_charge_costs_this_wallet(env):
    from core.wallet import custody, lifecycle, paykit_mpp, proposals

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=False) as resource:
        parked = _park(resource, profile)
        proposal = proposals.get_proposal(parked.proposal_id)
        challenge = lifecycle.default_lifecycle()._challenge_for(proposal, custody.get_wallet(profile.wallet_id))
    assert challenge.sponsored_gas is False and challenge.fee_asset == "SOL"
    assert challenge.max_network_fee_minor == paykit_mpp.payer_fee_minor(proposal) > 0


def test_an_unsponsored_charge_is_never_approvable_before_its_fee_is_bound(env, monkeypatch):
    """The moment the proposal becomes approvable, an approval sheet read then already shows the fee this wallet will
    pay: the fee is bound while the proposal is prepared, not after it was published as pending."""
    from core.wallet import custody, lifecycle, paykit_mpp, proposals

    profile = _pocket()
    seen = []
    real = proposals.transition

    def watching(proposal_id, new_state, **kwargs):
        moved = real(proposal_id, new_state, **kwargs)
        if new_state == proposals.STATE_PENDING_APPROVAL and moved is not None:
            seen.append(lifecycle.default_lifecycle()._challenge_for(moved, custody.get_wallet(profile.wallet_id)).max_network_fee_minor)
        return moved

    monkeypatch.setattr(proposals, "transition", watching)
    with ScriptedMppResource(env["rpc"], sponsored=False) as resource:
        parked = _park(resource, profile)
    assert seen == [paykit_mpp.payer_fee_minor(proposals.get_proposal(parked.proposal_id))] and seen[0] > 0


class _ApproverSeeingNoFee:
    """The owner approves a sheet whose charge shows no network fee (the binding's fee was not there when it was read)."""

    def __init__(self, pin):
        from core.wallet import approval

        self._inner = approval.PinApprover(pin)
        self.sheets = []

    def approve(self, challenge):
        self.sheets.append(challenge)
        return self._inner.approve(challenge)


def test_a_charge_approved_without_its_fee_is_never_held_or_paid(env):
    """An approval whose sheet showed no fee for a charge this wallet pays the fee of: execution never works the fee
    out afresh and holds more than was approved. It refuses before anything is held, signed or sent."""
    from core.wallet import lifecycle, limits, paykit_x402, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=False) as resource:
        parked = _park(resource, profile)
        x402._update_binding(paykit_x402.binding_for(parked.proposal_id)["request_digest"], max_network_fee_minor=0)
        approver = _ApproverSeeingNoFee(PIN)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approver)
        assert approver.sheets[0].max_network_fee_minor == 0 and approver.sheets[0].max_total_minor == 1500
        assert refused.value.context["reason"] == "paykit_fee_not_what_was_approved"
        assert limits.reservation_state(parked.proposal_id) == "", "nothing was held"
        assert resource.paid_requests == [] and env["rpc"].send_count() == 0
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL


@pytest.mark.parametrize("order", ["sponsored-then-unsponsored", "unsponsored-then-sponsored"])
def test_a_request_parked_again_binds_the_fee_facts_of_its_new_offer(env, order):
    """The same request meets one offer, that proposal dies (approval attempts exhausted), then it meets another offer
    with the other fee arrangement: the approval binds the new offer's fee, never the old one's."""
    from core.wallet import custody, lifecycle, outbound, paykit_mpp, paykit_x402, proposals, x402
    from core.wallet.errors import WalletFault
    from tests.wallet._rig_paykit import ScriptedPayKitResource

    profile = _pocket()
    sponsored_first = order == "sponsored-then-unsponsored"
    with ScriptedPayKitResource(env["rpc"]) as sponsored, ScriptedMppResource(env["rpc"], sponsored=False) as unsponsored:
        first_offer, second_offer = (sponsored, unsponsored) if sponsored_first else (unsponsored, sponsored)
        url = first_offer.url
        first = paykit_x402.park_challenge(outbound.fetch(url), url=url, method="GET", headers=None, body=b"", wallet_id=profile.wallet_id)
        for _ in range(lifecycle.MAX_APPROVAL_ATTEMPTS):
            with pytest.raises(WalletFault):
                _approve(first.proposal_id, pin="000000")
        assert proposals.get_proposal(first.proposal_id).state == proposals.STATE_REJECTED
        second = paykit_x402.park_challenge(outbound.fetch(second_offer.url), url=url, method="GET", headers=None, body=b"", wallet_id=profile.wallet_id)
        proposal = proposals.get_proposal(second.proposal_id)
        assert second.proposal_id != first.proposal_id and proposal.state == proposals.STATE_PENDING_APPROVAL
        challenge = lifecycle.default_lifecycle()._challenge_for(proposal, custody.get_wallet(profile.wallet_id))
        binding = x402.binding_for_proposal(second.proposal_id)
    if sponsored_first:
        fee = paykit_mpp.payer_fee_minor(proposal)
        assert challenge.sponsored_gas is False and binding["sponsored_gas"] == 0 and binding["fee_payer"] == ""
        assert challenge.max_network_fee_minor == fee > 0 and challenge.max_total_minor == proposal.amount_minor + fee
    else:
        assert challenge.sponsored_gas is True and binding["sponsored_gas"] == 1 and binding["fee_payer"]
        assert challenge.max_network_fee_minor == 0 and binding["max_network_fee_minor"] == 0
        assert challenge.max_total_minor == proposal.amount_minor
    assert challenge.fee_asset == "SOL"


# --- guard: no payment without approval --------------------------------------------------------------------------

def test_nothing_is_signed_or_sent_before_the_owner_approves(env):
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        assert _park(resource, profile).proposal_id == parked.proposal_id
        with pytest.raises(WalletFault) as retried:
            x402.retry_paid_resource(parked.proposal_id)
        assert retried.value.context["reason"] == "paykit_delivery_happens_at_approval"
        with pytest.raises(WalletFault) as wrong_pin:
            _approve(parked.proposal_id, pin="000000")
        assert wrong_pin.value.code == "wallet_approval_rejected"
        assert resource.paid_requests == [] and env["rpc"].transactions == {}
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL


# --- guard: the spend cap ------------------------------------------------------------------------------------------

def test_a_charge_over_the_cap_is_refused_before_any_proposal(env, monkeypatch):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "1000")
    profile = _pocket()
    with ScriptedMppResource(env["rpc"], amount_minor=1500) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "wallet_x402_cap_exceeded"
        assert proposals.list_proposals() == [] and resource.paid_requests == []


# --- guard: what this lane takes ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"network": "mainnet"}, "offer_network_differs_from_wallet"),
    ({"network": "localnet"}, "mpp_network_not_declared"),
    ({"currency": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "decimals": 6}, "offer_asset_not_registered"),
    ({"currency": "USDC", "decimals": 9}, "mpp_decimals_differ_from_registry"),
    ({"intent": "session"}, "mpp_intent_not_supported"),
    ({"splits": [{"recipient": DESTINATION, "amount": "100"}]}, "mpp_splits_not_supported"),
    ({"recipient": "not-a-solana-address"}, "offer_payee_invalid"),
    ({"expires": "2020-01-01T00:00:00Z"}, "mpp_challenge_expired"),
])
def test_a_charge_this_wallet_cannot_honour_is_refused_before_any_proposal(env, kwargs, reason):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()  # a Solana Devnet wallet
    with ScriptedMppResource(env["rpc"], **kwargs) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "x402_scheme_unavailable" and exc.value.context["reason"] == reason
        assert proposals.list_proposals() == [] and resource.paid_requests == []


# --- guard: the signed message is exactly the approved transfer ------------------------------------------------------

@pytest.mark.parametrize(("field", "value"), [("recipient", DESTINATION), ("amount", "1499"), ("amount", "1501")])
def test_a_payee_or_amount_changed_after_approval_is_never_signed(env, field, value):
    from solana_pay_kit.protocols.mpp.core.base64url import decode_json, encode_json

    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with connection() as conn:
            row = conn.execute("SELECT offer_json FROM wallet_x402_bindings WHERE proposal_id = ?", (parked.proposal_id,)).fetchone()
            offer = json.loads(row[0])
            request = decode_json(offer["challenge"]["request"])
            request[field] = value
            offer["challenge"]["request"] = encode_json(request)
            conn.execute("UPDATE wallet_x402_bindings SET offer_json = ? WHERE proposal_id = ?", (json.dumps(offer), parked.proposal_id))
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.code in {"wallet_signature_invalid", "wallet_signing_unavailable"}
        assert resource.paid_requests == [] and env["rpc"].transactions == {}
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
        assert limits.reservation_state(parked.proposal_id) == "released"


def test_an_unsponsored_charge_carrying_a_large_priority_fee_is_never_signed():
    from solders.hash import Hash
    from solders.instruction import Instruction
    from solders.keypair import Keypair
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    payer = Keypair().pubkey()
    budget = Pubkey.from_string(paykit_x402.COMPUTE_BUDGET_PROGRAM)

    def message(price: int) -> bytes:
        instructions = [Instruction(budget, bytes([3]) + price.to_bytes(8, "little"), []), Instruction(budget, bytes([2]) + (200_000).to_bytes(4, "little"), []),
                        transfer(TransferParams(from_pubkey=payer, to_pubkey=Pubkey.from_string(OTHER_DESTINATION), lamports=1500))]
        return bytes(to_bytes_versioned(MessageV0.try_compile(payer, instructions, [], Hash.default())))

    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": str(payer), "payer_pays_fee": True}
    assert paykit_x402.verify_payment_message(message(1), payer=str(payer), **terms)["payer_pays_fee"] is True
    with pytest.raises(WalletFault) as exc:
        paykit_x402.verify_payment_message(message(1_000_000), payer=str(payer), **terms)
    assert exc.value.context["reason"] == "paykit_priority_fee_above_bound"
    with pytest.raises(WalletFault) as sponsored_shape:
        paykit_x402.verify_payment_message(message(1), payer=str(payer), **{**terms, "payer_pays_fee": False})
    assert sponsored_shape.value.context["reason"] == "paykit_fee_payer_not_the_offer"


# --- guard: replay with another body or method; a second retry ------------------------------------------------------

def test_another_body_or_method_is_another_request_and_a_paid_one_is_never_paid_again(env):
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
        other_body = _park(resource, profile, body=b'{"question": "everything"}')
        other_method = _park(resource, profile, method="PUT")
        assert len({parked.proposal_id, other_body.proposal_id, other_method.proposal_id}) == 3
        for outcome in (other_body, other_method):
            assert proposals.get_proposal(outcome.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        with pytest.raises(WalletFault) as same_again:
            _park(resource, profile)
        assert same_again.value.context["reason"] == "paykit_request_already_paid"
        with pytest.raises(WalletFault) as approve_again:
            _approve(parked.proposal_id)
        assert approve_again.value.code == "wallet_duplicate_payment"
        assert x402.retry_paid_resource(parked.proposal_id).body == PAID_BODY
        with pytest.raises(WalletFault):
            x402.retry_paid_resource(parked.proposal_id)
        assert len(resource.paid_requests) == 1 and len(env["rpc"].transactions) == 1


def test_an_mpp_challenge_parked_for_a_request_already_paid_never_rebinds_it(env):
    """Each MPP challenge carries its own id, so the same request challenged again would get a fresh proposal key:
    park_challenge itself refuses a request this lane already paid, and the request stays bound to its payment."""
    from core.wallet import outbound, paykit_mpp, paykit_x402, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    headers = {"Content-Type": "application/json"}
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
        challenge = outbound.fetch(resource.url, method="POST", headers=headers, body=REQUEST_BODY)
        assert challenge["status"] == 402
        with pytest.raises(WalletFault) as again:
            paykit_mpp.park_challenge(challenge, url=resource.url, method="POST", headers=headers, body=REQUEST_BODY, wallet_id=profile.wallet_id)
        assert again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.landed) == 1
    binding = x402.binding_for_digest(paykit_x402.request_digest("POST", resource.url, REQUEST_BODY))
    assert binding["proposal_id"] == parked.proposal_id


@pytest.mark.parametrize("state", ["broadcast", "confirmed"])
def test_a_late_mpp_challenge_cannot_replace_an_unknown_or_paid_request(env, monkeypatch, state):
    """The MPP lane's version of the late challenge: a charge paid (confirmed) or left unknown (broadcast), and a second
    caller past a stale check whose challenge asks another amount. The request stays its payment's; the proposal
    minted for the late challenge is rejected."""
    from core.wallet import outbound, paykit_mpp, paykit_x402, proposals, x402
    from core.wallet.errors import WalletFault
    from tests.wallet.test_wallet_paykit_x402 import _stale_first_check

    profile = _pocket()
    headers = {"Content-Type": "application/json"}
    with ScriptedMppResource(env["rpc"], mode="ok" if state == "confirmed" else "no_settlement_header") as resource:
        parked = _park(resource, profile)
        assert _approve(parked.proposal_id).state == state
        resource.amount_minor = 1400
        challenge = outbound.fetch(resource.url, method="POST", headers=headers, body=REQUEST_BODY)
        _stale_first_check(monkeypatch)
        with pytest.raises(WalletFault) as late:
            paykit_mpp.park_challenge(challenge, url=resource.url, method="POST", headers=headers, body=REQUEST_BODY, wallet_id=profile.wallet_id)
        assert late.value.code == "wallet_duplicate_payment"
        assert len(resource.landed) == 1
    assert x402.binding_for_digest(paykit_x402.request_digest("POST", resource.url, REQUEST_BODY))["proposal_id"] == parked.proposal_id
    others = [p for p in proposals.list_proposals() if p.proposal_id != parked.proposal_id]
    assert [(p.state, p.amount_minor) for p in others] == [(proposals.STATE_REJECTED, 1400)]


def test_a_challenge_that_expires_before_approval_is_never_signed_and_releases_the_hold(env):
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with connection() as conn:
            row = conn.execute("SELECT offer_json FROM wallet_x402_bindings WHERE proposal_id = ?", (parked.proposal_id,)).fetchone()
            offer = json.loads(row[0])
            offer["challenge"]["expires"] = "2020-01-01T00:00:00Z"
            conn.execute("UPDATE wallet_x402_bindings SET offer_json = ? WHERE proposal_id = ?", (json.dumps(offer), parked.proposal_id))
        with pytest.raises(WalletFault):
            _approve(parked.proposal_id)
        assert resource.paid_requests == [] and env["rpc"].transactions == {}
    assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(parked.proposal_id) == "released"


# --- settlement is the chain's word -----------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["no_settlement_header", "wrong_settlement"])
def test_a_sponsored_delivery_the_chain_does_not_prove_keeps_the_hold_as_unknown(env, mode):
    from core.wallet import limits, proposals

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], mode=mode) as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
    assert receipt.state == proposals.STATE_BROADCAST
    assert proposals.get_proposal(parked.proposal_id).tx_signature == ""
    assert limits.reservation_state(parked.proposal_id) == "reserved"
    assert _receipt(parked.proposal_id)["settlement"] == "unknown"


def test_an_unsponsored_charge_is_proven_by_its_own_signature_without_a_receipt(env):
    """This wallet is the fee payer, so the transaction id is its own signature: the chain answers for it."""
    from core.wallet import limits, proposals

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=False, mode="no_settlement_header") as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
        landed = resource.landed[0]
    assert receipt.state == proposals.STATE_CONFIRMED and receipt.tx_signature == landed
    assert limits.reservation_state(parked.proposal_id) == "settled"


def test_a_charge_that_failed_on_chain_releases_the_hold(env):
    from core.wallet import limits, proposals

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], mode="settle_fails") as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
    assert receipt.state == proposals.STATE_FAILED and limits.reservation_state(parked.proposal_id) == "released"


@pytest.mark.parametrize("paid_answer", ["ok", "status:304"])
def test_a_charge_that_failed_on_chain_still_counts_the_fee_this_wallet_paid(env, paid_answer):
    """This wallet paid the network fee of a charge that failed on chain: the amount never moved, but the fee was
    charged, so the caps count the fee and not the amount (the way the Pilot lane settles a failed transfer)."""
    from core.wallet import limits, paykit_mpp, proposals
    from core.wallet.store import connection

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=False, mode="settle_fails", paid_answer=paid_answer) as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
    assert receipt.state == proposals.STATE_FAILED
    assert limits.reservation_state(parked.proposal_id) == "settled"
    with connection() as conn:
        rows = conn.execute("SELECT state, amount_minor, fee_minor FROM wallet_spend_ledger WHERE proposal_id IN (?, ?)",
                            (parked.proposal_id, limits._fee_hold_id(parked.proposal_id))).fetchall()
    assert {row[0] for row in rows} == {"settled"}
    assert sum(row[1] for row in rows) == 0, "the amount of a failed charge never counts"
    assert sum(row[2] for row in rows) == paykit_mpp.payer_fee_minor(proposals.get_proposal(parked.proposal_id))


# --- the ordinary x402 door -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("sponsored", "mode", "state", "hold"), [
    (False, "ok", "confirmed", "settled"),
    (False, "settle_fails", "failed", "settled"),
    (True, "ok", "broadcast", "reserved"),
], ids=["wallet-pays-fee-landed", "wallet-pays-fee-failed-on-chain", "server-pays-fee"])
def test_a_charge_answered_with_a_bare_3xx_is_decided_by_the_chain(env, sponsored, mode, state, hold):
    """The charge lands (or fails on chain), then the resource answers a bare 304. The request left with the payment,
    so only the chain decides: this wallet's own signature names the transaction when it paid the fee; a sponsored
    charge has no id this wallet knows, so its hold stays as unknown."""
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedMppResource(env["rpc"], sponsored=sponsored, mode=mode, paid_answer="status:304") as resource:
        parked = _park(resource, profile)
        if state == "broadcast":
            with pytest.raises(WalletFault) as exc:
                _approve(parked.proposal_id)
            assert exc.value.context["reason"].startswith("submit_unknown:")
        else:
            receipt = _approve(parked.proposal_id)
            assert receipt.state == state and receipt.tx_signature == resource.landed[0]
            assert _receipt(parked.proposal_id)["settlement"] == ("settled" if state == "confirmed" else "failed")
            assert _receipt(parked.proposal_id)["delivered"] is False
        assert len(resource.landed) == 1
        assert proposals.get_proposal(parked.proposal_id).state == state
        assert limits.reservation_state(parked.proposal_id) == hold
        if state != "failed":
            with pytest.raises(WalletFault) as again:
                _park(resource, profile)
            assert again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.landed) == 1


def test_the_ordinary_x402_door_hands_an_mpp_challenge_to_paykit(env):
    from core.wallet import proposals, x402

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert proposals.get_proposal(parked.proposal_id).origin == proposals.ORIGIN_MPP_PAYKIT
        assert x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id).proposal_id == parked.proposal_id
        assert len(resource.requests) == 1
        assert _approve(parked.proposal_id).state == proposals.STATE_CONFIRMED
        assert [r["method"] for r in resource.paid_requests] == ["GET"]


def test_an_external_signer_wallet_is_refused_before_any_signature(env):
    from core.wallet import lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().request_external_signature(parked.proposal_id)
        assert refused.value.context["reason"] == "paykit_external_signer_not_supported"
        assert resource.paid_requests == [] and proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL
