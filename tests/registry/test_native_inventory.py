"""Complete captured skills must pass both author and signed native install paths."""
from pathlib import Path

import pytest

from core import plugin_catalog, plugin_lifecycle
from core.plugin_skills import load_skills
from core.registry.artifact import inspect, pack
from core.registry.codec import RegistryError
from core.registry.native import install
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture
from tools.community_skills import preflight


def variant_artifact(tmp_path, variant):
    _, source, info = artifact_fixture(tmp_path)
    skill = source / "skills/echo/SKILL.md"
    valid = skill.read_bytes()
    if variant == "missing-frontmatter":
        skill.write_text("Summarize the supplied notes.\n")
    elif variant == "invalid-yaml":
        skill.write_text("---\nname: echo\ndescription: [unclosed\n---\nSummarize supplied notes.\n")
    elif variant == "invalid-utf8":
        skill.write_bytes(valid + b"\xff")
    elif variant == "mixed-siblings":
        sibling = source / "skills/other/SKILL.md"
        sibling.parent.mkdir()
        sibling.write_text("Instructions without frontmatter.\n")
    elif variant == "nested":
        nested = skill.parent / "deeper/SKILL.md"
        nested.parent.mkdir()
        skill.rename(nested)
    elif variant == "overlong":
        skill.write_bytes(valid + b"a" * 8001)
    elif variant == "flat":
        skill.rename(source / "skills/SKILL.md")
    elif variant == "multiple":
        sibling = source / "skills/other/SKILL.md"
        sibling.parent.mkdir()
        sibling.write_bytes(valid.replace(b"name: echo", b"name: other"))
    elif variant == "plugin-only":
        skill.unlink()
        info["kind"] = "plugin"
    return pack(source, info, tmp_path / "pack"), info


INVALID = ["missing-frontmatter", "invalid-yaml", "invalid-utf8", "mixed-siblings", "nested", "overlong"]
VALID = ["simple", "flat", "multiple", "plugin-only"]


@pytest.mark.parametrize("variant", INVALID + VALID)
@pytest.mark.parametrize("route", ["sdk", "native"])
def test_complete_inventory_sdk_and_signed_native_install(tmp_path, monkeypatch, variant, route):
    raw, info = variant_artifact(tmp_path, variant)
    invalid = variant in INVALID
    if route == "sdk":
        report = preflight(raw, tmp_path / "preflight")
        assert report["admitted"] is False
        assert report["status"] == ("blocked" if invalid else "preflight_passed")
        if invalid:
            assert any(item["check"] == "native-format" for item in report["findings"])
            assert all(not Path(item.get("path", "")).is_absolute() for item in report["findings"])
        return
    home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(home / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(home / "addons"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    plugin_lifecycle.reset_for_tests()
    policy, keys, root = trust_fixture()
    trust = TrustedCatalog(root, home / "config/registry_trust.sqlite", now=NOW)
    try:
        trust.accept(catalog_fixture(raw, policy, keys), now=NOW)
        plugin_id = inspect(raw)["manifest"]["name"]
        destination = plugin_catalog.configured_plugins_root() / "plugins" / plugin_id
        if invalid:
            with pytest.raises(RegistryError, match="native skill validation"):
                install(trust, raw, package_id=info["id"], version=info["version"],
                        target=TARGET, accepted=True, enable=False, now=NOW)
            assert not plugin_lifecycle.records()
            assert not destination.exists()
            assert not (home / "config/registry_receipts" / (plugin_id + ".json")).exists()
            assert not (home / "config/registry_transactions" / (plugin_id + ".json")).exists()
        else:
            result = install(trust, raw, package_id=info["id"], version=info["version"],
                             target=TARGET, accepted=True, enable=False, now=NOW)
            assert result["status"] == "installed" and result["enabled"] is False
            skills = load_skills(destination, plugin_id=plugin_id)
            assert len(skills) == (0 if variant == "plugin-only" else 2 if variant == "multiple" else 1)
            assert all(skill.body for skill in skills)
    finally:
        trust.close()
        plugin_lifecycle.reset_for_tests()
