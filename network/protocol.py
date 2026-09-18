from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core import policy_engine
from core.identity_lifecycle import enforce_active_identity
from network import signer
from network.assist_models import validate_assist_payload
from network.knowledge_models import validate_knowledge_payload
from storage.db import get_connection

_ALLOWED_TYPES = {
    "PING",
    "HEARTBEAT",
    "QUERY_SHARD",
    "SHARD_CANDIDATES",
    "REQUEST_SHARD",
    "SHARD_PAYLOAD",
    "REPUTATION_HINT",
    "REPORT_ABUSE",
    "CAPABILITY_AD",
    "TASK_OFFER",
    "TASK_CLAIM",
    "TASK_ASSIGN",
    "TASK_PROGRESS",
    "TASK_RESULT",
    "TASK_REVIEW",
    "TASK_REWARD",
    "TASK_CANCEL",
    "FIND_NODE",
    "NODE_FOUND",
    "FIND_BLOCK",
    "BLOCK_FOUND",
    "REQUEST_BLOCK",
    "BLOCK_PAYLOAD",
    "CREDIT_OFFER",
    "CREDIT_TRANSFER",
    "HELLO_AD",
    "PRESENCE_HEARTBEAT",
    "KNOWLEDGE_AD",
    "KNOWLEDGE_WITHDRAW",
    "KNOWLEDGE_FETCH_REQUEST",
    "KNOWLEDGE_FETCH_OFFER",
    "KNOWLEDGE_REPLICA_AD",
    "KNOWLEDGE_REFRESH",
    "KNOWLEDGE_TOMBSTONE",
}

class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str = Field(min_length=8, max_length=128)
    msg_type: Literal[
        "PING",
        "HEARTBEAT",
        "QUERY_SHARD",
        "SHARD_CANDIDATES",
        "REQUEST_SHARD",
        "SHARD_PAYLOAD",
        "REPUTATION_HINT",
        "REPORT_ABUSE",
        "CAPABILITY_AD",
        "TASK_OFFER",
        "TASK_CLAIM",
        "TASK_ASSIGN",
        "TASK_PROGRESS",
        "TASK_RESULT",
        "TASK_REVIEW",
        "TASK_REWARD",
        "TASK_CANCEL",
        "FIND_NODE",
        "NODE_FOUND",
        "FIND_BLOCK",
        "BLOCK_FOUND",
        "REQUEST_BLOCK",
        "BLOCK_PAYLOAD",
        "CREDIT_OFFER",
        "CREDIT_TRANSFER",
        "HELLO_AD",
        "PRESENCE_HEARTBEAT",
        "KNOWLEDGE_AD",
        "KNOWLEDGE_WITHDRAW",
        "KNOWLEDGE_FETCH_REQUEST",
        "KNOWLEDGE_FETCH_OFFER",
        "KNOWLEDGE_REPLICA_AD",
        "KNOWLEDGE_REFRESH",
        "KNOWLEDGE_TOMBSTONE",
    ]
    protocol_version: Literal[1]
    sender_peer_id: str = Field(min_length=16, max_length=256)
    timestamp: datetime
    nonce: str = Field(min_length=8, max_length=128)
    payload: dict[str, Any]
    signature: str = Field(min_length=16, max_length=4096)

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        if v.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        if v < now - timedelta(minutes=10) or v > now + timedelta(minutes=10):
            raise ValueError("timestamp outside allowed skew window")
        return v

class QueryShardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=8, max_length=128)
    problem_class: str = Field(max_length=128)
    problem_signature: str = Field(min_length=16, max_length=128)
    environment_tags: dict[str, str]
    max_candidates: int = Field(default=5, ge=1, le=10)

class RequestShardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=8, max_length=128)
    shard_id: str = Field(min_length=8, max_length=256)

class FindNodePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=16, max_length=256)

class NodeFoundEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    peer_id: str = Field(min_length=16, max_length=256)
    ip: str = Field(max_length=64)
    port: int = Field(ge=1, le=65535)

class NodeFoundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=16, max_length=256)
    nodes: list[NodeFoundEntry] = Field(default_factory=list, max_length=20)

class FindBlockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_hash: str = Field(min_length=64, max_length=128) # SHA-256 is 64 hex chars

class BlockFoundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_hash: str = Field(min_length=64, max_length=128)
    hosting_peers: list[NodeFoundEntry] = Field(default_factory=list, max_length=20)

class RequestBlockPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_hash: str = Field(min_length=64, max_length=128)

class BlockPayloadMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_hash: str = Field(min_length=64, max_length=128)
    byte_hex: str = Field(min_length=2, max_length=4194304) # 2MB hex string = 4MB


class ReportAbusePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(min_length=8, max_length=128)
    accused_peer_id: str = Field(min_length=16, max_length=256)
    signal_type: str = Field(min_length=3, max_length=64)
    severity: float = Field(ge=0.0, le=1.0)
    task_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    details: dict[str, Any] = Field(default_factory=dict)
    ttl: int = Field(default=0, ge=0, le=5)



class ShardCandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=8, max_length=256)
    problem_class: str = Field(max_length=128)
    summary: str = Field(max_length=512)
    trust_score: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    freshness_ts: datetime
    risk_flags: list[str] = Field(default_factory=list, max_length=16)


class ShardCandidatesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=8, max_length=128)
    candidates: list[ShardCandidateSummary] = Field(default_factory=list, max_length=10)


class ShardPayloadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shard_id: str = Field(min_length=8, max_length=256)
    schema_version: int = Field(ge=1, le=128)
    problem_class: str = Field(max_length=128)
    problem_signature: str = Field(min_length=16, max_length=128)
    summary: str = Field(min_length=1, max_length=4096)
    resolution_pattern: list[str] = Field(default_factory=list, max_length=64)
    environment_tags: dict[str, str] = Field(default_factory=dict)
    source_type: str = Field(default="unknown", max_length=64)
    source_node_id: str = Field(default="", max_length=256)
    quality_score: float = Field(ge=0.0, le=1.0)
    trust_score: float = Field(ge=0.0, le=1.0)
    risk_flags: list[str] = Field(default_factory=list, max_length=32)
    freshness_ts: datetime
    expires_ts: Optional[datetime] = None
    signature: str = Field(default="", max_length=4096)


class ShardPayloadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=8, max_length=128)
    manifest_id: Optional[str] = Field(default=None, min_length=8, max_length=128)
    content_hash: Optional[str] = Field(default=None, min_length=8, max_length=256)
    version: Optional[int] = Field(default=None, ge=1, le=128)
    summary_digest: Optional[str] = Field(default=None, min_length=8, max_length=128)
    shard: ShardPayloadBody


def encode_message(
    *, msg_id: str, msg_type: str, sender_peer_id: str, nonce: str, payload: dict[str, Any]
) -> bytes:
    base = {
        "msg_id": msg_id,
        "msg_type": msg_type,
        "protocol_version": 1,
        "sender_peer_id": sender_peer_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": nonce,
        "payload": payload,
    }
    raw = _canonical_message_bytes(base)
    signature = signer.sign(raw)
    base["signature"] = signature
    envelope = Envelope.model_validate(base)
    return json.dumps(envelope.model_dump(), sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")

def _canonical_message_bytes(envelope_dict: dict[str, Any]) -> bytes:
    unsigned = dict(envelope_dict)
    unsigned.pop("signature", None)
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), default=_json_default).encode("utf-8")


def message_proof_hash(envelope_dict: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_message_bytes(envelope_dict)).hexdigest()

def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Not JSON serializable: {type(value)!r}")


def peek_message_type(raw_bytes: bytes) -> str | None:
    """
    Cheap, non-validating parser used only for routing.
    It does NOT touch nonce cache or signatures.
    """
    try:
        raw_obj = json.loads(raw_bytes.decode("utf-8"))
        msg_type = raw_obj.get("msg_type")
        return str(msg_type) if isinstance(msg_type, str) else None
    except Exception:
        return None


def peek_sender_peer_id(raw_bytes: bytes) -> str | None:
    """
    Cheap, non-validating parser used only for routing.
    It does NOT touch nonce cache or signatures.
    """
    try:
        raw_obj = json.loads(raw_bytes.decode("utf-8"))
        sender = raw_obj.get("sender_peer_id")
        return str(sender) if isinstance(sender, str) else None
    except Exception:
        return None


class Protocol:
    """
    Class interface wrapping standalone functions to maintain compatibility with Node Stub.
    """
    @staticmethod
    def decode_and_validate(raw_bytes: bytes) -> dict:
        max_bytes = int(policy_engine.get("system.max_message_bytes", 32768))
        if len(raw_bytes) > max_bytes:
            raise ValueError("Message exceeds max allowed size.")

        try:
            raw_obj = json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:
            raise ValueError("Invalid JSON payload.") from e

        try:
            envelope = Envelope.model_validate(raw_obj)
        except ValidationError as e:
            raise ValueError(f"Envelope schema validation failed: {e}") from e

        if not verify_signature(envelope):
            raise ValueError("Invalid signature.")

        validate_payload(envelope)
        if not consume_nonce_once(envelope.sender_peer_id, envelope.nonce):
            raise ValueError("Replay detected (nonce already seen).")
        return envelope.model_dump()

