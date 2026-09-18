from __future__ import annotations

from core.daemon.peer_delivery import broadcast_to_recent_peers, send_to_peer_or_log
from core.discovery_index import (
    candidate_endpoints_for_peer,
    note_verified_peer_endpoint_delivery_result,
    register_peer_endpoint,
    register_peer_endpoint_candidate,
    verified_endpoints_for_peer,
)
from storage.db import get_connection
from storage.migrations import run_migrations


def _clear_tables() -> None:
    conn = get_connection()
    try:
        for table in ("peer_endpoints", "peer_endpoint_observations", "peer_endpoint_candidates"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def test_verified_endpoint_delivery_feedback_reorders_same_source_endpoints() -> None:
    run_migrations()
    _clear_tables()
    peer_id = "peer-delivery-order"
    register_peer_endpoint(peer_id, "198.51.100.10", 49110, source="observed")
    register_peer_endpoint(peer_id, "198.51.100.11", 49111, source="observed")

    note_verified_peer_endpoint_delivery_result(peer_id, "198.51.100.10", 49110, delivered=False)
    note_verified_peer_endpoint_delivery_result(peer_id, "198.51.100.11", 49111, delivered=True)

    endpoints = verified_endpoints_for_peer(peer_id, limit=4)

    assert [(item.host, item.port) for item in endpoints[:2]] == [
        ("198.51.100.11", 49111),
        ("198.51.100.10", 49110),
    ]
    assert endpoints[0].consecutive_delivery_failures == 0
    assert endpoints[0].last_delivery_success_at
    assert endpoints[1].consecutive_delivery_failures == 1
    assert endpoints[1].last_delivery_failure_at


def test_send_to_peer_or_log_falls_back_to_second_verified_endpoint() -> None:
    run_migrations()
    _clear_tables()
    peer_id = "peer-send-fallback"
    register_peer_endpoint(peer_id, "198.51.100.20", 49120, source="observed")
    register_peer_endpoint(peer_id, "198.51.100.21", 49121, source="observed")

    attempted: list[tuple[str, int]] = []

    def _send(host: str, port: int, _payload: bytes) -> bool:
        attempted.append((host, int(port)))
        return (host, int(port)) == ("198.51.100.20", 49120)

    ok = send_to_peer_or_log(
        peer_id,
        b"task-result",
        message_type="TASK_RESULT",
        target_id="task-1",
        send_attempt=_send,
    )

    assert ok is True
    assert attempted == [("198.51.100.21", 49121), ("198.51.100.20", 49120)]
    endpoints = verified_endpoints_for_peer(peer_id, limit=4)
    assert [(item.host, item.port) for item in endpoints[:2]] == [
        ("198.51.100.20", 49120),
        ("198.51.100.21", 49121),
    ]


def test_send_to_peer_or_log_updates_candidate_probe_result_when_candidate_used() -> None:
    run_migrations()
    _clear_tables()
    peer_id = "peer-candidate-fallback"
    register_peer_endpoint_candidate(peer_id, "198.51.100.30", 49130, source="dht")

    ok = send_to_peer_or_log(
        peer_id,
        b"claim",
        message_type="TASK_CLAIM",
        target_id="task-2",
        include_candidates=True,
        send_attempt=lambda host, port, _payload: (host, int(port)) == ("198.51.100.30", 49130),
    )

    assert ok is True
    candidates = candidate_endpoints_for_peer(peer_id, limit=2)
    assert len(candidates) == 1
    assert candidates[0].last_probe_delivery_ok is True
    assert candidates[0].consecutive_probe_failures == 0
    assert candidates[0].last_probe_attempt_at


def test_broadcast_to_recent_peers_uses_ordered_peer_fallback_and_counts_successful_peers() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint("peer-a", "198.51.100.40", 49140, source="observed")
    register_peer_endpoint("peer-a", "198.51.100.41", 49141, source="observed")
    register_peer_endpoint("peer-b", "198.51.100.50", 49150, source="observed")
    note_verified_peer_endpoint_delivery_result("peer-a", "198.51.100.40", 49140, delivered=True)

    attempts: list[tuple[str, int]] = []

    def _send(host: str, port: int, _payload: bytes) -> bool:
        attempts.append((host, int(port)))
        return (host, int(port)) in {
            ("198.51.100.41", 49141),
            ("198.51.100.50", 49150),
        }

    sent = broadcast_to_recent_peers(
        b"hello",
        message_type="HELLO_AD",
        target_id="local-peer",
        limit=4,
        send_attempt=_send,
    )

    assert sent == 2
    assert ("198.51.100.40", 49140) in attempts
    assert ("198.51.100.41", 49141) in attempts
    assert ("198.51.100.50", 49150) in attempts
    assert attempts.index(("198.51.100.40", 49140)) < attempts.index(("198.51.100.41", 49141))
