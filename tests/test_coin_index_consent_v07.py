"""Coin identity refresh cannot bypass fresh-profile consent or the HTTP door."""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import policy_engine, runtime_paths
from core.auto_local_only_mode import CONTEXT_KEY
from core.effect_gateway import (
    current_effect_ledger,
    effect_outcomes,
    effect_receipts,
    named_background_effect_scope,
)
from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope
from tools.web import coin_index

_ROWS = [
    {"symbol": "vexx", "id": "vex-network", "market_cap_rank": 94},
    {"symbol": "sui", "id": "sui", "market_cap_rank": 33},
    {"symbol": "vexx", "id": "vex-impostor", "market_cap_rank": 240},
    {"symbol": "zzzz", "id": "below-ceiling", "market_cap_rank": 501},
]


@pytest.fixture
def fresh_index_home(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    old_override = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(home))
    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
        monkeypatch.delenv(name, raising=False)
    runtime_paths.configure_runtime_home(home)
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", None)
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE_BINDING", None)
    monkeypatch.setattr(coin_index, "_cache_path", lambda: home / "cache" / "coingecko_rank_index.json")
    coin_index.reset_cache_for_test()
    try:
        yield home
    finally:
        coin_index.reset_cache_for_test()
        runtime_paths.configure_runtime_home(old_override)


