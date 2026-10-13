import pytest

from core import plugin_lifecycle
from core.registry.admission import admit
from core.registry.artifact import inspect
from core.registry.codec import RegistryError, digest
from core.registry.native import install, preview
from core.registry.trust import TrustedCatalog
from tests.registry.audit_helpers import package, runtime, trusted
from tests.registry.helpers import NOW, TARGET, catalog_fixture, evidence_fixture, signed, trust_fixture


@pytest.mark.parametrize("effect,actions", [("network_send", ("external_messages",)),
                                           ("wallet_spend", ("financial_or_paid_actions",))])
def test_a1_preview_and_install_disclose_native_tool_contracts(tmp_path, monkeypatch, effect, actions):
    home = runtime(tmp_path, monkeypatch)
    raw, _, info = package(tmp_path, tools=True, permissions=actions, effect=effect)
    with trusted(raw, home / "config/registry_trust.sqlite") as (catalog, *_):
        prepared = preview(catalog, raw, package_id=info["id"], version=info["version"], target=TARGET, now=NOW)
        assert prepared["credential_bindings"] == ["binding-mail"]
        tool = prepared["tools"][0]
        assert tool["intent"].startswith(inspect(raw)["manifest"]["name"] + ".")
        assert tool["side_effect_class"] == effect and tool["permission_actions"] == list(actions)
        assert tool["handler"]["kind"] == "subprocess"
        assert tool["handler"]["network"] is (effect == "network_send")
        assert tool["handler"]["env_allowlist"] == ["REGISTRY_PROVIDER_NAME"]
        assert not plugin_lifecycle.records()
        result = install(catalog, raw, package_id=info["id"], version=info["version"], target=TARGET,
                         accepted=True, enable=False, now=NOW)
        assert result["tools"] == prepared["tools"] and result["credential_bindings"] == prepared["credential_bindings"]
        assert result["permissions"] == prepared["permissions"] == list(actions)


@pytest.mark.parametrize("route", ["admission", "install"])
def test_a1_missing_top_level_tool_permissions_refuse(tmp_path, monkeypatch, route):
    home = runtime(tmp_path, monkeypatch)
    raw, _, info = package(tmp_path, tools=True)
    policy, _, _ = trust_fixture()
    with pytest.raises(RegistryError, match="permission"):
        if route == "admission":
            admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
        else:
            # Even an externally signed deficient admission cannot bypass the
            # native consent check. Sign a schema-valid entry for the bad bytes.
            good = tmp_path / "covered"
            good.mkdir()
            good_raw, _, _ = package(good, tools=True, permissions=("external_messages",))
            policy, keys, root = trust_fixture()
            snapshot = catalog_fixture(good_raw, policy, keys)["signed"]
            bad = inspect(raw)
            entry = snapshot["entries"][0]["signed"]
            entry.update(artifact_sha256=bad["sha256"], artifact_length=len(raw), native_digest=bad["native_digest"])
            entry["publication"]["path"] = "packages/" + bad["sha256"] + ".json"
            snapshot["entries"] = [signed(entry, keys["admission"])]
            authorization = snapshot["authorization"]["signed"]
            authorization["entries_sha256"] = digest(snapshot["entries"])
            snapshot["authorization"] = signed(authorization, keys["admission"])
            catalog = TrustedCatalog(root, home / "config/registry_trust.sqlite", now=NOW)
            try:
                catalog.accept(signed(snapshot, keys["catalog"]), now=NOW)
                install(catalog, raw, package_id=info["id"], version=info["version"], target=TARGET,
                        accepted=True, enable=False, now=NOW)
            finally:
                catalog.close()
    assert not plugin_lifecycle.records()
