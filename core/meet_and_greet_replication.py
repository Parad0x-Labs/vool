from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from core.discovery_index import register_peer_endpoint
from core.meet_and_greet_models import (
    IndexDeltaRecord,
    IndexSnapshotResponse,
    KnowledgeHolderRecord,
    KnowledgeIndexEntry,
    MeetNodeRegisterRequest,
    PaymentStatusUpsertRequest,
    PresenceUpsertRequest,
    PresenceWithdrawRequest,
)
from core.meet_and_greet_service import MeetAndGreetService
from network.knowledge_models import KnowledgeAdvert, KnowledgeRefresh, KnowledgeReplicaAd, KnowledgeWithdraw
from storage.knowledge_index import add_index_delta
from storage.knowledge_manifests import upsert_manifest
from storage.meet_node_registry import get_meet_node, get_sync_state
from storage.replica_table import mark_holder_withdrawn, upsert_holder


@dataclass
class ReplicationConfig:
    request_timeout_seconds: int = 10
    max_delta_rows: int = 1000
    prefer_delta_sync: bool = True
    seed_snapshot_on_first_sync: bool = True
    local_region: str = "global"
    cross_region_summary_only: bool = True
    cross_region_force_snapshot: bool = True
    tls_ca_file: str | None = None
    tls_insecure_skip_verify: bool = False
    auth_token: str | None = None
    auth_tokens_by_base_url: dict[str, str] = field(default_factory=dict)


@dataclass
class ReplicationResult:
    remote_node_id: str
    mode: str
    applied_presence: int = 0
    applied_knowledge_entries: int = 0
    applied_payment_markers: int = 0
    applied_meet_nodes: int = 0
    applied_deltas: int = 0
    snapshot_cursor: str | None = None
    delta_cursor: str | None = None
    remote_region: str | None = None
    summary_mode: str = "regional_detail"


class RemoteMeetClient(Protocol):
    def fetch_snapshot(
        self,
        base_url: str,
        *,
        target_region: str | None,
        summary_mode: str,
    ) -> IndexSnapshotResponse:
        ...

    def fetch_deltas(
        self,
        base_url: str,
        *,
        since_created_at: str | None,
        limit: int,
        target_region: str | None,
        summary_mode: str,
    ) -> list[IndexDeltaRecord]:
        ...


class HttpMeetClient:
    def __init__(
        self,
        *,
        timeout_seconds: int = 10,
        tls_ca_file: str | None = None,
        tls_insecure_skip_verify: bool = False,
        auth_token: str | None = None,
        auth_tokens_by_base_url: dict[str, str] | None = None,
    ) -> None:
        self.timeout_seconds = int(timeout_seconds)
        self.tls_ca_file = str(tls_ca_file or "").strip() or None
        self.tls_insecure_skip_verify = bool(tls_insecure_skip_verify)
        self.auth_token = str(auth_token or "").strip() or None
        self.auth_tokens_by_base_url = {
            _normalize_base_url(base): str(token).strip()
            for base, token in dict(auth_tokens_by_base_url or {}).items()
            if str(base).strip() and str(token).strip()
        }

    def fetch_snapshot(
        self,
        base_url: str,
        *,
        target_region: str | None,
        summary_mode: str,
    ) -> IndexSnapshotResponse:
        query = self._query(target_region=target_region, summary_mode=summary_mode)
        url = f"{base_url.rstrip('/')}/v1/index/snapshot"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = self._get_json(url)
        return IndexSnapshotResponse.model_validate(payload["result"])

    def fetch_deltas(
        self,
        base_url: str,
        *,
        since_created_at: str | None,
        limit: int,
        target_region: str | None,
        summary_mode: str,
    ) -> list[IndexDeltaRecord]:
        query = self._query(target_region=target_region, summary_mode=summary_mode)
        query["limit"] = str(int(limit))
        if since_created_at:
            query["since_created_at"] = since_created_at
        url = f"{base_url.rstrip('/')}/v1/index/deltas"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        payload = self._get_json(url)
        return [IndexDeltaRecord.model_validate(item) for item in payload["result"]]

    def _query(self, *, target_region: str | None, summary_mode: str) -> dict[str, str]:
        query: dict[str, str] = {"summary_mode": summary_mode}
        if target_region:
            query["target_region"] = target_region
        return query

    def _get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, method="GET")
        request.add_header("Content-Type", "application/json")
        auth_token = self._auth_token_for_url(url)
        if auth_token:
            request.add_header("X-Vool-Meet-Token", auth_token)
        context = self._ssl_context_for_url(url)
        # ONE outbound HTTP door (veto before any socket + per-turn reporting); the computed TLS
        # ``context`` is not forwarded, matching the other rebound lanes' door convention.
        from core.remote_fetch_policy import open_remote

        with open_remote(request, timeout=self.timeout_seconds) as response:
            obj = json.loads(response.read().decode("utf-8"))
        if not obj.get("ok"):
            raise ValueError(str(obj.get("error") or f"Remote request failed: {url}"))
        return obj

    def _auth_token_for_url(self, url: str) -> str | None:
        token = self.auth_tokens_by_base_url.get(_normalize_base_url(url))
        if token:
            return token
        return self.auth_token

    def _ssl_context_for_url(self, url: str) -> ssl.SSLContext | None:
        if not url.lower().startswith("https://"):
            return None
        if self.tls_insecure_skip_verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        if self.tls_ca_file:
            return ssl.create_default_context(cafile=self.tls_ca_file)
        return ssl.create_default_context()


