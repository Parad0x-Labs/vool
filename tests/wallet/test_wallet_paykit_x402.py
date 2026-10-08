"""x402 through Solana pay-kit, with this wallet as the only money authority.

The owner's request meets a 402 -> pay-kit parses the offer -> ONE capped proposal bound to that exact request
(method, body, replayable headers) -> the ordinary approval -> pay-kit builds the payment and this wallet signs it only
after proving the message moves exactly the approved amount to the approved payee -> the SAME request goes once with
the payment -> settlement is believed from the chain. Every guard below is pinned by a sabotage in the series README.
"""
from __future__ import annotations

import json
import os

import pytest

from tests.wallet._rig import DESTINATION, OTHER_DESTINATION
from tests.wallet._rig_paykit import MAINNET_CAIP2, PAID_BODY, ScriptedPayKitResource

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]
PIN = "246810"
REQUEST_BODY = json.dumps({"question": "q3 revenue", "format": "json"}).encode()


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


# --- the working path ------------------------------------------------------------------------------------------------

def test_a_post_with_a_body_is_paid_once_and_the_same_request_is_delivered(env):
    from core.wallet import limits, paykit_x402, proposals, receipts, x402

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile, headers={"Content-Type": "application/json", "Authorization": "Bearer caller-secret", "Cookie": "sid=1"})
        assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED and parked.http_status == 402
        proposal = proposals.get_proposal(parked.proposal_id)
        assert proposal.origin == proposals.ORIGIN_X402_PAYKIT and proposal.state == proposals.STATE_PENDING_APPROVAL
        assert proposal.amount_minor == 1500 and proposal.destination == OTHER_DESTINATION
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED and receipt.tx_signature in env["rpc"].transactions
        assert len(resource.paid_requests) == 1, "exactly one paid request"
        paid = resource.paid_requests[0]
        assert paid["method"] == "POST" and paid["body"] == REQUEST_BODY and paid["content_type"] == "application/json"
        assert paid["authorization"] == "" and paid["cookie"] == "", "credentials are never stored or replayed with a payment"
        assert paykit_x402.delivered_body(parked.proposal_id) == PAID_BODY
        assert paykit_x402.delivered_body(parked.proposal_id) is None, "the delivered body is handed out once"
    binding = x402.binding_for_proposal(parked.proposal_id)
    assert binding["state"] == x402.BINDING_DELIVERED and binding["resource_status"] == 200 and binding["tx_signature"] == receipt.tx_signature
    assert "caller-secret" not in json.dumps(binding) and "sid=1" not in json.dumps(binding)
    bound = [r for r in receipts.list_receipts() if r.get("x402_paykit")]
    assert bound and bound[0]["x402_paykit"]["settlement"] == "settled" and bound[0]["x402_paykit"]["delivered"] is True
    assert limits.reservation_state(parked.proposal_id) == "settled"
    assert env["rpc"].send_count() == 0, "the wallet never broadcasts a transfer of its own for a pay-kit payment"


# --- guard: no payment without approval --------------------------------------------------------------------------

def test_nothing_is_signed_or_sent_before_the_owner_approves(env):
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        again = _park(resource, profile)
        assert again.proposal_id == parked.proposal_id, "the same request reuses the parked proposal"
        with pytest.raises(WalletFault) as retried:
            x402.retry_paid_resource(parked.proposal_id)
        assert retried.value.context["reason"] == "paykit_delivery_happens_at_approval"
        with pytest.raises(WalletFault) as wrong_pin:
            _approve(parked.proposal_id, pin="000000")
        assert wrong_pin.value.code == "wallet_approval_rejected"
        assert resource.paid_requests == [] and all(not r["paid"] for r in resource.requests)
        assert env["rpc"].transactions == {} and env["rpc"].send_count() == 0
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL


def test_an_external_signer_wallet_is_refused_before_any_signature(env):
    from core.wallet import lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as refused:
            lifecycle.default_lifecycle().request_external_signature(parked.proposal_id)
        assert refused.value.context["reason"] == "paykit_external_signer_not_supported"
        assert resource.paid_requests == [] and proposals.get_proposal(parked.proposal_id).state == proposals.STATE_PENDING_APPROVAL


# --- guard: the spend cap ------------------------------------------------------------------------------------------

def test_an_offer_over_the_cap_is_refused_before_any_proposal(env, monkeypatch):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "1000")
    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"], amount_minor=1500) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "wallet_x402_cap_exceeded"
        assert proposals.list_proposals() == [] and resource.paid_requests == []


