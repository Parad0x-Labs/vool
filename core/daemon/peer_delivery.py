from __future__ import annotations

import time
from collections.abc import Callable

from core import audit_logger, policy_engine
from core.discovery_index import (
    delivery_targets_for_peer,
    note_peer_endpoint_candidate_probe_result,
    note_verified_peer_endpoint_delivery_result,
    recent_peer_verified_endpoints,
)
from network.signer import get_local_peer_id as local_peer_id
from network.transport import send_message


def _send_with_retries(
    host: str,
    port: int,
    payload: bytes,
    *,
    message_type: str,
    target_id: str,
    log_failure: bool,
) -> bool:
    critical_types = {"TASK_ASSIGN", "TASK_RESULT", "TASK_REVIEW", "TASK_REWARD", "TASK_CLAIM"}
    retries = int(policy_engine.get("network.critical_send_retries", 2)) if message_type in critical_types else 0
    retries = max(0, retries)
    attempts = 1 + retries

    for attempt in range(1, attempts + 1):
        ok = send_message(host, int(port), payload)
        if ok:
            return True
        if attempt < attempts:
            time.sleep(min(0.25, 0.05 * attempt))

    if log_failure:
        audit_logger.log(
            "outbound_send_failed",
            target_id=target_id,
            target_type="network",
            details={
                "host": host,
                "port": int(port),
                "message_type": message_type,
                "attempts": attempts,
            },
        )
    return False


def send_or_log(host: str, port: int, payload: bytes, *, message_type: str, target_id: str) -> bool:
    return _send_with_retries(
        host,
        int(port),
        payload,
        message_type=message_type,
        target_id=target_id,
        log_failure=True,
    )


def send_to_peer_or_log(
    peer_id: str,
    payload: bytes,
    *,
    message_type: str,
    target_id: str,
    include_candidates: bool = False,
    candidate_limit: int = 1,
    verified_limit: int = 4,
    fallback_addr: tuple[str, int] | None = None,
    send_attempt: Callable[[str, int, bytes], bool] | None = None,
) -> bool:
    targets = delivery_targets_for_peer(
        peer_id,
        verified_limit=verified_limit,
        include_candidates=include_candidates,
        candidate_limit=candidate_limit,
    )
    attempted: list[dict[str, object]] = []

    def _try(host: str, port: int) -> bool:
        if send_attempt is not None:
            return bool(send_attempt(host, int(port), payload))
        return _send_with_retries(
            host,
            int(port),
            payload,
            message_type=message_type,
            target_id=target_id,
            log_failure=False,
        )

    for target in targets:
        ok = _try(target.host, int(target.port))
        attempted.append(
            {
                "host": target.host,
                "port": int(target.port),
                "source": target.source,
                "verified": bool(target.verified),
                "delivered": bool(ok),
            }
        )
        if target.verified:
            note_verified_peer_endpoint_delivery_result(
                peer_id,
                target.host,
                int(target.port),
                delivered=bool(ok),
            )
        else:
            note_peer_endpoint_candidate_probe_result(
                peer_id,
                target.host,
                int(target.port),
                source=target.source,
                delivered=bool(ok),
            )
        if ok:
            return True

    if fallback_addr is not None:
        fallback_host, fallback_port = str(fallback_addr[0]), int(fallback_addr[1])
        if (fallback_host, fallback_port) not in {
            (str(item["host"]), int(item["port"])) for item in attempted
        }:
            ok = _try(fallback_host, fallback_port)
            attempted.append(
                {
                    "host": fallback_host,
                    "port": fallback_port,
                    "source": "fallback",
                    "verified": False,
                    "delivered": bool(ok),
                }
            )
            if ok:
                return True

    audit_logger.log(
        "outbound_peer_send_failed",
        target_id=target_id,
        target_type="network",
        details={
            "peer_id": peer_id,
            "message_type": message_type,
            "attempted_endpoints": attempted,
        },
    )
    return False


def broadcast_to_recent_peers(
    payload: bytes,
    *,
    message_type: str,
    target_id: str,
    limit: int = 32,
    fanout: int | None = None,
    exclude_peer_ids: set[str] | None = None,
    include_candidates: bool = False,
    candidate_limit: int = 1,
    verified_limit: int = 4,
    send_attempt: Callable[[str, int, bytes], bool] | None = None,
) -> int:
    excluded = {str(item).strip() for item in set(exclude_peer_ids or set()) if str(item).strip()}
    excluded.add(local_peer_id())
    peers = recent_peer_verified_endpoints(
        exclude_peer_id=local_peer_id(),
        limit=max(1, int(limit)),
        per_peer_limit=1,
    )
    sent = 0
    seen_peers: set[str] = set()
    for endpoint in peers:
        peer_id = str(endpoint.peer_id or "").strip()
        if not peer_id or peer_id in excluded or peer_id in seen_peers:
            continue
        seen_peers.add(peer_id)
        if send_to_peer_or_log(
            peer_id,
            payload,
            message_type=message_type,
            target_id=target_id,
            include_candidates=include_candidates,
            candidate_limit=candidate_limit,
            verified_limit=verified_limit,
            send_attempt=send_attempt,
        ):
            sent += 1
        if fanout is not None and sent >= max(0, int(fanout)):
            break
    return sent
