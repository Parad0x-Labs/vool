"""A verified installation can advance after rotation without accepting a rollback."""
import json
from pathlib import Path

import pytest

from core import plugin_catalog, plugin_lifecycle
from core.registry import native
from core.registry.codec import NEWER_VERSION_REQUIRED, RegistryError, canonical, decode
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, catalog_fixture, signed, trust_fixture
from tests.registry.test_skill_codex_checks import info, package


@pytest.fixture
def installation(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    for name, path in {
        "VOOL_HOME": runtime,
        "VOOL_PLUGIN_LIFECYCLE_PATH": runtime / "plugin_lifecycle.json",
        "VOOL_PLUGINS_DIR": runtime / "addons",
        "VOOL_BUNDLED_PLUGINS_DIR": tmp_path / "no-bundled",
        "VOOL_NATIVE_SKILLS_DIR": tmp_path / "no-native",
    }.items():
        monkeypatch.setenv(name, str(path))
    plugin_lifecycle.reset_for_tests()
    plugin_catalog.invalidate_storage_listing()
    (tmp_path / "first").mkdir()
    raw = package(tmp_path / "first")
    policy, keys, root = trust_fixture()
    state = runtime / "config" / native.STATE_NAME
    catalog = TrustedCatalog(root, state, now=NOW)
    catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
    result = native.install(catalog, raw, package_id="alice/notes", version="1.0.0", target=TARGET,
                            accepted=True, enable=True, now=NOW)
    assert plugin_lifecycle.is_available(result["plugin_id"])
    yield catalog, policy, keys, root, result["plugin_id"], state
    catalog.close()


def offer(tmp_path, catalog, policy, keys, version):
    (tmp_path / ("next-" + version)).mkdir()
    raw = package(tmp_path / ("next-" + version), metadata=info(version=version))
    catalog.accept(catalog_fixture(raw, policy, keys, root_version=catalog.root["version"], sequence=2), now=NOW)
    return raw


def update(catalog, raw, version):
    return native.install(catalog, raw, package_id="alice/notes", version=version, target=TARGET,
                          accepted=True, enable=True, now=NOW)


@pytest.mark.parametrize("rotate", [False, True])
@pytest.mark.parametrize("version", ["1.1.0", "1.0.0", "0.9.0"])
def test_update_uses_the_root_that_verified_the_installation(installation, tmp_path, rotate, version):
    catalog, policy, keys, _, plugin_id, _ = installation
    before = plugin_lifecycle.record_for(plugin_id)
    receipt = native._receipt_path(plugin_id).read_bytes()
    if rotate:
        _, replacement_keys, replacement = trust_fixture()
        catalog.rotate(signed({**replacement["signed"], "version": 2}, keys["root"] | replacement_keys["root"]), now=NOW)
        keys = replacement_keys
        assert not plugin_lifecycle.is_available(plugin_id)
    raw = offer(tmp_path, catalog, policy, keys, version)
    if version == "1.1.0":
        assert update(catalog, raw, version)["version"] == version
        assert plugin_lifecycle.record_for(plugin_id).version == version
        assert plugin_lifecycle.is_available(plugin_id)
    else:
        with pytest.raises(RegistryError, match=NEWER_VERSION_REQUIRED):
            update(catalog, raw, version)
        assert plugin_lifecycle.record_for(plugin_id) == before
        assert native._receipt_path(plugin_id).read_bytes() == receipt


@pytest.mark.parametrize("damage", ["json", "signature", "receipt-version", "record-version", "missing-root"])
def test_unverifiable_prior_installation_is_preserved(installation, tmp_path, damage):
    catalog, policy, keys, _, plugin_id, _ = installation
    path = native._receipt_path(plugin_id)
    if damage == "json":
        path.write_bytes(b"not json")
    elif damage == "record-version":
        store = plugin_lifecycle.store_path()
        value = json.loads(store.read_text())
        value["plugins"][plugin_id]["version"] = "0.1.0"
        store.write_text(json.dumps(value))
    elif damage == "missing-root":
        catalog.db.execute("DELETE FROM root_history")
        catalog.db.commit()
    else:
        receipt = decode(path.read_bytes())
        if damage == "signature":
            receipt["admission"]["signatures"][0]["sig"] = "AAAA"
        else:
            receipt["admission"]["signed"]["metadata"]["version"] = "0.1.0"
        path.write_bytes(canonical(receipt))
    before_record = plugin_lifecycle.store_path().read_bytes()
    before_receipt = path.read_bytes()
    installed = Path(plugin_lifecycle.record_for(plugin_id).root)
    before_files = {p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()}
    raw = offer(tmp_path, catalog, policy, keys, "1.1.0")
    with pytest.raises(RegistryError, match=r"installed registry receipt.*could not be (read|verified)"):
        update(catalog, raw, "1.1.0")
    assert plugin_lifecycle.store_path().read_bytes() == before_record
    assert path.read_bytes() == before_receipt
    assert {p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()} == before_files


def test_legacy_trust_state_retains_receipt_keys_through_rotation_and_restart(installation, tmp_path):
    catalog, policy, keys, root, plugin_id, state = installation
    catalog.db.execute("DROP TABLE root_history")
    catalog.db.commit()
    catalog.close()
    catalog = TrustedCatalog(root, state, now=NOW)
    _, next_keys, next_root = trust_fixture()
    rotated = signed({**next_root["signed"], "version": 2}, keys["root"] | next_keys["root"])
    catalog.rotate(rotated, now=NOW)
    catalog.close()
    catalog = TrustedCatalog(rotated, state, now=NOW)
    try:
        raw = offer(tmp_path, catalog, policy, next_keys, "1.1.0")
        assert update(catalog, raw, "1.1.0")["version"] == "1.1.0"
        assert plugin_lifecycle.is_available(plugin_id)
    finally:
        catalog.close()
