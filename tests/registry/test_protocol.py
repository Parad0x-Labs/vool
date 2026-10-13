import copy

import pytest

from core.plugin_package import capture_package
from core.registry.admission import admit, validate_policy
from core.registry.artifact import inspect, materialize, pack
from core.registry.codec import RegistryError, canonical, decode
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import (
    NOW,
    TARGET,
    artifact_fixture,
    catalog_fixture,
    evidence_fixture,
    signed,
    trust_fixture,
)


def test_transport_recreates_all_bytes_modes_and_empty_directories(tmp_path):
    raw, root, _ = artifact_fixture(tmp_path, executable=True)
    output = tmp_path / "restored"
    artifact = materialize(raw, output)
    assert capture_package(output).digest == capture_package(root).digest == artifact["native_digest"]
    assert (output / "empty").is_dir()
    assert (output / "tool.py").stat().st_mode & 0o111 == 0o111


@pytest.mark.parametrize("mutation", ["bytes", "mode", "path", "duplicate", "directory"])
def test_altered_distribution_is_refused(tmp_path, mutation):
    raw, _, _ = artifact_fixture(tmp_path, executable=True)
    value = decode(raw)
    item = next(item for item in value["entries"] if item["path"] == "tool.py")
    if mutation == "bytes":
        item["content"] = "Zm9yZ2Vk"
    elif mutation == "mode":
        item["executable"] = 0
    elif mutation == "path":
        item["path"] = "../escape"
    elif mutation == "duplicate":
        value["entries"].append(dict(item))
    else:
        value["entries"] = [item for item in value["entries"] if item["path"] != "empty"]
    with pytest.raises(RegistryError):
        inspect(canonical(value))


def test_credential_filename_is_refused_before_capture(tmp_path):
    _, root, info = artifact_fixture(tmp_path)
    (root / ".env.local").write_text("must not be packed")
    with pytest.raises(RegistryError, match="credential"):
        pack(root, info, tmp_path / "second")


@pytest.mark.parametrize("mutation", ["missing", "warning", "skipped", "stale", "config", "input", "dependencies"])
def test_incomplete_or_unbound_evidence_cannot_admit(tmp_path, mutation):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, _, _ = trust_fixture()
    evidence = evidence_fixture(raw, policy)
    if mutation == "missing":
        evidence["checks"].pop()
    elif mutation == "warning":
        evidence["checks"][0]["findings"] = [{"severity": "warning", "message": "unreviewed"}]
    elif mutation == "skipped":
        evidence["checks"][0]["coverage"] = "unsupported"
    elif mutation == "stale":
        evidence["checks"][0]["completed_at"] = NOW - 3601
    elif mutation == "config":
        evidence["checks"][0]["scanner"]["config_sha256"] = "f" * 64
    elif mutation == "input":
        evidence["artifact_sha256"] = "f" * 64
    else:
        evidence["dependencies"] = {"hidden": "f" * 64}
    with pytest.raises(RegistryError):
        admit(inspect(raw), policy, evidence, now=NOW)


def test_persisted_catalog_blocks_replay_equivocation_expiry_and_revocation(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    state = tmp_path / "state.sqlite"
    client = TrustedCatalog(root, state, now=NOW)
    first = catalog_fixture(raw, policy, keys)
    client.accept(first, now=NOW)
    entry = client.resolve("alice/echo", "1.0.0", target=TARGET, now=NOW)
    second = catalog_fixture(raw, policy, keys, sequence=2, revoked=[entry["artifact_sha256"]])
    client.accept(second, now=NOW)
    with pytest.raises(RegistryError, match="revoked"):
        client.resolve("alice/echo", "1.0.0", target=TARGET, now=NOW)
    with pytest.raises(RegistryError, match="rollback"):
        client.accept(first, now=NOW)
    changed = copy.deepcopy(second["signed"])
    changed["revoked"] = []
    with pytest.raises(RegistryError, match="authorization"):
        client.accept(signed(changed, keys["catalog"]), now=NOW)
    client.close()
    reopened = TrustedCatalog(root, state, now=NOW)
    with pytest.raises(RegistryError, match="stale"):
        reopened.resolve("alice/echo", "1.0.0", target=TARGET, now=NOW + 3601)
    reopened.close()


def test_online_key_cannot_admit_and_duplicate_signature_does_not_meet_threshold(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    catalog = catalog_fixture(raw, policy, keys)
    entry = catalog["signed"]["entries"][0]["signed"]
    client = TrustedCatalog(root, tmp_path / "state", now=NOW)
    for forged in (signed(entry, keys["catalog"]), signed(entry, dict(list(keys["admission"].items())[:1]))):
        if len(forged["signatures"]) == 1:
            forged["signatures"] *= 2
        catalog["signed"]["entries"] = [forged]
        with pytest.raises(RegistryError):
            client.accept(signed(catalog["signed"], keys["catalog"]), now=NOW)
    client.close()


def test_wrong_runtime_target_cannot_install(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    client = TrustedCatalog(root, tmp_path / "state", now=NOW)
    client.accept(catalog_fixture(raw, policy, keys), now=NOW)
    with pytest.raises(RegistryError, match="exact VOOL"):
        client.resolve("alice/echo", "1.0.0", target={**TARGET, "commit": "c" * 40}, now=NOW)
    client.close()


def test_static_policy_pin_cannot_reuse_a_verdict_for_another_target(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, _, _ = trust_fixture()
    evidence = evidence_fixture(raw, policy)
    changed = copy.deepcopy(policy)
    changed["targets"][0]["commit"] = "f" * 40
    assert validate_policy(changed) == validate_policy(policy)
    with pytest.raises(RegistryError, match="target matrix"):
        admit(inspect(raw), changed, evidence, now=NOW)