class MeetAndGreetReplicator:
    def __init__(
        self,
        service: MeetAndGreetService,
        *,
        config: ReplicationConfig | None = None,
        remote_client: RemoteMeetClient | None = None,
    ) -> None:
        self.service = service
        self.config = config or ReplicationConfig(local_region=service.config.local_region)
        if not self.config.local_region:
            self.config.local_region = service.config.local_region
        self.remote_client = remote_client or HttpMeetClient(
            timeout_seconds=self.config.request_timeout_seconds,
            tls_ca_file=self.config.tls_ca_file,
            tls_insecure_skip_verify=self.config.tls_insecure_skip_verify,
            auth_token=self.config.auth_token,
            auth_tokens_by_base_url=self.config.auth_tokens_by_base_url,
        )

    def sync_remote_node(self, *, remote_node_id: str, base_url: str, force_snapshot: bool = False) -> ReplicationResult:
        sync_state = get_sync_state(remote_node_id) or {}
        remote_region = self._remote_region(remote_node_id)
        summary_mode = self._summary_mode_for_region(remote_region)
        try:
            need_snapshot = force_snapshot or not self.config.prefer_delta_sync
            if self.config.seed_snapshot_on_first_sync and not (sync_state.get("last_snapshot_cursor") or sync_state.get("last_delta_cursor")):
                need_snapshot = True
            if self.config.cross_region_force_snapshot and summary_mode == "global_summary":
                need_snapshot = True

            if need_snapshot:
                snapshot = self.remote_client.fetch_snapshot(
                    base_url,
                    target_region=self.config.local_region,
                    summary_mode=summary_mode,
                )
                return self.apply_snapshot(snapshot, remote_node_id=remote_node_id, remote_region=remote_region)

            deltas = self.remote_client.fetch_deltas(
                base_url,
                since_created_at=str(sync_state.get("last_delta_cursor") or sync_state.get("last_snapshot_cursor") or ""),
                limit=self.config.max_delta_rows,
                target_region=self.config.local_region,
                summary_mode=summary_mode,
            )
            if not deltas and sync_state.get("last_snapshot_cursor"):
                self.service.update_sync_state(remote_node_id=remote_node_id, last_error=None)
                return ReplicationResult(
                    remote_node_id=remote_node_id,
                    remote_region=remote_region,
                    mode="delta",
                    summary_mode=summary_mode,
                    snapshot_cursor=str(sync_state.get("last_snapshot_cursor")),
                    delta_cursor=str(sync_state.get("last_delta_cursor")),
                )
            if deltas:
                return self.apply_deltas(deltas, remote_node_id=remote_node_id, remote_region=remote_region)

            snapshot = self.remote_client.fetch_snapshot(
                base_url,
                target_region=self.config.local_region,
                summary_mode=summary_mode,
            )
            return self.apply_snapshot(snapshot, remote_node_id=remote_node_id, remote_region=remote_region)
        except Exception as exc:
            self.service.update_sync_state(remote_node_id=remote_node_id, last_error=str(exc))
            raise

    def sync_registered_nodes(self, *, exclude_node_id: str | None = None, limit: int = 32) -> list[ReplicationResult]:
        results: list[ReplicationResult] = []
        for node in self.service.list_meet_nodes(limit=limit, active_only=True):
            if exclude_node_id and node.node_id == exclude_node_id:
                continue
            results.append(self.sync_remote_node(remote_node_id=node.node_id, base_url=node.base_url))
        return results

    def apply_snapshot(
        self,
        snapshot: IndexSnapshotResponse,
        *,
        remote_node_id: str,
        remote_region: str | None = None,
    ) -> ReplicationResult:
        applied_meet_nodes = 0
        for node in snapshot.meet_nodes:
            self.service.register_meet_node(
                MeetNodeRegisterRequest(
                    node_id=node.node_id,
                    base_url=node.base_url,
                    region=node.region,
                    role=node.role,
                    platform_hint=node.platform_hint,
                    priority=node.priority,
                    status=node.status,
                    metadata=node.metadata,
                )
            )
            applied_meet_nodes += 1

        applied_presence = 0
        for record in snapshot.active_presence:
            self.service.heartbeat_presence(
                PresenceUpsertRequest(
                    agent_id=record.agent_id,
                    agent_name=record.agent_name,
                    status=record.status,
                    capabilities=record.capabilities,
                    home_region=record.home_region,
                    current_region=record.current_region,
                    transport_mode=record.transport_mode,
                    trust_score=record.trust_score,
                    timestamp=_parse_dt(record.last_heartbeat_at),
                    lease_seconds=max(30, _lease_seconds(record.lease_expires_at, record.last_heartbeat_at)),
                    endpoint=record.endpoint,
                )
            )
            applied_presence += 1

        applied_knowledge_entries = 0
        for entry in snapshot.knowledge_index:
            self._merge_knowledge_entry(entry, remote_node_id=remote_node_id, source_region=snapshot.source_region)
            applied_knowledge_entries += 1

        applied_payment_markers = 0
        for record in snapshot.payment_status:
            self.service.upsert_payment_status(
                PaymentStatusUpsertRequest(
                    task_or_transfer_id=record.task_or_transfer_id,
                    payer_peer_id=record.payer_peer_id,
                    payee_peer_id=record.payee_peer_id,
                    status=record.status,
                    receipt_reference=record.receipt_reference,
                    metadata=record.metadata,
                )
            )
            applied_payment_markers += 1

        self.service.update_sync_state(
            remote_node_id=remote_node_id,
            last_snapshot_cursor=snapshot.snapshot_cursor,
            last_delta_cursor=snapshot.snapshot_cursor,
            last_error=None,
        )
        return ReplicationResult(
            remote_node_id=remote_node_id,
            remote_region=remote_region or snapshot.source_region,
            mode="snapshot",
            summary_mode=snapshot.summary_mode,
            applied_presence=applied_presence,
            applied_knowledge_entries=applied_knowledge_entries,
            applied_payment_markers=applied_payment_markers,
            applied_meet_nodes=applied_meet_nodes,
            snapshot_cursor=snapshot.snapshot_cursor,
            delta_cursor=snapshot.snapshot_cursor,
        )

    def apply_deltas(
        self,
        deltas: list[IndexDeltaRecord],
        *,
        remote_node_id: str,
        remote_region: str | None = None,
    ) -> ReplicationResult:
        applied_presence = 0
        applied_knowledge_entries = 0
        applied_payment_markers = 0
        applied_meet_nodes = 0
        latest_cursor: str | None = None

        for delta in deltas:
            latest_cursor = delta.created_at
            if delta.delta_type in {"presence_register", "presence_heartbeat"}:
                request = PresenceUpsertRequest.model_validate(delta.payload)
                if delta.delta_type == "presence_register":
                    self.service.register_presence(request)
                else:
                    self.service.heartbeat_presence(request)
                applied_presence += 1
            elif delta.delta_type == "presence_withdraw":
                self.service.withdraw_presence(PresenceWithdrawRequest.model_validate(delta.payload))
                applied_presence += 1
            elif delta.delta_type == "knowledge_withdraw":
                self.service.withdraw_knowledge(KnowledgeWithdraw.model_validate(delta.payload))
                applied_knowledge_entries += 1
            elif delta.delta_type == "knowledge_refresh":
                self.service.refresh_knowledge(KnowledgeRefresh.model_validate(delta.payload))
                applied_knowledge_entries += 1
            elif delta.delta_type == "knowledge_replica_ad":
                self.service.replicate_knowledge(KnowledgeReplicaAd.model_validate(delta.payload))
                applied_knowledge_entries += 1
            elif delta.delta_type == "knowledge_ad":
                self.service.advertise_knowledge(KnowledgeAdvert.model_validate(delta.payload))
                applied_knowledge_entries += 1
            elif delta.delta_type == "payment_status":
                self.service.upsert_payment_status(PaymentStatusUpsertRequest.model_validate(delta.payload))
                applied_payment_markers += 1
            elif delta.delta_type == "meet_node_register":
                self.service.register_meet_node(MeetNodeRegisterRequest.model_validate(delta.payload))
                applied_meet_nodes += 1

        self.service.update_sync_state(
            remote_node_id=remote_node_id,
            last_delta_cursor=latest_cursor,
            last_error=None,
        )
        return ReplicationResult(
            remote_node_id=remote_node_id,
            remote_region=remote_region,
            mode="delta",
            summary_mode="regional_detail",
            applied_presence=applied_presence,
            applied_knowledge_entries=applied_knowledge_entries,
            applied_payment_markers=applied_payment_markers,
            applied_meet_nodes=applied_meet_nodes,
            applied_deltas=len(deltas),
            delta_cursor=latest_cursor,
        )

    def _merge_knowledge_entry(self, entry: KnowledgeIndexEntry, *, remote_node_id: str, source_region: str) -> None:
        metadata = dict(entry.metadata)
        metadata["_index_priority_region"] = entry.priority_region
        metadata["_index_region_replication_counts"] = entry.region_replication_counts
        metadata["_index_summary_mode"] = entry.summary_mode
        metadata["_index_source_region"] = source_region
        upsert_manifest(
            manifest_id=entry.manifest_id,
            shard_id=entry.shard_id,
            content_hash=entry.content_hash,
            version=entry.version,
            topic_tags=entry.topic_tags,
            summary_digest=entry.summary_digest,
            size_bytes=entry.size_bytes,
            metadata=metadata,
        )
        for holder in entry.holders:
            self._merge_holder(entry, holder, remote_node_id=remote_node_id)

    def _merge_holder(self, entry: KnowledgeIndexEntry, holder: KnowledgeHolderRecord, *, remote_node_id: str) -> None:
        if holder.endpoint:
            register_peer_endpoint(holder.holder_peer_id, holder.endpoint.host, holder.endpoint.port, source=holder.endpoint.source)
        upsert_holder(
            shard_id=entry.shard_id,
            holder_peer_id=holder.holder_peer_id,
            home_region=holder.home_region,
            content_hash=entry.content_hash,
            version=holder.version,
            freshness_ts=holder.freshness_ts,
            expires_at=holder.expires_at,
            access_mode=holder.access_mode,
            fetch_route=holder.fetch_route,
            trust_weight=holder.trust_weight,
            status=holder.status,
            source=f"replicated:{remote_node_id}",
        )
        if holder.status != "active":
            mark_holder_withdrawn(entry.shard_id, holder.holder_peer_id, status=holder.status)
        add_index_delta(
            delta_id=f"replicated-{remote_node_id}-{entry.shard_id[:24]}-{holder.holder_peer_id[:24]}-{holder.version}",
            delta_type="knowledge_ad",
            payload={
                "shard_id": entry.shard_id,
                "content_hash": entry.content_hash,
                "version": holder.version,
                "holder_peer_id": holder.holder_peer_id,
                "home_region": holder.home_region,
                "topic_tags": entry.topic_tags,
                "summary_digest": entry.summary_digest,
                "size_bytes": entry.size_bytes,
                "freshness_ts": holder.freshness_ts,
                "ttl_seconds": max(30, _lease_seconds(holder.expires_at, holder.freshness_ts)),
                "trust_weight": holder.trust_weight,
                "access_mode": holder.access_mode,
                "fetch_methods": [str(holder.fetch_route.get("method") or "request_shard")],
                "fetch_route": holder.fetch_route,
                "metadata": entry.metadata,
                "manifest_id": entry.manifest_id,
            },
            peer_id=holder.holder_peer_id,
        )

    def _remote_region(self, remote_node_id: str) -> str | None:
        node = get_meet_node(remote_node_id)
        if not node:
            return None
        return str(node.get("region") or "global")

    def _summary_mode_for_region(self, remote_region: str | None) -> str:
        if not remote_region or remote_region == self.config.local_region:
            return "regional_detail"
        if self.config.cross_region_summary_only:
            return "global_summary"
        return "regional_detail"


def _normalize_base_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _lease_seconds(expires_at: str, reference_ts: str) -> int:
    try:
        expiry = datetime.fromisoformat(expires_at)
        reference = datetime.fromisoformat(reference_ts)
        seconds = int((expiry - reference).total_seconds())
        return min(3600, max(30, seconds))
    except Exception:
        return 180
