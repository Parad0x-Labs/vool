"""Chain honesty pins: what this build REFUSES is as much the product as what it pays.

BNB resolution (checkpoint B, 2026-09-06): "B402" is Binance's implementation of the x402
protocol on BNB Smart Chain MAINNET (chain 56) — a Binance-Pay facilitator + proxy
contracts documented only on Binance properties; there is no bnb-chain spec repo, and no
verified facilitator or pegged USD asset exists for BNB TESTNET (chain 97). This build
therefore declares BNB testnet with its native asset only: a USD x402 offer on eip155:97
is an honest typed refusal (asset_not_registered), and B402/mainnet-56 offers are refused
as mainnet (already pinned in test_wallet_multichain_authority). A generic EVM transfer
will never be labelled "B402 support".
"""
from __future__ import annotations

import pytest

from tests.wallet._rig import DEVNET
from tests.wallet._rig_evm import FacilitatorSimulator, ScriptedEvmRpc

pytestmark = [pytest.mark.safety]

BNB_TESTNET = "eip155:97"
BNB_MAINNET = "eip155:56"
SOLANA_USDC_MINT = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
# on-chain-verified 2026-09-07 via bsc-dataseed (18 decimals, no permit/EIP-3009)
BNB_USDT_MAINNET = "0x55d398326f99059ff775485246999027b3197955"
BNB_USDC_MAINNET = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"


def test_a_bnb_testnet_usd_x402_offer_refuses_asset_not_registered(wallet_env, monkeypatch):
    """No pegged USD asset is declared on eip155:97 and no facilitator serves it: the offer
    is a typed refusal BEFORE a proposal exists, never a guessed token."""
    import json

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import custody
    from core.wallet.errors import WalletFault

    profile = custody.register_external_signer_wallet("0x" + "d" * 40, network=BNB_TESTNET)
    from core.wallet import x402

    offer = x402.X402Request(
        amount_minor=50_000, asset=BNB_USDT_MAINNET, network=BNB_TESTNET,
        pay_to="0x" + "e" * 40, resource="https://example.test/bnb", scheme="exact",
    )
    with pytest.raises(WalletFault) as exc:
        x402.propose_from_x402(offer, wallet_id=profile.wallet_id)
    assert exc.value.code == "wallet_network_disabled"
    assert exc.value.context["reason"] in {"asset_not_registered", "asset_not_registered_on_chain"}


def test_a_bnb_mainnet_b402_shaped_offer_refuses_as_mainnet(wallet_env, monkeypatch):
    """The real B402 rail lives on chain 56 (verified live stock-agent.bnbchain.org offer
    shape): this build refuses it as mainnet, whatever it calls itself."""
    import base64
    import json

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import custody, x402, x402_v2
    from core.wallet.errors import WalletFault

    profile = custody.register_external_signer_wallet("0x" + "d" * 40, network=BNB_TESTNET)
    accept = {"scheme": "exact", "network": BNB_MAINNET, "amount": "100000000000000000", "asset": "0xcE24439F2D9C6a2289F741120FE202248B666666", "payTo": "0x15958aad30b758dabfbb9788da69dfcd56e89078", "maxTimeoutSeconds": 600, "extra": {"name": "United Stables", "version": "1", "assetTransferMethod": "eip3009", "spenderAddress": "0x3038f7ac3b4d1a3fe886bdcb5cd01e9f6bdd8633", "signerAddress": "0x34F7a661160780Ce1346e6D7B96D2bE244590899"}}
    body = json.dumps({"x402Version": 2, "error": "PAYMENT-SIGNATURE required", "accepts": [accept]}).encode()
    headers = {"payment-required": base64.b64encode(body).decode()}
    offer = x402_v2.parse_payment_required(headers, body, status=402)
    entry, reason = x402_v2.select_offer(offer, wallet_id=profile.wallet_id)
    assert entry is None and reason == "refused:mainnet_entry_only"


def test_solana_usdc_spl_is_an_honest_refusal_not_a_silent_transfer(wallet_env, monkeypatch):
    """The supported Solana path is the v1 exact lane for SOL. USDC (SPL) has no builder in
    this product: paying an x402 offer for it must refuse typed, never fall back to a SOL
    transfer or a guessed instruction."""
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import custody, proposals
    from core.wallet.errors import WalletFault

    profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="135790").profile
    from core.wallet import lifecycle

    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination="9wFFyRfZBsuAha4YcuxcXLK3xDcC5XuJmVSqCKo7TfxL",
        amount_minor=1_000_000, asset=SOLANA_USDC_MINT, origin="user", network=DEVNET,
    )
    engine = lifecycle.default_lifecycle()
    with pytest.raises(WalletFault) as exc:
        engine.prepare(proposal.proposal_id)
    assert exc.value.code == "wallet_simulation_failed"
    assert exc.value.context["reason"] == "asset_not_supported_in_p1"
    assert wallet_env and wallet_env["rpc"].send_count() == 0, "nothing was built or sent"
