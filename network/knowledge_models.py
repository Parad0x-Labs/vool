from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class HelloAd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    agent_name: Optional[str] = Field(default=None, max_length=64)
    status: Literal["idle", "busy", "offline", "limited"]
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    home_region: str = Field(default="global", max_length=64)
    current_region: Optional[str] = Field(default=None, max_length=64)
    transport_mode: str = Field(max_length=64)
    trust_score: float = Field(ge=0, le=1)
    timestamp: datetime
    lease_seconds: int = Field(ge=30, le=3600)


class PresenceHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=16, max_length=256)
    agent_name: Optional[str] = Field(default=None, max_length=64)
    status: Literal["idle", "busy", "offline", "limited"]
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    home_region: str = Field(default="global", max_length=64)
    current_region: Optional[str] = Field(default=None, max_length=64)
    transport_mode: str = Field(max_length=64)
    trust_score: float = Field(ge=0, le=1)
    timestamp: datetime
    lease_seconds: int = Field(ge=30, le=3600)


class KnowledgeAdvert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    content_hash: str = Field(min_length=16, max_length=256)
    version: int = Field(ge=1, le=1_000_000)
    holder_peer_id: str = Field(min_length=16, max_length=256)
    home_region: str = Field(default="global", max_length=64)
    topic_tags: list[str] = Field(default_factory=list, max_length=16)
    summary_digest: str = Field(min_length=8, max_length=128)
    size_bytes: int = Field(ge=1, le=100_000_000)
    freshness_ts: datetime
    ttl_seconds: int = Field(ge=30, le=86_400)
    trust_weight: float = Field(ge=0, le=1)
    access_mode: Literal["public", "trusted_only", "private"] = "public"
    fetch_methods: list[str] = Field(default_factory=list, max_length=8)
    fetch_route: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    manifest_id: str = Field(min_length=8, max_length=128)


class KnowledgeWithdraw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    holder_peer_id: str = Field(min_length=16, max_length=256)
    reason: str = Field(max_length=256)
    timestamp: datetime


class KnowledgeFetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    requester_peer_id: str = Field(min_length=16, max_length=256)
    request_id: str = Field(min_length=8, max_length=128)
    timestamp: datetime


class KnowledgeFetchOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    holder_peer_id: str = Field(min_length=16, max_length=256)
    request_id: str = Field(min_length=8, max_length=128)
    fetch_route: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class KnowledgeReplicaAd(KnowledgeAdvert):
    model_config = ConfigDict(extra="forbid")


class KnowledgeRefresh(KnowledgeAdvert):
    model_config = ConfigDict(extra="forbid")


class KnowledgeTombstone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=16, max_length=256)
    content_hash: str = Field(min_length=16, max_length=256)
    version: int = Field(ge=1, le=1_000_000)
    reason: str = Field(max_length=256)
    timestamp: datetime


_MODEL_BY_TYPE = {
    "HELLO_AD": HelloAd,
    "PRESENCE_HEARTBEAT": PresenceHeartbeat,
    "KNOWLEDGE_AD": KnowledgeAdvert,
    "KNOWLEDGE_WITHDRAW": KnowledgeWithdraw,
    "KNOWLEDGE_FETCH_REQUEST": KnowledgeFetchRequest,
    "KNOWLEDGE_FETCH_OFFER": KnowledgeFetchOffer,
    "KNOWLEDGE_REPLICA_AD": KnowledgeReplicaAd,
    "KNOWLEDGE_REFRESH": KnowledgeRefresh,
    "KNOWLEDGE_TOMBSTONE": KnowledgeTombstone,
}


def validate_knowledge_payload(msg_type: str, payload: dict[str, Any]) -> BaseModel:
    model = _MODEL_BY_TYPE.get(msg_type)
    if not model:
        raise ValueError(f"Unsupported knowledge msg_type: {msg_type}")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"Knowledge payload validation failed: {exc}") from exc
