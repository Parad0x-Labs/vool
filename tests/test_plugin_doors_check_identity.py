"""Every door that brings a package in asks who it says it is, not only the folder door.

The Add-ons page already refused a folder that names itself as VOOL's own or as checked by
someone. Four other ways in read the setup file and never asked: a signed package from the Skill
Codex (its review sheet, its install and the signer's admission), the operator's lifecycle
action, and a skill added from the public list. One test per door, each through the door itself.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import plugin_catalog
from core import plugin_lifecycle as lifecycle
from core.plugin_identity import OWN_NAME, VOUCHING
from core.plugin_migration import RESERVED_NAME
from core.registry.admission import admit
from core.registry.artifact import inspect, native_identity, pack
from core.registry.codec import RegistryError
from core.registry.native import STATE_NAME, install, preview
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, evidence_fixture, signed, trust_fixture
from tests.test_plugin_manifest_v1 import manifest

REPO = Path(__file__).resolve().parents[1]


def _package(tmp_path, *, name="Sample provider", publisher="Example"):
    """A signed-list package holding one plugin under its own id."""
    root = tmp_path / "source"
    (root / ".codex-plugin").mkdir(parents=True)
    raw = manifest()
    raw["name"] = raw["vool"]["id"] = "service"
    raw["vool"].update(name=name, publisher=publisher)
    (root / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    (root / "LICENSE").write_bytes((REPO / "examples/plugins/template/LICENSE").read_bytes())
    info = {"id": "alice/service", "kind": "plugin", "version": "1.0.0", "title": "Sample provider",
            "summary": "Connect a compatible chat service",
            "category": "productivity", "subcategory": "notes-summaries", "license": "MIT", "languages": ["en"],
            "source": {"repository": "alice/plugins", "commit": "b" * 40, "path": "service"}}
    return pack(root, info, tmp_path / "capture")


def _signed_list(raw, policy, keys):
    """The list as a signer that skipped the identity rule would have signed it: built by hand, not by admit()."""
    from core.registry.codec import digest
    from core.registry.rules import analyze

    artifact = inspect(raw)
    evidence = evidence_fixture(raw, policy)
    entry = {"metadata": artifact["metadata"], "artifact_sha256": artifact["sha256"], "artifact_length": artifact["raw_length"],
             "native_digest": artifact["native_digest"], "plugin_id": native_identity("alice/service"), "dependencies": {},
             "policy_sha256": evidence["policy_sha256"], "evidence_sha256": digest(evidence), "targets": policy["targets"],
             "publication": evidence["publication"],
             "native_rule_review": {"obeys_native_rules": True, "conflicts": [], "overlaps": [
                 item["control"] for item in analyze(artifact["files"])["native_control_overlaps"]]},
             "schema": "vool.registry.admission.v1", "issued_at": NOW, "expires": NOW + 7200}
    entries = [signed(entry, keys["admission"])]
    authorization = {"schema": "vool.registry.catalog_authorization.v1", "root_version": 1, "epoch": 1, "issued_at": NOW,
                     "expires": NOW + 7200, "entries_sha256": digest(entries), "revoked_sha256": digest([])}
    return signed({"schema": "vool.registry.catalog.v1", "root_version": 1, "sequence": 1, "issued_at": NOW,
                   "expires": NOW + 3600, "entries": entries, "revoked": [],
                   "authorization": signed(authorization, keys["admission"])}, keys["catalog"])


@pytest.fixture
def profile(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(home / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(home / "addons"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    lifecycle.reset_for_tests()
    plugin_catalog.reset_storage_state()
    yield home
    plugin_catalog.reset_storage_state()


@pytest.fixture
def signed_poser(tmp_path, profile):
    """A package whose plugin calls itself "VOOL Official", on a list that verifies on this device."""
    policy, keys, root = trust_fixture()
    raw = _package(tmp_path, name="VOOL Official", publisher="Example")
    catalog = TrustedCatalog(root, profile / "config" / STATE_NAME, now=NOW)
    catalog.accept(_signed_list(raw, policy, keys), now=NOW)
    try:
        yield catalog, raw
    finally:
        catalog.close()


def test_the_skill_codex_install_refuses_a_signed_plugin_that_uses_vools_name(signed_poser):
    catalog, raw = signed_poser
    with pytest.raises(RegistryError) as refused:
        install(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET, accepted=True, enable=False, now=NOW)
    assert str(refused.value) == OWN_NAME
    assert "vool.name" in refused.value.fix
    assert lifecycle.records() == []


def test_the_skill_codex_review_sheet_refuses_it_before_anything_is_shown(signed_poser):
    catalog, raw = signed_poser
    with pytest.raises(RegistryError) as refused:
        preview(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET, now=NOW)
    assert str(refused.value) == OWN_NAME


@pytest.mark.parametrize("fields, sentence, where", [
    ({"name": "Parcel desk", "publisher": "Verified maker"}, VOUCHING, "vool.publisher"),
    ({"name": "Editor's Choice notes", "publisher": "Example"}, VOUCHING, "vool.name"),
])
def test_the_signers_admission_refuses_a_plugin_that_claims_a_check(tmp_path, fields, sentence, where):
    policy, _keys, _root = trust_fixture()
    raw = _package(tmp_path, **fields)
    with pytest.raises(RegistryError) as refused:
        admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
    assert str(refused.value) == sentence and where in refused.value.fix


def test_a_plain_signed_plugin_still_passes_every_one_of_these_doors(tmp_path, profile):
    policy, keys, root = trust_fixture()
    raw = _package(tmp_path)
    admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
    catalog = TrustedCatalog(root, profile / "config" / STATE_NAME, now=NOW)
    try:
        catalog.accept(_signed_list(raw, policy, keys), now=NOW)
        assert preview(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET, now=NOW)["reviewed"] is True
        result = install(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET, accepted=True,
                         enable=False, now=NOW)
        assert result["status"] == "installed" and result["enabled"] is False
    finally:
        catalog.close()


def _dropped_pack(profile, plugin_id, **block):
    """A folder someone put straight into the plugins folder, which the catalog then lists."""
    folder = profile / "addons" / "plugins" / plugin_id
    (folder / ".codex-plugin").mkdir(parents=True)
    raw = manifest()
    raw["name"] = raw["vool"]["id"] = plugin_id
    raw["vool"].update(block)
    (folder / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    return folder


@pytest.mark.parametrize("plugin_id, block, sentence", [
    ("vool-helper", {}, RESERVED_NAME),
    ("email", {}, RESERVED_NAME),
    ("acme-parcel", {"name": "VOOL Database"}, OWN_NAME),
    ("acme-parcel", {"publisher": "Official ✓"}, VOUCHING),
])
@pytest.mark.parametrize("action", ["install", "verify", "update", "enable"])
def test_the_operator_lifecycle_door_refuses_a_pack_posing_as_vools(profile, monkeypatch, plugin_id, block, sentence, action):
    from core.web.api.repoops_api import plugin_lifecycle_action

    folder = _dropped_pack(profile, plugin_id, **block)
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: ((plugin_id, folder),))
    result = plugin_lifecycle_action(action=action, plugin_id=plugin_id)
    assert result == {"ok": False, "action": action, "plugin_id": plugin_id, "error": sentence}
    assert lifecycle.record_for(plugin_id) is None


def test_the_operator_lifecycle_door_still_takes_a_plain_pack_and_vools_own(profile, monkeypatch, tmp_path):
    from core.web.api.repoops_api import plugin_lifecycle_action

    folder = _dropped_pack(profile, "acme-parcel")
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: (("acme-parcel", folder),))
    assert plugin_lifecycle_action(action="install", plugin_id="acme-parcel")["ok"] is True
    # A pack VOOL ships keeps its own id: it is VOOL's by where it is loaded from.
    shipped = plugin_catalog.bundled_plugin_dirs()
    if shipped:
        own = json.loads((shipped[0] / ".codex-plugin/plugin.json").read_text())["name"]
        monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: ((own, shipped[0]),))
        assert plugin_lifecycle_action(action="install", plugin_id=own)["ok"] is True


@pytest.mark.parametrize("fields, sentence", [
    ({"name": "VOOL Official helper"}, OWN_NAME),
    ({"publisher": "Verified by the store"}, VOUCHING),
    ({"name": "Notes ✓"}, VOUCHING),
])
def test_a_skill_from_the_public_list_is_not_added_under_a_name_that_poses_as_vools(tmp_path, monkeypatch, fields, sentence):
    from core import addon_store as store
    from core.addon_security import AddonError
    from tests.test_addon_store import _base_rig, _install_scanner

    with _base_rig(tmp_path, monkeypatch):
        _install_scanner()
        entry = dict(store.CATALOG[0], repository="https://github.com/someone/skills", **fields)
        monkeypatch.setattr(store, "CATALOG", [entry])
        review = store.prepare("example", approved=True)
        assert review["can_install"]
        with pytest.raises(AddonError) as refused:
            store.install_review(review["review_id"], accepted=True)
        assert refused.value.message == sentence
        assert not [record for record in lifecycle.records() if record.plugin_id != "sample-scanner"]
        assert not (tmp_path / "addons/plugins" / (store.PREFIX + "example")).exists()
