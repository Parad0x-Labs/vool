from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest import mock

from core.discovery_index import (
    candidate_endpoints_for_peer,
    endpoint_for_peer,
    get_best_helpers,
    note_peer_endpoint_candidate_probe_result,
    recent_peer_endpoint_candidates,
    recent_peer_verified_endpoints,
    record_bootstrap_presence,
    record_signed_peer_endpoint_observation,
    register_peer_endpoint,
    register_peer_endpoint_candidate,
    signed_observed_endpoints_for_peer,
)
from core.maintenance import MaintenanceConfig, MaintenanceLoop
from network.protocol import Protocol, encode_message
from network.signer import get_local_peer_id as local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_tables() -> None:
    conn = get_connection()
    try:
        existing_tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        for table in (
            "agent_capabilities",
            "peers",
            "peer_endpoints",
            "peer_endpoint_observations",
            "peer_endpoint_candidates",
            "presence_leases",
            "replica_table",
        ):
            if table in existing_tables:
                conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()


def _signed_envelope(*, msg_type: str = "PING", payload: dict[str, object] | None = None) -> dict[str, object]:
    raw = encode_message(
        msg_id=str(uuid.uuid4()),
        msg_type=msg_type,
        sender_peer_id=local_peer_id(),
        nonce=uuid.uuid4().hex,
        payload=payload or {},
    )
    return Protocol.decode_and_validate(raw)


def test_get_best_helpers_prefers_fresher_capability_rows() -> None:
    run_migrations()
    _clear_tables()
    record_bootstrap_presence(
        peer_id="fresh-peer",
        status="idle",
        capabilities=["code"],
        capacity=2,
        trust_score=0.8,
    )
    record_bootstrap_presence(
        peer_id="stale-peer",
        status="idle",
        capabilities=["code"],
        capacity=2,
        trust_score=0.8,
    )
    stale_seen = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE agent_capabilities SET last_seen_at = ? WHERE peer_id = ?",
            (stale_seen, "stale-peer"),
        )
        conn.commit()
    finally:
        conn.close()

    helpers = get_best_helpers(required_capabilities=["code"], limit=2)

    assert [item.peer_id for item in helpers[:2]] == ["fresh-peer", "stale-peer"]
    assert helpers[0].freshness_score > helpers[1].freshness_score


def test_recent_peer_endpoint_candidates_returns_candidate_rows_without_promoting_them() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.55", 49555, source="dht")

    candidates = recent_peer_endpoint_candidates(limit=4)

    assert len(candidates) == 1
    assert (candidates[0].peer_id, candidates[0].host, candidates[0].port, candidates[0].source) == (
        "candidate-peer",
        "198.51.100.55",
        49555,
        "dht",
    )
    assert endpoint_for_peer("candidate-peer") is None


def test_record_signed_peer_endpoint_observation_persists_liveness_proof_and_promotes_selected_endpoint() -> None:
    run_migrations()
    _clear_tables()
    peer_id = local_peer_id()

    record_signed_peer_endpoint_observation(
        peer_id,
        "198.51.100.70",
        49570,
        envelope=_signed_envelope(),
    )

    proofs = signed_observed_endpoints_for_peer(peer_id)
    assert len(proofs) == 1
    assert (proofs[0].host, proofs[0].port, proofs[0].source) == ("198.51.100.70", 49570, "observed")
    assert proofs[0].verification_kind == "protocol_signature"
    assert proofs[0].proof_message_type == "PING"
    assert proofs[0].proof_message_id
    assert proofs[0].proof_count == 1
    assert proofs[0].proof_hash
    assert endpoint_for_peer(peer_id) == ("198.51.100.70", 49570)


def test_recent_peer_verified_endpoints_includes_signed_backup_before_candidates() -> None:
    run_migrations()
    _clear_tables()
    peer_id = local_peer_id()
    register_peer_endpoint(peer_id, "203.0.113.20", 49020, source="bootstrap")
    record_signed_peer_endpoint_observation(
        peer_id,
        "203.0.113.21",
        49021,
        envelope=_signed_envelope(msg_type="HEARTBEAT"),
    )
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.61", 49561, source="dht")

    endpoints = recent_peer_verified_endpoints(limit=3, per_peer_limit=2)

    assert [(item.peer_id, item.host, item.port) for item in endpoints[:2]] == [
        (peer_id, "203.0.113.21", 49021),
        (peer_id, "203.0.113.20", 49020),
    ]


