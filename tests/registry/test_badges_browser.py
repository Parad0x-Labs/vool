"""The served Add-ons page draws a team badge only from the signed badge document: a chip for a verified badge,
and none for a flag on a row, for words an author wrote, or for a document one person signed."""
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from core import plugin_lifecycle
from core import plugin_manager as manager
from core.addon_catalog import CATALOG
from core.registry import client as native_client
from core.registry.artifact import inspect, pack
from core.registry.client import Client
from core.registry.codec import canonical
from tests.registry.badge_helpers import ORIGIN, by, entry, payload, raw_document, relay, stamp_for, world
from tests.registry.helpers import NOW, TARGET, catalog_fixture, signed
from tests.test_plugin_identity_is_checked_at_install import door

TESTED, CHOICE = "Tested by VOOL", "Editor's Choice"
HELP = {TESTED: "The VOOL team installed and used this exact version.",
        CHOICE: "Picked by the VOOL team as one of the best of its kind."}
ROW = next(row for row in CATALOG if row.get("bundled_source") and row.get("sha256"))


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugin-home"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    plugin_lifecycle.reset_for_tests()
    w = world(tmp_path, monkeypatch)
    anchor = tmp_path / "trusted-root.json"
    anchor.write_bytes(canonical(w.root))
    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(anchor))
    w.release = tmp_path / "release"
    w.release.mkdir()
    monkeypatch.setattr(native_client, "_shipped_badges", lambda: w.release)
    monkeypatch.setattr(native_client, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(native_client, "runtime_target", lambda: TARGET)
    monkeypatch.setattr(native_client, "configured_client",
                        lambda: Client(w.root, TARGET, origin=ORIGIN, request=w.request, now=NOW))
    yield w
    plugin_lifecycle.reset_for_tests()


@contextlib.contextmanager
def add_ons_page(tmp_path):
    """The real chat page and its real add-on routes on a local port, in Chromium."""
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    runtime = RuntimeServices(display_name="VOOL")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/chat":
                return self.reply(200, render_vool_chat_html().encode(), "text/html")
            if path in {"/api/addons", "/api/plugins", "/api/intake/providers"}:
                result = dispatch_get(path=path, query={}, runtime=runtime, model_name="fixture", client_host="127.0.0.1")
                return self.reply(result.status, result.body)
            return self.reply(200, b"{}")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path != "/api/addons":
                return self.reply(200, b"{}")
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime, model_name="fixture",
                                   workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.reply(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager_, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        yield page
    finally:
        browser.close()
        manager_.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def skill_codex_card(page):
    page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
    page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
    card = page.locator("#pluginsBody .addon-card").first
    card.get_by_role("button", name="Download for inspection", exact=True).wait_for()
    return card


def chips(scope):
    return scope.locator("vool-badge.vool-team-badge").all_inner_texts()


def test_a_verified_badge_is_a_chip_beside_the_title_with_its_help_line(device, tmp_path):
    w = device
    document = raw_document([entry("tested", w.sha[-1], w.people), entry("editors_choice", w.sha[-1], w.people)])
    assert relay(w, payload(w, document)).status_code == 200
    with add_ons_page(tmp_path) as page:
        card = skill_codex_card(page)
        assert chips(card) == [TESTED, CHOICE]
        assert chips(card.locator(".addon-card-head")) == [TESTED, CHOICE]  # beside the title, in the card's head
        for label in (TESTED, CHOICE):
            chip = card.locator("vool-badge", has_text=label)
            assert chip.get_attribute("title") == HELP[label]
            assert chip.get_attribute("aria-label") == label + ". " + HELP[label]
        assert chips(page.locator("body")) == [TESTED, CHOICE]  # and nowhere else on the page
        page.screenshot(path=str(tmp_path / "team-badges.png"), full_page=True)


def test_a_flag_on_the_row_draws_no_chip_and_no_pick_line(device, tmp_path):
    w = device
    marks = {"schema": "vool.registry.marks.v1", "sequence": 1, "issued_at": NOW,
             "tested": ["alice/echo@" + w.sha[-1]], "highlighted": ["alice/echo"], "suspended": [], "teams": {},
             "curation": {"schema": "vool.registry.curation.v1", "official_namespaces": [], "featured": ["alice/echo"],
                          "editors_picks": ["alice/echo"], "approved_pictures": [], "reviewers": {}, "reviews": []}}
    w.store.set_marks(signed(marks, w.keys["admission"]), w.root["signed"], now=NOW)
    with add_ons_page(tmp_path) as page:
        card = skill_codex_card(page)
        assert chips(page.locator("body")) == []
        text = card.inner_text()
        assert "Editor" not in text and "Featured" not in text and "Tested by" not in text
        assert page.locator(".registry-editor-pick").count() == 0


def test_words_an_author_wrote_stay_plain_words(device, tmp_path):
    """A package that reached the list with badge words in its title and summary: they are text, never a chip."""
    w = device
    author = tmp_path / "author"
    info = {**inspect(w.store.public_artifact(w.sha[-1])[0])["metadata"], "version": "1.0.1",
            "title": "✓ Tested by VOOL", "summary": "Tested by VOOL. Editor's Choice."}
    manifest = author / ".codex-plugin" / "plugin.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "version": "1.0.1"}))
    raw = pack(author, info, tmp_path / "scratch")
    w.trust.accept(catalog_fixture(raw, w.policy, w.keys, sequence=2), now=NOW)
    w.store.publish(w.trust, "alice/echo", "1.0.1", TARGET, raw, now=NOW)
    with add_ons_page(tmp_path) as page:
        card = skill_codex_card(page)
        assert card.locator("h3").inner_text() == "✓ Tested by VOOL"  # shown as the author wrote it
        assert chips(page.locator("body")) == [] and page.locator("vool-badge").count() == 0


def test_a_document_one_person_signed_draws_no_chip(device, tmp_path):
    w = device
    document = raw_document([entry("tested", w.sha[-1], w.people), entry("editors_choice", w.sha[-1], w.people)])
    w.served = {"issuers": w.issuers, "badges": by(document, w.people[:1]), "stamp": stamp_for(document, w.keys)}
    with add_ons_page(tmp_path) as page:
        skill_codex_card(page)
        assert "/registry/api/badges" in w.fetched
        assert chips(page.locator("body")) == [] and page.locator("vool-badge").count() == 0


def test_a_bundled_skill_and_an_installed_plugin_show_the_same_chip(device, door, tmp_path):
    w = device
    review = manager.prepare_local(str(door("acme-parcel")))
    manager.install(review["review_id"], accepted=True, digest=review["digest"])
    package = plugin_lifecycle.record_for("acme-parcel").verified_digest
    document = raw_document([
        entry("editors_choice", ROW["sha256"], w.people, package_id=ROW["id"], digest_type="skill-file"),
        entry("tested", package, w.people, package_id="acme-parcel", kind="plugin", digest_type="package")])
    (w.release / "badges.json").write_bytes(canonical(by(document, w.people[:2])))
    (w.release / "badge-issuers.json").write_bytes(canonical(w.issuers))
    with add_ons_page(tmp_path) as page:
        page.locator("#pluginsSearch").fill(ROW["name"])
        card = page.locator("#pluginsBody .addon-card").first
        card.locator("vool-badge").wait_for()
        assert chips(card) == [CHOICE] and chips(page.locator("body")) == [CHOICE]
        page.locator("#pluginsSearch").fill("")
        page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
        page.locator("#pluginsBody .addon-controls").get_by_role("button", name="Plugins", exact=True).click()
        plugin = page.locator('#pluginsBody [data-plugin-id="acme-parcel"]')
        plugin.wait_for()
        assert chips(plugin) == [TESTED] and chips(plugin.locator(".plugin-title-row")) == [TESTED]
        assert chips(page.locator("body")) == [TESTED]
        page.screenshot(path=str(tmp_path / "plugin-badge.png"), full_page=True)