def verify_signature(envelope: Envelope) -> bool:
    try:
        enforce_active_identity(envelope.sender_peer_id, scope="mesh_message")
    except ValueError:
        return False
    envelope_dict = envelope.model_dump()
    payload_bytes = _canonical_message_bytes(envelope_dict)
    return signer.verify(payload_bytes, envelope.signature, envelope.sender_peer_id)

def validate_payload(envelope: Envelope) -> None:
    msg_type = envelope.msg_type
    payload = envelope.payload

    if msg_type == "QUERY_SHARD":
        QueryShardPayload.model_validate(payload)
    elif msg_type == "REQUEST_SHARD":
        RequestShardPayload.model_validate(payload)
    elif msg_type == "SHARD_CANDIDATES":
        ShardCandidatesPayload.model_validate(payload)
    elif msg_type == "SHARD_PAYLOAD":
        ShardPayloadPayload.model_validate(payload)
    elif msg_type == "FIND_NODE":
        FindNodePayload.model_validate(payload)
    elif msg_type == "NODE_FOUND":
        NodeFoundPayload.model_validate(payload)
    elif msg_type == "FIND_BLOCK":
        FindBlockPayload.model_validate(payload)
    elif msg_type == "BLOCK_FOUND":
        BlockFoundPayload.model_validate(payload)
    elif msg_type == "REQUEST_BLOCK":
        RequestBlockPayload.model_validate(payload)
    elif msg_type == "BLOCK_PAYLOAD":
        BlockPayloadMsg.model_validate(payload)
    elif msg_type == "REPORT_ABUSE":
        ReportAbusePayload.model_validate(payload)
    elif msg_type in {
        "HELLO_AD",
        "PRESENCE_HEARTBEAT",
        "KNOWLEDGE_AD",
        "KNOWLEDGE_WITHDRAW",
        "KNOWLEDGE_FETCH_REQUEST",
        "KNOWLEDGE_FETCH_OFFER",
        "KNOWLEDGE_REPLICA_AD",
        "KNOWLEDGE_REFRESH",
        "KNOWLEDGE_TOMBSTONE",
    }:
        validate_knowledge_payload(msg_type, payload)
    elif msg_type in {
        "CAPABILITY_AD", "TASK_OFFER", "TASK_CLAIM", "TASK_ASSIGN",
        "TASK_PROGRESS", "TASK_RESULT", "TASK_REVIEW", "TASK_REWARD",
        "CREDIT_OFFER", "CREDIT_TRANSFER",
    }:
        validate_assist_payload(msg_type, payload)
    elif msg_type in {"PING", "HEARTBEAT", "TASK_CANCEL"}:
        if payload and not isinstance(payload, dict):
            raise ValueError(f"{msg_type} payload must be an object.")
    else:
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object.")

def is_replay(sender_peer_id: str, nonce: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM nonce_cache WHERE sender_peer_id = ? AND nonce = ? LIMIT 1",
            (sender_peer_id, nonce),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()

def store_nonce(sender_peer_id: str, nonce: str) -> None:
    conn = get_connection()
    try:
        prune_nonce_cache(conn)
        conn.execute(
            "INSERT OR IGNORE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
            (sender_peer_id, nonce, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def consume_nonce_once(sender_peer_id: str, nonce: str) -> bool:
    """
    Atomically consume a sender/nonce tuple exactly once.
    Returns True when the nonce is newly accepted and persisted.
    Returns False when the nonce was already present (replay).
    """
    conn = get_connection()
    try:
        prune_nonce_cache(conn)
        cur = conn.execute(
            "INSERT OR IGNORE INTO nonce_cache (sender_peer_id, nonce, seen_at) VALUES (?, ?, ?)",
            (sender_peer_id, nonce, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return int(cur.rowcount or 0) > 0
    finally:
        conn.close()


def prune_nonce_cache(conn=None, *, max_age_hours: int | None = None, max_rows: int | None = None) -> int:
    own_conn = conn is None
    conn = conn or get_connection()
    try:
        age_hours = int(max_age_hours or policy_engine.get("system.nonce_cache_max_age_hours", 48))
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, age_hours))).isoformat()
        removed = 0
        cur = conn.execute("DELETE FROM nonce_cache WHERE seen_at < ?", (cutoff,))
        removed += int(cur.rowcount or 0)
        row_limit = int(max_rows or policy_engine.get("system.nonce_cache_max_rows", 200000))
        if row_limit > 0:
            # Keep the newest N nonces and prune anything older.
            extra = conn.execute(
                """
                DELETE FROM nonce_cache
                WHERE rowid IN (
                    SELECT rowid
                    FROM nonce_cache
                    ORDER BY seen_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (row_limit,),
            )
            removed += int(extra.rowcount or 0)
        if own_conn:
            conn.commit()
        return removed
    finally:
        if own_conn:
            conn.close()
