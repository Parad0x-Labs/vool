from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import core.bootstrap_sync as bootstrap_sync
from core.bootstrap_adapters import FileTopicAdapter
from core.bootstrap_sync import publish_local_presence_snapshots, sync_from_bootstrap_topics
from core.discovery_index import (
    note_verified_peer_endpoint_delivery_result,
    record_verified_peer_endpoint_proof,
    register_peer_endpoint,
    verified_endpoints_for_peer,
)
from core.runtime_paths import configure_runtime_home
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations


def test_publish_local_presence_snapshots_uses_active_record_helper() -> None:
    adapter = mock.Mock()
    adapter.publish_snapshot.return_value = True

    with mock.patch("core.bootstrap_sync._get_active_peer_records", return_value=[]):
        written = publish_local_presence_snapshots(topic_names=["topic_a"], adapter=adapter)

    assert written == 1
    adapter.publish_snapshot.assert_called_once()


def test_sync_from_bootstrap_topics_returns_complete_result_fields() -> None:
    adapter = mock.Mock()
    adapter.fetch_snapshot.side_effect = [{"records": []}, None]

    with mock.patch("core.bootstrap_sync._merge_snapshot", return_value=2):
        result = sync_from_bootstrap_topics(topic_names=["topic_a", "topic_b"], adapter=adapter)

    assert result.topics_written == 0
    assert result.topics_read == 1
    assert result.records_merged == 2


def test_file_topic_adapter_defaults_to_runtime_bootstrap_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        configure_runtime_home(tmp)
        try:
            adapter = FileTopicAdapter()
            assert adapter.base_dir == (Path(tmp) / "data" / "bootstrap").resolve()
            assert adapter.base_dir != Path("bootstrap").resolve()
        finally:
            configure_runtime_home(None)


def test_publish_local_presence_snapshots_writes_to_active_runtime_home() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        configure_runtime_home(tmp)
        try:
            topic_name = "runtime_isolation_test"
            with mock.patch("core.bootstrap_sync._get_active_peer_records", return_value=[]), mock.patch(
                "core.bootstrap_sync.local_peer_id",
                return_value="peer-local",
            ), mock.patch(
                "core.bootstrap_sync.sign",
                return_value="signed",
            ), mock.patch("core.bootstrap_sync.audit_logger.log"):
                written = publish_local_presence_snapshots(topic_names=[topic_name])

            topic_path = Path(tmp) / "data" / "bootstrap" / f"{topic_name}.json"
            assert written == 1
            assert topic_path.exists()
            assert not (Path("bootstrap").resolve() / f"{topic_name}.json").exists()
            payload = json.loads(topic_path.read_text(encoding="utf-8"))
            assert payload["publisher_peer_id"] == "peer-local"
            assert payload["signature"] == "signed"
        finally:
            configure_runtime_home(None)


def test_sync_from_bootstrap_topics_persists_bootstrap_endpoint_proof_from_endpoints_list() -> None:
    run_migrations()
    reset_default_connection()
    conn = get_connection()
    try:
        for table in ("agent_capabilities", "peers", "peer_endpoints", "peer_endpoint_observations", "peer_endpoint_candidates"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    peer_id = "peer-bootstrap-proof-000000000000000000000001"
    record = {
        "peer_id": peer_id,
        "status": "idle",
        "capabilities": ["research"],
        "capacity": 2,
        "trust_score": 0.6,
        "host_group_hint_hash": None,
        "last_seen_at": "2026-03-26T10:00:00+00:00",
        "endpoints": [
            {"host": "203.0.113.80", "port": 49880, "source": "bootstrap"},
        ],
    }
    body = bootstrap_sync._snapshot_body("topic_a", bootstrap_sync.local_peer_id(), [record], 15)
    signed = bootstrap_sync._sign_snapshot_body(body)
    adapter = mock.Mock()
    adapter.fetch_snapshot.side_effect = [signed]

    result = sync_from_bootstrap_topics(topic_names=["topic_a"], adapter=adapter)

    assert result.records_merged == 1
    endpoints = verified_endpoints_for_peer(peer_id)
    assert len(endpoints) == 1
    assert (endpoints[0].host, endpoints[0].port, endpoints[0].source) == ("203.0.113.80", 49880, "bootstrap")
    assert endpoints[0].verification_kind == "bootstrap_snapshot"
    assert endpoints[0].proof_hash == signed["snapshot_hash"]


def test_publish_local_presence_snapshots_exports_delivery_order_not_stale_registry_order() -> None:
    run_migrations()
    reset_default_connection()
    conn = get_connection()
    try:
        for table in ("agent_capabilities", "peers", "peer_endpoints", "peer_endpoint_observations", "peer_endpoint_candidates"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            """
            INSERT INTO agent_capabilities (
                peer_id, status, capabilities_json, compute_class, supported_models_json,
                capacity, trust_score, assist_filters_json, host_group_hint_hash,
                last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "peer-export-order",
                "idle",
                '["research"]',
                "cpu_basic",
                "[]",
                2,
                0.7,
                "{}",
                None,
                "2026-03-26T10:00:00+00:00",
                "2026-03-26T10:00:00+00:00",
                "2026-03-26T10:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    register_peer_endpoint("peer-export-order", "203.0.113.90", 49890, source="self")
    register_peer_endpoint("peer-export-order", "203.0.113.91", 49891, source="bootstrap")
    record_verified_peer_endpoint_proof(
        "peer-export-order",
        "203.0.113.91",
        49891,
        source="bootstrap",
        verification_kind="bootstrap_snapshot",
        proof_message_id="bootstrap-proof",
        proof_timestamp="2026-03-26T10:30:00+00:00",
    )
    note_verified_peer_endpoint_delivery_result("peer-export-order", "203.0.113.90", 49890, delivered=True)

    records = bootstrap_sync._local_capability_records(limit=8)

    assert len(records) == 1
    assert records[0]["endpoint"] == {"host": "203.0.113.90", "port": 49890}
    assert records[0]["endpoints"][:2] == [
        {"host": "203.0.113.90", "port": 49890, "source": "self"},
        {"host": "203.0.113.91", "port": 49891, "source": "bootstrap"},
    ]
