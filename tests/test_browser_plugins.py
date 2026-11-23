"""Browser plugins: a real plugin package, the real plugin manager, a real engine, real pages.

A browser plugin is a VOOL plugin that carries an ordinary browser extension. These checks
follow one from a folder on disk to a page in the window, by the same door the Plugins page
uses, and ask what a person would ask: does it run only once I enabled it, does switching it
off stop it at once, does a private window stay clean, and is a plugin that asks for too
much turned away with a sentence I can read.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core import plugin_lifecycle as lifecycle
from core.i18n.catalog import catalog_for
from core.plugin_manifest import BROWSER_REFUSED_ACCESS
from core.vool_browser import desk as desk_module
from core.vool_browser import extensions, live
from core.web.api.addon_api import handle_addon_post
from tests.test_desk_browser import _quiet_test_engine, _wait

HEADERS = {"Content-Type": "application/json"}


def door(**body):
    """The Plugins page's own request, as the local app sends it."""
    return handle_addon_post(body, HEADERS, "127.0.0.1")


@pytest.fixture
def site():
    """A page with an advert: a script the page fetches, and a part the page marks as one."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            if self.path.startswith("/advert.js"):
                self.send_header("content-type", "text/javascript")
                self.end_headers()
                self.wfile.write(b"document.title='advert ran'")
                return
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>News</title><h1>News</h1><div id=box class=advert-banner>Buy now</div>"
                             b"<script src='/advert.js'></script>")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    return tmp_path / "profile"


@pytest.fixture
def browser(home, monkeypatch):
    if not desk_module.engine_binary():
        pytest.skip("no Chromium-family engine on this machine")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("the engine's sandbox does not start for root")
    if os.name == "posix" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and os.uname().sysname != "Darwin":
        pytest.skip("a window needs a display")
    monkeypatch.setattr(live, "available", lambda: True)
    _quiet_test_engine(monkeypatch, home)
    # The product's own desk, so the plugin manager reaches the open window the way it does in the app.
    item = desk_module.desk()
    yield item
    item.stop()
    with desk_module._LOCK:
        for key in [key for key, value in desk_module._DESKS.items() if value is item]:
            del desk_module._DESKS[key]


def blocker(folder: Path, *, extension: dict | None = None, plugin_id: str = "quiet-pages", version: str = "1.0.0") -> Path:
    """An advert blocker as a person would get it: the plugin's facts and a standard extension."""
    (folder / ".codex-plugin").mkdir(parents=True)
    (folder / "extension").mkdir()
    (folder / "LICENSE").write_text("MIT License\n")
    (folder / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": plugin_id, "version": version, "vool": {
            "id": plugin_id, "version": version, "name": "Quiet pages", "publisher": "Example",
            "description": "Stops adverts before they load.", "homepage": "https://example.org/",
            "license": "MIT", "kind": ["browser"], "network": [], "credentials": {},
            "browser": {"extension": "extension"}}}))
    manifest = {
        "manifest_version": 3, "name": "Quiet pages", "version": version,
        "permissions": ["declarativeNetRequest"], "host_permissions": ["<all_urls>"],
        "declarative_net_request": {"rule_resources": [{"id": "adverts", "enabled": True, "path": "rules.json"}]},
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["page.js"], "run_at": "document_start"}],
    }
    manifest.update(extension or {})
    (folder / "extension" / "manifest.json").write_text(json.dumps(manifest))
    (folder / "extension" / "rules.json").write_text(json.dumps([{
        "id": 1, "priority": 1, "action": {"type": "block"},
        "condition": {"urlFilter": "/advert.js", "resourceTypes": ["script"]}}]))
    (folder / "extension" / "page.js").write_text("document.documentElement.dataset.quiet = 'on';\n")
    return folder


def install(folder: Path, *, update: bool = False) -> dict:
    status, review = door(action="plugin_prepare", path=str(folder), update=update)
    assert status == 200, review
    status, result = door(action="plugin_install", review_id=review["review_id"], digest=review["digest"], accepted=True)
    assert status == 200, result
    return result["plugin"]


def enable(info: dict) -> None:
    status, result = door(action="plugin_enable", id=info["id"], accepted=True, approval_digest=info["approval_digest"])
    assert status == 200 and result.get("enabled") is not False, result


def look(browser, base: str, name: str, hits: list[str], *, private: bool = False) -> dict:
    """Open a fresh page and say what happened on it: was the advert fetched, did the plugin's script run."""
    hits.clear()
    browser.open(f"{base}/{name}", private=private)
    assert _wait(lambda: f"/{name}" in hits)
    window = browser._private[-1] if private else browser._main

    async def read():
        page = next(p for p in window.context.pages if p.url.endswith("/" + name))
        await page.wait_for_load_state("load")
        return {"script_ran": await page.evaluate("document.documentElement.dataset.quiet || ''"),
                "title": await page.title(),
                "advert_shown": await page.evaluate("getComputedStyle(document.getElementById('box')).display") != "none"}

    seen = browser._call(read())
    return {**seen, "advert_fetched": any(path.startswith("/advert.js") for path in hits)}


