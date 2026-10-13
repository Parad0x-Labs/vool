"""Skill Codex server pieces: comments, moderation, curation labels, search, profiles, updates, GitHub import."""
import json
import subprocess
import urllib.parse
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from core.registry.admission import admit
from core.registry.artifact import inspect, native_identity, pack
from core.registry.codec import RegistryError, digest
from core.registry.trust import TrustedCatalog
from registry_service import community
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth, Provider
from registry_service.curation import load_curation
from registry_service.moderation import review
from registry_service.store import Store
from tests.registry.helpers import (
    NOW,
    TARGET,
    evidence_fixture,
    prepublication_fixture,
    signed,
    trust_fixture,
)

ORIGIN = "https://registry.test"
DAY = 86400


def package(tmp_path, package_id, *, version="1.0.0", category="productivity", subcategory="meetings",
            summary="Summarize meeting notes into decisions", title=None):
    name = package_id.split("/")[1]
    root = tmp_path / ("src-" + package_id.replace("/", "-") + "-" + version)
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": native_identity(package_id), "version": version, "skills": "./skills/", "requires": []}))
    (root / "skills" / name).mkdir(parents=True)
    (root / "skills" / name / "SKILL.md").write_text(
        "---\nname: " + name + "\ndescription: " + summary + "\n---\nUse only the text the user provides.\n")
    (root / "LICENSE").write_text("Test fixture licence.\n")
    info = {"id": package_id, "kind": "skill", "version": version, "summary": summary, "category": category,
            "subcategory": subcategory, "license": "MIT",
            "source": {"repository": package_id.split("/")[0] + "/skills", "commit": "b" * 40, "path": name}}
    if title:
        info["title"] = title
    return pack(root, info, tmp_path / "scratch")


def catalog_of(packages, policy, keys):
    entries = []
    for raw, issued in packages:
        entry = admit(inspect(raw), policy, evidence_fixture(raw, policy), now=NOW)
        entry.update(schema="vool.registry.admission.v1", issued_at=issued, expires=NOW + 7200)
        entries.append(signed(entry, keys["admission"]))
    authorization = {"schema": "vool.registry.catalog_authorization.v1", "root_version": 1, "epoch": 1,
                     "issued_at": NOW, "expires": NOW + 7200, "entries_sha256": digest(entries),
                     "revoked_sha256": digest([])}
    value = {"schema": "vool.registry.catalog.v1", "root_version": 1, "sequence": 1, "issued_at": NOW,
             "expires": NOW + 3600, "entries": entries, "revoked": [],
             "authorization": signed(authorization, keys["admission"])}
    return signed(value, keys["catalog"])


def account(store, subject, namespace, *, created=NOW - 3 * DAY, provider="github"):
    who = store.identity(provider, subject, now=created)
    if namespace:
        store.profile(who, {"namespace": namespace, "bio": "", "handles": {}})
    token, csrf = store.session(who, now=NOW)
    return who, token, csrf


def service(tmp_path, packages, *, curation=None, github=None):
    policy, keys, root = trust_fixture()
    store = Store(tmp_path / "private" / "accounts.sqlite")
    catalog = TrustedCatalog(root, tmp_path / "trust" / "state", now=NOW)
    built = [(package(tmp_path, *spec[:1], **spec[1]), spec[2]) for spec in packages]
    if built:
        catalog.accept(catalog_of(built, policy, keys), now=NOW)
        for raw, _ in built:
            meta = inspect(raw)["metadata"]
            store.publish(catalog, meta["id"], meta["version"], TARGET, raw, now=NOW)
    app = create_app(store, Auth(store, ORIGIN, []), catalog, TARGET, clock=lambda: NOW,
                     curation=curation, github=github)
    return store, catalog, app, policy, keys, root


def mutation(csrf):
    return {"Origin": ORIGIN, "X-Vool-CSRF": csrf}


# -- moderation --------------------------------------------------------------------------------


@pytest.mark.parametrize("text", [
    "This is fucking great", "what a pile of sh1t", "f u c k this", "f.u.c.k.i.n.g broken", "total b1tch move",
    "visit example.com for more", "see https://example.org", "www.deals.example", "ping @promo_bot",
    "mail me at a@b.co", "call +1 415 555 0100 now", "send to 0x52908400098527886E0F7030069857D2E4169EE7",
    "share your seed phrase to unlock", "connect your wallet to claim", "free sol airdrop today",
    "DM me for a better skill", "use code SAVE20 at checkout", "hot nudes inside", "porn skill",
    "buy cocaine here", "selling cvv and fullz", "join my telegram group",
    "fu​ck this",
])
def test_filter_refuses_cursing_links_contacts_scams_promos_sexual_and_illegal_text(text):
    assert review(text)[0] == "refused"


