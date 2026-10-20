"""The model radar's hourly catalog fetch is automatic outbound work.

A fresh install ships ``network.outbound_enabled: false``, so nothing may go online until
the operator allows it. These tests drive the real chat-shell poll route
(``/api/model-radar/feed``) and the observer's scheduled cycle against an isolated
profile: a fresh one, and upgraded ones carrying a saved policy file. Any socket the
process tries to open while outbound is off fails the test.
"""

from __future__ import annotations

import copy
import json
import socket
import sqlite3

import pytest
import yaml

from core import policy_engine, runtime_paths


class _OutboundAttempted(AssertionError):
    pass


@pytest.fixture()
def profile(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    old_override = runtime_paths._VOOL_HOME_OVERRIDE
    old_policy = copy.deepcopy(policy_engine._POLICY_CACHE)
    old_binding = policy_engine._POLICY_CACHE_BINDING
    monkeypatch.setenv("VOOL_HOME", str(home))
    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB", "VOOL_MODEL_RADAR_POLLER"):
        monkeypatch.delenv(name, raising=False)
    runtime_paths.configure_runtime_home(home)
    policy_engine._POLICY_CACHE = None

    db_file = tmp_path / "radar.db"

    def _fresh_connection():
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("storage.model_radar.get_connection", _fresh_connection)

    from core import model_radar_service

    monkeypatch.setattr(model_radar_service, "_observer_started", False)
    started: list[object] = []

    class _RecordingThread:
        def __init__(self, *, target, name, daemon):
            self.target, self.name, self.daemon = target, name, daemon

        def start(self):
            started.append(self)

    monkeypatch.setattr(model_radar_service.threading, "Thread", _RecordingThread)

    sockets: list[tuple] = []

    def _refuse(*args, **kwargs):
        sockets.append(args)
        raise _OutboundAttempted(f"outbound network attempt: {args!r}")

    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a: _refuse(*a))

    door_calls: list[str] = []

    class _Answer:
        status = 200

        def read(self):
            return json.dumps({
                "data": [{
                    "id": "vendor/alpha",
                    "name": "Alpha",
                    "context_length": 8192,
                    "pricing": {"prompt": "0.000002", "completion": "0.000008"},
                }]
            }).encode("utf-8")

    def _door(url, **kwargs):
        door_calls.append(url)
        return _Answer()

    monkeypatch.setattr("core.openrouter_catalog.open_remote_url", _door)
    try:
        yield {"home": home, "started": started, "sockets": sockets, "door": door_calls}
    finally:
        runtime_paths.configure_runtime_home(old_override)
        policy_engine._POLICY_CACHE = old_policy
        policy_engine._POLICY_CACHE_BINDING = old_binding


def _save_policy(home, policy: dict) -> None:
    path = home / "config" / "default_policy.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    policy_engine.load(force_reload=True)


def _poll_feed(host: str = "127.0.0.1") -> int:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/api/model-radar/feed", query={}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host=host,
    )
    return res.status


def _assert_offline(profile) -> None:
    from core import model_radar_service

    assert profile["started"] == [], "the hourly observer must not start"
    assert model_radar_service._observer_started is False, "a later poll must still be able to start it"
    assert model_radar_service._observer_cycle() is False, "a scheduled cycle must not fetch"
    assert profile["door"] == [], "the catalog door must not be called"
    assert profile["sockets"] == [], "no socket may be opened"


def test_fresh_install_radar_makes_no_outbound_call(profile) -> None:
    policy_engine.load(force_reload=True)
    assert policy_engine.get("network.outbound_enabled") is False
    assert not (profile["home"] / "config" / "default_policy.yaml").exists()

    for _ in range(3):  # the chat shell polls every minute
        assert _poll_feed() == 200
    _assert_offline(profile)


@pytest.mark.parametrize(
    "saved",
    [
        {"system": {"local_only_mode": False, "allow_web_fallback": True}},  # web on, outbound key absent
        {"system": {"allow_web_fallback": False}, "network": {"outbound_enabled": False}},
        {"system": {"local_only_mode": True}, "network": {"outbound_enabled": True}},  # Local Only outranks
    ],
    ids=["upgrade-web-on-outbound-unset", "upgrade-outbound-off", "upgrade-local-only"],
)
def test_upgraded_profile_without_outbound_consent_stays_offline(profile, saved) -> None:
    _save_policy(profile["home"], saved)
    assert policy_engine.automatic_outbound_enabled() is False

    assert _poll_feed() == 200
    _assert_offline(profile)


def test_web_env_consent_is_not_automatic_outbound_consent(profile, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_ENABLE_WEB", "1")
    policy_engine.load(force_reload=True)
    assert policy_engine.allow_web_fallback() is True

    assert _poll_feed() == 200
    _assert_offline(profile)


def test_radar_fetches_once_the_operator_allows_outbound(profile) -> None:
    from core import model_radar_service
    from storage import model_radar as radar_storage

    assert _poll_feed() == 200
    assert profile["started"] == []

    policy_engine.set_operator_policy_values({"network.outbound_enabled": True})
    assert policy_engine.automatic_outbound_enabled() is True

    assert _poll_feed() == 200
    assert len(profile["started"]) == 1, "the next poll after consent starts the observer"
    thread = profile["started"][0]
    assert thread.target is model_radar_service._observer_loop and thread.daemon is True
    assert _poll_feed() == 200
    assert len(profile["started"]) == 1, "started once per process"

    assert model_radar_service._observer_cycle() is True
    assert profile["door"] == ["https://openrouter.ai/api/v1/models"]
    assert radar_storage.get_observation("openrouter", "vendor/alpha") is not None


def test_upgraded_profile_with_saved_outbound_consent_keeps_fetching(profile) -> None:
    from core import model_radar_service

    _save_policy(profile["home"], {"system": {"local_only_mode": False}, "network": {"outbound_enabled": True}})

    assert _poll_feed() == 200
    assert len(profile["started"]) == 1
    assert model_radar_service._observer_cycle() is True
    assert profile["door"] == ["https://openrouter.ai/api/v1/models"]


def test_turning_outbound_off_stops_the_running_observer(profile) -> None:
    from core import model_radar_service

    policy_engine.set_operator_policy_values({"network.outbound_enabled": True})
    assert _poll_feed() == 200
    assert len(profile["started"]) == 1

    policy_engine.set_operator_policy_values({"network.outbound_enabled": False})
    assert model_radar_service._observer_cycle() is False
    assert profile["door"] == [] and profile["sockets"] == []


def test_unreadable_policy_fails_closed(profile, monkeypatch) -> None:
    from core import model_radar_service

    def _broken():
        raise ValueError("Policy file must contain a YAML mapping/object.")

    monkeypatch.setattr(policy_engine, "automatic_outbound_enabled", _broken)
    assert model_radar_service.ensure_background_observer() is False
    assert model_radar_service._observer_cycle() is False
    assert profile["started"] == [] and profile["door"] == [] and profile["sockets"] == []


def test_explicit_refresh_is_not_gated_by_automatic_outbound(profile) -> None:
    """A Refresh the person clicks is not automatic work: the gate stays on the schedule."""
    from core import cloud_model_control

    assert policy_engine.automatic_outbound_enabled() is False
    result = cloud_model_control.force_catalog_refresh()
    assert result["ok"] is True and result["total"] == 1
    assert profile["door"] == ["https://openrouter.ai/api/v1/models"]
