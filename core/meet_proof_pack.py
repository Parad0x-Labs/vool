from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from core.meet_and_greet_models import IndexDeltaRecord, IndexSnapshotResponse, PresenceUpsertRequest
from core.meet_and_greet_replication import MeetAndGreetReplicator
from core.meet_and_greet_service import MeetAndGreetConfig, MeetAndGreetService
from storage.db import get_connection
from storage.meet_node_registry import upsert_meet_node


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProofScenarioResult:
    name: str
    passed: bool
    details: dict[str, object]


class _FakeRemoteClient:
    def __init__(self, snapshot: IndexSnapshotResponse, deltas: list[IndexDeltaRecord]) -> None:
        self.snapshot = snapshot
        self.deltas = deltas

    def fetch_snapshot(self, base_url: str, *, target_region: str | None, summary_mode: str) -> IndexSnapshotResponse:
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
        if since_created_at:
            return [row for row in self.deltas if row.created_at > since_created_at][:limit]
        return self.deltas[:limit]


def run_adversarial_proof_pack() -> list[ProofScenarioResult]:
    return [
        _duplicate_delta_idempotence(),
        _snapshot_partition_heal(),
    ]


def run_cross_region_convergence_proof() -> ProofScenarioResult:
    _clear_meet_state()
    eu = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))
    us = MeetAndGreetService(MeetAndGreetConfig(local_region="us"))
    apac = MeetAndGreetService(MeetAndGreetConfig(local_region="apac"))

    _seed_node(eu, "seed-eu-1", "https://seed-eu-1.vool.test", "eu")
    _seed_node(us, "seed-us-1", "https://seed-us-1.vool.test", "us")
    _seed_node(apac, "seed-apac-1", "https://seed-apac-1.vool.test", "apac")

    _presence(eu, "eu", "Atlas")
    _presence(us, "us", "Maria")
    _presence(apac, "apac", "Pipilon")

    _sync_pair(us, eu, remote_node_id="seed-eu-1")
    _sync_pair(apac, eu, remote_node_id="seed-eu-1")
    _sync_pair(eu, us, remote_node_id="seed-us-1")
    _sync_pair(apac, us, remote_node_id="seed-us-1")
    _sync_pair(eu, apac, remote_node_id="seed-apac-1")
    _sync_pair(us, apac, remote_node_id="seed-apac-1")

    eu_digest = _snapshot_digest(eu.get_snapshot(target_region="eu", summary_mode="regional_detail"))
    us_digest = _snapshot_digest(us.get_snapshot(target_region="us", summary_mode="regional_detail"))
    apac_digest = _snapshot_digest(apac.get_snapshot(target_region="apac", summary_mode="regional_detail"))
    passed = eu_digest == us_digest == apac_digest
    return ProofScenarioResult(
        name="cross_region_convergence",
        passed=passed,
        details={
            "eu_digest": eu_digest,
            "us_digest": us_digest,
            "apac_digest": apac_digest,
            "presence_counts": {
                "eu": len(eu.list_presence(limit=50)),
                "us": len(us.list_presence(limit=50)),
                "apac": len(apac.list_presence(limit=50)),
            },
        },
    )


def _duplicate_delta_idempotence() -> ProofScenarioResult:
    _clear_meet_state()
    service = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))
    peer_id = f"peer-{uuid.uuid4().hex}{uuid.uuid4().hex}"
    delta = IndexDeltaRecord(
        delta_id=f"delta-{uuid.uuid4().hex}",
        peer_id=peer_id,
        delta_type="presence_register",
        payload={
            "agent_id": peer_id,
            "agent_name": "ReplayCheck",
            "status": "idle",
            "capabilities": ["research"],
            "home_region": "eu",
            "current_region": "eu",
            "transport_mode": "wan_direct",
            "trust_score": 0.6,
            "timestamp": _now().isoformat(),
            "lease_seconds": 180,
            "endpoint": {"host": "198.51.100.10", "port": 49000, "source": "api"},
        },
        created_at=_now().isoformat(),
    )
    replicator = MeetAndGreetReplicator(service)
    replicator.apply_deltas([delta], remote_node_id="seed-eu-1", remote_region="eu")
    replicator.apply_deltas([delta], remote_node_id="seed-eu-1", remote_region="eu")
    rows = service.list_presence(limit=50)
    passed = len([row for row in rows if row.agent_id == peer_id]) == 1
    return ProofScenarioResult(
        name="duplicate_delta_idempotence",
        passed=passed,
        details={"presence_count": len(rows), "peer_id": peer_id},
    )


