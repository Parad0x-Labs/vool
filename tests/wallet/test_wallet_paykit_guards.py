"""The pay-kit lane's money guards around pay-kit itself, each pinned on its own.

The signature guard's rules, the guarded signer and the settlement check need no pay-kit and live in
``test_wallet_paykit_signer_guards`` so they run in every install. Here: pay-kit's own USDC payment passes the guard
only as approved, the panic freeze brakes before the claim and before the request, a wallet that is not a pocket
wallet never reaches a signer, a payment pay-kit did not sign through the guard never leaves, pay-kit's own amount
guard stays armed, a request in flight is never parked again, and the request and offer bounds hold. One token
payment goes end to end.
"""
from __future__ import annotations

import base64
import json

import pytest

from tests.wallet._rig import OTHER_DESTINATION
from tests.wallet._rig_paykit import DEVNET_CAIP2, ScriptedMppResource, ScriptedPayKitResource
from tests.wallet.test_wallet_paykit_signer_guards import _key, _usdc
from tests.wallet.test_wallet_paykit_x402 import PIN, _approve, _park, _pocket, env

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]


@pytest.mark.parametrize(("change", "reason"), [
    ({}, None),
    ({"extra": {"decimals": 9}}, "paykit_transfer_decimals_differ"),
    ({"amount": "1501"}, "paykit_transfer_amount_differs"),
    ({"feePayer": False}, "paykit_fee_payer_not_the_offer"),
], ids=["as-approved", "offer-decimals-9", "offer-amount-1501", "offer-fee-payer-false"])
def test_paykits_own_usdc_payment_is_signed_only_as_approved(change, reason):
    """pay-kit builds the real x402 ``exact`` USDC payment; the guard signs it as the owner approved it and refuses an
    offer that drifted from that."""
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    key, fee_payer = Keypair(), _key()
    usdc = _usdc()

    class _Signer:
        public_key = str(key.pubkey())

        def sign(self, message):
            return bytes(key.sign_message(message))

    requirement = {"scheme": "exact", "network": DEVNET_CAIP2, "amount": "1500", "asset": usdc.address, "payTo": OTHER_DESTINATION,
                   "maxTimeoutSeconds": 60, "extra": {"feePayer": fee_payer, "decimals": usdc.decimals}}
    extra = change.get("extra")
    requirement = {**requirement, **{k: v for k, v in change.items() if k != "extra"}}
    if extra:
        requirement["extra"] = {**requirement["extra"], **extra}
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": usdc.address, "decimals": usdc.decimals, "fee_payer": fee_payer}
    guarded = paykit_x402.GuardedSigner(_Signer(), terms=terms)
    binding = {"offer_json": json.dumps({"x402Version": 2, "requirement": requirement})}
    if reason is None:
        name, _value = paykit_x402.build_payment_header(binding, guarded, blockhash="4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM")
        assert name == "PAYMENT-SIGNATURE" and guarded.proof["mint"] == usdc.address and guarded.proof["amount_minor"] == 1500
    else:
        with pytest.raises(WalletFault) as exc:
            paykit_x402.build_payment_header(binding, guarded, blockhash="4uQeVj5tqViQh7yWWGStvkEG1Zmhx6uasJtWCJziofM")
        assert exc.value.context["reason"] == reason and guarded.signed_message == b""


# --- the lifecycle around the guard ----------------------------------------------------------------------------------

class _FreezingApprover:
    """The owner approves with the right PIN, and the panic freeze lands at that moment."""

    def __init__(self, pin):
        from core.wallet import approval

        self._inner = approval.PinApprover(pin)

    def approve(self, challenge):
        from core.wallet import limits

        decision = self._inner.approve(challenge)
        limits.set_frozen(True)
        return decision


def test_a_freeze_before_the_claim_leaves_the_proposal_approvable(env):
    from core.wallet import lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=_FreezingApprover(PIN))
        assert exc.value.context["reason"] == "panic_freeze_blocks_paykit_claim"
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert limits.reservation_state(parked.proposal_id) == "", "nothing was held"
        assert resource.paid_requests == []
        limits.set_frozen(False)
        assert _approve(parked.proposal_id).state == proposals.STATE_CONFIRMED and len(resource.paid_requests) == 1


