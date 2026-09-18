"""OC L1 totality closure proof: the live ungated hive outbound lanes go
through THE one outbound door (`core.remote_fetch_policy.open_remote`).

- With the per-turn veto active (`allow_remote_fetch=False`), NO socket opens:
  `urllib.request.urlopen` is replaced by an AssertionError-raiser, so any raw
  socket attempt fails the test loudly instead of silently reaching the network.
  The failure surfaces as `RemoteFetchRefusedError` (typed, fail-closed).
- Without the veto, a bare `PublicHiveBridge()` (as built by apps/vool_agent.py
  and core/adaptation_autopilot.py) routes through the door and reports the
  fetch into the owning turn's ledger.
"""
from __future__ import annotations

import json

import pytest

from core.public_hive.client import PublicHiveHttpClient, _door_urlopen
from core.public_hive.config import PublicHiveBridgeConfig


class _FakeResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _client() -> PublicHiveHttpClient:
    return PublicHiveHttpClient(PublicHiveBridgeConfig(request_timeout_seconds=3))


# --- (a) veto active: no socket, typed failure ------------------------------


def test_veto_blocks_hive_read_before_any_socket(monkeypatch):
    from core.remote_fetch_policy import (
        RemoteFetchRefusedError,
        remote_fetch_policy_scope,
    )

    def _no_socket(request, timeout=None, context=None):
        raise AssertionError(f"raw socket opened despite veto: {getattr(request, 'full_url', '')}")

    monkeypatch.setattr("urllib.request.urlopen", _no_socket)
    with pytest.raises(RemoteFetchRefusedError):
        with remote_fetch_policy_scope({"allow_remote_fetch": False}):
            _client().get_json("https://hive.example.invalid", "/v1/presence")


def test_veto_blocks_replication_snapshot_before_any_socket(monkeypatch):
    from core.meet_and_greet_replication import HttpMeetClient
    from core.remote_fetch_policy import (
        RemoteFetchRefusedError,
        remote_fetch_policy_scope,
    )

    def _no_socket(request, timeout=None, context=None):
        raise AssertionError(f"raw socket opened despite veto: {getattr(request, 'full_url', '')}")

    monkeypatch.setattr("urllib.request.urlopen", _no_socket)
    http_client = HttpMeetClient(timeout_seconds=3)
    with pytest.raises(RemoteFetchRefusedError):
        with remote_fetch_policy_scope({"allow_remote_fetch": False}):
            http_client._get_json("https://node.example.invalid/v1/index/snapshot")


# --- (b) no veto: the call goes through the door and reports ----------------


def test_bare_bridge_default_opener_is_the_door(monkeypatch):
    """Production constructs PublicHiveBridge() bare -- its default opener must
    be the canonical door, not urllib.request.urlopen."""
    import urllib.request

    from core.public_hive.bridge import PublicHiveBridge

    assert PublicHiveBridge(PublicHiveBridgeConfig())._client._urlopen is _door_urlopen
    assert _door_urlopen is not urllib.request.urlopen


def test_hive_read_traverses_open_remote_and_reports(monkeypatch):
    from core.remote_fetch_policy import (
        open_remote,
        remote_fetch_attempt_count,
        remote_fetch_attempts,
        remote_fetch_policy_scope,
    )

    captured: list[str] = []
    real_open_remote = open_remote

    def _recording_door(request, *, timeout):
        captured.append(str(getattr(request, "full_url", "")))
        return real_open_remote(request, timeout=timeout)

    monkeypatch.setattr("core.remote_fetch_policy.open_remote", _recording_door)
    # The REAL door underneath must hit this fake socket and report the fetch.
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None, context=None: _FakeResponse({"ok": True, "result": {"agents": []}}),
    )
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        result = _client().get_json("https://hive.example.invalid", "/v1/presence")
        assert result == {"agents": []}
        assert captured == ["https://hive.example.invalid/v1/presence"]
        # Reported into the owning turn's ledger, with the address.
        assert remote_fetch_attempt_count() == 1
        entry = remote_fetch_attempts()[0]
        assert entry["url"] == "https://hive.example.invalid/v1/presence"
        assert entry["host"] == "hive.example.invalid"


def test_replication_get_json_reports_through_the_door(monkeypatch):
    from core.meet_and_greet_replication import HttpMeetClient
    from core.remote_fetch_policy import (
        remote_fetch_attempt_count,
        remote_fetch_attempts,
        remote_fetch_policy_scope,
    )

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None, context=None: _FakeResponse(
            {"ok": True, "result": {"entries": []}}
        ),
    )
    http_client = HttpMeetClient(timeout_seconds=3)
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        obj = http_client._get_json("https://node.example.invalid/v1/index/snapshot")
        assert obj["ok"] is True
        assert remote_fetch_attempt_count() == 1
        assert remote_fetch_attempts()[0]["host"] == "node.example.invalid"


# --- standing source guard ---------------------------------------------------


def test_hive_lanes_carry_no_raw_urlopen_default():
    """The four rebound sites must not fall back to a raw socket as their default."""
    import pathlib

    for rel, marker in (
        ("core/public_hive/client.py", "_door_urlopen"),
        ("core/public_hive/bridge.py", "_door_urlopen"),
        ("core/hive_activity_tracker.py", "open_remote"),
        ("core/meet_and_greet_replication.py", "open_remote"),
    ):
        source = pathlib.Path(rel).read_text()
        assert marker in source, f"{rel} lost its door binding"
    bridge_source = pathlib.Path("core/public_hive/bridge.py").read_text()
    assert "urllib.request.urlopen" not in bridge_source
