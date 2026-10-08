"""Truthful pack/restore receipts (F14 repair).

Confirmed at e821457d: ``pack_bytes_artifact`` labelled the Python zstandard
backend ``liquefy`` — a backend name is not engine provenance — and reported
``compression_level: max(1, level)``, the REQUESTED level, while the codec
received a clamped value (zstd ≤19, gzip ≤9). A receipt that misstates the
engine, codec or effective level misdescribes every archive built under it.
"""
from __future__ import annotations

import gzip
import hashlib
import unittest
from unittest import mock

from core import liquefy_bridge
from core.liquefy_bridge import load_packed_bytes, pack_bytes_artifact


def _payload(n: int = 4096) -> bytes:
    body = b"cold-archive corpus: ferry log rows with timestamps and ports\n"
    return (body * (n // len(body) + 1))[:n]


class ReceiptTruthTests(unittest.TestCase):
    def test_receipt_reports_effective_level_not_the_request(self) -> None:
        # 0 is falsy and means "unset" (the `or` default), so it resolves to the
        # archive default 12 — which the receipt must record; 99/-7 clamp.
        for requested, expected in ((99, 19), (0, 12), (-7, 1), (12, 12)):
            with self.subTest(requested=requested):
                packed = pack_bytes_artifact(
                    artifact_id=f"level-{requested}",
                    payload=_payload(),
                    compression_level=requested,
                )
                self.assertEqual(int(packed["effective_compression_level"]), expected)
                # the legacy field now carries the same truth: the level the codec used
                self.assertEqual(int(packed["compression_level"]), expected)

    def test_receipt_names_the_real_codec_and_engine(self) -> None:
        packed = pack_bytes_artifact(artifact_id="engine-proof", payload=_payload())
        self.assertEqual(packed["engine"], "python-byte-compression")
        if packed["storage_backend"] == "liquefy":
            self.assertEqual(packed["codec"], "zstd")
            self.assertIn("zstandard", packed["codec_library"])
            self.assertNotIn("cli", packed["engine"])
        else:
            self.assertEqual(packed["codec"], "gzip")
            self.assertEqual(packed["storage_backend"], "local_archive")

    def test_gzip_branch_receipt_clamps_to_nine(self) -> None:
        with mock.patch.object(liquefy_bridge, "_ZSTD_AVAILABLE", False), \
                mock.patch.object(liquefy_bridge, "zstd", None):
            packed = pack_bytes_artifact(
                artifact_id="gzip-branch", payload=_payload(), compression_level=99,
            )
        self.assertEqual(packed["storage_backend"], "local_archive")
        self.assertEqual(packed["codec"], "gzip")
        self.assertEqual(int(packed["effective_compression_level"]), 9)

    def test_round_trip_preserves_bytes_exactly_both_codecs(self) -> None:
        payload = _payload()
        z = pack_bytes_artifact(artifact_id="rt-zstd", payload=payload)
        self.assertEqual(load_packed_bytes(payload=bytes(z["compressed_payload"]), storage_backend=z["storage_backend"]), payload)
        with mock.patch.object(liquefy_bridge, "_ZSTD_AVAILABLE", False), \
                mock.patch.object(liquefy_bridge, "zstd", None):
            g = pack_bytes_artifact(artifact_id="rt-gzip", payload=payload)
        self.assertEqual(load_packed_bytes(payload=bytes(g["compressed_payload"]), storage_backend=g["storage_backend"]), payload)

    def test_hashes_in_the_receipt_describe_the_actual_bytes(self) -> None:
        payload = _payload()
        packed = pack_bytes_artifact(artifact_id="hash-proof", payload=payload)
        self.assertEqual(packed["content_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(
            packed["compressed_sha256"],
            hashlib.sha256(bytes(packed["compressed_payload"])).hexdigest(),
        )

    # -- old-format readability ------------------------------------------------------------

    def test_legacy_backend_labels_still_dispatch(self) -> None:
        """Archives written before this repair carry the old backend vocabulary;
        restore must keep reading them unchanged."""
        payload = _payload()
        gz = gzip.compress(payload, compresslevel=9)
        self.assertEqual(load_packed_bytes(payload=gz, storage_backend="local_archive"), payload)
        self.assertEqual(load_packed_bytes(payload=gz, storage_backend="gzip"), payload)

    def test_missing_codec_restore_is_refused_loudly(self) -> None:
        payload = _payload()
        packed = pack_bytes_artifact(artifact_id="codec-gone", payload=payload)
        if packed["storage_backend"] != "liquefy":
            self.skipTest("zstandard unavailable in this environment")
        with mock.patch.object(liquefy_bridge, "_ZSTD_AVAILABLE", False), \
                mock.patch.object(liquefy_bridge, "zstd", None):
            with self.assertRaises(RuntimeError):
                load_packed_bytes(payload=bytes(packed["compressed_payload"]), storage_backend="liquefy")

    def test_unknown_backend_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            load_packed_bytes(payload=b"x", storage_backend="not-a-backend")

    def test_cold_lookup_does_not_label_rows_by_cli_presence(self) -> None:
        """lookup_cold_archive_candidates described sqlite rows as backend
        'liquefy' whenever ANY liquefy binary existed on PATH — telemetry
        dishonesty. The rows are local DB rows; a CLI being installed changes
        nothing about where those bytes live."""
        conn = liquefy_bridge.get_connection()
        try:
            conn.execute("DELETE FROM finalized_responses")
            conn.execute(
                """
                INSERT INTO finalized_responses (
                    parent_task_id, rendered_persona_text, raw_synthesized_text,
                    status_marker, confidence_score, created_at
                ) VALUES ('task-cold-1', 'plot the greenhouse night curve', '', 'final', 0.9, '2026-09-29T00:00:00')
                """
            )
            conn.commit()
        finally:
            conn.close()
        with mock.patch.object(liquefy_bridge, "liquefy_available", return_value=True):
            rows = liquefy_bridge.lookup_cold_archive_candidates("greenhouse")
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["storage_backend"], "local_archive")


if __name__ == "__main__":
    unittest.main()
