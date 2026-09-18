"""The mesh proof bundle is Ed25519-signed per entry.

export_proof_bundle previously carried only a per-entry SHA-256 (internal consistency),
so a forger could re-hash arbitrary fields. Each entry is now signed with the node's key,
and verify_proof_bundle confirms both the hash and the signature — a re-hashed forgery from
another key no longer passes. Signatures must survive JSON transport (the bundle is shared).
"""
from __future__ import annotations

import copy
import json

from core.mesh.credit_ledger import CreditLedger, verify_proof_bundle
from storage.db import get_connection

NODE = "bundle-sign-test-node"


def _reset() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM mesh_credit_ledger WHERE node_id = ?", (NODE,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _bundle() -> list[dict]:
    _reset()
    led = CreditLedger(node_id=NODE)
    led.earn("task-1", 5.0, "a" * 64)
    led.spend("task-2", 2.0, "peerX")
    return led.export_proof_bundle()


def test_exported_entries_are_signed() -> None:
    bundle = _bundle()
    assert len(bundle) == 2
    for entry in bundle:
        assert entry["bundle_hash"]
        assert entry["signature"], "entry was not signed"
        assert len(entry["signer_peer_id"]) == 64  # hex Ed25519 public key


def test_genuine_bundle_verifies() -> None:
    v = verify_proof_bundle(_bundle())
    assert v["ok"] is True
    assert v["checked"] == 2
    assert v["tampered"] == [] and v["unsigned"] == []


def test_tampered_amount_is_rejected() -> None:
    forged = copy.deepcopy(_bundle())
    forged[0]["amount"] = 999.0  # change the value without re-signing
    v = verify_proof_bundle(forged)
    assert v["ok"] is False
    assert forged[0]["entry_id"] in v["tampered"]


def test_forged_signature_is_rejected() -> None:
    forged = copy.deepcopy(_bundle())
    forged[0]["signature"] = "AAAA"
    assert verify_proof_bundle(forged)["ok"] is False


def test_signature_survives_json_transport() -> None:
    # The bundle is meant to be shared over the wire; signatures must survive JSON.
    roundtripped = json.loads(json.dumps(_bundle()))
    assert verify_proof_bundle(roundtripped)["ok"] is True


def test_unsigned_entry_is_reported_not_tampered() -> None:
    # An entry exported without a signature (no signer available) is 'unsigned', not
    # 'tampered', and does not by itself fail the bundle — the hash is still checked.
    bundle = copy.deepcopy(_bundle())
    bundle[0]["signature"] = ""
    bundle[0]["signer_peer_id"] = ""
    v = verify_proof_bundle(bundle)
    assert bundle[0]["entry_id"] in v["unsigned"]
    assert bundle[0]["entry_id"] not in v["tampered"]
    assert v["ok"] is True
