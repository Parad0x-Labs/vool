"""USDC on the Solana Mainnet row exists for the UsePod x402 payment lane, and only for it.

The registry declares the mint once; a proposal on that row in that token is accepted when the UsePod lane proposes it
and refused, typed and before any write, for every other origin -- the pilot's own Mainnet transfers stay native. No
chain is reached: proposal intake validates shape, registry and origin locally.
"""
from __future__ import annotations

import uuid

import pytest

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PIN = "482913"


@pytest.fixture
def mainnet_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    environment.set_active_environment("mainnet")
    yield
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _pilot_wallet() -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"m-{uuid.uuid4().hex}", label="x402 payer")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _recipient() -> str:
    from solders.keypair import Keypair

    return str(Keypair().pubkey())


def test_the_mainnet_row_declares_usdc_by_symbol_and_exact_mint_beside_native_sol(mainnet_home) -> None:
    from core.wallet import chains, svm_tokens

    by_symbol, by_mint = chains.asset_for(SOLANA_MAINNET, "USDC"), chains.asset_for(SOLANA_MAINNET, USDC_MINT)
    assert by_symbol == by_mint and (by_symbol.address, by_symbol.decimals, by_symbol.native) == (USDC_MINT, 6, False)
    assert chains.native_asset(SOLANA_MAINNET).symbol == "SOL"
    assert svm_tokens.is_token_transfer(SOLANA_MAINNET, "USDC") and not svm_tokens.is_token_transfer(SOLANA_MAINNET, "SOL")


def test_a_mainnet_usdc_proposal_is_accepted_only_from_the_usepod_lane(mainnet_home) -> None:
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    wallet = _pilot_wallet()
    usepod = proposals.propose_transaction(
        wallet_id=wallet["wallet_id"], destination=_recipient(), amount_minor=150_000, asset="USDC", origin=proposals.ORIGIN_USEPOD,
        memo="x402 payment", idempotency_key=f"usepod:{uuid.uuid4().hex}", network=SOLANA_MAINNET,
    )
    assert (usepod.asset, usepod.network, usepod.origin, int(usepod.amount_minor)) == ("USDC", SOLANA_MAINNET, "usepod", 150_000)
    for origin in (proposals.ORIGIN_USER, proposals.ORIGIN_MODEL, proposals.ORIGIN_X402):
        with pytest.raises(WalletFault) as caught:
            proposals.propose_transaction(
                wallet_id=wallet["wallet_id"], destination=_recipient(), amount_minor=150_000, asset="USDC", origin=origin,
                idempotency_key=f"{origin}:{uuid.uuid4().hex}", network=SOLANA_MAINNET,
            )
        assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_network_disabled", "mainnet_tokens_move_only_for_usepod_x402"), origin
    native = proposals.propose_transaction(
        wallet_id=wallet["wallet_id"], destination=_recipient(), amount_minor=1_000_000, asset="SOL", origin=proposals.ORIGIN_USER,
        idempotency_key=f"user:{uuid.uuid4().hex}", network=SOLANA_MAINNET,
    )
    assert native.asset == "SOL", "the pilot's own native transfers are unchanged"
