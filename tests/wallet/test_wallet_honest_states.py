"""Honest re-proposal after a terminal refusal, truthful fee reservation on the Solana
lane, and truthful sponsorship on the EIP-3009 lane.

RED at the 1d610653 base: a rejected/expired/failed proposal permanently wedged its
idempotency key, so the same lawful request could never be proposed again
(``already_prepared`` on a dead row); the Solana lane reserved ZERO fee while the
payer pays the transaction fee at broadcast; and v2 EIP-3009 bindings recorded
``sponsored_gas=0`` while the flow in fact never charges the payer gas (the facilitator
settles on-chain).
"""
from __future__ import annotations

import json

import pytest

from tests.wallet._rig import DESTINATION, DEVNET, ScriptedX402Resource

pytestmark = [pytest.mark.safety]
PIN = "246810"


@pytest.fixture
def lane_env(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    return wallet_env


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def test_a_terminal_refusal_does_not_wedge_the_idempotency_key(lane_env):
    """A rejected proposal must not burn the request forever: the SAME paid resource can be
    re-proposed under a NEW proposal, prepared and paid. A CONFIRMED proposal still returns
    the one payment (never a re-pay)."""
    from core.wallet import approval, custody, lifecycle, proposals, x402

    with ScriptedX402Resource(lane_env["rpc"]) as resource:
        profile = _pocket(custody)
        first = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        proposals.transition(first.proposal_id, proposals.STATE_REJECTED, detail={"reason": "owner_rejected"}, expected_state=proposals.STATE_PENDING_APPROVAL)

        second = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert second.status == x402.OUTCOME_PAYMENT_REQUIRED
        assert second.proposal_id != first.proposal_id, "a dead proposal must not be resurrected"
        reborn = proposals.get_proposal(second.proposal_id)
        assert reborn.state == proposals.STATE_PENDING_APPROVAL

        receipt = lifecycle.default_lifecycle().approve_and_execute(second.proposal_id, approver=approval.PinApprover(PIN))
        delivered = x402.retry_paid_resource(second.proposal_id)
        assert delivered.status == x402.OUTCOME_DELIVERED and delivered.tx_signature == receipt.tx_signature
        assert lane_env["rpc"].send_count() == 1, "one payment for the whole journey"

        # confirmed stays confirmed: the same fetch returns the paid delivery, never a new proposal
        again = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert again.status == x402.OUTCOME_DELIVERED and again.proposal_id == second.proposal_id
        assert lane_env["rpc"].send_count() == 1


def test_the_solana_lane_reserves_the_transaction_fee(lane_env):
    """The payer pays the on-chain fee at broadcast, so the hold must include it: a daily
    cap that fits principal+fee passes, one that fits only the principal refuses."""
    from core.wallet import custody, lifecycle, limits, proposals
    from core.wallet.store import connection

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=1500, per_destination_daily_minor=10_000))
    from core.wallet import x402

    with ScriptedX402Resource(lane_env["rpc"], amount_minor=1000) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        # 1000 principal + 5000 simulated fee > 1500 daily: refused BEFORE any approval
        verdict = limits.check_limits(profile.wallet_id, "SOL", 1000, DESTINATION, fee_minor=5000, chain=DEVNET)
        assert not verdict.ok and verdict.limit == "daily"
        assert lane_env["rpc"].send_count() == 0

    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=6_000, per_destination_daily_minor=10_000))
    with ScriptedX402Resource(lane_env["rpc"], amount_minor=1000) as resource:
        parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = lifecycle.default_lifecycle()
        engine.prepare(parked.proposal_id)
        from core.wallet import approval as approval_module

        engine.approve_and_execute(parked.proposal_id, approver=approval_module.PinApprover(PIN))
        with connection() as conn:
            row = conn.execute("SELECT amount_minor, fee_minor FROM wallet_spend_ledger WHERE proposal_id = ?", (parked.proposal_id,)).fetchone()
        assert row is not None and row[0] == 1000 and row[1] == 5000, f"the hold must carry the fee, got {row}"
        assert lane_env["rpc"].send_count() == 1


def test_an_eip3009_binding_records_sponsored_gas_truthfully(lane_env, monkeypatch):
    """EIP-3009 exact: the payer signs an authorization and the FACILITATOR settles on-chain,
    so the binding says sponsored (fee 0 because the payer truly pays no gas), not the
    contradictory 'not sponsored, fee 0'."""
    from core.wallet import chains, custody, facilitators, x402
    from tests.wallet._rig_evm import FacilitatorSimulator, ScriptedEvmRpc, X402V2Resource

    with ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=("eip155:84532",)) as facilitator:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({"eip155:84532": rpc.url}))
        chains.invalidate_chain_identity(None)
        facilitators.discover(facilitator.url, facilitator_id="fac-sponsor")
        with X402V2Resource(facilitator, amount_minor=1000) as resource:
            from tests.wallet._rig_evm import EvmExtensionSigner

            with EvmExtensionSigner() as signer:
                profile = custody.register_external_signer_wallet(signer.address, network="eip155:84532")
                outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
                binding = x402.binding_for_proposal(outcome.proposal_id)
                assert int(binding.get("sponsored_gas") or 0) == 1, "the facilitator settles and pays gas; the card must say so"
                assert int(binding.get("max_network_fee_minor") or 0) == 0
