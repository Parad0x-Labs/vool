"""Portable ``.nullpass`` — a self-contained, offline-verifiable work credential.

A ``.nullpass`` bundles a ``Web0WorkReceipt`` plus the issuer's ed25519 signature
into a single JSON blob that anyone can verify WITHOUT VOOL's database and
WITHOUT the network:

  1. recompute the execution proof hash from the proof fields,
  2. recompute the x402 payment receipt hash from the payment fields,
  3. verify the issuer's ed25519 signature over the canonical receipt,
  4. check the proof binds the same result the receipt claims,

and, optionally and online, confirm the payment actually settled on-chain. The
``null://`` dial's receipt becomes a credential the bearer can hand to anyone:
tamper with any field and verification fails closed.

The signed payload is the canonical JSON of the receipt — ``json.dumps(receipt,
sort_keys=True, separators=(",", ":"))`` — so signing and verification agree
byte-for-byte across a JSON round-trip.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any, Optional

from core.proof_of_execution import ProofReceipt, verify_proof_receipt
from core.vool_wallet import b58decode
from core.web0_work_receipt import Web0WorkReceipt
from core.x402.client import X402Receipt

NULLPASS_VERSION = "nullpass/1"


def _canonical(receipt: dict) -> bytes:
    """The exact bytes the issuer signs / the verifier checks (stable across JSON)."""
    return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signer_pubkey(signer: Any) -> str:
    pk = signer.pubkey
    return str(pk() if callable(pk) else pk)


def _explorer(payment_tx: str, mode: str) -> str:
    cluster = "" if mode == "mainnet" else "?cluster=devnet"
    return f"https://explorer.solana.com/tx/{payment_tx}{cluster}"


def build_nullpass(receipt: Web0WorkReceipt, *, signer: Any) -> dict:
    """Mint a ``.nullpass`` for a work receipt, signed by ``signer`` (ed25519).

    ``signer`` exposes ``pubkey`` (str or callable→str) and ``sign(bytes)->bytes``
    — e.g. a ``VoolWallet``. Returns a self-contained, JSON-serializable dict.
    """
    receipt_dict = receipt.to_dict()
    signature = signer.sign(_canonical(receipt_dict))
    payment = receipt_dict.get("payment", {})
    tx = str(payment.get("payment_tx", ""))
    mode = str(payment.get("mode", ""))
    return {
        "version": NULLPASS_VERSION,
        "receipt": receipt_dict,
        "issuer": {
            "alg": "ed25519",
            "pubkey": _signer_pubkey(signer),
            "signature": base64.b64encode(bytes(signature)).decode("ascii"),
        },
        "anchor": {
            "payment_tx": tx,
            "mode": mode,
            "recipient_wallet": payment.get("recipient_wallet"),
            "amount_usdc": payment.get("amount_usdc"),
            "explorer": _explorer(tx, mode) if tx else None,
        },
    }


def _check_proof_hash(receipt: dict) -> bool:
    p = receipt.get("proof")
    if not isinstance(p, dict):
        return False
    try:
        return verify_proof_receipt(ProofReceipt(
            receipt_id=p["receipt_id"], task_id=p["task_id"],
            helper_peer_id=p["helper_peer_id"], result_hash=p["result_hash"],
            started_at=p["started_at"], finished_at=p["finished_at"],
            proof_hash=p["proof_hash"],
            signer_peer_id=p.get("signer_peer_id", ""), signature=p.get("signature", ""),
        ))
    except (KeyError, TypeError):
        return False


def _check_payment_hash(receipt: dict) -> bool:
    pay = receipt.get("payment")
    if not isinstance(pay, dict) or "receipt_hash" not in pay:
        return False
    try:
        recomputed = X402Receipt(
            session_id=pay["session_id"], payment_tx=pay["payment_tx"],
            amount_usdc=float(pay["amount_usdc"]), recipient_wallet=pay["recipient_wallet"],
            facilitator_sig=pay.get("facilitator_sig", ""), timestamp=float(pay["timestamp"]),
            mode=pay["mode"],
        ).receipt_hash
    except (KeyError, TypeError, ValueError):
        return False
    return recomputed == pay["receipt_hash"]


def _check_signature(bundle: dict) -> bool:
    issuer = bundle.get("issuer")
    receipt = bundle.get("receipt")
    if not isinstance(issuer, dict) or not isinstance(receipt, dict):
        return False
    if issuer.get("alg") != "ed25519":
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(b58decode(str(issuer["pubkey"])))
        sig = base64.b64decode(issuer["signature"])
        pub.verify(sig, _canonical(receipt))
        return True
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return False
    except Exception:
        return False


def _check_result_binding(receipt: dict) -> bool:
    p = receipt.get("proof")
    if not isinstance(p, dict):
        return False
    return bool(receipt.get("result_hash")) and receipt.get("result_hash") == p.get("result_hash")


def verify_nullpass(
    bundle: dict, *, confirm_onchain: bool = False,
    rpc_call: Optional[Callable[[str, list], Any]] = None,
) -> dict:
    """Verify a ``.nullpass``. Returns a verdict; ``valid`` is True only if every
    REQUIRED check passes. Fails closed on any malformed or tampered field.

    Offline checks (always): proof_hash, payment receipt_hash, issuer signature,
    result binding. ``confirm_onchain`` additionally re-derives the settlement —
    the payment tx must exist and have succeeded (``rpc_call`` does getTransaction);
    it is advisory and does not gate ``valid`` unless requested.
    """
    errors: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("version") != NULLPASS_VERSION:
        return {"valid": False, "checks": {}, "errors": ["bad_version_or_shape"]}
    receipt = bundle.get("receipt")
    if not isinstance(receipt, dict):
        return {"valid": False, "checks": {}, "errors": ["missing_receipt"]}

    checks = {
        "proof_hash": _check_proof_hash(receipt),
        "payment_receipt_hash": _check_payment_hash(receipt),
        "signature": _check_signature(bundle),
        "result_binding": _check_result_binding(receipt),
    }
    for name, ok in checks.items():
        if not ok:
            errors.append(f"failed:{name}")

    if confirm_onchain:
        checks["settlement"] = _confirm_settlement(receipt, rpc_call)
        if not checks["settlement"]:
            errors.append("failed:settlement")

    required = ("proof_hash", "payment_receipt_hash", "signature", "result_binding")
    valid = all(checks[k] for k in required) and (
        not confirm_onchain or checks.get("settlement", False)
    )
    return {
        "valid": valid,
        "version": NULLPASS_VERSION,
        "receipt_id": receipt.get("receipt_id"),
        "worker_id": receipt.get("worker_id"),
        "issuer": (bundle.get("issuer") or {}).get("pubkey"),
        "checks": checks,
        "errors": errors,
    }


def _confirm_settlement(receipt: dict, rpc_call: Optional[Callable[[str, list], Any]]) -> bool:
    """Online: the payment tx exists on-chain and succeeded. Stub-mode payments
    (no real tx) are not confirmable and return False."""
    pay = receipt.get("payment") or {}
    tx, mode = str(pay.get("payment_tx", "")), str(pay.get("mode", ""))
    if mode == "stub" or not tx or rpc_call is None:
        return False
    try:
        res = rpc_call("getTransaction", [tx, {"encoding": "json", "commitment": "confirmed",
                                              "maxSupportedTransactionVersion": 0}])
    except Exception:
        return False
    return isinstance(res, dict) and res.get("meta") is not None and res["meta"].get("err") is None


__all__ = ["NULLPASS_VERSION", "build_nullpass", "verify_nullpass"]
