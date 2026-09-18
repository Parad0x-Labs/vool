from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_daemon import DaemonConfig, VoolDaemon


def test_daemon_uses_runtime_port_for_advertised_endpoint_after_udp_fallback() -> None:
    config = DaemonConfig(bind_host="127.0.0.1", bind_port=49152, advertise_host="198.51.100.20")
    daemon = VoolDaemon(config)
    runtime = SimpleNamespace(
        host="127.0.0.1",
        port=55123,
        public_host="203.0.113.10",
        public_port=55123,
        running=True,
    )
    fake_transport = mock.Mock()
    fake_transport.start.return_value = runtime
    relay_mode = SimpleNamespace(mode="direct")
    nat_probe = SimpleNamespace(mode="wan_direct")
    fake_maintenance = mock.Mock()
    fake_thread = mock.Mock()

    peer_id = "peer-000000000001"

    with mock.patch("core.agent_runtime.daemon.setup_logging"), mock.patch(
        "core.agent_runtime.daemon.policy_engine.get",
        side_effect=lambda _path, default=None: default,
    ), mock.patch(
        "core.agent_runtime.daemon.required_pow_difficulty",
        return_value=4,
    ), mock.patch(
        "core.agent_runtime.daemon.generate_pow",
        return_value="nonce",
    ), mock.patch(
        "core.agent_runtime.daemon.register_capability_ad",
    ), mock.patch(
        "core.agent_runtime.daemon.sync_local_learning_shards",
    ), mock.patch(
        "core.agent_runtime.daemon.UDPTransportServer",
        return_value=fake_transport,
    ), mock.patch(
        "network.nat_probe.detect_local_host",
        return_value="127.0.0.1",
    ), mock.patch(
        "network.nat_probe.classify_nat",
        return_value=nat_probe,
    ), mock.patch(
        "network.relay_fallback.choose_relay_mode",
        return_value=relay_mode,
    ), mock.patch(
        "network.bootstrap_node.upsert_bootstrap_peer",
    ) as upsert_bootstrap_peer, mock.patch(
        "core.agent_runtime.daemon.register_peer_endpoint",
    ) as register_peer_endpoint, mock.patch(
        "core.agent_runtime.daemon.MaintenanceLoop",
        return_value=fake_maintenance,
    ), mock.patch(
        "core.agent_runtime.daemon.broadcast_hello",
    ), mock.patch(
        "core.agent_runtime.daemon.broadcast_local_knowledge_ads",
    ), mock.patch.object(
        daemon,
        "_refresh_assist_status",
        return_value=True,
    ), mock.patch.object(
        daemon,
        "_start_health_server",
    ), mock.patch(
        "core.agent_runtime.daemon.threading.Thread",
        return_value=fake_thread,
    ), mock.patch(
        "core.agent_runtime.daemon.local_peer_id",
        return_value=peer_id,
    ), mock.patch(
        "core.agent_runtime.daemon.audit_logger.log",
    ):
        started = daemon.start()

    assert started.port == 55123
    assert daemon.config.bind_port == 55123
    register_peer_endpoint.assert_called_once_with(peer_id, "198.51.100.20", 55123, source="self")
    upsert_bootstrap_peer.assert_called_once_with(peer_id, "198.51.100.20", 55123, "direct")
