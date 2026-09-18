from __future__ import annotations

import os
import threading

# The read-only RPC door (publicnode only) is authority-free: it reads blockhashes and signature
# statuses for the kept confirm/preview helpers. Nothing in this module signs or broadcasts.
from core.vool_wallet import _rpc_call

# Reuse the ONE compliant RPC path (publicnode only — never api.mainnet-beta,
# which 403s with an Origin header) and the base58 codec + wallet loader.

# solders builds the canonical transaction message; the wallet signs the exact
# serialized bytes, so no raw keypair leaves the wallet abstraction.
try:
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.message import Message
    from solders.pubkey import Pubkey
except ImportError:  # solders not installed -> anchoring degrades to no-op
    Hash = Instruction = AccountMeta = Message = Pubkey = None  # type: ignore[assignment]

# SPL Memo program — arbitrary on-chain note, the safe minimal anchor (no
# program-specific account layout to get wrong). Every anchored receipt becomes
# a clickable Solana transaction whose memo carries the work-receipt hash.
_MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
_ANCHOR_TAG = b"vool-receipt:"


def anchor_enabled() -> bool:
    """True only when receipt anchoring is explicitly opted in (VOOL_ANCHOR_RECEIPTS=1).

    A real anchor broadcasts a SOL-spending memo tx, so it must never fire by
    default. Both call sites (finalizer + the API service) gate on this single
    helper so they cannot drift apart.
    """
    return os.environ.get("VOOL_ANCHOR_RECEIPTS") == "1"


def _anchor_spend_blocked() -> bool:
    """Kept for callers; anchoring itself is retired (see submit_memo_anchor)."""
    return True


def build_memo_anchor_message(payer_pubkey: str, payload_hash: str, recent_blockhash: str) -> bytes:
    """Serialize a single-signer SPL-Memo message carrying the receipt hash.

    Pure + deterministic (no network, no signing). The fee payer is the only
    signer; the memo data is ``vool-receipt:<hash>``.
    """
    if Pubkey is None:
        raise RuntimeError("solders is not installed")
    payer = Pubkey.from_string(payer_pubkey)
    memo = Pubkey.from_string(_MEMO_PROGRAM_ID)
    ix = Instruction(
        program_id=memo,
        accounts=[AccountMeta(pubkey=payer, is_signer=True, is_writable=True)],
        data=_ANCHOR_TAG + payload_hash.encode("utf-8"),
    )
    msg = Message.new_with_blockhash([ix], payer, Hash.from_string(recent_blockhash))
    return bytes(msg)


def _latest_blockhash() -> str | None:
    # Anchor against a 'finalized' blockhash so the tx commits to a rooted slot
    # (no risk of building on a forked/dropped recent block).
    result = _rpc_call("getLatestBlockhash", [{"commitment": "finalized"}])
    if isinstance(result, dict):
        value = result.get("value") or {}
        bh = value.get("blockhash")
        if bh:
            return str(bh)
    return None


def parse_signature_status(result: object) -> dict[str, object] | None:
    """Pull the single per-signature status out of a getSignatureStatuses result.

    Pure parser (no network) so it is trivially testable against a fixture.
    Returns the status dict (with ``confirmationStatus``, ``err``, ``slot``) for
    the first/only signature, or None when the signature is unknown to the RPC
    (the ``value`` slot is null) or the shape is unexpected.
    """
    if not isinstance(result, dict):
        return None
    value = result.get("value")
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    return first if isinstance(first, dict) else None


def confirm_signature(signature: str, *, commitment: str = "finalized") -> dict[str, object] | None:
    """OPTIONAL, light landed-confirmation check for an anchor tx signature.

    Does a single getSignatureStatuses call (with searchTransactionHistory) and
    returns the parsed status dict, or None if unknown/unavailable. This is NOT
    wired into the broadcast hot path — broadcasting stays fire-and-forget so the
    finalize path runs at full speed; callers opt in to confirmation explicitly.
    """
    if not signature:
        return None
    try:
        result = _rpc_call(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
        )
    except Exception:
        return None
    status = parse_signature_status(result)
    if status is None:
        return None
    # Surface the requested commitment alongside the raw status for callers that
    # want to compare without re-passing it.
    return {"requested_commitment": commitment, **status}


def submit_memo_anchor(payload_hash: str) -> str | None:
    """RETIRED: an unattended broadcast on every turn is exactly the bypass the canonical lifecycle forbids.

    Anchoring, when it returns, is a wallet proposal the operator approves. Typed, receipt-backed refusal.
    """
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("solana_anchor.submit_memo_anchor")


def anchor_vault_proof(parent_task_id: str, final_response_hash: str, confidence: float) -> str | None:
    """RETIRED with submit_memo_anchor; a strict no-op when anchoring is off, a typed refusal when on."""
    if not anchor_enabled():
        return None
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("solana_anchor.anchor_vault_proof")


def _anchor_and_persist(parent_task_id: str, final_response_hash: str, confidence: float) -> None:
    return None


def dispatch_anchor_in_background(parent_task_id: str, final_response_hash: str, confidence: float) -> threading.Thread | None:
    """RETIRED: never spawns; a strict no-op when anchoring is off, a typed refusal when on."""
    if not anchor_enabled():
        return None
    from core.wallet.authority import refuse_legacy

    raise refuse_legacy("solana_anchor.dispatch_anchor_in_background")


__all__ = [
    "anchor_enabled",
    "anchor_vault_proof",
    "build_memo_anchor_message",
    "confirm_signature",
    "dispatch_anchor_in_background",
    "parse_signature_status",
    "submit_memo_anchor",
]
