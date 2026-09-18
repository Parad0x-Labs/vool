from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone

from core import audit_logger
from core.candidate_knowledge_lane import record_candidate_output
from core.final_response_store import store_final_response
from core.human_input_adapter import adapt_user_input
from core.knowledge_registry import record_remote_holder, register_local_shard
from core.vool_user_summary import build_user_summary, render_user_summary
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.swarm_memory import save_sniffed_context


class VoolUserSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_summary_includes_learning_mesh_and_memory_sections(self) -> None:
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
                ) VALUES (?, 1, 'security_hardening', ?, ?, ?, ?, 'local_generated', ?, 0.9, 0.7, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Harden secrets so passwords never leak from local setup",
                    json.dumps(["identify_sensitive_surfaces", "remove_secret_exposure_paths"]),
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

        register_local_shard(shard_id)
        remote_peer = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        record_remote_holder(
            shard_id=f"remote-{shard_id}",
            holder_peer_id=remote_peer,
            content_hash=f"remote-{shard_id}",
            version=1,
            freshness_ts=now,
            ttl_seconds=900,
            topic_tags=["mesh", "replication"],
            summary_digest="digest-mesh-replication",
            size_bytes=128,
            metadata={"problem_class": "system_design"},
            fetch_route={"method": "request_shard", "shard_id": f"remote-{shard_id}"},
            trust_weight=0.65,
            home_region="global",
            access_mode="public",
        )

        audit_logger.log("knowledge_ad_broadcast", target_id=get_local_peer_id(), target_type="peer", details={"sent": 3})
        save_sniffed_context(
            parent_peer_id=remote_peer,
            prompt_data={"summary": "mesh replication topic"},
            result_data={"summary": "replicated safely"},
        )
        record_candidate_output(
            task_hash=f"task-hash-{uuid.uuid4().hex}",
            task_id=f"task-{uuid.uuid4().hex}",
            trace_id=f"trace-{uuid.uuid4().hex}",
            task_class="security_hardening",
            task_kind="analysis",
            output_mode="summary_block",
            provider_name="test-provider",
            model_name="test-model",
            raw_output="raw draft output",
            normalized_output="normalized draft output",
            structured_output={"summary": "draft"},
            confidence=0.7,
            trust_score=0.6,
            validation_state="candidate",
        )
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifact_manifests (
                    artifact_id, source_kind, topic_id, claim_id, candidate_id, session_id,
                    title, summary, tags_json, search_text, metadata_json, file_path,
                    storage_backend, content_sha256, raw_bytes, compressed_bytes, compression_ratio,
                    created_at, updated_at
                ) VALUES (?, 'test_summary', '', '', '', ?, 'Security hardening packet', 'Packed security shard summary', '[]', 'security packed', '{}', ?, 'test', 'sha256-test', 64, 32, 2.0, ?, ?)
                """,
                (
                    f"artifact-{uuid.uuid4().hex}",
                    f"session-{uuid.uuid4().hex}",
                    f"/tmp/{uuid.uuid4().hex}.json.gz",
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        adapt_user_input("pls harden tg setup so no passwrods leak", session_id=f"summary-{uuid.uuid4().hex}")
        store_final_response(
            parent_task_id=f"task-{uuid.uuid4().hex}",
            raw="raw response",
            rendered="Rendered response about safe hardening.",
            status="complete",
            confidence=0.82,
        )

        report = build_user_summary(limit_recent=5)

        self.assertGreaterEqual(report["learning"]["local_generated_shards"], 1)
        self.assertGreaterEqual(report["knowledge_lanes"]["shareable_store_shards"], 1)
        self.assertGreaterEqual(report["knowledge_lanes"]["candidate_rows"], 1)
        self.assertGreaterEqual(report["knowledge_lanes"]["artifact_manifests"], 1)
        self.assertGreaterEqual(report["mesh_index"]["remote_indexed_shards"], 1)
        self.assertGreaterEqual(report["data_flow"]["outbound_messages_estimate"], 1)
        self.assertTrue(report["memory"]["recent_final_responses"])
        self.assertTrue(any("Harden secrets" in item["summary"] for item in report["learning"]["recent_learning"]))

    def test_rendered_summary_has_user_facing_sections(self) -> None:
        report = build_user_summary(limit_recent=3)
        rendered = render_user_summary(report)
        self.assertIn("VOOL MEMORY AND MESH SUMMARY", rendered)
        self.assertIn("[WHAT VOOL LEARNED]", rendered)
        self.assertIn("[MESH INDEX]", rendered)
        self.assertIn("[KNOWLEDGE LANES]", rendered)
        self.assertIn("[DATA FLOW]", rendered)


if __name__ == "__main__":
    unittest.main()
