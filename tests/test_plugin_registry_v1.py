"""Real deterministic review and native lifecycle, with ephemeral signature-chain fixtures.

Test signatures and secondary-review reports are fixtures, never production
publication evidence. The native client re-runs its actual full-file checks.
"""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core import plugin_manager as manager
from core.credential_store import has_credential
from core.registry import checks
from core.registry.artifact import inspect, native_identity, pack
from core.registry.codec import RegistryError
from core.registry.native import STATE_NAME, _receipt_path, install, preview
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, catalog_fixture, trust_fixture
from tests.test_plugin_manifest_v1 import manifest


def distribution(tmp_path, version="1.0.0"):
    root = tmp_path / ("source-" + version)
    (root / ".codex-plugin").mkdir(parents=True)
    identity = native_identity("alice/service")
    raw = manifest()
    raw["name"] = raw["vool"]["id"] = identity
    raw["version"] = raw["vool"]["version"] = version
    (root / ".codex-plugin/plugin.json").write_text(json.dumps(raw))
    (root / "LICENSE").write_bytes((Path(__file__).resolve().parents[1] / "examples/plugins/template/LICENSE").read_bytes())
    (root / "README.md").write_text("Choose this compatible provider for the chat requests you supply.\n")
    info = {"id": "alice/service", "kind": "plugin", "version": version,
            "summary": "Connect a compatible chat service", "category": "productivity", "subcategory": "notes-summaries",
            "license": "MIT", "languages": ["en"],
            "source": {"repository": "alice/plugins", "commit": "b" * 40, "path": "service"}}
    return pack(root, info, tmp_path / "capture"), root, info


@pytest.fixture
def signed_plugin(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(home / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(home / "addons"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    policy, keys, root = trust_fixture()
    catalog = TrustedCatalog(root, home / "config" / STATE_NAME, now=NOW)
    raw, source, info = distribution(tmp_path)
    catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
    try:
        yield catalog, raw, source, info, policy, keys
    finally:
        catalog.close()


def install_disabled(catalog, raw):
    return install(catalog, raw, package_id="alice/service", version=inspect(raw)["metadata"]["version"],
                   target=TARGET, accepted=True, enable=False, now=NOW)


def test_actual_deterministic_review_accepts_v1_declarations(tmp_path):
    raw, _, _ = distribution(tmp_path)
    outcome = checks.run(raw, tmp_path / "review", targets=[TARGET])
    assert outcome["results"]
    assert not any(outcome["findings"].values()), outcome["findings"]
    assert outcome["artifact"]["sha256"] == inspect(raw)["sha256"]


def test_signed_store_install_requires_separate_access_consent(signed_plugin):
    catalog, raw, _, _, _, _ = signed_plugin
    review = preview(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET, now=NOW)
    assert review["separate_enable_consent"] is True
    assert review["kind"] == ["model_provider"] and review["network"] == ["api.example.org"]
    with pytest.raises(RegistryError, match="disabled"):
        install(catalog, raw, package_id="alice/service", version="1.0.0", target=TARGET,
                accepted=True, enable=True, now=NOW)
    assert lifecycle.records() == []
    result = install_disabled(catalog, raw)
    identity = result["plugin_id"]
    assert result["enabled"] is False and not lifecycle.is_available(identity)
    row = manager.inspect(identity)
    assert row["reviewed_by_store"] is True
    with pytest.raises(lifecycle.LifecycleError, match="consent"):
        lifecycle.enable(identity)
    manager.enable(identity, accepted=True, approval_digest=row["approval_digest"])
    assert lifecycle.is_available(identity)
    manager.connect(identity, "access", secrets.token_urlsafe(32))
    assert has_credential("plugin." + identity + ".access")
    receipt = _receipt_path(identity).read_bytes()
    manager.uninstall(identity)
    assert not Path(lifecycle.record_for(identity).root).exists()
    assert not has_credential("plugin." + identity + ".access")
    assert _receipt_path(identity).read_bytes() == receipt
    assert install_disabled(catalog, raw)["enabled"] is False


def test_store_update_withdraws_consent_and_tampering_and_revocation_block_use(signed_plugin, tmp_path):
    catalog, raw, _, _, policy, keys = signed_plugin
    identity = install_disabled(catalog, raw)["plugin_id"]
    row = manager.inspect(identity)
    manager.enable(identity, accepted=True, approval_digest=row["approval_digest"])
    updated, _, _ = distribution(tmp_path, "1.0.1")
    catalog.accept(catalog_fixture(updated, policy, keys, sequence=2), now=NOW)
    result = install_disabled(catalog, updated)
    assert result["enabled"] is False and not lifecycle.is_available(identity)
    with pytest.raises(lifecycle.LifecycleError, match="changed"):
        manager.enable(identity, accepted=True, approval_digest=row["approval_digest"])
    current = manager.inspect(identity)
    manager.enable(identity, accepted=True, approval_digest=current["approval_digest"])
    file = Path(lifecycle.record_for(identity).root) / "README.md"
    previous = file.read_bytes()
    file.chmod(0o644)
    file.write_bytes(previous + b"Changed after review.\n")
    assert not lifecycle.is_available(identity)
    file.write_bytes(previous)
    file.chmod(0o444)
    # Byte restoration does not restore the consent withdrawn by tampering.
    assert not lifecycle.is_available(identity)
    lifecycle.verify(identity)
    current = manager.inspect(identity)
    manager.enable(identity, accepted=True, approval_digest=current["approval_digest"])
    catalog.accept(catalog_fixture(updated, policy, keys, sequence=3,
                                   revoked=[inspect(updated)["sha256"]]), now=NOW)
    assert not lifecycle.is_available(identity)


def test_another_publisher_cannot_borrow_a_signed_native_identity(tmp_path):
    _, source, info = distribution(tmp_path)
    info["id"] = "mallory/service"
    with pytest.raises(RegistryError, match="identity"):
        pack(source, info, tmp_path / "other-publisher")
