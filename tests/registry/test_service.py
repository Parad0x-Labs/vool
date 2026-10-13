import copy
import hashlib

import pytest
from starlette.testclient import TestClient

from core.registry.artifact import pack
from core.registry.codec import RegistryError, canonical, decode
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth, Provider
from registry_service.store import Store
from tests.registry.helpers import (
    NOW,
    TARGET,
    artifact_fixture,
    catalog_fixture,
    evidence_fixture,
    signed,
    trust_fixture,
)


def setup_service(tmp_path):
    raw, source, info = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private" / "accounts.sqlite")
    auth = Auth(store, "https://registry.test", [])
    catalog = TrustedCatalog(root, tmp_path / "trust" / "state", now=NOW)
    app = create_app(store, auth, catalog, TARGET, clock=lambda: NOW)
    account = store.identity("github", "123", now=NOW)
    store.profile(account, {"namespace": "alice", "bio": "Author", "handles": {"github": "alice"}})
    token, csrf = store.session(account, now=NOW)
    return raw, source, info, policy, keys, root, store, catalog, app, account, token, csrf


def test_private_upload_to_review_to_publication_and_one_star(tmp_path):
    raw, _, _, policy, keys, root, store, catalog, app, account, token, csrf = setup_service(tmp_path)
    headers = {"Origin": "https://registry.test", "X-Vool-CSRF": csrf,
               "Content-Type": "application/vnd.vool.registry+json"}
    with TestClient(app, base_url="https://registry.test") as client:
        client.cookies.set("vool_registry", token)
        upload = client.post(PREFIX + "/submissions", content=raw, headers=headers)
        assert upload.status_code == 201, upload.text
        submission = upload.json()
        assert submission["state"] == "quarantined"
        before = client.get(PREFIX + "/catalog")
        assert before.status_code == 400  # no approved snapshot, not a simulated empty success
        denied = client.post(PREFIX + "/submissions/" + submission["id"] + "/consent",
            json={"sha256": submission["sha256"], "public": True}, headers={key: val for key, val in headers.items() if key != "Content-Type"})
        assert denied.status_code == 400
        incomplete = evidence_fixture(raw, policy)
        incomplete["checks"].pop()
        store.review(submission["id"], signed(incomplete, keys["review"]), policy, root["signed"], now=NOW)
        assert store.submission(account, submission["id"])["state"] == "held"
        store.review(submission["id"], signed(evidence_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
        consent = store.consent(account, submission["id"], submission["sha256"], public=True)
        assert consent["state"] == "ready_for_github"
        exported, _ = store.export_submission(submission["id"], policy, root["signed"], now=NOW)
        assert exported == raw
        catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
        store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
        listing = client.get(PREFIX + "/catalog").json()["entries"]
        assert listing[0]["reviewed"] and listing[0]["stars"] == 0
        for _ in range(2):
            response = client.put(PREFIX + "/stars", json={"id": "alice/echo", "version": "1.0.0", "starred": True},
                headers={"Origin": "https://registry.test", "X-Vool-CSRF": csrf})
            assert response.status_code == 200 and response.json()["stars"] == 1
        ticket = client.get(PREFIX + "/download", params={"id": "alice/echo", "version": "1.0.0"}).json()
        for _ in range(2):
            assert client.get(ticket["url"]).content == raw
        assert store.stats("alice/echo") == {"stars": 1, "download_requests": 1}
        client.cookies.clear()
        assert client.get(PREFIX + "/submissions").status_code == 400
        assert client.get(PREFIX + "/submissions/" + submission["id"]).status_code == 400
    catalog.close()


def test_secret_failure_is_private_redacted_and_not_retained(tmp_path):
    _, source, info, _, _, _, store, catalog, _, account, _, _ = setup_service(tmp_path)
    secret = "ghp_" + "A" * 36
    (source / "notes.txt").write_text(secret)
    raw = pack(source, info, tmp_path / "secret-scratch")
    result = store.upload(account, raw, now=NOW)
    assert result["state"] == "rejected" and "credential" in result["findings"][0]["reason"]
    assert secret not in canonical(result).decode()
    with store.connection() as db:
        assert db.execute("SELECT artifact FROM submissions WHERE id=?", (result["id"],)).fetchone()[0] is None
    catalog.close()


def test_cross_origin_csrf_namespace_spoofing_and_identity_linking_fail_closed(tmp_path):
    raw, _, _, _, _, _, store, catalog, app, account, token, csrf = setup_service(tmp_path)
    other = store.identity("google", "other-subject", now=NOW)
    with pytest.raises(RegistryError, match="another account"):
        store.identity("github", "123", now=NOW, link_account=other)
    with pytest.raises(RegistryError, match="reserved"):
        store.profile(other, {"namespace": "vool", "bio": "", "handles": {}})
    with TestClient(app, base_url="https://registry.test") as client:
        client.cookies.set("vool_registry", token)
        for origin, anti_csrf in (("https://evil.test", csrf), ("https://registry.test", "wrong")):
            response = client.post(PREFIX + "/submissions", content=raw, headers={"Origin": origin,
                "X-Vool-CSRF": anti_csrf, "Content-Type": "application/vnd.vool.registry+json"})
            assert response.status_code == 400
    catalog.close()


def test_revoked_and_expired_admissions_disappear_from_browse_and_download(tmp_path):
    raw, _, _, policy, keys, _, store, catalog, app, _, _, _ = setup_service(tmp_path)
    catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    ticket = store.ticket(hashlib.sha256(raw).hexdigest(), now=NOW)
    catalog.accept(catalog_fixture(raw, policy, keys, sequence=2, revoked=[hashlib.sha256(raw).hexdigest()]), now=NOW)
    with TestClient(app, base_url="https://registry.test") as client:
        assert client.get(PREFIX + "/catalog").json()["entries"] == []
        assert client.get(PREFIX + "/artifacts/" + hashlib.sha256(raw).hexdigest(), params={"ticket": ticket}).status_code == 400
    catalog.close()


def test_stale_review_cannot_be_exported_even_after_consent(tmp_path):
    raw, _, _, policy, keys, root, store, catalog, _, account, _, _ = setup_service(tmp_path)
    submission = store.upload(account, raw, now=NOW)
    store.review(submission["id"], signed(evidence_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
    store.consent(account, submission["id"], submission["sha256"], public=True)
    with pytest.raises(RegistryError, match="stale"):
        store.export_submission(submission["id"], policy, root["signed"], now=NOW + 3601)
    catalog.close()


def test_login_state_is_browser_bound_consumed_and_token_not_stored(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    calls = []

    def exchange(url, data=None, *, token=""):
        calls.append(url)
        if url.endswith("access_token"):
            assert data["code_verifier"]
            return {"access_token": "example-provider-token", "scope": ""}
        assert token == "example-provider-token"
        return {"id": 42, "email": "do-not-retain@example.test"}

    auth = Auth(store, "https://registry.test", [Provider("github", "test-client", "test-secret")], request=exchange)
    url, browser = auth.begin("github", now=NOW)
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(url).query)
    assert "scope" not in params and params["code_challenge_method"] == ["S256"]
    state = params["state"][0]
    with pytest.raises(RegistryError, match="mismatched"):
        auth.finish("github", state, "code", "another-browser", now=NOW)
    token, csrf = auth.finish("github", state, "code", browser, now=NOW)
    assert store.authenticate(token, csrf=csrf, now=NOW)
    with pytest.raises(RegistryError, match="mismatched"):
        auth.finish("github", state, "code", browser, now=NOW)
    disk = store.path.read_bytes()
    assert b"example-provider-token" not in disk and b"do-not-retain@example.test" not in disk
    assert len(calls) == 2
