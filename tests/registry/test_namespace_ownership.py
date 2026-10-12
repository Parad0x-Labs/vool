"""Publisher identity, deletion privacy and legacy-state regression family."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from starlette.testclient import TestClient

from core.registry.artifact import inspect
from core.registry.codec import RegistryError, canonical
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


def _profile(namespace, *, bio="", github=""):
    return {"namespace": namespace, "bio": bio, "handles": {"github": github} if github else {}}


@pytest.fixture
def published_service(tmp_path, request):
    raw, _, _ = artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private" / "accounts.sqlite")
    if getattr(request, "param", None) == "website":
        author = store.identity("github", "publisher-original", now=NOW)
        store.profile(author, _profile("alice", bio="Original author", github="alice"))
        artifact = inspect(raw)
        draft = store.draft(author, {"metadata": {key: val for key, val in artifact["metadata"].items() if key != "source"},
            "skill_markdown": artifact["files"]["skills/echo/SKILL.md"].decode(),
            "license_text": artifact["files"]["LICENSE"].decode()}, now=NOW)
        with store.connection() as db:
            raw = db.execute("SELECT artifact FROM submissions WHERE id=?", (draft["id"],)).fetchone()[0]
        store.review(draft["id"], signed(evidence_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
        store.consent(author, draft["id"], draft["sha256"], public=True)
    catalog = TrustedCatalog(root, tmp_path / "trust" / "state", now=NOW)
    try:
        catalog.accept(catalog_fixture(raw, policy, keys), now=NOW)
        app = create_app(store, Auth(store, "https://registry.test", []), catalog, TARGET, clock=lambda: NOW)
        yield store, catalog, app, raw
    finally:
        catalog.close()


@pytest.mark.parametrize("published_service", ["website"], indirect=True)
def test_deleted_publisher_cannot_be_reclaimed_in_served_catalog(published_service):
    store, catalog, app, raw = published_service
    author = store.identity("github", "publisher-original", now=NOW)
    store.profile(author, _profile("alice", bio="Original author", github="alice"))
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    token, csrf = store.session(author, now=NOW)
    attacker = store.identity("google", "publisher-impostor", now=NOW)
    attacker_token, attacker_csrf = store.session(attacker, now=NOW)
    with TestClient(app, base_url="https://registry.test") as client:
        before = client.get(PREFIX + "/catalog")
        assert before.status_code == 200, before.text
        assert before.json()["entries"][0]["creator"]["bio"] == "Original author"
        client.cookies.set("vool_registry", token)
        deletion = client.delete(PREFIX + "/session", headers={"Origin": "https://registry.test", "X-Vool-CSRF": csrf})
        assert deletion.status_code == 200, deletion.text
        client.cookies.clear()
        client.cookies.set("vool_registry", attacker_token)
        reclaim = client.put(PREFIX + "/profile", json=_profile("alice", bio="Impostor", github="impostor"),
            headers={"Origin": "https://registry.test", "X-Vool-CSRF": attacker_csrf})
        assert reclaim.status_code == 400, reclaim.text
        after = client.get(PREFIX + "/catalog")
        assert after.status_code == 200, after.text
        row = after.json()["entries"][0]
        assert row["id"] == "alice/echo" and row["reviewed"]
        assert row["creator"] == {"namespace": "alice", "bio": "", "handles": {}}
        spoof = client.post(PREFIX + "/submissions", content=raw, headers={"Origin": "https://registry.test",
            "X-Vool-CSRF": attacker_csrf, "Content-Type": "application/vnd.vool.registry+json"})
        assert spoof.status_code == 400, spoof.text
        ticket = client.get(PREFIX + "/download", params={"id": "alice/echo", "version": "1.0.0"})
        assert ticket.status_code == 200, ticket.text
        assert client.get(ticket.json()["url"]).content == raw


def test_same_publisher_updates_and_new_namespace_work_after_refusals(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    owner = store.identity("github", "stable-owner", now=NOW)
    other = store.identity("apple", "other-account", now=NOW)
    store.profile(owner, _profile("steady"))
    with pytest.raises(RegistryError):
        store.profile(other, _profile("steady", bio="Impostor"))
    result = store.profile(owner, _profile("steady", bio="Updated", github="steady"))
    assert result["namespace"] == "steady" and result["bio"] == "Updated"
    assert store.profile(other, _profile("unused"))["namespace"] == "unused"
    with pytest.raises(RegistryError):
        store.profile(owner, _profile("renamed"))
    assert store.public_profile("steady")["bio"] == "Updated"


def test_unpublished_namespace_remains_reserved_without_private_identity(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    owner = store.identity("google", "deleted-private-subject", now=NOW)
    store.profile(owner, _profile("unpublished"))
    store.delete_account(owner)
    attacker = store.identity("apple", "fresh-subject", now=NOW)
    with pytest.raises(RegistryError):
        store.profile(attacker, _profile("unpublished"))
    assert store.profile(attacker, _profile("available"))["namespace"] == "available"


def test_true_concurrent_namespace_claim_has_one_winner(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    accounts = [store.identity("github", "contender-one", now=NOW),
                store.identity("google", "contender-two", now=NOW)]
    start = Barrier(2)

    def claim(account):
        start.wait(timeout=5)
        try:
            store.profile(account, _profile("contended", bio=account))
            return account
        except RegistryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        winners = [result for result in workers.map(claim, accounts) if result is not None]
    assert len(winners) == 1
    loser = next(account for account in accounts if account != winners[0])
    assert store.profile(winners[0])["namespace"] == "contended"
    assert store.profile(loser)["namespace"] is None
    store.delete_account(winners[0])
    with pytest.raises(RegistryError):
        store.profile(loser, _profile("contended"))


def test_legacy_current_account_backfill_is_idempotent_and_preserves_owner(tmp_path):
    path = tmp_path / "accounts.sqlite"
    store = Store(path)
    owner = store.identity("github", "legacy-owner", now=NOW)
    store.profile(owner, _profile("legacy"))
    # Historical schema had no independent reservation table.
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS namespace_reservations")
    migrated = Store(path)
    Store(path)
    assert migrated.profile(owner, _profile("legacy", bio="Still mine"))["bio"] == "Still mine"
    migrated.delete_account(owner)
    attacker = migrated.identity("google", "new-after-migration", now=NOW)
    with pytest.raises(RegistryError):
        migrated.profile(attacker, _profile("legacy"))


def test_legacy_orphan_publication_reserves_deleted_author_namespace(published_service):
    store, catalog, _, raw = published_service
    owner = store.identity("github", "old-published-owner", now=NOW)
    store.profile(owner, _profile("alice"))
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    store.delete_account(owner)
    with store.connection() as db:
        db.execute("DROP TABLE IF EXISTS namespace_reservations")
    migrated = Store(store.path)
    attacker = migrated.identity("google", "post-migration-attacker", now=NOW)
    with pytest.raises(RegistryError):
        migrated.profile(attacker, _profile("alice"))
    assert migrated.public_artifact(catalog.resolve("alice/echo", "1.0.0", target=TARGET, now=NOW)["artifact_sha256"])[0] == raw
    assert migrated.public_profile("alice") == {"namespace": "alice", "bio": "", "handles": {}}


def test_direct_signed_publication_reserves_namespace_without_website_account(published_service):
    store, catalog, _, raw = published_service
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    attacker = store.identity("apple", "direct-publisher-impostor", now=NOW)
    with pytest.raises(RegistryError):
        store.profile(attacker, _profile("alice", bio="Not the publisher"))
    # A refusal does not prevent legitimate unrelated author onboarding.
    assert store.profile(attacker, _profile("newcomer"))["namespace"] == "newcomer"
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    assert store.stats("alice/echo") == {"stars": 0, "download_requests": 0}


def test_deletion_purges_private_rows_and_retains_only_public_name_reservation(published_service):
    store, catalog, _, raw = published_service
    owner = store.identity("github", "private-original-provider-subject", now=NOW)
    store.identity("apple", "private-linked-provider-subject", now=NOW, link_account=owner)
    store.profile(owner, _profile("alice", bio="Private account biography"))
    token, _ = store.session(owner, now=NOW)
    submission = store.upload(owner, raw, now=NOW)
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    store.star(owner, "alice/echo", True, now=NOW)
    linking = Auth(store, "https://registry.test", [Provider("google", "fixture-client", "fixture-secret")])
    linking.begin("google", now=NOW, link_account=owner)
    store.delete_account(owner)
    with pytest.raises(RegistryError):
        store.authenticate(token, now=NOW)
    with pytest.raises(RegistryError):
        store.submission(owner, submission["id"])
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM accounts WHERE id=?", (owner,)).fetchone()[0] == 0
        for table in ("identities", "sessions", "submissions", "stars", "namespace_owners"):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE account=?", (owner,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM oauth WHERE link_account=?", (owner,)).fetchone()[0] == 0
        reservations = [dict(row) for row in db.execute("SELECT * FROM namespace_reservations")]
        assert {tuple(row) for row in reservations} == {("namespace",)}
        assert {row["namespace"] for row in reservations} == {"alice"}
        assert db.execute("SELECT count(*) FROM public_versions").fetchone()[0] == 1
    assert store.stats("alice/echo")["stars"] == 0
    assert store.public_profile("alice") == {"namespace": "alice", "bio": "", "handles": {}}


@pytest.mark.parametrize("entry", [{}, {"metadata": {"id": "wrong/echo", "version": "1.0.0"}}])
def test_ambiguous_legacy_publication_refuses_migration(published_service, entry):
    store, catalog, _, raw = published_service
    store.publish(catalog, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
    with store.connection() as db:
        db.execute("DROP TABLE namespace_reservations")
        db.execute("UPDATE public_versions SET entry=?", (canonical(entry),))
    with pytest.raises(RegistryError, match="migration"):
        Store(store.path)


def test_concurrent_same_account_cannot_replace_its_first_namespace(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    account = store.identity("github", "same-account", now=NOW)
    start = Barrier(2)

    def claim(namespace):
        start.wait(timeout=5)
        try:
            return store.profile(account, _profile(namespace))["namespace"]
        except RegistryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        winners = [name for name in workers.map(claim, ("first", "second")) if name]
    assert len(winners) == 1
    assert store.profile(account)["namespace"] == winners[0]
