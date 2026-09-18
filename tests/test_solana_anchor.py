from __future__ import annotations

import unittest

import core.solana_anchor as anchor
from core.vool_wallet import b58decode

_HAS_SOLDERS = anchor.Pubkey is not None
# A valid 32-byte base58 string usable as both a pubkey and a blockhash in tests.
_ZERO_KEY = "11111111111111111111111111111111"
_MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


@unittest.skipUnless(_HAS_SOLDERS, "solders required to build the anchor message")
class MemoAnchorMessageTests(unittest.TestCase):
    def test_message_carries_the_receipt_hash(self) -> None:
        payload = "a44934b41fe230e8e41c4deb49f28cb5"
        msg = anchor.build_memo_anchor_message(_ZERO_KEY, payload, _ZERO_KEY)
        self.assertIsInstance(msg, bytes)
        self.assertGreater(len(msg), 0)
        # the memo data (tag + hash) is embedded verbatim in the serialized message
        self.assertIn(b"vool-receipt:" + payload.encode(), msg)

    def test_message_includes_memo_program_account(self) -> None:
        msg = anchor.build_memo_anchor_message(_ZERO_KEY, "deadbeef", _ZERO_KEY)
        # the memo program's 32-byte key appears among the account keys
        self.assertIn(b58decode(_MEMO_PROGRAM_ID), msg)

    def test_roundtrips_through_solders(self) -> None:
        from solders.message import Message
        msg = anchor.build_memo_anchor_message(_ZERO_KEY, "cafe", _ZERO_KEY)
        parsed = Message.from_bytes(msg)
        # fee payer is the first account key (the sole signer)
        self.assertEqual(str(parsed.account_keys[0]), _ZERO_KEY)
        self.assertIn(_MEMO_PROGRAM_ID, [str(k) for k in parsed.account_keys])


class AnchorDegradationTests(unittest.TestCase):
    def test_builder_raises_without_solders(self) -> None:
        if _HAS_SOLDERS:
            self.skipTest("solders present")
        with self.assertRaises(RuntimeError):
            anchor.build_memo_anchor_message(_ZERO_KEY, "x", _ZERO_KEY)


if __name__ == "__main__":
    unittest.main()
