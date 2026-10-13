import copy

import pytest

from core.registry.codec import RegistryError, canonical, digest
from core.registry.publisher import add_signature, admission, attach_to_directory, authorize_catalog, catalog
from core.registry.trust import TrustedCatalog
from tests.registry.helpers import NOW, TARGET, artifact_fixture, evidence_fixture, signed, trust_fixture
from tools.registry_ci import check_candidate, latest_release


def test_ci_checks_candidate_commit_latest_release_and_signed_evidence(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    review = signed(evidence_fixture(raw, policy), keys["review"])
    result = check_candidate(raw, review, policy, root["signed"], publication_commit="c" * 40,
        released={"version": TARGET["version"], "commit": TARGET["commit"]}, scratch=tmp_path / "ci", now=NOW)
    assert result["status"] == "eligible_for_admission" and result["admitted"] is False
    for source_sha, version in (("d" * 40, TARGET["version"]), (TARGET["commit"], "0.7.2")):
        with pytest.raises(RegistryError, match="latest released"):
            check_candidate(raw, review, policy, root["signed"], publication_commit="c" * 40,
                released={"version": version, "commit": source_sha}, scratch=tmp_path / "ci", now=NOW)
    with pytest.raises(RegistryError, match="different candidate"):
        check_candidate(raw, review, policy, root["signed"], publication_commit="f" * 40,
            released={"version": TARGET["version"], "commit": TARGET["commit"]}, scratch=tmp_path / "ci", now=NOW)


def test_latest_release_resolves_annotated_tag_and_rejects_drafts():
    replies = [{"draft": False, "prerelease": False, "tag_name": "v0.7.1"},
               {"object": {"type": "tag", "sha": "b" * 40}},
               {"object": {"type": "commit", "sha": "a" * 40}}]
    urls = []

    def request(url, *, limit):
        urls.append(url)
        return canonical(replies.pop(0))

    assert latest_release(request=request) == {"version": "0.7.1", "commit": "a" * 40}
    assert all(url.startswith("https://api.github.com/repos/Parad0x-Labs/vool/") for url in urls)
    with pytest.raises(RegistryError, match="published"):
        latest_release(request=lambda *args, **kwargs: canonical({"draft": True, "prerelease": False}))


def test_offline_publication_binds_existing_catalog_and_preserves_discovery(tmp_path):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    entry = admission(raw, policy, signed(evidence_fixture(raw, policy), keys["review"]), root["signed"], now=NOW)
    for key in keys["admission"].values():
        entry = add_signature(entry, key)
    authorization = authorize_catalog(root["signed"], [entry], epoch=1, revoked=[], now=NOW)
    for key in keys["admission"].values():
        authorization = add_signature(authorization, key)
    snapshot = catalog(root["signed"], [entry], sequence=1, revoked=[], authorization=authorization, now=NOW)
    for key in keys["catalog"].values():
        snapshot = add_signature(snapshot, key)
    original = {"entries": [{"id": "legacy", "import_ready": True, "discovery_only": False}], "sources": ["pinned"]}
    client = TrustedCatalog(root, tmp_path / "trust", now=NOW)
    result = attach_to_directory(original, snapshot, client, now=NOW)
    assert result["entries"] == original["entries"] and result["sources"] == original["sources"]
    assert set(result) == set(original) | {"registry"}
    changed = copy.deepcopy(snapshot["signed"])
    changed["revoked"] = [entry["signed"]["artifact_sha256"]]
    with pytest.raises(RegistryError, match="authorization"):
        client.accept(signed(changed, keys["catalog"]), now=NOW)
    client.close()
