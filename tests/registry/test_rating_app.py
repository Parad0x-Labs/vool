"""VOOL's side of ratings: the install pass is kept only for an add-on that installed, and leaves only on the Rate click."""
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from core.registry.codec import RegistryError, decode
from registry_service.api import PREFIX
from tests.registry.helpers import TARGET
from tests.registry.test_ratings import NOTES, act, later, listed, person, write
from tests.registry.test_skill_codex_authority import ORIGIN
from tests.registry.test_skill_codex_finish_journey import browse, native_http, open_codex
from tests.registry.test_skill_codex_finish_journey import served as served
from tests.registry.test_skill_codex_github_signer import world as world


@pytest.fixture
def app(served, monkeypatch, tmp_path):
    """The app's registry client against the served registry, fetching packages with their headers as VOOL does."""
    from core.registry import client

    def request(url, *, limit):
        parsed = urlsplit(url)
        served.outbound.append(("GET", parsed.path))
        response = served.http.get(parsed.path + ("?" + parsed.query if parsed.query else ""))
        if response.status_code != 200:
            raise RegistryError(response.json()["error"])
        return response.content

    def download(url, *, limit):
        parsed = urlsplit(url)
        served.outbound.append(("GET", parsed.path))
        response = served.http.get(parsed.path + "?" + parsed.query)
        assert response.status_code == 200, response.text
        return response.content, response.headers.get(client.PASS_HEADER, "")

    monkeypatch.setattr(client, "configured_client", lambda: client.Client(
        decode((tmp_path / "app-root.json").read_bytes()), TARGET, origin=ORIGIN, request=request, now=served.clock.t,
        download=download))
    return served


def receipt(package_id):
    from core.registry.artifact import native_identity
    from core.registry.native import _receipt_path

    path = _receipt_path(native_identity(package_id))
    return decode(path.read_bytes()) if path.exists() else None


def install(package_id, *, accepted=True):
    from core.registry.client import handle

    review = handle("registry_prepare", {"id": package_id, "version": "1.0.0", "approved": True})
    return handle("registry_install", {"id": package_id, "version": "1.0.0", "review_id": review["review_id"],
                                       "accepted": accepted, "enable": False})


def test_the_app_keeps_a_pass_only_after_an_install_and_sends_one_pass_only_on_the_rate_click(app):
    from core.registry import native
    from core.registry.client import handle

    listed(app, "notes", "tasks")
    start = len(app.outbound)  # everything before this was the signer's own traffic
    handle("registry_refresh", {"approved": True})
    staging = native.home() / "registry_staging"
    # Declined at the last step: nothing is installed, so no pass is kept anywhere.
    with pytest.raises(RegistryError):
        install("alice/tasks", accepted=False)
    assert receipt("alice/tasks") is None and not list(staging.glob("*.pass"))
    with pytest.raises(RegistryError, match="no install pass; install it from the Skill Codex to rate it"):
        handle("registry_rate", {"id": "alice/tasks"})

    assert install(NOTES)["status"] == "installed" and install("alice/tasks")["status"] == "installed"
    kept, other = receipt(NOTES)["review_pass"], receipt("alice/tasks")["review_pass"]
    assert kept != other and not list(staging.glob("*.pass"))
    rows = {row["id"]: row for row in handle("registry_refresh", {"approved": True})["entries"]}
    assert rows[NOTES]["rate_ready"] is True and rows[NOTES]["rating"] == {"shown": False, "average": None, "count": 0, "removed": 0}

    before = list(app.outbound)
    answer = handle("registry_rate", {"id": NOTES})
    assert app.outbound == before  # the click makes a link; VOOL itself sends nothing
    url = urlsplit(answer["url"])
    assert (url.scheme, url.netloc, url.path, url.query) == ("https", "vool.dev", "/codex/skills/review", "")
    assert parse_qs(url.fragment) == {"id": [NOTES], "pass": [kept]}  # after the #: one add-on, its one pass
    assert other not in answer["url"]
    # Nothing VOOL asked the registry for named an installed add-on, apart from the downloads themselves.
    asked = {path for _, path in app.outbound[start:]}
    assert asked <= {PREFIX + "/snapshot", PREFIX + "/presentation", PREFIX + "/ratings/summary", PREFIX + "/download"} | {
        path for path in asked if path.startswith(PREFIX + "/artifacts/")}

    # The pass the website receives is the one the registry takes, the next day, from a signed-in person.
    later(app, 1)
    bob = person(app, "2", "bob")
    done = act(app, bob, "PUT", "/ratings", json={"id": NOTES, "stars": 5, "pass": parse_qs(url.fragment)["pass"][0]})
    assert done.status_code == 200 and done.json()["state"] == "published"


def test_the_card_shows_stars_the_download_figure_the_team_notice_and_the_rate_button(app, tmp_path):
    from playwright.sync_api import expect

    from tests.served_browser import launch_chromium

    listed(app, "notes", "tasks")
    crowd = [person(app, str(800 + index)) for index in range(6)]
    for index, who in enumerate(crowd):
        write(app, who, NOTES, 5 if index < 4 else 3)
    with app.store.connection() as db:
        db.execute("UPDATE ratings SET state='voided', void_reason='fake', voided_at=? WHERE account=?", (app.clock.t, crowd[5]))
    from core.registry.client import handle

    handle("registry_refresh", {"approved": True})
    assert install(NOTES)["status"] == "installed"
    manager, browser = launch_chromium()
    try:
        with native_http() as address:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            open_codex(page, address)
            assert browse(page).ok
            notes = page.locator("#pluginsBody .addon-card").filter(has_text="alice/notes")
            tasks = page.locator("#pluginsBody .addon-card").filter(has_text="alice/tasks")
            expect(notes.locator(".registry-rating")).to_have_text("4.6 out of 5 · 5 ratings")
            expect(notes.locator(".registry-rating svg")).to_have_count(1)
            expect(notes.locator(".registry-downloads")).to_have_text("1 download requests")
            expect(notes.locator(".registry-downloads svg")).to_have_count(1)  # its own figure, its own icon
            expect(notes.locator(".registry-moderation")).to_have_text("Ratings removed by the VOOL team for breaking the rules: 1")
            expect(tasks.locator(".registry-rating-few")).to_have_text("Not enough ratings yet (0)")
            expect(tasks.locator(".registry-rating")).to_have_count(0)
            expect(tasks.locator(".registry-moderation")).to_have_count(0)
            expect(tasks.get_by_role("button", name="Rate this add-on")).to_have_count(0)  # not installed: nothing to rate
            before = list(app.outbound)
            notes.get_by_role("button", name="Rate this add-on").click()
            link = notes.get_by_role("link", name="Open Skill Codex", exact=True)
            expect(link).to_have_attribute("href", re.compile(r"^https://vool\.dev/codex/skills/review#id=alice%2Fnotes&pass=[A-Za-z0-9_-]{135}$"))
            expect(link).to_have_attribute("rel", "noopener noreferrer")
            expect(notes).to_contain_text("VOOL sends nothing itself.")
            assert app.outbound == before
            # Text from the registry is set as text, never parsed as markup.
            assert page.evaluate("document.querySelectorAll('#pluginsBody .registry-figures script, #pluginsBody .registry-figures img').length") == 0
            page.screenshot(path=str(tmp_path / "rating-card.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
