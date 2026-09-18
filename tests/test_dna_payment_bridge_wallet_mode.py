"""The simulated DNA payment bridge after `purchase_credits` was retired.

Linking an address is still an authority-free bookkeeping step; buying credits is a typed,
receipt-backed refusal that awards nothing and debits nothing. The former "purchase debits the
hot wallet" test is gone with the simulated settlement it exercised.
"""
from __future__ import annotations

import unittest

from core.credit_ledger import get_credit_balance
from core.dna_payment_bridge import DNAPaymentBridge, dna_bridge
from core.dna_wallet_manager import DNAWalletManager
from core.faults.recorder import list_faults
from core.wallet.errors import WalletFault
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations

LEGACY = "wallet_legacy_surface_retired"


class DNAPaymentBridgeWalletModeTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM dna_wallet_ledger")
            conn.execute("DELETE FROM dna_wallet_security")
            conn.execute("DELETE FROM dna_wallet_profiles")
            conn.execute("DELETE FROM compute_credit_ledger WHERE peer_id = ?", (get_local_peer_id(),))
            conn.commit()
        finally:
            conn.close()

    def _assert_refused(self, fn, *args, surface: str = "dna_payment_bridge.purchase_credits", **kwargs) -> WalletFault:
        peer_id = get_local_peer_id()
        starting_balance = get_credit_balance(peer_id)
        with self.assertRaises(WalletFault) as ctx:
            fn(*args, **kwargs)
        fault = ctx.exception
        self.assertEqual(fault.code, LEGACY)
        self.assertTrue(fault.fault_id.startswith("fault-"), fault.fault_id)
        self.assertEqual(fault.context.get("surface"), surface)
        self.assertEqual(get_credit_balance(peer_id), starting_balance, "a refused purchase awarded credits")
        receipts = [r for r in list_faults(code=LEGACY, limit=50) if r.fault_id == fault.fault_id]
        self.assertTrue(receipts, "the refusal left no fault receipt")
        return fault

    def test_link_wallet_validates_and_records_an_address_only(self) -> None:
        bridge = DNAPaymentBridge()
        self.assertFalse(bridge.link_wallet("too-short"))
        self.assertTrue(bridge.link_wallet("solana_wallet_address_for_bridge_123456789"))
        self.assertTrue(bridge.bridge_active)
        self.assertIsNone(DNAWalletManager().get_status())  # linking touches no custody row

    def test_purchase_credits_refuses_with_a_linked_wallet(self) -> None:
        bridge = DNAPaymentBridge()
        bridge.link_wallet("solana_wallet_address_for_bridge_123456789")
        self._assert_refused(bridge.purchase_credits, 1.0, local_peer_id=get_local_peer_id())
        self.assertIsNone(DNAWalletManager().get_status())

    def test_purchase_credits_refuses_without_a_linked_wallet(self) -> None:
        bridge = DNAPaymentBridge()
        bridge.bridge_active = False
        self._assert_refused(bridge.purchase_credits, 1.0, local_peer_id=get_local_peer_id())

    def test_purchase_credits_refuses_for_every_amount(self) -> None:
        bridge = DNAPaymentBridge()
        bridge.link_wallet("solana_wallet_address_for_bridge_123456789")
        for amount in (0.0, 0.05, 1.0, 10_000.0, -1.0):
            self._assert_refused(bridge.purchase_credits, amount, local_peer_id=get_local_peer_id())

    def test_purchase_from_dex_refuses(self) -> None:
        bridge = DNAPaymentBridge()
        bridge.link_wallet("solana_wallet_address_for_bridge_123456789")
        self._assert_refused(
            bridge.purchase_credits_from_dex, 1000, local_peer_id=get_local_peer_id(),
            surface="dna_payment_bridge.purchase_credits_from_dex",
        )

    def test_module_singleton_refuses_too(self) -> None:
        self._assert_refused(dna_bridge.purchase_credits, 1.0, local_peer_id=get_local_peer_id())


if __name__ == "__main__":
    unittest.main()
