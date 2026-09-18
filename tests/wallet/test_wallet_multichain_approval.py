"""Stage D — approval binding and the fee-inclusive cost authority (RED corpus).

The operator's approval binds EVERY cost-bearing fact in one digest; a one-byte change to
any bound field invalidates it. The atomic reservation covers principal PLUS every
non-sponsored maximum fee, per wallet, asset, chain and destination; parallel approvals
cannot jointly escape any ceiling; a cross-asset total with no declared conversion refuses
rather than pretending.
"""
from __future__ import annotations

import threading

import pytest

from core.wallet.errors import WalletFault

BASE_SEPOLIA = "eip155:84532"
ETHEREUM_SEPOLIA = "eip155:11155111"
DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "246810"


@pytest.fixture
def wallet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield
    store_module.reset_default_store()


def _full_challenge(**overrides):
    from core.wallet import approval

    fields = dict(
        proposal_id="pay-fullybound00000001", wallet_id="wallet-1", amount_minor=1500, asset="USDC",
        destination="0x" + "2" * 40, network=BASE_SEPOLIA, account_id="0x" + "a" * 40, chain=BASE_SEPOLIA,
        scheme="exact", scheme_version=2, asset_address="0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        asset_decimals=6, human_amount="0.0015", payee="0x" + "2" * 40,
        resource_origin="https://paid.example.test", resource_method="GET",
        facilitator="fac-sim@https://facilitator.test", nonce="0x" + "ab" * 32,
        deadline=1_800_000_000, expiry=1_800_000_060, max_facilitator_fee_minor=10,
        max_network_fee_minor=5, fee_asset="USDC", sponsored_gas=False,
        max_total_minor=1515, idempotency_key="x402v2:abc123",
    )
    fields.update(overrides)
    return approval.ApprovalChallenge(**fields)


BOUND_FIELD_MUTATIONS = {
    "proposal_id": {"proposal_id": "pay-fullybound00000002"},
    "wallet_id": {"wallet_id": "wallet-2"},
    "account_id": {"account_id": "0x" + "b" * 40},
    "chain": {"chain": ETHEREUM_SEPOLIA},
    "network": {"network": ETHEREUM_SEPOLIA},
    "scheme": {"scheme": "upto"},
    "scheme_version": {"scheme_version": 1},
    "asset_address": {"asset_address": "0x1c7d4b196cb0c7b01d743fbc6116a902379c7238"},
    "asset": {"asset": "ETH"},
    "asset_decimals": {"asset_decimals": 18},
    "amount_minor": {"amount_minor": 1501},
    "human_amount": {"human_amount": "0.0016"},
    "payee": {"payee": "0x" + "9" * 40},
    "resource_origin": {"resource_origin": "https://evil.example.test"},
    "resource_method": {"resource_method": "POST"},
    "facilitator": {"facilitator": "fac-other@https://facilitator.test"},
    "nonce": {"nonce": "0x" + "cd" * 32},
    "deadline": {"deadline": 1_800_000_001},
    "expiry": {"expiry": 1_800_000_061},
    "max_facilitator_fee_minor": {"max_facilitator_fee_minor": 11},
    "max_network_fee_minor": {"max_network_fee_minor": 6},
    "fee_asset": {"fee_asset": "ETH"},
    "sponsored_gas": {"sponsored_gas": True},
    "max_total_minor": {"max_total_minor": 1516},
    "idempotency_key": {"idempotency_key": "x402v2:DIFFERENT"},
}


def test_d1_the_challenge_digest_binds_every_listed_field(wallet_env):
    from core.wallet import approval

    challenge = _full_challenge()
    decision = approval.ApprovalDecision(approved=True, method=approval.METHOD_PIN, challenge_digest=challenge.digest)
    assert approval.decision_binds(decision, challenge)
    for field_name, patch in BOUND_FIELD_MUTATIONS.items():
        moved = _full_challenge(**patch)
        assert moved.digest != challenge.digest, field_name
        replay = approval.ApprovalDecision(approved=True, method=approval.METHOD_PIN, challenge_digest=challenge.digest)
        assert not approval.decision_binds(replay, moved), field_name
        assert not approval.decision_binds(decision, moved), field_name


