"""SPL token facts and transfers on Solana rows: the one place the quote, the approval lane and the settlement read.

What a token payment needs (a UsePod x402 payment in USDC, the owner's own USDT transfer), and every fact comes from
the chain, checked against the registry rather than assumed: the mint is owned by the SPL Token program and carries the
registry's decimals; the payer's associated token account is a token account of that mint owned by the payer; the
recipient's associated token account exists, holds the same mint and belongs to the quoted recipient. A payment lane
never creates an account (a missing recipient account is a typed refusal). The owner's own transfer may create it --
the Associated Token Account program's ``CreateIdempotent``, the rent paid by the owner, quoted and approved -- but only
for a recipient that is a wallet key (on the ed25519 curve, not a token account or a mint). The transfer is ONE
``TransferChecked`` built from those validated facts, after that creation when there is one; the principal proof is
read back from a confirmed transaction's own token balances. No private key is handled here.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from core.wallet import chains
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.svm_tokens"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_ACCOUNT_SIZE = 165
MINT_SIZE = 82
TRANSFER_CHECKED = 12
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
#: Associated Token Account program instruction 1: create the account unless it already exists for this owner and mint
CREATE_IDEMPOTENT = 1


@dataclass(frozen=True)
class TokenAccountFacts:
    address: str
    owner: str
    mint: str
    amount_minor: int
    frozen: bool


def is_token_transfer(network: str, asset: str) -> bool:
    """True for a registered non-native asset of a Solana row (a mint), False for a native coin or anything unknown."""
    try:
        spec = chains.resolve_network(network)
        if not spec.is_svm:
            return False
        registered = chains.asset_for(spec.network, asset)
    except Exception:
        return False
    return not registered.native and bool(registered.address)


def is_lane_token(network: str, asset: str) -> bool:
    """A registered token a service payment lane may pay with (``chains.PAYMENT_LANE_TOKENS``)."""
    if not is_token_transfer(network, asset):
        return False
    return chains.asset_for(chains.resolve_network(network).network, asset).symbol in chains.PAYMENT_LANE_TOKENS


#: a service payment lane's token payment (UsePod x402, the DNA fee collection, the pay-kit lanes): never creates an account
ROUTE_LANE = "lane"
#: the owner's own token transfer (a chat or Crypto request): may create the recipient's account, quoted and approved
ROUTE_OWNER = "owner"


def token_route(proposal: Any) -> str:
    """How a proposal moves a Solana token: :data:`ROUTE_LANE`, :data:`ROUTE_OWNER`, or '' for a native coin, an
    unregistered asset, or an origin no token route serves (the proposal gate refuses those before anything is built)."""
    from core.wallet import proposals

    network, asset, origin = str(getattr(proposal, "network", "") or ""), str(getattr(proposal, "asset", "") or ""), str(getattr(proposal, "origin", "") or "")
    if not is_token_transfer(network, asset):
        return ""
    if origin in (proposals.ORIGIN_USEPOD, proposals.ORIGIN_DNA_FEE, *proposals.PAYKIT_ORIGINS):
        return ROUTE_LANE
    if origin in (proposals.ORIGIN_USER, proposals.ORIGIN_MODEL) and chains.is_user_transfer_token(network, asset):
        return ROUTE_OWNER
    return ""


def token_asset(network: str, asset: str, *, source_context: dict[str, Any] | None = None) -> chains.AssetSpec:
    """The registered SPL asset of a Solana row, by symbol or exact mint, or a typed refusal."""
    spec = chains.resolve_network(network)
    if not spec.is_svm:
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "reason": "token_transfer_needs_a_solana_row"}, source_context=source_context)
    registered = chains.asset_for(spec.network, asset)
    if registered.native or not registered.address:
        raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"network": spec.network, "asset": str(asset)[:16], "reason": "not_a_token_asset"}, source_context=source_context)
    return registered


def associated_account(owner: str, mint: str) -> str:
    """The associated token account of ``owner`` for ``mint`` under the SPL Token program (derived, never read)."""
    from solders.pubkey import Pubkey
    from solders.token.associated import get_associated_token_address

    return str(get_associated_token_address(Pubkey.from_string(str(owner)), Pubkey.from_string(str(mint))))


def _account_value(rpc: Any, address: str) -> dict[str, Any] | None:
    answer = rpc._call("getAccountInfo", [str(address), {"encoding": "base64", "commitment": "confirmed"}])
    value = (answer or {}).get("value") if isinstance(answer, dict) else None
    return value if isinstance(value, dict) else None


def _account_bytes(value: dict[str, Any], *, source_context: dict[str, Any] | None) -> bytes:
    data = value.get("data")
    if isinstance(data, list) and len(data) == 2 and data[1] == "base64":
        try:
            return base64.b64decode(str(data[0] or ""), validate=True)
        except Exception:
            pass
    raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={"reason": "account_data_unreadable"}, source_context=source_context)


def require_mint(rpc: Any, asset: chains.AssetSpec, *, source_context: dict[str, Any] | None = None) -> int:
    """The mint's decimals, read from the chain and required to equal the registry's. A missing mint, a mint owned by
    another program, an uninitialized mint or different decimals refuses before anything is built."""
    from solders.token.state import Mint

    value = _account_value(rpc, asset.address)
    context = {"network": asset.chain, "asset": asset.symbol, "mint": asset.address}
    if value is None:
        raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={**context, "reason": "mint_account_missing"}, source_context=source_context)
    if str(value.get("owner") or "") != TOKEN_PROGRAM:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "mint_not_owned_by_the_token_program"}, source_context=source_context)
    raw = _account_bytes(value, source_context=source_context)
    if len(raw) != MINT_SIZE:
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "mint_account_size_unexpected"}, source_context=source_context)
    mint = Mint.from_bytes(raw)
    if not bool(mint.is_initialized):
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "mint_not_initialized"}, source_context=source_context)
    if int(mint.decimals) != int(asset.decimals):
        raise wallet_fault("wallet_quote_mismatch", authority=AUTHORITY, context={**context, "reason": "mint_decimals_differ_from_registry", "chain_decimals": int(mint.decimals), "registry_decimals": int(asset.decimals)}, source_context=source_context)
    return int(mint.decimals)


def read_token_account(rpc: Any, *, owner: str, mint: str, refusal_code: str, source_context: dict[str, Any] | None = None) -> TokenAccountFacts | None:
    """The associated token account of ``owner`` for ``mint``: None when no account exists there; ``refusal_code`` when
    an account exists but is not an initialized token account of this mint and owner."""
    from solders.token.state import TokenAccount, TokenAccountState

    address = associated_account(owner, mint)
    value = _account_value(rpc, address)
    if value is None:
        return None
    context = {"address": address, "mint": mint}
    if str(value.get("owner") or "") != TOKEN_PROGRAM:
        raise wallet_fault(refusal_code, authority=AUTHORITY, context={**context, "reason": "token_account_owned_by_another_program"}, source_context=source_context)
    raw = _account_bytes(value, source_context=source_context)
    if len(raw) != TOKEN_ACCOUNT_SIZE:
        raise wallet_fault(refusal_code, authority=AUTHORITY, context={**context, "reason": "token_account_size_unexpected"}, source_context=source_context)
    account = TokenAccount.from_bytes(raw)
    if str(account.mint) != str(mint):
        raise wallet_fault(refusal_code, authority=AUTHORITY, context={**context, "reason": "token_account_mint_mismatch"}, source_context=source_context)
    if str(account.owner) != str(owner):
        raise wallet_fault(refusal_code, authority=AUTHORITY, context={**context, "reason": "token_account_owner_mismatch"}, source_context=source_context)
    if account.state == TokenAccountState.Uninitialized:
        raise wallet_fault(refusal_code, authority=AUTHORITY, context={**context, "reason": "token_account_uninitialized"}, source_context=source_context)
    return TokenAccountFacts(address=address, owner=str(owner), mint=str(mint), amount_minor=int(account.amount), frozen=account.state == TokenAccountState.Frozen)


def token_balance_minor(rpc: Any, *, owner: str, asset: chains.AssetSpec, source_context: dict[str, Any] | None = None) -> int:
    """What ``owner`` holds of ``asset``: its associated account's amount, 0 when that account does not exist."""
    facts = read_token_account(rpc, owner=owner, mint=asset.address, refusal_code="wallet_quote_unavailable", source_context=source_context)
    return 0 if facts is None or facts.frozen else facts.amount_minor


