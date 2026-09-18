"""Regression: escrow release/refund must touch only the SELECTED escrow row.

A parent_task_id can carry 2+ active escrow rows (escrow_credits_for_task ->
escrow:{task} and reserve_swarm_dispatch_budget -> escrow:{reason} both stamp the
same parent_task_id). The release/refund SELECTs one row (LIMIT 1) to size the
payout but historically UPDATEd `WHERE parent_task_id=? AND status='active'`,
mutating EVERY active row — silently losing credits from the closed-loop ledger.
"""
from __future__ import annotations

import unittest

from core.credit_ledger import (
    refund_escrow_remainder,
    release_escrow_to_helper,
)
from storage.db import get_connection
from storage.migrations import run_migrations

_TS = "2026-06-22T00:00:00Z"


def _insert_escrow(escrow_id: str, parent_task_id: str, escrowed: float) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO dispatch_credit_escrow
               (escrow_id, parent_task_id, poster_peer_id, total_escrowed,
                total_released, total_refunded, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 0, 0, 'active', ?, ?)""",
            (escrow_id, parent_task_id, "poster", escrowed, _TS, _TS),
        )
        conn.commit()
    finally:
        conn.close()


def _rows(parent_task_id: str):
    conn = get_connection()
    try:
        return {
            r["escrow_id"]: (float(r["total_released"]), float(r["total_refunded"]), r["status"])
            for r in conn.execute(
                "SELECT escrow_id, total_released, total_refunded, status "
                "FROM dispatch_credit_escrow WHERE parent_task_id = ?",
                (parent_task_id,),
            ).fetchall()
        }
    finally:
        conn.close()


class EscrowRowScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM dispatch_credit_escrow")
            conn.execute("DELETE FROM compute_credit_ledger")
            conn.commit()
        finally:
            conn.close()

    def test_release_touches_only_one_escrow_row(self) -> None:
        _insert_escrow("escrow:taskX", "taskX", 10.0)
        _insert_escrow("escrow:dispatchY", "taskX", 20.0)
        self.assertTrue(release_escrow_to_helper("taskX", "helper", 5.0))
        rows = _rows("taskX")
        released = sorted(r[0] for r in rows.values())
        # exactly one row released 5; the other untouched — NOT both (which would
        # over-release 10 against a 5 payout and corrupt the ledger).
        self.assertEqual(released, [0.0, 5.0])

    def test_refund_settles_only_one_escrow_row(self) -> None:
        _insert_escrow("escrow:taskX", "taskX", 10.0)
        _insert_escrow("escrow:dispatchY", "taskX", 20.0)
        refunded = refund_escrow_remainder("taskX")
        self.assertGreater(refunded, 0.0)
        rows = _rows("taskX")
        statuses = sorted(r[2] for r in rows.values())
        # one row settled, the other still active — the second escrow is NOT
        # closed-and-zeroed without being paid out.
        self.assertEqual(statuses, ["active", "settled"])


if __name__ == "__main__":
    unittest.main()