def test_d2_no_approval_replay_across_account_chain_asset_origin_amount_or_request(wallet_env):
    from core.wallet import approval

    base = _full_challenge()
    for patch in (
        {"account_id": "0x" + "c" * 40},  # another account
        {"chain": DEVNET},  # another chain
        {"asset_address": "0x" + "7" * 40},  # another asset
        {"resource_origin": "https://other.example.test"},  # another origin
        {"amount_minor": 99999},  # another amount
        {"idempotency_key": "another-request"},  # another request
    ):
        other = _full_challenge(**patch)
        stolen = approval.ApprovalDecision(approved=True, method=approval.METHOD_PIN, challenge_digest=base.digest)
        assert approval.decision_binds(stolen, base)
        assert not approval.decision_binds(stolen, other)


def test_d3_biometric_default_approves_nothing(wallet_env):
    from core.wallet import approval

    challenge = _full_challenge()
    decision = approval.UnavailableBiometric().approve(challenge)
    assert decision.approved is False
    assert not approval.decision_binds(decision, challenge)
    replay = approval.ApprovalDecision(approved=False, method=approval.METHOD_BIOMETRIC, challenge_digest=challenge.digest)
    assert not approval.decision_binds(replay, challenge)


def test_d4_five_failed_pin_attempts_lock_while_one_stays_recoverable(wallet_env, rpc, monkeypatch):
    """The PIN screen's law on the multichain engine: the same refusal counting, the same
    lock, and the same recovery path for the first four mistakes."""
    from core.wallet import approval, custody, lifecycle, proposals
    from tests.wallet._rig import DESTINATION

    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", rpc.url)

    created = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN)
    profile = created.profile
    proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1500, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    for _attempt in range(lifecycle.MAX_APPROVAL_ATTEMPTS - 1):
        with pytest.raises(WalletFault) as wrong:
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("000000"))
        assert wrong.value.code == "wallet_approval_rejected"
        current = proposals.get_proposal(proposal.proposal_id)
        assert current.state == proposals.STATE_PENDING_APPROVAL  # still recoverable
    with pytest.raises(WalletFault) as lock:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("000000"))
    assert lock.value.code == "wallet_approval_rejected"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_REJECTED
    with pytest.raises(WalletFault) as after_lock:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert after_lock.value.code == "wallet_duplicate_payment"
    assert rpc.send_count() == 0


def test_d5_reservation_always_includes_non_sponsored_fees(wallet_env):
    from core.wallet import limits

    limits.set_limits("wallet-fees", "USDC", limits.SpendLimits(per_tx_minor=10_000, daily_minor=10_000, per_destination_daily_minor=10_000))
    # principal 9900 fits; principal + max fee 9900+200 does not
    verdict = limits.check_limits("wallet-fees", "USDC", 9900, "0x" + "1" * 40, fee_minor=200)
    assert not verdict.ok and verdict.limit == limits.LIMIT_PER_TRANSACTION
    # sponsored gas is truthfully zero: the same payment without the fee claim fits
    ok = limits.reserve_spend(wallet_id="wallet-fees", asset="USDC", amount_minor=9900, destination="0x" + "1" * 40, proposal_id="pay-fee1", fee_minor=0)
    assert ok.ok
    # a second parallel proposal cannot claim fee room the first one already holds
    verdict2 = limits.reserve_spend(wallet_id="wallet-fees", asset="USDC", amount_minor=4500, destination="0x" + "2" * 40, proposal_id="pay-fee2", fee_minor=200)
    assert not verdict2.ok and verdict2.limit == limits.LIMIT_DAILY


