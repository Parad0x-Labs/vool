"""On a Mac a plugin's programs can see how other running apps were started, and no confinement rule stops
that. Before the person turns a plugin on, the real served review sheet says so; for a plugin that can
also reach the internet it says plainly that keys can be there and that the plugin can send what it sees."""
from __future__ import annotations

import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core import plugin_lifecycle as lifecycle
from tests.served_browser import launch_chromium

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "template"
WITH_INTERNET = ("can see the start-up settings of other apps you run. Those can hold keys, and this plugin can "
                 "send what it sees to the sites under Network access.")
WITHOUT = "can see some settings of other apps you run, like any app can."


@pytest.mark.skipif(sys.platform != "darwin", reason="the note is about macOS")
@pytest.mark.parametrize("hosts,said,unsaid", [(["api.example.org"], WITH_INTERNET, WITHOUT), ([], WITHOUT, WITH_INTERNET)])
def test_the_review_sheet_says_what_a_plugin_can_see_on_a_mac(tmp_path, monkeypatch, hosts, said, unsaid):
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "profile"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "installed"))
    monkeypatch.setenv("VOOL_PLUGIN_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    lifecycle.reset_for_tests()
    package = tmp_path / "source"
    shutil.copytree(TEMPLATE, package)
    manifest = package / ".codex-plugin" / "plugin.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["vool"]["network"] = hosts
    manifest.write_text(json.dumps(value, indent=2), encoding="utf-8")
    plugin_id = value["vool"]["id"]
    runtime = RuntimeServices(display_name="VOOL")

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
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime,
                                   model_name="fixture", workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.send(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.evaluate("window.VoolPageActions.openSkills()")
        page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
        page.locator("#pluginsBody").get_by_role("button", name="Plugins", exact=True).click()
        page.get_by_label("Plugin folder location", exact=True).fill(str(package))
        page.get_by_role("button", name="Review folder", exact=True).click()
        review = page.get_by_role("dialog", name="Review plugin package", exact=True)
        review.get_by_role("button", name="Install and keep disabled", exact=True).click()
        card = page.locator(f'[data-plugin-id="{plugin_id}"]')
        card.get_by_role("button", name="Review access and enable", exact=True).click()
        consent = page.get_by_role("dialog", name="Review plugin access", exact=True)
        consent.wait_for()
        shown = " ".join(consent.inner_text().replace("’", "'").split())
        assert said in shown and unsaid not in shown
        assert "Your plugin keys are never put there." in shown
        assert not lifecycle.is_available(plugin_id), "reading the sheet turns nothing on"
        page.screenshot(path=str(tmp_path / "plugin-mac-note.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        lifecycle.reset_for_tests()
