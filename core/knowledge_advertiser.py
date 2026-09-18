from __future__ import annotations

import uuid
from typing import Any

from core import audit_logger
from core.daemon.peer_delivery import broadcast_to_recent_peers
from core.discovery_index import peer_trust
from core.knowledge_freshness import DEFAULT_KNOWLEDGE_TTL_SECONDS, DEFAULT_LEASE_SECONDS, iso_now, lease_expiry
from core.knowledge_registry import holders_for_fetch, local_manifest, sync_local_learning_shards
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.knowledge_index import upsert_presence_lease


def _nonce() -> str:
    return uuid.uuid4().hex


def build_hello_payload(
    *,
    agent_name: str | None = None,
    capabilities: list[str],
    status: str = "idle",
    transport_mode: str = "lan_only",
    home_region: str = "global",
    current_region: str | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": local_peer_id(),
        "agent_name": agent_name,
        "status": status,
        "capabilities": capabilities,
        "home_region": home_region,
        "current_region": current_region or home_region,
        "transport_mode": transport_mode,
        "trust_score": peer_trust(local_peer_id()),
        "timestamp": iso_now(),
        "lease_seconds": DEFAULT_LEASE_SECONDS,
    }


def build_presence_heartbeat(
    *,
    status: str = "idle",
    capabilities: list[str],
    transport_mode: str = "lan_only",
    home_region: str = "global",
    current_region: str | None = None,
) -> dict[str, Any]:
    return {
        "agent_id": local_peer_id(),
        "status": status,
        "capabilities": capabilities,
        "home_region": home_region,
        "current_region": current_region or home_region,
        "transport_mode": transport_mode,
        "trust_score": peer_trust(local_peer_id()),
        "timestamp": iso_now(),
        "lease_seconds": DEFAULT_LEASE_SECONDS,
    }


def _record_local_presence(payload: dict[str, Any]) -> None:
    upsert_presence_lease(
        peer_id=payload["agent_id"],
        agent_name=payload.get("agent_name"),
        status=payload["status"],
        capabilities=list(payload["capabilities"]),
        home_region=str(payload.get("home_region") or "global"),
        current_region=str(payload.get("current_region") or payload.get("home_region") or "global"),
        transport_mode=payload["transport_mode"],
        trust_score=float(payload["trust_score"]),
        lease_expires_at=lease_expiry(int(payload["lease_seconds"])),
        last_heartbeat_at=str(payload["timestamp"]),
    )


def build_knowledge_advert(shard_id: str) -> dict[str, Any] | None:
    manifest = local_manifest(shard_id)
    if not manifest:
        return None
    local_holder = next(
        (holder for holder in holders_for_fetch(shard_id) if holder["holder_peer_id"] == local_peer_id()),
        None,
    )
    if not local_holder:
        return None
    metadata = dict(manifest.get("metadata") or {})
    access_mode = str(local_holder.get("access_mode") or manifest["metadata"].get("share_scope") or "public")
    if access_mode == "local_only":
        return None
    fetch_route = {
        "method": "request_shard",
        "shard_id": manifest["shard_id"],
        "content_hash": manifest["content_hash"],
        "dense_storage_backend": metadata.get("dense_storage_backend"),
        "dense_storage_policy": metadata.get("dense_storage_policy"),
    }
    return {
        "shard_id": manifest["shard_id"],
        "content_hash": manifest["content_hash"],
        "version": int(manifest["version"]),
        "holder_peer_id": local_peer_id(),
        "home_region": str(metadata.get("home_region") or "global"),
        "topic_tags": list(manifest["topic_tags"]),
        "summary_digest": manifest["summary_digest"],
        "size_bytes": int(manifest["size_bytes"]),
        "freshness_ts": str(metadata.get("freshness_ts") or iso_now()),
        "ttl_seconds": DEFAULT_KNOWLEDGE_TTL_SECONDS,
        "trust_weight": float(metadata.get("trust_score") or peer_trust(local_peer_id())),
        "access_mode": access_mode,
        "fetch_methods": ["request_shard"],
        "fetch_route": fetch_route,
        "metadata": metadata,
        "manifest_id": manifest["manifest_id"],
    }


def _broadcast(msg_type: str, payload: dict[str, Any], *, limit: int = 32) -> int:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type=msg_type,
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=payload,
    )
    return broadcast_to_recent_peers(
        msg,
        message_type=msg_type,
        target_id=local_peer_id(),
        limit=limit,
    )


def broadcast_hello(
    *,
    agent_name: str | None = None,
    capabilities: list[str],
    status: str = "idle",
    transport_mode: str = "lan_only",
    home_region: str = "global",
    current_region: str | None = None,
    limit: int = 32,
) -> int:
    payload = build_hello_payload(
        agent_name=agent_name,
        capabilities=capabilities,
        status=status,
        transport_mode=transport_mode,
        home_region=home_region,
        current_region=current_region,
    )
    _record_local_presence(payload)
    sent = _broadcast("HELLO_AD", payload, limit=limit)
    audit_logger.log("hello_ad_broadcast", target_id=local_peer_id(), target_type="peer", details={"sent": sent})
    return sent


def broadcast_presence_heartbeat(
    *,
    capabilities: list[str],
    status: str = "idle",
    transport_mode: str = "lan_only",
    home_region: str = "global",
    current_region: str | None = None,
    limit: int = 32,
) -> int:
    payload = build_presence_heartbeat(
        status=status,
        capabilities=capabilities,
        transport_mode=transport_mode,
        home_region=home_region,
        current_region=current_region,
    )
    _record_local_presence(payload)
    sent = _broadcast("PRESENCE_HEARTBEAT", payload, limit=limit)
    audit_logger.log("presence_heartbeat_broadcast", target_id=local_peer_id(), target_type="peer", details={"sent": sent})
    return sent


def broadcast_local_knowledge_ads(*, limit: int = 32, shard_limit: int = 128) -> int:
    sync_local_learning_shards(limit=shard_limit)
    from storage.knowledge_manifests import all_manifests

    sent = 0
    for manifest in all_manifests(limit=shard_limit):
        payload = build_knowledge_advert(manifest["shard_id"])
        if not payload:
            continue
        sent += _broadcast("KNOWLEDGE_AD", payload, limit=limit)
    audit_logger.log("knowledge_ad_broadcast", target_id=local_peer_id(), target_type="peer", details={"sent": sent})
    return sent
