"""Browser gestures call the real owner API, vault and package lifecycle."""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core import plugin_lifecycle as lifecycle
from core.credential_store import get_credential, has_credential
from core.plugin_tools import plugin_scratch_dir
from tests.served_browser import launch_chromium
from tests.test_plugin_manager_v1 import package


def test_owner_installs_reviews_connects_disables_updates_and_removes(package, tmp_path):
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    runtime = RuntimeServices(display_name="VOOL")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, content_type="application/json"):
            self.send_response(status)
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
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if self.path != "/api/addons":
                return self.send(200, b"{}")
            calls.append(body["action"])
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime,
                                   model_name="fixture", workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.send(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.evaluate("window.VoolPageActions.openSkills()")
        page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
        page.locator("#pluginsBody").get_by_role("button", name="Plugins", exact=True).click()
        page.get_by_text("No plugins installed.", exact=True).wait_for()
        assert calls == [] and lifecycle.records() == []
        page.get_by_label("Plugin folder location", exact=True).fill(str(package))
        page.get_by_role("button", name="Review folder", exact=True).click()
        review = page.get_by_role("dialog", name="Review plugin package", exact=True)
        review.get_by_text("Not reviewed by the VOOL store", exact=True).wait_for()
        assert lifecycle.records() == []
        review.get_by_role("button", name="Install and keep disabled", exact=True).click()
        card = page.locator('[data-plugin-id="sample-provider"]')
        card.get_by_text("Disabled", exact=True).wait_for()
        assert not lifecycle.is_available("sample-provider")
        card.get_by_role("button", name="Review access and enable", exact=True).click()
        consent = page.get_by_role("dialog", name="Review plugin access", exact=True)
        assert "api.example.org" in consent.inner_text()
        assert "Your service key" in consent.inner_text()
        consent.get_by_role("button", name="Keep disabled", exact=True).click()
        assert not lifecycle.is_available("sample-provider")
        value = secrets.token_urlsafe(32)
        card.get_by_label("Your service key", exact=True).fill(value)
        card.get_by_role("button", name="Save key", exact=True).click()
        card.get_by_text("Key saved.", exact=True).wait_for()
        assert get_credential("plugin.sample-provider.access") == value
        assert value not in page.locator("body").inner_text()
        assert card.get_by_label("Your service key", exact=True).input_value() == ""
        card.get_by_role("button", name="Review access and enable", exact=True).click()
        consent.get_by_role("button", name="Accept and enable", exact=True).click()
        card.get_by_text("Enabled", exact=True).wait_for()
        assert lifecycle.is_available("sample-provider")
        card.get_by_role("button", name="Disable", exact=True).click()
        card.get_by_text("Disabled", exact=True).wait_for()
        assert not lifecycle.is_available("sample-provider")
        (package / "LICENSE").write_text("Updated package licence\n")
        page.get_by_label("Plugin folder location", exact=True).fill(str(package))
        page.get_by_role("checkbox", name="Review an update to an installed plugin", exact=True).check()
        page.get_by_role("button", name="Review folder", exact=True).click()
        review.get_by_role("button", name="Install and keep disabled", exact=True).click()
        card.get_by_text("Disabled", exact=True).wait_for()
        assert not lifecycle.is_available("sample-provider")
        target = Path(lifecycle.record_for("sample-provider").root)
        scratch = plugin_scratch_dir("sample-provider")
        scratch.mkdir(parents=True)
        (scratch / "output.txt").write_text("Owned working file")
        card.get_by_role("button", name="Uninstall", exact=True).click()
        removal = page.get_by_role("dialog", name="Uninstall plugin", exact=True)
        assert removal.get_by_role("checkbox", name="Also remove saved keys", exact=True).is_checked()
        removal.get_by_role("button", name="Uninstall", exact=True).click()
        page.get_by_text("No plugins installed.", exact=True).wait_for()
        assert not target.exists() and not scratch.exists()
        assert not has_credential("plugin.sample-provider.access")
        assert lifecycle.record_for("sample-provider").stage == "uninstalled"
        assert errors == []
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(5)
