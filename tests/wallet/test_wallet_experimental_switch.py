"""Crypto is OFF by default and switched on in Settings, under a 🧪 Experimental group.

Operator decision (2026-09-07): the wallet is an experimental, opt-in feature. Until now the only switch was an
environment variable no user could reach. The switch is a persisted preference written through the same door as
every other Settings toggle; the environment variable stays as an operator override (a truthy value forces on,
an explicit false forces off), and the wallet reads the switch on every call so no restart is needed.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def isolated_prefs(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_off_by_default(isolated_prefs):
    from core.wallet import config

    assert config.wallet_enabled() is False


def test_the_settings_preference_switches_it_on_without_a_restart(isolated_prefs):
    from core.user_preferences import load_preferences, save_preferences
    from core.wallet import config

    prefs = load_preferences()
    assert prefs.wallet_enabled is False
    prefs.wallet_enabled = True
    save_preferences(prefs)
    assert load_preferences().wallet_enabled is True
    assert config.wallet_enabled() is True


def test_the_environment_variable_is_an_operator_override(isolated_prefs, monkeypatch):
    from core.user_preferences import load_preferences, save_preferences
    from core.wallet import config

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    assert config.wallet_enabled() is True
    prefs = load_preferences()
    prefs.wallet_enabled = True
    save_preferences(prefs)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
    assert config.wallet_enabled() is False, "an explicit false forces the wallet off even when the preference is on"


def test_the_write_door_accepts_the_switch_and_the_list_reads_it_back():
    import inspect

    from core.command_registry.groups import convergence
    from core.web.api import registry_authorities

    door = inspect.getsource(registry_authorities.set_prefs_authority)
    assert '"wallet_enabled"' in door, "the prefs write door must accept the switch as a bool preference"
    src = Path(convergence.__file__).read_text()
    assert '"wallet_enabled": p.wallet_enabled' in src, "a value that can be written must be read back by settings.prefs.list"


def test_the_settings_group_is_experimental_and_leads_with_the_switch():
    from core.vool_settings_page import settings_groups

    group = next(g for g in settings_groups() if g["id"] == "wallet")
    # the Crypto (Pilot) face: a plain glyph, the Pilot badge on the group and on the switch (no emoji navigation,
    # no "Testnets only" wording); the switch still leads
    assert group["icon"] == "◇" and group["title"] == "Crypto" and group.get("badge") == "Pilot"
    assert "Testnets only" not in (group.get("blurb") or "")
    first = group["rows"][0]
    assert first["id"] == "wallet_enabled" and first["kind"] == "toggle"
    assert first["write"] == {"url": "/api/settings/prefs", "field": "wallet_enabled", "type": "bool"}
    assert first.get("badge") == "Pilot"
    assert "Testnets only" not in first.get("help", "")
    assert any(r.get("widget") == "wallet" for r in group["rows"][1:])


def test_the_off_state_points_at_the_switch_not_an_environment_variable():
    from core.wallet_fragment import render_wallet_fragment

    fragment = render_wallet_fragment()
    assert "VOOL_WALLET_ENABLED" not in fragment
    assert "Settings → Crypto" in fragment and "Wallet (Experimental)" not in fragment  # the section's current name
    # Goal 2 stage 2 (2026-09-17): the off STATE is the wallet/contacts delivery's own surface
    # (served-proven there and in Goal 1's combined gate); after the crypto-settings merge its copy
    # is the owning delivery's wording — same law asserted: crypto optional, nothing auto-created.
    assert "none is ever created automatically" in fragment and "Crypto is optional" in fragment


@pytest.mark.timeout(600)
def test_served_switch_turns_the_wallet_on_without_restart(tmp_path):
    import tests._reader_served_rig as rig

    def _json(base, path, body=None):
        req = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"} if body is not None else {}, method="POST" if body is not None else "GET")
        with urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": ""})
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        try:
            _s, st = _json(daemon.base_url, "/api/wallet/status")
            assert st["status"]["enabled"] is False, st["status"]
            _s, _res = _json(daemon.base_url, "/api/settings/prefs", {"wallet_enabled": True})
            _s, st2 = _json(daemon.base_url, "/api/wallet/status")
            assert st2["status"]["enabled"] is True, st2["status"]
            _s, prefs = _json(daemon.base_url, "/api/settings/prefs")
            assert (prefs.get("wallet_enabled") if "wallet_enabled" in prefs else prefs.get("data", {}).get("wallet_enabled")) is True, prefs
        finally:
            daemon.stop()
