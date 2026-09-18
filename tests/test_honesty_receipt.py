"""Signed Honesty Receipts: issue -> verify -> tamper-detect -> chain, all offline."""
from __future__ import annotations

from core.honesty_receipt import (
    VERDICT_BLOCKED,
    VERDICT_CLEAN,
    HonestyReceipt,
    issue_honesty_receipt,
    latest_receipt_hash,
    list_honesty_receipts,
    record_honesty_receipt,
    verify_honesty_chain,
    verify_honesty_receipt,
)


def _clean_receipt(**kw) -> HonestyReceipt:
    base = dict(
        session_id="sess-1",
        turn_index=0,
        prompt_text="write hello.txt to my Desktop",
        response_text="Done — created ~/Desktop/hello.txt (3 lines).",
        claimed_actions=["file_write"],
        executed_tools=[{"tool": "workspace.write_file", "status": "executed", "receipt_key": "rk-1"}],
        verdict=VERDICT_CLEAN,
        verdict_detail="claim backed by executed tool receipt",
    )
    base.update(kw)
    return issue_honesty_receipt(**base)


def test_signed_receipt_verifies_offline() -> None:
    r = _clean_receipt()
    assert r.signer_peer_id and r.signature, "receipt should be Ed25519-signed by the local key"
    ok, reason = verify_honesty_receipt(r)
    assert ok, reason
    # Verifies from a plain dict too (the on-disk / shared form), no object needed.
    ok2, _ = verify_honesty_receipt(r.to_dict())
    assert ok2


def test_tampering_any_field_breaks_verification() -> None:
    r = _clean_receipt().to_dict()
    ok, _ = verify_honesty_receipt(r)
    assert ok
    for field, bad in (
        ("verdict", VERDICT_BLOCKED),          # flip a caught lie into "clean"
        ("response_hash", "0" * 64),
        ("claimed_actions", []),               # hide a claim
        ("executed_tools", []),                # hide/forge execution ground truth
    ):
        tampered = dict(r)
        tampered[field] = bad
        ok_t, reason = verify_honesty_receipt(tampered)
        assert not ok_t, f"tampering {field} must fail verification (got {reason})"


def test_signature_swap_is_rejected() -> None:
    a = _clean_receipt(session_id="s-a").to_dict()
    b = _clean_receipt(session_id="s-b").to_dict()
    forged = dict(a)
    forged["signature"] = b["signature"]  # graft another receipt's signature
    ok, reason = verify_honesty_receipt(forged)
    assert not ok and reason in {"bad_signature", "content_hash_mismatch (tampered or malformed)"}


def test_hash_chain_detects_reorder_or_deletion() -> None:
    r0 = _clean_receipt(session_id="chain", turn_index=0, prev_hash="")
    r1 = _clean_receipt(session_id="chain", turn_index=1, prev_hash=r0.content_hash)
    r2 = _clean_receipt(session_id="chain", turn_index=2, prev_hash=r1.content_hash)
    chain = [r0.to_dict(), r1.to_dict(), r2.to_dict()]
    ok, reason = verify_honesty_chain(chain)
    assert ok, reason
    # Delete the middle receipt -> chain link breaks.
    ok_del, _ = verify_honesty_chain([r0.to_dict(), r2.to_dict()])
    assert not ok_del
    # Reorder -> chain link breaks.
    ok_ro, _ = verify_honesty_chain([r1.to_dict(), r0.to_dict(), r2.to_dict()])
    assert not ok_ro


def test_blocked_false_claim_is_recorded_verifiably() -> None:
    # The model claimed a wallet action but nothing executed -> caught + blocked, and the
    # receipt records that verdict tamper-evidently.
    r = issue_honesty_receipt(
        session_id="lie",
        turn_index=0,
        response_text="I sent 5 SOL to your friend.",
        claimed_actions=["funds_sent"],
        executed_tools=[],  # ground truth: nothing ran
        verdict=VERDICT_BLOCKED,
        verdict_detail="claimed funds_sent with no execution receipt",
    )
    ok, _ = verify_honesty_receipt(r)
    assert ok
    assert r.verdict == VERDICT_BLOCKED
    assert r.executed_tools == []


def test_ledger_roundtrip_and_chain(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    sess = "ledger-sess"
    assert list_honesty_receipts(sess) == []
    prev = ""
    for i in range(3):
        r = _clean_receipt(session_id=sess, turn_index=i, prev_hash=prev)
        record_honesty_receipt(r)
        prev = r.content_hash
    stored = list_honesty_receipts(sess)
    assert len(stored) == 3
    assert latest_receipt_hash(sess) == prev
    ok, reason = verify_honesty_chain(stored)
    assert ok, reason