def test_the_wallets_own_spend_limit_still_rejects_at_prepare(env):
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=1000, daily_minor=10_000, per_destination_daily_minor=10_000))
    with ScriptedPayKitResource(env["rpc"], amount_minor=1500) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "wallet_limit_exceeded"
        assert [p.state for p in proposals.list_proposals()] == [proposals.STATE_REJECTED] and resource.paid_requests == []


# --- guard: network and asset ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("kwargs", "reason"), [
    ({"asset": "So11111111111111111111111111111111111111112"}, "offer_asset_not_registered"),
    ({"asset": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"}, "offer_asset_not_registered"),
    ({"fee_payer": False}, "offer_fee_payer_missing"),
    ({"pay_to": "not-a-solana-address"}, "offer_payee_invalid"),
])
def test_an_offer_this_wallet_cannot_honour_is_refused_before_any_proposal(env, kwargs, reason):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"], **kwargs) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "x402_scheme_unavailable" and exc.value.context["reason"] == reason
        assert proposals.list_proposals() == [] and resource.paid_requests == []


def test_an_offer_on_another_network_is_never_parked(env):
    """pay-kit's selection only PREFERS the wallet's network; this wallet's own terms check is what refuses."""
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()  # a Solana Devnet wallet
    with ScriptedPayKitResource(env["rpc"], network=MAINNET_CAIP2) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "x402_scheme_unavailable" and exc.value.context["reason"] == "offer_network_differs_from_wallet"
        assert proposals.list_proposals() == [] and resource.paid_requests == []


def test_the_terms_check_refuses_an_offer_on_a_network_other_than_the_wallets(env):
    from core.wallet import chains, paykit_x402
    from core.wallet.errors import WalletFault

    offer = {"scheme": "exact", "network": MAINNET_CAIP2, "amount": "1500", "asset": "SOL", "payTo": OTHER_DESTINATION, "extra": {"feePayer": DESTINATION}}
    with pytest.raises(WalletFault) as exc:
        paykit_x402._terms_from_requirement(offer, wallet_network=chains.SOLANA_DEVNET, source_context=None)
    assert exc.value.context["reason"] == "offer_network_differs_from_wallet"


# --- guard: the signed message is exactly the approved transfer ------------------------------------------------------

@pytest.mark.parametrize(("field", "value"), [("payTo", DESTINATION), ("amount", "1499"), ("amount", "1501")])
def test_a_payee_or_amount_changed_after_approval_is_never_signed(env, field, value):
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with connection() as conn:
            row = conn.execute("SELECT offer_json FROM wallet_x402_bindings WHERE proposal_id = ?", (parked.proposal_id,)).fetchone()
            offer = json.loads(row[0])
            offer["requirement"][field] = value
            conn.execute("UPDATE wallet_x402_bindings SET offer_json = ? WHERE proposal_id = ?", (json.dumps(offer), parked.proposal_id))
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.code == "wallet_signature_invalid"
        assert exc.value.context["reason"] in {"paykit_transfer_parties_differ", "paykit_transfer_amount_differs"}
        assert resource.paid_requests == [] and env["rpc"].transactions == {}
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
        assert limits.reservation_state(parked.proposal_id) == "released"


def _message(instructions, *, fee_payer, payer):
    from solders.hash import Hash
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey

    message = MessageV0.try_compile(Pubkey.from_string(fee_payer), instructions, [], Hash.default())
    _ = payer
    return bytes(to_bytes_versioned(message))


def _system_transfer(src, dst, lamports):
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    return transfer(TransferParams(from_pubkey=Pubkey.from_string(src), to_pubkey=Pubkey.from_string(dst), lamports=lamports))


def _memo():
    from solders.instruction import Instruction
    from solders.pubkey import Pubkey

    from core.wallet import paykit_x402

    return Instruction(Pubkey.from_string(paykit_x402.MEMO_PROGRAM), b"nonce", [])


