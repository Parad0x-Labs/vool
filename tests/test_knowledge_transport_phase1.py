from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

from core.daemon.messages import handle_request_shard, handle_shard_payload
from core.knowledge_registry import register_local_shard
from core.knowledge_transport import build_transport_shard_response
from core.shard_synthesizer import from_task_result, shard_signable_bytes
from network.signer import get_local_peer_id, sign
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.shard_fetch_receipts import latest_receipt_for_shard


def _insert_signed_public_shard(*, problem_class: str = "system_design", summary: str = "Swarm replication baseline") -> tuple[str, dict]:
    task = SimpleNamespace(
        task_class=problem_class,
        task_summary=summary,
        environment_os="macos",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    plan = SimpleNamespace(
        summary=summary,
        abstract_steps=["compare topology", "validate holder state"],
        risk_flags=[],
        confidence=0.91,
    )
    shard = from_task_result(task, plan, outcome={"status": "ok"})
    shard["trust_score"] = 0.82
    shard["signature"] = sign(shard_signable_bytes(shard))
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, 0, 'active', ?, ?, ?, ?, ?, ?, 'public_knowledge', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                shard["shard_id"],
                int(shard["schema_version"]),
                shard["problem_class"],
                shard["problem_signature"],
                shard["summary"],
                json.dumps(shard["resolution_pattern"], sort_keys=True),
                json.dumps(shard["environment_tags"], sort_keys=True),
                shard["source_type"],
                shard["source_node_id"],
                float(shard["quality_score"]),
                float(shard["trust_score"]),
                json.dumps(shard["risk_flags"], sort_keys=True),
                shard["freshness_ts"],
                shard["expires_ts"],
                shard["signature"],
                "task-origin",
                "session-origin",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return shard["shard_id"], shard


def _reset_tables() -> None:
    run_migrations()
    conn = get_connection()
    try:
        for table in ("learning_shards", "knowledge_manifests", "knowledge_holders", "shard_fetch_receipts", "index_deltas"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_build_transport_shard_response_includes_manifest_binding_and_origin_fields() -> None:
    _reset_tables()
    shard_id, shard = _insert_signed_public_shard(summary="Swarm replication notes with signed provenance")
    register_local_shard(shard_id)

    payload = build_transport_shard_response(query_id=f"query-{uuid.uuid4().hex}", shard_id=shard_id)

    assert payload is not None
    assert payload["manifest_id"].startswith("manifest-")
    assert payload["content_hash"]
    assert payload["version"] == 1
    assert payload["summary_digest"]
    assert payload["shard"]["source_type"] == "local_generated"
    assert payload["shard"]["source_node_id"] == shard["source_node_id"] == get_local_peer_id()


def test_handle_request_shard_emits_transport_payload_with_manifest_binding() -> None:
    _reset_tables()
    shard_id, _ = _insert_signed_public_shard(summary="Emit transport payload with manifest binding")
    register_local_shard(shard_id)

    class _Daemon:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def _send_or_log(self, _host: str, _port: int, raw: bytes, **_kwargs: object) -> None:
            self.sent.append(raw)

    daemon = _Daemon()
    handle_request_shard(daemon, {"query_id": f"query-{uuid.uuid4().hex}", "shard_id": shard_id}, ("127.0.0.1", 9000))

    assert daemon.sent
    envelope = json.loads(daemon.sent[0].decode("utf-8"))
    payload = dict(envelope["payload"])
    assert envelope["msg_type"] == "SHARD_PAYLOAD"
    assert payload["manifest_id"].startswith("manifest-")
    assert payload["content_hash"]
    assert payload["summary_digest"]
    assert payload["shard"]["source_node_id"] == get_local_peer_id()


def test_handle_shard_payload_records_receipt_and_caches_valid_remote_shard() -> None:
    _reset_tables()
    shard_id, shard = _insert_signed_public_shard(summary="Cache validated remote shard for later reuse")
    register_local_shard(shard_id)
    payload = build_transport_shard_response(query_id=f"query-{uuid.uuid4().hex}", shard_id=shard_id)
    assert payload is not None
    conn = get_connection()
    try:
        conn.execute("DELETE FROM learning_shards WHERE shard_id = ?", (shard_id,))
        conn.commit()
    finally:
        conn.close()

    handle_shard_payload(None, payload, sender_peer_id=shard["source_node_id"])

    receipt = latest_receipt_for_shard(shard_id)
    assert receipt is not None
    assert receipt["accepted"] is True
    assert receipt["validation_state"] == "signature_and_manifest_verified"
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source_type, source_node_id FROM learning_shards WHERE shard_id = ? LIMIT 1",
            (shard_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["source_type"] == "peer_received"
    assert row["source_node_id"] == shard["source_node_id"]


def test_handle_shard_payload_rejects_manifest_mismatch_and_records_failed_receipt() -> None:
    _reset_tables()
    shard_id, shard = _insert_signed_public_shard(summary="Reject manifest mismatch during remote fetch")
    register_local_shard(shard_id)
    payload = build_transport_shard_response(query_id=f"query-{uuid.uuid4().hex}", shard_id=shard_id)
    assert payload is not None
    payload["content_hash"] = f"wrong-{uuid.uuid4().hex}"
    conn = get_connection()
    try:
        conn.execute("DELETE FROM learning_shards WHERE shard_id = ?", (shard_id,))
        conn.commit()
    finally:
        conn.close()

    handle_shard_payload(None, payload, sender_peer_id=shard["source_node_id"])

    receipt = latest_receipt_for_shard(shard_id)
    assert receipt is not None
    assert receipt["accepted"] is False
    assert receipt["validation_state"] == "content_hash_mismatch"
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM learning_shards WHERE shard_id = ? LIMIT 1", (shard_id,)).fetchone()
    finally:
        conn.close()
    assert row is None