@pytest.mark.parametrize("text", [
    "Great for meeting notes, saved me an hour.", "It assesses the class schedule well.",
    "Works for Scunthorpe council minutes too.", "Version 2 fixed the method names.",
    "Missing a step: it skips unresolved questions sometimes.", "Je l'utilise pour mes notes, très bien.",
])
def test_filter_publishes_ordinary_feedback(text):
    assert review(text) == ("published", "")


@pytest.mark.parametrize("text", ["Хороший навык для заметок",
                                  "THIS SKILL IS THE BEST THING EVER MADE", "soooooooo good"])
def test_filter_holds_text_the_lists_cannot_judge(text):
    assert review(text)[0] == "held"


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001, "bell\x07", None, "a\n\n\n\nb"])
def test_filter_refuses_empty_oversized_and_control_text(text):
    assert review(text)[0] == "refused"


# -- comments ----------------------------------------------------------------------------------


def test_only_signed_in_downloaders_comment_and_held_text_stays_private(tmp_path):
    store, catalog, app, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    bob, bob_token, bob_csrf = account(store, "2", "bob")
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json() == {"comments": [], "can_comment": False}
        assert client.post(PREFIX + "/comments", json={"id": "alice/notes", "body": "Nice"},
                           headers=mutation(bob_csrf)).status_code == 400  # no session cookie
        client.cookies.set("vool_registry", bob_token)
        refused = client.post(PREFIX + "/comments", json={"id": "alice/notes", "body": "Nice"}, headers=mutation(bob_csrf))
        assert refused.status_code == 400 and "download this skill" in refused.json()["error"]
        assert client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json()["can_comment"] is False
        # An anonymous download does not count; a signed-in website download does.
        client.cookies.clear()
        ticket = client.get(PREFIX + "/download", params={"id": "alice/notes", "version": "1.0.0"}).json()
        client.get(ticket["url"])
        client.cookies.set("vool_registry", bob_token)
        assert client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json()["can_comment"] is False
        ticket = client.get(PREFIX + "/download", params={"id": "alice/notes", "version": "1.0.0"}).json()
        assert client.get(ticket["url"]).status_code == 200
        assert client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json()["can_comment"] is True
        posted = client.post(PREFIX + "/comments", json={"id": "alice/notes", "body": "Saved me an hour."},
                             headers=mutation(bob_csrf))
        assert posted.status_code == 201 and posted.json()["state"] == "published"
        blocked = client.post(PREFIX + "/comments", json={"id": "alice/notes", "body": "free sol airdrop, dm me"},
                              headers=mutation(bob_csrf))
        assert blocked.status_code == 400 and blocked.json()["state"] == "refused"
        held = client.post(PREFIX + "/comments", json={"id": "alice/notes", "body": "BEST SKILL EVER MADE FOR NOTES"},
                           headers=mutation(bob_csrf)).json()
        assert held["state"] == "held"
        mine = client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json()["mine"]
        assert {item["state"] for item in mine} == {"published", "held"}
        client.cookies.clear()
        public = client.get(PREFIX + "/comments", params={"id": "alice/notes"}).json()
        assert [item["body"] for item in public["comments"]] == ["Saved me an hour."]
        assert public["comments"][0]["author"] == "bob" and "mine" not in public
    with store.connection() as db:
        stored = [row[0] for row in db.execute("SELECT body FROM comments WHERE state='refused'")]
    assert stored == [""]  # refused text is never kept
    catalog.close()