def test_recent_peer_endpoint_candidates_skips_recent_failures_within_cooldown() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.56", 49556, source="dht")
    note_peer_endpoint_candidate_probe_result(
        "candidate-peer",
        "198.51.100.56",
        49556,
        source="dht",
        delivered=False,
    )

    candidates = recent_peer_endpoint_candidates(limit=4, cooldown_seconds=600, max_consecutive_failures=3)

    assert candidates == []


def test_recent_peer_endpoint_candidates_excludes_over_failure_limit() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.57", 49557, source="dht")
    for _ in range(3):
        note_peer_endpoint_candidate_probe_result(
            "candidate-peer",
            "198.51.100.57",
            49557,
            source="dht",
            delivered=False,
        )

    candidates = recent_peer_endpoint_candidates(limit=4, cooldown_seconds=0, max_consecutive_failures=3)

    assert candidates == []


def test_maintenance_tick_uses_candidate_endpoints_when_verified_coverage_is_sparse() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.60", 49560, source="dht")
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            bootstrap_topics=[],
            dht_discovery_min_nodes=20,
            dht_discovery_verified_limit=4,
            dht_discovery_candidate_limit=2,
            dht_discovery_min_verified_endpoints=1,
        )
    )

    with mock.patch("core.maintenance.publish_local_presence_snapshots"), mock.patch(
        "core.maintenance.sync_from_bootstrap_topics"
    ), mock.patch("core.maintenance.prune_stale_capabilities", return_value=0), mock.patch(
        "core.maintenance.prune_expired_presence",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_holders", return_value=0), mock.patch(
        "core.maintenance.reap_stale_subtasks",
        return_value=0,
    ), mock.patch("core.maintenance.release_mature_pending_rewards", return_value=0), mock.patch(
        "core.maintenance.finalize_confirmed_rewards",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_topic_files", return_value=0), mock.patch(
        "core.maintenance.initiate_random_hardware_challenge"
    ), mock.patch("core.maintenance.schedule_adaptation_autopilot_tick"), mock.patch(
        "core.maintenance.sync_control_plane_workspace",
        return_value={"ok": True, "writes": 0},
    ), mock.patch("network.signer.get_local_peer_id", return_value="local-peer"), mock.patch(
        "network.dht.get_routing_table",
        return_value=mock.Mock(nodes={}),
    ), mock.patch("network.assist_router.build_find_node_message", return_value=b"find-node"), mock.patch(
        "network.transport.send_message",
        return_value=True,
    ) as send_message:
        loop.run_tick()

    send_message.assert_called_once_with("198.51.100.60", 49560, b"find-node")
    assert endpoint_for_peer("candidate-peer") is None


def test_maintenance_tick_prefers_verified_endpoints_before_candidates() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint("verified-peer", "203.0.113.20", 49020, source="observed")
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.61", 49561, source="dht")
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            bootstrap_topics=[],
            dht_discovery_min_nodes=20,
            dht_discovery_verified_limit=4,
            dht_discovery_candidate_limit=2,
            dht_discovery_min_verified_endpoints=1,
        )
    )

    with mock.patch("core.maintenance.publish_local_presence_snapshots"), mock.patch(
        "core.maintenance.sync_from_bootstrap_topics"
    ), mock.patch("core.maintenance.prune_stale_capabilities", return_value=0), mock.patch(
        "core.maintenance.prune_expired_presence",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_holders", return_value=0), mock.patch(
        "core.maintenance.reap_stale_subtasks",
        return_value=0,
    ), mock.patch("core.maintenance.release_mature_pending_rewards", return_value=0), mock.patch(
        "core.maintenance.finalize_confirmed_rewards",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_topic_files", return_value=0), mock.patch(
        "core.maintenance.initiate_random_hardware_challenge"
    ), mock.patch("core.maintenance.schedule_adaptation_autopilot_tick"), mock.patch(
        "core.maintenance.sync_control_plane_workspace",
        return_value={"ok": True, "writes": 0},
    ), mock.patch("network.signer.get_local_peer_id", return_value="local-peer"), mock.patch(
        "network.dht.get_routing_table",
        return_value=mock.Mock(nodes={}),
    ), mock.patch("network.assist_router.build_find_node_message", return_value=b"find-node"), mock.patch(
        "network.transport.send_message",
        return_value=True,
    ) as send_message:
        loop.run_tick()

    send_message.assert_called_once_with("203.0.113.20", 49020, b"find-node")