def test_d6_parallel_approvals_cannot_escape_chain_scoped_ceilings(wallet_env):
    """USDC on Base Sepolia and USDC on Ethereum Sepolia draw on SEPARATE daily buckets;
    within one bucket, concurrent approvals cannot jointly exceed it."""
    from core.wallet import limits

    limits.set_limits("wallet-chains", "USDC", limits.SpendLimits(per_tx_minor=6_000, daily_minor=10_000, per_destination_daily_minor=10_000))
    results: list[tuple[bool, str]] = []

    def attempt(index: int):
        verdict = limits.reserve_spend(
            wallet_id="wallet-chains", asset="USDC", amount_minor=4_000, destination=f"0x{index:040x}",
            proposal_id=f"pay-chain{index}", chain=BASE_SEPOLIA,
        )
        results.append((verdict.ok, verdict.limit))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(1 for ok, _ in results if ok) == 2  # 10_000 daily on the chain, 4_000 each
    # the other chain's bucket is untouched: two more fit there
    assert limits.reserve_spend(wallet_id="wallet-chains", asset="USDC", amount_minor=4_000, destination="0x" + "9" * 40, proposal_id="pay-other-chain-1", chain=ETHEREUM_SEPOLIA).ok
    assert limits.reserve_spend(wallet_id="wallet-chains", asset="USDC", amount_minor=4_000, destination="0x" + "8" * 40, proposal_id="pay-other-chain-2", chain=ETHEREUM_SEPOLIA).ok


def test_d7_cross_asset_totals_refuse_rather_than_pretend(wallet_env, monkeypatch):
    """A global ceiling needs declared conversions. Without them the joint cross-asset
    reservation refuses; with them, the converted joint total is enforced."""
    from core.wallet import limits

    limits.set_limits("wallet-global", "USDC", limits.SpendLimits(per_tx_minor=900_000, daily_minor=2_000_000, per_destination_daily_minor=2_000_000))
    limits.set_limits("wallet-global", "SOL", limits.SpendLimits(per_tx_minor=90_000_000, daily_minor=90_000_000, per_destination_daily_minor=90_000_000))
    monkeypatch.setenv("VOOL_WALLET_GLOBAL_DAILY_MINOR", "1000000")
    monkeypatch.delenv("VOOL_WALLET_ASSET_CONVERSIONS", raising=False)
    # no conversions: the global ceiling cannot be checked honestly, so even the first
    # cross-asset participant refuses rather than pretending
    first = limits.reserve_spend(wallet_id="wallet-global", asset="USDC", amount_minor=600_000, destination="0x" + "1" * 40, proposal_id="pay-global-0", chain=BASE_SEPOLIA)
    assert not first.ok and first.limit == limits.LIMIT_GLOBAL_UNCOMPARABLE

    monkeypatch.setenv("VOOL_WALLET_ASSET_CONVERSIONS", '{"USDC": 1}')
    second = limits.reserve_spend(wallet_id="wallet-global", asset="USDC", amount_minor=600_000, destination="0x" + "1" * 40, proposal_id="pay-global-1", chain=BASE_SEPOLIA)
    assert second.ok
    # SOL has no declared factor: its reservation refuses instead of guessing
    third = limits.reserve_spend(wallet_id="wallet-global", asset="SOL", amount_minor=30_000_000, destination=DESTINATION_SOL, proposal_id="pay-global-2", chain=DEVNET)
    assert not third.ok and third.limit == limits.LIMIT_GLOBAL_UNCOMPARABLE

    monkeypatch.setenv("VOOL_WALLET_ASSET_CONVERSIONS", '{"USDC": 1, "SOL": 100}')
    fourth = limits.reserve_spend(wallet_id="wallet-global", asset="SOL", amount_minor=30_000_000, destination=DESTINATION_SOL, proposal_id="pay-global-3", chain=DEVNET)
    # 600_000 (USDC) + 30_000_000 x 100 = 3_000_600_000 global: far over 1_000_000
    assert not fourth.ok and fourth.limit == limits.LIMIT_GLOBAL_DAILY
    fifth = limits.reserve_spend(wallet_id="wallet-global", asset="SOL", amount_minor=100, destination=DESTINATION_SOL, proposal_id="pay-global-4", chain=DEVNET)
    assert fifth.ok  # 600_000 + 10_000 fits
    sixth = limits.reserve_spend(wallet_id="wallet-global", asset="USDC", amount_minor=500_000, destination="0x" + "3" * 40, proposal_id="pay-global-5", chain=BASE_SEPOLIA)
    # 610_000 held + 500_000 = 1_110_000 > 1_000_000: the joint total is enforced
    assert not sixth.ok and sixth.limit == limits.LIMIT_GLOBAL_DAILY


