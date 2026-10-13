"""Real served native UI and installation; public registry/scanner keys are disposable fixtures."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from core import plugin_lifecycle
from core.registry.client import Client
from core.registry.trust import TrustedCatalog
from registry_service.api import create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.audit_helpers import package
from tests.registry.helpers import NOW, TARGET, artifact_fixture, catalog_fixture, trust_fixture


@pytest.mark.parametrize("with_tools", [False, True])
def test_native_registry_inspection_and_explicit_enable_in_served_browser(tmp_path, monkeypatch, with_tools):
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
    raw, _, _ = package(tmp_path, tools=True, permissions=("external_messages",)) if with_tools else artifact_fixture(tmp_path)
    policy, keys, root = trust_fixture()
    server_trust = TrustedCatalog(root, tmp_path / "server/trust", now=NOW)
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
    monkeypatch.setattr(native_client, "configured_client", lambda: Client(root, TARGET, origin="https://registry.test", request=request, now=NOW))
    runtime = RuntimeServices(display_name="VOOL")
    actions = []

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
            actions.append(body.get("action"))
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
        assert not actions and not plugin_lifecycle.records()
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
        page.get_by_role("button", name="Download for inspection", exact=True).click()
        page.get_by_role("button", name="Install and enable", exact=True).wait_for()
        assert not plugin_lifecycle.records()
        assert "native rules" in page.locator("#pluginsBody").inner_text().lower()
        if with_tools:
            page.get_by_text("Exact files, permissions and source", exact=True).click()
            visible = page.locator("#pluginsBody details[open] pre").inner_text()
            for declaration in ("network_send", "external_messages", "subprocess", '"network": true',
                                "env_allowlist", "REGISTRY_PROVIDER_NAME", "credential_bindings", "binding-mail"):
                assert declaration in visible
        page.get_by_role("button", name="Install and enable", exact=True).click()
        page.wait_for_function("document.querySelector('#addonNav button[aria-pressed=true]')?.textContent === 'My add-ons'")
        records = plugin_lifecycle.records()
        assert len(records) == 1 and plugin_lifecycle.is_available(records[0].plugin_id)
        assert actions == ["registry_refresh", "registry_prepare", "registry_install"]
        page.screenshot(path=str(tmp_path / "registry-native-fixture.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
        server_trust.close()
