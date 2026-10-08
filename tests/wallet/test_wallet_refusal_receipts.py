"""Every refusal the payment lifecycle makes leaves a durable receipt, with nothing charged.

A failed simulation, an endpoint that proves another chain, a ceiling (at prepare, or at the claim when the limits
changed in between), a claim refused because the payment's effect is already in flight or a session budget refused it,
each refused approval and the owner's own rejection are decisions money control made about one payment. Each records a receipt under its real
fault code, in the state the payment is left in, with a zero charged amount and fee, in the same transaction as that
state: a payment never ends without its receipt.
"""
from __future__ import annotations

import pytest

from tests.wallet._rig import DESTINATION

pytestmark = [pytest.mark.safety]
PIN = "246810"


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _propose(profile, amount_minor=10_000):
    from core.wallet import proposals

    return proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=amount_minor, asset="SOL", origin=proposals.ORIGIN_USER)


def _receipts(proposal_id):
    from core.wallet import receipts

    return list(reversed([r for r in receipts.list_receipts() if r["proposal_id"] == proposal_id]))


def _assert_refusal_receipt(proposal_id, *, state, fault_code, hold="", **detail):
    from core.wallet import limits, proposals

    assert proposals.get_proposal(proposal_id).state == state
    [receipt] = _receipts(proposal_id)
    assert (receipt["state"], receipt["fault_code"], receipt["tx_signature"]) == (state, fault_code, "")
    refusal = receipt["refusal"]
    assert (refusal["charged_amount_minor"], refusal["charged_fee_minor"]) == (0, 0)
    assert {k: refusal[k] for k in detail} == detail
    assert limits.reservation_state(proposal_id) == hold, "nothing was held, or the hold was released"


def test_prepare_limit_failure_has_truthful_zero_charge_receipt(wallet_env):
    from core.wallet import lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=5_000, daily_minor=1_000_000, per_destination_daily_minor=1_000_000))
    proposal = _propose(profile)
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    assert exc.value.code == "wallet_limit_exceeded"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", limit="per_transaction")
    assert wallet_env["rpc"].send_count() == 0


def test_limit_refusal_has_a_durable_wallet_receipt(wallet_env):
    """The owner lowers the daily ceiling while the payment waits for approval: the claim's reservation refuses it."""
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    proposal = _propose(profile)
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=1_000_000, daily_minor=5_000, per_destination_daily_minor=1_000_000))
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_limit_exceeded"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", limit="daily")
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("failure", ["simulation_refused", "endpoint_on_another_chain"])
def test_prepare_simulation_failure_has_truthful_zero_charge_receipt(wallet_env, failure):
    from core.wallet import lifecycle, proposals
    from core.wallet.errors import WalletFault

    if failure == "simulation_refused":
        wallet_env["rpc"].simulate_ok = False
    else:
        wallet_env["rpc"].genesis_hash = "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY"  # Solana Testnet's, not Devnet's
    proposal = _propose(_pocket())
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    expected = "wallet_simulation_failed" if failure == "simulation_refused" else "wallet_chain_identity_mismatch"
    assert exc.value.code == expected
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_FAILED, fault_code=expected)
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("refusals", [1, 5])
def test_refused_pin_approvals_have_durable_receipts(wallet_env, refusals):
    """A wrong PIN is a counted refusal, not the end of the payment: it stays pending, and the refusal leaves a receipt
    in that state. The refusal that reaches the lock rejects the payment, with its receipt in the same transaction."""
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    proposal = _propose(_pocket())
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    for _ in range(refusals):
        with pytest.raises(WalletFault) as exc:
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("999999"))
        assert exc.value.code == "wallet_approval_rejected"
    locked = refusals >= lifecycle.MAX_APPROVAL_ATTEMPTS
    seen = [(r["state"], r["fault_code"], r["refusal"]["reason"], r["refusal"]["attempts"], r["refusal"]["charged_amount_minor"], r["refusal"]["charged_fee_minor"])
            for r in _receipts(proposal.proposal_id)]
    expected = [(proposals.STATE_PENDING_APPROVAL, "wallet_approval_rejected", "challenge_mismatch_or_wrong_pin", attempt, 0, 0) for attempt in range(1, refusals + 1)]
    if locked:
        expected[-1] = (proposals.STATE_REJECTED, "wallet_approval_rejected", "approval_attempts_exhausted", refusals, 0, 0)
    assert seen == expected
    assert proposals.get_proposal(proposal.proposal_id).state == (proposals.STATE_REJECTED if locked else proposals.STATE_PENDING_APPROVAL)
    assert limits.reservation_state(proposal.proposal_id) == "" and wallet_env["rpc"].send_count() == 0


