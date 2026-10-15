"""Author consent, release notes, public repository artifacts and cross-surface presentation."""
import pytest

from core.registry.codec import canonical, decode
from registry_service.authority import AGREEMENT
from tests.registry.test_skill_codex_authority import ORIGIN, SKILL, creator, draft, rows
from tests.registry.test_skill_codex_finish_journey import served as served
from tests.registry.test_skill_codex_github_signer import FOLDER, app_rows, on_github, run, team_steps
from tests.registry.test_skill_codex_github_signer import world as world


def form(package_id="alice/notes", *, notes=None):
    info = {"id": package_id, "kind": "skill", "version": "1.0.0", "summary": "Turn meeting notes into decisions",
            "category": "productivity", "subcategory": "meetings", "license": "MIT", "languages": ["en"]}
    if notes is not None:
        info["release_notes"] = notes
    return {"metadata": info, "skill_markdown": SKILL, "agreement": AGREEMENT}


def test_agreement_leaves_upload_private_until_exact_checksum_consent(world):
    _, token, csrf = creator(world, "101", "alice")
    world.http.cookies.set("vool_registry", token)
    headers = {"Origin": ORIGIN, "X-Vool-CSRF": csrf}
    answer = world.http.post("/registry/api/drafts", json=form(), headers=headers)
    assert answer.status_code == 201, answer.text
    submission = answer.json()
    assert submission["state"] == "checking" and submission["consent"] == 0
    assert world.store.queue() == []
    world.http.cookies.clear()
    assert run(world)["admitted"] == [] and world.model.calls == []
    world.http.cookies.set("vool_registry", token)
    path = "/registry/api/submissions/" + submission["id"] + "/consent"
    assert world.http.post(path, json={"sha256": "0" * 64, "public": True}, headers=headers).status_code == 400
    consent = world.http.post(path, json={"sha256": submission["sha256"], "public": True}, headers=headers)
    assert consent.status_code == 200 and consent.json()["consent"] == 1
    world.http.cookies.clear()
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]


def test_existing_implicit_consent_is_withheld_on_upgrade_and_fresh_consent_survives_restart(world):
    from registry_service.store import Store

    account, token, csrf = creator(world, "101", "alice")
    item = draft(world, token, csrf, "alice/notes").json()
    # Earlier automatic intake persisted consent=1 from the rights agreement alone.
    with world.store.connection() as db:
        db.execute("DELETE FROM service_keys WHERE name='checksum-consent-v1'")
    upgraded = Store(world.store.path)
    assert upgraded.submission(account, item["id"])["consent"] == 0
    assert upgraded.queue() == []
    upgraded.consent(account, item["id"], item["sha256"], public=True)
    restarted = Store(world.store.path)
    assert restarted.submission(account, item["id"])["consent"] == 1
    assert len(restarted.queue()) == 1
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]


def test_upgrade_withholds_an_unpublished_legacy_generic_publish_choice(world):
    from registry_service.store import Store

    account, token, csrf = creator(world, "101", "alice")
    item = draft(world, token, csrf, "alice/notes").json()
    with world.store.connection() as db:
        db.execute("DELETE FROM service_keys WHERE name='checksum-consent-v1'")
        db.execute("UPDATE submissions SET agreement=NULL,auto_consent=1,state='ready_for_github' WHERE id=?", (item["id"],))
    upgraded = Store(world.store.path)
    private = upgraded.submission(account, item["id"])
    assert private["state"] == "preflighted" and private["consent"] == 0
    with upgraded.connection() as db:
        assert db.execute("SELECT auto_consent FROM submissions WHERE id=?", (item["id"],)).fetchone()[0] == 0


def test_consent_upgrade_keeps_already_published_history_available(world):
    from registry_service.store import Store

    account, token, csrf = creator(world, "101", "alice")
    item = draft(world, token, csrf, "alice/notes").json()
    run(world)
    with world.store.connection() as db:
        db.execute("DELETE FROM service_keys WHERE name='checksum-consent-v1'")
    upgraded = Store(world.store.path)
    assert upgraded.submission(account, item["id"])["state"] == "published"
    assert upgraded.submission(account, item["id"])["consent"] == 1
    assert upgraded.public_artifact(item["sha256"])[0] and rows(world)["alice/notes"]


def test_release_notes_are_in_signed_bytes_and_published_rows(world):
    _, token, csrf = creator(world, "101", "alice")
    world.http.cookies.set("vool_registry", token)
    headers = {"Origin": ORIGIN, "X-Vool-CSRF": csrf}
    response = world.http.post("/registry/api/drafts", json=form(notes="Adds a separate unresolved questions section."), headers=headers)
    assert response.status_code == 201, response.text
    submission = response.json()
    path = "/registry/api/submissions/" + submission["id"] + "/consent"
    assert world.http.post(path, json={"sha256": submission["sha256"], "public": True}, headers=headers).status_code == 200
    world.http.cookies.clear()
    assert run(world)["admitted"] == ["alice/notes 1.0.0"]
    assert rows(world)["alice/notes"]["release_notes"] == "Adds a separate unresolved questions section."
    public = on_github(world, FOLDER + "/packages/" + submission["sha256"] + ".json")
    assert decode(public)["metadata"]["release_notes"] == rows(world)["alice/notes"]["release_notes"]
    assert public == world.store.public_artifact(submission["sha256"])[0]