def require_wallet_key_owner(rpc: Any, owner: str, *, mint: str, source_context: dict[str, Any] | None = None) -> None:
    """A token account may be created only for a wallet key: an address on the ed25519 curve (a program-derived address
    has no key that could ever spend from it) that is not itself a token account or a mint (the token account address
    pasted instead of the wallet address). Refused typed, before anything is built."""
    from solders.pubkey import Pubkey

    context = {"mint": mint, "recipient": str(owner)}
    try:
        key = Pubkey.from_string(str(owner))
    except Exception:
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={**context, "reason": "recipient_not_a_solana_key"}, source_context=source_context) from None
    if not key.is_on_curve():
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={**context, "reason": "recipient_not_a_wallet_key"}, source_context=source_context)
    value = _account_value(rpc, str(owner))
    if value is not None and str(value.get("owner") or "") != SYSTEM_PROGRAM:
        reason = "recipient_is_a_token_account" if str(value.get("owner") or "") == TOKEN_PROGRAM else "recipient_owned_by_a_program"
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={**context, "reason": reason}, source_context=source_context)


def _owner_worded(fault: Any, token: chains.AssetSpec) -> Any:
    """The owner's own token send (USDT) gets its specific line; a payment lane keeps the catalog's."""
    if chains.is_user_transfer_token(token.chain, token.symbol):
        from core.wallet import token_text

        native = chains.native_asset(token.chain)
        token_text.worded(fault, symbol=token.symbol, gas=native.symbol, chain=chains.resolve_network(token.chain).display_name)
    return fault