def test_the_signature_guard_accepts_only_the_exact_transfer():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    payer, fee_payer = str(Keypair().pubkey()), str(Keypair().pubkey())
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    good = _message([_system_transfer(payer, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer, payer=payer)
    assert paykit_x402.verify_payment_message(good, payer=payer, **terms)["amount_minor"] == 1500
    cases = {
        "a second transfer": [_system_transfer(payer, OTHER_DESTINATION, 1500), _system_transfer(payer, DESTINATION, 1), _memo()],
        "another payee": [_system_transfer(payer, DESTINATION, 1500), _memo()],
        "another amount": [_system_transfer(payer, OTHER_DESTINATION, 1501), _memo()],
        "no transfer": [_memo()],
    }
    for name, instructions in cases.items():
        with pytest.raises(WalletFault):
            paykit_x402.verify_payment_message(_message(instructions, fee_payer=fee_payer, payer=payer), payer=payer, **terms)
        assert name
    with pytest.raises(WalletFault) as self_fee:
        paykit_x402.verify_payment_message(_message([_system_transfer(payer, OTHER_DESTINATION, 1500)], fee_payer=payer, payer=payer), payer=payer, **{**terms, "fee_payer": payer})
    assert self_fee.value.context["reason"] == "paykit_fee_payer_not_the_offer"
    with pytest.raises(WalletFault) as legacy:
        paykit_x402.verify_payment_message(good[1:], payer=payer, **terms)
    assert legacy.value.context["reason"] in {"paykit_message_not_v0", "paykit_message_unparseable"}


def test_the_guarded_signer_signs_once():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    key = Keypair()

    class _Signer:
        public_key = str(key.pubkey())

        def sign(self, message):
            return bytes(key.sign_message(message))

    fee_payer = str(Keypair().pubkey())
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    guarded = paykit_x402.GuardedSigner(_Signer(), terms=terms)
    message = _message([_system_transfer(_Signer.public_key, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer, payer=_Signer.public_key)
    assert len(guarded.sign(message)) == 64
    with pytest.raises(WalletFault) as exc:
        guarded.sign(message)
    assert exc.value.context["reason"] == "paykit_second_signature_refused"
    assert not hasattr(guarded.keypair, "secret") and not hasattr(guarded.keypair, "sign_message"), "pay-kit only ever sees a public key"


# --- guard: replay with another body or method; a second retry ------------------------------------------------------

def test_another_body_or_method_is_another_request_that_needs_its_own_approval(env):
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
        assert len(resource.paid_requests) == 1
        other_body = _park(resource, profile, body=b'{"question": "everything"}')
        other_method = _park(resource, profile, method="PUT")
        assert len({parked.proposal_id, other_body.proposal_id, other_method.proposal_id}) == 3
        for outcome in (other_body, other_method):
            assert proposals.get_proposal(outcome.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert len(resource.paid_requests) == 1, "no payment rode another request"
        assert [r["paid"] for r in resource.requests].count(True) == 1
        with pytest.raises(WalletFault) as same_again:
            _park(resource, profile)
        assert same_again.value.code == "wallet_duplicate_payment" and same_again.value.context["reason"] == "paykit_request_already_paid"


def test_a_paid_request_is_never_paid_or_sent_again(env):
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
        with pytest.raises(WalletFault) as approve_again:
            _approve(parked.proposal_id)
        assert approve_again.value.code == "wallet_duplicate_payment"
        handed = x402.retry_paid_resource(parked.proposal_id)
        assert handed.status == x402.OUTCOME_DELIVERED and handed.body == PAID_BODY, "a retry hands back the delivered body"
        with pytest.raises(WalletFault) as retry:
            x402.retry_paid_resource(parked.proposal_id)
        assert retry.value.context["reason"] == "paykit_delivery_happens_at_approval"
        sent = len(resource.requests)
        with pytest.raises(WalletFault) as same_again:
            _park(resource, profile)
        assert same_again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.requests) == sent, "the paid request is refused before it is sent"
        assert len(resource.paid_requests) == 1 and len(env["rpc"].transactions) == 1, "nothing was sent again"


# --- the ordinary x402 door -------------------------------------------------------------------------------------------

def test_the_ordinary_x402_door_hands_a_canonical_solana_offer_to_paykit(env):
    """The agent tool and the wallet API both call x402.fetch_paid_resource: a canonical Solana offer it meets is
    parked on the pay-kit lane from the 402 already received, and paid only on approval."""
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert parked.status == x402.OUTCOME_PAYMENT_REQUIRED
        assert proposals.get_proposal(parked.proposal_id).origin == proposals.ORIGIN_X402_PAYKIT
        again = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert again.proposal_id == parked.proposal_id and len(resource.requests) == 1, "a parked request is not sent again"
        receipt = _approve(parked.proposal_id)
        assert receipt.state == proposals.STATE_CONFIRMED
        assert [r["method"] for r in resource.paid_requests] == ["GET"]
        assert x402.retry_paid_resource(parked.proposal_id).body == PAID_BODY
        with pytest.raises(WalletFault) as paid:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert paid.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.paid_requests) == 1


def test_vools_own_v1_solana_offer_stays_on_its_lane_with_paykit_installed(env):
    from core.wallet import proposals, x402
    from tests.wallet._rig import ScriptedX402Resource

    profile = _pocket()
    with ScriptedX402Resource(env["rpc"]) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert proposals.get_proposal(parked.proposal_id).origin == proposals.ORIGIN_X402


# --- settlement is the chain's word -----------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["no_settlement_header", "wrong_settlement"])
def test_a_delivery_the_chain_does_not_prove_keeps_the_hold_as_unknown(env, mode):
    from core.wallet import limits, paykit_x402, proposals, receipts

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"], mode=mode) as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
    assert receipt.state == proposals.STATE_BROADCAST
    assert limits.reservation_state(parked.proposal_id) == "reserved", "unknown never releases or settles the hold"
    assert paykit_x402.delivered_body(parked.proposal_id) is None
    bound = [r for r in receipts.list_receipts() if r.get("x402_paykit")]
    assert bound[0]["x402_paykit"]["settlement"] == "unknown" and bound[0]["x402_paykit"]["delivered"] is False
    # a transaction id the resource named but the chain did not prove ours is never the proposal's evidence
    assert proposals.get_proposal(parked.proposal_id).tx_signature == ""
    assert bound[0]["x402_paykit"]["claimed_transaction_unproven"] is (mode == "wrong_settlement")
    from core.wallet import reconciliation

    assert reconciliation._payment_resolver({"resource_identity": parked.proposal_id}).outcome.name == "STILL_UNKNOWN"


def test_a_settlement_naming_another_of_this_wallets_payments_is_not_proof(env):
    """The named transaction is real, successful and signed by this wallet, but over ANOTHER message: it proves the
    earlier payment, never this one."""
    from core.wallet import limits, proposals

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        first = _park(resource, profile)
        assert _approve(first.proposal_id).state == proposals.STATE_CONFIRMED
        resource.mode = "names_earlier_payment"
        second = _park(resource, profile, body=b'{"question": "q4 revenue"}')
        receipt = _approve(second.proposal_id)
    assert receipt.state == proposals.STATE_BROADCAST
    assert proposals.get_proposal(second.proposal_id).tx_signature == ""
    assert limits.reservation_state(second.proposal_id) == "reserved"


def test_a_payment_that_failed_on_chain_releases_the_hold(env):
    from core.wallet import limits, paykit_x402, proposals

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"], mode="settle_fails") as resource:
        parked = _park(resource, profile)
        receipt = _approve(parked.proposal_id)
    assert receipt.state == proposals.STATE_FAILED and limits.reservation_state(parked.proposal_id) == "released"
    assert paykit_x402.delivered_body(parked.proposal_id) is None


@pytest.mark.parametrize("paid_answer", ["status:304", "status:300", "redirect", "oversize", "drop"])
def test_a_paid_request_that_left_keeps_the_hold_whatever_the_answer(env, paid_answer):
    """The payment lands, then the resource answers oddly (a bare 3xx, a redirect, an oversize body) or not at all.
    The request left with the payment, so the outcome is unknown: the hold stays, nothing is recorded as failed, no
    redirect is followed, and the same request is never paid again."""
    from core.wallet import limits, proposals, receipts
    from core.wallet.errors import WalletFault

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"], paid_answer=paid_answer) as resource:
        parked = _park(resource, profile)
        with pytest.raises(WalletFault) as exc:
            _approve(parked.proposal_id)
        assert exc.value.code == "wallet_broadcast_failed" and exc.value.context["reason"].startswith("submit_unknown:")
        assert len(resource.landed) == 1 and len(resource.paid_requests) == 1
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_BROADCAST
        assert limits.reservation_state(parked.proposal_id) == "reserved", "a payment that may have landed is never released"
        states = [r.get("state") for r in receipts.list_receipts() if r.get("proposal_id") == parked.proposal_id]
        assert states and proposals.STATE_FAILED not in states
        with pytest.raises(WalletFault) as again:
            _park(resource, profile)
        assert again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.landed) == 1 and len(resource.requests) == 2, "no second payment and no redirect followed"


@pytest.mark.parametrize("refusal", ["loopback_off", "loopback_name", "dns_failure", "door_veto"])
def test_a_paid_request_refused_before_its_socket_releases_the_hold_and_can_be_parked_again(env, refusal):
    """The paid request is refused before its socket opens: by the target check (the loopback switch off for it, a
    loopback name answering a public address, a failed name lookup) or by the network door's own veto. Nothing was
    sent, so the proposal fails, the hold is released and the same request can be parked again."""
    import socket
    import types

    from core import remote_fetch_policy
    from core.wallet import limits, outbound, proposals, receipts
    from core.wallet.errors import WalletFault

    def lookup(*_args, **_kwargs):
        if refusal == "dns_failure":
            raise socket.gaierror("no such name")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        with pytest.MonkeyPatch.context() as patch:
            # only the paid request's door changes: the wallet's own RPC reads go their own way
            if refusal in ("loopback_off", "loopback_name"):
                patch.setattr(outbound, "_loopback_switch", lambda: False)
            if refusal in ("loopback_name", "dns_failure"):
                patch.setattr(outbound, "socket", types.SimpleNamespace(getaddrinfo=lookup))
            veto = remote_fetch_policy._REMOTE_FETCH_FORBIDDEN.set(refusal == "door_veto")
            try:
                with pytest.raises(WalletFault) as exc:
                    _approve(parked.proposal_id)
            finally:
                remote_fetch_policy._REMOTE_FETCH_FORBIDDEN.reset(veto)
        assert exc.value.code == "wallet_broadcast_failed" and exc.value.context["reason"].startswith("submit_refused:")
        assert resource.paid_requests == [] and resource.landed == [] and len(resource.requests) == 1
        assert proposals.get_proposal(parked.proposal_id).state == proposals.STATE_FAILED
        assert limits.reservation_state(parked.proposal_id) == "released"
        recorded = [r for r in receipts.list_receipts() if r.get("proposal_id") == parked.proposal_id]
        assert [(r.get("state"), bool(r.get("fault_code"))) for r in recorded] == [(proposals.STATE_FAILED, True)], "one failed receipt, naming the fault"
        again = _park(resource, profile)
        assert again.status == "payment_required" and again.proposal_id != parked.proposal_id


def test_a_challenge_parked_for_a_request_already_paid_never_rebinds_it(env):
    """park_challenge itself refuses a request this lane already paid, whoever calls it, even when the resource now
    asks on other terms: the request is never rebound to a second proposal."""
    from core.wallet import outbound, paykit_x402, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    headers = {"Content-Type": "application/json"}
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
        resource.amount_minor = 1400
        challenge = outbound.fetch(resource.url, method="POST", headers=headers, body=REQUEST_BODY)
        assert challenge["status"] == 402
        with pytest.raises(WalletFault) as again:
            paykit_x402.park_challenge(challenge, url=resource.url, method="POST", headers=headers, body=REQUEST_BODY, wallet_id=profile.wallet_id)
        assert again.value.context["reason"] == "paykit_request_already_paid"
        assert len(resource.landed) == 1
    binding = x402.binding_for_digest(paykit_x402.request_digest("POST", resource.url, REQUEST_BODY))
    assert binding["proposal_id"] == parked.proposal_id


# --- the optional dependency and the legacy lane --------------------------------------------------------------------

def test_without_paykit_the_lane_refuses_before_sending_anything(env, monkeypatch):
    from core.wallet import paykit_x402, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    monkeypatch.setattr(paykit_x402, "availability", lambda: (False, "paykit_not_installed"))
    with ScriptedPayKitResource(env["rpc"]) as resource:
        with pytest.raises(WalletFault) as exc:
            _park(resource, profile)
        assert exc.value.code == "wallet_paykit_unavailable" and exc.value.context["reason"] == "paykit_not_installed"
        assert "optional pay extra" in exc.value.user_message and "EVM" not in exc.value.user_message
        assert resource.requests == [] and proposals.list_proposals() == []


def test_paying_creates_no_file_in_the_working_directory(env, tmp_path, monkeypatch):
    """pay-kit's server-side config can persist a generated secret to ./.env; the client lane must never reach it."""
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    profile = _pocket()
    with ScriptedPayKitResource(env["rpc"]) as resource:
        parked = _park(resource, profile)
        _approve(parked.proposal_id)
    assert os.listdir(work) == []


def test_loading_paykit_never_logs_its_demo_signer():
    """pay-kit materialises its shipped demo signer at import and warns about it; this lane never uses that key, so
    the owner's logs never name it."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    probe = "from core.wallet import paykit_x402\nassert paykit_x402.availability()[0]\npaykit_x402._payment_module()\n"
    done = subprocess.run([sys.executable, "-c", probe], cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert "demo signer" not in done.stderr and "demo signer" not in done.stdout
