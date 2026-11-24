"""One card and one detail view for a skill and for a plugin, on the real served page in Chromium.

The registry is the real service. Then the same views against a service that answers whatever it likes:
markup, a script, a link that runs code, a forged badge line and badge flags all stay text, and no chip appears.
"""
import json
import os
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from starlette.testclient import TestClient

from core import plugin_catalog, plugin_lifecycle
from core.registry.artifact import inspect, pack
from core.registry.client import Client
from core.registry.codec import canonical
from core.registry.trust import TrustedCatalog
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, trust_fixture
from tests.registry.test_listing_standard import DESCRIPTION
from tests.registry.test_rating_app import app as app
from tests.registry.test_rating_app import install
from tests.registry.test_ratings import NOTES, listed
from tests.registry.test_skill_codex import catalog_of
from tests.registry.test_skill_codex_finish_journey import native_http, open_codex
from tests.registry.test_skill_codex_finish_journey import served as served
from tests.registry.test_skill_codex_github_signer import world as world

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "examples" / "plugins" / "template"
HOSTILE = ('<img src=x onerror="window.pwned=1">\n\n[click me](javascript:window.pwned=2)\n\n'
           '✓ Tested by VOOL\n\n<script>window.pwned=3</script>\n\n# <b>Editor\'s Choice</b>\n')


def _skill(tmp_path) -> bytes:
    _, root, info = artifact_fixture(tmp_path)
    info = {**info, "title": "Echo notes", "summary": "Summarize a message you paste, in three short lines."}
    (root / "registry.json").write_text(json.dumps({**info, "listing": {
        "description": DESCRIPTION, "links": {"homepage": "https://example.org/echo", "docs": "https://example.org/echo/guide"},
        "compatibility": {"min_vool": "0.7.0", "platforms": ["macos-arm64", "linux-x86_64"]}}}))
    return pack(root, info, tmp_path / "pack-skill")


def _plugin(tmp_path) -> bytes:
    source = tmp_path / "maker" / "text-digest-example"
    shutil.copytree(TEMPLATE, source)
    info = {"id": "alice/text-digest-example", "kind": "plugin", "version": "1.0.0", "title": "Text digest example",
            "summary": "Compute the SHA-256 digest of text you give it.", "category": "productivity",
            "subcategory": "notes-summaries", "license": "MIT", "languages": ["any"],
            "source": {"repository": "alice/plugins", "commit": "b" * 40, "path": "text-digest-example"}}
    (source / "registry.json").write_text(json.dumps({**info, "listing": {"description": DESCRIPTION}}))
    return pack(source, info, tmp_path / "pack-plugin")


def test_the_two_fragment_files_never_set_markup_from_data():
    """Author text reaches the page as text nodes. The one markup assignment is VOOL's own fixed picture tile."""
    for name in ("addon_store_fragment.py", "plugin_store_fragment.py"):
        text = (ROOT / "core" / name).read_text(encoding="utf-8")
        uses = re.findall(r"[^\n;{}]*\b(?:innerHTML|outerHTML|insertAdjacentHTML|document\.write|DOMParser|createContextualFragment|srcdoc)\b[^\n;{}]*", text)
        assert [use.strip() for use in uses] == (["tile.innerHTML=MISSING_PICTURE"] if name == "addon_store_fragment.py" else []), (name, uses)
        assert "eval(" not in text and "new Function" not in text
    fragment = (ROOT / "core" / "addon_store_fragment.py").read_text(encoding="utf-8")
    constant = re.search(r"const MISSING_PICTURE='([^']*)';", fragment).group(1)
    assert constant.startswith("<svg ") and "${" not in constant and "+" not in constant


