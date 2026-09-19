"""One coherent plugins-root resolution: explicit, current, legacy reuse, and the bundled source.

The measured defects this file pins (2026-09-19):

* the default root was hard-wired to ``~/Desktop/Vool-skills-plugins`` while the owner's real
  installation lived in the pre-rename ``~/Desktop/Nulla-skills-plugins`` -- two manifest-bearing
  packs existed and NOTHING discovered them (the catalog reported the missing-current state);
* the app's own ``plugins/`` tree (the first-party packs the bundle ships, e.g. vool-database)
  was invisible to the runtime: the bundle never staged it (SRC_PACKAGES) and no source read it;
* two packs declaring the same identity could both reach the catalog and the registry with no
  rule and no report.

Every case here drives the real resolvers against disposable fixture trees. Nothing touches the
operator's real Desktop: HOME is repointed and VOOL_PLUGINS_DIR is cleared per case.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import plugin_catalog as pc
from tests._toolchain_fixtures import PLUGIN_ID, make_plugin, reset_toolchain_state


@pytest.fixture
def world(tmp_path, monkeypatch):
    """An isolated home with NO Desktop plugins tree, a clean enable store, no bundled source."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "runtime-home"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.delenv("VOOL_PLUGINS_DIR", raising=False)
    empty = tmp_path / "no-native-skills"
    empty.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(empty))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled-tree"))
    reset_toolchain_state()
    pc.reset_storage_state()
    yield home
    reset_toolchain_state()
    pc.reset_storage_state()


def _install_pack(root: Path, plugin_id: str = PLUGIN_ID) -> Path:
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    return make_plugin(root, plugin_id=plugin_id, admit=False)


# --- configured_plugins_root: explicit > current > legacy reuse > current write target ---------


def test_an_explicit_override_wins_over_every_folder_on_disk(world, tmp_path, monkeypatch) -> None:
    override = tmp_path / "operator-configured"
    _install_pack(override)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(override))
    # Desktop folders also exist and would otherwise be picked.
    _install_pack(world / "Desktop" / "Vool-skills-plugins")

    assert pc.configured_plugins_root() == override
    assert pc.plugins_root() == override


def test_a_legacy_only_installation_is_reused_without_moving_it(world) -> None:
    legacy = world / "Desktop" / "Nulla-skills-plugins"
    _install_pack(legacy)

    assert pc.plugins_root() == legacy, "the existing pre-rename installation must be discovered"
    # Reuse, not migration: nothing was created in the current-named folder.
    assert not (world / "Desktop" / "Vool-skills-plugins").exists()


def test_both_folders_existing_resolves_to_the_current_one_deterministically(world) -> None:
    current = world / "Desktop" / "Vool-skills-plugins"
    legacy = world / "Desktop" / "Nulla-skills-plugins"
    _install_pack(current, plugin_id="current-pack")
    _install_pack(legacy, plugin_id="legacy-pack")

    assert pc.plugins_root() == current
    catalog = pc.read_plugin_catalog()
    ids = {entry["id"] for entry in catalog["plugins"]}
    assert "current-pack" in ids and "legacy-pack" not in ids, "one tree, deterministically"


def test_a_fresh_profile_targets_the_current_name_and_reports_missing(world) -> None:
    assert pc.plugins_root() is None
    assert pc.configured_plugins_root() == world / "Desktop" / "Vool-skills-plugins"
    catalog = pc.read_plugin_catalog()
    assert catalog["installed"] is False
    assert catalog["storage"]["state"] == pc.STORAGE_MISSING
    assert catalog["plugins"] == [], "no native library, no bundled packs, no install: an honest empty catalog"


def test_a_directory_without_a_plugins_subtree_is_not_an_installation(world) -> None:
    # A stray folder (created by hand, or the legacy name used for something else) must not be
    # mistaken for an installation: only a root carrying plugins/ counts.
    (world / "Desktop" / "Nulla-skills-plugins").mkdir(parents=True)
    assert pc.plugins_root() is None


# --- the bundled source ------------------------------------------------------------------------