def test_new_accounts_limits_reports_mutes_and_operator_actions(tmp_path):
    store, catalog, app, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    fresh, _, _ = account(store, "3", "fresh", created=NOW - 60)
    nameless, _, _ = account(store, "4", None)
    bob, _, _ = account(store, "2", "bob")
    for who in (fresh, nameless, bob):
        community.record_download(store, who, "alice/notes", now=NOW)
    assert community.post_comment(store, fresh, "alice/notes", "Helpful", now=NOW)["state"] == "held"
    with pytest.raises(RegistryError, match="public name"):
        community.post_comment(store, nameless, "alice/notes", "Helpful", now=NOW)
    with pytest.raises(RegistryError, match="not published"):
        community.post_comment(store, bob, "alice/missing", "Helpful", now=NOW)
    first = community.post_comment(store, bob, "alice/notes", "Clear and short", now=NOW)
    for text in ("porn", "shit", "fuck"):
        assert community.post_comment(store, bob, "alice/notes", text, now=NOW)["state"] == "refused"
    community.post_comment(store, bob, "alice/notes", "One more note", now=NOW)
    with pytest.raises(RegistryError, match="daily comment limit"):
        community.post_comment(store, bob, "alice/notes", "Sixth", now=NOW)
    assert community.post_comment(store, bob, "alice/notes", "Next day", now=NOW + DAY + 1)["state"] == "published"
    with pytest.raises(RegistryError, match="own comment"):
        community.report_comment(store, bob, first["id"], now=NOW)
    reporters = [account(store, str(10 + index), f"reader{index}")[0] for index in range(3)]
    community.report_comment(store, reporters[0], first["id"], now=NOW)  # one report per account
    for index, who in enumerate(reporters):
        community.report_comment(store, who, first["id"], now=NOW)
        state = "held" if index == 2 else "published"
        with store.connection() as db:
            assert db.execute("SELECT state FROM comments WHERE id=?", (first["id"],)).fetchone()[0] == state
    assert first["id"] in {item["id"] for item in community.held(store)}
    community.moderate(store, first["id"], "publish")
    assert first["id"] in {item["id"] for item in community.comments(store, "alice/notes")["comments"]}
    community.moderate(store, first["id"], "remove")
    with store.connection() as db:
        assert tuple(db.execute("SELECT state, body FROM comments WHERE id=?", (first["id"],)).fetchone()) == ("removed", "")
    community.mute(store, "bob", True, now=NOW)
    assert community.comments(store, "alice/notes", bob)["can_comment"] is False
    assert all(item["author"] != "bob" for item in community.comments(store, "alice/notes")["comments"])
    with pytest.raises(RegistryError, match="turned off"):
        community.post_comment(store, bob, "alice/notes", "Back again", now=NOW + 3 * DAY)
    store.delete_account(bob)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM comments WHERE account=?", (bob,)).fetchone()[0] == 0
    catalog.close()


# -- curation, search, sort, profiles ----------------------------------------------------------


