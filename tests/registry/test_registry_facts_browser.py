"""The Add-ons page shows a skill's signed languages, needs and tested builds before install."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from core import plugin_lifecycle
from core.registry.artifact import pack
from core.registry.client import Client
from core.registry.trust import TrustedCatalog
from registry_service.api import create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture


@pytest.mark.parametrize("declared", [True, False])
def test_add_ons_page_shows_languages_needs_and_tested_builds_before_install(tmp_path, monkeypatch, declared):
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
    _, root, info = artifact_fixture(tmp_path)
    if declared:
        info = {**info, "languages": ["en", "lt"], "needs": ["search_web", "wallet"]}
    else:
        # A package signed before these fields existed shows no languages or needs rows.
        info = {key: value for key, value in info.items() if key != "languages"}
    raw = pack(root, info, tmp_path / "scratch")
    policy, keys, trust_root = trust_fixture()
    server_trust = TrustedCatalog(trust_root, tmp_path / "server/trust", now=NOW)
    server_trust.accept(catalog_fixture(raw, policy, keys), now=NOW)
    store = Store(tmp_path / "server/accounts")
    store.publish(server_trust, "alice/echo", "1.0.0", TARGET, raw, now=NOW)
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
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
        card = page.locator("#pluginsBody .addon-card").first
        card.get_by_role("button", name="Download for inspection", exact=True).wait_for()

        def facts(scope):
            terms = scope.locator(".addon-facts dt").all_inner_texts()
            return dict(zip(terms, scope.locator(".addon-facts dd").all_inner_texts(), strict=True))

        tested = "VOOL 0.7.1 (macOS Apple Silicon)"
        expected = {"Languages": "en, lt", "Needs": "Web search, VOOL wallet", "Checked for": tested} if declared \
            else {"Checked for": tested}
        assert facts(card) == expected
        card.get_by_role("button", name="Download for inspection", exact=True).click()
        page.get_by_role("button", name="Install and enable", exact=True).wait_for()
        assert facts(page.locator("#pluginsBody")) == expected  # the same facts on the review screen
        assert not plugin_lifecycle.records()  # nothing installed by looking
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server_trust.close()
