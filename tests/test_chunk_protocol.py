from __future__ import annotations

import unittest

from network.chunk_protocol import chunk_payload, reassemble_chunks


class ChunkProtocolTests(unittest.TestCase):
    def test_reassembles_out_of_order_chunks(self) -> None:
        payload = b"hello-world" * 128
        manifest, chunks = chunk_payload("transfer-a", payload, chunk_size=32)
        reordered = list(reversed(chunks))
        self.assertEqual(reassemble_chunks(manifest, reordered), payload)

    def test_packet_loss_is_detected(self) -> None:
        payload = b"hello-world" * 64
        manifest, chunks = chunk_payload("transfer-b", payload, chunk_size=32)
        with self.assertRaises(ValueError):
            reassemble_chunks(manifest, chunks[:-1])


if __name__ == "__main__":
    unittest.main()
