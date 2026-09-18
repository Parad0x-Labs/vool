"""Execution receipts are Ed25519-signed so a re-hashed forgery does not verify.

Before signing, verify_proof_receipt only checked a self-recomputable SHA-256, so anyone
could recompute a valid proof_hash for arbitrary fields. Signing binds the receipt to the
signer's key; an unsigned (legacy) receipt still verifies on hash alone for compatibility.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json

from core.proof_of_execution import ProofReceipt, create_proof_receipt, verify_proof_receipt


def _make() -> ProofReceipt:
    return create_proof_receipt(
        receipt_id="r1",
        task_id="t1",
        helper_peer_id="h1",
        result_hash="rh",
        started_at="a",
        finished_at="b",
    )


def test_signed_receipt_verifies_and_carries_signature() -> None:
    receipt = _make()
    assert receipt.signature and receipt.signer_peer_id
    assert verify_proof_receipt(receipt) is True


def test_rehashed_forgery_with_stale_signature_is_rejected() -> None:
    receipt = _make()
    # A forger edits a field AND recomputes a self-consistent proof_hash (the hash is public),
    # but cannot re-sign without the key. The stale signature must fail verification.
    payload = {
        "receipt_id": "r1",
        "task_id": "t1",
        "helper_peer_id": "h1",
        "result_hash": "EVIL",
        "started_at": "a",
        "finished_at": "b",
    }
    forged_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    forged = dataclasses.replace(receipt, result_hash="EVIL", proof_hash=forged_hash)
    assert verify_proof_receipt(forged) is False


def test_tampered_signature_is_rejected() -> None:
    receipt = _make()
    assert verify_proof_receipt(dataclasses.replace(receipt, signature="AAAA")) is False


def test_unsigned_legacy_receipt_still_verifies_on_hash() -> None:
    payload = {
        "receipt_id": "r2",
        "task_id": "t2",
        "helper_peer_id": "h2",
        "result_hash": "x",
        "started_at": "a",
        "finished_at": "b",
    }
    proof_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    legacy = ProofReceipt(proof_hash=proof_hash, **payload)  # no signature fields
    assert legacy.signature == ""
    assert verify_proof_receipt(legacy) is True
    # …but a hash that does not match its own fields still fails.
    assert verify_proof_receipt(dataclasses.replace(legacy, result_hash="y")) is False
