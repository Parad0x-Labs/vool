from __future__ import annotations

import unittest
import uuid
from unittest import mock

from core.credit_ledger import (
    award_credits,
    burn_credits,
    ensure_starter_credits,
    get_credit_balance,
    get_free_tier_dispatch_usage,
    reserve_swarm_dispatch_budget,
)
from storage.db import get_connection
from storage.migrations import run_migrations


class CreditLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM compute_credit_ledger")
            conn.execute("DELETE FROM swarm_dispatch_budget_events")
            conn.commit()
        finally:
            conn.close()

    def test_replay_receipt_is_rejected(self) -> None:
        peer_id = "peer-ledger-a"
        receipt_id = f"receipt-{uuid.uuid4().hex}"
        first = award_credits(peer_id, 10.0, "test_award", receipt_id=receipt_id)
        second = award_credits(peer_id, 10.0, "test_award", receipt_id=receipt_id)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_negative_balance_is_prevented(self) -> None:
        peer_id = "peer-ledger-b"
        self.assertFalse(burn_credits(peer_id, 999.0, "overdraw", receipt_id="burn-overdraw"))
        self.assertGreaterEqual(get_credit_balance(peer_id), 0.0)

    def test_dispatch_budget_uses_paid_credits_when_available(self) -> None:
        peer_id = "peer-ledger-paid"
        award_credits(peer_id, 15.0, "seed", receipt_id=f"seed-{uuid.uuid4().hex}")
        reservation = reserve_swarm_dispatch_budget(peer_id, 10.0, receipt_id=f"dispatch-{uuid.uuid4().hex}")
        self.assertTrue(reservation.allowed)
        self.assertEqual(reservation.mode, "paid")
        self.assertAlmostEqual(get_credit_balance(peer_id), 5.0)
        self.assertAlmostEqual(get_free_tier_dispatch_usage(peer_id), 0.0)

    def test_dispatch_budget_falls_back_to_free_tier_within_cap(self) -> None:
        peer_id = "peer-ledger-free"
        with mock.patch("core.credit_ledger.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.free_tier_daily_swarm_points": 10.0,
                "economics.free_tier_max_dispatch_points": 6.0,
            }.get(path, default)
            reservation = reserve_swarm_dispatch_budget(peer_id, 4.0, receipt_id=f"dispatch-{uuid.uuid4().hex}")
        self.assertTrue(reservation.allowed)
        self.assertEqual(reservation.mode, "free_tier")
        self.assertAlmostEqual(get_free_tier_dispatch_usage(peer_id), 4.0)

    def test_dispatch_budget_blocks_when_daily_cap_is_exhausted(self) -> None:
        peer_id = "peer-ledger-cap"
        with mock.patch("core.credit_ledger.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.free_tier_daily_swarm_points": 5.0,
                "economics.free_tier_max_dispatch_points": 5.0,
            }.get(path, default)
            first = reserve_swarm_dispatch_budget(peer_id, 5.0, receipt_id=f"dispatch-{uuid.uuid4().hex}")
            second = reserve_swarm_dispatch_budget(peer_id, 1.0, receipt_id=f"dispatch-{uuid.uuid4().hex}")
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason, "daily_free_tier_budget_exhausted")

    def test_starter_credits_seed_once(self) -> None:
        peer_id = "peer-ledger-starter"
        with mock.patch("core.credit_ledger.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.starter_credits_enabled": True,
                "economics.starter_credits_amount": 9.0,
            }.get(path, default)
            self.assertTrue(ensure_starter_credits(peer_id))
            self.assertFalse(ensure_starter_credits(peer_id))
        self.assertAlmostEqual(get_credit_balance(peer_id), 9.0)


if __name__ == "__main__":
    unittest.main()
