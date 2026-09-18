from __future__ import annotations

import uuid
from typing import Any

from core import audit_logger, policy_engine
from core.daemon.peer_delivery import broadcast_to_recent_peers
from core.discovery_index import (
    delivery_endpoints_for_peer,
    endpoint_for_peer,
    get_best_helpers,
)
from network.protocol import encode_message
from network.signer import get_local_peer_id as local_peer_id
from network.transport import send_message


def _nonce() -> str:
    return uuid.uuid4().hex


def _send_to_peer(
    peer_id: str,
    raw_message: bytes,
    *,
    verified_limit: int = 4,
    include_candidates: bool = False,
    candidate_limit: int = 2,
) -> bool:
    for host, port in delivery_endpoints_for_peer(
        peer_id,
        verified_limit=verified_limit,
        include_candidates=include_candidates,
        candidate_limit=candidate_limit,
    ):
        if send_message(host, int(port), raw_message):
            return True
    return False


def broadcast_capability_ad(raw_message: bytes, *, limit: int = 25) -> int:
    sent = broadcast_to_recent_peers(
        raw_message,
        message_type="CAPABILITY_AD",
        target_id=local_peer_id(),
        limit=limit,
    )

    audit_logger.log(
        "capability_ad_broadcast",
        target_id=local_peer_id(),
        target_type="peer",
        details={"sent": sent},
    )
    return sent


def dispatch_query_shard(query: dict[str, Any], *, limit: int = 5) -> int:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="QUERY_SHARD",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=query,
    )

    sent = broadcast_to_recent_peers(
        msg,
        message_type="QUERY_SHARD",
        target_id=str(query.get("query_id") or local_peer_id()),
        limit=limit,
    )

    audit_logger.log(
        "query_shard_dispatched",
        target_id=local_peer_id(),
        target_type="peer",
        details={
            "sent": sent,
            "problem_class": query.get("problem_class"),
            "query_id": query.get("query_id"),
        },
    )
    return sent


def request_specific_shard(*, peer_id: str, query_id: str, shard_id: str) -> bool:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="REQUEST_SHARD",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload={
            "query_id": query_id,
            "shard_id": shard_id,
        },
    )
    ok = _send_to_peer(peer_id, msg, include_candidates=True, candidate_limit=2)

    audit_logger.log(
        "request_shard_sent",
        target_id=shard_id,
        target_type="shard",
        details={
            "peer_id": peer_id,
            "ok": ok,
            "query_id": query_id,
        },
    )
    return ok


def broadcast_task_offer(
    *,
    offer_payload: dict[str, Any],
    required_capabilities: list[str],
    exclude_host_group_hint_hash: str | None = None,
    limit: int | None = None,
) -> int:
    helpers = get_best_helpers(
        required_capabilities=required_capabilities,
        exclude_peer_id=local_peer_id(),
        limit=limit or 10,
        exclude_host_group_hint_hash=exclude_host_group_hint_hash,
    )

    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="TASK_OFFER",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=offer_payload,
    )

    sent = 0
    for helper in helpers:
        if _send_to_peer(helper.peer_id, msg):
            sent += 1

    if sent == 0 and bool(policy_engine.get("orchestration.local_loopback_offer_on_no_helpers", True)):
        try:
            from core.order_book import global_order_book
            from network.assist_router import handle_incoming_assist_message

            local_endpoint = endpoint_for_peer(local_peer_id()) or ("127.0.0.1", 49152)
            # Store the offer + capsule rows locally first; the later self
            # TASK_CLAIM has a FOREIGN KEY on task_offers and dies without them.
            route = handle_incoming_assist_message(raw_bytes=msg, source_addr=local_endpoint)
            if not route.ok:
                raise RuntimeError(f"loopback offer rejected: {route.reason}")
            global_order_book.push(msg, local_endpoint, offer_payload)
            sent += 1
            audit_logger.log(
                "task_offer_loopback_enqueued",
                target_id=offer_payload.get("task_id"),
                target_type="task",
                details={"local_endpoint": f"{local_endpoint[0]}:{int(local_endpoint[1])}"},
            )
        except Exception as exc:
            audit_logger.log(
                "task_offer_loopback_enqueue_failed",
                target_id=offer_payload.get("task_id"),
                target_type="task",
                details={"error": str(exc)},
            )

    audit_logger.log(
        "task_offer_broadcast",
        target_id=offer_payload.get("task_id"),
        target_type="task",
        details={
            "sent": sent,
            "required_capabilities": required_capabilities,
        },
    )
    return sent

# Phase 29: Credit Market DEX Broadcast
def broadcast_credit_offer(offer_payload: dict[str, Any], *, limit: int = 50) -> int:
    msg = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type="CREDIT_OFFER",
        sender_peer_id=local_peer_id(),
        nonce=_nonce(),
        payload=offer_payload,
    )

    sent = broadcast_to_recent_peers(
        msg,
        message_type="CREDIT_OFFER",
        target_id=str(offer_payload.get("offer_id") or ""),
        limit=limit,
    )

    audit_logger.log(
        "credit_offer_broadcast",
        target_id=offer_payload.get("offer_id"),
        target_type="dex",
        details={"sent": sent, "amount": offer_payload.get("credits_available")},
    )
    return sent