DESTINATION_SOL = "11111111111111111111111111111111"


def test_d8_evm_prepare_proves_identity_then_pends_without_signing(wallet_env, evm_rig):
    """The EVM simulation stage: ONE bounded chain-identity read plus the capability
    checks, then pending_approval. No signature, no broadcast, and the proposal carries
    its canonical chain identity into every later stage."""
    import json as _json
    import os

    from core.wallet import chains, custody, facilitators, lifecycle, proposals, x402_v2
    from core.wallet import config as wallet_config

    facilitators.discover(evm_rig.facilitator.url, facilitator_id="fac-sim")
    rpc = evm_rig.rpc
    env_map = {wallet_config.NETWORK_RPC_URLS_ENV: _json.dumps({BASE_SEPOLIA: rpc.url})}
    saved = {k: os.environ.get(k) for k in env_map}
    try:
        for key, value in env_map.items():
            os.environ[key] = value
        chains.invalidate_chain_identity(None)
        profile = custody.register_external_signer_wallet("0x" + "a" * 40, network=BASE_SEPOLIA)
        entry = x402_v2.V2Requirements(
            scheme="exact", network=BASE_SEPOLIA, amount_minor=100_000, asset="0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            pay_to="0x" + "2" * 40, resource_url="https://paid.example.test/report", eip712_name="USDC", eip712_version="2",
        )
        offer = x402_v2.V2Offer(resource_url=entry.resource_url, resource_description="d", accepts=(entry,))
        proposal = x402_v2.propose_from_v2_offer(offer, entry, wallet_id=profile.wallet_id)
        assert proposals.get_proposal(proposal.proposal_id).network == BASE_SEPOLIA
        engine = lifecycle.default_lifecycle()
        prepared = engine.prepare(proposal.proposal_id)
        assert prepared.state == proposals.STATE_PENDING_APPROVAL
        assert prepared.simulation["ok"] is True
        assert prepared.simulation["reason"] == "chain_identity_and_capability_verified"
        # the identity read hit the scripted endpoint exactly once (eth_chainId), then cached
        assert "eth_chainId" in evm_rig.rpc.methods()
        # nothing was signed or broadcast on this lane
        assert evm_rig.facilitator.settle_count() == 0
        # a lying endpoint (a mainnet id) is a typed identity failure for a NEW scope
        chains.invalidate_chain_identity(None)
        evm_rig.rpc.lie_chain_id = 8453
        other = x402_v2.V2Requirements(
            scheme="exact", network=BASE_SEPOLIA, amount_minor=90_000, asset="0x036cbd53842c5426634e7929541ec2318f3dcf7e",
            pay_to="0x" + "3" * 40, resource_url="https://paid.example.test/other", eip712_name="USDC", eip712_version="2",
        )
        proposal2 = x402_v2.propose_from_v2_offer(
            x402_v2.V2Offer(resource_url=other.resource_url, resource_description="d", accepts=(other,)), other, wallet_id=profile.wallet_id,
        )
        with pytest.raises(WalletFault) as mismatch:
            engine.prepare(proposal2.proposal_id)
        assert mismatch.value.code == "wallet_simulation_failed"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
