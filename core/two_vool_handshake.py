"""Two-VOOL local handshake: signed capsule in, signed receipt out.

A minimal, transport-agnostic handshake between two VOOL nodes that share
nothing but each other's public peer id:

  1. Node A builds + signs a TaskCapsule (`core.task_capsule`) and sends it.
  2. Node B independently verifies the capsule (`verify_task_capsule`), then
     issues a *signed receipt* binding B's verdict to the exact capsule hash.
  3. Node A verifies the receipt against B's peer id and confirms it
     acknowledges the capsule A actually sent.

The receipt reuses the capsule's canonical-JSON + sha256 + ed25519 discipline
verbatim, so the same tamper guarantees hold: any mutation of the capsule or
the receipt, or a signature from the wrong key, is rejected. There is no
network code here — callers move the two JSON blobs over whatever transport
they like; this module owns only the build/verify contract.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from core.task_capsule import TaskCapsule, verify_task_capsule
from network import signer

__all__ = [
    "RECEIPT_STATUSES",
    "HandshakeError",
    "build_capsule_receipt",
    "canonical_receipt_bytes",
    "compute_receipt_hash",
    "verify_capsule_receipt",
]

RECEIPT_STATUSES = ("accepted", "rejected")


class HandshakeError(ValueError):
    """Raised when a capsule or receipt fails verification."""


def _json_default(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _unsigned_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    out = dict(receipt)
    out.pop("signature", None)
    return out


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    """Canonical bytes the signature covers: everything except ``signature``.

    ``receipt_hash`` is intentionally included so the signature also binds the
    hash — re-hashing a tampered receipt without the private key still fails.
    """
    return json.dumps(
        _unsigned_receipt(receipt),
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("receipt_hash", None)
    payload.pop("signature", None)
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_capsule_receipt(
    capsule: dict[str, Any] | TaskCapsule,
    *,
    status: str = "accepted",
    detail: str = "",
) -> dict[str, Any]:
    """Node B: verify the incoming capsule, then issue a signed receipt for it.

    Signs with B's *local* key; the receipt names B as ``receiver_agent_id`` and
    binds B's verdict to the exact capsule hash. Raises :class:`HandshakeError`
    if the capsule itself does not verify, so B never receipts a forged capsule.
    """
    if status not in RECEIPT_STATUSES:
        raise HandshakeError(f"unknown receipt status: {status!r}")

    raw = capsule.model_dump() if isinstance(capsule, TaskCapsule) else dict(capsule)
    try:
        verified = verify_task_capsule(raw)
    except ValueError as exc:
        raise HandshakeError(f"refusing to receipt an invalid capsule: {exc}") from exc

    receipt: dict[str, Any] = {
        "receipt_id": str(uuid.uuid4()),
        "capsule_id": verified.capsule_id,
        "capsule_hash": verified.capsule_hash,
        "task_id": verified.task_id,
        "sender_agent_id": verified.parent_agent_id,
        "receiver_agent_id": signer.get_local_peer_id(),
        "status": status,
        "detail": detail,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    receipt["signature"] = signer.sign(canonical_receipt_bytes(receipt))
    return receipt


def verify_capsule_receipt(
    receipt: dict[str, Any],
    *,
    expected_capsule_hash: str | None = None,
    expected_receiver_id: str | None = None,
) -> dict[str, Any]:
    """Node A: verify a receipt B returned. Raises :class:`HandshakeError` on mismatch.

    Checks, in order: shape, ``receipt_hash`` integrity, ed25519 signature
    against the receiver's claimed peer id, and — when given — that the receipt
    acknowledges the exact capsule A sent and was signed by the expected peer.
    """
    if not isinstance(receipt, dict):
        raise HandshakeError("receipt must be a dict")
    for field in ("receipt_hash", "signature", "receiver_agent_id", "capsule_hash"):
        if not receipt.get(field):
            raise HandshakeError(f"receipt missing {field}")

    if compute_receipt_hash(receipt) != receipt["receipt_hash"]:
        raise HandshakeError("receipt hash mismatch")

    receiver = str(receipt["receiver_agent_id"])
    if expected_receiver_id is not None and receiver != str(expected_receiver_id):
        raise HandshakeError("receipt signed by an unexpected receiver")

    if not signer.verify(canonical_receipt_bytes(receipt), str(receipt["signature"]), receiver):
        raise HandshakeError("receipt signature invalid")

    if expected_capsule_hash is not None and receipt["capsule_hash"] != str(expected_capsule_hash):
        raise HandshakeError("receipt acknowledges a different capsule")

    if receipt.get("status") not in RECEIPT_STATUSES:
        raise HandshakeError("receipt has an unknown status")

    return receipt
