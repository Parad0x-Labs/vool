"""A plugin may LIST the services it pays; only VOOL pays them, from the Skills & plugins wallet, inside the owner's tick.

The money wall stays whole: a plugin still cannot ask for wallet, payment, signing, approval, cap, budget or receipt
authority. What is new is one declaration, ``vool.paid_services``, and one ask a tool's result may carry,
``observation.paid_fetch.url``. VOOL checks the ask against the approved manifest and hands it to
``core.wallet.addon_wallet.fetch_for_addon``, which pays only inside that plugin's own allowance
(tests/wallet/test_addon_wallet_pays.py pays a real x402 request through it).
"""
from __future__ import annotations

import json

import pytest

from core import plugin_payments
from core.plugin_executor import PluginToolResult
from core.plugin_tools import PluginManifestError, parse_manifest
from tests.test_plugin_manager_v1 import installed, package
from tests.test_plugin_manifest_v1 import manifest

SERVICE = "https://api.example.org"


def _tools_manifest(**vool):
    raw = manifest()
    block = raw["vool"]
    block["kind"] = ["tools"]
    del block["model_provider"]
    block["tools"] = [{"intent": "sample-provider.forecast", "description": "Read a paid forecast.",
                       "handler": {"kind": "subprocess", "entry": "bin/server", "timeout_seconds": 5},
                       "input_schema": {"type": "object", "properties": {}}, "side_effect_class": "read_only",
                       "permission_actions": ["read_files"], "approval_requirement": "none"}]
    block.update(vool)
    return raw


def _load(raw, tmp_path):
    (tmp_path / "bin").mkdir(exist_ok=True)
    (tmp_path / "bin/server").write_text("#!/bin/sh\n")
    return parse_manifest(raw, path=tmp_path / ".codex-plugin" / "plugin.json")


def test_a_plugin_lists_paid_services_only_inside_its_own_websites(tmp_path):
    assert _load(_tools_manifest(paid_services=[SERVICE]), tmp_path).vool["paid_services"] == [SERVICE]
    for wrong in (["https://other.example.org"], [SERVICE + "/v1/forecast"], [SERVICE + "?key=1"], ["http://api.example.org"], [SERVICE, SERVICE + "/"]):
        with pytest.raises(PluginManifestError):
            _load(_tools_manifest(paid_services=wrong), tmp_path)
    provider = manifest()
    provider["vool"]["paid_services"] = [SERVICE]
    with pytest.raises(PluginManifestError, match="Only a plugin with tools"):
        _load(provider, tmp_path)


@pytest.mark.parametrize("field", ["payments", "wallet", "signing", "budget"])
def test_the_money_wall_still_refuses_every_authority_shaped_field(tmp_path, field):
    raw = _tools_manifest(paid_services=[SERVICE])
    raw["vool"]["tools"][0]["handler"][field] = {"services": [SERVICE]}
    with pytest.raises(PluginManifestError):
        _load(raw, tmp_path)
    raw = _tools_manifest(paid_services=[SERVICE])
    raw["vool"]["tools"][0].update(side_effect_class="wallet_spend", permission_actions=["financial_or_paid_actions"])
    with pytest.raises(PluginManifestError, match=r"authority|spend money"):
        _load(raw, tmp_path)


@pytest.fixture
def wallet_asks(monkeypatch):
    from core.wallet import addon_wallet

    asked: list[tuple[str, str, dict]] = []

    def fetch(addon_id, url, *, source_context=None):
        asked.append((addon_id, url, dict(source_context or {})))
        return {"status": "delivered", "http_status": 200, "proposal_id": "p-1", "reason": "", "body": b'{"rain": 0.2}',
                "payment": {"paid": True, "decision": "pay", "delivered": True}}

    monkeypatch.setattr(addon_wallet, "fetch_for_addon", fetch)
    return asked


def _result(**observation):
    return PluginToolResult(ok=True, text="asked", observation=observation)


OWNER = {"surface": "desktop", "_owner_local": True}


def test_the_ask_is_paid_through_the_plugins_own_allowance_and_the_body_comes_back(wallet_asks):
    settled = plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]},
                                     _result(paid_fetch={"url": SERVICE + "/v1/today"}, kept=1), OWNER)
    assert [(a, u) for a, u, _ in wallet_asks] == [("plugin:sample-provider", SERVICE + "/v1/today")]
    assert settled.text == '{"rain": 0.2}' and settled.observation["kept"] == 1
    assert settled.observation["paid_fetch"]["paid"] is True and settled.observation["paid_fetch"]["status"] == "delivered"


