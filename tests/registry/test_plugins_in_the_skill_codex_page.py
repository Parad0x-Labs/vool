"""The Skill Codex tab says which rows are plugins, and installs one through the plugin review sheet, switched off.

The real served page in Chromium against the real service; only the app's release identity is the test's.
"""
import json
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
from core.registry.trust import TrustedCatalog
from registry_service.api import create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, trust_fixture
from tests.registry.test_skill_codex import catalog_of

TEMPLATE = Path(__file__).resolve().parents[2] / "examples" / "plugins" / "template"


def _plugin_package(tmp_path) -> bytes:
    source = tmp_path / "maker" / "text-digest-example"
    shutil.copytree(TEMPLATE, source)
    info = {"id": "alice/text-digest-example", "kind": "plugin", "version": "1.0.0", "title": "Text digest example",
            "summary": "Compute the SHA-256 digest of text you give it.", "category": "productivity",
            "subcategory": "notes-summaries", "license": "MIT", "languages": ["any"],
            "source": {"repository": "alice/plugins", "commit": "b" * 40, "path": "text-digest-example"}}
    (source / "registry.json").write_text(json.dumps(info))
    return pack(source, info, tmp_path / "pack-plugin")


def test_a_plugin_row_is_labelled_and_installs_through_the_plugin_sheet_switched_off(tmp_path, monkeypatch):
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
    skill, _, _ = artifact_fixture(tmp_path)
    plugin = _plugin_package(tmp_path)
    policy, keys, trust_root = trust_fixture()
    server_trust = TrustedCatalog(trust_root, tmp_path / "server/trust", now=NOW)
    server_trust.accept(catalog_of([(skill, NOW), (plugin, NOW)], policy, keys), now=NOW)
    store = Store(tmp_path / "server/accounts")
    for raw in (skill, plugin):
        store.publish(server_trust, inspect(raw)["metadata"]["id"], "1.0.0", TARGET, raw, now=NOW)
    service = TestClient(create_app(store, Auth(store, "https://registry.test", []), server_trust, TARGET, clock=lambda: NOW),
                         base_url="https://registry.test")

    def request(url, *, limit):
        response = service.get(url)
        assert response.status_code == 200 and len(response.content) <= limit
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
        cards = page.locator("#pluginsBody .addon-card")
        cards.first.get_by_role("button", name="Download for inspection", exact=True).wait_for()
        lines = {card.locator("h3").inner_text(): card.locator("small").first.inner_text() for card in cards.all()}
        assert lines["Text digest example"].startswith("Plugin · alice/text-digest-example · ")
        assert lines["alice/echo"].startswith("Skill · ")

        card = cards.filter(has_text="Text digest example")
        card.get_by_role("button", name="Download for inspection", exact=True).click()
        sheet = page.get_by_role("dialog", name="Review plugin package")
        sheet.wait_for()
        text = sheet.inner_text()
        assert "Text digest example" in text and "Published in the Skill Codex by alice." in text
        assert "That does not prove they are safe." in text
        assert "No network access." in text and "No service keys requested." in text
        assert "bin/server" in text and "can run as a program" in text and ".codex-plugin/plugin.json" in text
        assert "It is installed switched off." in text
        assert page.get_by_role("button", name="Install and enable", exact=True).count() == 0
        assert not plugin_lifecycle.records()  # nothing installed by looking

        sheet.get_by_role("button", name="Install and keep disabled", exact=True).click()
        page.wait_for_function("document.querySelector('#addonNav button[aria-pressed=true]')?.textContent === 'My add-ons'")
        record, = plugin_lifecycle.records()
        assert (record.plugin_id, record.source, record.enabled) == (
            "text-digest-example", "registry:alice/text-digest-example", False)
        assert not plugin_lifecycle.is_available("text-digest-example")
        assert problems == []
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server_trust.close()
        plugin_lifecycle.reset_for_tests()
        plugin_catalog.reset_storage_state()
