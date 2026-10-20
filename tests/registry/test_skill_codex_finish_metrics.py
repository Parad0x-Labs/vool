"""Website actions become distinct native and public-repository counters."""
from playwright.sync_api import expect

from core.registry.codec import decode
from tests.registry.test_skill_codex_authority import ORIGIN, creator
from tests.registry.test_skill_codex_finish_journey import browse, native_http, open_codex
from tests.registry.test_skill_codex_finish_journey import served as served
from tests.registry.test_skill_codex_finish_marketplace import form
from tests.registry.test_skill_codex_github_signer import FOLDER, on_github, run, team_steps
from tests.registry.test_skill_codex_github_signer import world as world
from tests.served_browser import launch_chromium


def test_nonzero_website_metrics_reach_real_native_browser_and_repository(served, tmp_path):
    _, token, csrf = creator(served, "101", "alice")
    served.http.cookies.set("vool_registry", token)
    headers = {"Origin": ORIGIN, "X-Vool-CSRF": csrf}
    answer = served.http.post("/registry/api/drafts", json=form(notes="Adds an unresolved questions section."), headers=headers)
    assert answer.status_code == 201, answer.text
    item = answer.json()
    assert served.http.post("/registry/api/submissions/" + item["id"] + "/consent",
        json={"sha256": item["sha256"], "public": True}, headers=headers).status_code == 200
    served.http.cookies.clear()
    run(served)
    for subject, name in (("102", "bob"), ("103", "carol")):
        _, token, csrf = creator(served, subject, name)
        served.http.cookies.set("vool_registry", token)
        answer = served.http.put("/registry/api/stars", json={"id":"alice/notes", "version":"1.0.0", "starred":True},
                                headers={"Origin":ORIGIN, "X-Vool-CSRF":csrf})
        assert answer.status_code == 200, answer.text
    for _ in range(3):
        ticket = served.http.get("/registry/api/download", params={"id":"alice/notes", "version":"1.0.0"})
        assert ticket.status_code == 200, ticket.text
        assert served.http.get(ticket.json()["url"]).status_code == 200
    served.http.cookies.clear()
    team_steps(served, "editors-pick alice/notes\n")
    run(served)
    row, = decode(on_github(served, FOLDER + "/registry.json"))["entries"]
    assert (row["stars"], row["download_requests"]) == (2, 3)
    manager, browser = launch_chromium()
    try:
        with native_http() as address:
            page = browser.new_page(viewport={"width":1280, "height":960})
            open_codex(page, address)
            assert browse(page).ok
            expect(page.locator("#pluginsBody")).to_contain_text("2 website stars · 3 download requests")
            expect(page.locator("#pluginsBody .registry-editor-pick")).to_have_text("Editor's pick")
            expect(page.get_by_role("button", name="Browse Skill Codex", exact=True)).to_be_enabled()
            page.get_by_text("Release notes", exact=True).click()
            page.screenshot(path=str(tmp_path / "native-browser-metrics-proof.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
