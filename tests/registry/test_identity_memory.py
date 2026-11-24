"""Deleting an account and signing in again is the same person: suspension, mute and daily limits come back with them.

What deletion leaves is one small row per sign-in, keyed by a hash only this service can make: no name, no
provider id, no text. Real service handlers; the signer and the review answers are the scripted test ones."""
import pytest
from starlette.testclient import TestClient

from core.registry.codec import RegistryError
from registry_service import community, reports
from registry_service.api import PREFIX
from registry_service.store import DAILY_SUBMISSIONS, Store
from tests.registry.helpers import NOW, rated
from tests.registry.test_skill_codex import METADATA, SKILL, OwnerOnlyGitHub, account, mutation, service
from tests.registry.test_skill_codex_authority import DAY, ORIGIN, creator, draft
from tests.registry.test_skill_codex_authority import world as world

OLD = NOW - 60 * DAY
WAIT = "an account with this sign-in was deleted; you can choose a public name from "


def delete(client, token, csrf):
    """The website's own "delete my account"."""
    client.cookies.set("vool_registry", token)
    response = client.request("DELETE", PREFIX + "/session", headers=mutation(csrf))
    client.cookies.clear()
    assert response.status_code == 200 and response.json() == {"status": "deleted"}, response.text


def choose_name(client, token, csrf, namespace):
    client.cookies.set("vool_registry", token)
    response = client.put(PREFIX + "/profile", json={"namespace": namespace, "bio": "", "handles": {}}, headers=mutation(csrf))
    client.cookies.clear()
    return response


def returns(world, subject):
    """The same GitHub account signs in again, as the sign-in callback does."""
    who = world.store.identity("github", subject, now=world.clock.t, since=OLD)
    return (who, *world.store.session(who, now=world.clock.t))


def test_a_suspended_creator_who_deletes_and_signs_in_again_is_still_suspended(world):
    alice, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    world.mac.run_once()
    world.mac.act("suspend", "alice")
    assert draft(world, token, csrf, "alice/other").json() == {"error": "this creator can't publish right now"}
    delete(world.http, token, csrf)

    again, token, csrf = returns(world, "101")
    assert again != alice
    # No new name for 30 days, so leaving and coming back is never a quick way to a clean one.
    refused = choose_name(world.http, token, csrf, "alice-two")
    assert refused.status_code == 400 and refused.json()["error"].startswith(WAIT)
    world.clock.t += 30 * DAY - 60
    token, csrf = world.store.session(again, now=world.clock.t)
    assert choose_name(world.http, token, csrf, "alice-two").json()["error"].startswith(WAIT)
    world.clock.t += 61
    token, csrf = world.store.session(again, now=world.clock.t)
    assert choose_name(world.http, token, csrf, "alice-two").json()["namespace"] == "alice-two"
    # The new name is not on the suspended list; the person still is.
    assert world.store.marks()["suspended"] == ["alice"]
    assert draft(world, token, csrf, "alice-two/notes").json() == {"error": "this creator can't publish right now"}

    # Leaving a second time does not wash it off either.
    delete(world.http, token, csrf)
    third, token, csrf = returns(world, "101")
    world.clock.t += 30 * DAY + 1
    token, csrf = world.store.session(third, now=world.clock.t)
    assert choose_name(world.http, token, csrf, "alice-three").json()["namespace"] == "alice-three"
    assert draft(world, token, csrf, "alice-three/notes").json() == {"error": "this creator can't publish right now"}

    # Someone who never left, and someone signing in for the first time, are untouched.
    _, bob_token, bob_csrf = creator(world, "102", "bob")
    assert draft(world, bob_token, bob_csrf, "bob/notes").json()["state"] == "checking"

    # The team lifts it the way it gave it: by the old name, whatever the person is called now.
    world.mac.act("unsuspend", "alice")
    assert draft(world, token, csrf, "alice-three/notes").json()["state"] == "checking"


