"""The simulated DNA hot/cold custody manager after its mutators were retired.

`configure_wallets`, `top_up_hot_from_cold`, `move_hot_to_cold`, `deposit_hot` and
`consume_hot_for_credit_purchase` are typed, receipt-backed refusals (core.wallet is the one
custody authority). `get_status` stays read-only. The former round-trip / top-up / move tests
are gone with the mutations they exercised; what is pinned instead is that every mutator refuses
BEFORE touching the tables, that the refusal carries a fault receipt, and that the read-only
status of an untouched profile is None.
"""
from __future__ import annotations

import unittest

from core.dna_wallet_manager import DNAWalletManager
from core.faults.recorder import list_faults
from core.wallet.errors import WalletFault
from storage.db import get_connection
from storage.migrations import run_migrations

LEGACY = "wallet_legacy_surface_retired"
_TABLES = ("dna_wallet_ledger", "dna_wallet_security", "dna_wallet_profiles")


def _row_counts() -> dict[str, int]:
    conn = get_connection()
    try:
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in _TABLES}
    finally:
        conn.close()


class DNAWalletManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in _TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()
        self.manager = DNAWalletManager()

    def _assert_refused(self, fn, *args, **kwargs) -> WalletFault:
        before = _row_counts()
        with self.assertRaises(WalletFault) as ctx:
            fn(*args, **kwargs)
        fault = ctx.exception
        self.assertEqual(fault.code, LEGACY)
        self.assertTrue(fault.fault_id.startswith("fault-"), fault.fault_id)
        self.assertTrue(fault.user_message)
        self.assertTrue(str(fault.context.get("surface", "")).startswith("dna_wallet_manager."), fault.context)
        self.assertEqual(_row_counts(), before, "a retired custody mutator touched the tables")
        receipts = [r for r in list_faults(code=LEGACY, limit=50) if r.fault_id == fault.fault_id]
        self.assertTrue(receipts, "the refusal left no fault receipt")
        return fault

    def test_status_is_read_only_and_none_for_an_unconfigured_profile(self) -> None:
        self.assertIsNone(self.manager.get_status())
        self.assertFalse(self.manager.hot_wallet_ready())
        self.assertEqual(_row_counts(), {table: 0 for table in _TABLES})

    def test_configure_wallets_refuses_and_writes_nothing(self) -> None:
        fault = self._assert_refused(
            self.manager.configure_wallets,
            hot_wallet_address="hot_wallet_address_12345678901234567890",
            cold_wallet_address="cold_wallet_address_123456789012345678",
            cold_secret="correct horse battery staple",
            initial_hot_usdc=1.25,
            initial_cold_usdc=9.75,
        )
        self.assertEqual(fault.context["surface"], "dna_wallet_manager.configure_wallets")
        self.assertIsNone(self.manager.get_status())
        # the secret never reaches the receipt
        self.assertNotIn("correct horse battery staple", str(fault.to_dict()))

    def test_topup_refuses_regardless_of_the_cold_secret(self) -> None:
        for secret in ("wrong-secret", "secret-1234", ""):
            self._assert_refused(self.manager.top_up_hot_from_cold, 1.0, cold_secret=secret)
        self.assertIsNone(self.manager.get_status())

    def test_move_to_cold_refuses(self) -> None:
        self._assert_refused(self.manager.move_hot_to_cold, 1.5, cold_secret="secret-1234")

    def test_deposit_hot_refuses(self) -> None:
        fault = self._assert_refused(self.manager.deposit_hot, 2.0, initiated_by="user", reference_id="ref-1")
        self.assertEqual(fault.context["surface"], "dna_wallet_manager.deposit_hot")

    def test_consume_for_credit_purchase_refuses(self) -> None:
        self._assert_refused(
            self.manager.consume_hot_for_credit_purchase,
            1.0,
            local_peer_id="peer-1",
            reference_id="sim-tx-1",
            initiated_by="agent",
        )

    def test_refusal_is_the_same_on_a_profile_that_was_seeded_directly(self) -> None:
        # A row planted straight into the table (outside the retired mutator) is readable but still
        # cannot be moved: the mutators refuse before looking at it, and the balances do not change.
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO dna_wallet_profiles (profile_id, hot_wallet_address, cold_wallet_address, hot_balance_usdc, cold_balance_usdc, hot_auto_spend_enabled, created_at, updated_at)"
                " VALUES ('default', 'hot_wallet_address_12345678901234567890', 'cold_wallet_address_123456789012345678', 1.0, 4.0, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
            )
            conn.commit()
        finally:
            conn.close()
        status = self.manager.get_status()
        self.assertIsNotNone(status)
        self.assertEqual(status.hot_wallet_address, "hot_wallet_address_12345678901234567890")
        self._assert_refused(self.manager.top_up_hot_from_cold, 2.0, cold_secret="secret-1234")
        self._assert_refused(self.manager.move_hot_to_cold, 0.5, cold_secret="secret-1234")
        after = self.manager.get_status()
        self.assertAlmostEqual(after.hot_balance_usdc, 1.0)
        self.assertAlmostEqual(after.cold_balance_usdc, 4.0)


if __name__ == "__main__":
    unittest.main()