@pytest.mark.parametrize("ask", [{"url": "https://other.example.org/x"}, {"url": ""}, "https://api.example.org/x", {"url": 7}])
def test_an_ask_outside_the_approved_manifest_pays_nothing_and_never_reaches_the_wallet(wallet_asks, ask):
    settled = plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]}, _result(paid_fetch=ask), OWNER)
    assert wallet_asks == [] and settled.observation["paid_fetch"]["reason"] == "not_a_declared_service"
    assert "Nothing was paid" in settled.text


def test_a_plugin_that_lists_no_paid_service_pays_nothing(wallet_asks):
    settled = plugin_payments.settle("sample-provider", {}, _result(paid_fetch={"url": SERVICE + "/x"}), OWNER)
    assert wallet_asks == [] and settled.observation["paid_fetch"]["paid"] is False


def test_network_off_pays_nothing(wallet_asks):
    settled = plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]}, _result(paid_fetch={"url": SERVICE + "/x"}),
                                     {**OWNER, "allow_remote_fetch": False})
    assert wallet_asks == [] and settled.observation["paid_fetch"]["reason"] == "network_off"


def test_a_result_with_no_ask_or_a_failed_tool_is_left_alone(wallet_asks):
    plain = _result(rain=1)
    assert plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]}, plain, OWNER) is plain
    failed = PluginToolResult(ok=False, error="boom", observation={"paid_fetch": {"url": SERVICE + "/x"}})
    assert plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]}, failed, OWNER) is failed
    assert wallet_asks == []


def test_a_refusal_from_the_wallet_says_so_and_pays_nothing(monkeypatch):
    from core.wallet import addon_wallet

    monkeypatch.setattr(addon_wallet, "fetch_for_addon", lambda *a, **k: {"status": "not_paid", "reason": "not_allowed", "body": b""})
    settled = plugin_payments.settle("sample-provider", {"paid_services": [SERVICE]}, _result(paid_fetch={"url": SERVICE + "/x"}), OWNER)
    assert "Tick it under Settings" in settled.text and settled.observation["paid_fetch"]["paid"] is False


def test_the_wallet_lists_only_an_installed_switched_on_plugin_that_lists_paid_services(package, monkeypatch):
    from core import plugin_executor, plugin_lifecycle
    from core import plugin_manager as manager
    from core.wallet import addon_wallet

    # listing reads manifests; it runs no plugin code, so this device's sandbox does not matter here
    monkeypatch.setattr(plugin_executor, "process_isolation_refusal", lambda **_: "")
    monkeypatch.setattr(plugin_executor, "kernel_confinement_available", lambda *a, **k: True)
    raw = _tools_manifest(paid_services=[SERVICE])
    (package / "bin").mkdir()
    (package / "bin/server").write_text("#!/bin/sh\n")
    (package / "bin/server").chmod(0o755)
    (package / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    info = installed(package)
    assert addon_wallet.requester("plugin:sample-provider") is None, "installed but not switched on lists nothing"
    manager.enable(info["id"], accepted=True, approval_digest=info["approval_digest"])
    assert plugin_lifecycle.is_available("sample-provider")
    listed = addon_wallet.requester("plugin:sample-provider")
    assert listed and listed["services"] == [SERVICE] and listed["name"] == "Sample provider"
    manager.disable("sample-provider")
    assert addon_wallet.requester("plugin:sample-provider") is None


def test_a_real_plugin_tool_asks_and_vool_pays_through_the_wallet_never_the_plugin(package, wallet_asks):
    """End to end through the confined child (needs the kernel sandbox, as every plugin-execution test does)."""
    from core import plugin_tools
    from tests.test_plugin_network_v1 import admit, tool_package

    tool_package(package, 'import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"text":"asking","observation":{"paid_fetch":{"url":"https://api.example.org/v1/today"}}}))\n')
    path = package / ".codex-plugin/plugin.json"
    raw = json.loads(path.read_text())
    raw["vool"].update(network=["api.example.org"], paid_services=[SERVICE])
    path.write_text(json.dumps(raw))
    result = plugin_tools.execute_plugin_contract(admit(package), {}, source_context=OWNER)
    assert result.ok, result.error
    assert [(a, u) for a, u, _ in wallet_asks] == [("plugin:sample-provider", SERVICE + "/v1/today")]
    assert result.text == '{"rain": 0.2}' and result.observation["trust"] == "untrusted_external"
    assert result.observation["paid_fetch"]["paid"] is True