def test_a_payment_whose_effect_is_already_in_flight_leaves_a_receipt(wallet_env):
    from core.runtime_continuity import reserve_logical_effect
    from core.wallet import approval, lifecycle, limits, proposals, reconciliation
    from core.wallet.errors import WalletFault

    proposal = _propose(_pocket())
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    # something else holds this payment's logical effect (a crashed earlier attempt, say): the claim refuses it
    reserve_logical_effect(intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": proposal.proposal_id}, resource_identity=proposal.proposal_id)
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_duplicate_payment"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_duplicate_payment", hold=limits.RESERVATION_RELEASED, reason="effect_in_flight")
    assert wallet_env["rpc"].send_count() == 0


def test_a_session_budget_that_refuses_the_payment_leaves_a_receipt(wallet_env):
    eb = pytest.importorskip("core.effect_budget")
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    eb.reset_effect_budget_process_state()
    token = eb.grant_operator_budget_authority("one transaction per session")  # outside any effect scope
    eb.apply_operator_adjustment(token, [eb.BudgetAdjustment(budget_class="wallet_transaction", scope=eb.SCOPE_SESSION, new_limit=1, window_seconds=0.0, note="one")])
    profile = _pocket()
    first, second = _propose(profile, 100), _propose(profile, 200)
    ctx = {"session_id": "s-receipt", "turn_id": "t-receipt", "request_id": "r-receipt", "workspace_root": "/wp"}
    engine = lifecycle.default_lifecycle(source_context=ctx)
    engine.prepare(first.proposal_id)
    engine.prepare(second.proposal_id)
    open_effect_receipt_scope(ctx)
    try:
        assert engine.approve_and_execute(first.proposal_id, approver=approval.PinApprover(PIN)).state == proposals.STATE_CONFIRMED
        with pytest.raises(WalletFault) as exc:
            engine.approve_and_execute(second.proposal_id, approver=approval.PinApprover(PIN))
    finally:
        close_effect_receipt_scope()
    assert exc.value.code == "wallet_limit_exceeded" and exc.value.context["reason"].startswith("effect_gateway_refused")
    _assert_refusal_receipt(second.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", hold=limits.RESERVATION_RELEASED, reason="effect_gateway_refused")
    assert wallet_env["rpc"].send_count() == 1


def test_a_refusal_whose_receipt_cannot_be_written_does_not_end_the_payment(wallet_env, monkeypatch):
    """The refusal and its receipt are one transaction: when the receipt cannot be written, the payment is not left
    rejected without one. It stays where it was, and the failure is raised."""
    from core.wallet import lifecycle, limits, proposals, receipts

    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=5_000, daily_minor=1_000_000, per_destination_daily_minor=1_000_000))
    proposal = _propose(profile)

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(receipts, "_record", disk_full)
    with pytest.raises(OSError):
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    monkeypatch.undo()
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_SIMULATED
    assert _receipts(proposal.proposal_id) == []


@pytest.mark.parametrize("moment", ["pending_approval", "after_claim"])
def test_the_owners_rejection_leaves_a_receipt(wallet_env, moment):
    import json

    from apps.vool_api_server import create_app
    from core.wallet import lifecycle, limits, proposals
    from core.web.api.runtime import RuntimeServices
    from tests.asgi_harness import asgi_request

    engine = lifecycle.default_lifecycle()
    if moment == "pending_approval":
        proposal = engine.prepare(_propose(_pocket()).proposal_id)
    else:
        # an external signer's payment is claimed (its spend held) while the signing request waits
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from core.vool_wallet import b58encode
        from core.wallet import custody

        public_key = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        proposal = engine.prepare(_propose(custody.register_external_signer_wallet(public_key, label="ext")).proposal_id)
        engine.request_external_signature(proposal.proposal_id)
    app = create_app(RuntimeServices(display_name="VOOL"))
    _status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/reject", body=json.dumps({"proposal_id": proposal.proposal_id}).encode(),
                                         headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"})
    assert json.loads(raw)["rejected"] is True
    if moment == "pending_approval":
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", reason="owner_rejected")
    else:
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="owner_rejected_after_claim")
    assert wallet_env["rpc"].send_count() == 0