def test_editor_picks_and_metrics_reach_the_app_and_github_then_leave_with_removal(world):
    _, token, csrf = creator(world, "101", "alice")
    submission = draft(world, token, csrf, "alice/notes").json()
    run(world)
    team_steps(world, "editors-pick alice/notes\n")
    run(world)
    assert app_rows(world)["alice/notes"]["editors_pick"] is True
    assert app_rows(world)["alice/notes"]["stars"] == 0
    directory = decode(on_github(world, FOLDER + "/registry.json"))
    assert directory["entries"][0]["editors_pick"] is True
    assert on_github(world, FOLDER + "/packages/" + submission["sha256"] + ".json")
    team_steps(world, "removed alice/notes\n")
    run(world)
    assert decode(on_github(world, FOLDER + "/registry.json"))["entries"] == []
    assert app_rows(world) == {}
    # Historic commits remain public; only the current registry projection is withdrawn.
    assert b"alice/notes" not in on_github(world, FOLDER + "/CATALOG.md")


def test_release_note_format_is_bounded_and_cannot_carry_a_secret(world):
    _, token, csrf = creator(world, "101", "alice")
    world.http.cookies.set("vool_registry", token)
    headers = {"Origin": ORIGIN, "X-Vool-CSRF": csrf}
    for text in ("x" * 2001, "<script>alert(1)</script>"):
        assert world.http.post("/registry/api/drafts", json=form(notes=text), headers=headers).status_code == 400
    secret = "ghp_" + "A" * 36
    response = world.http.post("/registry/api/drafts", json=form(notes="Changed credential " + secret), headers=headers)
    assert response.status_code == 201 and response.json()["state"] == "rejected"
    assert secret.encode() not in canonical(response.json())


@pytest.mark.parametrize("target", ["alice", "alice/notes"])
def test_suspension_withdraws_listed_skills_from_all_discovery_surfaces(world, target):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    run(world)
    team_steps(world, "suspended " + target + "\n")
    report = run(world)
    assert report["steps"]["applied"]
    assert rows(world) == {} and app_rows(world) == {}


def test_editorial_display_is_signed_and_cannot_replay_an_older_selection(world, tmp_path, monkeypatch):
    from core.registry.client import Client

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "app-home"))
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    run(world)
    team_steps(world, "editors-pick alice/notes\n")
    run(world)
    previous = world.http.get("/registry/api/presentation").json()
    mode = ["real"]
    def request(url, *, limit):
        response = world.http.get(url)
        assert response.status_code == 200
        if url.endswith("/presentation") and mode[0] != "real":
            value = decode(canonical(previous))
            if mode[0] == "forged":
                value["marks"]["signed"]["sequence"] += 100
            return canonical(value)
        return response.content
    client = Client(world.root, {"version": "0.7.1", "commit": "a" * 40, "platform": "macos-arm64"},
                    origin=ORIGIN, request=request, now=world.clock.t)
    try:
        assert client.refresh(approved=True, now=world.clock.t)["entries"][0]["editors_pick"]
        mode[0] = "forged"
        assert not client.refresh(approved=True, now=world.clock.t)["entries"][0].get("editors_pick", False)
        mode[0] = "real"
        team_steps(world, "")
        run(world)
        assert not client.refresh(approved=True, now=world.clock.t)["entries"][0]["editors_pick"]
        mode[0] = "replayed"
        assert not client.refresh(approved=True, now=world.clock.t)["entries"][0].get("editors_pick", False)
    finally:
        client.close()


def test_native_browser_shows_editor_pick_metrics_release_notes_and_disclaimer(served, tmp_path):
    from playwright.sync_api import expect

    from tests.registry.test_skill_codex_finish_journey import browse, native_http, open_codex
    from tests.served_browser import launch_chromium

    _, token, csrf = creator(served, "101", "alice")
    served.http.cookies.set("vool_registry", token)
    headers = {"Origin": ORIGIN, "X-Vool-CSRF": csrf}
    response = served.http.post("/registry/api/drafts", json=form(notes="Adds an unresolved questions section."), headers=headers)
    assert response.status_code == 201
    item = response.json()
    assert served.http.post("/registry/api/submissions/" + item["id"] + "/consent",
                            json={"sha256": item["sha256"], "public": True}, headers=headers).status_code == 200
    served.http.cookies.clear()
    run(served)
    team_steps(served, "editors-pick alice/notes\n")
    run(served)
    manager, browser = launch_chromium()
    try:
        with native_http() as address:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            open_codex(page, address)
            assert browse(page).ok
            assert page.get_by_role("button", name="Browse Skill Codex", exact=True).evaluate(
                "button => getComputedStyle(button).paddingTop") == "9px"
            expect(page.locator("#pluginsBody .addon-facts dt")).to_contain_text(["Languages", "Checked for"])
            expect(page.locator("#pluginsBody .registry-editor-pick")).to_have_text("Editor's pick")
            expect(page.locator("#pluginsBody")).to_contain_text("0 website stars")
            expect(page.locator("#pluginsBody")).to_contain_text("do not guarantee")
            page.get_by_text("Release notes", exact=True).click()
            expect(page.locator("#pluginsBody details[open]")).to_contain_text("Adds an unresolved questions section.")
            page.screenshot(path=str(tmp_path / "editorial-marketplace.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
