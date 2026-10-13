import hashlib
import json

import pytest

from core import plugin_lifecycle
from core.registry.admission import admit
from core.registry.artifact import inspect, pack
from core.registry.codec import RegistryError
from core.registry.native import install
from core.registry.trust import validate_entry
from tests.registry.audit_helpers import package, runtime, trusted
from tests.registry.helpers import NOW, TARGET, evidence_fixture, trust_fixture


def test_a2_artifact_cannot_borrow_another_package_native_identity(tmp_path):
    _, source, info = package(tmp_path)
    info["id"] = "mallory/echo"
    info["source"]["repository"] = "mallory/notes"
    with pytest.raises(RegistryError, match="identity"):
        pack(source, info, tmp_path / "borrowed")


def test_a2_signed_entry_must_bind_plugin_id_to_package_id(tmp_path):
    raw, _, _ = package(tmp_path)
    policy, _, root = trust_fixture()
    entry = admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
    entry.update(schema="vool.registry.admission.v1", issued_at=NOW, expires=NOW + 7200,
                 plugin_id="registry-" + hashlib.sha256(b"mallory/echo").hexdigest()[:24])
    with pytest.raises(RegistryError, match="identity"):
        validate_entry(entry, root["signed"], NOW)


def test_a2_signed_native_install_refuses_alias_before_lifecycle_mutation(tmp_path, monkeypatch):
    home = runtime(tmp_path, monkeypatch)
    _, source, info = package(tmp_path)
    info["id"] = "mallory/echo"
    info["source"]["repository"] = "mallory/notes"
    with pytest.raises(RegistryError, match="identity"):
        raw = pack(source, info, tmp_path / "malicious")
        with trusted(raw, home / "config/registry_trust.sqlite") as (catalog, *_):
            install(catalog, raw, package_id=info["id"], version=info["version"], target=TARGET,
                    accepted=True, enable=False, now=NOW)
    assert not plugin_lifecycle.records()


def test_a2_supported_ids_keep_one_identity_across_versions(tmp_path):
    raw, source, info = package(tmp_path)
    identity = inspect(raw)["manifest"]["name"]
    assert identity == "registry-" + hashlib.sha256(info["id"].encode()).hexdigest()[:24]
    info["version"] = "1.0.1"
    path = source / ".codex-plugin/plugin.json"
    manifest = json.loads(path.read_bytes())
    manifest["version"] = info["version"]
    path.write_text(json.dumps(manifest))
    assert inspect(pack(source, info, tmp_path / "updated"))["manifest"]["name"] == identity
