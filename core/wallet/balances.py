"""A native-coin balance read for one account, for the Crypto surface.

One authority for "what does this account hold right now": the row's own endpoint after its identity proof, the
native coin only (the Crypto Pilot moves native coins; tokens such as USDC are not sent by this wallet), and a typed
answer. A read that could not be made is ``unavailable`` with its reason -- never a zero. Reading never writes.
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
        "note": "Native coin only: this wallet sends the row's native coin; tokens (USDC and others) are not sent by it.",
    }


__all__ = ["STATE_READ", "STATE_UNAVAILABLE", "read_balance"]
