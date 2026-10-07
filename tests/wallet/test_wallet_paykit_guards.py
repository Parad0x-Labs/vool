"""The pay-kit lane's money guards, each pinned on its own.

The signature guard (``verify_payment_message``) stands between pay-kit and this wallet's key: every rule it holds has
a case here that breaks exactly that rule, for SOL and for a registered token (USDC), and pay-kit's own USDC payment
passes it only as approved. Around it: settlement counts only the transaction it was asked about, the signer is never
asked before the guard passed, the panic freeze brakes before the claim and before the request, a wallet that is not
a pocket wallet never reaches a signer, a payment pay-kit did not sign through the guard never leaves, pay-kit's own
amount guard stays armed, a request in flight is never parked again, and the request and offer bounds hold. One token
payment goes end to end.
"""
from __future__ import annotations

import base64
import json

import pytest

from tests.wallet._rig import OTHER_DESTINATION
from tests.wallet._rig_paykit import DEVNET_CAIP2, ScriptedMppResource, ScriptedPayKitResource
from tests.wallet.test_wallet_paykit_x402 import PIN, _approve, _park, _pocket, env

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]


# --- building messages ---------------------------------------------------------------------------------------------

def _key() -> str:
    from solders.keypair import Keypair

    return str(Keypair().pubkey())


def _usdc():
    from core.wallet import chains

    return chains.asset_for(chains.SOLANA_DEVNET, "USDC")


def _compile(instructions, *, fee_payer, lookups=()) -> bytes:
    from solders.hash import Hash
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey

    return bytes(to_bytes_versioned(MessageV0.try_compile(Pubkey.from_string(fee_payer), instructions, list(lookups), Hash.default())))


def _ix(program, data, accounts=()):
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    return Instruction(Pubkey.from_string(program), bytes(data), [AccountMeta(Pubkey.from_string(k), signer, writable) for k, signer, writable in accounts])


def _sol(source, destination, lamports):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.SYSTEM_PROGRAM, (2).to_bytes(4, "little") + int(lamports).to_bytes(8, "little"), [(source, True, True), (destination, False, True)])


def _token(owner, pay_to, mint, amount, decimals, *, kind=12, source="", destination=""):
    from core.wallet import paykit_x402

    source = source or paykit_x402._ata(owner, mint, paykit_x402.TOKEN_PROGRAM)
    destination = destination or paykit_x402._ata(pay_to, mint, paykit_x402.TOKEN_PROGRAM)
    data = bytes([kind]) + int(amount).to_bytes(8, "little") + bytes([decimals])
    return _ix(paykit_x402.TOKEN_PROGRAM, data, [(source, False, True), (mint, False, False), (destination, False, True), (owner, True, False)])


def _memo(*accounts, signer=False):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.MEMO_PROGRAM, b"nonce", [(a, signer, False) for a in accounts])


def _budget(kind, value, size, *accounts):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.COMPUTE_BUDGET_PROGRAM, bytes([kind]) + int(value).to_bytes(size - 1, "little"), [(a, False, False) for a in accounts])


def _refused(message, **terms) -> str:
    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault) as exc:
        paykit_x402.verify_payment_message(message, **terms)
    assert exc.value.code == "wallet_signature_invalid"
    return str(exc.value.context["reason"])


# --- the signature guard, rule by rule -----------------------------------------------------------------------------

def _sol_case(name, payer, fee_payer):
    other = _key()
    transfer = _sol(payer, OTHER_DESTINATION, 1500)
    cases = {
        "a lookup table": ([transfer, _memo()], "paykit_message_uses_lookup_tables"),
        "a third signer": ([transfer, _memo(other, signer=True)], "paykit_unexpected_signers"),
        "a memo naming an account": ([transfer, _memo(other)], "paykit_memo_names_accounts"),
        "another program": ([transfer, _ix(other, b"\x01")], "paykit_unexpected_program"),
        "two memos": ([transfer, _memo(), _memo()], "paykit_instruction_count_differs"),
        "three compute-budget settings": ([_budget(2, 20_000, 5), _budget(3, 1, 9), _budget(3, 1, 9), transfer], "paykit_instruction_count_differs"),
        "a compute-budget setting of the wrong size": ([_budget(2, 20_000, 6), transfer], "paykit_unexpected_compute_budget_instruction"),
        "a compute-budget setting naming an account": ([_budget(2, 20_000, 5, other), transfer], "paykit_unexpected_compute_budget_instruction"),
    }
    instructions, reason = cases[name]
    lookups = ()
    if name == "a lookup table":
        from solders.address_lookup_table_account import AddressLookupTableAccount
        from solders.pubkey import Pubkey

        lookups = (AddressLookupTableAccount(key=Pubkey.from_string(_key()), addresses=[Pubkey.from_string(OTHER_DESTINATION)]),)
    return _compile(instructions, fee_payer=fee_payer, lookups=lookups), reason