def test_curation_labels_search_filters_sorts_and_profiles(tmp_path):
    curation_file = tmp_path / "curation.json"
    curation_file.write_text(json.dumps({
        "schema": "vool.registry.curation.v1", "official_namespaces": ["voolteam"],
        "featured": ["alice/notes"], "editors_picks": ["voolteam/budget"],
        "reviewers": {"carol": "community", "voolteam": "team"},
        "reviews": [{"id": "alice/notes", "version": "1.0.0", "reviewer": "carol", "date": "2026-10-13"},
                    {"id": "alice/notes", "version": "0.9.0", "reviewer": "voolteam", "date": "2026-10-01"}]}))
    curation = load_curation(curation_file)
    store, catalog, app, *_ = service(tmp_path, [
        ("alice/notes", {}, NOW - 300),
        ("voolteam/budget", {"category": "money-crypto", "subcategory": "budgeting",
                             "summary": "Plan a monthly budget from your own numbers"}, NOW - 100),
        ("bob/translate", {"category": "writing", "subcategory": "translation",
                           "summary": "Translate a short message into another language"}, NOW - 200),
    ], curation=curation)
    voter, token, csrf = account(store, "2", "voter")
    store.star(voter, "bob/translate", True, now=NOW)
    with TestClient(app, base_url=ORIGIN) as client:
        rows = {row["id"]: row for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert rows["voolteam/budget"]["origin"] == "official" and rows["alice/notes"]["origin"] == "community"
        assert rows["alice/notes"]["featured"] and not rows["bob/translate"]["featured"]
        assert rows["voolteam/budget"]["editors_pick"]
        assert rows["alice/notes"]["community_reviews"] == [
            {"reviewer": "carol", "role": "community", "version": "1.0.0", "date": "2026-10-13"}]  # 0.9.0 review not shown
        def ids(**params):
            return [row["id"] for row in client.get(PREFIX + "/catalog", params=params).json()["entries"]]
        assert ids() == ["bob/translate", "alice/notes", "voolteam/budget"]  # stars, then id
        assert ids(q="summarize meetings") == ["alice/notes"]  # job words, not the name
        assert ids(q="Money budgeting") == ["voolteam/budget"]  # category label and subcategory
        assert ids(category="writing") == ["bob/translate"]
        assert ids(origin="official") == ["voolteam/budget"]
        assert ids(sort="newest") == ["voolteam/budget", "bob/translate", "alice/notes"]
        for bad in ({"sort": "random"}, {"q": "x" * 101}, {"admin": "1"}):
            assert client.get(PREFIX + "/catalog", params=bad).status_code == 400
        page = client.get(PREFIX + "/profiles/voolteam").json()
        assert page["origin"] == "official" and [row["id"] for row in page["packages"]] == ["voolteam/budget"]
        assert client.get(PREFIX + "/profiles/nobody").json()["packages"] == []
    catalog.close()


@pytest.mark.parametrize("change, message", [
    ({"official_namespaces": ["Bad Name"]}, "official"),
    ({"featured": ["alice/notes", "alice/notes"]}, "featured"),
    ({"reviewers": {"carol": "owner"}}, "reviewers"),
    ({"reviews": [{"id": "carol/x", "version": "1.0.0", "reviewer": "carol", "date": "2026-10-13"}]}, "own skills"),
    ({"reviews": [{"id": "alice/x", "version": "1.0.0", "reviewer": "dave", "date": "2026-10-13"}]}, "review entry"),
    ({"extra": True}, "curation format"),
])
def test_invalid_curation_refuses_to_load(tmp_path, change, message):
    value = {"schema": "vool.registry.curation.v1", "official_namespaces": [], "featured": [], "editors_picks": [],
             "reviewers": {"carol": "community"}, "reviews": [], **change}
    path = tmp_path / "curation.json"
    path.write_text(json.dumps(value))
    with pytest.raises(RegistryError, match=message):
        load_curation(path)


def test_shipped_curation_file_loads():
    assert load_curation()["schema"] == "vool.registry.curation.v1"


def test_updates_keep_the_stable_id_and_refuse_reused_or_lower_versions(tmp_path):
    store, catalog, _, policy, keys, root = service(tmp_path, [])
    alice, _, _ = account(store, "1", "alice")
    first = store.draft(alice, {"metadata": dict(METADATA), "skill_markdown": SKILL,
                                "license_text": "Test fixture licence.\n"}, now=NOW)
    with store.connection() as db:
        raw = db.execute("SELECT artifact FROM submissions WHERE id=?", (first["id"],)).fetchone()[0]
    store.review(first["id"], signed(prepublication_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
    store.consent(alice, first["id"], first["sha256"], public=True)
    catalog.accept(catalog_of([(raw, NOW - 100)], policy, keys), now=NOW)
    store.publish(catalog, "alice/notes", "1.0.0", TARGET, raw, now=NOW)
    voter, _, _ = account(store, "2", "voter")
    store.star(voter, "alice/notes", True, now=NOW)
    for version in ("1.0.0", "0.9.0"):
        with pytest.raises(RegistryError, match="already published"):
            store.draft(alice, {"metadata": {**METADATA, "version": version}, "skill_markdown": SKILL,
                                "license_text": "Test fixture licence.\n"}, now=NOW)
    update = store.draft(alice, {"metadata": {**METADATA, "version": "1.0.1"}, "skill_markdown": SKILL + "Be brief.\n",
                                 "license_text": "Test fixture licence.\n"}, now=NOW)
    assert update["state"] == "quarantined" and update["package_id"] == "alice/notes"
    assert store.stats("alice/notes")["stars"] == 1  # stars stay with the stable id
    catalog.close()


# -- GitHub import -----------------------------------------------------------------------------


class LocalGitHub:
    """A real git repository behind the three GitHub reads the importer makes."""

    def __init__(self, repo: Path, owner_id: int, *, private=False):
        self.repo, self.owner_id, self.private, self.calls = repo, owner_id, private, []

    def repository(self, repository):
        self.calls.append(("repository", repository))
        return {"owner_id": self.owner_id, "private": self.private}

    def commit(self, repository, ref):
        self.calls.append(("commit", ref))
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", ref + "^{commit}"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def archive(self, repository, commit):
        self.calls.append(("archive", commit))
        return subprocess.run(["git", "-C", str(self.repo), "archive", "--format=tar.gz",
                               "--prefix=alice-skills-" + commit[:7] + "/", commit], check=True, capture_output=True).stdout


def git_repo(tmp_path, files, *, links=()):
    repo = tmp_path / "skills-repo"
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    for name, target in links:
        (repo / name).symlink_to(target)
    identity = ["-c", "user.name=sls_0x", "-c", "user.email=240776969+Parad0x-Labs@users.noreply.github.com"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *identity, "commit", "-q", "-m", "skill"], check=True)
    return repo


SKILL = "---\nname: notes\ndescription: Summarize meeting notes into decisions\n---\nUse only the notes the user provides.\n"
METADATA = {"id": "alice/notes", "kind": "skill", "version": "1.0.0", "summary": "Summarize meeting notes",
            "category": "productivity", "subcategory": "meetings", "license": "MIT"}


def import_body(**change):
    return {"repository": "alice/skills", "ref": "main", "path": "skills/notes", "metadata": dict(METADATA),
            "publish_when_checks_pass": False, **change}


def test_github_import_pins_the_commit_binds_the_owner_and_can_publish_when_checks_pass(tmp_path):
    repo = git_repo(tmp_path, {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n",
                               "skills/notes/references/example.md": "Example notes.\n", "README.md": "Not packaged.\n"})
    github = LocalGitHub(repo, 1)
    store, catalog, app, policy, keys, root = service(tmp_path, [], github=github)
    alice, token, csrf = account(store, "1", "alice")
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        response = client.post(PREFIX + "/imports", json=import_body(publish_when_checks_pass=True), headers=mutation(csrf))
        assert response.status_code == 201, response.text
        result = response.json()
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    assert result["state"] == "quarantined"
    assert result["source"] == {"repository": "alice/skills", "commit": commit, "path": "skills/notes"}
    with store.connection() as db:
        row = db.execute("SELECT artifact, publisher_bound, auto_consent FROM submissions WHERE id=?", (result["id"],)).fetchone()
    artifact = inspect(row[0])
    assert row[1] == 1 and row[2] == 1
    assert artifact["metadata"]["source"]["commit"] == commit
    assert set(artifact["files"]) >= {"skills/notes/SKILL.md", "skills/notes/references/example.md", "LICENSE"}
    assert not any(name.endswith("README.md") for name in artifact["files"])
    # Checks passing moves a pre-consented import straight to publishing.
    store.review(result["id"], signed(prepublication_fixture(row[0], policy), keys["review"]), policy, root["signed"], now=NOW)
    assert store.submission(alice, result["id"])["state"] == "ready_for_github"
    catalog.close()


def test_github_import_without_pre_consent_waits_for_the_consent_click(tmp_path):
    repo = git_repo(tmp_path, {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n"})
    store, catalog, _, policy, keys, root = service(tmp_path, [])
    alice, _, _ = account(store, "1", "alice")
    result = store.github_import(alice, import_body(), LocalGitHub(repo, 1), now=NOW)
    with store.connection() as db:
        raw = db.execute("SELECT artifact FROM submissions WHERE id=?", (result["id"],)).fetchone()[0]
    store.review(result["id"], signed(prepublication_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
    assert store.submission(alice, result["id"])["state"] == "preflighted"
    catalog.close()


@pytest.mark.parametrize("case, message", [
    ("other-owner", "belong to your GitHub account"),
    ("private", "public repositories"),
    ("no-licence", "SKILL.md and LICENSE"),
    ("symlink", "links and special files"),
    ("google-only", "sign in with GitHub"),
    ("wrong-namespace", "your namespace"),
    ("bad-ref", "invalid branch"),
    ("traversal", "ambiguous package path"),
    ("source-supplied", "assigned by the server"),
])
def test_github_import_refusals(tmp_path, case, message):
    files = {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n"}
    if case == "no-licence":
        files.pop("skills/notes/LICENSE")
    links = [("skills/notes/secret", "/etc/passwd")] if case == "symlink" else []
    repo = git_repo(tmp_path, files, links=links)
    github = LocalGitHub(repo, 99 if case == "other-owner" else 1, private=case == "private")
    store, catalog, *_ = service(tmp_path, [])
    who, _, _ = account(store, "1", "alice", provider="google" if case == "google-only" else "github")
    body = import_body()
    if case == "wrong-namespace":
        body["metadata"]["id"] = "mallory/notes"
    if case == "bad-ref":
        body["ref"] = "../main"
    if case == "traversal":
        body["path"] = "../skills"
    if case == "source-supplied":
        body["metadata"]["source"] = {"repository": "alice/skills", "commit": "a" * 40, "path": "x"}
    with pytest.raises(RegistryError, match=message):
        store.github_import(who, body, github, now=NOW)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 0
    if case in {"google-only", "wrong-namespace", "bad-ref", "traversal", "source-supplied"}:
        assert not any(call[0] == "archive" for call in github.calls)  # refused before any download
    catalog.close()


def test_import_route_refuses_when_not_configured_or_cross_origin(tmp_path):
    store, catalog, app, *_ = service(tmp_path, [])
    _, token, csrf = account(store, "1", "alice")
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        assert "not configured" in client.post(PREFIX + "/imports", json=import_body(), headers=mutation(csrf)).json()["error"]
    _, _, app, *_ = service(tmp_path / "second", [], github=LocalGitHub(tmp_path, 1))
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        response = client.post(PREFIX + "/imports", json=import_body(),
                               headers={"Origin": "https://evil.test", "X-Vool-CSRF": csrf})
        assert response.status_code == 400 and "same-origin" in response.json()["error"]
    catalog.close()


def test_operator_moderation_commands_act_on_the_private_store(tmp_path, capsys):
    from registry_service.cli import main

    store, catalog, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    fresh, _, _ = account(store, "3", "fresh", created=NOW - 60)
    community.record_download(store, fresh, "alice/notes", now=NOW)
    held = community.post_comment(store, fresh, "alice/notes", "Helpful skill", now=NOW)
    config = tmp_path / "service.json"
    config.write_text(json.dumps({"schema": "vool.registry.service.v1", "private_dir": str(tmp_path / "private")}))
    assert main(["comments-held", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == held["id"]
    assert main(["comment-publish", "--config", str(config), "--target", held["id"]]) == 0
    assert [item["body"] for item in community.comments(store, "alice/notes")["comments"]] == ["Helpful skill"]
    assert main(["mute", "--config", str(config), "--target", "fresh"]) == 0
    assert community.comments(store, "alice/notes")["comments"] == []
    capsys.readouterr()
    assert main(["comment-remove", "--config", str(config), "--target", "0" * 32]) == 1
    assert "comment not found" in capsys.readouterr().out
    catalog.close()


def test_github_import_stops_on_an_oversized_archive_before_unpacking_it(tmp_path):
    import io
    import tarfile

    class Zeros(io.RawIOBase):
        def __init__(self, size):
            self.left = size

        def readable(self):
            return True

        def readinto(self, buffer):
            count = min(len(buffer), self.left)
            buffer[:count] = bytes(count)
            self.left -= count
            return count

    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz", compresslevel=1) as bundle:
        member = tarfile.TarInfo("alice-skills-abc/padding.bin")
        member.size = 257 * 1024 * 1024
        bundle.addfile(member, Zeros(member.size))
        for name, text in (("skills/notes/SKILL.md", SKILL), ("skills/notes/LICENSE", "Test fixture licence.\n")):
            item = tarfile.TarInfo("alice-skills-abc/" + name)
            item.size = len(text.encode())
            bundle.addfile(item, io.BytesIO(text.encode()))
    archive = data.getvalue()
    assert len(archive) < 16 * 1024 * 1024  # small on the wire, huge when unpacked

    class Bomb(LocalGitHub):
        def archive(self, repository, commit):
            return archive

    repo = git_repo(tmp_path, {"skills/notes/SKILL.md": SKILL, "skills/notes/LICENSE": "Test fixture licence.\n"})
    store, catalog, *_ = service(tmp_path, [])
    alice, _, _ = account(store, "1", "alice")
    with pytest.raises(RegistryError, match="archive exceeds limits"):
        store.github_import(alice, import_body(), Bomb(repo, 1), now=NOW)
    catalog.close()


# -- website contract (shapes the Skill Codex pages read) --------------------------------------


@pytest.mark.parametrize("text", [
    "Handy, longer write up at example dot com slash review", "see example (dot) com", "example [dot] net",
    "example{dot}io", "example d0t c0m", "example DOT app", "visit example . com", "example [.] com",
    "hxxps example", "https colon slash slash example", "example com slash review", "find it at the dotcom",
])
def test_filter_refuses_links_spelled_out_in_words(text):
    assert review(text)[0] == "refused"


@pytest.mark.parametrize("text", [
    "It helped me slash my prep time in half.", "The dot top right shows the status.",
    "Connect the dots to finish the outline.", "I use the slash command every day.",
])
def test_filter_keeps_everyday_dot_and_slash_wording(text):
    assert review(text) == ("published", "")


def test_me_shapes_provider_names_and_linking_returns_the_provider_url(tmp_path):
    store, catalog, _, *_ = service(tmp_path, [])

    def exchange(url, data=None, *, token=""):
        if url.endswith("access_token"):
            return {"access_token": "example-provider-token", "scope": ""}
        return {"id": 77}

    auth = Auth(store, ORIGIN, [Provider("github", "test-client", "test-secret"),
                                Provider("google", "test-client", "test-secret")], request=exchange)
    app = create_app(store, auth, catalog, TARGET, clock=lambda: NOW)
    who, token, csrf = account(store, "google-subject-private", "alice", provider="google")
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(PREFIX + "/me").json() == {"profile": None, "csrf": None}
        client.cookies.set("vool_registry", "expired-or-unknown")
        stale = client.get(PREFIX + "/me")
        assert stale.status_code == 200 and stale.json() == {"profile": None, "csrf": None}
        client.cookies.clear()
        # A plain sign-in link is still a redirect to the provider.
        assert client.get(PREFIX + "/auth/github/start", follow_redirects=False).headers["location"].startswith(
            "https://github.com/login/oauth/authorize?")
        client.cookies.clear()
        client.cookies.set("vool_registry", token)
        me = client.get(PREFIX + "/me")
        assert me.json()["profile"]["providers"] == ["google"] and me.json()["csrf"] == csrf
        assert "google-subject-private" not in me.text  # provider subjects stay private
        for headers in ({"Origin": ORIGIN}, {"Origin": "https://evil.test", "X-Vool-CSRF": csrf}):
            assert client.post(PREFIX + "/auth/github/start", headers=headers).status_code == 400
        linking = client.post(PREFIX + "/auth/github/start", headers=mutation(csrf))
        assert linking.status_code == 200 and set(linking.json()) == {"url"}
        url = linking.json()["url"]
        assert url.startswith("https://github.com/login/oauth/authorize?")
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]
        back = client.get(PREFIX + "/auth/github/callback", params={"state": state, "code": "code"},
                          follow_redirects=False)
        assert back.status_code == 303, back.text
        client.cookies.set("vool_registry", back.cookies["vool_registry"])
        me = client.get(PREFIX + "/me").json()
        assert me["profile"]["id"] == who and me["profile"]["providers"] == ["github", "google"]
        saved = client.put(PREFIX + "/profile", json={"namespace": "alice", "bio": "Notes", "handles": {}},
                           headers=mutation(me["csrf"]))
        assert saved.json()["providers"] == ["github", "google"] and saved.json()["bio"] == "Notes"
    catalog.close()


def test_rows_carry_starred_for_signed_in_viewers_and_optional_titles(tmp_path):
    store, catalog, app, *_ = service(tmp_path, [
        ("alice/notes", {"title": "Meeting Notes"}, NOW - 300),
        ("alice/agenda", {"subcategory": "planning-tasks", "summary": "Draft an agenda from a goal"}, NOW - 200)])
    voter, token, _ = account(store, "2", "voter")
    store.star(voter, "alice/notes", True, now=NOW)
    with TestClient(app, base_url=ORIGIN) as client:
        signed_out = {row["id"]: row for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert all("starred" not in row for row in signed_out.values())
        assert signed_out["alice/notes"]["title"] == "Meeting Notes" and "title" not in signed_out["alice/agenda"]
        client.cookies.set("vool_registry", token)
        rows = {row["id"]: row["starred"] for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert rows == {"alice/notes": True, "alice/agenda": False}
        page = {row["id"]: row["starred"] for row in client.get(PREFIX + "/profiles/alice").json()["packages"]}
        assert page == rows
        client.cookies.set("vool_registry", "expired-or-unknown")
        assert all("starred" not in row for row in client.get(PREFIX + "/catalog").json()["entries"])
    catalog.close()


def test_comment_reason_codes_tell_the_page_why_commenting_is_closed(tmp_path):
    store, catalog, *_ = service(tmp_path, [("alice/notes", {}, NOW - 100)])
    nameless, _, _ = account(store, "4", None)
    bob, _, _ = account(store, "2", "bob")
    assert "can_comment_reason" not in community.comments(store, "alice/notes")
    assert community.comments(store, "alice/notes", nameless)["can_comment_reason"] == "no_public_name"
    assert community.comments(store, "alice/notes", bob)["can_comment_reason"] == "not_downloaded"
    community.record_download(store, bob, "alice/notes", now=NOW)
    open_ = community.comments(store, "alice/notes", bob)
    assert open_["can_comment"] is True and "can_comment_reason" not in open_
    community.mute(store, "bob", True, now=NOW)
    assert community.comments(store, "alice/notes", bob)["can_comment_reason"] == "muted"
    catalog.close()


class OwnerOnlyGitHub:
    """Repository owner lookups only; publisher linking never downloads anything."""

    def __init__(self, owner_id: int):
        self.owner_id, self.calls = owner_id, []

    def repository(self, repository):
        self.calls.append(repository)
        return {"owner_id": self.owner_id, "private": False}


def test_github_publisher_name_links_to_the_repository_owner_and_then_accepts_drafts(tmp_path):
    github = OwnerOnlyGitHub(77)
    store, catalog, app, *_ = service(tmp_path, [("alice/notes", {}, NOW - 300)], github=github)
    who, token, csrf = account(store, "77", None)
    draft = {"metadata": {**METADATA, "version": "1.0.1"}, "skill_markdown": SKILL, "license_text": "Test fixture licence.\n"}
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        claimed = client.put(PREFIX + "/profile", json={"namespace": "alice", "bio": "", "handles": {}},
                             headers=mutation(csrf))
        assert claimed.status_code == 400 and "reserved" in claimed.json()["error"]
        assert "public publisher name" in client.post(PREFIX + "/drafts", json=draft, headers=mutation(csrf)).json()["error"]
        cross = client.post(PREFIX + "/publisher-link", json={"namespace": "alice"},
                            headers={"Origin": "https://evil.test", "X-Vool-CSRF": csrf})
        assert cross.status_code == 400 and github.calls == []
        linked = client.post(PREFIX + "/publisher-link", json={"namespace": "alice"}, headers=mutation(csrf))
        assert linked.status_code == 200 and linked.json() == {"namespace": "alice", "verified": True}
        assert github.calls == ["alice/skills"]
        assert client.get(PREFIX + "/me").json()["profile"]["namespace"] == "alice"
        created = client.post(PREFIX + "/drafts", json=draft, headers=mutation(csrf))
        assert created.status_code == 201 and created.json()["package_id"] == "alice/notes"
    catalog.close()


@pytest.mark.parametrize("case, message", [
    ("google-only", "sign in with GitHub"),
    ("other-owner", "must belong to the GitHub account"),
    ("unpublished-name", "no GitHub publications"),
    ("prior-claimant", "another account holds"),
    ("different-name", "different publisher name"),
    ("website-upload", "not built for this account"),
])
def test_github_publisher_link_refusals(tmp_path, case, message):
    github = OwnerOnlyGitHub(99 if case == "other-owner" else 77)
    store, catalog, _, policy, keys, root = service(tmp_path, [])
    if case == "prior-claimant":
        account(store, "5", "alice", provider="google")
    packages = [(package(tmp_path, "alice/notes"), NOW - 300)]
    namespace = "alice"
    if case == "website-upload":
        # A website upload whose author later deleted the account leaves no proof for anyone else.
        dave, _, _ = account(store, "8", "dave", provider="google")
        first = store.draft(dave, {"metadata": {**METADATA, "id": "dave/notes"}, "skill_markdown": SKILL,
                                   "license_text": "Test fixture licence.\n"}, now=NOW)
        with store.connection() as db:
            raw = db.execute("SELECT artifact FROM submissions WHERE id=?", (first["id"],)).fetchone()[0]
        store.review(first["id"], signed(prepublication_fixture(raw, policy), keys["review"]), policy, root["signed"], now=NOW)
        store.consent(dave, first["id"], first["sha256"], public=True)
        packages.append((raw, NOW - 200))
        namespace = "dave"
    catalog.accept(catalog_of(packages, policy, keys), now=NOW)
    for raw, _ in packages:
        meta = inspect(raw)["metadata"]
        store.publish(catalog, meta["id"], meta["version"], TARGET, raw, now=NOW)
    if case == "website-upload":
        store.delete_account(dave)
    who, _, _ = account(store, "77", "elsewhere" if case == "different-name" else None,
                        provider="google" if case == "google-only" else "github")
    with pytest.raises(RegistryError, match=message):
        store.link_github_publisher(who, "nobody" if case == "unpublished-name" else namespace, github)
    if case != "other-owner":
        assert github.calls == []  # refused before any GitHub lookup
    assert store.profile(who)["namespace"] in (None, "elsewhere")
    with store.connection() as db:
        assert not db.execute("SELECT 1 FROM namespace_owners WHERE account=? AND verified=1", (who,)).fetchone()
    catalog.close()


def test_publisher_link_route_refuses_when_github_is_not_configured(tmp_path):
    store, catalog, app, *_ = service(tmp_path, [])
    _, token, csrf = account(store, "77", None)
    with TestClient(app, base_url=ORIGIN) as client:
        client.cookies.set("vool_registry", token)
        response = client.post(PREFIX + "/publisher-link", json={"namespace": "alice"}, headers=mutation(csrf))
        assert response.status_code == 400 and "not configured" in response.json()["error"]
    catalog.close()