def test_a_freeze_between_the_signature_and_the_request_keeps_the_payment_home(env, monkeypatch):
    from core.wallet import limits, proposals, signers
    from core.wallet.errors import WalletFault

    real = signers.signer_for

    class _FreezesAfterSigning:
        def __init__(self, inner):
            self._inner = inner
            self.public_key = inner.public_key

        def sign(self, message):
            signature = self._inner.sign(message)
            limits.set_frozen(True)
            return signature

    monkeypatch.setattr(signers, "signer_for", lambda *a, **k: _FreezesAfterSigning(real(*a, **k)))
    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.context["reason"] == "panic_freeze_blocks_paykit_delivery"
        assert resource.paid_requests == [] and resource.landed == []
    assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(parked.proposal_id) == "released"


@pytest.mark.parametrize("rig", [ScriptedPayKitResource, ScriptedMppResource], ids=["x402", "mpp"])
def test_a_payment_the_freeze_kept_home_is_receipted_and_its_request_can_be_paid_once_unfrozen(env, monkeypatch, rig):
    """The freeze lands between the first signature and the request, for an x402 offer and for an MPP charge. That
    payment ends with its receipt and its effect resolved, and holds nothing against the limits: once the owner lifts
    the freeze, the same request parks a new payment that fits under a per-payee limit of one payment and pays once."""
    from core.runtime_continuity import find_active_unresolved_effect
    from core.wallet import limits, proposals, receipts, reconciliation, signers
    from core.wallet.errors import WalletFault

    real = signers.signer_for
    froze = []

    class _FreezesAfterTheFirstSignature:
        def __init__(self, inner):
            self._inner = inner
            self.public_key = inner.public_key

        def sign(self, message):
            signature = self._inner.sign(message)
            if not froze:
                froze.append(True)
                limits.set_frozen(True)
            return signature

    monkeypatch.setattr(signers, "signer_for", lambda *a, **k: _FreezesAfterTheFirstSignature(real(*a, **k)))
    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=2000, daily_minor=2500, per_destination_daily_minor=2500))
    with rig(env["rpc"]) as resource:
        kept_home = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            _approve(kept_home.proposal_id)
        assert exc.value.context["reason"] == "panic_freeze_blocks_paykit_delivery"
        ended = [(r.get("state"), (r.get("refusal") or {}).get("reason")) for r in receipts.list_receipts() if r.get("proposal_id") == kept_home.proposal_id]
        assert ended == [(proposals.STATE_FAILED, "panic_freeze_blocks_paykit_delivery")]
        assert find_active_unresolved_effect(reconciliation.logical_effect_id(kept_home.proposal_id)) is None, "its effect is resolved"
        limits.set_frozen(False)
        again = _park(resource, profile)
        assert again.proposal_id != kept_home.proposal_id
        assert _approve(again.proposal_id).state == proposals.STATE_CONFIRMED
        assert len(resource.paid_requests) == 1 and len(resource.landed) == 1


def test_a_payment_built_without_the_guarded_signature_never_leaves(env, monkeypatch):
    from core.wallet import limits, paykit_x402, proposals
    from core.wallet.errors import WalletFault

    monkeypatch.setattr(paykit_x402, "build_payment_header", lambda binding, signer, *, blockhash: ("PAYMENT-SIGNATURE", "e30="))
    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.context["reason"] == "paykit_built_without_signing"
        assert resource.paid_requests == []
    assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(parked.proposal_id) == "released"


def test_a_wallet_that_is_not_a_pocket_wallet_never_reaches_a_signer(env, monkeypatch):
    from core.wallet import custody, limits, proposals, signers
    from core.wallet.errors import WalletFault

    asked: list[str] = []
    monkeypatch.setattr(signers, "signer_for", lambda *a, **k: asked.append("signer") or (_ for _ in ()).throw(AssertionError("signer asked")))
    profile = custody.register_external_signer_wallet(_key())
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.context["reason"] == "paykit_external_signer_not_supported"
        assert resource.paid_requests == []
    assert asked == []
    assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert limits.reservation_state(parked.proposal_id) == ""


def test_paykits_own_amount_guard_stays_armed_for_an_mpp_charge(env):
    """The stored charge is tampered to ask more than the proposal's amount: pay-kit's own guard (the approved amount
    as its maximum) refuses to build, before this wallet's guard is even reached."""
    from core.wallet import limits, paykit_mpp, proposals
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket()
    with ScriptedMppResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with connection() as conn:
            row = conn.execute("SELECT offer_json FROM wallet_x402_bindings WHERE proposal_id = ?", (parked.proposal_id,)).fetchone()
            offer = json.loads(row[0])
            request = json.loads(base64.urlsafe_b64decode(offer["challenge"]["request"] + "=" * (-len(offer["challenge"]["request"]) % 4)))
            request["amount"] = "1501"
            offer["challenge"]["request"] = paykit_mpp._mpp("core.base64url").encode_json(request)
            conn.execute("UPDATE wallet_x402_bindings SET offer_json = ? WHERE proposal_id = ?", (json.dumps(offer), parked.proposal_id))
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert str(exc.value.context["reason"]).startswith("paykit_build_failed:")
        assert resource.paid_requests == []
    assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
    assert limits.reservation_state(parked.proposal_id) == "released"