@pytest.mark.parametrize("name", ["a lookup table", "a third signer", "a memo naming an account", "another program", "two memos",
                                  "three compute-budget settings", "a compute-budget setting of the wrong size",
                                  "a compute-budget setting naming an account"])
def test_the_guard_refuses_a_sol_payment_with(name):
    payer, fee_payer = _key(), _key()
    message, reason = _sol_case(name, payer, fee_payer)
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    assert _refused(message, **terms) == reason


def test_the_guard_refuses_bytes_that_are_not_exactly_the_message():
    from core.wallet import paykit_x402

    payer, fee_payer = _key(), _key()
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    good = _compile([_sol(payer, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer)
    assert paykit_x402.verify_payment_message(good, **terms)["amount_minor"] == 1500
    assert _refused(good + b"\x00", **terms) == "paykit_message_not_canonical"


@pytest.mark.parametrize("name", ["exact", "another payee's account", "another source account", "other decimals", "another amount",
                                  "a plain Transfer", "a System transfer beside it", "another program beside it"])
def test_the_guard_holds_a_token_payment_to_the_exact_transfer(name):
    from core.wallet import paykit_x402

    payer, fee_payer, other = _key(), _key(), _key()
    usdc = _usdc()
    mint, decimals = usdc.address, usdc.decimals
    exact = _token(payer, OTHER_DESTINATION, mint, 1500, decimals)
    cases = {
        "exact": ([_budget(2, 20_000, 5), _budget(3, 1, 9), exact, _memo()], None),
        "another payee's account": ([_token(payer, other, mint, 1500, decimals)], "paykit_transfer_parties_differ"),
        "another source account": ([_token(payer, OTHER_DESTINATION, mint, 1500, decimals, source=paykit_x402._ata(other, mint, paykit_x402.TOKEN_PROGRAM))], "paykit_transfer_parties_differ"),
        "other decimals": ([_token(payer, OTHER_DESTINATION, mint, 1500, 9)], "paykit_transfer_decimals_differ"),
        "another amount": ([_token(payer, OTHER_DESTINATION, mint, 1501, decimals)], "paykit_transfer_amount_differs"),
        "a plain Transfer": ([_token(payer, OTHER_DESTINATION, mint, 1500, decimals, kind=3)], "paykit_unexpected_token_instruction"),
        # with a mint approved, a System transfer out of this wallet is never "the transfer"
        "a System transfer beside it": ([exact, _sol(payer, other, 1)], "paykit_unexpected_program"),
        "another program beside it": ([exact, _ix(other, b"\x01")], "paykit_unexpected_program"),
    }
    instructions, reason = cases[name]
    message = _compile(instructions, fee_payer=fee_payer)
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": mint, "decimals": decimals, "fee_payer": fee_payer}
    if reason is None:
        proof = paykit_x402.verify_payment_message(message, **terms)
        assert proof["mint"] == mint and proof["amount_minor"] == 1500 and proof["pay_to"] == OTHER_DESTINATION
    else:
        assert _refused(message, **terms) == reason


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


def test_the_signer_is_never_asked_before_the_guard_passed():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    key = Keypair()
    asked: list[bytes] = []

    class _Spy:
        public_key = str(key.pubkey())

        def sign(self, message):
            asked.append(bytes(message))
            return bytes(key.sign_message(message))

    fee_payer = _key()
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    guarded = paykit_x402.GuardedSigner(_Spy(), terms=terms)
    with pytest.raises(WalletFault):
        guarded.sign(_compile([_sol(_Spy.public_key, OTHER_DESTINATION, 1501), _memo()], fee_payer=fee_payer))
    assert asked == [] and guarded.signature == b""


# --- settlement ------------------------------------------------------------------------------------------------------

def test_settlement_counts_only_the_transaction_it_was_asked_about():
    """An endpoint that answers the same successful transaction whatever id it is asked about proves only that id."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0, to_bytes_versioned
    from solders.transaction import VersionedTransaction

    from core.wallet import paykit_x402

    payer, fee_payer = Keypair(), Keypair()
    message = MessageV0.try_compile(fee_payer.pubkey(), [_sol(str(payer.pubkey()), OTHER_DESTINATION, 1500), _memo()], [], Hash.default())
    signed = bytes(to_bytes_versioned(message))
    landed = VersionedTransaction.populate(message, [fee_payer.sign_message(signed), payer.sign_message(signed)])
    wire = base64.b64encode(bytes(landed)).decode()

    class _SameAnswer:
        def _call(self, method, params):
            assert method == "getTransaction"
            return {"transaction": [wire, "base64"], "meta": {"err": None}}

    def verdict(tx_id):
        return paykit_x402.verify_settlement_on_chain(_SameAnswer(), tx_id, signed_message=signed, payer=str(payer.pubkey()), attempts=1)

    assert verdict(str(landed.signatures[0])) == "settled"
    assert verdict(str(Keypair().sign_message(b"another payment"))) == "unknown"


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
