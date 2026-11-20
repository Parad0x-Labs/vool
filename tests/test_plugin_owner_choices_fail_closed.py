"""Owner choices that must hold when stores are damaged, doors are reached from other pages,
or a plugin is replaced, turned off or declares no permissions."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import plugin_catalog, plugin_services, tool_offer_assembly, tool_registry
from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core.credential_store import has_credential
from core.mode_permission_policy import PermissionAction, actions_for_tool
from tests.test_plugin_manager_v1 import installed, package
from tests.test_plugin_network_v1 import admit, tool_package


@pytest.fixture
def owner_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    pack = tmp_path / "folder" / "owner-pack"
    (pack / "skills" / "notes").mkdir(parents=True)
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: (("owner-pack", pack),))
    monkeypatch.setattr(plugin_catalog, "bundled_plugin_dirs", lambda: ())
    return pack


def _offered() -> set[str]:
    return {pid for pid, _path in tool_offer_assembly._plugin_dirs()}


def test_damaged_plugin_records_stop_offering_unrecorded_packs_and_are_kept(owner_pack, tmp_path):
    assert _offered() == {"owner-pack"}
    store = tmp_path / "lifecycle.json"
    store.write_text("{damaged")
    assert _offered() == set()

    # The next write keeps the damaged file and remembers that records were lost.
    lifecycle._save(lifecycle.PluginRecord(plugin_id="other-pack"))
    assert [p.read_text() for p in tmp_path.glob("lifecycle.damaged-*.json")] == ["{damaged"]
    assert json.loads(store.read_text())["records_lost_at"]
    assert not lifecycle.store_trusted()
    assert _offered() == set()


def test_unreadable_owner_choices_turn_every_plugin_off_until_saved_again(owner_pack, tmp_path):
    choices = tmp_path / "profile" / "config" / "plugins_enabled.json"
    choices.parent.mkdir(parents=True)
    choices.write_text("not json")
    assert "owner-pack" in plugin_catalog._disabled_ids() and _offered() == set()
    choices.write_text(json.dumps({"disabled": "owner-pack"}))
    assert _offered() == set()

    assert plugin_catalog.set_plugin_enabled("owner-pack", True)
    assert json.loads(choices.read_text()) == {"disabled": []}
    assert _offered() == {"owner-pack"}


def test_turning_a_plugin_off_in_the_plugins_panel_stops_its_tools_and_services_now(package):
    tool_package(package, 'import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"text":"ran"}))\n')
    raw = json.loads((package / ".codex-plugin/plugin.json").read_text())
    raw["vool"]["kind"] = ["tools", "model_provider"]
    raw["vool"]["model_provider"] = {"base_url": "https://api.example.org", "auth_placement": "header",
                                     "credential_slot": "access", "model_list_path": "/v1/models",
                                     "pricing_source": "models"}
    raw["vool"]["credentials"] = {"access": {"label": "Your service key", "help_url": "https://example.org/keys",
                                             "env": "SAMPLE_ACCESS"}}
    raw["vool"]["network"] = ["api.example.org"]
    (package / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    admit(package)
    assert plugin_services.declaration("sample-provider", "model_provider")

    assert plugin_catalog.set_plugin_enabled("sample-provider", False)
    assert lifecycle.record_for("sample-provider").enabled is False
    contract = tool_registry.tool_for_intent("sample-provider.echo")
    assert contract is None or not contract.supported
    with pytest.raises(plugin_services.ServiceRefused):
        plugin_services.declaration("sample-provider", "model_provider")


def _post(path, body, headers):
    from core.web.api.service import dispatch_post

    response = dispatch_post(path=path, body=body, headers=headers, runtime=SimpleNamespace(runtime_version_stamp={}, version="test"),
                             model_name="test", workspace_root_provider=lambda: ".", client_host="127.0.0.1")
    return response.status


@pytest.mark.parametrize("path, body", [
    ("/api/plugins/lifecycle", {"action": "enable", "plugin_id": "sample-provider"}),
    ("/api/plugins/enable", {"id": "sample-provider", "enabled": True}),
])
@pytest.mark.parametrize("headers, status", [
    ({"content-type": "application/json", "host": "127.0.0.1:8000", "origin": "http://127.0.0.1:9999"}, 403),
    ({"content-type": "application/json", "host": "127.0.0.1:8000", "sec-fetch-site": "cross-site"}, 403),
    ({"content-type": "text/plain", "host": "127.0.0.1:8000"}, 415),
])
def test_plugin_doors_refuse_other_local_pages(path, body, headers, status, package):
    assert _post(path, body, headers) == status


def test_registry_plugin_cannot_be_reinstalled_from_elsewhere_to_drop_its_revocation(package):
    lifecycle._save(lifecycle.PluginRecord(plugin_id="sample-provider", stage=lifecycle.STAGE_VERIFIED,
                                           source="registry:sample-provider", root=str(package)))
    with pytest.raises(lifecycle.LifecycleError, match="plugin registry"):
        lifecycle.install("sample-provider", root=package, source="operator")
    assert lifecycle.record_for("sample-provider").source == "registry:sample-provider"


@pytest.mark.parametrize("change", ["publisher", "origin"])
def test_update_from_another_party_does_not_inherit_the_saved_key(package, change):
    info = installed(package)
    manager.connect(info["id"], "access", "saved-service-key-0123456789")
    raw = json.loads((package / ".codex-plugin/plugin.json").read_text())
    raw["version"] = raw["vool"]["version"] = "1.0.1"
    if change == "publisher":
        raw["vool"]["publisher"] = "Somebody else"
    (package / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    review = manager.prepare_local(str(package), update=True,
                                   **({"source": "github:example/other@" + "a" * 40} if change == "origin" else {}))
    result = manager.install(review["review_id"], accepted=True, digest=review["digest"])
    assert result["keys_removed"] is True
    assert not has_credential("plugin.sample-provider.access")


def test_update_from_the_same_place_and_publisher_keeps_the_saved_key(package):
    info = installed(package)
    manager.connect(info["id"], "access", "saved-service-key-0123456789")
    raw = json.loads((package / ".codex-plugin/plugin.json").read_text())
    raw["version"] = raw["vool"]["version"] = "1.0.1"
    (package / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    review = manager.prepare_local(str(package), update=True)
    assert manager.install(review["review_id"], accepted=True, digest=review["digest"])["keys_removed"] is False
    assert has_credential("plugin.sample-provider.access")


@pytest.mark.parametrize("side_effect_class, expected", [
    ("network_send", PermissionAction.SEND_EXTERNAL_MESSAGES),
    ("workspace_write", PermissionAction.OVERWRITE_EXISTING_FILES),
    ("external_unpinned", PermissionAction.UNKNOWN_SIDE_EFFECT),
])
def test_plugin_tool_without_declared_permissions_is_classified_by_its_class_not_its_name(
        monkeypatch, side_effect_class, expected):
    contract = SimpleNamespace(permission_actions=(), source="plugin:image", side_effect_class=side_effect_class)
    monkeypatch.setattr(tool_registry, "registry_map", lambda: {"image.send_report": contract})
    actions = set(actions_for_tool("image.send_report", {}))
    assert expected in actions
