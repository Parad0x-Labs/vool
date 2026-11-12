"""Operator lifecycle requests select admitted catalog identities, never arbitrary paths.

The id in a lifecycle request picks one identity from the server-owned catalog
(``core.plugin_catalog.discovered_plugin_sources``), and the catalog names the directory. A
folder the catalog did not admit -- an installed copy that declares a bundled pack's identity,
or a directory whose manifest names another pack -- is never installed, verified, updated or
re-registered through this door.
"""

from __future__ import annotations

import json

import pytest

from core import plugin_catalog, plugin_lifecycle
from core.web.api import repoops_api
from core.web.api.repoops_api import plugin_lifecycle_action


def _pack(directory, name: str):
    (directory / ".codex-plugin").mkdir(parents=True)
    (directory / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1"}))
    return directory


@pytest.fixture
def world(tmp_path, monkeypatch):
    base = tmp_path / "catalog"
    (base / "plugins").mkdir(parents=True)
    outside = _pack(base / "outside", "outside")
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(base))
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: ())
    return base, outside


def test_path_shaped_id_cannot_install_an_outside_pack(world):
    result = plugin_lifecycle_action(action="install", plugin_id="../outside")
    assert result["ok"] is False
    assert plugin_lifecycle.record_for("../outside") is None


def test_absolute_path_cannot_install_an_outside_pack(world):
    result = plugin_lifecycle_action(action="install", plugin_id=str(world[1]))
    assert result["ok"] is False
    assert plugin_lifecycle.record_for(str(world[1])) is None


def test_manifest_identity_selects_its_catalog_directory(world, monkeypatch):
    root = world[1]
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: (("outside", root),))
    assert plugin_lifecycle_action(action="install", plugin_id="outside")["ok"] is True
    assert plugin_lifecycle.record_for("outside").root == str(root)
    assert plugin_lifecycle_action(action="verify", plugin_id="outside")["ok"] is True
    assert plugin_lifecycle_action(action="enable", plugin_id="outside")["available"] is True
    assert plugin_lifecycle_action(action="disable", plugin_id="outside")["available"] is False
    assert plugin_lifecycle_action(action="uninstall", plugin_id="outside")["ok"] is True


@pytest.mark.parametrize("action", ["install", "verify", "update"])
def test_a_folder_the_catalog_did_not_admit_is_refused(world, monkeypatch, action):
    """A directory under plugins/ is not an identity until the catalog admits it."""
    base, _outside = world
    _pack(base / "plugins" / "stray", "stray")
    result = plugin_lifecycle_action(action=action, plugin_id="stray")
    assert result == {
        "ok": False,
        "action": action,
        "plugin_id": "stray",
        "error": "plugin is absent from the catalog",
    }
    assert plugin_lifecycle.record_for("stray") is None


def test_an_installed_copy_cannot_shadow_the_bundled_identity_it_declares(world, monkeypatch):
    """The catalog keeps the bundled pack for a duplicated identity; so does the lifecycle door."""
    base, _outside = world
    bundled = _pack(base / "bundled" / "wallet", "wallet")
    # The installed folder is named after itself but declares the bundled pack's identity, so the
    # catalog's merged inventory drops it as a duplicate and keeps only the bundled directory.
    _pack(base / "plugins" / "notes", "wallet")
    monkeypatch.setattr(plugin_catalog, "discovered_plugin_sources", lambda: (("wallet", bundled),))

    assert plugin_lifecycle_action(action="install", plugin_id="notes")["ok"] is False
    assert plugin_lifecycle.record_for("notes") is None

    assert plugin_lifecycle_action(action="install", plugin_id="wallet")["ok"] is True
    assert plugin_lifecycle.record_for("wallet").root == str(bundled)


def test_reregistration_reads_only_the_catalogs_directory(world, monkeypatch):
    root = world[1]
    loaded = []

    from core import plugin_tools

    def fake_load(manifest):
        loaded.append(str(manifest))
        raise RuntimeError("stop after the manifest was chosen")

    monkeypatch.setattr(plugin_tools, "load_manifest", fake_load)
    ok, error = repoops_api._reregister_pack("outside", root)
    assert ok is False and "stop after the manifest was chosen" in error
    assert loaded == [str(root / ".codex-plugin" / "plugin.json")]

    assert repoops_api._reregister_pack("outside", None) == (False, "plugin is absent from the catalog")
    assert len(loaded) == 1


def test_served_lifecycle_refuses_a_path_identifier(world):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path="/api/plugins/lifecycle",
        body={"action": "install", "plugin_id": "../outside"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="fixture",
        client_host="127.0.0.1",
        headers={},
        workspace_root_provider=lambda: world[0],
    )
    assert json.loads(response.body)["ok"] is False
    assert plugin_lifecycle.record_for("../outside") is None
