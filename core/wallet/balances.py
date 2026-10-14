"""Balance reads for one account, for the Crypto surface and the chat.

One authority for "what does this account hold right now": the row's own endpoint after its identity proof, and a
typed answer. :func:`read_balance` reads the native coin; :func:`read_token_balances` reads the tokens the owner may
send from that row (``chains.USER_TRANSFER_TOKENS``, USDT on Solana). A read that could not be made is
``unavailable`` with its reason -- never a zero. Reading never writes.
"""
from __future__ import annotations

import time
from typing import Any

from core.wallet import amounts, chains, custody, environment
from core.wallet.errors import WalletFault

AUTHORITY = "core.wallet.balances"
STATE_READ = "read"
STATE_UNAVAILABLE = "unavailable"


def _unavailable(profile: Any, spec: Any, reason: str) -> dict[str, Any]:
    native = chains.native_asset(spec.network)
    return {
        "wallet_id": profile.wallet_id, "network": spec.network, "asset": spec.native_display_symbol or native.symbol,
        "state": STATE_UNAVAILABLE, "reason": reason, "balance_minor": None, "balance_human": "", "balance_display": "",
        "ref": "", "observed_at": time.time(), "note": "The balance could not be read; it is not zero.",
    }


def read_balance(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """The account's native balance from its row's endpoint, or a typed ``unavailable``."""
    custody.require_enabled(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    spec = chains.resolve_network(profile.network)
    native = chains.native_asset(spec.network)
    symbol = spec.native_display_symbol or native.symbol
    if not environment.is_active(spec.network):
        return _unavailable(profile, spec, "environment_inactive")
    try:
        from core.wallet import quotes

        if spec.is_svm:
            from core.wallet import lifecycle

            rpc = lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
            quotes._prove(spec, rpc.url, lambda method: rpc._call(method, []), source_context=source_context)
            answer = rpc._call("getBalance", [profile.public_key, {"commitment": "confirmed"}])
            if not isinstance(answer, dict) or answer.get("value") is None:
                return _unavailable(profile, spec, "balance_unknown")
            minor = int(answer["value"])
            ref = f"slot:{int((answer.get('context') or {}).get('slot') or 0)}"
        else:
            from core.wallet import evm

            evm.require_evm_dependencies("balance_read", source_context=source_context)
            url = chains.network_rpc_url(spec.network)
            quotes._prove(spec, url, lambda method: quotes._evm_rpc(spec, url, method, [], source_context=source_context), source_context=source_context)
            minor = int(quotes._evm_rpc(spec, url, "eth_getBalance", [profile.public_key, "latest"], source_context=source_context), 16)
            block = quotes._evm_rpc(spec, url, "eth_getBlockByNumber", ["latest", False], source_context=source_context)
            ref = f"block:{int(block['number'], 16)}"
    except WalletFault as exc:
        return _unavailable(profile, spec, str(exc.context.get("reason") or exc.code))
    except Exception as exc:  # a transport or shape failure is a reason, never a number
        return _unavailable(profile, spec, f"read_failed:{type(exc).__name__}")
    return {
        "wallet_id": profile.wallet_id, "network": spec.network, "asset": symbol, "state": STATE_READ, "reason": "",
        "balance_minor": str(minor), "balance_human": amounts.format_minor(minor, native.decimals),
        "balance_display": amounts.display_amount(minor, native.decimals, symbol), "ref": ref, "observed_at": time.time(),
        "note": _native_note(spec),
    }


def _native_note(spec: Any) -> str:
    tokens = [token.symbol for token in chains.user_transfer_tokens(spec.network)]
    if tokens:
        return f"The chain's own coin; {', '.join(tokens)} on this chain is read on its own."
    return "Native coin only: this wallet sends the row's native coin; tokens (USDC and others) are not sent by it."


def read_token_balances(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What the account holds of each token the owner may send from its row, read from the row's endpoint after its
    identity proof: one answer per token, ``unavailable`` with its reason when it could not be read (never a zero).
    A row without such tokens answers an empty list."""
    custody.require_enabled(source_context=source_context)
    profile = custody.require_wallet(wallet_id, source_context=source_context)
    spec = chains.resolve_network(profile.network)
    tokens = chains.user_transfer_tokens(spec.network)
    if not tokens:
        return []

    def unavailable(token: chains.AssetSpec, reason: str) -> dict[str, Any]:
        return {"wallet_id": profile.wallet_id, "network": spec.network, "asset": token.symbol, "state": STATE_UNAVAILABLE, "reason": reason,
                "balance_minor": None, "balance_human": "", "balance_display": "", "ref": "", "observed_at": time.time(),
                "note": "The balance could not be read; it is not zero."}

    if not environment.is_active(spec.network) or not spec.is_svm:
        return [unavailable(token, "environment_inactive" if spec.is_svm else "token_read_not_supported") for token in tokens]
    from core.wallet import lifecycle, quotes, svm_tokens

    try:
        rpc = lifecycle.RpcClient(chains.network_rpc_url(spec.network), network=spec.network)
        quotes._prove(spec, rpc.url, lambda method: rpc._call(method, []), source_context=source_context)
    except WalletFault as exc:
        return [unavailable(token, str(exc.context.get("reason") or exc.code)) for token in tokens]
    except Exception as exc:
        return [unavailable(token, f"read_failed:{type(exc).__name__}") for token in tokens]
    answers = []
    for token in tokens:
        try:
            svm_tokens.require_mint(rpc, token, source_context=source_context)
            minor = svm_tokens.token_balance_minor(rpc, owner=profile.public_key, asset=token, source_context=source_context)
        except WalletFault as exc:
            answers.append(unavailable(token, str(exc.context.get("reason") or exc.code)))
            continue
        except Exception as exc:
            answers.append(unavailable(token, f"read_failed:{type(exc).__name__}"))
            continue
        answers.append({
            "wallet_id": profile.wallet_id, "network": spec.network, "asset": token.symbol, "state": STATE_READ, "reason": "",
            "balance_minor": str(minor), "balance_human": amounts.format_minor(minor, token.decimals),
            "balance_display": amounts.display_amount(minor, token.decimals, token.symbol), "ref": "", "observed_at": time.time(), "note": "",
        })
    return answers


__all__ = ["STATE_READ", "STATE_UNAVAILABLE", "read_balance", "read_token_balances"]
