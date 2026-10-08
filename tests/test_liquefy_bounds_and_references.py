"""Bounded decompression and reference-ownership isolation.

Decompression bound (role item 6): a small compressed payload must never be
able to force unbounded memory. At e821457d ``load_packed_bytes`` called
one-shot decompression with no output cap for both codecs.

Reference ownership: dedup at the CAS must not couple the authorization of two
distinct references — revoking/deleting one shard's availability must neither
leak it through a shared blob nor corrupt another shard's authorized restore.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import core.liquefy_bridge as liquefy_bridge
import storage.chunk_store as chunk_store
from core.knowledge_registry import (
    load_canonical_shareable_shard_payload,
    register_local_shard,
    withdraw_local_shard,
)
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.knowledge_manifests import upsert_manifest
from storage.migrations import run_migrations

NOW = datetime.now(timezone.utc)

# 40 MiB of compressible body compressed to a few KiB — the decompression
# cap is 32 MiB, so this is decisively over it while cheap to build.
_OVER_CAP_BODY = b"\0" * (40 * 1024 * 1024)
_UNDER_CAP_BODY = b"press log: pressure curve nominal at 2.1 bar\n" * 2048  # ~96 KiB


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class DecompressionBoundsTests(unittest.TestCase):
    def _pack_via_codec(self, backend: str) -> bytes:
        if backend == "liquefy":
            import zstandard as zstd

            return zstd.ZstdCompressor(level=19).compress(_OVER_CAP_BODY)
        return gzip.compress(_OVER_CAP_BODY, compresslevel=9)

    def test_zstd_over_cap_payload_is_refused(self) -> None:
        try:
            import zstandard
        except ImportError:
            self.skipTest("zstandard unavailable")
        packed = self._pack_via_codec("liquefy")
        self.assertLess(len(packed), 1024 * 1024)
        with self.assertRaises(Exception):
            liquefy_bridge.load_packed_bytes(payload=packed, storage_backend="liquefy")

    def test_gzip_over_cap_payload_is_refused(self) -> None:
        packed = self._pack_via_codec("gzip")
        self.assertLess(len(packed), 1024 * 1024)
        with self.assertRaises(Exception):
            liquefy_bridge.load_packed_bytes(payload=packed, storage_backend="local_archive")

    def test_legitimate_payload_under_cap_round_trips(self) -> None:
        try:
            import zstandard as zstd

            packed = zstd.ZstdCompressor().compress(_UNDER_CAP_BODY)
            self.assertEqual(
                liquefy_bridge.load_packed_bytes(payload=packed, storage_backend="liquefy"),
                _UNDER_CAP_BODY,
            )
        except ImportError:
            pass
        gz = gzip.compress(_UNDER_CAP_BODY)
        self.assertEqual(
            liquefy_bridge.load_packed_bytes(payload=gz, storage_backend="local_archive"),
            _UNDER_CAP_BODY,
        )


class ReferenceOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("learning_shards", "knowledge_manifests", "knowledge_holders"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()
        self._cas_root = Path(tempfile.mkdtemp(prefix="lq-refs-cas-"))
        patcher = mock.patch.object(chunk_store, "chunk_root", lambda: self._cas_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: _rmtree(self._cas_root))

    def _promote(self, shard_id: str, summary: str) -> dict:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_shards (
                    shard_id, schema_version, problem_class, problem_signature,
                    summary, resolution_pattern_json, environment_tags_json,
                    source_type, source_node_id, quality_score, trust_score,
                    local_validation_count, local_failure_count,
                    quarantine_status, risk_flags_json, freshness_ts, expires_ts,
                    signature, origin_task_id, origin_session_id, share_scope,
                    restricted_terms_json, created_at, updated_at
                ) VALUES (?, 1, 'mill_routing', ?, ?, ?, ?, 'local_generated', ?, 0.9, 0.8, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    summary,
                    json.dumps(["inspect", "adjust", "verify"]),
                    json.dumps({"os": "linux"}),
                    get_local_peer_id(),
                    _iso(NOW),
                    _iso(NOW),
                    _iso(NOW),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        manifest = register_local_shard(shard_id)
        self.assertIsNotNone(manifest)
        return manifest

    def _drop_row(self, shard_id: str) -> None:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM learning_shards WHERE shard_id = ?", (shard_id,))
            conn.commit()
        finally:
            conn.close()

    def test_withdrawing_one_shard_never_breaks_the_other(self) -> None:
        """Revocation isolation through the real owner: withdrawing shard A's
        holder must leave shard B's authorized restore untouched."""
        shard_a = f"shard-{uuid.uuid4().hex}"
        shard_b = f"shard-{uuid.uuid4().hex}"
        self._promote(shard_a, "Spindle speed drift fix with thermal compensation notes")
        self._promote(shard_b, "Coolant nozzle alignment fix with dwell calibration")
        # withdraw A while its live row still exists (the withdraw owner keys on
        # the row), then let both rows go — restores go through the archive
        self.assertTrue(withdraw_local_shard(shard_a))
        self._drop_row(shard_a)
        self._drop_row(shard_b)
        self.assertIsNone(load_canonical_shareable_shard_payload(shard_a))
        self.assertIsNotNone(
            load_canonical_shareable_shard_payload(shard_b),
            "revoking one reference must not corrupt another authorized occurrence",
        )

    def test_private_manifest_on_a_shared_blob_is_refused(self) -> None:
        """Dedup means two manifests can point at the same blob. Authorization
        is per-reference: a manifest that downgrades its own scope to private
        must not keep serving those shared bytes."""
        shard_pub = f"shard-{uuid.uuid4().hex}"
        shard_priv = f"shard-{uuid.uuid4().hex}"
        manifest_pub = self._promote(shard_pub, "Public kiln schedule fix with cone-bend targets")
        # a second reference to the SAME blob, but declaring a private scope
        upsert_manifest(
            manifest_id=f"manifest-{uuid.uuid4().hex}",
            shard_id=shard_priv,
            content_hash=manifest_pub["content_hash"],
            version=1,
            topic_tags=[],
            summary_digest=manifest_pub["summary_digest"],
            size_bytes=manifest_pub["size_bytes"],
            metadata=dict(
                manifest_pub["metadata"],
                share_scope="local_only",
            ),
        )
        from storage.replica_table import upsert_holder

        upsert_holder(
            shard_id=shard_priv,
            holder_peer_id=get_local_peer_id(),
            home_region="global",
            content_hash=manifest_pub["content_hash"],
            version=1,
            freshness_ts=_iso(NOW),
            expires_at=_iso(NOW + timedelta(days=30)),
            access_mode="public",
            fetch_route={"method": "request_shard", "shard_id": shard_priv},
            trust_weight=0.8,
            status="active",
            source="local",
        )
        self._drop_row(shard_pub)
        self.assertIsNone(
            load_canonical_shareable_shard_payload(shard_priv),
            "a private reference must not keep serving a shared blob",
        )


if __name__ == "__main__":
    unittest.main()