def _snapshot_partition_heal() -> ProofScenarioResult:
    _clear_meet_state()
    remote = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))
    local = MeetAndGreetService(MeetAndGreetConfig(local_region="eu"))
    _presence(remote, "eu", "Partitioned")
    snapshot = remote.get_snapshot(target_region="eu", summary_mode="regional_detail")
    replicator = MeetAndGreetReplicator(local, remote_client=_FakeRemoteClient(snapshot, []))
    result = replicator.sync_remote_node(remote_node_id="seed-eu-1", base_url="https://seed-eu-1.vool.test", force_snapshot=True)
    passed = result.mode == "snapshot" and len(local.list_presence(limit=50)) == len(remote.list_presence(limit=50))
    return ProofScenarioResult(
        name="snapshot_partition_heal",
        passed=passed,
        details={"mode": result.mode, "presence_count": len(local.list_presence(limit=50))},
    )


def _presence(service: MeetAndGreetService, region: str, name: str) -> None:
    peer_id = f"peer-{region}-{uuid.uuid4().hex}{uuid.uuid4().hex}"
    service.register_presence(
        PresenceUpsertRequest(
            agent_id=peer_id,
            agent_name=name,
            status="idle",
            capabilities=["research"],
            home_region=region,
            current_region=region,
            transport_mode="wan_direct",
            trust_score=0.6,
            timestamp=_now(),
            lease_seconds=300,
            endpoint={"host": f"203.0.113.{len(name)+10}", "port": 49000 + len(name), "source": "api"},
        )
    )


def _seed_node(service: MeetAndGreetService, node_id: str, base_url: str, region: str) -> None:
    upsert_meet_node(
        node_id=node_id,
        base_url=base_url,
        region=region,
        role="seed",
        platform_hint="linux",
        priority=10,
        status="active",
        metadata={},
        last_seen_at=_now().isoformat(),
    )


def _sync_pair(local: MeetAndGreetService, remote: MeetAndGreetService, *, remote_node_id: str) -> None:
    snapshot = remote.get_snapshot(target_region=local.config.local_region, summary_mode="regional_detail")
    snapshot = IndexSnapshotResponse(
        snapshot_cursor=snapshot.snapshot_cursor,
        source_region=snapshot.source_region,
        summary_mode=snapshot.summary_mode,
        meet_nodes=[],
        active_presence=snapshot.active_presence,
        knowledge_index=snapshot.knowledge_index,
        payment_status=snapshot.payment_status,
    )
    replicator = MeetAndGreetReplicator(local, remote_client=_FakeRemoteClient(snapshot, []))
    replicator.sync_remote_node(remote_node_id=remote_node_id, base_url="https://sync.example.test", force_snapshot=True)


def _snapshot_digest(snapshot: IndexSnapshotResponse) -> str:
    payload = {
        "meet_nodes": sorted(
            [item.model_dump(mode="json") for item in snapshot.meet_nodes],
            key=lambda row: str(row.get("node_id") or ""),
        ),
        "active_presence": sorted(
            [item.model_dump(mode="json") for item in snapshot.active_presence],
            key=lambda row: str(row.get("agent_id") or ""),
        ),
        "knowledge_index": sorted(
            [item.model_dump(mode="json") for item in snapshot.knowledge_index],
            key=lambda row: str(row.get("shard_id") or ""),
        ),
        "payment_status": sorted(
            [item.model_dump(mode="json") for item in snapshot.payment_status],
            key=lambda row: str(row.get("task_or_transfer_id") or ""),
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _clear_meet_state() -> None:
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