@pytest.fixture
def index_service(monkeypatch):
    requests = []
    response = {"status": 200, "body": json.dumps(_ROWS).encode()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append({"path": self.path, "user_agent": self.headers.get("User-Agent")})
            self.send_response(response["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response["body"])))
            self.end_headers()
            self.wfile.write(response["body"])

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setattr(coin_index, "_INDEX_URL", f"http://127.0.0.1:{server.server_port}/coins/markets")
    try:
        yield requests, response
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.fixture
def dns_observer(monkeypatch):
    calls = []
    original = socket.getaddrinfo

    def observed(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", observed)
    return calls


def _enable_web():
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})


def test_fresh_semantic_resolution_does_not_reach_public_index_before_consent(
    fresh_index_home, dns_observer, monkeypatch,
):
    from core.agent_runtime.fast_live_info_price import price_assets_named

    transport = []

    def refused_transport(*args, **kwargs):
        transport.append(args)
        raise AssertionError("a fresh profile reached HTTP transport")

    monkeypatch.setattr(coin_index.urllib.request, "urlopen", refused_transport)
    context = {"surface": "api"}
    with remote_fetch_policy_scope(context):
        assert price_assets_named("what is the price of VEXX and BTC?") == ["btc"]
        assert coin_index.resolve_symbol("sui") == ""
        assert remote_fetch_attempt_count() == 0
    assert policy_engine.allow_web_fallback() is False
    assert policy_engine.automatic_outbound_enabled() is False
    assert transport == []
    assert dns_observer == []
    assert coin_index._memory_index is None
    assert not coin_index._cache_path().exists()


def test_saved_web_consent_fetches_real_http_and_preserves_upgrade_policy(
    fresh_index_home, index_service,
):
    from tools.web.web_research import _looks_like_price_query_all

    path = fresh_index_home / "config" / "default_policy.yaml"
    path.parent.mkdir(parents=True)
    saved = b"system:\n  allow_web_fallback: true\nnetwork:\n  outbound_enabled: false\n"
    path.write_bytes(saved)
    policy_engine.load(force_reload=True)
    context = {"surface": "api"}
    with remote_fetch_policy_scope(context):
        assert _looks_like_price_query_all("VEXX and SUI price") == ["vex-network", "sui"]
        assert remote_fetch_attempt_count() == 1
        receipts = effect_receipts()
        assert [entry["lifecycle"] for entry in receipts] == ["authorized", "started", "succeeded"]
        outcomes = effect_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0]["provider_id"] == "coingecko"
        assert outcomes[0]["keyed_or_keyless"] == "keyless"
        assert outcomes[0]["lifecycle"] == "succeeded"
        assert outcomes[0]["transport_ran"] is True
        assert {entry["effect_id"] for entry in receipts} == {outcomes[0]["effect_id"]}
    assert index_service[0] == [{"path": "/coins/markets", "user_agent": "VOOL-PRICE/1.0"}]
    assert coin_index.resolve_symbol("zzzz") == ""
    assert path.read_bytes() == saved
    assert policy_engine.automatic_outbound_enabled() is False


def test_permission_refusal_does_not_delay_new_web_consent(
    fresh_index_home, index_service, dns_observer, monkeypatch,
):
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == ""
    assert dns_observer == []
    assert coin_index._memory_index is None
    monkeypatch.setenv("VOOL_ENABLE_WEB", "1")
    policy_engine.load(force_reload=True)
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
        assert remote_fetch_attempt_count() == 1
    assert len(index_service[0]) == 1
    assert dns_observer == ["127.0.0.1"]
    assert policy_engine.automatic_outbound_enabled() is False


def test_unscoped_refresh_cannot_authorize_itself_and_named_owner_can_recover(
    fresh_index_home, index_service, dns_observer,
):
    _enable_web()
    assert current_effect_ledger() is None
    assert coin_index.warm_index() is False
    assert current_effect_ledger() is None
    assert coin_index._memory_index is None
    assert dns_observer == []
    assert index_service[0] == []
    with named_background_effect_scope("test.coin_index.explicit_owner"):
        assert coin_index.warm_index() is True
        assert [entry["lifecycle"] for entry in effect_receipts()] == ["authorized", "started", "succeeded"]
    assert len(index_service[0]) == 1


def test_turn_veto_does_not_poison_a_later_permitted_turn(
    fresh_index_home, index_service, dns_observer,
):
    _enable_web()
    with remote_fetch_policy_scope({"surface": "api", "allow_remote_fetch": False}):
        assert coin_index.resolve_symbol("vexx") == ""
        assert remote_fetch_attempt_count() == 0
    assert dns_observer == []
    assert coin_index._memory_index is None
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
        assert remote_fetch_attempt_count() == 1
    assert len(index_service[0]) == 1


def test_mode_refusal_is_receipted_and_does_not_delay_a_permitted_new_turn(
    fresh_index_home, index_service, dns_observer, monkeypatch,
):
    from core import mode_permission_policy

    _enable_web()
    monkeypatch.setattr(mode_permission_policy, "_ACTIVE_MODES", dict(mode_permission_policy._ACTIVE_MODES))
    session = "coin-index-permission-control"
    context = {"surface": "api", "session_id": session}
    mode_permission_policy.set_active_mode(session, "plan")
    with remote_fetch_policy_scope(context):
        assert coin_index.resolve_symbol("vexx") == ""
        assert remote_fetch_attempt_count() == 0
        assert [entry["lifecycle"] for entry in effect_receipts()] == ["denied"]
    assert dns_observer == []
    assert coin_index._memory_index is None
    mode_permission_policy.set_active_mode(session, "manual")
    with remote_fetch_policy_scope(context):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
        assert remote_fetch_attempt_count() == 1
    assert len(index_service[0]) == 1


def test_local_only_veto_does_not_poison_a_later_permitted_turn(
    fresh_index_home, index_service, dns_observer,
):
    _enable_web()
    with remote_fetch_policy_scope({"surface": "api", CONTEXT_KEY: True}):
        assert coin_index.resolve_symbol("vexx") == ""
        assert remote_fetch_attempt_count() == 0
    assert dns_observer == []
    assert coin_index._memory_index is None
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
    assert len(index_service[0]) == 1


def test_cached_identities_remain_available_without_new_egress(
    fresh_index_home, index_service, dns_observer,
):
    _enable_web()
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
    policy_engine.set_operator_policy_values({"system.allow_web_fallback": False})
    coin_index.reset_cache_for_test()
    dns_observer.clear()
    with remote_fetch_policy_scope({"surface": "api", "allow_remote_fetch": False}):
        assert coin_index.resolve_symbol("vexx") == "vex-network"
        assert coin_index.resolve_symbol("sui") == "sui"
        assert remote_fetch_attempt_count() == 0
    assert dns_observer == []
    assert len(index_service[0]) == 1


def test_real_rate_limit_is_accounted_and_negative_cached_only_after_an_attempt(
    fresh_index_home, index_service,
):
    _enable_web()
    index_service[1]["status"] = 429
    with remote_fetch_policy_scope({"surface": "api"}):
        assert coin_index.resolve_symbol("vexx") == ""
        assert coin_index.resolve_symbol("sui") == ""
        assert remote_fetch_attempt_count() == 1
        assert [entry["lifecycle"] for entry in effect_receipts()] == ["authorized", "started", "failed"]
    assert len(index_service[0]) == 1
    assert coin_index._memory_index == {}
    assert not coin_index._cache_path().exists()
