"""SPL token facts and the TransferChecked message, against the SIMULATION Solana node (real keys, real signatures).

The node executes what it is sent and serves token balances, so these checks judge the wallet's own reads and the
bytes it builds -- not a scripted answer. The Devnet row's registered USDC mint is used so the registry is the
product's, unchanged; nothing here reaches a public cluster.
"""
from __future__ import annotations

import base64
import json
import urllib.request

import pytest

from tests.wallet._simulated_solana import SimulatedSolanaNode

DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
DEVNET_GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"


class _Rpc:
    """A bare JSON-RPC caller with the one method the token module uses (``_call``)."""

    def __init__(self, url: str) -> None:
        self.url = url

    def _call(self, method: str, params: list) -> object:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}), timeout=10) as response:
            payload = json.load(response)
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload["result"]


@pytest.fixture
def chain():
    from core.wallet import chains

    usdc = chains.asset_for(DEVNET, "USDC")
    with SimulatedSolanaNode(genesis_hash=DEVNET_GENESIS) as node:
        node.create_mint(usdc.address, decimals=usdc.decimals)
        yield node, _Rpc(node.url), usdc


def _keypair():
    from solders.keypair import Keypair

    return Keypair()


def test_the_registered_mint_is_validated_from_the_chain(chain) -> None:
    from core.wallet import svm_tokens
    from core.wallet.errors import WalletFault

    node, rpc, usdc = chain
    assert svm_tokens.is_token_transfer(DEVNET, "USDC") and not svm_tokens.is_token_transfer(DEVNET, "SOL")
    assert svm_tokens.require_mint(rpc, usdc) == 6
    node.mints[usdc.address]["decimals"] = 9  # the chain disagrees with the registry
    with pytest.raises(WalletFault) as caught:
        svm_tokens.require_mint(rpc, usdc)
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_quote_mismatch", "mint_decimals_differ_from_registry")


def test_token_accounts_are_read_for_their_owner_and_mint_or_refused(chain) -> None:
    from core.wallet import svm_tokens
    from core.wallet.errors import WalletFault

    node, rpc, usdc = chain
    payer, stranger = _keypair(), _keypair()
    assert svm_tokens.read_token_account(rpc, owner=str(payer.pubkey()), mint=usdc.address, refusal_code="wallet_recipient_refused") is None
    assert svm_tokens.token_balance_minor(rpc, owner=str(payer.pubkey()), asset=usdc) == 0
    node.fund_token(str(payer.pubkey()), usdc.address, 2_500_000)
    facts = svm_tokens.read_token_account(rpc, owner=str(payer.pubkey()), mint=usdc.address, refusal_code="wallet_recipient_refused")
    assert (facts.amount_minor, facts.frozen, facts.address) == (2_500_000, False, svm_tokens.associated_account(str(payer.pubkey()), usdc.address))
    # an account at the derived address that belongs to someone else is not this owner's token account
    address = svm_tokens.associated_account(str(stranger.pubkey()), usdc.address)
    node.token_accounts[address] = {"mint": usdc.address, "owner": str(payer.pubkey()), "amount": 1, "frozen": False}
    node.lamports[address] = 2_039_280
    with pytest.raises(WalletFault) as caught:
        svm_tokens.read_token_account(rpc, owner=str(stranger.pubkey()), mint=usdc.address, refusal_code="wallet_recipient_refused")
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_recipient_refused", "token_account_owner_mismatch")
    node.token_accounts[svm_tokens.associated_account(str(payer.pubkey()), usdc.address)]["frozen"] = True
    assert svm_tokens.token_balance_minor(rpc, owner=str(payer.pubkey()), asset=usdc) == 0, "a frozen account cannot pay"


def test_the_built_message_moves_exactly_the_amount_and_the_proof_reads_it_back(chain) -> None:
    from solders.transaction import Transaction

    from core.wallet import svm_tokens

    node, rpc, usdc = chain
    payer, recipient = _keypair(), _keypair()
    node.fund_sol(str(payer.pubkey()), 10_000_000)
    node.fund_token(str(payer.pubkey()), usdc.address, 1_000_000)
    node.open_token_account(str(recipient.pubkey()), usdc.address)
    blockhash = rpc._call("getLatestBlockhash", [{}])["value"]["blockhash"]
    message = svm_tokens.build_transfer_message(payer=str(payer.pubkey()), recipient_owner=str(recipient.pubkey()), asset=usdc, amount_minor=123_456, blockhash=blockhash)
    assert rpc._call("getFeeForMessage", [base64.b64encode(bytes(message)).decode(), {}])["value"] == 5000
    from solders.hash import Hash

    signed = Transaction([payer], message, Hash.from_string(blockhash))
    signature = rpc._call("sendTransaction", [base64.b64encode(bytes(signed)).decode(), {"encoding": "base64"}])
    assert node.token_balance(str(recipient.pubkey()), usdc.address) == 123_456
    answer = rpc._call("getTransaction", [signature, {}])
    kwargs = {"mint": usdc.address, "payer": str(payer.pubkey()), "recipient_owner": str(recipient.pubkey())}
    assert svm_tokens.principal_moved(answer, amount_minor=123_456, **kwargs) is True
    assert svm_tokens.principal_moved(answer, amount_minor=123_457, **kwargs) is False
    assert svm_tokens.principal_moved({"meta": {"err": None}}, amount_minor=123_456, **kwargs) is None
    assert svm_tokens.principal_moved({"meta": {"err": {"InstructionError": [0, "Custom"]}, "preTokenBalances": [], "postTokenBalances": []}}, amount_minor=123_456, **kwargs) is False
