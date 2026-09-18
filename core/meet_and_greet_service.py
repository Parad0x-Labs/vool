from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from core.discovery_index import (
    delivery_targets_for_peer,
    record_verified_peer_endpoint_proof,
    register_peer_endpoint,
    upsert_peer_minimal,
)
from core.knowledge_freshness import utcnow
from core.knowledge_possession_challenge import (
    issue_knowledge_possession_challenge,
    respond_to_knowledge_possession_challenge,
    verify_knowledge_possession_response,
)
from core.meet_and_greet_models import (
    HealthResponse,
    IndexDeltaRecord,
    IndexSnapshotResponse,
    KnowledgeChallengeIssueRequest,
    KnowledgeChallengeRecord,
    KnowledgeChallengeResponse,
    KnowledgeChallengeResponseRequest,
    KnowledgeChallengeVerifyRequest,
    KnowledgeHolderRecord,
    KnowledgeIndexEntry,
    KnowledgeSearchRequest,
    MeetNodeRecord,
    MeetNodeRegisterRequest,
    MeetSyncStateRecord,
    PaymentStatusRecord,
    PaymentStatusUpsertRequest,
    PeerEndpointRecord,
    PresenceRecord,
    PresenceUpsertRequest,
    PresenceWithdrawRequest,
)
from network.knowledge_models import (
    HelloAd,
    KnowledgeAdvert,
    KnowledgeRefresh,
    KnowledgeReplicaAd,
    KnowledgeWithdraw,
    PresenceHeartbeat,
)
from network.knowledge_router import handle_knowledge_message
from network.presence_router import handle_presence_message
from storage.knowledge_index import (
    active_presence,
    add_index_delta,
    latest_index_cursor,
    list_index_deltas,
    presence_for_peer,
    withdraw_presence_lease,
)
from storage.knowledge_manifests import all_manifests, manifest_for_shard
from storage.meet_node_registry import (
    get_meet_node,
    get_sync_state,
    list_meet_nodes,
    list_sync_state,
    upsert_meet_node,
    upsert_sync_state,
)
from storage.payment_status import get_payment_status, list_payment_status, upsert_payment_status
from storage.replica_table import holders_for_shard

SummaryMode = Literal["regional_detail", "global_summary"]


@dataclass
class MeetAndGreetConfig:
    local_region: str = "global"
    cross_region_holder_limit: int = 1
    max_presence_rows: int = 512
    max_index_rows: int = 1000
    max_delta_rows: int = 1000
    max_payment_rows: int = 500
    max_meet_nodes: int = 128


