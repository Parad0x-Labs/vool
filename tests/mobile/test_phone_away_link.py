"""Away from home: the phone listener's hidden address on the private route.

Runs the real shared Tor runtime against tests/fixtures/fake_tor.py (Tor's command line and control
protocol, no network), so the listener, the kept key and the pairing code are the shipped code.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import tor_runtime
from core.mobile_companion import away, bot_runtime
from core.mobile_companion import listener as listener_module
from core.tor_runtime import TorRuntime
from tests.mobile.test_mobile_companion_api import _body, _mobile_home, _post, runtime  # noqa: F401

FAKE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_tor.py"


@pytest.fixture
def tor(tmp_path, monkeypatch):
    launcher = tmp_path / "tor"
    launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    launcher.chmod(0o755)
    monkeypatch.setenv("FAKE_TOR_BOOTSTRAP", "never")  # the address must not wait for the network
    runtime = TorRuntime(data_dir=tmp_path / "tor-data", binary=str(launcher))
    monkeypatch.setattr(tor_runtime, "shared", lambda: runtime)
    yield runtime
    runtime.stop()


@pytest.fixture
def kept(monkeypatch):
    """The credential store, in memory."""
    store: dict[str, str] = {}
    monkeypatch.setattr(away, "_load_key", lambda: (store.get("key", ""), store.get("address", "")))
    monkeypatch.setattr(away, "_save_key", lambda key, address: store.update(key=key, address=address))
    return store


@pytest.fixture
def listener(tmp_path, monkeypatch):
    monkeypatch.setattr(listener_module, "_settings_path", lambda: tmp_path / "listener.json")
    monkeypatch.setattr(bot_runtime, "manager_for", lambda runtime: SimpleNamespace(restore=lambda: None))
    made = listener_module.CompanionListener(SimpleNamespace())
    yield made
    made.close()


def test_off_by_default_starts_no_tor(listener, tor, kept):
    state = listener.configure(enabled=True, address="127.0.0.1")
    assert state["away"] is False and state["away_address"] == ""
    assert listener.away is None and not tor.running and tor.users() == frozenset()
    assert kept == {}


def test_switched_on_publishes_a_loopback_copy_and_keeps_the_address(listener, tor, kept):
    state = listener.configure(enabled=True, address="127.0.0.1", away=True)
    address = state["away_address"]
    assert state["away"] is True and away.HIDDEN_HOST.fullmatch(address)
    assert tor.running and tor.users() == frozenset({away.AwayLink.USER})
    assert kept["address"] == address and kept["key"].startswith("ED25519-V3:")
    assert listener.away.server.server_address[0] == "127.0.0.1"
    assert listener_module.load_settings()["away"] is True
    # The copy is the phone listener, not the desktop API: other paths are refused.
    port = listener.away.server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/chat", timeout=5)
    assert refused.value.code == 404 and json.loads(refused.value.read())["ok"] is False

    # Off: the address is withdrawn and Tor stops, because nothing else uses it.
    state = listener.configure(enabled=True, address="127.0.0.1", away=False)
    assert state["away"] is False and state["away_address"] == ""
    assert listener.away is None and not tor.running
    assert listener_module.load_settings()["away"] is False

    # On again: the same address, so a paired phone keeps reaching this computer.
    assert listener.configure(enabled=True, address="127.0.0.1", away=True)["away_address"] == address


def test_phone_access_off_takes_the_away_address_down(listener, tor, kept):
    listener.configure(enabled=True, address="127.0.0.1", away=True)
    listener.configure(enabled=False)
    assert listener.away is None and not tor.running


def test_tor_stays_up_for_another_user(listener, tor, kept):
    tor.start("private-web", wait_for_network=False)
    listener.configure(enabled=True, address="127.0.0.1", away=True)
    listener.configure(enabled=True, address="127.0.0.1", away=False)
    assert tor.running and tor.users() == frozenset({"private-web"})


def test_a_changed_address_is_refused(listener, tor, kept):
    """A kept address that Tor no longer gives back means the key was swapped: do not publish."""
    kept.update(key="ED25519-V3:" + "A" * 86 + "==", address="b" * 56 + ".onion")
    state = listener.configure(enabled=True, address="127.0.0.1", away=True)
    assert state["enabled"] is True and state["away_address"] == ""  # home network still works
    assert listener.away is None and not tor.running
    assert listener_module.load_settings()["away"] is True  # the choice stays; next start retries


def test_no_tor_in_this_copy_leaves_home_network_working(listener, kept, monkeypatch, tmp_path):
    missing = TorRuntime(data_dir=tmp_path / "none", binary=str(tmp_path / "no-such-tor"))
    monkeypatch.setattr(tor_runtime, "shared", lambda: missing)
    state = listener.configure(enabled=True, address="127.0.0.1", away=True)
    assert state["enabled"] is True and state["port"] and state["away_address"] == ""
    assert listener.away is None


def test_pairing_code_carries_the_away_address_only_while_switched_on(_mobile_home, runtime, tor, kept, tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    monkeypatch.setattr(listener_module, "_settings_path", lambda: tmp_path / "listener.json")
    phone = listener_module.listener_for(runtime)
    try:
        phone.configure(enabled=True, address="127.0.0.1")
        home_only = _body(_post("/api/mobile/pairing/start", {}, runtime=runtime))["qr_uri"]
        assert "away" not in parse_qs(urlsplit(home_only).query)

        address = phone.configure(enabled=True, address="127.0.0.1", away=True)["away_address"]
        both = urlsplit(_body(_post("/api/mobile/pairing/start", {}, runtime=runtime))["qr_uri"])
        assert parse_qs(both.query)["away"] == [address]
        assert both.netloc == "127.0.0.1:%d" % phone.server.server_address[1]  # home address unchanged
    finally:
        phone.close()
