import json

from core import plugin_catalog


def test_bundled_provenance_is_independent_of_author_claim(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    plugin = bundled / "shipped"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "shipped", "version": "1.0.0"}))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(bundled))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "external"))
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "runtime"))
    plugin_catalog.invalidate_storage_listing()
    row = next(row for row in plugin_catalog.read_plugin_catalog()["plugins"] if row["id"] == "shipped")
    assert row["origin"] == "bundled" and row["first_party"] is True
