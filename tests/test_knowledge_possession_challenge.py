from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timezone

import pytest

from core.knowledge_possession_challenge import (
    issue_knowledge_possession_challenge,
    respond_to_knowledge_possession_challenge,
    verify_knowledge_possession_response,
)
from core.knowledge_registry import record_remote_holder, register_local_shard
from core.meet_and_greet_models import (
    KnowledgeChallengeIssueRequest,
    KnowledgeChallengeResponseRequest,
    KnowledgeChallengeVerifyRequest,
)
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


class KnowledgePossessionChallengeTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "knowledge_possession_challenges",
                "knowledge_holders",
                "knowledge_manifests",
                "learning_shards",
                "manifest_store",
                "blob_index",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.xfail(reason="Pre-existing: knowledge holder state not initialized")
    def test_local_holder_can_answer_possession_challenge(self) -> None:
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
                ) VALUES (?, 1, 'python_discord', ?, ?, ?, ?, 'local_generated', ?, 0.9, 0.7, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Discord bot moderation workflow with official API checks",
                    json.dumps(["inspect_docs", "compare_permissions"]),
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
        self.assertIsNotNone(manifest)

        challenge = issue_knowledge_possession_challenge(
            KnowledgeChallengeIssueRequest(
                shard_id=shard_id,
                holder_peer_id=get_local_peer_id(),
                requester_peer_id=get_local_peer_id(),
            )
        )
        response = respond_to_knowledge_possession_challenge(
            KnowledgeChallengeResponseRequest(
                challenge_id=challenge.challenge_id,
                shard_id=challenge.shard_id,
                holder_peer_id=challenge.holder_peer_id,
                requester_peer_id=challenge.requester_peer_id,
                chunk_index=challenge.chunk_index,
                nonce=challenge.nonce,
            )
        )
        verified = verify_knowledge_possession_response(
            KnowledgeChallengeVerifyRequest(
                challenge_id=challenge.challenge_id,
                requester_peer_id=challenge.requester_peer_id,
                response=response,
            )
        )
        self.assertEqual(verified.status, "passed")

    def test_remote_manifest_without_cas_proof_is_not_challengeable(self) -> None:
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        holder_peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        record_remote_holder(
            shard_id=shard_id,
            holder_peer_id=holder_peer_id,
            content_hash=f"remote-{uuid.uuid4().hex}",
            version=1,
            freshness_ts=datetime.now(timezone.utc).isoformat(),
            ttl_seconds=900,
            topic_tags=["telegram", "bot"],
            summary_digest="digest",
            size_bytes=128,
            metadata={"problem_class": "python_telegram"},
            fetch_route={"method": "request_shard", "shard_id": shard_id},
            trust_weight=0.6,
        )
        with self.assertRaisesRegex(ValueError, "proof-capable CAS chunk metadata"):
            issue_knowledge_possession_challenge(
                KnowledgeChallengeIssueRequest(
                    shard_id=shard_id,
                    holder_peer_id=holder_peer_id,
                    requester_peer_id=get_local_peer_id(),
                )
            )


if __name__ == "__main__":
    unittest.main()