def require_payable(
    rpc: Any, *, network: str, asset: str, payer: str, recipient_owner: str, amount_minor: int, held_token_minor: int = 0,
    fee_max_minor: int | None = None, held_native_minor: int = 0, allow_create: bool = False, source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every chain fact a token payment needs before it may be quoted or signed, refused typed: the mint (program and
    decimals), the recipient's token account (it must exist, hold the mint, belong to the recipient and not be frozen),
    the payer's spendable token balance, and -- when a fee ceiling is given -- a native balance that carries that fee and
    still leaves the account rent-exempt. ``held_*`` are what other in-flight transfers already hold of each asset.

    ``allow_create`` (the owner's own transfer only): a missing recipient account is created in the same transaction for
    a recipient that is a wallet key; the facts then say ``create_recipient_account`` and the native balance must also
    carry that account's rent (``recipient_account_rent_minor``)."""
    token = token_asset(network, asset, source_context=source_context)
    require_mint(rpc, token, source_context=source_context)
    context = {"network": token.chain, "asset": token.symbol}
    recipient = read_token_account(rpc, owner=recipient_owner, mint=token.address, refusal_code="wallet_recipient_refused", source_context=source_context)
    create = False
    account_rent = 0
    if recipient is None:
        if not allow_create:
            raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={**context, "reason": "recipient_token_account_missing", "recipient_token_account": associated_account(recipient_owner, token.address)}, source_context=source_context)
        require_wallet_key_owner(rpc, recipient_owner, mint=token.address, source_context=source_context)
        create = True
        account_rent = int(rpc._call("getMinimumBalanceForRentExemption", [TOKEN_ACCOUNT_SIZE]))
    elif recipient.frozen:
        raise wallet_fault("wallet_recipient_refused", authority=AUTHORITY, context={**context, "reason": "recipient_token_account_frozen"}, source_context=source_context)
    payer_account = read_token_account(rpc, owner=payer, mint=token.address, refusal_code="wallet_quote_unavailable", source_context=source_context)
    balance = 0 if payer_account is None or payer_account.frozen else payer_account.amount_minor
    spendable = balance - max(0, int(held_token_minor))
    if spendable < int(amount_minor):
        raise _owner_worded(wallet_fault("wallet_insufficient_funds", authority=AUTHORITY, context={**context, "reason": "token_balance_below_amount", "shortfall_minor": int(amount_minor) - spendable, "gas_asset": token.symbol}, source_context=source_context), token)
    facts: dict[str, Any] = {
        "principal_balance_minor": balance, "recipient_token_account": associated_account(recipient_owner, token.address),
        "payer_token_account": associated_account(payer, token.address), "mint": token.address, "decimals": token.decimals,
        "create_recipient_account": create, "recipient_account_rent_minor": account_rent,
    }
    if fee_max_minor is not None:
        answer = rpc._call("getBalance", [str(payer), {"commitment": "confirmed"}])
        native_balance = (answer or {}).get("value") if isinstance(answer, dict) else None
        if native_balance is None:
            raise wallet_fault("wallet_quote_unavailable", authority=AUTHORITY, context={**context, "reason": "balance_unknown"}, source_context=source_context)
        rent_minimum = int(rpc._call("getMinimumBalanceForRentExemption", [0]))
        if int(native_balance) - max(0, int(held_native_minor)) - int(fee_max_minor) - account_rent < rent_minimum:
            native = chains.native_asset(token.chain)
            raise _owner_worded(wallet_fault("wallet_insufficient_funds", authority=AUTHORITY, context={
                **context, "reason": "fee_balance_below_fee_and_rent", "fee_max_minor": int(fee_max_minor), "rent_minimum_minor": rent_minimum,
                "recipient_account_rent_minor": account_rent, "gas_asset": native.symbol,
            }, source_context=source_context), token)
        facts.update({"fee_balance_minor": int(native_balance), "rent_minimum_minor": rent_minimum})
    return facts


def transfer_checked_instruction(*, source: str, mint: str, destination: str, owner: str, amount_minor: int, decimals: int) -> Any:
    """SPL Token ``TransferChecked`` (instruction 12): source account, mint, destination account, owner (signer)."""
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    data = bytes([TRANSFER_CHECKED]) + int(amount_minor).to_bytes(8, "little") + bytes([int(decimals)])
    return Instruction(Pubkey.from_string(TOKEN_PROGRAM), data, [
        AccountMeta(Pubkey.from_string(str(source)), False, True),
        AccountMeta(Pubkey.from_string(str(mint)), False, False),
        AccountMeta(Pubkey.from_string(str(destination)), False, True),
        AccountMeta(Pubkey.from_string(str(owner)), True, False),
    ])


def create_recipient_account_instruction(*, payer: str, recipient_owner: str, mint: str) -> Any:
    """Associated Token Account ``CreateIdempotent``: the payer funds the recipient's associated account for ``mint``."""
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    return Instruction(Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM), bytes([CREATE_IDEMPOTENT]), [
        AccountMeta(Pubkey.from_string(str(payer)), True, True),
        AccountMeta(Pubkey.from_string(associated_account(recipient_owner, mint)), False, True),
        AccountMeta(Pubkey.from_string(str(recipient_owner)), False, False),
        AccountMeta(Pubkey.from_string(str(mint)), False, False),
        AccountMeta(Pubkey.from_string(SYSTEM_PROGRAM), False, False),
        AccountMeta(Pubkey.from_string(TOKEN_PROGRAM), False, False),
    ])


