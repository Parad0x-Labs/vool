"""The one Tor VOOL runs: started only on request, shared, stopped when nobody needs it.

Runs against tests/fixtures/fake_tor.py, which speaks the command line and control protocol
VOOL uses, so this holds on machines without Tor. (Checked against the real tor 0.4.9 too:
start, auto ports, cookie sign-in, onion add / re-add with a kept key / remove, shared users,
and Tor exiting when its owner dies.)
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
from pathlib import Path

import pytest

from core import private_web, tor_runtime
from core.tor_runtime import TorRuntime, TorUnavailable
from tests.first_run_pact_rig import pact_rig  # noqa: F401 - fixture

FAKE = Path(__file__).parent / "fixtures" / "fake_tor.py"


@pytest.fixture
def fake_tor(tmp_path, monkeypatch) -> str:
    launcher = tmp_path / "tor"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "0")
    return str(launcher)


@pytest.fixture
def runtime(tmp_path, fake_tor):
    rt = TorRuntime(data_dir=tmp_path / "data" / "tor", binary=fake_tor)
    yield rt
    rt.stop()


def _greets(port: int) -> bool:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(b"\x05\x01\x00")
        return sock.recv(2) == b"\x05\x00"


def test_tor_is_not_running_until_a_feature_asks(runtime) -> None:
    assert not runtime.running
    assert runtime.socks_port == 0


def test_start_gives_a_working_loopback_socks_port_and_a_private_data_dir(runtime, tmp_path) -> None:
    port = runtime.start("private_web", timeout=10)
    assert port and runtime.socks_port == port
    assert _greets(port)
    data = tmp_path / "data" / "tor"
    assert stat.S_IMODE(os.stat(data).st_mode) == 0o700


def test_one_tor_is_shared_and_stops_with_its_last_user(runtime) -> None:
    first = runtime.start("private_web", timeout=10)
    second = runtime.start("phone_away", wait_for_network=False)
    assert first == second, "both features use the same Tor"
    runtime.release("private_web")
    assert runtime.running and runtime.users() == {"phone_away"}
    runtime.release("phone_away")
    assert not runtime.running


def test_an_onion_address_is_made_kept_and_removed(runtime) -> None:
    runtime.start("phone_away", wait_for_network=False)
    made = runtime.add_onion("", 8765)
    assert made.address.endswith(".onion") and len(made.address) == 62
    assert made.private_key.startswith("ED25519-V3:")
    runtime.remove_onion(made.address)
    again = runtime.add_onion(made.private_key, 8765)
    assert again.address == made.address, "the stored key brings back the same address"


def test_no_tor_binary_is_a_plain_refusal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tor_runtime, "find_tor_binary", lambda env=None: None)
    rt = TorRuntime(data_dir=tmp_path / "tor")
    with pytest.raises(TorUnavailable, match="not installed"):
        rt.start("private_web")
    assert not rt.running and rt.users() == frozenset()


def test_tor_that_cannot_reach_the_network_fails_the_private_web_user_only(runtime, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "never")
    runtime.start("phone_away", wait_for_network=False)
    with pytest.raises(TorUnavailable, match="could not connect"):
        runtime.start("private_web", timeout=1.5)
    assert runtime.running and runtime.users() == {"phone_away"}


def test_how_far_tor_has_connected_is_shown_while_it_connects(runtime, monkeypatch) -> None:
    from core import private_web_service

    assert runtime.bootstrap_percent() == 0, "not running yet"
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "never")
    runtime.start("phone_away", wait_for_network=False)
    assert runtime.bootstrap_percent() == 14
    monkeypatch.setattr(tor_runtime, "shared", lambda: runtime)
    monkeypatch.setattr(private_web, "current_route", lambda: private_web.RouteState("tor", runtime.socks_port))
    monkeypatch.setattr(private_web_service, "_starter", type("Alive", (), {"is_alive": lambda self: True})())
    status = private_web_service.status()
    assert status["state"] == "starting" and status["detail"] == "Tor network: 14% connected."


def test_private_web_uses_the_running_tor_port(runtime, monkeypatch) -> None:
    from core import policy_engine

    port = runtime.start("private_web", timeout=10)
    monkeypatch.setattr(tor_runtime, "shared", lambda: runtime)
    real_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine, "get",
        lambda path, default=None: "tor" if path == "network.private_web.route" else real_get(path, default),
    )
    try:
        state = private_web.refresh()
        assert state.kind == "tor" and state.socks_port == port
        assert private_web.route_problem(state) is None
        runtime.release("private_web")
        state = private_web.refresh()
        assert private_web.route_problem(state) is not None, "Tor stopped means Private web is down"
    finally:
        monkeypatch.undo()
        private_web.refresh()


# ------------------------------------------------- Settings: the real owner routes, end to end


def test_settings_turn_tor_on_and_off_through_the_owner_routes(pact_rig, fake_tor, monkeypatch, tmp_path) -> None:
    rt = TorRuntime(data_dir=tmp_path / "tor-data", binary=fake_tor)
    monkeypatch.setattr(tor_runtime, "shared", lambda: rt)
    from core import private_web_service

    try:
        status, data = pact_rig.get("/api/private-web")
        assert status == 200 and data["route"] == "off" and data["state"] == "off"

        # someone else on the network cannot change it
        status, _ = pact_rig.post("/api/private-web/settings", {"route": "tor"}, client_host="10.0.0.2")
        assert status == 403
        assert pact_rig.get("/api/private-web")[1]["route"] == "off"

        status, data = pact_rig.post("/api/private-web/settings", {"route": "tor"})
        assert status == 200 and data["route"] == "tor"
        if private_web_service._starter is not None:
            private_web_service._starter.join(timeout=15)
        data = pact_rig.get("/api/private-web")[1]
        assert data["state"] == "on", data
        assert rt.running and "private_web" in rt.users()

        status, data = pact_rig.post("/api/private-web/settings", {"route": "off"})
        assert status == 200 and data["state"] == "off"
        assert not rt.running, "Tor stops when Private web no longer needs it"

        status, data = pact_rig.post("/api/private-web/settings", {"route": "carrier-pigeon"})
        assert status == 400
    finally:
        rt.stop()
        private_web.refresh()


def test_settings_reject_a_bad_vpn_file_in_plain_words(pact_rig) -> None:
    status, data = pact_rig.post("/api/private-web/vpn_file", {"text": "[Interface]\nPrivateKey = nope\n[Peer]\n"})
    assert status == 400
    assert "PrivateKey" in json.dumps(data)