UNTOUCHED = {"script_ran": "", "title": "advert ran", "advert_shown": True, "advert_fetched": True}
BLOCKED = {"script_ran": "on", "title": "News", "advert_shown": True, "advert_fetched": False}


def test_an_advert_blocker_runs_only_while_the_person_has_it_enabled(browser, site, tmp_path):
    base, hits = site
    status, review = door(action="plugin_prepare", path=str(blocker(tmp_path / "source")))
    assert status == 200
    # The review says what the extension inside asks for, read from the package itself.
    assert review["plugin"]["kind"] == ["browser"]
    assert review["plugin"]["browser"] == {"name": "Quiet pages", "version": "1.0.0", "every_site": True,
                                           "access": ["declarativeNetRequest"], "sites": ["<all_urls>"]}
    assert look(browser, base, "before", hits) == UNTOUCHED

    status, result = door(action="plugin_install", review_id=review["review_id"], digest=review["digest"], accepted=True)
    info = result["plugin"]
    assert status == 200 and info["enabled"] is False
    # Installed is not enabled: nothing is loaded yet.
    assert look(browser, base, "installed", hits) == UNTOUCHED and browser.status()["plugins"] == []

    enable(info)
    # Enabled: the open window has it at once, no restart.
    assert browser.status()["plugins"] == ["quiet-pages"]
    assert look(browser, base, "enabled", hits) == BLOCKED
    # A private window never gets a plugin.
    assert look(browser, base, "private", hits, private=True) == UNTOUCHED

    # The engine keeps its own index beside the extension. That lands in VOOL's working copy;
    # the package the person approved is not written to and still matches what was approved.
    sealed = Path(lifecycle.record_for("quiet-pages").root)
    copies = list(extensions._working_root().iterdir())
    assert [copy.name.rsplit("-", 1)[0] for copy in copies] == ["quiet-pages"]
    assert sorted(path.name for path in (sealed / "extension").iterdir()) == ["manifest.json", "page.js", "rules.json"]
    assert lifecycle.is_available("quiet-pages")

    assert door(action="plugin_disable", id="quiet-pages")[0] == 200
    # Switched off: gone from the open window at once, and its working copy with it.
    assert browser.status()["plugins"] == []
    assert look(browser, base, "disabled", hits) == UNTOUCHED
    assert list(extensions._working_root().iterdir()) == []


def test_enabled_plugins_come_back_after_a_restart_and_a_removed_one_does_not(browser, site, tmp_path):
    base, hits = site
    info = install(blocker(tmp_path / "source"))
    enable(info)
    # Enabled while the browser was closed: the next start loads it.
    assert look(browser, base, "first", hits) == BLOCKED
    browser.stop()
    assert look(browser, base, "again", hits) == BLOCKED
    browser.stop()

    # A working copy changed while the browser was closed is not what gets loaded:
    # the copy is made again from the approved package.
    copy = next(extensions._working_root().iterdir())
    (copy / "page.js").write_text("document.documentElement.dataset.quiet = 'changed';\n")
    assert look(browser, base, "restored", hits) == BLOCKED
    browser.stop()

    # The approved package itself changed on disk: the plugin no longer runs at all.
    sealed = Path(lifecycle.record_for("quiet-pages").root) / "extension" / "page.js"
    sealed.chmod(0o644)
    sealed.write_text("document.documentElement.dataset.quiet = 'changed';\n")
    assert look(browser, base, "tampered", hits) == UNTOUCHED
    assert browser.status()["plugins"] == []
    browser.stop()

    status, result = door(action="plugin_uninstall", id="quiet-pages", remove_key=True)
    assert status == 200, result
    assert look(browser, base, "removed", hits) == UNTOUCHED
    assert list(extensions._working_root().iterdir()) == []


def test_an_update_the_person_approved_replaces_the_running_version(browser, site, tmp_path):
    base, hits = site
    enable(install(blocker(tmp_path / "one")))
    assert look(browser, base, "one", hits) == BLOCKED
    newer = blocker(tmp_path / "two", version="1.1.0")
    (newer / "extension" / "page.js").write_text("document.documentElement.dataset.quiet = 'newer';\n")
    info = install(newer, update=True)
    # An update is installed disabled until its access is accepted again: the old version stops.
    assert look(browser, base, "between", hits) == UNTOUCHED
    enable(info)
    assert look(browser, base, "two", hits) == {**BLOCKED, "script_ran": "newer"}
    assert len(list(extensions._working_root().iterdir())) == 1


