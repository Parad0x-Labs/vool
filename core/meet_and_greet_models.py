from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PeerEndpointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    source: str = Field(default="api", max_length=64)


class PresenceUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    agent_name: Optional[str] = Field(default=None, max_length=64)
    status: Literal["idle", "busy", "offline", "limited"]
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    home_region: str = Field(default="global", max_length=64)
    current_region: Optional[str] = Field(default=None, max_length=64)
    transport_mode: str = Field(default="lan_only", max_length=64)
    trust_score: float = Field(ge=0, le=1)
    timestamp: datetime
    lease_seconds: int = Field(ge=30, le=3600)
    endpoint: Optional[PeerEndpointRecord] = None
    endpoints: list[PeerEndpointRecord] = Field(default_factory=list, max_length=8)


class PresenceWithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    reason: str = Field(default="manual_withdraw", max_length=256)
    timestamp: datetime


class PresenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    agent_name: Optional[str] = None
    status: str
    capabilities: list[str] = Field(default_factory=list)
    home_region: str = "global"
    current_region: Optional[str] = None
    transport_mode: str
    trust_score: float
    last_heartbeat_at: str
    lease_expires_at: str
    endpoint: Optional[PeerEndpointRecord] = None
    endpoints: list[PeerEndpointRecord] = Field(default_factory=list)
    summary_only: bool = False


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: Optional[str] = Field(default=None, max_length=512)
    problem_class: Optional[str] = Field(default=None, max_length=128)
    topic_tags: list[str] = Field(default_factory=list, max_length=16)
    min_trust_weight: float = Field(default=0.0, ge=0, le=1)
    preferred_region: Optional[str] = Field(default=None, max_length=64)
    summary_mode: Literal["regional_detail", "global_summary"] = "regional_detail"
    limit: int = Field(default=20, ge=1, le=200)


class KnowledgeHolderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holder_peer_id: str
    home_region: str = "global"
    version: int
    freshness_ts: str
    expires_at: str
    trust_weight: float
    access_mode: str
    fetch_route: dict[str, Any] = Field(default_factory=dict)
    status: str
    endpoint: Optional[PeerEndpointRecord] = None
    summary_only: bool = False


class KnowledgeIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    shard_id: str
    content_hash: str
    version: int
    topic_tags: list[str] = Field(default_factory=list)
    summary_digest: str
    size_bytes: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    latest_freshness: Optional[str] = None
    replication_count: int = 0
    live_holder_count: int = 0
    stale_holder_count: int = 0
    priority_region: Optional[str] = None
    region_replication_counts: dict[str, int] = Field(default_factory=dict)
    summary_mode: Literal["regional_detail", "global_summary"] = "regional_detail"
    holders: list[KnowledgeHolderRecord] = Field(default_factory=list)


class PaymentStatusUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_or_transfer_id: str = Field(min_length=8, max_length=256)
    payer_peer_id: str = Field(min_length=16, max_length=256)
    payee_peer_id: str = Field(min_length=16, max_length=256)
    status: Literal["unpaid", "reserved", "paid", "disputed", "failed"]
    receipt_reference: Optional[str] = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaymentStatusRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_or_transfer_id: str
    payer_peer_id: str
    payee_peer_id: str
    status: str
    receipt_reference: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str


class MeetNodeRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=4, max_length=128)
    base_url: str = Field(min_length=8, max_length=512)
    region: str = Field(default="global", max_length=64)
    role: Literal["seed", "replica", "edge"] = "seed"
    platform_hint: str = Field(default="unknown", max_length=64)
    priority: int = Field(default=100, ge=1, le=10_000)
    status: Literal["active", "draining", "offline"] = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MeetNodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    base_url: str
    region: str
    role: str
    platform_hint: str
    priority: int
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: Optional[str] = None
    created_at: str
    updated_at: str


class MeetSyncStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_node_id: str
    last_snapshot_cursor: Optional[str] = None
    last_delta_cursor: Optional[str] = None
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    updated_at: str


class IndexDeltaRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta_id: str
    peer_id: Optional[str] = None
    delta_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class IndexSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_cursor: Optional[str] = None
    source_region: str = "global"
    summary_mode: Literal["regional_detail", "global_summary"] = "regional_detail"
    meet_nodes: list[MeetNodeRecord] = Field(default_factory=list)
    active_presence: list[PresenceRecord] = Field(default_factory=list)
    knowledge_index: list[KnowledgeIndexEntry] = Field(default_factory=list)
    payment_status: list[PaymentStatusRecord] = Field(default_factory=list)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    status: Literal["ok"]
    active_presence_count: int
    knowledge_entry_count: int
    payment_marker_count: int
    snapshot_cursor: Optional[str] = None


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    status: Literal["ready", "not_ready"]
    checks: dict[str, str] = Field(default_factory=dict)


class ApiEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    result: Optional[Any] = None
    error: Optional[str] = None


class SignedApiWriteEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signer_peer_id: str = Field(min_length=16, max_length=256)
    nonce: str = Field(min_length=8, max_length=128)
    timestamp: datetime
    target_path: str = Field(min_length=2, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(min_length=16, max_length=4096)


class KnowledgeChallengeIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    holder_peer_id: str = Field(min_length=16, max_length=256)
    requester_peer_id: str = Field(min_length=16, max_length=256)


class KnowledgeChallengeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    shard_id: str
    holder_peer_id: str
    requester_peer_id: str
    content_hash: str
    manifest_id: str
    chunk_index: int
    expected_chunk_hash: str
    nonce: str
    status: str
    created_at: str
    expires_at: str
    updated_at: Optional[str] = None
    verification_note: Optional[str] = None


class KnowledgeChallengeResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=8, max_length=256)
    shard_id: str = Field(min_length=16, max_length=256)
    holder_peer_id: str = Field(min_length=16, max_length=256)
    requester_peer_id: str = Field(min_length=16, max_length=256)
    chunk_index: int = Field(ge=0, le=1_000_000)
    nonce: str = Field(min_length=8, max_length=128)


class KnowledgeChallengeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    shard_id: str
    holder_peer_id: str
    requester_peer_id: str
    chunk_index: int
    chunk_hash: str
    chunk_b64: str
    created_at: str


class KnowledgeChallengeVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: str = Field(min_length=8, max_length=256)
    requester_peer_id: str = Field(min_length=16, max_length=256)
    response: KnowledgeChallengeResponse
