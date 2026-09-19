"""Crypto Pilot stage 0: the fakes the pilot proofs stand on must behave like the chains they model.

A rig that accepts anything proves nothing. These tests pin the scripted Solana node's defaults (the
existing suite's answers stay byte-identical), the native EVM chain's enforcement of chain id, nonce,
balance and fee cap on decoded signed bytes, and the private-key leak detector's sensitivity, each with a
control that must NOT trip.
"""
from __future__ import annotations

import json
import secrets
import urllib.request

import pytest

from tests.wallet._rig import DEVNET_GENESIS, MAINNET_GENESIS, ScriptedRpc, key_leaked


def _rpc(url: str, method: str, params: list | None = None) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": method, "params": params or []}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_the_scripted_solana_node_keeps_its_historical_answers_and_adds_identity():
    with ScriptedRpc() as node:
        assert _rpc(node.url, "getBalance", ["11111111111111111111111111111111"])["result"]["value"] == 5_000_000_000
        statuses = _rpc(node.url, "getSignatureStatuses", [["sig"], {"searchTransactionHistory": True}])["result"]["value"]
        assert statuses[0]["confirmationStatus"] == "confirmed" and statuses[0]["err"] is None
        assert _rpc(node.url, "getGenesisHash")["result"] == DEVNET_GENESIS
        node.status_mode = "none"
        assert _rpc(node.url, "getSignatureStatuses", [["sig"]])["result"]["value"] == [None]
        node.status_mode = "err"
        assert _rpc(node.url, "getSignatureStatuses", [["sig"]])["result"]["value"][0]["err"]
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as mainnet:
        assert _rpc(mainnet.url, "getGenesisHash")["result"] == MAINNET_GENESIS


def _signed(chain_id: int, key: bytes, *, nonce: int = 0, value: int = 10**15, max_fee: int = 3_000_000_000, gas: int = 21_000) -> tuple[str, str]:
    from eth_account import Account

    account = Account.from_key(key)
    tx = {"type": 2, "chainId": chain_id, "nonce": nonce, "to": "0x" + "22" * 20, "value": value, "gas": gas,
          "maxFeePerGas": max_fee, "maxPriorityFeePerGas": 1_000_000, "data": b"", "accessList": []}
    return account.address, "0x" + bytes(account.sign_transaction(tx).raw_transaction).hex()


def test_the_native_evm_chain_enforces_what_a_node_enforces_on_decoded_bytes():
    pytest.importorskip("eth_account")
    from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

    key = secrets.token_bytes(32)
    with ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack", base_fee=5_000_000, l1_fee=4_000) as chain:
        sender, raw = _signed(8453, key)
        chain.fund(sender, 10**16)
        # control: a well-formed transfer is accepted and its hash is keccak(raw)
        from eth_utils import keccak

        accepted = _rpc(chain.url, "eth_sendRawTransaction", [raw])
        assert accepted["result"] == "0x" + keccak(bytes.fromhex(raw[2:])).hex()
        receipt = _rpc(chain.url, "eth_getTransactionReceipt", [accepted["result"]])["result"]
        assert receipt["status"] == "0x1" and int(receipt["l1Fee"], 16) == 4_000
        charged = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16) + 4_000
        assert chain.balance_of(sender) == 10**16 - 10**15 - charged
        # the same nonce again is refused; a wrong chain id is refused; an unfunded sender is refused
        _sender, replay = _signed(8453, key, nonce=0, value=2)
        assert "nonce too low" in _rpc(chain.url, "eth_sendRawTransaction", [replay])["error"]["message"]
        _sender, wrong_chain = _signed(1, key, nonce=1)
        assert "chain id" in _rpc(chain.url, "eth_sendRawTransaction", [wrong_chain])["error"]["message"]
        poor_sender, poor = _signed(8453, secrets.token_bytes(32))
        assert "insufficient funds" in _rpc(chain.url, "eth_sendRawTransaction", [poor])["error"]["message"]
        assert chain.send_count() == 1
        # the L1 oracle answers only with transaction bytes, and a dead oracle is an error, never zero
        from eth_utils import keccak as _k

        selector = "0x" + _k(text="getL1Fee(bytes)")[:4].hex()
        payload = selector + "20".rjust(64, "0") + "10".rjust(64, "0") + "ab" * 16 + "00" * 16
        assert int(_rpc(chain.url, "eth_call", [{"to": "0x420000000000000000000000000000000000000F", "data": payload}, "latest"])["result"], 16) == 4_000
        chain.oracle_available = False
        assert "error" in _rpc(chain.url, "eth_call", [{"to": "0x420000000000000000000000000000000000000F", "data": payload}, "latest"])


def test_the_arbitrum_model_ignores_tips_and_folds_l1_gas_into_gas_used():
    pytest.importorskip("eth_account")
    from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

    key = secrets.token_bytes(32)
    with ScriptedEvmNativeChain(chain_id=46630, fee_model="arbitrum", base_fee=10_000_000, estimate_gas=25_736, gas_used_for_l1=4_736) as chain:
        sender, raw = _signed(46630, key, gas=32_170, max_fee=20_000_000)
        chain.fund(sender, 10**16)
        tx_hash = _rpc(chain.url, "eth_sendRawTransaction", [raw])["result"]
        receipt = _rpc(chain.url, "eth_getTransactionReceipt", [tx_hash])["result"]
        assert int(receipt["effectiveGasPrice"], 16) == 10_000_000  # the base fee, whatever the tip
        assert int(receipt["gasUsedForL1"], 16) == 4_736 and int(receipt["gasUsed"], 16) == 25_736


def test_the_leak_detector_finds_every_spelling_and_ignores_unrelated_text():
    from core.vool_wallet import b58encode

    hex_key = "0x" + secrets.token_bytes(32).hex()
    assert key_leaked(hex_key, f"log line {hex_key}")
    assert key_leaked(hex_key, "value=" + hex_key[2:].upper())
    assert key_leaked(hex_key, "chunked " + hex_key[10:40])
    keypair = b58encode(secrets.token_bytes(64))
    assert key_leaked(keypair, f"backup: {keypair}")
    assert key_leaked(keypair, keypair[30:52])
    # controls: another key, a transaction hash and ordinary prose never trip
    other = "0x" + secrets.token_bytes(32).hex()
    assert key_leaked(hex_key, f"tx {other} confirmed on Base") is None
    assert key_leaked(keypair, "Send 0.1 SOL to 11111111111111111111111111111111 on Solana Mainnet") is None