REFUSALS = {
    "programs outside the browser": ({"permissions": ["nativeMessaging"]},
        "This browser plugin asks for access no browser plugin may have, such as saved sign-ins, the browser's connection or programs outside the browser. It was not installed."),
    "the browser's connection, asked for later": ({"optional_permissions": ["proxy"]},
        "This browser plugin asks for access no browser plugin may have, such as saved sign-ins, the browser's connection or programs outside the browser. It was not installed."),
    "saving files by itself": ({"permissions": ["declarativeNetRequest", "downloads"]},
        "This browser plugin asks for access no browser plugin may have, such as saved sign-ins, the browser's connection or programs outside the browser. It was not installed."),
    "the browser's own safety settings": ({"permissions": ["contentSettings"]},
        "This browser plugin asks for access no browser plugin may have, such as saved sign-ins, the browser's connection or programs outside the browser. It was not installed."),
    "replacing the person's search": ({"chrome_settings_overrides": {"search_provider": {"name": "x", "search_url": "https://x.example/?q={searchTerms}", "is_default": True}}},
        "This browser plugin asks for access no browser plugin may have, such as saved sign-ins, the browser's connection or programs outside the browser. It was not installed."),
    "the older extension format": ({"manifest_version": 2},
        "This browser plugin uses an older extension format that VOOL's browser does not load. Ask its maker for an update."),
    "updates from outside VOOL": ({"update_url": "https://updates.example.org/x.xml"},
        "This browser plugin would fetch its own updates from outside VOOL. It was not installed."),
    "a broken extension": ({"name": 7},
        "This browser plugin has no working extension inside it. Ask its maker to fix it."),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_browser_plugin_that_asks_for_too_much_is_turned_away_at_review_in_plain_words(home, tmp_path, case):
    change, sentence = REFUSALS[case]
    status, result = door(action="plugin_prepare", path=str(blocker(tmp_path / "source", extension=change)))
    assert status == 400 and result["message"] == sentence
    # The sentence is one the page can show in the person's language.
    assert result["message_key"] in catalog_for("en").keys
    assert lifecycle.records() == [] and extensions.enabled() == []


def test_every_access_that_reaches_outside_the_browser_is_on_the_refused_list():
    for name in ("nativeMessaging", "debugger", "proxy", "vpnProvider", "management", "privacy",
                 "passwordsPrivate", "downloads", "certificateProvider", "webAuthenticationProxy",
                 "contentSettings", "desktopCapture"):
        assert name in BROWSER_REFUSED_ACCESS


def test_a_plugin_that_names_no_extension_folder_or_a_folder_outside_itself_is_refused(home, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "manifest.json").write_text(json.dumps({"manifest_version": 3, "name": "x", "version": "1"}))
    for index, folder in enumerate(("missing", "../elsewhere", str(outside))):
        source = blocker(tmp_path / f"source{index}")
        raw = json.loads((source / ".codex-plugin" / "plugin.json").read_text())
        raw["vool"]["browser"]["extension"] = folder
        (source / ".codex-plugin" / "plugin.json").write_text(json.dumps(raw))
        status, result = door(action="plugin_prepare", path=str(source))
        assert status == 400 and "no working extension" in result["message"], (folder, result)
    assert lifecycle.records() == []


def test_a_person_makes_their_own_browser_plugin_and_it_runs(browser, site, tmp_path):
    base, hits = site
    status, made = door(action="plugin_browser_starter", name="Calm reading")
    assert status == 200 and made["id"] == "calm-reading"
    folder = Path(made["folder"])
    # Their plugins live beside the installed ones, never inside them, and nothing is installed yet.
    assert folder.parent.name == "my-browser-plugins" and lifecycle.records() == []
    assert sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()) == [
        ".codex-plugin/plugin.json", "README.txt", "extension/manifest.json", "extension/page.js"]
    assert look(browser, base, "before", hits)["advert_shown"] is True

    # The folder goes through the same review and consent as any other plugin.
    status, review = door(action="plugin_prepare", path=str(folder))
    assert status == 200 and review["plugin"]["name"] == "Calm reading" and review["plugin"]["browser"]["every_site"] is True
    status, result = door(action="plugin_install", review_id=review["review_id"], digest=review["digest"], accepted=True)
    enable(result["plugin"])
    # The starter hides what a page marks as an advert.
    assert look(browser, base, "after", hits)["advert_shown"] is False

    # They change the script and review the update: the new version runs.
    (folder / "extension" / "page.js").write_text("document.documentElement.dataset.quiet = 'mine';\n")
    info = install(folder, update=True)
    enable(info)
    assert look(browser, base, "changed", hits)["script_ran"] == "mine"

    # A second plugin of the same name gets its own folder; a name in other letters still works.
    assert door(action="plugin_browser_starter", name="Calm reading")[1]["id"] == "calm-reading-2"
    status, other = door(action="plugin_browser_starter", name="Тихое чтение")
    assert status == 200 and other["id"] == "my-browser-plugin"
    assert door(action="plugin_prepare", path=other["folder"])[1]["plugin"]["name"] == "Тихое чтение"
    # No name is not a plugin.
    status, refused = door(action="plugin_browser_starter", name="   ")
    assert status == 400 and refused["message"] == "Give your browser plugin a short name."


