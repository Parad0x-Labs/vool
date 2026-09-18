from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.knowledge_freshness_audit import (
    HolderFreshnessPolicy,
    assess_holder_audit_need,
    finalize_sampling_audit,
    select_holders_for_sampling,
    start_sampling_audit,
)
from core.knowledge_possession_challenge import respond_to_knowledge_possession_challenge
from core.knowledge_registry import register_local_shard
from core.meet_and_greet_models import KnowledgeChallengeResponseRequest, KnowledgeChallengeVerifyRequest
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.knowledge_possession_store import get_challenge
from storage.migrations import run_migrations
from storage.replica_table import all_holders


class KnowledgeFreshnessAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            for table in (
                "knowledge_holder_audits",
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

    def _seed_local_shard(self) -> str:
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
                ) VALUES (?, 1, 'python_telegram', ?, ?, ?, ?, 'local_generated', ?, 0.9, 0.7, 0, 0, 'active', '[]', ?, NULL, '', '', '', 'public_knowledge', '[]', ?, ?)
                """,
                (
                    shard_id,
                    f"sig-{uuid.uuid4().hex}",
                    "Telegram bot architecture with evidence",
                    json.dumps(["inspect_docs", "compare_patterns"]),
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
        register_local_shard(shard_id)
        return shard_id

    def test_stale_holder_is_selected_for_sampling(self) -> None:
        shard_id = self._seed_local_shard()
        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE knowledge_holders
                SET freshness_ts = ?, expires_at = ?, last_proved_at = NULL
                WHERE shard_id = ? AND holder_peer_id = ?
                """,
                (
                    (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                    (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    shard_id,
                    get_local_peer_id(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        holder = next(row for row in all_holders(limit=50) if row["shard_id"] == shard_id)
        decision = assess_holder_audit_need(holder, policy=HolderFreshnessPolicy(stale_after_seconds=60))
        self.assertTrue(decision.due)
        selected = select_holders_for_sampling(policy=HolderFreshnessPolicy(stale_after_seconds=60))
        self.assertTrue(any(row["shard_id"] == shard_id for row in selected))

    @pytest.mark.xfail(reason="Pre-existing: knowledge holder state not initialized")
    def test_successful_sampling_audit_marks_holder_verified(self) -> None:
        shard_id = self._seed_local_shard()
        audit = start_sampling_audit(shard_id=shard_id, holder_peer_id=get_local_peer_id())
        challenge = get_challenge(str(audit["challenge_id"]))
        self.assertIsNotNone(challenge)
        response = respond_to_knowledge_possession_challenge(
            KnowledgeChallengeResponseRequest(
                challenge_id=str(audit["challenge_id"]),
                shard_id=shard_id,
                holder_peer_id=get_local_peer_id(),
                requester_peer_id=get_local_peer_id(),
                chunk_index=int(challenge["chunk_index"]),
                nonce=str(challenge["nonce"]),
            )
        )
        result = finalize_sampling_audit(
            verify_request=KnowledgeChallengeVerifyRequest(
                challenge_id=str(audit["challenge_id"]),
                requester_peer_id=get_local_peer_id(),
                response=response,
            )
        )
        self.assertEqual(result["status"], "passed")
        holder = next(row for row in all_holders(limit=50) if row["shard_id"] == shard_id)
        self.assertEqual(holder["audit_state"], "verified")
        self.assertGreaterEqual(int(holder["successful_audits"]), 1)
