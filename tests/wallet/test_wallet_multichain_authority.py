"""Stage A — authority and defaults for the multichain wallet (RED corpus).

The multichain lane may add networks, but it may not dilute a single one of the base
invariants: `core.wallet` stays the only money authority, the wallet stays disabled by
default, the allowed networks are exactly the four declared testnets, and every
mainnet/unknown/arbitrary-chain path refuses BEFORE a signer is touched or a socket is
opened.
"""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet.errors import WalletFault
from tests.wallet._rig import DESTINATION, fault_codes

#: The declared rows of this build, in canonical (CAIP-2) form and registry order: five Test networks rows
#: then five Mainnet rows (Crypto Pilot). Nothing else — no alias row, no arbitrary chain — may ever appear.
DECLARED_NETWORKS = (
    "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",  # Solana devnet (genesis prefix, verified 2026-09-03)
    "eip155:84532",  # Base Sepolia
    "eip155:11155111",  # Ethereum Sepolia
    "eip155:97",  # BNB Smart Chain testnet
    "eip155:46630",  # Robinhood Chain Testnet
    "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",  # Solana mainnet
    "eip155:4663",  # Robinhood Chain
    "eip155:8453",  # Base
    "eip155:1",  # Ethereum
    "eip155:56",  # BNB Smart Chain
)

#: Declared Mainnet rows: under Test networks, every effect on them refuses on the environment.
OTHER_ENVIRONMENT_ROWS = ("eip155:1", "eip155:8453", "eip155:56", "eip155:4663", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp")

UNDECLARED_ATTEMPTS = (
    "solana:5eykt4UsFv8P8NJdTREpYojv6fgkHiE7W",  # a wrong Solana mainnet genesis prefix
    "solana-mainnet",
    "mainnet-beta",
    "eip155:137",  # Polygon mainnet
    "base-mainnet",
    "ethereum",
    "base",
    "bnb",
    "arbitrum",
    "eip155:999999",  # unregistered arbitrary chain
    "solana:DevNeT",  # a confusable alias that is not a genesis prefix
)


def _sol_destination() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


EVM_DESTINATION = "0x" + "1" * 40


@pytest.fixture
def wallet_off(monkeypatch, tmp_path):
    """The wallet OFF switch: VOOL_WALLET_ENABLED absent, blackbox isolated."""
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield tmp_path
    store_module.reset_default_store()


@pytest.fixture
def wallet_env_unroutable(monkeypatch):
    """Wallet ON, but every chain endpoint is unroutable: a refusal must come from POLICY,
    never from a network error that would prove a socket was opened."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "http://127.0.0.1:1")


def test_a1_allowed_networks_are_exactly_the_declared_rows_of_both_environments():
    """The whole reachable universe of this build is the ten declared rows, five per environment."""
    from core.wallet import chains, config

    assert tuple(config.ALLOWED_NETWORKS) == DECLARED_NETWORKS
    assert [chains.resolve_network(n).environment for n in DECLARED_NETWORKS] == ["testnet"] * 5 + ["mainnet"] * 5


def test_a2_disabled_wallet_creates_no_artifacts_and_no_network(monkeypatch, wallet_off):
    """Disabled status/propose/x402: no profile, no proposal, no key material, no socket."""
    from core.wallet import custody, proposals, status

    st = status.wallet_status()
    assert st["enabled"] is False
    assert custody.list_wallets() == []
    with pytest.raises(WalletFault) as disabled_status:
        custody.require_enabled()
    assert disabled_status.value.code == "wallet_disabled"
    with pytest.raises(WalletFault) as evm_propose:
        proposals.propose_transaction(
            wallet_id="wallet-none", destination=EVM_DESTINATION, amount_minor=1,
            asset="USDC", origin="model", network="eip155:84532",
        )
    assert evm_propose.value.code == "wallet_disabled"
    with pytest.raises(WalletFault) as svm_propose:
        proposals.propose_transaction(
            wallet_id="wallet-none", destination=DESTINATION, amount_minor=1,
            asset="SOL", origin="model", network=DECLARED_NETWORKS[0],
        )
    assert svm_propose.value.code == "wallet_disabled"
    assert custody.list_wallets() == []
    assert proposals.list_proposals() == []
    assert "wallet_disabled" in fault_codes(limit=20)


def test_a3_every_undeclared_or_inactive_chain_refuses_before_signer_and_network(wallet_off, wallet_env_unroutable):
    """Policy refusals only: an unroutable RPC proves nothing reached a socket, because the
    refusal happens before the RPC client could even be constructed. Undeclared chains refuse as
    undeclared; declared Mainnet rows refuse on the active environment (Test networks here)."""
    from core.wallet import custody

    for network in UNDECLARED_ATTEMPTS:
        with pytest.raises(WalletFault) as refused:
            custody.require_network(network)
        assert refused.value.code == "wallet_network_disabled", network
    for network in OTHER_ENVIRONMENT_ROWS:
        with pytest.raises(WalletFault) as inactive:
            custody.create_watch_only_wallet(EVM_DESTINATION if network.startswith("eip155:") else _sol_destination(), network=network)
        assert inactive.value.code == "wallet_environment_inactive", network
    # the same refusals hold at the proposal door, where a signer would be the next step
    profile = custody.create_watch_only_wallet(EVM_DESTINATION, network="eip155:84532")
    for network, destination, asset, expected in (
        ("eip155:1", EVM_DESTINATION, "USDC", "wallet_environment_inactive"),
        ("solana-mainnet", _sol_destination(), "SOL", "wallet_network_disabled"),
        ("eip155:56", EVM_DESTINATION, "USDC", "wallet_environment_inactive"),
        ("notachain:1234", EVM_DESTINATION, "USDC", "wallet_network_disabled"),
    ):
        with pytest.raises(WalletFault) as refused_proposal:
            proposals_propose(network=network, destination=destination, asset=asset, wallet_id=profile.wallet_id)
        assert refused_proposal.value.code == expected, network


def proposals_propose(*, network: str, destination: str, asset: str, wallet_id: str):
    from core.wallet import proposals

    return proposals.propose_transaction(
        wallet_id=wallet_id, destination=destination, amount_minor=1,
        asset=asset, origin="user", network=network,
    )


def test_a4_legacy_money_surfaces_stay_typed_refusals_under_multichain(wallet_off):
    """The executable census still reports zero surviving retired surfaces after the
    multichain work begins. It touches no network."""
    from core.wallet.authority import executable_census

    assert executable_census(home=wallet_off / "census-home") == []


def test_a5_disabled_wallet_never_prompts_or_creates_payment_keys_on_status_paths(monkeypatch, wallet_off):
    """The disabled contract extends to every read path: status, proposals, receipts, x402."""
    from core.wallet import custody, receipts, status
    from core.wallet import x402 as wallet_x402

    assert status.wallet_status()["enabled"] is False
    with pytest.raises(WalletFault) as x402_refused:
        wallet_x402.propose_from_x402(
            wallet_x402.X402Request(amount_minor=1, asset="USDC", network="eip155:84532",
                                    pay_to=EVM_DESTINATION, resource="https://example.test/r"),
            wallet_id="wallet-none",
        )
    assert x402_refused.value.code == "wallet_disabled"
    assert custody.list_wallets() == []
    assert receipts.list_receipts() == []
