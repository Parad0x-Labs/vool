"""
core/x402/receipt_verifier.py
=============================
Local, read-only on-chain verification of an x402 USDC settlement.

Why this exists
---------------
An :class:`core.x402.client.X402Receipt` carries a real SHA-256
``receipt_hash`` even in STUB mode, and
:func:`core.credit_ledger.is_real_receipt_hash` accepts ANY 64-char hex string.
So a caller who self-reports a ``receipt_hash`` plus a ``mode`` of "mainnet"
could earn full proof-of-settlement reputation without ever moving a token.

This module closes that hole by re-deriving settlement truth from the chain
itself: given a payment transaction signature, it asks a COMPLIANT publicnode
Solana RPC whether that transaction actually moved the expected amount of USDC
into the account it should have. Nothing here trusts the caller's claimed hash.

Scope (be precise about what this proves)
-----------------------------------------
This is an **on-chain USDC-transfer check**: it confirms a finalized transaction
exists, did not error, and increased the credited token account's USDC balance —
for the cluster's USDC mint (mainnet vs devnet, selected by ``mode``) — by at
least the expected amount. The x402 client transfers into the facilitator escrow
ATA rather than the recipient wallet's own ATA, so callers pass the credited
account (``credited_account``) to check the account the tx truly funded; with no
credited account given, the check falls back to the recipient wallet's owned
USDC account(s). It is NOT the full facilitator-verified x402 flow (quote
binding, facilitator signature, escrow-ATA routing, replay/nonce checks) — that
lives in the cross-repo ``dna_x402`` verifier. Treat a ``True`` here as "a real
USDC payment of >= the amount reached the checked account", not as "the complete
x402 protocol was honored".

Design stance: **fail closed.** Any RPC failure, missing transaction, parse
error, wrong mint, short amount, unresolvable credited account, or unsupported
mode returns ``False``. When in doubt, return ``False``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.vool_wallet import _rpc_call as _default_rpc_call

# The USDC SPL mint differs by cluster: mainnet-beta and devnet use distinct
# mints. We reuse the SAME constants the payment client uses so the verifier
# checks the exact mint a real settlement moved on each cluster — never a
# hardcoded mismatch. ``USDC_MINT_MAINNET`` is re-exported for callers/tests.
from core.x402.client import (
    USDC_MINT_DEVNET as _CLIENT_USDC_MINT_DEVNET,
)
from core.x402.client import (
    USDC_MINT_MAINNET as _CLIENT_USDC_MINT_MAINNET,
)

# Re-export the canonical mainnet mint (kept as a module symbol for callers and
# tests that import it from here). The value is sourced from core.x402.client.
USDC_MINT_MAINNET = _CLIENT_USDC_MINT_MAINNET
USDC_MINT_DEVNET = _CLIENT_USDC_MINT_DEVNET

# The USDC mint to check, selected by settlement mode. ``getTransaction`` jsonParsed
# token-balance entries carry the cluster-specific mint, so the verifier must
# compare against the mint that matches ``mode``.
_USDC_MINT_BY_MODE = {
    "mainnet": USDC_MINT_MAINNET,
    "devnet": USDC_MINT_DEVNET,
}

# USDC has 6 decimals; 1 atomic unit = 1e-6 USDC. We compare balances in integer
# atomic units to avoid float drift, then allow a sub-atomic tolerance below.
USDC_DECIMALS = 6
USDC_ATOMIC_PER_UNIT = 10 ** USDC_DECIMALS

# Settlement modes this verifier will confirm. Anything else fails closed.
_VERIFIABLE_MODES = ("mainnet", "devnet")

# Tolerance, in atomic units, applied when comparing the observed USDC delta to
# the expected amount. One atomic unit (1e-6 USDC) absorbs the rounding of the
# expected float into atomic units without ever accepting a short payment of a
# whole atomic unit or more.
_ATOMIC_TOLERANCE = 1


def _account_keys(result: Any) -> list[str]:
    """Flatten a jsonParsed transaction's account list to base58 pubkey strings.

    A token-balance entry references the account it describes by ``accountIndex``
    into this list. The list is ``transaction.message.accountKeys`` (each entry is
    either a bare base58 string or a ``{"pubkey": ...}`` object under jsonParsed),
    followed by any ``meta.loadedAddresses`` (writable then readonly) for v0
    transactions with address-table lookups. We resolve indices through this same
    flattened order so we can match the exact credited token account.

    Returns ``[]`` on any malformed shape (the caller then fails closed).
    """
    keys: list[str] = []
    if not isinstance(result, dict):
        return keys
    txn = result.get("transaction")
    if isinstance(txn, dict):
        message = txn.get("message")
        if isinstance(message, dict):
            for raw in message.get("accountKeys") or []:
                if isinstance(raw, dict):
                    keys.append(str(raw.get("pubkey") or ""))
                else:
                    keys.append(str(raw))
    meta = result.get("meta")
    if isinstance(meta, dict):
        loaded = meta.get("loadedAddresses")
        if isinstance(loaded, dict):
            for bucket in ("writable", "readonly"):
                for raw in loaded.get(bucket) or []:
                    keys.append(str(raw))
    return keys


def _usdc_atomic_for_target(
    balances: Any,
    *,
    usdc_mint: str,
    owner: str | None,
    account_pubkeys: set[str] | None,
    keys: list[str],
) -> int:
    """Sum the USDC atomic balance of the target token account(s).

    ``balances`` is ``meta.preTokenBalances`` / ``meta.postTokenBalances`` from a
    jsonParsed ``getTransaction`` response: a list of entries shaped like
    ``{"accountIndex": <int>, "owner": <pubkey>, "mint": <mint>,
       "uiTokenAmount": {"amount": "<int str>"}}``.

    An entry contributes only when its ``mint`` equals ``usdc_mint`` AND it
    matches the requested target:

    * if ``account_pubkeys`` is given, the entry's ``accountIndex`` must resolve
      (through ``keys``) to one of those token-account addresses — this is the
      precise check for the account the payment tx actually credited (the
      facilitator escrow ATA in the x402 flow, or a recipient ATA);
    * otherwise the entry's ``owner`` must equal ``owner`` (the wallet-owner
      fallback used when no explicit credited account is supplied).

    Returns the total in integer atomic units; a target with no matching USDC
    entry contributes 0 (a valid "before" state when the token account is created
    by this very transfer). Any malformed entry is skipped conservatively.
    """
    total = 0
    if not isinstance(balances, list):
        return 0
    for entry in balances:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("mint") or "") != usdc_mint:
            continue
        if account_pubkeys is not None:
            idx = entry.get("accountIndex")
            if not isinstance(idx, int) or idx < 0 or idx >= len(keys):
                continue
            if keys[idx] not in account_pubkeys:
                continue
        else:
            if str(entry.get("owner") or "") != owner:
                continue
        ui = entry.get("uiTokenAmount")
        if not isinstance(ui, dict):
            continue
        raw_amount = ui.get("amount")
        try:
            # ``amount`` is the atomic-unit count as a string per the SPL spec.
            total += int(str(raw_amount))
        except (TypeError, ValueError):
            # A malformed amount must not inflate the observed delta.
            continue
    return total


def verify_payment_receipt(
    payment_tx: str,
    *,
    recipient_wallet: str,
    amount_usdc: float,
    mode: str,
    credited_account: str | None = None,
    rpc_call: Callable[..., Any] | None = None,
) -> bool:
    """Confirm an on-chain USDC transfer actually settled, read-only.

    Looks up ``payment_tx`` via ``getTransaction`` (jsonParsed, finalized
    commitment) over the COMPLIANT publicnode RPC and returns ``True`` ONLY when
    every one of these holds:

    * ``mode`` is one of ``("mainnet", "devnet")``;
    * ``payment_tx``, ``recipient_wallet`` are non-empty and ``amount_usdc > 0``;
    * the transaction exists and ``meta.err`` is null (it did not fail);
    * the credited token account's USDC balance — for the mint that matches
      ``mode`` (mainnet vs devnet) — computed as
      ``postTokenBalances - preTokenBalances`` in atomic units, increased by at
      least ``amount_usdc`` (within a one-atomic-unit float tolerance).

    Which account is checked
    ------------------------
    The x402 payment tx built by :class:`core.x402.client.X402Client` transfers
    USDC into the FACILITATOR ESCROW ATA (``dest=escrow_ata``), not the
    recipient wallet's own ATA — so the recipient wallet's balance is unchanged
    on ``payment_tx`` itself. Pass ``credited_account`` with the token account the
    tx credited (the escrow ATA, or a recipient ATA for a direct transfer) and
    the verifier confirms the USDC landed in THAT account, matched by its on-chain
    address via the transaction's ``accountKeys``.

    When ``credited_account`` is omitted, the verifier falls back to confirming
    the USDC increase for the token account(s) OWNED by ``recipient_wallet`` — the
    direct-transfer case where the recipient receives the USDC into its own ATA.

    Fail-closed: returns ``False`` on any RPC failure, missing transaction, parse
    error, wrong mint, insufficient amount, unsupported mode, or any doubt. This
    function performs **no** signing and **no** state mutation — it only reads.

    NOTE: this is an on-chain USDC-transfer check, not the full
    facilitator-verified x402 flow (facilitator signature / quote binding /
    escrow routing). That stronger check lives in the cross-repo ``dna_x402``
    verifier; this is the local, trust-nothing settlement gate.

    Parameters
    ----------
    payment_tx:
        Solana transaction signature (base58) to inspect.
    recipient_wallet:
        Base58 pubkey of the wallet that should ultimately be paid. Used as the
        owner-match fallback when ``credited_account`` is not supplied.
    amount_usdc:
        Minimum whole-USDC amount that must have been received.
    mode:
        ``"mainnet"`` or ``"devnet"``. Any other value fails closed. Selects the
        USDC mint (mainnet vs devnet) the transfer is checked against.
    credited_account:
        Optional base58 address of the SPL token account the payment tx credited
        (the facilitator escrow ATA in the x402 flow, or a recipient ATA). When
        given, the USDC increase is verified against THIS account; when omitted,
        the verifier matches token accounts owned by ``recipient_wallet``.
    rpc_call:
        Optional injected RPC callable (used by tests). Defaults to the shared
        publicnode-only :func:`core.vool_wallet._rpc_call`.

    Returns
    -------
    bool
        ``True`` only if a real USDC transfer of >= ``amount_usdc`` into the
        checked account is confirmed on-chain; ``False`` otherwise.
    """
    # ── Cheap, total guards first — fail closed on any malformed input. ──────
    mode_key = str(mode or "").strip().lower()
    if mode_key not in _VERIFIABLE_MODES:
        return False
    usdc_mint = _USDC_MINT_BY_MODE.get(mode_key)
    if not usdc_mint:
        return False
    tx_sig = str(payment_tx or "").strip()
    recipient = str(recipient_wallet or "").strip()
    credited = str(credited_account or "").strip()
    # We need SOME target to check: an explicit credited account, or a recipient
    # wallet to owner-match. With neither, there is nothing to confirm → False.
    if not tx_sig or (not recipient and not credited):
        return False
    try:
        expected = float(amount_usdc)
    except (TypeError, ValueError):
        return False
    if expected != expected or expected in (float("inf"), float("-inf")):
        return False  # NaN / inf guard
    if expected <= 0:
        return False
    # Required atomic-unit increase, rounded down so we never demand more than
    # asked; the tolerance below absorbs the rounding the other direction.
    expected_atomic = int(expected * USDC_ATOMIC_PER_UNIT)
    if expected_atomic <= 0:
        return False

    call = rpc_call or _default_rpc_call

    # ── Read-only on-chain lookup. Any exception here means "unknown" → False.
    try:
        result = call(
            "getTransaction",
            [
                tx_sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "finalized",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
    except Exception:
        return False

    # Missing tx (RPC None / not found / non-dict) → fail closed.
    if not isinstance(result, dict):
        return False

    meta = result.get("meta")
    if not isinstance(meta, dict):
        return False

    # A transaction that erred did not settle.
    if meta.get("err") is not None:
        return False

    # Choose the target: the explicit credited token account (matched by address
    # via accountKeys) when supplied, else the recipient wallet's owned account(s).
    if credited:
        account_pubkeys: set[str] | None = {credited}
        owner: str | None = None
        keys = _account_keys(result)
        # An explicit credited account we cannot resolve in this tx's account list
        # means the tx never touched it → nothing to confirm → fail closed.
        if credited not in keys:
            return False
    else:
        account_pubkeys = None
        owner = recipient
        keys = []

    pre = _usdc_atomic_for_target(
        meta.get("preTokenBalances"),
        usdc_mint=usdc_mint,
        owner=owner,
        account_pubkeys=account_pubkeys,
        keys=keys,
    )
    post = _usdc_atomic_for_target(
        meta.get("postTokenBalances"),
        usdc_mint=usdc_mint,
        owner=owner,
        account_pubkeys=account_pubkeys,
        keys=keys,
    )
    delta_atomic = post - pre

    # Require the credited account's USDC to have INCREASED by at least the
    # expected amount, allowing a one-atomic-unit tolerance for float→atomic
    # rounding.
    return delta_atomic >= (expected_atomic - _ATOMIC_TOLERANCE) and delta_atomic > 0


__all__ = [
    "USDC_MINT_DEVNET",
    "USDC_MINT_MAINNET",
    "verify_payment_receipt",
]
