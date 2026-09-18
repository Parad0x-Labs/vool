from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.knowledge_registry import local_manifest, record_remote_holder, withdraw_holder
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.knowledge_index import add_index_delta, add_tombstone


@dataclass
class KnowledgeRouteResult:
    ok: bool
    reason: str
    generated_messages: list[bytes]


def _nonce() -> str:
    return uuid.uuid4().hex


def handle_knowledge_message(msg_type: str, payload_model: Any) -> KnowledgeRouteResult:
    generated: list[bytes] = []

    if msg_type in {"KNOWLEDGE_AD", "KNOWLEDGE_REPLICA_AD", "KNOWLEDGE_REFRESH"}:
        record_remote_holder(
            shard_id=payload_model.shard_id,
            holder_peer_id=payload_model.holder_peer_id,
            content_hash=payload_model.content_hash,
            version=int(payload_model.version),
            freshness_ts=payload_model.freshness_ts.isoformat(),
            ttl_seconds=int(payload_model.ttl_seconds),
            topic_tags=list(payload_model.topic_tags),
            summary_digest=payload_model.summary_digest,
            size_bytes=int(payload_model.size_bytes),
            metadata=dict(payload_model.metadata),
            fetch_route=dict(payload_model.fetch_route),
            trust_weight=float(payload_model.trust_weight),
            home_region=str(getattr(payload_model, "home_region", None) or "global"),
            access_mode=payload_model.access_mode,
        )
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type=msg_type.lower(),
            payload=payload_model.model_dump(mode="json"),
            peer_id=payload_model.holder_peer_id,
        )
        return KnowledgeRouteResult(True, "Knowledge holder metadata stored.", generated)

    if msg_type == "KNOWLEDGE_WITHDRAW":
        withdraw_holder(payload_model.shard_id, payload_model.holder_peer_id)
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="knowledge_withdraw",
            payload=payload_model.model_dump(mode="json"),
            peer_id=payload_model.holder_peer_id,
        )
        return KnowledgeRouteResult(True, "Knowledge holder withdrawn.", generated)

    if msg_type == "KNOWLEDGE_TOMBSTONE":
        # A8 STRICT digest law: the erasure mechanism itself must not retain
        # the tombstoned shard's content_hash as an unsalted confirmation
        # oracle. The tombstone records WHAT was withdrawn, not a guessable
        # digest of it.
        add_tombstone(
            payload_model.shard_id,
            "",
            int(payload_model.version),
            payload_model.reason,
        )
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="knowledge_tombstone",
            payload=payload_model.model_dump(mode="json"),
            peer_id=None,
        )
        return KnowledgeRouteResult(True, "Knowledge tombstone stored.", generated)

    if msg_type == "KNOWLEDGE_FETCH_REQUEST":
        manifest = local_manifest(payload_model.shard_id)
        if not manifest:
            return KnowledgeRouteResult(False, "Requested shard not held locally.", generated)
        offer_payload = {
            "shard_id": payload_model.shard_id,
            "holder_peer_id": local_peer_id(),
            "request_id": payload_model.request_id,
            "fetch_route": {"method": "request_shard", "shard_id": payload_model.shard_id},
            "timestamp": datetime.now(timezone.utc),
        }
        generated.append(
            encode_message(
                msg_id=str(uuid.uuid4()),
                msg_type="KNOWLEDGE_FETCH_OFFER",
                sender_peer_id=local_peer_id(),
                nonce=_nonce(),
                payload=offer_payload,
            )
        )
        return KnowledgeRouteResult(True, "Knowledge fetch offer generated.", generated)

    if msg_type == "KNOWLEDGE_FETCH_OFFER":
        add_index_delta(
            delta_id=str(uuid.uuid4()),
            delta_type="knowledge_fetch_offer",
            payload=payload_model.model_dump(mode="json"),
            peer_id=payload_model.holder_peer_id,
        )
        return KnowledgeRouteResult(True, "Knowledge fetch offer observed.", generated)

    return KnowledgeRouteResult(False, f"Unhandled knowledge msg_type: {msg_type}", generated)
