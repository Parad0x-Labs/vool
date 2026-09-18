"""The Plugins & Skills catalog reader for the console panel."""

from __future__ import annotations

import json

import pytest

from core import plugin_catalog as pc


@pytest.fixture(autouse=True)
def _isolate_native_library(monkeypatch, tmp_path):
    """These tests verify the EXTERNAL plugin repo semantics; the first-party native
    library that read_plugin_catalog now also projects is covered in
    tests/test_native_skill_library.py. Isolate it so counts stay external-only."""
    empty = tmp_path / "no-native-skills"
    empty.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(empty))


def test_missing_repo_is_soft(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "nope"))
    cat = pc.read_plugin_catalog()
    assert cat["installed"] is False
    assert cat["plugin_count"] == 0
    assert cat["reason"]


def test_reads_plugins_and_skills(monkeypatch, tmp_path) -> None:
    plugin = tmp_path / "plugins" / "video-generator"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "video-generator", "version": "1.0.0",
        "author": {"name": "sls_0x", "url": "https://github.com/Parad0x-Labs"},
        "tools": ["video.generate", "video.image_to_video"],
        "permissions": ["filesystem.write", "gpu.use"],
        "interface": {"displayName": "Local Video Generator", "shortDescription": "Make video locally", "category": "Creation"},
    }))
    skill = plugin / "skills" / "create-product-ad"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: create-product-ad\ndescription: Make a 30s product ad.\n---\n\n# Create Product Ad\n")

    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    cat = pc.read_plugin_catalog()
    assert cat["installed"] is True
    assert cat["plugin_count"] == 1 and cat["skill_count"] == 1
    p = cat["plugins"][0]
    assert p["id"] == "video-generator"
    assert p["name"] == "Local Video Generator"
    assert p["version"] == "1.0.0"
    assert p["category"] == "Creation"
    assert p["tools"] == ["video.generate", "video.image_to_video"]
    assert p["permissions"] == ["filesystem.write", "gpu.use"]
    assert p["first_party"] is True  # authored by Parad0x -> gets the badge
    assert p["skills"][0]["name"] == "create-product-ad"
    assert "30s product ad" in p["skills"][0]["description"]
    assert p["enabled"] is True  # nothing disabled yet -> on by default


def _one_plugin(tmp_path) -> None:
    plugin = tmp_path / "plugins" / "video-generator"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "video-generator", "version": "1.0.0",
        "interface": {"displayName": "Local Video Generator"},
    }))


def test_enable_state_persists_and_reflects_in_catalog(monkeypatch, tmp_path) -> None:
    _one_plugin(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))

    # Default: enabled.
    assert pc.read_plugin_catalog()["plugins"][0]["enabled"] is True

    # Disable -> persisted -> reflected.
    assert pc.set_plugin_enabled("video-generator", False) is True
    assert pc.read_plugin_catalog()["plugins"][0]["enabled"] is False
    store = json.loads((tmp_path / "home" / "config" / "plugins_enabled.json").read_text())
    assert store["disabled"] == ["video-generator"]

    # Re-enable -> cleared.
    assert pc.set_plugin_enabled("video-generator", True) is True
    assert pc.read_plugin_catalog()["plugins"][0]["enabled"] is True
    assert json.loads((tmp_path / "home" / "config" / "plugins_enabled.json").read_text())["disabled"] == []


def test_set_plugin_enabled_rejects_blank_id(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    assert pc.set_plugin_enabled("", False) is False
    assert pc.set_plugin_enabled("   ", True) is False
