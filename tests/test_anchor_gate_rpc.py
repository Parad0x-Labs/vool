from __future__ import annotations

import unittest
from unittest import mock

from core.solana_anchor import anchor_enabled
from core.x402.client import SOLANA_RPC_MAINNET, X402Config, X402Mode


class MainnetRpcGuardrailTests(unittest.TestCase):
    def test_mainnet_constant_is_publicnode_not_banned(self) -> None:
        self.assertNotIn("api.mainnet-beta", SOLANA_RPC_MAINNET)
        self.assertIn("publicnode", SOLANA_RPC_MAINNET)

    def test_effective_rpc_mainnet_is_compliant(self) -> None:
        rpc = X402Config(mode=X402Mode.MAINNET).effective_rpc
        self.assertNotIn("api.mainnet-beta", rpc)
        self.assertEqual(rpc, "https://solana-rpc.publicnode.com")

    def test_explicit_rpc_override_still_wins(self) -> None:
        rpc = X402Config(mode=X402Mode.MAINNET, rpc_url="https://my.rpc").effective_rpc
        self.assertEqual(rpc, "https://my.rpc")


class AnchorGateTests(unittest.TestCase):
    def test_enabled_only_when_env_is_exactly_1(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}):
            self.assertTrue(anchor_enabled())
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "0"}):
            self.assertFalse(anchor_enabled())
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "true"}):
            self.assertFalse(anchor_enabled())  # only "1" opts in

    def test_disabled_by_default(self) -> None:
        env = {k: v for k, v in __import__("os").environ.items() if k != "VOOL_ANCHOR_RECEIPTS"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertFalse(anchor_enabled())


if __name__ == "__main__":
    unittest.main()
