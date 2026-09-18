from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone

import pytest

from core.knowledge_advertiser import broadcast_hello
from core.knowledge_registry import (
    find_relevant_remote_shards,
    load_shareable_shard_payload,
    register_local_shard,
    sync_local_learning_shards,
)
from network.knowledge_models import HelloAd, KnowledgeAdvert
from network.knowledge_router import handle_knowledge_message
from network.presence_router import handle_presence_message
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.knowledge_index import active_presence
from storage.knowledge_manifests import manifest_for_shard
from storage.migrations import run_migrations
from storage.replica_table import holders_for_shard


class KnowledgePresenceTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in ("learning_shards", "knowledge_manifests", "knowledge_holders", "presence_leases", "index_deltas"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.xfail(reason="Pre-existing: knowledge presence state not initialized")
    def test_local_shard_registration_creates_manifest_and_holder(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
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
                ) VALUES (?, 1, 'python_telegram', ?, ?, ?, ?, 'local_generated', ?, 0.8, 0.74, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Python Telegram bot command routing example",
                    json.dumps(["review_problem", "rank_options"]),
                    json.dumps({"os": "macos"}),
                    get_local_peer_id(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        manifest = register_local_shard(shard_id)
        self.assertIsNotNone(manifest)
        stored_manifest = manifest_for_shard(shard_id)
        self.assertIsNotNone(stored_manifest)
        metadata = dict(stored_manifest["metadata"])
        self.assertIn("dense_storage_backend", metadata)
        self.assertIn("dense_compressed_bytes", metadata)
        self.assertIn("dense_cas_blob_hash", metadata)
        self.assertGreater(int(metadata["dense_compressed_bytes"]), 0)
        self.assertGreater(int(metadata["dense_raw_bytes"]), 0)
        self.assertEqual(int(stored_manifest["size_bytes"]), int(metadata["dense_compressed_bytes"]))
        holders = holders_for_shard(shard_id)
        self.assertTrue(any(holder["holder_peer_id"] == get_local_peer_id() for holder in holders))
        self.assertEqual(holders[0]["fetch_route"]["method"], "request_shard")
        self.assertIn("dense_storage_backend", holders[0]["fetch_route"])

    def test_dense_capsule_can_rehydrate_shard_without_learning_row(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
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
                ) VALUES (?, 1, 'dense_knowledge', ?, ?, ?, ?, 'local_generated', ?, 0.92, 0.68, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Dense compressed shard that should still rehydrate after row deletion",
                    json.dumps(["canonicalize", "compress", "advertise"]),
                    json.dumps({"os": "linux", "lang": "python"}),
                    get_local_peer_id(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        register_local_shard(shard_id)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM learning_shards WHERE shard_id = ?", (shard_id,))
            conn.commit()
        finally:
            conn.close()

        shard = load_shareable_shard_payload(shard_id)
        self.assertIsNotNone(shard)
        self.assertEqual(shard["shard_id"], shard_id)
        self.assertEqual(shard["problem_class"], "dense_knowledge")
        self.assertEqual(shard["resolution_pattern"], ["canonicalize", "compress", "advertise"])
        self.assertEqual(shard["environment_tags"]["lang"], "python")
        self.assertEqual(shard["summary"], "Dense compressed shard that should still rehydrate after row deletion")

    def test_low_value_public_shard_stays_candidate_only(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
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
                ) VALUES (?, 1, 'low_value', ?, ?, ?, ?, 'local_generated', ?, 0.21, 0.24, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "thin summary",
                    json.dumps([]),
                    json.dumps({"os": "linux"}),
                    get_local_peer_id(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        manifest = register_local_shard(shard_id)
        self.assertIsNone(manifest)
        self.assertIsNone(manifest_for_shard(shard_id))
        self.assertFalse(any(holder["holder_peer_id"] == get_local_peer_id() for holder in holders_for_shard(shard_id)))
        self.assertIsNone(load_shareable_shard_payload(shard_id))

    def test_sync_withdraws_manifest_when_shard_falls_below_gate(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
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
                ) VALUES (?, 1, 'quality_decay', ?, ?, ?, ?, 'local_generated', ?, 0.91, 0.82, 1, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "High-value shard with enough detail to promote into dense shared memory",
                    json.dumps(["capture pattern", "summarize fix"]),
                    json.dumps({"os": "linux"}),
                    get_local_peer_id(),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertIsNotNone(register_local_shard(shard_id))
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE learning_shards
                SET quality_score = 0.22,
                    trust_score = 0.18,
                    resolution_pattern_json = '[]',
                    summary = 'thin summary',
                    updated_at = CURRENT_TIMESTAMP
                WHERE shard_id = ?
                """,
                (shard_id,),
            )
            conn.commit()
        finally:
            conn.close()

        sync_local_learning_shards()
        self.assertFalse(any(holder["holder_peer_id"] == get_local_peer_id() for holder in holders_for_shard(shard_id)))
        self.assertIsNone(load_shareable_shard_payload(shard_id))

    def test_remote_knowledge_ad_is_indexed(self) -> None:
        remote_peer = f"peer-{uuid.uuid4().hex}"
        shard_id = f"remote-shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        payload = KnowledgeAdvert(
            shard_id=shard_id,
            content_hash=shard_id,
            version=1,
            holder_peer_id=remote_peer,
            topic_tags=["python", "telegram", "bot"],
            summary_digest="digest-python-telegram",
            size_bytes=128,
            freshness_ts=datetime.now(timezone.utc),
            ttl_seconds=600,
            trust_weight=0.7,
            access_mode="public",
            fetch_methods=["request_shard"],
            fetch_route={"method": "request_shard", "shard_id": shard_id},
            metadata={"problem_class": "python_telegram"},
            manifest_id=f"manifest-{uuid.uuid4().hex}",
        )
        result = handle_knowledge_message("KNOWLEDGE_AD", payload)
        self.assertTrue(result.ok)
        matches = find_relevant_remote_shards("python_telegram", "Need help with telegram bot commands", limit=5)
        self.assertTrue(any(item["shard_id"] == shard_id for item in matches))

    def test_presence_heartbeat_updates_active_presence(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}"
        payload = HelloAd(
            agent_id=peer_id,
            agent_name="TestAgent",
            status="idle",
            capabilities=["research", "validation"],
            transport_mode="lan_only",
            trust_score=0.6,
            timestamp=datetime.now(timezone.utc),
            lease_seconds=180,
        )
        result = handle_presence_message("HELLO_AD", payload)
        self.assertTrue(result.ok)
        rows = active_presence()
        self.assertTrue(any(row["peer_id"] == peer_id for row in rows))

    def test_local_hello_records_self_presence(self) -> None:
        broadcast_hello(
            agent_name="Vool",
            capabilities=["research", "validation"],
            status="idle",
            transport_mode="lan_only",
        )
        rows = active_presence()
        local_rows = [row for row in rows if row["peer_id"] == get_local_peer_id()]
        self.assertTrue(local_rows)
        self.assertEqual(local_rows[0]["agent_name"], "Vool")

    def test_broadcast_hello_routes_through_peer_delivery_broadcast(self) -> None:
        calls: list[dict[str, object]] = []

        def _broadcast(raw: bytes, *, message_type: str, target_id: str, limit: int = 32, **_kwargs: object) -> int:
            calls.append(
                {
                    "message_type": message_type,
                    "target_id": target_id,
                    "limit": limit,
                    "raw": raw,
                }
            )
            return 2

        with unittest.mock.patch("core.knowledge_advertiser.broadcast_to_recent_peers", side_effect=_broadcast):
            sent = broadcast_hello(
                agent_name="Vool",
                capabilities=["research"],
                status="idle",
                transport_mode="lan_only",
                limit=5,
            )

        self.assertEqual(sent, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["message_type"], "HELLO_AD")
        self.assertEqual(calls[0]["target_id"], get_local_peer_id())
        self.assertEqual(calls[0]["limit"], 5)


if __name__ == "__main__":
    unittest.main()