def test_maintenance_tick_uses_signed_backup_endpoint_before_candidate_when_verified_truth_exists() -> None:
    run_migrations()
    _clear_tables()
    peer_id = local_peer_id()
    register_peer_endpoint(peer_id, "203.0.113.30", 49030, source="bootstrap")
    record_signed_peer_endpoint_observation(
        peer_id,
        "203.0.113.31",
        49031,
        envelope=_signed_envelope(msg_type="HEARTBEAT"),
    )
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.63", 49563, source="dht")
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            bootstrap_topics=[],
            dht_discovery_min_nodes=20,
            dht_discovery_verified_limit=4,
            dht_discovery_candidate_limit=2,
            dht_discovery_min_verified_endpoints=1,
        )
    )

    with mock.patch("core.maintenance.publish_local_presence_snapshots"), mock.patch(
        "core.maintenance.sync_from_bootstrap_topics"
    ), mock.patch("core.maintenance.prune_stale_capabilities", return_value=0), mock.patch(
        "core.maintenance.prune_expired_presence",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_holders", return_value=0), mock.patch(
        "core.maintenance.reap_stale_subtasks",
        return_value=0,
    ), mock.patch("core.maintenance.release_mature_pending_rewards", return_value=0), mock.patch(
        "core.maintenance.finalize_confirmed_rewards",
        return_value=0,
    ), mock.patch("core.maintenance.prune_expired_topic_files", return_value=0), mock.patch(
        "core.maintenance.initiate_random_hardware_challenge"
    ), mock.patch("core.maintenance.schedule_adaptation_autopilot_tick"), mock.patch(
        "core.maintenance.sync_control_plane_workspace",
        return_value={"ok": True, "writes": 0},
    ), mock.patch("network.signer.get_local_peer_id", return_value="other-local-peer"), mock.patch(
        "network.dht.get_routing_table",
        return_value=mock.Mock(nodes={}),
    ), mock.patch("network.assist_router.build_find_node_message", return_value=b"find-node"), mock.patch(
        "network.transport.send_message",
        return_value=True,
    ) as send_message:
        loop.run_tick()

    assert send_message.call_args_list == [
        mock.call("203.0.113.31", 49031, b"find-node"),
        mock.call("203.0.113.30", 49030, b"find-node"),
    ]


def test_maintenance_tick_records_candidate_probe_failures_and_cools_down_retry() -> None:
    run_migrations()
    _clear_tables()
    register_peer_endpoint_candidate("candidate-peer", "198.51.100.62", 49562, source="dht")
    loop = MaintenanceLoop(
        config=MaintenanceConfig(
            bootstrap_topics=[],
            dht_discovery_min_nodes=20,
            dht_discovery_verified_limit=4,
            dht_discovery_candidate_limit=2,
            dht_discovery_min_verified_endpoints=1,
            dht_candidate_probe_cooldown_seconds=600,
            dht_candidate_probe_failure_limit=3,
        )
    )

    def _run_tick(send_ok: bool) -> mock.Mock:
        with ExitStack() as stack:
            stack.enter_context(mock.patch("core.maintenance.publish_local_presence_snapshots"))
            stack.enter_context(mock.patch("core.maintenance.sync_from_bootstrap_topics"))
            stack.enter_context(mock.patch("core.maintenance.prune_stale_capabilities", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.prune_expired_presence", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.prune_expired_holders", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.reap_stale_subtasks", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.release_mature_pending_rewards", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.finalize_confirmed_rewards", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.prune_expired_topic_files", return_value=0))
            stack.enter_context(mock.patch("core.maintenance.initiate_random_hardware_challenge"))
            stack.enter_context(mock.patch("core.maintenance.schedule_adaptation_autopilot_tick"))
            stack.enter_context(mock.patch("core.maintenance.sync_control_plane_workspace", return_value={"ok": True, "writes": 0}))
            stack.enter_context(mock.patch("network.signer.get_local_peer_id", return_value="local-peer"))
            stack.enter_context(mock.patch("network.dht.get_routing_table", return_value=mock.Mock(nodes={})))
            stack.enter_context(mock.patch("network.assist_router.build_find_node_message", return_value=b"find-node"))
            send_message = stack.enter_context(mock.patch("network.transport.send_message", return_value=send_ok))
            loop.run_tick()
            return send_message

    send_message = _run_tick(False)
    send_message.assert_called_once_with("198.51.100.62", 49562, b"find-node")
    candidate = candidate_endpoints_for_peer("candidate-peer")[0]
    assert candidate.consecutive_probe_failures == 1
    assert candidate.last_probe_delivery_ok is False
    assert candidate.last_probe_attempt_at

    send_message = _run_tick(True)
    send_message.assert_not_called()
