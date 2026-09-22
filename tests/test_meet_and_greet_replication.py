from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.meet_and_greet_models import (
    IndexDeltaRecord,
    IndexSnapshotResponse,
    KnowledgeHolderRecord,
    KnowledgeIndexEntry,
    MeetNodeRecord,
    MeetNodeRegisterRequest,
    PaymentStatusRecord,
    PresenceRecord,
)
from core.meet_and_greet_replication import HttpMeetClient, MeetAndGreetReplicator, ReplicationConfig, _lease_seconds
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from storage.db import get_connection
from storage.migrations import run_migrations


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeRemoteClient:
    def __init__(self, snapshot: IndexSnapshotResponse, deltas: list[IndexDeltaRecord]) -> None:
        self.snapshot = snapshot
        self.deltas = deltas
        self.snapshot_calls = 0
        self.delta_calls = 0
        self.last_snapshot_kwargs: dict[str, str | None] | None = None
        self.last_delta_kwargs: dict[str, str | None] | None = None

    def fetch_snapshot(self, base_url: str, *, target_region: str | None, summary_mode: str) -> IndexSnapshotResponse:
        self.snapshot_calls += 1
        self.last_snapshot_kwargs = {"target_region": target_region, "summary_mode": summary_mode}
        return self.snapshot

    def fetch_deltas(
        self,
        base_url: str,
        *,
        since_created_at: str | None,
        limit: int,
        target_region: str | None,
        summary_mode: str,
    ) -> list[IndexDeltaRecord]:
        self.delta_calls += 1
        self.last_delta_kwargs = {
            "since_created_at": since_created_at,
            "target_region": target_region,
            "summary_mode": summary_mode,
        }
        return self.deltas[:limit]


class MeetAndGreetReplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        _clear_meet_tables()
        self.service = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))

    def test_snapshot_import_merges_presence_knowledge_payment_and_meet_nodes(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        shard_id = f"shard-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"
        snapshot = IndexSnapshotResponse(
            snapshot_cursor=_now().isoformat(),
            source_region="eu",
            summary_mode="regional_detail",
            meet_nodes=[
                MeetNodeRecord(
                    node_id=f"seed-{uuid.uuid4().hex[:8]}",
                    base_url="https://seed.example.test",
                    region="eu",
                    role="seed",
                    platform_hint="linux",
                    priority=10,
                    status="active",
                    metadata={},
                    last_seen_at=_now().isoformat(),
                    created_at=_now().isoformat(),
                    updated_at=_now().isoformat(),
                )
            ],
            active_presence=[
                PresenceRecord(
                    agent_id=peer_id,
                    agent_name="Maria",
                    status="idle",
                    capabilities=["research"],
                    home_region="eu",
                    current_region="eu",
                    transport_mode="wan_direct",
                    trust_score=0.62,
                    last_heartbeat_at=_now().isoformat(),
                    lease_expires_at=(_now() + timedelta(minutes=3)).isoformat(),
                    endpoint={"host": "203.0.113.10", "port": 49152, "source": "api"},
                )
            ],
            knowledge_index=[
                KnowledgeIndexEntry(
                    manifest_id=f"manifest-{uuid.uuid4().hex}",
                    shard_id=shard_id,
                    content_hash=shard_id,
                    version=1,
                    topic_tags=["telegram", "routing"],
                    summary_digest="digest-telegram-routing",
                    size_bytes=256,
                    metadata={"problem_class": "python_telegram"},
                    latest_freshness=_now().isoformat(),
                    replication_count=1,
                    live_holder_count=1,
                    stale_holder_count=0,
                    holders=[
                        KnowledgeHolderRecord(
                            holder_peer_id=peer_id,
                            home_region="eu",
                            version=1,
                            freshness_ts=_now().isoformat(),
                            expires_at=(_now() + timedelta(minutes=10)).isoformat(),
                            trust_weight=0.72,
                            access_mode="public",
                            fetch_route={"method": "request_shard", "shard_id": shard_id},
                            status="active",
                            endpoint={"host": "203.0.113.10", "port": 49152, "source": "api"},
                        )
                    ],
                )
            ],
            payment_status=[
                PaymentStatusRecord(
                    task_or_transfer_id=task_id,
                    payer_peer_id=f"payer-{uuid.uuid4().hex}{uuid.uuid4().hex}",
                    payee_peer_id=peer_id,
                    status="reserved",
                    receipt_reference="receipt-1",
                    metadata={"currency": "DNA"},
                    updated_at=_now().isoformat(),
                )
            ],
        )
        replicator = MeetAndGreetReplicator(self.service, remote_client=_FakeRemoteClient(snapshot, []))
        result = replicator.apply_snapshot(snapshot, remote_node_id="remote-seed")

        self.assertEqual(result.mode, "snapshot")
        self.assertTrue(any(item.agent_id == peer_id for item in self.service.list_presence(limit=50)))
        self.assertTrue(any(item.shard_id == shard_id for item in self.service.list_knowledge_index(limit=50)))
        self.assertTrue(any(item.task_or_transfer_id == task_id for item in self.service.list_payment_status(limit=50)))
        self.assertTrue(any(item.node_id == snapshot.meet_nodes[0].node_id for item in self.service.list_meet_nodes(limit=50, active_only=False)))
        self.assertTrue(any(item.remote_node_id == "remote-seed" for item in self.service.list_sync_state(limit=50)))

    def test_sync_remote_node_prefers_snapshot_then_deltas(self) -> None:
        peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
        remote_node_id = f"global-seed-{uuid.uuid4().hex[:8]}"
        snapshot_cursor = _now().isoformat()
        snapshot = IndexSnapshotResponse(
            snapshot_cursor=snapshot_cursor,
            source_region="eu",
            summary_mode="regional_detail",
            meet_nodes=[],
            active_presence=[],
            knowledge_index=[],
            payment_status=[],
        )
        delta = IndexDeltaRecord(
            delta_id=f"delta-{uuid.uuid4().hex}",
            peer_id=peer_id,
            delta_type="presence_register",
            payload={
                "agent_id": peer_id,
                "agent_name": "Thomas",
                "status": "idle",
                "capabilities": ["validation"],
                "home_region": "eu",
                "current_region": "eu",
                "transport_mode": "wan_direct",
                "trust_score": 0.59,
                "timestamp": _now().isoformat(),
                "lease_seconds": 180,
                "endpoint": {"host": "198.51.100.5", "port": 49170, "source": "api"},
            },
            created_at=(_now() + timedelta(seconds=1)).isoformat(),
        )
        client = _FakeRemoteClient(snapshot, [delta])
        replicator = MeetAndGreetReplicator(
            self.service,
            config=ReplicationConfig(seed_snapshot_on_first_sync=True, local_region="eu"),
            remote_client=client,
        )

        first = replicator.sync_remote_node(remote_node_id=remote_node_id, base_url="https://seed.example.test")
        second = replicator.sync_remote_node(remote_node_id=remote_node_id, base_url="https://seed.example.test")

        self.assertEqual(first.mode, "snapshot")
        self.assertEqual(second.mode, "delta")
        self.assertEqual(client.snapshot_calls, 1)
        self.assertEqual(client.delta_calls, 1)
        self.assertEqual(client.last_snapshot_kwargs, {"target_region": "eu", "summary_mode": "regional_detail"})
        self.assertEqual(client.last_delta_kwargs["summary_mode"], "regional_detail")
        self.assertTrue(any(item.agent_id == peer_id for item in self.service.list_presence(limit=50)))

    def test_cross_region_sync_uses_summary_snapshot(self) -> None:
        remote_node_id = f"seed-us-{uuid.uuid4().hex[:8]}"
        self.service.register_meet_node(
            MeetNodeRegisterRequest(
                node_id=remote_node_id,
                base_url="https://seed-us.example.test",
                region="us",
                role="seed",
                platform_hint="linux",
                priority=10,
                status="active",
                metadata={},
            )
        )
        snapshot = IndexSnapshotResponse(
            snapshot_cursor=_now().isoformat(),
            source_region="us",
            summary_mode="global_summary",
            meet_nodes=[],
            active_presence=[],
            knowledge_index=[],
            payment_status=[],
        )
        client = _FakeRemoteClient(snapshot, [])
        replicator = MeetAndGreetReplicator(
            self.service,
            config=ReplicationConfig(local_region="eu", cross_region_summary_only=True, cross_region_force_snapshot=True),
            remote_client=client,
        )

        result = replicator.sync_remote_node(remote_node_id=remote_node_id, base_url="https://seed-us.example.test")

        self.assertEqual(result.mode, "snapshot")
        self.assertEqual(result.summary_mode, "global_summary")
        self.assertEqual(client.snapshot_calls, 1)
        self.assertEqual(client.delta_calls, 0)
        self.assertEqual(client.last_snapshot_kwargs, {"target_region": "eu", "summary_mode": "global_summary"})

    def test_lease_seconds_helper_clamps_remote_snapshot_leases(self) -> None:
        reference = _now()
        expires = reference + timedelta(seconds=7109)

        lease_seconds = _lease_seconds(expires.isoformat(), reference.isoformat())

        self.assertEqual(lease_seconds, 3600)

    def test_http_client_adds_auth_token_header_by_base_url(self) -> None:
        captured: dict[str, str] = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"ok": true, "result": {}}'

        def fake_urlopen(req, timeout=0, context=None):
            captured.clear()
            for key, value in req.header_items():
                captured[str(key)] = str(value)
            return _Resp()

        from core.effect_gateway import named_background_effect_scope

        client = HttpMeetClient(
            auth_token="global-token",
            auth_tokens_by_base_url={"https://seed-us.example.test": "seed-token"},
        )
        with patch("core.meet_and_greet_replication.urllib.request.urlopen", side_effect=fake_urlopen):
            # The outbound door denies a fetch outside any turn or named background scope; the
            # replication client runs as exactly such background work, so its owner opens the
            # named scope the law requires at the owning entry point.
            with named_background_effect_scope("test.meet_and_greet_http_client"):
                client._get_json("https://seed-us.example.test/v1/index/snapshot")
                self.assertEqual(captured.get("X-vool-meet-token"), "seed-token")
                client._get_json("https://seed-eu.example.test/v1/index/snapshot")
                self.assertEqual(captured.get("X-vool-meet-token"), "global-token")


if __name__ == "__main__":
    unittest.main()


def _clear_meet_tables() -> None:
    conn = get_connection()
    try:
        for table in (
            "presence_leases",
            "knowledge_tombstones",
            "index_deltas",
            "knowledge_manifests",
            "knowledge_holders",
            "meet_nodes",
            "meet_sync_state",
            "payment_status",
            "peer_endpoints",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
