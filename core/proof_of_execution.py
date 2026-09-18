from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ProofReceipt:
    receipt_id: str
    task_id: str
    helper_peer_id: str
    result_hash: str
    started_at: str
    finished_at: str
    proof_hash: str
    # Ed25519 identity binding (added later; defaults keep older 7-field construction and
    # serialized receipts working). signer_peer_id is the hex public key that signed the
    # canonical payload; signature is base64 over the same bytes proof_hash covers.
    signer_peer_id: str = ""
    signature: str = ""


def _canonical_payload_bytes(payload: dict[str, str]) -> bytes:
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def create_proof_receipt(*, receipt_id: str, task_id: str, helper_peer_id: str, result_hash: str, started_at: str, finished_at: str) -> ProofReceipt:
    payload = {
        "receipt_id": receipt_id,
        "task_id": task_id,
        "helper_peer_id": helper_peer_id,
        "result_hash": result_hash,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    canonical = _canonical_payload_bytes(payload)
    proof_hash = hashlib.sha256(canonical).hexdigest()
    # Sign with the local node key so a re-hashed forgery does not verify. Defensive: a
    # missing/unavailable signer (e.g. a bare CI runner) must never block receipt creation;
    # the receipt is then simply unsigned and falls back to hash-only verification.
    signer_peer_id = ""
    signature = ""
    try:
        from network.signer import get_local_peer_id, sign

        signature = sign(canonical)
        signer_peer_id = get_local_peer_id()
    except Exception:
        signer_peer_id = ""
        signature = ""
    return ProofReceipt(
        proof_hash=proof_hash,
        signer_peer_id=signer_peer_id,
        signature=signature,
        **payload,
    )


def verify_proof_receipt(receipt: ProofReceipt) -> bool:
    payload = {
        "receipt_id": receipt.receipt_id,
        "task_id": receipt.task_id,
        "helper_peer_id": receipt.helper_peer_id,
        "result_hash": receipt.result_hash,
        "started_at": receipt.started_at,
        "finished_at": receipt.finished_at,
    }
    canonical = _canonical_payload_bytes(payload)
    if hashlib.sha256(canonical).hexdigest() != receipt.proof_hash:
        return False
    # A signed receipt must verify against its signer, so re-hashing forged fields is not
    # enough to pass. An unsigned receipt (legacy, or created where no signer was available)
    # falls back to hash-only self-consistency for backward compatibility.
    if receipt.signature:
        try:
            from network.signer import verify

            return bool(verify(canonical, receipt.signature, receipt.signer_peer_id))
        except Exception:
            return False
    return True
