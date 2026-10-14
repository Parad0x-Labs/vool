"""The words the owner reads when a send of their own token (``chains.USER_TRANSFER_TOKENS``, USDT) is refused before
anything is signed: what happened, that nothing was sent, and the step that actually helps. Payment lanes keep the
catalog's general lines; only the owner's token sends use these."""
from __future__ import annotations

from typing import Any

_CONTRACT_NOT_AS_EXPECTED = ("{symbol} on {chain} can't be sent right now: its token contract isn't working the way this wallet expects. "
                             "Nothing was sent. You can send {symbol} on another network, or try again later.")
_NEEDS_FEE_COIN = ("Sending {symbol} needs a little {gas} in this account to pay the network fee, and there isn't enough. "
                   "Nothing was sent. Add some {gas} to this account, then try again.")
_TEXTS: dict[str, str] = {
    "token_balance_below_amount": "This account doesn't hold enough {symbol} for this amount. Nothing was sent. Lower the amount or add {symbol}, then try again.",
    "fee_balance_below_fee_ceiling": _NEEDS_FEE_COIN,
    "fee_balance_below_fee_and_rent": _NEEDS_FEE_COIN,
    "token_takes_a_transfer_fee": ("{symbol} on {chain} can't be sent right now: its token contract would take a cut, so the recipient would get less "
                                   "than you approve. Nothing was sent. You can send {symbol} on another network, or try again later."),
    "token_contract_has_no_code": _CONTRACT_NOT_AS_EXPECTED,
    "token_decimals_differ_from_registry": _CONTRACT_NOT_AS_EXPECTED,
    "recipient_is_the_token_contract": ("That address is the {symbol} token contract itself, not someone's wallet, and {symbol} sent there is lost. "
                                        "Nothing was sent. Check the recipient's address, then try again."),
    "token_transfers_need_a_crypto_pilot_wallet": "{symbol} can be sent only from a Crypto Pilot account. Nothing was sent. You can create one in Settings, Crypto.",
}


def refusal_text(reason: str, *, symbol: str, gas: str = "", chain: str = "") -> str:
    """The owner's line for ``reason``, or "" when the catalog's general line stays."""
    text = _TEXTS.get(str(reason or ""))
    return text.format(symbol=symbol, gas=gas or "the network's own coin", chain=chain or "this network") if text else ""


def worded(fault: Any, *, symbol: str, gas: str = "", chain: str = "") -> Any:
    """``fault`` with its user line set from its context's reason when one is defined here; returned for ``raise``."""
    text = refusal_text(str((getattr(fault, "context", None) or {}).get("reason") or ""), symbol=symbol, gas=gas, chain=chain)
    if text:
        fault.user_message = text
    return fault


__all__ = ["refusal_text", "worded"]