def test_a_request_whose_payment_is_in_flight_is_never_parked_again(env):
    from core.wallet import proposals

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        for state in (proposals.STATE_APPROVED, proposals.STATE_SIGNED):
            assert proposals.transition(parked.proposal_id, state, detail={"reason": "test"}) is not None
            again = _park(resource, profile)
            assert again.proposal_id == parked.proposal_id
            assert len(resource.requests) == 1, "the request is not sent again while its payment is in flight"
        assert [p.proposal_id for p in proposals.list_proposals()] == [parked.proposal_id]


# --- request and offer bounds ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "body", "reason"), [
    ("TRACE", b"", "request_method_not_supported"),
    ("CONNECT", b"", "request_method_not_supported"),
    ("POST", b"x" * (64 * 1024 + 1), "request_body_too_large"),
])
def test_a_request_outside_the_bounds_is_refused_before_it_is_sent(env, method, body, reason):
    from core.wallet import paykit_x402, proposals
    from core.wallet.errors import WalletFault

    assert len(body) <= paykit_x402.MAX_REQUEST_BODY_BYTES or reason == "request_body_too_large"
    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile, method=method, body=body)
        assert exc.value.context["reason"] == reason
        assert resource.requests == [] and proposals.list_proposals() == []


@pytest.mark.parametrize("amount", ["0", "-1500"])
def test_an_offer_for_nothing_or_less_is_refused(amount):
    from core.wallet import chains, paykit_x402
    from core.wallet.errors import WalletFault

    offer = {"scheme": "exact", "network": DEVNET_CAIP2, "amount": amount, "asset": "SOL", "payTo": OTHER_DESTINATION, "extra": {"feePayer": _key()}}
    with pytest.raises(WalletFault) as exc:
        paykit_x402._terms_from_requirement(offer, wallet_network=chains.SOLANA_DEVNET, source_context=None)
    assert exc.value.context["reason"] == "offer_amount_invalid"


# --- a token payment end to end ----------------------------------------------------------------------------------------

def test_a_usdc_payment_is_paid_once_through_the_guard_and_proven_on_chain(env):
    from solders.pubkey import Pubkey
    from solders.token.state import Mint, TokenAccount, TokenAccountState

    from core.wallet import limits, paykit_x402, proposals, svm_tokens, x402
    from tests.wallet._rig_paykit import PAID_BODY

    usdc = _usdc()
    rpc = env["rpc"]
    profile = _pocket()

    def token_account(owner, amount):
        return {"owner": svm_tokens.TOKEN_PROGRAM, "data": bytes(TokenAccount(
            mint=Pubkey.from_string(usdc.address), owner=Pubkey.from_string(owner), amount=amount, delegate=None,
            state=TokenAccountState.Initialized, is_native=None, delegated_amount=0, close_authority=None))}

    rpc.accounts[usdc.address] = {"owner": svm_tokens.TOKEN_PROGRAM, "data": bytes(Mint(mint_authority=None, supply=10**12, decimals=usdc.decimals, is_initialized=True, freeze_authority=None))}
    rpc.accounts[svm_tokens.associated_account(profile.public_key, usdc.address)] = token_account(profile.public_key, 1_000_000)
    rpc.accounts[svm_tokens.associated_account(OTHER_DESTINATION, usdc.address)] = token_account(OTHER_DESTINATION, 0)
    with ScriptedPayKitResource(rpc, asset=usdc.address) as resource:
        parked = _park(resource, profile)
        proposal = proposals.get_proposal(parked.proposal_id)
        assert proposal.asset == "USDC" and proposal.amount_minor == 1500 and proposal.destination == OTHER_DESTINATION
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED and receipt.tx_signature == resource.landed[0]
        assert len(resource.paid_requests) == 1 and paykit_x402.delivered_body(parked.proposal_id) == PAID_BODY
    assert limits.reservation_state(parked.proposal_id) == "settled"
    assert x402.binding_for_proposal(parked.proposal_id)["state"] == x402.BINDING_DELIVERED
    assert rpc.send_count() == 0