def test_suspending_the_old_name_after_the_account_was_deleted_still_reaches_the_person(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    world.mac.run_once()
    delete(world.http, token, csrf)
    _, token, csrf = creator(world, "104", None)  # someone who never chose a public name leaves too
    delete(world.http, token, csrf)
    world.mac.act("suspend", "alice")  # the team only notices after alice has gone

    world.clock.t += 30 * DAY + 1
    world.store.prune(now=world.clock.t)  # the clean-up keeps what a suspended name still needs
    again, token, csrf = returns(world, "101")
    assert choose_name(world.http, token, csrf, "alice-two").json()["namespace"] == "alice-two"
    assert draft(world, token, csrf, "alice-two/notes").json() == {"error": "this creator can't publish right now"}
    # The person who had no name held nothing a suspension could follow.
    _, other_token, other_csrf = returns(world, "104")
    assert choose_name(world.http, other_token, other_csrf, "newcomer").json()["namespace"] == "newcomer"
    assert draft(world, other_token, other_csrf, "newcomer/notes").json()["state"] == "checking"
    with world.store.connection() as db:
        assert db.execute("SELECT count(*) FROM retired_identities").fetchone()[0] == 1  # only alice's is kept

    world.mac.act("unsuspend", "alice")
    assert draft(world, token, csrf, "alice-two/notes").json()["state"] == "checking"


def test_a_suspension_given_after_the_row_was_forgotten_no_longer_finds_the_person(world):
    _, token, csrf = creator(world, "102", "bob")
    delete(world.http, token, csrf)
    world.clock.t += 30 * DAY + 1
    world.store.prune(now=world.clock.t)  # 30 days on, nothing held bob's row: it is forgotten
    with world.store.connection() as db:
        assert db.execute("SELECT count(*) FROM retired_identities").fetchone()[0] == 0
    world.mac.act("suspend", "bob")
    _, token, csrf = returns(world, "102")
    assert choose_name(world.http, token, csrf, "bob-two").json()["namespace"] == "bob-two"
    assert draft(world, token, csrf, "bob-two/notes").json()["state"] == "checking"  # the stated limit of the memory


def test_an_upload_waiting_when_its_returned_creator_is_known_again_is_refused_not_signed(world):
    alice, token, csrf = creator(world, "101", "alice")
    world.mac.act("suspend", "alice")
    delete(world.http, token, csrf)
    # The returning sign-in is linked to an account that already has a name, so nothing asks for a new one.
    carol, token, csrf = creator(world, "103", "carol")
    assert draft(world, token, csrf, "carol/notes").json()["state"] == "checking"
    world.store.identity("github", "101", now=world.clock.t, link_account=carol, since=OLD)
    report = world.mac.run_once()
    assert report["admitted"] == [] and world.model.calls == []
    world.http.cookies.set("vool_registry", token)
    listed = world.http.get(PREFIX + "/submissions").json()["submissions"]
    assert [(item["state"], item["findings"][0]["reason"]) for item in listed] == [
        ("refused", "This creator can't publish right now.")]
    world.http.cookies.clear()
    assert draft(world, token, csrf, "carol/other").json() == {"error": "this creator can't publish right now"}


def test_a_muted_person_who_deletes_and_signs_in_again_is_still_muted(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    world.mac.run_once()
    bob, token, csrf = creator(world, "102", "bob")
    community.mute(world.store, "bob", True, now=world.clock.t)
    delete(world.http, token, csrf)
    again, token, csrf = returns(world, "102")
    with world.store.connection() as db:
        assert db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (again,)).fetchone()
    # Linking the returning sign-in to another account brings the mute to that account too.
    delete(world.http, token, csrf)
    carol, _, _ = creator(world, "103", "carol")
    rated(world.store, carol, "alice/notes", now=world.clock.t)
    assert community.comments(world.store, "alice/notes", carol)["can_comment"] is True
    world.store.identity("github", "102", now=world.clock.t, link_account=carol, since=OLD)
    assert community.comments(world.store, "alice/notes", carol)["can_comment_reason"] == "muted"
    with pytest.raises(RegistryError, match="turned off"):
        community.post_comment(world.store, carol, "alice/notes", "Back again", now=world.clock.t)
    # An operator's unmute ends it, and it does not come back by itself.
    community.mute(world.store, "carol", False, now=world.clock.t)
    assert community.comments(world.store, "alice/notes", carol)["can_comment"] is True


def test_daily_upload_comment_and_report_limits_continue_across_delete_and_sign_in(tmp_path):
    skills = [("alice/notes", {}, NOW - 300)] + [(f"pub{index}/skill", {}, NOW - 300) for index in range(11)]
    store, catalog, app, *_ = service(tmp_path, skills, github=OwnerOnlyGitHub(77))

    def body(index):
        return {"metadata": {**METADATA, "version": f"1.0.{index + 1}"}, "skill_markdown": SKILL,
                "license_text": "Test fixture licence.\n"}

    def signed_in(client):
        """GitHub account 77 signs in and links the publisher name its repositories already publish under."""
        who, token, csrf = account(store, "77", None, created=NOW)
        client.cookies.set("vool_registry", token)
        linked = client.post(PREFIX + "/publisher-link", json={"namespace": "alice"}, headers=mutation(csrf))
        assert linked.status_code == 200, linked.text
        return who, token, csrf

    with TestClient(app, base_url=ORIGIN) as client:
        who, token, csrf = signed_in(client)
        for index in range(DAILY_SUBMISSIONS):
            assert client.post(PREFIX + "/drafts", json=body(index), headers=mutation(csrf)).status_code == 201
        assert client.post(PREFIX + "/drafts", json=body(10), headers=mutation(csrf)).json() == {
            "error": "daily submission quota reached"}
        rated(store, who, "pub0/skill", now=NOW)
        for index in range(community.COMMENTS_PER_DAY):
            community.post_comment(store, who, "pub0/skill", f"Note number {index}", now=NOW)
        for index in range(reports.REPORTS_PER_DAY):
            reports.file_report(store, who, {"id": f"pub{index}/skill", "reason": "broken"}, now=NOW)
        delete(client, token, csrf)

        again, token, csrf = signed_in(client)
        assert again != who
        assert client.post(PREFIX + "/drafts", json=body(11), headers=mutation(csrf)).json() == {
            "error": "daily submission quota reached"}
        rated(store, again, "pub0/skill", now=NOW)
        with pytest.raises(RegistryError, match="daily comment limit reached"):
            community.post_comment(store, again, "pub0/skill", "One more", now=NOW)
        assert reports.status(store, "pub10/skill", again, now=NOW)["can_report_reason"] == "daily_limit"
        with pytest.raises(RegistryError, match="most reports allowed"):
            reports.file_report(store, again, {"id": "pub10/skill", "reason": "broken"}, now=NOW)
        # A day later the limits are open again, as they would be for anyone.
        assert reports.file_report(store, again, {"id": "pub10/skill", "reason": "broken"}, now=NOW + DAY + 1) == {
            "status": "received"}
        assert "id" in community.post_comment(store, again, "pub0/skill", "The next day", now=NOW + DAY + 1)
        assert store.upload(again, _package(tmp_path), now=NOW + DAY + 1)["package_id"] == "alice/notes"
    catalog.close()


def _package(tmp_path):
    from tests.registry.test_skill_codex import package

    return package(tmp_path, "alice/notes", version="2.0.0")


def test_what_deletion_leaves_names_nobody(tmp_path):
    store = Store(tmp_path / "accounts.sqlite")
    who = store.identity("github", "private-provider-subject-4711", now=NOW)
    store.identity("apple", "private-linked-subject-0815", now=NOW, link_account=who)
    store.profile(who, {"namespace": "leaving", "bio": "Private biography", "handles": {"github": "leaving-handle"}})
    store.delete_account(who, now=NOW)
    with store.connection() as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM retired_identities")]
        assert db.execute("SELECT count(*) FROM identities").fetchone()[0] == 0
    assert len(rows) == 2  # one per sign-in, so either one is known again
    assert all(set(row) == {"identity", "deleted_at", "names", "muted", "recent"} for row in rows)
    assert all(len(row["identity"]) == 64 and row["deleted_at"] == NOW and row["muted"] == 0 for row in rows)
    # The public name is kept as a hash of its own kind: not the name, and not one of the sign-in hashes.
    assert {row["names"] for row in rows} == {rows[0]["names"]} and len(rows[0]["names"]) == 64
    assert rows[0]["names"] not in {row["identity"] for row in rows} and "leaving" not in repr(rows)
    kept = repr(rows) + store.path.read_bytes().decode("latin-1")
    for private in ("private-provider-subject-4711", "private-linked-subject-0815", "Private biography",
                    "leaving-handle", who):
        assert private not in kept
    # The hash needs this service's own key: another service's row for the same sign-in differs.
    other = Store(tmp_path / "other" / "accounts.sqlite")
    other.delete_account(other.identity("github", "private-provider-subject-4711", now=NOW), now=NOW)
    with other.connection() as db:
        assert db.execute("SELECT identity FROM retired_identities").fetchone()[0] not in {row["identity"] for row in rows}
    # Once nothing depends on a row any more, the next clean-up drops it.
    store.prune(now=NOW + 30 * DAY + 1)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM retired_identities").fetchone()[0] == 0


def test_a_database_from_before_identity_memory_opens_and_then_remembers(tmp_path):
    store, catalog, *_ = service(tmp_path, [("alice/notes", {}, NOW - 300)])
    path = store.path
    readers = [store.identity("github", str(10 + index), now=NOW - 30 * DAY) for index in range(3)]
    with store.connection() as db:
        # The state an older service wrote: no identity memory, reports tied to accounts alone.
        db.execute("DROP TABLE IF EXISTS retired_identities")
        db.execute("DROP INDEX IF EXISTS skill_reports_package")
        db.execute("DROP INDEX IF EXISTS skill_reports_created")
        if "reporter" in {row[1] for row in db.execute("PRAGMA table_info(skill_reports)")}:
            db.execute("ALTER TABLE skill_reports DROP COLUMN reporter")
        db.execute("DELETE FROM service_keys WHERE name='identity-hmac'")
        for index, who in enumerate(readers):
            db.execute("INSERT INTO skill_reports (id,package_id,account,created,reason,note,counted,state) VALUES (?,?,?,?,?,?,?,?)",
                       (f"{index:032x}", "alice/notes", who, NOW - DAY, "scam", "An old note.", 1, "open"))
        db.execute("INSERT INTO paused_packages VALUES ('alice/notes',?,'reports')", (NOW - DAY,))
    catalog.close()

    migrated = Store(path)
    Store(path)  # opening twice changes nothing more
    migrated.delete_account(readers[0], now=NOW)
    listed = reports.open_reports(migrated, now=NOW)
    assert [(len(item["reports"]), item["paused"]) for item in listed] == [(3, "reports")]
    assert [item.get("account_deleted", False) for item in listed[0]["reports"]] == [True, False, False]
    assert listed[0]["reports"][0]["note"] == "" and listed[0]["counting"] == 3
    again = migrated.identity("github", "10", now=NOW)
    with pytest.raises(RegistryError, match="already reported"):
        reports.file_report(migrated, again, {"id": "alice/notes", "reason": "scam"}, now=NOW)
    with pytest.raises(RegistryError, match="deleted; you can choose a public name from"):
        migrated.profile(again, {"namespace": "back-again", "bio": "", "handles": {}}, now=NOW)