@pytest.fixture
def bundled(world, tmp_path):
    """A bundled tree with ONE manifest-bearing pack, wired as the runtime's bundled source."""
    root = tmp_path / "app-tree"
    pack = root / "plugins" / "bundled-pack"
    (pack / ".codex-plugin").mkdir(parents=True)
    (pack / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "bundled-pack",
                "version": "1.0.0",
                "description": "A pack shipped inside the app.",
                "runtime": {"contract_version": 1},
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    import os

    os.environ["VOOL_BUNDLED_PLUGINS_DIR"] = str(root / "plugins")
    yield root
    os.environ["VOOL_BUNDLED_PLUGINS_DIR"] = str(tmp_path / "no-bundled-tree")


def _bundled_tool_pack(root: Path, plugin_id: str = "bundled-tools") -> Path:
    """A bundled pack carrying a REAL loadable tool, through the shared fixture writer."""
    staging_root = root / "bundle-source"
    make_plugin(staging_root, plugin_id=plugin_id, admit=False)
    pack = staging_root / "plugins" / plugin_id
    destination = root / "plugins" / plugin_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    pack.rename(destination)
    import os

    os.environ["VOOL_BUNDLED_PLUGINS_DIR"] = str(root / "plugins")
    from core import plugin_lifecycle

    plugin_lifecycle.install(plugin_id, root=destination, source="bundled-fixture")
    plugin_lifecycle.verify(plugin_id, root=destination)
    plugin_lifecycle.enable(plugin_id)
    return destination


def test_bundled_packs_are_catalogued_even_with_no_external_installation(world, bundled) -> None:
    catalog = pc.read_plugin_catalog()

    assert catalog["installed"] is False, "no Desktop tree: the external state stays missing"
    entries = {entry["id"]: entry for entry in catalog["plugins"]}
    assert "bundled-pack" in entries
    assert entries["bundled-pack"]["origin"] == "bundled"
    assert entries["bundled-pack"]["enabled"] is True


def test_the_boot_registers_bundled_tools_without_any_desktop_folder(world, tmp_path) -> None:
    from core import tool_registry

    _bundled_tool_pack(tmp_path / "app-tree")
    state = pc.discover_and_register(budget_s=pc.BOOT_PROBE_BUDGET_S, reason="boot")

    assert state["state"] == pc.STORAGE_MISSING, state
    assert state["bundled_loaded"] == ["bundled-tools"], state
    sources = {str(getattr(c, "source", "")) for c in tool_registry.registry_map().values()}
    assert "plugin:bundled-tools" in sources, "the shipped pack's tools load on a fresh profile"


def test_listing_bundled_packs_does_not_register_their_tools(world, bundled) -> None:
    from core import tool_registry

    entries = pc.discovered_plugin_sources()

    assert [pid for pid, _ in entries] == ["bundled-pack"]
    assert not any(
        str(getattr(c, "source", "")).startswith("plugin:") for c in tool_registry.registry_map().values()
    ), "listing stays an observation; only the boot door registers"


def test_a_duplicate_identity_keeps_the_bundled_pack_and_reports_the_installed_copy(
    world, bundled, tmp_path, monkeypatch
) -> None:
    external = tmp_path / "external"
    _install_pack(external, plugin_id="other-pack")
    duplicate = external / "plugins" / "bundled-pack-duplicate"
    (duplicate / ".codex-plugin").mkdir(parents=True)
    (duplicate / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "bundled-pack", "version": "9.9.9", "description": "same identity", "tools": []}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(external))

    sources = dict(pc.discovered_plugin_sources())
    assert str(sources["bundled-pack"]).endswith("bundled-pack")
    duplicates = pc.duplicate_plugin_sources()
    assert len(duplicates) == 1 and duplicates[0]["id"] == "bundled-pack"
    assert str(duplicates[0]["dir"]).endswith("bundled-pack-duplicate")

    catalog = pc.read_plugin_catalog()
    assert catalog["duplicates"] == [
        {"id": "bundled-pack", "dir": duplicates[0]["dir"], "reason": duplicates[0]["reason"]}
    ]
    ids = [entry["id"] for entry in catalog["plugins"]]
    assert ids.count("bundled-pack") == 1, "one identity, once, in the catalog"


def test_two_installed_packs_declaring_one_identity_keep_the_first_sorted_dir(world, tmp_path, monkeypatch) -> None:
    external = tmp_path / "external"
    external.mkdir()
    for dirname in ("b-second", "a-first"):
        pack = external / "plugins" / dirname
        (pack / ".codex-plugin").mkdir(parents=True)
        (pack / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "same-id", "version": "1.0.0", "description": "duplicate identity", "tools": []}),
            encoding="utf-8",
        )
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(external))

    sources = dict(pc.discovered_plugin_sources())
    assert str(sources["same-id"]).endswith("a-first"), "sorted directory order decides, deterministically"
    assert [d["id"] for d in pc.duplicate_plugin_sources()] == ["same-id"]


def test_a_disabled_bundled_pack_stays_listed_with_its_state(world, bundled, monkeypatch) -> None:
    from core.plugin_catalog import set_plugin_enabled

    assert set_plugin_enabled("bundled-pack", False) is True

    catalog = pc.read_plugin_catalog()
    entry = next(e for e in catalog["plugins"] if e["id"] == "bundled-pack")
    assert entry["enabled"] is False, "disabled is a state, not an absence"

    from core.tool_offer_assembly import _plugin_dirs

    assert all(pid != "bundled-pack" for pid, _ in _plugin_dirs()), "a disabled pack guides no turn"


# --- the shared resolver reaches the writers and the render bridge ------------------------------


def test_skill_staging_and_install_target_the_resolved_tree(world, tmp_path, monkeypatch) -> None:
    legacy = world / "Desktop" / "Nulla-skills-plugins"
    _install_pack(legacy)
    from core import skill_tools

    assert skill_tools.plugins_root() == legacy
    assert skill_tools.staging_root() == legacy / "staged-skills"
    # The create path writes through the resolver, never through a hard-coded current-name path.
    result = skill_tools.create_skill(name="Resolver Probe", description="pins the write target", body="do nothing")
    assert result["status"] == "ok", result
    assert str(result["path"]).startswith(str(legacy / "staged-skills"))


def test_the_local_render_bridge_finds_a_legacy_named_pack(world, tmp_path) -> None:
    legacy = world / "Desktop" / "Nulla-skills-plugins"
    script = legacy / "plugins" / "nulla-local-render" / "runtime" / "render_sdxl.py"
    script.parent.mkdir(parents=True)
    script.write_text("# render", encoding="utf-8")
    from core import local_media_render

    assert local_media_render.local_render_script() == script
    assert local_media_render.local_render_available() is True


def test_the_local_render_bridge_prefers_the_current_named_pack(world, tmp_path) -> None:
    current = world / "Desktop" / "Vool-skills-plugins"
    legacy = world / "Desktop" / "Nulla-skills-plugins"
    for tree, name in ((current, "vool-local-render"), (legacy, "nulla-local-render")):
        p = tree / "plugins" / name / "runtime" / "render_sdxl.py"
        p.parent.mkdir(parents=True)
        p.write_text("# render", encoding="utf-8")
    from core import local_media_render

    assert local_media_render.local_render_script() == current / "plugins" / "vool-local-render" / "runtime" / "render_sdxl.py"