# -- the Plugins page, served by the normal app ------------------------------------------

from tests.test_plugin_services_v1 import https_service  # noqa: E402,F401  (a fixture)


def test_the_plugins_page_makes_reviews_and_groups_a_browser_plugin_in_plain_words(tmp_path, https_service):  # noqa: F811
    from playwright.sync_api import expect

    from tests.served_browser import launch_chromium
    from tests.test_plugin_served_journey import PluginDaemon, accept_access, plugins_page

    daemon = PluginDaemon(tmp_path / "served", https_service).start()
    manager, engine = launch_chromium()
    page = engine.new_page()
    page.set_default_timeout(10000)
    try:
        plugins_page(page, daemon)
        # No browser plugin yet: no group headings.
        assert not page.get_by_role("heading", name="Web browser", exact=True).count()

        # The person makes their own: one name, one button.
        page.get_by_role("heading", name="Make a browser plugin", exact=True).wait_for()
        page.get_by_label("Name of your plugin", exact=True).fill("Calm reading")
        page.get_by_role("button", name="Make the folder", exact=True).click()
        page.get_by_role("status").filter(has_text="Your plugin folder is ready").wait_for()
        folder = Path(page.locator("#pluginLocalFolder").input_value())
        # The folder is already filled in for review, and it is a real plugin on disk.
        assert folder.name == "calm-reading" and (folder / "extension" / "page.js").is_file()
        assert daemon.get("/api/addons")[1]["plugins"] == []

        page.get_by_role("button", name="Review folder", exact=True).click()
        review = page.get_by_role("dialog", name="Review plugin package")
        review.wait_for()
        # What a browser plugin can do is said in plain words before anything is installed.
        expect(review.get_by_role("heading", name="Web browser", exact=True)).to_be_visible()
        expect(review.get_by_text("A private window never loads it.", exact=False)).to_be_visible()
        expect(review.get_by_text("Can read and change every page you open, and can send what it sees to websites.", exact=True)).to_be_visible()
        # A plugin that only lives in the browser is not described as having "no network access".
        assert not review.get_by_text("No network access.", exact=True).count()
        page.screenshot(path=str(tmp_path / "browser-plugin-review.png"), full_page=True)
        page.get_by_role("button", name="Install and keep disabled", exact=True).click()
        card = page.locator('[data-plugin-id="calm-reading"]')
        card.wait_for()
        assert card.get_attribute("data-plugin-group") == "browser"
        expect(page.get_by_role("heading", name="Web browser", exact=True)).to_be_visible()
        accept_access(page, card)
        row = daemon.get("/api/addons")[1]["plugins"][0]
        assert row["id"] == "calm-reading" and row["enabled"] is True and row["kind"] == ["browser"]
        page.screenshot(path=str(tmp_path / "browser-plugin-enabled.png"), full_page=True)

        # A blocker that asks the browser for more says so, each in a sentence.
        blocker(tmp_path / "blocker", extension={"permissions": ["declarativeNetRequest", "tabs", "storage"],
                                                 "host_permissions": ["https://news.example.org/*"],
                                                 "content_scripts": []})
        page.locator("#pluginLocalFolder").fill(str(tmp_path / "blocker"))
        page.get_by_role("button", name="Review folder", exact=True).click()
        review = page.get_by_role("dialog", name="Review plugin package")
        review.wait_for()
        expect(review.get_by_text("Can read and change pages on these sites, and can send what it sees to websites: https://news.example.org/*", exact=True)).to_be_visible()
        expect(review.get_by_text("Block or change what pages load", exact=True)).to_be_visible()
        expect(review.get_by_text("See the addresses and titles of your open tabs", exact=True)).to_be_visible()
        expect(review.get_by_text("Other browser features: storage", exact=True)).to_be_visible()
        page.get_by_role("button", name="Cancel", exact=True).click()

        # A plugin that asks for too much is turned away on the page, in a sentence.
        blocker(tmp_path / "greedy", extension={"permissions": ["nativeMessaging"]})
        page.locator("#pluginLocalFolder").fill(str(tmp_path / "greedy"))
        page.get_by_role("button", name="Review folder", exact=True).click()
        page.get_by_text("This browser plugin asks for access no browser plugin may have", exact=False).wait_for()
        assert [row["id"] for row in daemon.get("/api/addons")[1]["plugins"]] == ["calm-reading"]
    finally:
        engine.close()
        manager.stop()
        daemon.stop()
