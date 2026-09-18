from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.daemon.messages import handle_report_abuse
from storage.migrations import run_migrations


def test_handle_report_abuse_routes_through_peer_broadcast_helper() -> None:
    run_migrations()
    payload = {
        "report_id": "report-" + ("a" * 32),
        "accused_peer_id": "peer-" + ("b" * 32),
        "signal_type": "spam_wave",
        "severity": 0.9,
        "details": {"source": "test"},
        "ttl": 2,
    }
    daemon = SimpleNamespace()

    with mock.patch("storage.abuse_gossip_store.mark_report_seen", return_value=True), mock.patch(
        "storage.abuse_gossip_store.allow_reporter_report",
        return_value=True,
    ), mock.patch("core.daemon.messages.peer_trust", return_value=0.9), mock.patch(
        "core.fraud_engine.record_signal"
    ) as record_signal, mock.patch(
        "core.daemon.messages.broadcast_to_recent_peers",
        return_value=3,
    ) as broadcast_to_recent_peers, mock.patch("core.daemon.messages.audit_logger.log"):
        handle_report_abuse(daemon, payload, "sender-peer", ("198.51.100.60", 49160))

    record_signal.assert_called_once()
    broadcast_to_recent_peers.assert_called_once()
    kwargs = broadcast_to_recent_peers.call_args.kwargs
    assert kwargs["message_type"] == "REPORT_ABUSE"
    assert kwargs["target_id"] == payload["report_id"]
    assert kwargs["fanout"] >= 1
    assert kwargs["exclude_peer_ids"] == {"sender-peer"}
