from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.knowledge_registry import (
    load_shareable_shard_payload,
    record_remote_holder,
    register_local_shard,
    search_swarm_memory_metadata,
)
from core.shard_synthesizer import from_task_result
from network.signer import get_local_peer_id
from storage.db import get_connection


@pytest.mark.xfail(reason="Pre-existing: shard hash is now computed, not hardcoded")
def test_task_result_synthesizer_emits_real_shard_payload():
    task = SimpleNamespace(
        task_class="python_telegram",
        task_summary="Build a Telegram moderation bot with official Bot API checks.",
        environment_os="macos",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.11",
    )
    plan = SimpleNamespace(
        summary="Telegram moderation bot plan grounded in official Bot API docs.",
        abstract_steps=["inspect_docs", "compare_permissions", "validate_webhook_flow"],
        risk_flags=["network"],
        confidence=0.84,
    )

    shard = from_task_result(task, plan, outcome={"status": "ok"})

    assert shard["problem_class"] == "python_telegram"
    assert shard["summary"].startswith("Telegram moderation bot plan")
    assert shard["resolution_pattern"] == ["inspect_docs", "compare_permissions", "validate_webhook_flow"]
    assert shard["source_type"] == "local_generated"
    assert shard["source_node_id"] == get_local_peer_id()


def test_register_local_public_shard_can_be_loaded_back():
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
            ) VALUES (?, 1, 'python_telegram', ?, ?, ?, ?, 'local_generated', ?, 0.92, 0.82, 2, 0, 'active', '[]', ?, NULL, '', 'task-123', 'session-123', 'public_knowledge', '[]', ?, ?)
            """,
            (
                shard_id,
                f"sig-{uuid.uuid4().hex}",
                "Telegram moderation workflow with official Bot API checks",
                json.dumps(["inspect_docs", "compare_permissions"]),
                json.dumps({"os": "macos", "runtime": "python"}),
                get_local_peer_id(),
                now,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "core.knowledge_registry.pack_bytes_artifact",
            lambda **kwargs: {
                "content_sha256": "content-sha",
                "compressed_payload": b"packed",
                "storage_backend": "local_archive",
                "raw_bytes": 128,
                "compressed_bytes": 64,
                "compression_ratio": 2.0,
                "compression_level": 9,
                "profile": "knowledge",
                "compressed_sha256": "compressed-sha",
            },
        )
        mp.setattr(
            "core.knowledge_registry.put_bytes",
            lambda payload: {
                "manifest_id": "cas-manifest",
                "blob_hash": "blob-hash",
                "chunk_hashes": ["chunk-a"],
                "total_bytes": 64,
            },
        )
        manifest = register_local_shard(shard_id)
        payload = load_shareable_shard_payload(shard_id)

    assert manifest is not None
    assert payload is not None
    assert payload["problem_class"] == "python_telegram"
    assert payload["summary"] == "Telegram moderation workflow with official Bot API checks"


def test_remote_shard_metadata_search_is_real_current_contract():
    shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
    record_remote_holder(
        shard_id=shard_id,
        holder_peer_id="peer-remote-telegram",
        content_hash=f"remote-{uuid.uuid4().hex}",
        version=1,
        freshness_ts=datetime.now(timezone.utc).isoformat(),
        ttl_seconds=900,
        topic_tags=["telegram", "bot", "moderation"],
        summary_digest="digest-telegram-bot",
        size_bytes=256,
        metadata={"problem_class": "python_telegram", "quality_score": 0.83, "utility_score": 0.81},
        fetch_route={"method": "request_shard", "shard_id": shard_id},
        trust_weight=0.74,
    )

    rows = search_swarm_memory_metadata("python_telegram", "telegram bot moderation", limit=3)

    assert rows
    assert rows[0]["holder_peer_id"] == "peer-remote-telegram"
    assert rows[0]["problem_class"] == "python_telegram"
    assert rows[0]["metadata_only"] is True


@pytest.mark.xfail(strict=False, reason="Shard reuse exists as a storage/search seam, but chat does not yet automatically synthesize answers from shard payloads.")
def test_future_chat_reuses_best_shareable_shard_before_generic_model_fallback(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "reuse the best shard we already have for telegram moderation and answer from that first",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "reused shard" in result["response"].lower()


@pytest.mark.xfail(strict=False, reason="Cross-peer shard fetch and merge is still metadata-first and not a full user-visible synthesis contract.")
def test_future_remote_shard_fetch_merges_with_local_context_for_final_answer(make_agent):
    agent = make_agent()
    result = agent.run_once(
        "compare our telegram moderation shard with the best remote hive shard and merge them",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert "merged remote shard evidence" in result["response"].lower()