def test_one_card_and_one_detail_view_serve_a_skill_and_a_plugin_and_hostile_text_stays_text(tmp_path, monkeypatch):
    from core.registry import client as native_client
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    runtime_home = tmp_path / "runtime"
    monkeypatch.setenv("VOOL_HOME", str(runtime_home))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(runtime_home / "plugin_lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugin-home"))
    monkeypatch.setenv("VOOL_BUNDLED_PLUGINS_DIR", str(tmp_path / "no-bundled"))
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "plugin-scratch"))
    plugin_lifecycle.reset_for_tests()
    plugin_catalog.reset_storage_state()
    skill, plugin = _skill(tmp_path), _plugin(tmp_path)
    policy, keys, trust_root = trust_fixture()
    server_trust = TrustedCatalog(trust_root, tmp_path / "server/trust", now=NOW)
    server_trust.accept(catalog_of([(skill, NOW), (plugin, NOW)], policy, keys), now=NOW)
    store = Store(tmp_path / "server/accounts")
    for raw in (skill, plugin):
        store.publish(server_trust, inspect(raw)["metadata"]["id"], "1.0.0", TARGET, raw, now=NOW)
    service = TestClient(create_app(store, Auth(store, "https://registry.test", []), server_trust, TARGET, clock=lambda: NOW),
                         base_url="https://registry.test")
    taken_over, asked = {"on": False}, []

    def request(url, *, limit):
        response = service.get(url)
        assert response.status_code == 200 and len(response.content) <= limit
        path = urlsplit(url).path
        asked.append(path)
        if taken_over["on"] and path == PREFIX + "/listing":
            value = response.json()
            value["description"] = {"format": "markdown-subset-1", "text": HOSTILE, "tree": {"format": "markdown-subset-1", "blocks": [
                {"type": "html", "html": "<img src=x onerror=window.pwned=4>", "spans": [{"type": "script", "text": "window.pwned=5"}]}]}}
            value["badges"] = {"editors_choice": True, "tested_by_vool": True, "tested_version": "1.0.0"}
            value["links"] = {"homepage": "javascript:window.pwned=6", "support": "https://user:pw@example.org/", "docs": "http://example.org/"}
            value["access"]["network"] = ["<img src=x onerror=window.pwned=7>"]
            value["changelog"] = [{"version": "<b>1.0.0</b>", "date": NOW, "notes": "<script>window.pwned=8</script>"}]
            return canonical(value)
        if taken_over["on"] and path == PREFIX + "/ratings":
            return canonical({"ratings": [{"id": "0" * 32, "author": "<i>mallory</i>", "stars": 5, "version": "1.0.0", "proof": "installed",
                                           "created": NOW, "updated": NOW, "body": "<img src=x onerror=window.pwned=9> ✓ Tested by VOOL"}]})
        return response.content

    monkeypatch.setattr(native_client, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(native_client, "configured_client",
                        lambda: Client(trust_root, TARGET, origin="https://registry.test", request=request, now=NOW))
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

    shots = Path(os.environ.get("VOOL_LISTING_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        problems = []
        page.on("pageerror", lambda error: problems.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
        cards = page.locator("#pluginsBody .addon-card[data-listing-id]")
        cards.first.get_by_role("button", name="Download for inspection", exact=True).wait_for()

        def themes(name):
            for theme in ("dark", "light"):
                page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
                page.screenshot(path=str(shots / f"{name}-{theme}.png"), full_page=True)

        # One card for both kinds: picture tile, name, kind and maker, one line, figures, state and actions.
        shape = {}
        for package_id, kind, name in (("alice/echo", "Skill", "Echo notes"), ("alice/text-digest-example", "Plugin", "Text digest example")):
            card = page.locator(f'#pluginsBody .addon-card[data-listing-id="{package_id}"]')
            assert card.locator(".vl-card-top .addon-icon").count() == 1
            assert card.locator(".vl-card-main h3").inner_text() == name
            assert card.locator(".vl-byline").inner_text() == f"{kind} · {package_id} · by alice"
            assert card.locator("p.vl-tagline").count() == 1 and card.locator(".registry-rating-few").inner_text() == "Not enough ratings yet (0)"
            assert card.locator(".registry-downloads").count() == 1
            assert card.locator("vool-badge").count() == 0  # no verified badge document, so no chip
            shape[kind] = card.evaluate("card => [...card.children].map(child => child.tagName + '.' + child.className.split(' ')[0])")
        assert shape["Skill"] == shape["Plugin"]
        themes("card")

        def open_detail(package_id):
            page.locator(f'#pluginsBody .addon-card[data-listing-id="{package_id}"]').get_by_role("button", name=re.compile("^View details")).click()
            detail = page.locator("#pluginsBody .vl-detail")
            detail.locator("section").first.wait_for()
            return detail

        def back():
            page.locator("#addonBack").click()
            cards.first.wait_for()

        # The detail view of the skill, from the real service.
        detail = open_detail("alice/echo")
        assert detail.locator(".vl-detail-head h2").inner_text() == "Echo notes"
        assert "Skill · by alice · alice/echo" in detail.locator(".vl-detail-head .vl-byline").inner_text()
        about = detail.locator(".vl-description")
        assert about.locator("h3").first.inner_text() == "Meeting notes" and about.locator("h4").inner_text() == "What you get"
        assert about.locator("strong").inner_text() == "decisions" and about.locator("em").inner_text() == "owners"
        assert about.locator("p code").inner_text() == "list the decisions"
        assert about.locator("ul li").count() == 2 and about.locator("ol li").count() == 2
        assert about.locator("pre").inner_text() == "Decision: ship on Friday (owner: Ana)"
        text = detail.inner_text()
        for words in ("What it needs", "Versions and changes", "Details", "VOOL 0.7.0 or newer", "macOS Apple Silicon, Linux",
                      "MIT", "Ratings and reviews", "Not enough ratings yet (0)", "No written reviews yet.", "Links",
                      "https://example.org/echo/guide", "The creator wrote these addresses."):
            assert words in text, words
        links = detail.locator(".vl-links a")
        assert [(a.get_attribute("href"), a.get_attribute("rel"), a.get_attribute("target")) for a in links.all()] == [
            ("https://example.org/echo", "noopener noreferrer", "_blank"), ("https://example.org/echo/guide", "noopener noreferrer", "_blank")]
        assert detail.get_by_role("button", name="Download for inspection", exact=True).count() == 1
        assert detail.get_by_role("link", name=re.compile("^Report this skill")).count() == 1
        themes("detail-skill")
        page.set_viewport_size({"width": 420, "height": 900})
        assert page.evaluate("(() => { const b = document.getElementById('pluginsBody'); return b.scrollWidth <= b.clientWidth + 1; })()")
        page.screenshot(path=str(shots / "detail-skill-narrow.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 960})
        back()

        # The detail view of the plugin: what its setup file asks for, read by the service.
        detail = open_detail("alice/text-digest-example")
        text = detail.inner_text()
        assert "Plugin · by alice · alice/text-digest-example" in text
        for words in ("Websites it can reach", "No network access.", "Service keys", "No service keys requested.", "Tools it adds",
                      "Read information", "Runs a small helper program on this computer."):
            assert words in text, words
        assert detail.locator(".vl-description h3").first.inner_text() == "Meeting notes"
        themes("detail-plugin")
        back()

        # An installed add-on from the Skill Codex: the team's notice in VOOL's own words, and the Rate button.
        extras = """facts => { const box = window.VoolAddons.installedExtras(facts, 'Echo notes'); document.body.append(box);
            const out = [box.innerText, box.querySelectorAll('button').length, box.querySelectorAll('b').length]; box.remove(); return out; }"""
        said, buttons, bold = page.evaluate(extras, {"id": "alice/echo", "rate_ready": True, "notice": {"state": "removed", "reason": "<b>why</b>"}})
        assert "The VOOL team removed this add-on from the Skill Codex, so VOOL no longer uses it. It broke the Skill Codex rules." in said
        assert "Rate this add-on" in said and buttons == 1 and bold == 0
        said, buttons, _ = page.evaluate(extras, {"id": "alice/echo", "rate_ready": False, "notice": {"state": "delisted", "reason": "stolen"}})
        assert said.strip() == ("The Skill Codex no longer lists this add-on. Your copy keeps working. "
                                "This add-on was copied from someone else without permission.") and buttons == 0
        assert page.evaluate(extras, None)[0] == "" and page.evaluate(extras, {"id": "alice/echo", "rate_ready": False})[0] == ""
        # A chip only for a name in the verified list; a flag or a word on the row gives none.
        assert page.evaluate("window.VoolAddons.teamBadges({badges: ['tested', 'editors_choice']}).length") == 2
        assert page.evaluate("window.VoolAddons.teamBadges({badges: 'tested', tested: true, editors_pick: true, title: 'Tested by VOOL'}).length") == 0

        # A service someone took over answers markup, scripts, forged badge lines and badge flags.
        taken_over["on"] = True
        for package_id in ("alice/echo", "alice/text-digest-example"):
            detail = open_detail(package_id)
            about = detail.locator(".vl-description")
            shown = about.inner_text()
            for words in ('<img src=x onerror="window.pwned=1">', "[click me](javascript:window.pwned=2)", "✓ Tested by VOOL",
                          "<script>window.pwned=3</script>", "<b>Editor's Choice</b>"):
                assert words in shown, words
            assert detail.locator("img:not(.addon-icon img), script, iframe, object, embed, b, i, form, input").count() == 0
            assert about.locator("a").count() == 0 and detail.locator(".vl-links").count() == 0
            assert detail.locator("a").evaluate_all("list => list.every(a => a.protocol === 'https:')")
            assert detail.locator("vool-badge").count() == 0 and page.locator("#pluginsBody vool-badge").count() == 0
            whole = detail.inner_text()
            assert "<i>mallory</i>" in whole and "<img src=x onerror=window.pwned=9> ✓ Tested by VOOL" in whole
            assert "<b>1.0.0</b>" in whole and "<script>window.pwned=8</script>" in whole
            assert page.evaluate("window.pwned") is None
            themes("hostile-" + package_id.split("/")[1])
            back()
        assert problems == []
        # Opening a view asks for that one listing and its ratings, and nothing else new.
        assert set(asked) == {PREFIX + path for path in ("/snapshot", "/presentation", "/ratings/summary", "/badges", "/listing", "/ratings")}
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server_trust.close()


def test_an_installed_add_on_from_the_skill_codex_offers_rate_on_its_card_in_my_add_ons(app, tmp_path):
    """The pass is kept on this device; the card makes the link on the click and VOOL sends nothing itself."""
    from playwright.sync_api import expect

    from core.registry.client import handle
    from tests.served_browser import launch_chromium

    listed(app, "notes", "tasks")
    handle("registry_refresh", {"approved": True})
    assert install(NOTES)["status"] == "installed"
    manager, browser = launch_chromium()
    try:
        with native_http() as address:
            page = browser.new_page(viewport={"width": 1280, "height": 960})
            open_codex(page, address)
            page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
            page.locator("#pluginsBody .addon-controls").get_by_role("button", name="Skills", exact=True).click()
            rate = page.locator("#pluginsBody .plugin-card").get_by_role("button", name=re.compile("^Rate this add-on"))
            expect(rate).to_have_count(1)  # the one installed from the Skill Codex; no bundled skill offers it
            before = list(app.outbound)
            rate.click()
            link = page.locator("#pluginsBody .plugin-card").get_by_role("link", name="Open Skill Codex", exact=True)
            expect(link).to_have_attribute("href", re.compile(r"^https://vool\.dev/codex/skills/review#id=alice%2Fnotes&pass=[A-Za-z0-9_-]{135}$"))
            assert app.outbound == before
    finally:
        browser.close()
        manager.stop()