def build_transfer_message(*, payer: str, recipient_owner: str, asset: chains.AssetSpec, amount_minor: int, blockhash: str,
                           create_recipient_account: bool = False) -> Any:
    """The message that moves ``amount_minor`` of ``asset`` between the two associated accounts: one ``TransferChecked``,
    after a ``CreateIdempotent`` of the recipient's account when the quote said to create it; the payer signs as owner
    and pays the fee (and that account's rent)."""
    from solders.hash import Hash
    from solders.message import Message
    from solders.pubkey import Pubkey

    instructions = []
    if create_recipient_account:
        instructions.append(create_recipient_account_instruction(payer=payer, recipient_owner=recipient_owner, mint=asset.address))
    instructions.append(transfer_checked_instruction(
        source=associated_account(payer, asset.address), mint=asset.address, destination=associated_account(recipient_owner, asset.address),
        owner=payer, amount_minor=amount_minor, decimals=asset.decimals,
    ))
    return Message.new_with_blockhash(instructions, Pubkey.from_string(str(payer)), Hash.from_string(str(blockhash)))


def principal_moved(answer: Any, *, mint: str, payer: str, recipient_owner: str, amount_minor: int) -> bool | None:
    """The principal proof from a transaction as the node served it: True when its token balances show exactly
    ``amount_minor`` of ``mint`` leaving the payer and arriving at the recipient, False when they show anything else
    (or the transaction failed), None when the node served no token balances to judge by."""
    if not isinstance(answer, dict) or not isinstance(answer.get("meta"), dict):
        return None
    meta = answer["meta"]
    if meta.get("err") is not None:
        return False
    pre, post = meta.get("preTokenBalances"), meta.get("postTokenBalances")
    if not isinstance(pre, list) or not isinstance(post, list):
        return None

    def amounts(rows: list[Any]) -> dict[str, int]:
        found: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict) or str(row.get("mint") or "") != str(mint):
                continue
            try:
                found[str(row.get("owner") or "")] = found.get(str(row.get("owner") or ""), 0) + int(str((row.get("uiTokenAmount") or {}).get("amount")))
            except (TypeError, ValueError):
                return {}
        return found

    before, after = amounts(pre), amounts(post)
    if not after:
        return None
    paid = before.get(str(payer), 0) - after.get(str(payer), 0)
    received = after.get(str(recipient_owner), 0) - before.get(str(recipient_owner), 0)
    return paid == int(amount_minor) and received == int(amount_minor)


__all__ = [
    "ASSOCIATED_TOKEN_PROGRAM",
    "AUTHORITY",
    "ROUTE_LANE",
    "ROUTE_OWNER",
    "TOKEN_PROGRAM",
    "TokenAccountFacts",
    "associated_account",
    "build_transfer_message",
    "create_recipient_account_instruction",
    "is_lane_token",
    "is_token_transfer",
    "principal_moved",
    "read_token_account",
    "require_mint",
    "require_payable",
    "require_wallet_key_owner",
    "token_asset",
    "token_balance_minor",
    "token_route",
    "transfer_checked_instruction",
]
