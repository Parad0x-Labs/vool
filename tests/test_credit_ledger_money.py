"""Money-math correctness for the (simulated) compute-credit ledger.

These guard three value-moving paths in ``core.credit_ledger``:

  (1) multi-helper escrow settlement allocates the whole payout pool — the
      per-helper amounts sum to the pool exactly, with the integer-division
      remainder distributed deterministically rather than dropped;
  (2) ``usdc_to_atomic`` rounds (not truncates) to atomic units and rejects
      amounts that round to 0 atomic units (no silent zero-value transfer);
  (3) ``_dispatch_receipt_record`` matches a prior dispatch on sign + reason, so
      a positive award/refund row sharing a receipt_id is not mis-selected as a
      prior paid dispatch.
"""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

from core import credit_ledger
from core.credit_ledger import (
    USDC_ATOMIC_PER_UNIT,
    _allocate_pool_atomic,
    _dispatch_receipt_record,
    award_credits,
    escrow_credits_for_task,
    get_credit_balance,
    get_escrow_for_task,
    settle_hive_task_escrow,
    transfer_credits,
    usdc_to_atomic,
)
from storage.db import get_connection
from storage.migrations import run_migrations


def _seed(peer_id: str, amount: float) -> None:
    award_credits(peer_id, amount, "seed", receipt_id=f"seed-{uuid.uuid4().hex}")


class CreditLedgerMoneyTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "compute_credit_ledger",
                "swarm_dispatch_budget_events",
                "dispatch_credit_escrow",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    # ---- (1) multi-helper escrow allocation sums to the pool exactly ----------

    def test_allocate_pool_atomic_sums_exactly_and_distributes_remainder(self) -> None:
        # 100 ticks across 3 -> 34, 33, 33 (sum == 100, remainder of 1 handed out)
        allocations = _allocate_pool_atomic(100, 3)
        self.assertEqual(sum(allocations), 100)
        self.assertEqual(allocations, [34, 33, 33])
        # leading recipients absorb the remainder; spread is at most one unit
        self.assertLessEqual(max(allocations) - min(allocations), 1)

    def test_allocate_pool_atomic_edge_cases(self) -> None:
        self.assertEqual(_allocate_pool_atomic(0, 4), [0, 0, 0, 0])
        self.assertEqual(_allocate_pool_atomic(10, 0), [])
        self.assertEqual(sum(_allocate_pool_atomic(7, 7)), 7)

    def test_settlement_allocations_sum_to_pool_with_remainder_handled(self) -> None:
        # 10.0 escrow split across 3 helpers does NOT divide evenly: a naive
        # round(10/3, 4) == 3.3333 per helper drops/overshoots. The fix must
        # release the full pool with the remainder assigned, not lost.
        poster = "poster-alloc"
        helpers = ["helper-a", "helper-b", "helper-c"]
        task_id = f"task-{uuid.uuid4().hex}"
        _seed(poster, 10.0)
        self.assertTrue(escrow_credits_for_task(poster, task_id, 10.0))

        result = settle_hive_task_escrow(task_id, helpers, result_status="solved")
        self.assertTrue(result["ok"])

        amounts = [s["amount"] for s in result["settlements"]]
        self.assertEqual(len(amounts), 3)
        # The per-helper allocations sum to the full pool exactly.
        self.assertAlmostEqual(sum(amounts), 10.0, places=4)
        # Each helper's balance equals their allocation (full settlement).
        for helper, amount in zip(helpers, amounts, strict=True):
            self.assertAlmostEqual(get_credit_balance(helper), amount, places=4)
        # No value escapes: released sum + refund == escrowed, escrow fully drained.
        self.assertAlmostEqual(
            result["released_amount"] + result["refunded_amount"], 10.0, places=4
        )
        escrow_after = get_escrow_for_task(task_id)
        self.assertAlmostEqual(float(escrow_after["remaining"]), 0.0, places=4)

    def test_partial_settlement_remainder_refunded_to_poster(self) -> None:
        # 'partial' pays out 50% of the pool; the remaining 50% must be refundable
        # and the helper allocations of the paid pool must sum exactly.
        poster = "poster-partial"
        helpers = ["ph-a", "ph-b", "ph-c"]
        task_id = f"task-{uuid.uuid4().hex}"
        _seed(poster, 10.0)
        escrow_credits_for_task(poster, task_id, 10.0)

        result = settle_hive_task_escrow(task_id, helpers, result_status="partial")
        self.assertTrue(result["ok"])
        released = sum(s["amount"] for s in result["settlements"])
        # 50% of 10.0 == 5.0 paid out across helpers, summing exactly.
        self.assertAlmostEqual(released, 5.0, places=4)
        self.assertAlmostEqual(result["released_amount"], 5.0, places=4)
        # The 5.0 unspent remainder stays in escrow (partial does not auto-refund).
        escrow_after = get_escrow_for_task(task_id)
        self.assertAlmostEqual(float(escrow_after["remaining"]), 5.0, places=4)

    # ---- (2) USDC -> atomic: round, reject sub-atomic --------------------------

    def test_usdc_to_atomic_round_trip_rounding(self) -> None:
        # Exact values round-trip.
        self.assertEqual(usdc_to_atomic(1.0), USDC_ATOMIC_PER_UNIT)
        self.assertEqual(usdc_to_atomic(0.001), 1_000)
        self.assertEqual(usdc_to_atomic(0.000001), 1)
        # Sub-atomic fractions ROUND rather than truncate toward zero.
        # 1.5 atomic units (0.0000015 USDC) rounds to 2, not floors to 1.
        self.assertEqual(usdc_to_atomic(0.0000015), 2)
        # 2.4 atomic units rounds down to 2; 2.6 rounds up to 3.
        self.assertEqual(usdc_to_atomic(0.0000024), 2)
        self.assertEqual(usdc_to_atomic(0.0000026), 3)

    def test_usdc_to_atomic_rejects_sub_atomic_amount(self) -> None:
        # An amount that rounds to 0 atomic units (< half an atomic unit) is a
        # silent zero-value transfer and must be rejected.
        with self.assertRaises(ValueError):
            usdc_to_atomic(0.0000004)  # 0.4 atomic units -> rounds to 0
        with self.assertRaises(ValueError):
            usdc_to_atomic(1e-12)
        with self.assertRaises(ValueError):
            usdc_to_atomic(0.0)
        with self.assertRaises(ValueError):
            usdc_to_atomic(-1.0)

    def test_usdc_to_atomic_rejects_non_finite(self) -> None:
        with self.assertRaises(ValueError):
            usdc_to_atomic(float("nan"))
        with self.assertRaises(ValueError):
            usdc_to_atomic(float("inf"))
        with self.assertRaises(ValueError):
            usdc_to_atomic("not-a-number")  # type: ignore[arg-type]

    # ---- (3) _dispatch_receipt_record matches on sign + reason -----------------

    def test_dispatch_receipt_record_ignores_positive_award_row(self) -> None:
        # A positive (award) row sharing a receipt_id must NOT be reported as a
        # prior PAID dispatch — paid dispatches are charges (negative amount).
        peer = "peer-dispatch-award"
        shared_receipt = f"shared-{uuid.uuid4().hex}"
        self.assertTrue(award_credits(peer, 5.0, "provider_reward", receipt_id=shared_receipt))
        conn = get_connection()
        try:
            self.assertIsNone(_dispatch_receipt_record(conn, shared_receipt))
            self.assertIsNone(
                _dispatch_receipt_record(conn, shared_receipt, reason="task_dispatch")
            )
        finally:
            conn.close()

    def test_dispatch_receipt_record_matches_dispatch_debit_on_reason(self) -> None:
        # A genuine paid-dispatch charge (negative amount + dispatch reason) is
        # matched; a different reason on the same id is not.
        peer = "peer-dispatch-debit"
        receipt = f"dispatch-{uuid.uuid4().hex}"
        now = "2026-06-22T00:00:00Z"
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO compute_credit_ledger
                    (peer_id, amount, reason, receipt_id, settlement_mode, timestamp)
                VALUES (?, ?, ?, ?, 'simulated', ?)
                """,
                (peer, -7.0, "task_dispatch", receipt, now),
            )
            conn.commit()
            matched = _dispatch_receipt_record(conn, receipt, reason="task_dispatch")
            self.assertIsNotNone(matched)
            assert matched is not None
            mode, amount = matched
            self.assertEqual(mode, "paid")
            self.assertAlmostEqual(amount, 7.0, places=4)
            # A mismatched reason does not select the row.
            self.assertIsNone(
                _dispatch_receipt_record(conn, receipt, reason="some_other_reason")
            )
        finally:
            conn.close()

    # ---- (4) same-second auto-generated ids do not collide ---------------------

    def test_two_same_second_transfers_both_move_funds(self) -> None:
        # ``_utcnow_iso`` only has second granularity. Two transfers between the
        # same pair in the same wall-clock second fall back to the same default
        # receipt id; pre-fix the second was mis-detected as a replay and dropped
        # (returned False, no funds moved). The auto-generated id must be unique
        # so both succeed and both move funds.
        sender = f"sender-{uuid.uuid4().hex}"
        receiver = f"receiver-{uuid.uuid4().hex}"
        _seed(sender, 10.0)

        frozen_second = "2026-06-22T12:00:00Z"
        with mock.patch.object(credit_ledger, "_utcnow_iso", return_value=frozen_second):
            first = transfer_credits(sender, receiver, 3.0, reason="peer_transfer")
            second = transfer_credits(sender, receiver, 4.0, reason="peer_transfer")

        self.assertTrue(first)
        self.assertTrue(second)
        # Both debits and both credits landed: sender 10 - 3 - 4, receiver 3 + 4.
        self.assertAlmostEqual(get_credit_balance(sender), 3.0, places=4)
        self.assertAlmostEqual(get_credit_balance(receiver), 7.0, places=4)

    def test_explicit_transfer_receipt_id_is_still_idempotent(self) -> None:
        # A caller-supplied receipt_id remains an idempotency key: the same id
        # replayed moves funds exactly once.
        sender = f"sender-{uuid.uuid4().hex}"
        receiver = f"receiver-{uuid.uuid4().hex}"
        _seed(sender, 10.0)
        explicit = f"transfer-{uuid.uuid4().hex}"

        self.assertTrue(transfer_credits(sender, receiver, 3.0, receipt_id=explicit))
        self.assertFalse(transfer_credits(sender, receiver, 3.0, receipt_id=explicit))
        self.assertAlmostEqual(get_credit_balance(sender), 7.0, places=4)
        self.assertAlmostEqual(get_credit_balance(receiver), 3.0, places=4)

    def test_two_same_second_escrows_same_task_both_move_funds(self) -> None:
        # Two escrow reservations for the same parent_task_id in the same second
        # fell back to the same ``escrow:{task}`` id. Pre-fix the second matched
        # ``_receipt_exists`` and returned True WITHOUT moving funds (a value-
        # losing false success). The auto-generated id must be unique so each
        # reservation actually debits the poster.
        poster = f"poster-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"
        _seed(poster, 10.0)

        frozen_second = "2026-06-22T12:00:00Z"
        with mock.patch.object(credit_ledger, "_utcnow_iso", return_value=frozen_second):
            first = escrow_credits_for_task(poster, task_id, 3.0)
            second = escrow_credits_for_task(poster, task_id, 4.0)

        self.assertTrue(first)
        self.assertTrue(second)
        # Both holds debited the poster: 10 - 3 - 4 == 3.
        self.assertAlmostEqual(get_credit_balance(poster), 3.0, places=4)

    def test_explicit_escrow_receipt_id_replay_is_idempotent_success(self) -> None:
        # An explicit receipt_id replay is a genuine no-op success (True) and
        # debits the poster exactly once.
        poster = f"poster-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"
        _seed(poster, 10.0)
        explicit = f"escrow-{uuid.uuid4().hex}"

        self.assertTrue(escrow_credits_for_task(poster, task_id, 3.0, receipt_id=explicit))
        self.assertTrue(escrow_credits_for_task(poster, task_id, 3.0, receipt_id=explicit))
        self.assertAlmostEqual(get_credit_balance(poster), 7.0, places=4)


if __name__ == "__main__":
    unittest.main()
