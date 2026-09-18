from __future__ import annotations

import unittest
from unittest.mock import patch

from network.chunk_protocol import chunk_payload, decode_frame, encode_frame, manifest_to_dict
from network.transfer_manager import TransferManager


class TransferManagerLimitTests(unittest.TestCase):
    def test_rejects_manifest_that_exceeds_memory_budget(self) -> None:
        def fake_get(key: str, default=None):
            if key == "network.stream.max_incoming_bytes":
                return 64
            return default

        payload = b"x" * 2048
        manifest, _ = chunk_payload("transfer-large", payload, chunk_size=256)

        with patch("network.transfer_manager.policy_engine.get", side_effect=fake_get):
            manager = TransferManager()
            response = manager.receive_frame(encode_frame("manifest", manifest_to_dict(manifest)))

        msg_type, body = decode_frame(response)
        self.assertEqual(msg_type, "error")
        self.assertEqual(body.get("reason"), "manifest_too_large")

    def test_rejects_when_incoming_transfer_count_limit_is_reached(self) -> None:
        def fake_get(key: str, default=None):
            if key == "network.stream.max_incoming_transfers":
                return 1
            if key == "network.stream.max_incoming_bytes":
                return 1024 * 1024
            return default

        first_manifest, _ = chunk_payload("transfer-1", b"a" * 32, chunk_size=16)
        second_manifest, _ = chunk_payload("transfer-2", b"b" * 32, chunk_size=16)

        with patch("network.transfer_manager.policy_engine.get", side_effect=fake_get):
            manager = TransferManager()
            first = manager.receive_frame(encode_frame("manifest", manifest_to_dict(first_manifest)))
            second = manager.receive_frame(encode_frame("manifest", manifest_to_dict(second_manifest)))

        first_type, _ = decode_frame(first)
        second_type, second_body = decode_frame(second)
        self.assertEqual(first_type, "ack")
        self.assertEqual(second_type, "error")
        self.assertEqual(second_body.get("reason"), "incoming_transfer_limit")


if __name__ == "__main__":
    unittest.main()
