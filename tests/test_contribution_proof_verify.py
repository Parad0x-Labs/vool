"""verify_contribution_proof_chain + fail-closed list_contribution_proof_receipts.

The append path hash-chains receipts, but nothing re-derived those hashes on read, so a
forged points/credits edit surfaced as if genuine. These lock in that the chain verifier
recomputes hashes + follows links, and that the listing drops a tampered row.
"""
from __future__ import annotations

import uuid

from core.contribution_proof import (
    append_contribution_proof_receipt,
    list_contribution_proof_receipts,
    verify_contribution_proof_chain,
)
from storage.db import get_connection
from storage.migrations import run_migrations


def _seed_ledger_entry(db_path: str, entry_id: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """
            INSERT INTO contribution_ledger (
                entry_id, task_id, helper_peer_id, parent_peer_id, contribution_type,
                outcome, created_at, updated_at
            ) VALUES (?, 'task-1', 'helper-1', 'parent-1', 'reasoning', 'accepted',
                      '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """,
            (entry_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _append(db_path: str, entry_id: str, stage: str, points: int) -> dict:
    return append_contribution_proof_receipt(
        entry_id=entry_id,
        task_id="task-1",
        helper_peer_id="helper-1",
        parent_peer_id="parent-1",
        stage=stage,
        outcome="accepted",
        points_awarded=points,
        compute_credits=1.5,
        db_path=db_path,
    )


def test_intact_chain_verifies_and_lists(tmp_path) -> None:
    db_path = str(tmp_path / "proof.db")
    run_migrations(db_path)
    entry = f"entry-{uuid.uuid4().hex}"
    _seed_ledger_entry(db_path, entry)

    _append(db_path, entry, "pending", 3)
    _append(db_path, entry, "confirmed", 5)
    _append(db_path, entry, "finalized", 5)

    verdict = verify_contribution_proof_chain(entry, db_path=db_path)
    assert verdict["ok"] is True
    assert verdict["checked"] == 3
    assert verdict["tampered_receipt_ids"] == []
    assert verdict["broken_link_receipt_ids"] == []

    listed = list_contribution_proof_receipts(entry_id=entry, db_path=db_path)
    assert len(listed) == 3
    assert all(item["verified"] is True for item in listed)


def test_equal_timestamps_keep_insertion_order_and_chain_tail(tmp_path) -> None:
    db_path = str(tmp_path / "proof.db")
    run_migrations(db_path)
    entry = f"entry-{uuid.uuid4().hex}"
    _seed_ledger_entry(db_path, entry)
    created_at = "2026-01-01T00:00:00+00:00"

    first = append_contribution_proof_receipt(
        entry_id=entry,
        task_id="task-1",
        helper_peer_id="helper-1",
        stage="confirmed",
        created_at=created_at,
        db_path=db_path,
    )
    second = append_contribution_proof_receipt(
        entry_id=entry,
        task_id="task-1",
        helper_peer_id="helper-1",
        stage="slashed",
        created_at=created_at,
        db_path=db_path,
    )

    assert second["previous_receipt_id"] == first["receipt_id"]
    listed = list_contribution_proof_receipts(entry_id=entry, db_path=db_path)
    assert [item["stage"] for item in listed] == ["slashed", "confirmed"]
    assert verify_contribution_proof_chain(entry, db_path=db_path)["ok"] is True


def test_tampered_points_column_fails_verification_and_is_dropped(tmp_path) -> None:
    db_path = str(tmp_path / "proof.db")
    run_migrations(db_path)
    entry = f"entry-{uuid.uuid4().hex}"
    _seed_ledger_entry(db_path, entry)

    _append(db_path, entry, "pending", 3)
    target = _append(db_path, entry, "confirmed", 5)

    # Forge the award on the column without touching payload_json / receipt_hash.
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE contribution_proof_receipts SET points_awarded = 999 WHERE receipt_id = ?",
            (target["receipt_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    verdict = verify_contribution_proof_chain(entry, db_path=db_path)
    assert verdict["ok"] is False
    assert target["receipt_id"] in verdict["tampered_receipt_ids"]

    # The listing fails closed: the forged row is dropped, the intact one remains.
    listed = list_contribution_proof_receipts(entry_id=entry, db_path=db_path)
    ids = {item["receipt_id"] for item in listed}
    assert target["receipt_id"] not in ids
    assert len(listed) == 1


def test_broken_link_is_flagged(tmp_path) -> None:
    db_path = str(tmp_path / "proof.db")
    run_migrations(db_path)
    entry = f"entry-{uuid.uuid4().hex}"
    _seed_ledger_entry(db_path, entry)

    _append(db_path, entry, "pending", 3)
    second = _append(db_path, entry, "confirmed", 5)

    # Point the link at a hash no receipt has. (This also breaks the row's own
    # reconstruction, so it is flagged as tampered too — the link break is the assertion.)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE contribution_proof_receipts SET previous_receipt_hash = 'deadbeef' WHERE receipt_id = ?",
            (second["receipt_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    verdict = verify_contribution_proof_chain(entry, db_path=db_path)
    assert verdict["ok"] is False
    assert second["receipt_id"] in verdict["broken_link_receipt_ids"]
