"""VOOL's own skills are listed first: the Browse directory, a pasted repository and the Skill Codex tab.

Only VOOL's own repository and the team's reserved publisher name earn that place; a look-alike
owner or namespace does not.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from core import addon_catalog, addon_discovery, addon_store
from tests.registry.test_skill_codex_authority import creator, draft
from tests.registry.test_skill_codex_github_signer import app_rows, run, team_steps
from tests.registry.test_skill_codex_github_signer import world as world
from tests.test_addon_store import rig as rig

OWN = "https://github.com/Parad0x-Labs/vool-skills"
REF = "a" * 40


def _ready(ecosystem):
    return next(e for e in addon_catalog.CATALOG if e.get("ecosystem", e["publisher"]) == ecosystem and e.get("import_ready"))


def _entries():
    """Two shipped entries, then one from VOOL's own repository and one from a look-alike owner, both last."""
    anthropic, superpowers = copy.deepcopy(_ready("Anthropic")), copy.deepcopy(_ready("Superpowers"))
    own = dict(copy.deepcopy(superpowers), id="github-" + "1" * 24, name="zz-own-skill", skill_name="zz-own-skill",
               repository=OWN, ref=REF, publisher="VOOL", ecosystem="VOOL")
    lookalike = dict(copy.deepcopy(superpowers), id="github-" + "2" * 24, name="aa-lookalike", skill_name="aa-lookalike",
                     repository="https://github.com/vool/vool-skills", ref=REF, publisher="vool", ecosystem="vool")
    return [anthropic, superpowers, own, lookalike]


def test_browse_catalogue_lists_vool_skills_first_and_only_from_its_own_repository(rig, monkeypatch):
    entries = _entries()
    monkeypatch.setattr(addon_store, "CATALOG", entries)
    rows = addon_store.catalog()["entries"]
    assert [row["id"] for row in rows] == [entries[2]["id"], entries[0]["id"], entries[1]["id"], entries[3]["id"]]
    assert [row["first_party"] for row in rows] == [True, False, False, False]


def test_a_pasted_repository_is_named_vool_only_when_it_is_vools_own():
    own = addon_discovery.listing("Parad0x-Labs/vool-skills", REF, "skills/writing/email-reply/SKILL.md")
    assert (own["publisher"], own["ecosystem"]) == ("VOOL", "VOOL") and addon_discovery.is_own(own)
    assert addon_discovery.is_own(addon_discovery.listing("parad0x-labs/VOOL-skills", REF, "a/SKILL.md"))
    other = addon_discovery.listing("vool/vool-skills", REF, "skills/writing/email-reply/SKILL.md")
    assert (other["publisher"], other["ecosystem"]) == ("vool", "vool") and not addon_discovery.is_own(other)


def test_skill_codex_lists_the_teams_skills_first_even_above_an_editors_pick(world):
    _, token, csrf = creator(world, "101", "alice")
    draft(world, token, csrf, "alice/notes")
    run(world)
    team_steps(world, "team vool-team 202\neditors-pick alice/notes\n")
    run(world)
    _, token, csrf = creator(world, "202", "bob")
    draft(world, token, csrf, "vool-team/meeting-notes")
    assert run(world)["admitted"] == ["vool-team/meeting-notes 1.0.0"]
    rows = app_rows(world)
    assert list(rows) == ["vool-team/meeting-notes", "alice/notes"]
    assert rows["alice/notes"]["editors_pick"] is True and rows["vool-team/meeting-notes"]["editors_pick"] is False


def test_browse_shows_the_vool_collection_and_its_cards_first(rig, monkeypatch, tmp_path):
    from core import credential_store
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get
    from tests.served_browser import launch_chromium

    monkeypatch.setattr(credential_store, "credential_is_indexed", lambda name: True)
    monkeypatch.setattr(addon_store, "CATALOG", _entries())
    runtime = RuntimeServices(display_name="VOOL")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, code, data, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/chat":
                return self.send(200, render_vool_chat_html().encode(), "text/html")
            if path in {"/api/addons", "/api/plugins", "/api/intake/providers"}:
                result = dispatch_get(path=path, query={}, runtime=runtime, model_name="fixture", client_host="127.0.0.1")
                return self.send(result.status, result.body)
            return self.send(200, b"{}")
        def do_POST(self):
            return self.send(200, json.dumps({}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="Browse", exact=True).click()
        page.get_by_role("button", name="View add-on").first.wait_for()
        assert page.locator(".addon-collections strong").all_inner_texts()[0] == "VOOL"
        titles = page.locator("#pluginsBody .addon-card h3").all_inner_texts()
        assert titles[0] == "Zz own skill" and "Aa lookalike" in titles[1:]
        page.locator("#addonNav").get_by_role("button", name="Browse", exact=True).click()
        page.locator("select[aria-label='Sort']").select_option("category")
        assert page.locator("#pluginsBody .addon-card h3").all_inner_texts()[0] == "Zz own skill"
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
