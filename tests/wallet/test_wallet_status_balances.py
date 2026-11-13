"""Asking VOOL for the wallet's balances answers for every chain the owner holds an account on, not one.

What executes: the production model tool (core.runtime_execution_tools wallet.status), the wallet's own balance authority
(core.wallet.balances: identity proof, then the row's balance call), real Crypto Pilot custody, and scripted chain nodes
(tests.wallet._rig / _rig_evm_native) that answer like Solana and EVM nodes. No model and no public network.
"""
from __future__ import annotations

import json

import pytest

from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
from tests.wallet.test_crypto_pilot_request import _env, _pilot
from tests.wallet.test_crypto_pilot_request import home as request_home

pytestmark = [pytest.mark.safety]

home = request_home

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
ETH_SEPOLIA = "eip155:11155111"
ARB_SEPOLIA = "eip155:421614"
BASE_MAINNET = "eip155:8453"


def _status():
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("wallet.status", {}, source_context={"session_id": "balances-proof"})


def test_balances_cover_every_chain_and_say_which_hold_nothing_and_which_could_not_be_read(home):
    _env("mainnet")
    _pilot(BASE_MAINNET, "Base")  # another environment: not part of a Test networks answer
    _env("testnet")
    sol = _pilot(SOLANA_DEVNET, "Solana Devnet")
    base = _pilot(BASE_SEPOLIA, "Base Sepolia")
    _pilot(ETH_SEPOLIA, "Ethereum Sepolia")
    _pilot(ARB_SEPOLIA, "Arbitrum Sepolia")
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as solana, \
            ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack") as base_node, \
            ScriptedEvmNativeChain(chain_id=11155111) as eth_node, \
            ScriptedEvmNativeChain(chain_id=421614, fee_model="arbitrum", lie_chain_id=1) as arb_node:
        base_node.fund(base["address"], 25 * 10**16)
        home.setenv("VOOL_WALLET_TESTNET_RPC_URL", solana.url)
        home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: base_node.url, ETH_SEPOLIA: eth_node.url, ARB_SEPOLIA: arb_node.url}))
        result = _status()

    assert result.ok and result.status == "ok", result.response_text
    text = result.response_text
    assert "Balances on Test networks (each chain's own coin):" in text
    assert "- Solana Devnet: 5 SOL" in text, text  # the scripted Solana node holds 5 SOL for every account
    assert "- Base Sepolia: 0.25 ETH" in text, text
    assert "Nothing held on: Ethereum Sepolia." in text, text
    assert "Could not be read just now (unknown, not zero): Arbitrum Sepolia." in text, "a node on the wrong chain is never read as a number"
    assert "Base (" not in text and "Base:" not in text.replace("Base Sepolia:", ""), "a Mainnet account is not part of a Test networks answer"
    rows = {row["network"]: row for row in result.details["balances"]}
    assert set(rows) == {SOLANA_DEVNET, BASE_SEPOLIA, ETH_SEPOLIA, ARB_SEPOLIA}
    assert rows[SOLANA_DEVNET]["wallet_id"] == sol["wallet_id"] and rows[SOLANA_DEVNET]["balance_minor"] == "5000000000"
    assert rows[BASE_SEPOLIA]["balance_minor"] == str(25 * 10**16) and rows[ETH_SEPOLIA]["balance_minor"] == "0"
    assert rows[ARB_SEPOLIA]["state"] == "unavailable" and rows[ARB_SEPOLIA]["balance_minor"] is None
    observation = result.details["observation"]
    assert observation["balances_read"] == 3 and observation["balances_unavailable"] == 1


def test_no_account_in_the_active_environment_says_so_and_reads_nothing(home):
    _env("testnet")
    _pilot(SOLANA_DEVNET, "Solana Devnet")
    _env("mainnet")
    result = _status()
    assert result.ok
    assert "Balances: no account on Mainnet yet." in result.response_text
    assert result.details["balances"] == []


@pytest.mark.parametrize(("network", "label", "caps"), [
    (BASE_SEPOLIA, "Base Sepolia", "Its caps: 0.01 ETH a payment, 0.05 ETH a day."),
    ("eip155:97", "BNB Smart Chain Testnet", "a payment, 0.1 tBNB a day."),
])
def test_an_evm_default_wallet_states_its_own_coins_caps_not_the_solana_row(home, network, label, caps):
    """The caps line reads the default wallet's own coin row: an EVM wallet's caps are never the SOL row in lamports
    printed with 18 decimals (that read 0.0000000001 ETH for a 0.01 ETH cap)."""
    from core.runtime_execution_tools import _wallet_status_head
    from core.wallet import status as wallet_status_module

    _env("testnet")
    wallet = _pilot(network, label)
    status = wallet_status_module.wallet_status()
    if status.get("wallet_id") != wallet["wallet_id"]:  # the head is about whichever wallet is the default
        status = {**status, "wallet_id": wallet["wallet_id"], "network": network, "public_key": wallet["address"], "custody_mode": "pocket_sealed"}
    head = _wallet_status_head(status)
    assert caps in head, head
    assert "0.0000000" not in head, head
