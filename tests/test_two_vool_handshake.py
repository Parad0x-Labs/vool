"""Two-VOOL local handshake: signed capsule in, signed receipt out.

A genuine two-identity round-trip — node A and node B hold *distinct* ed25519
keys (simulated by swapping the process-local keypair) — plus a tamper matrix:
mutate the capsule, mutate the receipt, re-hash without re-signing, or claim
the wrong signer, and verification rejects it.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from core import two_vool_handshake as h
from core.task_capsule import build_task_capsule
from network import signer


@contextlib.contextmanager
def _identity():
    """Run the body under a fresh process-local signing key (a distinct node)."""
    prev = signer._LOCAL_KEYPAIR
    sk = signer._generate_signing_key()
    signer._LOCAL_KEYPAIR = signer.LocalKeypair(signing_key=sk, verify_key=signer._verify_key(sk))
    try:
        yield signer.get_local_peer_id()
    finally:
        signer._LOCAL_KEYPAIR = prev


def _capsule():
    return build_task_capsule(
        parent_agent_id=signer.get_local_peer_id(),
        task_id="task-handshake-1",
        task_type="research",
        subtask_type="summarize-findings",
        summary="rank three abstract options by stated constraints",
        sanitized_context={
            "problem_class": "ranking",
            "environment_tags": {"tier": "local"},
            "abstract_inputs": ["option-a", "option-b", "option-c"],
            "known_constraints": ["latency-bound", "offline-only"],
        },
        allowed_operations=["reason", "rank", "summarize"],
        deadline_ts=datetime.now(timezone.utc) + timedelta(hours=1),
    ).model_dump()


def test_two_node_roundtrip_distinct_identities():
    capsule = _capsule()                 # node A builds + signs with its own key
    a_id = capsule["parent_agent_id"]

    with _identity() as b_id:            # node B: a distinct key
        assert b_id != a_id
        receipt = h.build_capsule_receipt(capsule, status="accepted", detail="ok")

    assert receipt["receiver_agent_id"] == b_id
    assert receipt["sender_agent_id"] == a_id
    assert receipt["capsule_hash"] == capsule["capsule_hash"]

    out = h.verify_capsule_receipt(      # back on node A
        receipt,
        expected_capsule_hash=capsule["capsule_hash"],
        expected_receiver_id=b_id,
    )
    assert out["status"] == "accepted"


def test_forged_capsule_is_not_receipted():
    capsule = _capsule()
    capsule["summary"] = capsule["summary"] + " tampered"  # breaks the capsule hash/sig
    with _identity(), pytest.raises(h.HandshakeError):
        h.build_capsule_receipt(capsule)


def test_tampered_receipt_field_rejected():
    capsule = _capsule()
    with _identity():
        receipt = h.build_capsule_receipt(capsule)
    receipt["status"] = "rejected"       # flip the verdict, leave hash/sig stale
    with pytest.raises(h.HandshakeError):
        h.verify_capsule_receipt(receipt)


def test_rehashed_receipt_without_resign_rejected():
    capsule = _capsule()
    with _identity():
        receipt = h.build_capsule_receipt(capsule)
    receipt["status"] = "rejected"
    receipt["receipt_hash"] = h.compute_receipt_hash(receipt)  # patch the hash...
    with pytest.raises(h.HandshakeError):                      # ...but the signature won't match
        h.verify_capsule_receipt(receipt)


def test_wrong_signer_rejected():
    capsule = _capsule()
    with _identity():
        receipt = h.build_capsule_receipt(capsule)
    with _identity() as other_id:        # claim a receiver that did not actually sign
        receipt["receiver_agent_id"] = other_id
        receipt["receipt_hash"] = h.compute_receipt_hash(receipt)
        with pytest.raises(h.HandshakeError):
            h.verify_capsule_receipt(receipt)


def test_receipt_for_a_different_capsule_rejected():
    capsule = _capsule()
    other = _capsule()
    with _identity():
        receipt = h.build_capsule_receipt(capsule)
    with pytest.raises(h.HandshakeError):
        h.verify_capsule_receipt(receipt, expected_capsule_hash=other["capsule_hash"])


def test_expected_receiver_mismatch_rejected():
    capsule = _capsule()
    with _identity():
        receipt = h.build_capsule_receipt(capsule)
    with pytest.raises(h.HandshakeError):
        h.verify_capsule_receipt(receipt, expected_receiver_id="00" * 32)


def test_unknown_status_is_refused_at_build():
    capsule = _capsule()
    with _identity(), pytest.raises(h.HandshakeError):
        h.build_capsule_receipt(capsule, status="maybe")