class MeetAndGreetService:
    def __init__(self, config: MeetAndGreetConfig | None = None) -> None:
        self.config = config or MeetAndGreetConfig()

    def register_presence(
        self,
        request: PresenceUpsertRequest,
        *,
        request_meta: dict[str, Any] | None = None,
    ) -> PresenceRecord:
        upsert_peer_minimal(request.agent_id)
        _persist_presence_endpoints(request.agent_id, request, request_meta=request_meta)
        payload = HelloAd(
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            status=request.status,
            capabilities=request.capabilities,
            home_region=request.home_region,
            current_region=request.current_region or request.home_region,
            transport_mode=request.transport_mode,
            trust_score=request.trust_score,
            timestamp=request.timestamp,
            lease_seconds=request.lease_seconds,
        )
        result = handle_presence_message("HELLO_AD", payload)
        if not result.ok:
            raise ValueError(result.reason)
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="presence_register",
            payload=request.model_dump(mode="json"),
            peer_id=request.agent_id,
        )
        return self.get_presence_record(request.agent_id)

    def heartbeat_presence(
        self,
        request: PresenceUpsertRequest,
        *,
        request_meta: dict[str, Any] | None = None,
    ) -> PresenceRecord:
        upsert_peer_minimal(request.agent_id)
        _persist_presence_endpoints(request.agent_id, request, request_meta=request_meta)
        payload = PresenceHeartbeat(
            agent_id=request.agent_id,
            agent_name=request.agent_name,
            status=request.status,
            capabilities=request.capabilities,
            home_region=request.home_region,
            current_region=request.current_region or request.home_region,
            transport_mode=request.transport_mode,
            trust_score=request.trust_score,
            timestamp=request.timestamp,
            lease_seconds=request.lease_seconds,
        )
        result = handle_presence_message("PRESENCE_HEARTBEAT", payload)
        if not result.ok:
            raise ValueError(result.reason)
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="presence_heartbeat",
            payload=request.model_dump(mode="json"),
            peer_id=request.agent_id,
        )
        return self.get_presence_record(request.agent_id)

    def withdraw_presence(self, request: PresenceWithdrawRequest) -> dict[str, Any]:
        withdraw_presence_lease(request.agent_id, withdrawn_at=request.timestamp.isoformat())
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="presence_withdraw",
            payload=request.model_dump(mode="json"),
            peer_id=request.agent_id,
        )
        return {
            "agent_id": request.agent_id,
            "status": "offline",
            "withdrawn_at": request.timestamp.isoformat(),
            "reason": request.reason,
        }

    def advertise_knowledge(self, request: KnowledgeAdvert) -> KnowledgeIndexEntry:
        result = handle_knowledge_message("KNOWLEDGE_AD", request)
        if not result.ok:
            raise ValueError(result.reason)
        return self.get_knowledge_entry(request.shard_id)

    def replicate_knowledge(self, request: KnowledgeReplicaAd) -> KnowledgeIndexEntry:
        result = handle_knowledge_message("KNOWLEDGE_REPLICA_AD", request)
        if not result.ok:
            raise ValueError(result.reason)
        return self.get_knowledge_entry(request.shard_id)

    def refresh_knowledge(self, request: KnowledgeRefresh) -> KnowledgeIndexEntry:
        result = handle_knowledge_message("KNOWLEDGE_REFRESH", request)
        if not result.ok:
            raise ValueError(result.reason)
        return self.get_knowledge_entry(request.shard_id)

    def withdraw_knowledge(self, request: KnowledgeWithdraw) -> dict[str, Any]:
        result = handle_knowledge_message("KNOWLEDGE_WITHDRAW", request)
        if not result.ok:
            raise ValueError(result.reason)
        return {
            "shard_id": request.shard_id,
            "holder_peer_id": request.holder_peer_id,
            "status": "withdrawn",
            "reason": request.reason,
        }

    def issue_knowledge_challenge(self, request: KnowledgeChallengeIssueRequest) -> KnowledgeChallengeRecord:
        return issue_knowledge_possession_challenge(request)

    def respond_knowledge_challenge(self, request: KnowledgeChallengeResponseRequest) -> KnowledgeChallengeResponse:
        return respond_to_knowledge_possession_challenge(request)

    def verify_knowledge_challenge(self, request: KnowledgeChallengeVerifyRequest) -> KnowledgeChallengeRecord:
        return verify_knowledge_possession_response(request)

    def get_presence_record(
        self,
        agent_id: str,
        *,
        target_region: str | None = None,
        summary_mode: SummaryMode = "regional_detail",
    ) -> PresenceRecord:
        row = presence_for_peer(agent_id)
        if row:
            return self._presence_record_from_row(row, target_region=target_region, summary_mode=summary_mode)
        endpoint = _endpoint_model(agent_id)
        home_region = target_region or self.config.local_region
        return PresenceRecord(
            agent_id=agent_id,
            agent_name=None,
            status="offline",
            capabilities=[],
            home_region=home_region,
            current_region=home_region,
            transport_mode="unknown",
            trust_score=0.0,
            last_heartbeat_at=utcnow().isoformat(),
            lease_expires_at=utcnow().isoformat(),
            endpoint=endpoint if summary_mode == "regional_detail" else None,
            endpoints=_endpoint_models(agent_id) if summary_mode == "regional_detail" else [],
            summary_only=summary_mode == "global_summary",
        )

    def list_presence(
        self,
        *,
        limit: int | None = None,
        target_region: str | None = None,
        summary_mode: SummaryMode = "regional_detail",
    ) -> list[PresenceRecord]:
        rows = active_presence(limit=limit or self.config.max_presence_rows)
        return [
            self._presence_record_from_row(row, target_region=target_region, summary_mode=summary_mode)
            for row in rows
        ]

    def get_knowledge_entry(
        self,
        shard_id: str,
        *,
        target_region: str | None = None,
        summary_mode: SummaryMode = "regional_detail",
    ) -> KnowledgeIndexEntry:
        manifest = manifest_for_shard(shard_id)
        if not manifest:
            raise KeyError(f"Unknown shard_id: {shard_id}")
        return self._knowledge_entry_from_manifest(
            manifest,
            target_region=target_region,
            summary_mode=summary_mode,
        )

    def list_knowledge_index(
        self,
        *,
        limit: int | None = None,
        target_region: str | None = None,
        summary_mode: SummaryMode = "regional_detail",
    ) -> list[KnowledgeIndexEntry]:
        manifests = all_manifests(limit=limit or self.config.max_index_rows)
        entries = [
            self._knowledge_entry_from_manifest(
                manifest,
                target_region=target_region,
                summary_mode=summary_mode,
            )
            for manifest in manifests
        ]
        entries.sort(
            key=lambda item: (
                item.live_holder_count,
                item.replication_count,
                item.latest_freshness or "",
            ),
            reverse=True,
        )
        return entries[: limit or self.config.max_index_rows]

    def search_knowledge(self, request: KnowledgeSearchRequest) -> list[KnowledgeIndexEntry]:
        query_tokens = _query_tokens(request.query_text, request.problem_class, request.topic_tags)
        preferred_region = request.preferred_region or self.config.local_region
        matches: list[tuple[float, KnowledgeIndexEntry]] = []
        for entry in self.list_knowledge_index(
            limit=self.config.max_index_rows,
            target_region=preferred_region,
            summary_mode=request.summary_mode,
        ):
            entry_tokens = {
                *(tag.lower() for tag in entry.topic_tags),
                str(entry.metadata.get("problem_class") or "").lower(),
                str(entry.summary_digest).lower(),
            }
            overlap = len(query_tokens & {token for token in entry_tokens if token})
            holder_trust = max((holder.trust_weight for holder in entry.holders), default=0.0)
            if query_tokens and overlap <= 0:
                continue
            if holder_trust < request.min_trust_weight:
                continue
            region_bonus = 0.0
            if entry.priority_region == preferred_region:
                region_bonus = 0.35
            elif preferred_region in entry.region_replication_counts:
                region_bonus = 0.15
            score = float(overlap) + float(holder_trust) + (0.15 * float(entry.live_holder_count)) + region_bonus
            matches.append((score, entry))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in matches[: request.limit]]

    def upsert_payment_status(self, request: PaymentStatusUpsertRequest) -> PaymentStatusRecord:
        upsert_payment_status(
            task_or_transfer_id=request.task_or_transfer_id,
            payer_peer_id=request.payer_peer_id,
            payee_peer_id=request.payee_peer_id,
            status=request.status,
            receipt_reference=request.receipt_reference,
            metadata=request.metadata,
        )
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="payment_status",
            payload=request.model_dump(mode="json"),
            peer_id=request.payee_peer_id,
        )
        row = get_payment_status(request.task_or_transfer_id)
        if not row:
            raise KeyError(request.task_or_transfer_id)
        return PaymentStatusRecord.model_validate(row)

    def list_payment_status(self, *, limit: int | None = None) -> list[PaymentStatusRecord]:
        return [
            PaymentStatusRecord.model_validate(row)
            for row in list_payment_status(limit=limit or self.config.max_payment_rows)
        ]

    def register_meet_node(self, request: MeetNodeRegisterRequest) -> MeetNodeRecord:
        upsert_meet_node(
            node_id=request.node_id,
            base_url=request.base_url,
            region=request.region,
            role=request.role,
            platform_hint=request.platform_hint,
            priority=request.priority,
            status=request.status,
            metadata=request.metadata,
            last_seen_at=utcnow().isoformat(),
        )
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="meet_node_register",
            payload=request.model_dump(mode="json"),
            peer_id=request.node_id,
        )
        node = get_meet_node(request.node_id)
        if not node:
            raise KeyError(request.node_id)
        return MeetNodeRecord.model_validate(node)

    def list_meet_nodes(self, *, limit: int | None = None, active_only: bool = True) -> list[MeetNodeRecord]:
        return [
            MeetNodeRecord.model_validate(row)
            for row in list_meet_nodes(active_only=active_only, limit=limit or self.config.max_meet_nodes)
        ]

    def update_sync_state(
        self,
        *,
        remote_node_id: str,
        last_snapshot_cursor: str | None = None,
        last_delta_cursor: str | None = None,
        last_error: str | None = None,
    ) -> MeetSyncStateRecord:
        upsert_sync_state(
            remote_node_id=remote_node_id,
            last_snapshot_cursor=last_snapshot_cursor,
            last_delta_cursor=last_delta_cursor,
            last_sync_at=utcnow().isoformat(),
            last_error=last_error,
        )
        row = get_sync_state(remote_node_id)
        if not row:
            raise KeyError(remote_node_id)
        return MeetSyncStateRecord.model_validate(row)

    def list_sync_state(self, *, limit: int | None = None) -> list[MeetSyncStateRecord]:
        return [
            MeetSyncStateRecord.model_validate(row)
            for row in list_sync_state(limit=limit or self.config.max_meet_nodes)
        ]

    def get_deltas(self, *, since_created_at: str | None = None, limit: int | None = None) -> list[IndexDeltaRecord]:
        rows = list_index_deltas(
            since_created_at=since_created_at,
            limit=limit or self.config.max_delta_rows,
        )
        return [IndexDeltaRecord.model_validate(row) for row in rows]

    def get_snapshot(
        self,
        *,
        target_region: str | None = None,
        summary_mode: SummaryMode = "regional_detail",
    ) -> IndexSnapshotResponse:
        return IndexSnapshotResponse(
            snapshot_cursor=latest_index_cursor(),
            source_region=self.config.local_region,
            summary_mode=summary_mode,
            meet_nodes=self.list_meet_nodes(limit=self.config.max_meet_nodes, active_only=False),
            active_presence=self.list_presence(
                limit=self.config.max_presence_rows,
                target_region=target_region,
                summary_mode=summary_mode,
            ),
            knowledge_index=self.list_knowledge_index(
                limit=self.config.max_index_rows,
                target_region=target_region,
                summary_mode=summary_mode,
            ),
            payment_status=self.list_payment_status(limit=self.config.max_payment_rows),
        )

    def health(self) -> HealthResponse:
        snapshot = self.get_snapshot()
        return HealthResponse(
            service="meet_and_greet",
            status="ok",
            active_presence_count=len(snapshot.active_presence),
            knowledge_entry_count=len(snapshot.knowledge_index),
            payment_marker_count=len(snapshot.payment_status),
            snapshot_cursor=snapshot.snapshot_cursor,
        )

    def _presence_record_from_row(
        self,
        row: dict[str, Any],
        *,
        target_region: str | None,
        summary_mode: SummaryMode,
    ) -> PresenceRecord:
        detail_region = target_region or self.config.local_region
        home_region = str(row.get("home_region") or "global")
        current_region = str(row.get("current_region") or home_region)
        summary_only = summary_mode == "global_summary" and not _is_local_region_match(
            detail_region,
            home_region=home_region,
            current_region=current_region,
        )
        return PresenceRecord(
            agent_id=row["peer_id"],
            agent_name=row.get("agent_name"),
            status=row["status"],
            capabilities=list(row.get("capabilities") or []),
            home_region=home_region,
            current_region=current_region,
            transport_mode=row["transport_mode"],
            trust_score=float(row["trust_score"]),
            last_heartbeat_at=row["last_heartbeat_at"],
            lease_expires_at=row["lease_expires_at"],
            endpoint=None if summary_only else _endpoint_model(row["peer_id"]),
            endpoints=[] if summary_only else _endpoint_models(row["peer_id"]),
            summary_only=summary_only,
        )

    def _knowledge_entry_from_manifest(
        self,
        manifest: dict[str, Any],
        *,
        target_region: str | None,
        summary_mode: SummaryMode,
    ) -> KnowledgeIndexEntry:
        detail_region = target_region or self.config.local_region
        metadata = dict(manifest.get("metadata") or {})
        holders = holders_for_shard(manifest["shard_id"], active_only=False)
        actual_region_counts: dict[str, int] = defaultdict(int)
        metadata_region_counts = {
            str(region): int(count)
            for region, count in dict(metadata.get("_index_region_replication_counts") or {}).items()
        }
        live_holder_count = 0
        stale_holder_count = 0
        latest_freshness: str | None = None
        holder_records: list[KnowledgeHolderRecord] = []
        remote_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for holder in holders:
            status = str(holder["status"])
            home_region = str(holder.get("home_region") or metadata.get("home_region") or "global")
            freshness = str(holder["freshness_ts"])
            if latest_freshness is None or freshness > latest_freshness:
                latest_freshness = freshness
            if status == "active":
                live_holder_count += 1
                actual_region_counts[home_region] += 1
            else:
                stale_holder_count += 1
            if summary_mode == "global_summary" and not _is_local_region_match(
                detail_region,
                home_region=home_region,
                current_region=home_region,
            ):
                remote_groups[home_region].append(holder)
                continue
            holder_records.append(self._holder_record(manifest["shard_id"], holder, home_region=home_region))

        region_replication_counts = dict(metadata_region_counts)
        for region, count in actual_region_counts.items():
            region_replication_counts[region] = max(region_replication_counts.get(region, 0), count)

        if summary_mode == "global_summary":
            for home_region, grouped in remote_groups.items():
                grouped.sort(
                    key=lambda holder: (
                        str(holder["status"]) == "active",
                        float(holder["trust_weight"]),
                        str(holder["freshness_ts"]),
                    ),
                    reverse=True,
                )
                for holder in grouped[: max(1, self.config.cross_region_holder_limit)]:
                    holder_records.append(
                        self._holder_record(
                            manifest["shard_id"],
                            holder,
                            home_region=home_region,
                            summary_only=True,
                        )
                    )

        priority_region = str(metadata.get("_index_priority_region") or "")
        if detail_region in region_replication_counts:
            priority_region = detail_region
        elif region_replication_counts:
            priority_region = max(
                region_replication_counts.items(),
                key=lambda item: (item[1], item[0] == self.config.local_region, item[0]),
            )[0]
        elif not priority_region:
            priority_region = str(metadata.get("home_region") or self.config.local_region)

        estimated_live_count = sum(region_replication_counts.values()) if region_replication_counts else live_holder_count
        return KnowledgeIndexEntry(
            manifest_id=manifest["manifest_id"],
            shard_id=manifest["shard_id"],
            content_hash=manifest["content_hash"],
            version=int(manifest["version"]),
            topic_tags=list(manifest.get("topic_tags") or []),
            summary_digest=str(manifest["summary_digest"]),
            size_bytes=int(manifest["size_bytes"]),
            metadata=metadata,
            latest_freshness=latest_freshness,
            replication_count=max(len(holders), estimated_live_count + stale_holder_count),
            live_holder_count=max(live_holder_count, estimated_live_count),
            stale_holder_count=stale_holder_count,
            priority_region=priority_region or None,
            region_replication_counts=region_replication_counts,
            summary_mode=summary_mode,
            holders=holder_records,
        )

    def _holder_record(
        self,
        shard_id: str,
        holder: dict[str, Any],
        *,
        home_region: str,
        summary_only: bool = False,
    ) -> KnowledgeHolderRecord:
        fetch_route = dict(holder.get("fetch_route") or {})
        if summary_only:
            fetch_route = {"method": "meet_lookup", "region": home_region, "shard_id": shard_id}
        return KnowledgeHolderRecord(
            holder_peer_id=holder["holder_peer_id"],
            home_region=home_region,
            version=int(holder["version"]),
            freshness_ts=str(holder["freshness_ts"]),
            expires_at=str(holder["expires_at"]),
            trust_weight=float(holder["trust_weight"]),
            access_mode=str(holder["access_mode"]),
            fetch_route=fetch_route,
            status=str(holder["status"]),
            endpoint=None if summary_only else _endpoint_model(holder["holder_peer_id"]),
            summary_only=summary_only,
        )


