from __future__ import annotations

import unittest
import uuid

from storage.db import get_connection
from storage.event_hash_chain import (
    EventConflictError,
    append_hashed_event,
    repair_chain,
    verify_chain,
)
from storage.migrations import run_migrations


class EventHashChainTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_repair_refuses_to_launder_by_default(self) -> None:
        # THE INTEGRITY LAW. Sabotage check: if repair becomes a silent default
        # again, this fails — a self-contained chain that re-signs payloads on
        # demand is a tamper LAUNDERER, not a tamper-evidence store.
        with self.assertRaises(ValueError, msg="repair_chain must refuse the default call"):
            repair_chain()

    def test_repair_chain_fixes_broken_prev_hash_links(self) -> None:
        first = f"evt-{uuid.uuid4().hex}"
        second = f"evt-{uuid.uuid4().hex}"
        append_hashed_event(first, {"kind": "first", "value": 1})
        append_hashed_event(second, {"kind": "second", "value": 2})

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE event_hash_chain
                SET prev_hash = ?, event_hash = ?
                WHERE event_id = ?
                """,
                ("broken-prev", "broken-hash", second),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertFalse(verify_chain())
        # Crash-recovery path: the caller asserts the payloads are intact.
        repaired = repair_chain(payloads_trusted=True)
        self.assertGreaterEqual(repaired, 1)
        self.assertTrue(verify_chain())

    def test_append_hashed_event_is_idempotent_for_same_event_id(self) -> None:
        event_id = f"evt-{uuid.uuid4().hex}"
        first_hash = append_hashed_event(event_id, {"kind": "same", "value": 1})
        second_hash = append_hashed_event(event_id, {"kind": "same", "value": 1})
        self.assertEqual(first_hash, second_hash)

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM event_hash_chain WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(int((row or {"cnt": 0})["cnt"]), 1)

    def test_same_event_id_with_different_payload_is_a_conflict_not_a_replay(self) -> None:
        # A collision must not pass as a redelivery: the visible row and the
        # chain entry cannot be allowed to disagree (measured defect — a
        # duplicate event_id with different content silently returned the OLD
        # hash, and event_log_v2's INSERT OR IGNORE kept the old row too).
        event_id = f"evt-{uuid.uuid4().hex}"
        append_hashed_event(event_id, {"kind": "receipt", "amount": 1})
        with self.assertRaises(EventConflictError):
            append_hashed_event(event_id, {"kind": "receipt", "amount": 2})
        # ...and the stored receipt is untouched by the refused write:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT payload_json FROM event_hash_chain WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(__import__("json").loads(row["payload_json"])["amount"], 1)


if __name__ == "__main__":
    unittest.main()
