"""Arbitrum aliases select their own row; a lying RPC never opens a quote.

Component tests with the existing simulated chain and real wallet custody/resolution.
"""
from __future__ import annotations

import json

import pytest

from core.wallet.errors import WalletFault
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_request import _env, _pilot
from tests.wallet.test_crypto_pilot_request import home as request_home

home = request_home


@pytest.mark.parametrize(("environment", "network", "other"), [
    ("mainnet", "eip155:42161", "eip155:8453"),
    ("testnet", "eip155:421614", "eip155:84532"),
])
@pytest.mark.parametrize("alias", ["arbitrum", "ARB", "Arbitrum One"])
def test_arbitrum_alias_selects_its_row_even_when_the_default_eth_account_is_elsewhere(home, environment, network, other, alias):
    from core.wallet import capabilities, chains, transfers

    _env(environment)
    selected = _pilot(network, "Arbitrum")
    _pilot(other, "Default ETH elsewhere")
    resolved = transfers.resolve_request(destination="0x" + "a" * 40, asset="ETH", amount="0.01", chain=alias)
    assert (resolved["wallet_id"], resolved["network"], resolved["asset"], resolved["amount_minor"]) == (
        selected["wallet_id"], network, "ETH", 10**16)
    spec = chains.resolve_network(network)
    assert capabilities.is_pilot_row(spec) and capabilities.pilot_transfer_ready(spec)
    assert spec.schemes == () and set(spec.assets) == {"ETH"}


@pytest.mark.parametrize(("environment", "network", "chain_id"), [
    ("mainnet", "eip155:42161", 42161),
    ("testnet", "eip155:421614", 421614),
])
def test_arbitrum_rpc_with_another_chain_id_refuses_before_balance_quote_or_send(home, environment, network, chain_id):
    from core.wallet import proposals, quotes

    _env(environment)
    wallet = _pilot(network, "Arbitrum")
    with ScriptedEvmNativeChain(chain_id=chain_id, fee_model="arbitrum", lie_chain_id=1) as node:
        home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({network: node.url}))
        proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], network=network,
            destination="0x" + "b" * 40, amount_minor=10**15, asset="ETH", origin=proposals.ORIGIN_USER)
        with pytest.raises(WalletFault) as refused:
            quotes.mint_quote(proposal.proposal_id)
        assert refused.value.code == "wallet_chain_identity_mismatch"
        methods = [call["method"] for call in node.calls]
        assert methods == ["eth_chainId"] and node.sent == []
        assert quotes.open_quote_for(proposal.proposal_id) is None