def _endpoint_model(peer_id: str) -> PeerEndpointRecord | None:
    targets = delivery_targets_for_peer(peer_id, verified_limit=1, include_candidates=False)
    if not targets:
        return None
    endpoint = targets[0]
    return PeerEndpointRecord(host=endpoint.host, port=int(endpoint.port), source=endpoint.source)


def _endpoint_models(peer_id: str, *, limit: int = 4) -> list[PeerEndpointRecord]:
    return [
        PeerEndpointRecord(host=endpoint.host, port=int(endpoint.port), source=endpoint.source)
        for endpoint in delivery_targets_for_peer(
            peer_id,
            verified_limit=limit,
            include_candidates=False,
        )
    ]


def _normalized_request_endpoints(request: PresenceUpsertRequest) -> list[PeerEndpointRecord]:
    endpoints = list(request.endpoints or [])
    if request.endpoint is not None:
        endpoints = [request.endpoint, *endpoints]
    deduped: list[PeerEndpointRecord] = []
    seen: set[tuple[str, int]] = set()
    for endpoint in endpoints:
        key = (endpoint.host, int(endpoint.port))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(PeerEndpointRecord(host=endpoint.host, port=int(endpoint.port), source="api"))
    return deduped


def _persist_presence_endpoints(
    agent_id: str,
    request: PresenceUpsertRequest,
    *,
    request_meta: dict[str, Any] | None,
) -> None:
    endpoints = _normalized_request_endpoints(request)
    if not endpoints:
        return
    meta = dict(request_meta or {})
    for endpoint in endpoints:
        register_peer_endpoint(agent_id, endpoint.host, int(endpoint.port), source="api")
        if not meta.get("signature") or not meta.get("proof_hash"):
            continue
        record_verified_peer_endpoint_proof(
            agent_id,
            endpoint.host,
            int(endpoint.port),
            source="api",
            verification_kind="signed_api_write",
            proof_message_id=str(meta.get("nonce") or ""),
            proof_message_type=str(meta.get("target_path") or "/v1/presence/register"),
            proof_hash=str(meta.get("proof_hash") or ""),
            proof_signature=str(meta.get("signature") or ""),
            proof_timestamp=str(meta.get("timestamp") or ""),
        )


def _is_local_region_match(detail_region: str | None, *, home_region: str, current_region: str | None) -> bool:
    if not detail_region:
        return True
    return detail_region in {home_region, current_region or home_region}


def _query_tokens(query_text: str | None, problem_class: str | None, topic_tags: list[str]) -> set[str]:
    parts = [query_text or "", problem_class or "", *topic_tags]
    tokens: set[str] = set()
    for part in parts:
        for token in str(part).lower().replace(",", " ").split():
            token = token.strip()
            if len(token) >= 3:
                tokens.add(token)
    return tokens
