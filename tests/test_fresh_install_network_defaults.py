"""Fresh network defaults and saved upgrade policy use the same policy owner."""
from __future__ import annotations

import copy

import pytest
import yaml

from core import policy_engine, runtime_paths


@pytest.fixture
def isolated_policy_home(tmp_path, monkeypatch):
    home = tmp_path / "runtime"
    old_override = runtime_paths._VOOL_HOME_OVERRIDE
    old_policy = copy.deepcopy(policy_engine._POLICY_CACHE)
    old_binding = policy_engine._POLICY_CACHE_BINDING
    monkeypatch.setenv("VOOL_HOME", str(home))
    for name in ("VOOL_ENABLE_WEB", "VOOL_ALLOW_WEB", "VOOL_DISABLE_WEB", "VOOL_NO_WEB"):
        monkeypatch.delenv(name, raising=False)
    runtime_paths.configure_runtime_home(home)
    policy_engine._POLICY_CACHE = None
    try:
        yield home
    finally:
        runtime_paths.configure_runtime_home(old_override)
        policy_engine._POLICY_CACHE = old_policy
        policy_engine._POLICY_CACHE_BINDING = old_binding


def test_fresh_profile_has_no_automatic_public_network_consent(isolated_policy_home):
    policy_engine.load(force_reload=True)
    assert policy_engine.allow_web_fallback() is False
    assert policy_engine.get("network.outbound_enabled") is False
    assert policy_engine.null_dial_enabled() is False
    assert not (isolated_policy_home / "config" / "default_policy.yaml").exists()


@pytest.mark.parametrize("web,outbound,local_only", [(True, True, False), (False, False, True), (True, False, False)])
def test_saved_upgrade_choices_are_preserved_byte_for_byte(isolated_policy_home, web, outbound, local_only):
    path = isolated_policy_home / "config" / "default_policy.yaml"
    path.parent.mkdir(parents=True)
    original = yaml.safe_dump({
        "system": {"local_only_mode": local_only, "allow_web_fallback": web},
        "network": {"outbound_enabled": outbound},
    }).encode()
    path.write_bytes(original)
    policy_engine.load(force_reload=True)
    assert policy_engine.allow_web_fallback() is web
    assert policy_engine.get("network.outbound_enabled") is outbound
    assert policy_engine.local_only_mode() is local_only
    assert path.read_bytes() == original


def test_explicit_web_consent_persists_without_enabling_automatic_outbound(isolated_policy_home):
    effective = policy_engine.set_operator_policy_values({"system.allow_web_fallback": True})
    assert effective == {"system.allow_web_fallback": True}
    policy_engine._POLICY_CACHE = None
    assert policy_engine.allow_web_fallback() is True
    assert policy_engine.get("network.outbound_enabled") is False


def test_fresh_cloud_transport_refuses_before_resolving_any_endpoint(isolated_policy_home):
    from core.cloud_transport import CloudTransportError, PolicyBoundCloudTransport

    transport = PolicyBoundCloudTransport(allowed_hosts=("provider.invalid",))
    with pytest.raises(CloudTransportError, match="cloud_network_disabled_by_policy"):
        transport.request_json(method="POST", url="https://provider.invalid/inference")


def test_changing_the_runtime_home_cannot_inherit_the_previous_profiles_consent(isolated_policy_home):
    policy_engine.set_operator_policy_values({
        "system.allow_web_fallback": True,
        "network.outbound_enabled": True,
    })
    assert policy_engine.allow_web_fallback() is True
    assert policy_engine.get("network.outbound_enabled") is True
    runtime_paths.configure_runtime_home(isolated_policy_home.parent / "fresh-profile")
    assert policy_engine.allow_web_fallback() is False
    assert policy_engine.get("network.outbound_enabled") is False
    runtime_paths.configure_runtime_home(isolated_policy_home)
    assert policy_engine.allow_web_fallback() is True
    assert policy_engine.get("network.outbound_enabled") is True
