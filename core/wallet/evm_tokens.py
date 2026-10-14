"""ERC-20 token facts and transfers on EVM rows: the one place the quote, the signer and the settlement read.

The owner's own token transfer (USDT on Ethereum, BNB Smart Chain and Arbitrum One, ``chains.USER_TRANSFER_TOKENS``) is ONE call of the
registry contract's ``transfer(recipient, amount)`` with no value attached. Every fact comes from the chain, checked
against the registry rather than assumed: the contract has code and its ``decimals()`` equals the registry's; the
payer's ``balanceOf`` covers the amount. The calldata is built here from the recipient and the amount, never taken
from a caller, and the signer rebuilds and compares it before it signs. A transfer counts as paid only when its
receipt carries the contract's own ``Transfer`` event for exactly that amount from the payer to the recipient: a
successful receipt without it stays unproven. No private key is handled here.
"""
from __future__ import annotations

from typing import Any

from core.wallet import chains
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.evm_tokens"
#: keccak("transfer(address,uint256)")[:4], keccak("balanceOf(address)")[:4], keccak("decimals()")[:4]
TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
BALANCE_OF_SELECTOR = bytes.fromhex("70a08231")
DECIMALS_SELECTOR = bytes.fromhex("313ce567")
#: keccak("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_UINT256_MAX = 2**256 - 1


def is_token_transfer(network: str, asset: str) -> bool:
    """True for a token the owner may send on an EVM row (``chains.USER_TRANSFER_TOKENS``), False for anything else."""
    try:
        spec = chains.resolve_network(network)
    except Exception:
        return False
    return spec.is_evm and chains.is_user_transfer_token(spec.network, asset)


def token_asset(network: str, asset: str, *, source_context: dict[str, Any] | None = None) -> chains.AssetSpec:
    """The registered ERC-20 the owner may send on an EVM row, by symbol or exact contract, or a typed refusal."""
    spec = chains.resolve_network(network)
    if not is_token_transfer(spec.network, asset):
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "asset": str(asset)[:16], "reason": "not_a_sendable_token"}, source_context=source_context)
    return chains.asset_for(spec.network, asset)


def _address_word(address: str) -> bytes:
    text = str(address or "").strip()
    if not chains.EVM_ADDRESS_RE.match(text):
        raise ValueError("not an EVM address")
    return bytes(12) + bytes.fromhex(text[2:])


def chain_amount(token: chains.AssetSpec, amount_minor: int) -> int:
    """The amount in the contract's own units: the wallet's figure times ``token.onchain_scale``."""
    return int(amount_minor) * token.onchain_scale


def transfer_calldata(recipient: str, amount: int) -> bytes:
    """``transfer(recipient, amount)`` for the token contract, ``amount`` in the contract's own units
    (:func:`chain_amount`): the selector and the two ABI words, nothing else."""
    amount = int(amount)
    if amount <= 0 or amount > _UINT256_MAX:
        raise ValueError("amount out of range")
    return TRANSFER_SELECTOR + _address_word(recipient) + amount.to_bytes(32, "big")


def balance_of_calldata(owner: str) -> str:
    return "0x" + (BALANCE_OF_SELECTOR + _address_word(owner)).hex()


def _word(answer: Any) -> int:
    text = str(answer or "")
    if not text.startswith("0x") or len(text) != 66:
        raise ValueError("not one ABI word")
    return int(text, 16)


def require_contract(call: Any, token: chains.AssetSpec, *, source_context: dict[str, Any] | None = None) -> None:
    """The registry's contract is live on this row and reports the registry's decimals, read through ``call`` (one
    JSON-RPC method and params -> result). A missing contract, an unreadable answer or other decimals refuse typed."""
    context = {"network": token.chain, "asset": token.symbol, "contract": token.address}
    code = str(call("eth_getCode", [token.address, "latest"]) or "0x").lower()
    if code in {"", "0x", "0x0"}:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "token_contract_has_no_code"}, source_context=source_context)
    try:
        decimals = _word(call("eth_call", [{"to": token.address, "data": "0x" + DECIMALS_SELECTOR.hex()}, "latest"]))
    except Exception:
        raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={**context, "reason": "token_decimals_unreadable"}, source_context=source_context) from None
    if decimals != token.onchain_decimals:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "token_decimals_differ_from_registry", "chain_decimals": decimals}, source_context=source_context)


def balance_minor(call: Any, token: chains.AssetSpec, owner: str, *, block: str = "latest", source_context: dict[str, Any] | None = None) -> int:
    """What ``owner`` holds of ``token`` at ``block`` (the contract's own ``balanceOf``) in the wallet's units, rounded
    down (a dust remainder finer than the wallet's unit is never counted), or a typed refusal when the answer cannot be
    read -- never a zero by default."""
    try:
        return _word(call("eth_call", [{"to": token.address, "data": balance_of_calldata(owner)}, block])) // token.onchain_scale
    except Exception:
        raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={"network": token.chain, "asset": token.symbol, "reason": "token_balance_unreadable"}, source_context=source_context) from None


def transfer_proven(receipt: Any, *, contract: str, payer: str, recipient: str, amount_minor: int) -> bool | None:
    """The principal proof from a receipt as the node served it (``amount_minor`` in the contract's own units, see
    :func:`chain_amount`): True when it succeeded and carries ``contract``'s own ``Transfer(payer, recipient, amount)`` event (exactly one such event, no other Transfer of this contract from the
    payer); False when it failed or shows another movement; None when the node served no logs to judge by."""
    if not isinstance(receipt, dict):
        return None
    if str(receipt.get("status") or "").lower() != "0x1":
        return False
    logs = receipt.get("logs")
    if not isinstance(logs, list):
        return None

    def topic_address(topic: Any) -> str:
        text = str(topic or "").lower()
        return "0x" + text[-40:] if text.startswith("0x") and len(text) == 66 else ""

    moved = []
    for log in logs:
        if not isinstance(log, dict) or str(log.get("address") or "").lower() != str(contract).lower():
            continue
        topics = log.get("topics") if isinstance(log.get("topics"), list) else []
        if len(topics) != 3 or str(topics[0]).lower() != TRANSFER_TOPIC or topic_address(topics[1]) != str(payer).lower():
            continue
        try:
            moved.append((topic_address(topics[2]), _word(log.get("data"))))
        except ValueError:
            return False
    return moved == [(str(recipient).lower(), int(amount_minor))]


__all__ = [
    "AUTHORITY", "BALANCE_OF_SELECTOR", "DECIMALS_SELECTOR", "TRANSFER_SELECTOR", "TRANSFER_TOPIC", "balance_minor", "balance_of_calldata", "chain_amount",
    "is_token_transfer", "require_contract", "token_asset", "transfer_calldata", "transfer_proven",
]
