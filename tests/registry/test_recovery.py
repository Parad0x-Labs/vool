import dataclasses
import json
from pathlib import Path

import pytest

from core import plugin_lifecycle
from core.registry.artifact import inspect, materialize, pack
from core.registry.codec import RegistryError, canonical
from core.registry.native import STATE_NAME, _journal_path, _receipt_path, install, recover
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture


def installed_fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "runtime/plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugin-home"))
    raw, source, info = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    client = TrustedCatalog(root, tmp_path / "runtime/config" / STATE_NAME, now=NOW)
    client.accept(catalog_fixture(raw, policy, keys), now=NOW)
    result = install(client, raw, package_id="alice/echo", version="1.0.0", target=TARGET, accepted=True, enable=True, now=NOW)
    return raw, source, info, policy, keys, client, result["plugin_id"]


def test_failed_update_restores_exact_enabled_installation(tmp_path, monkeypatch):
    raw, source, info, policy, keys, client, plugin_id = installed_fixture(tmp_path, monkeypatch)
    before = plugin_lifecycle.record_for(plugin_id)
    info["version"] = "1.0.1"
    manifest = source / ".codex-plugin/plugin.json"
    value = json.loads(manifest.read_text()); value["version"] = "1.0.1"
    manifest.write_bytes(canonical(value))
    updated = pack(source, info, tmp_path / "new-capture")
    client.accept(catalog_fixture(updated, policy, keys, sequence=2), now=NOW)
    verify = plugin_lifecycle.verify

    def refuse(*args, **kwargs):
        raise plugin_lifecycle.LifecycleError("injected verification failure")

    monkeypatch.setattr(plugin_lifecycle, "verify", refuse)
    with pytest.raises(plugin_lifecycle.LifecycleError):
        install(client, updated, package_id="alice/echo", version="1.0.1", target=TARGET, accepted=True, enable=True, now=NOW)
    monkeypatch.setattr(plugin_lifecycle, "verify", verify)
    after = plugin_lifecycle.record_for(plugin_id)
    assert after.verified_digest == before.verified_digest == inspect(raw)["native_digest"]
    assert after.enabled and plugin_lifecycle.is_available(plugin_id)
    assert not _journal_path(plugin_id).exists()
    client.close()


def test_durable_recovery_restores_a_crashed_filesystem_transition(tmp_path, monkeypatch):
    _, _, _, _, _, client, plugin_id = installed_fixture(tmp_path, monkeypatch)
    previous = plugin_lifecycle.record_for(plugin_id)
    root = tmp_path / "plugin-home"
    owned = root / ".registry-scratch/invocation-crash-fixture"
    owned.mkdir()
    destination = Path(previous.root)
    destination.rename(owned / "previous")
    receipt = json.loads(_receipt_path(plugin_id).read_text())
    journal = {"schema": "vool.registry.transaction.v1", "plugin_id": plugin_id, "root": str(root.resolve()),
        "owned": owned.name, "source": previous.source, "new_digest": "f" * 64,
        "previous": dataclasses.asdict(previous), "receipt": receipt, "was_disabled": False}
    path = _journal_path(plugin_id); path.parent.mkdir(exist_ok=True)
    path.write_bytes(canonical(journal))
    recover(plugin_id, root)
    assert destination.exists() and plugin_lifecycle.is_available(plugin_id)
    assert not path.exists() and not owned.exists()
    client.close()


def test_invalid_update_does_not_mutate_pending_recovery(tmp_path, monkeypatch):
    _, source, info, policy, keys, client, plugin_id = installed_fixture(tmp_path, monkeypatch)
    try:
        previous = plugin_lifecycle.record_for(plugin_id)
        root = tmp_path / "plugin-home"
        owned = root / ".registry-scratch/invocation-invalid-update"
        owned.mkdir()
        destination = Path(previous.root)
        destination.rename(owned / "previous")
        journal = {"schema": "vool.registry.transaction.v1", "plugin_id": plugin_id, "root": str(root.resolve()),
            "owned": owned.name, "source": previous.source, "new_digest": "f" * 64,
            "previous": dataclasses.asdict(previous), "receipt": json.loads(_receipt_path(plugin_id).read_text()),
            "was_disabled": False}
        path = _journal_path(plugin_id)
        path.parent.mkdir(exist_ok=True)
        before = canonical(journal)
        path.write_bytes(before)
        info["version"] = "1.0.1"
        manifest = source / ".codex-plugin/plugin.json"
        value = json.loads(manifest.read_text())
        value["version"] = info["version"]
        manifest.write_bytes(canonical(value))
        (source / "skills/echo/SKILL.md").write_text("No frontmatter in this update.\n")
        invalid = pack(source, info, tmp_path / "invalid")
        client.accept(catalog_fixture(invalid, policy, keys, sequence=2), now=NOW)
        with pytest.raises(RegistryError, match="native skill validation"):
            install(client, invalid, package_id="alice/echo", version="1.0.1", target=TARGET,
                    accepted=True, enable=False, now=NOW)
        assert path.read_bytes() == before
        assert (owned / "previous").is_dir() and not destination.exists()
        assert dataclasses.asdict(plugin_lifecycle.record_for(plugin_id)) == dataclasses.asdict(previous)
        recover(plugin_id, root)
        assert destination.is_dir() and not path.exists()
    finally:
        client.close()
