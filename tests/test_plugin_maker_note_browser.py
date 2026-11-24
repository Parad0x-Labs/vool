"""A maker who points the Add-ons page at their own folder reads, on the real served page, the sentence
anyone installing would read and under it the field at fault and what to write instead."""
from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from core import plugin_lifecycle as lifecycle
from tests.served_browser import launch_chromium

TEMPLATE = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "template"


def test_the_page_shows_the_sentence_and_the_note_for_the_maker(tmp_path, monkeypatch):
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
    value["vool"]["network"] = ["*.example.com"]
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
        alert = page.get_by_role("alert")
        alert.wait_for()
        assert "lists the websites it uses in a way VOOL doesn" in alert.inner_text()
        note = page.locator(".plugin-maker-note")
        note.wait_for()
        shown = " ".join(note.inner_text().replace("\u2019", "'").split())
        assert shown.startswith("For the plugin's maker: vool.network[0]:") and "no wildcard" in shown
        assert lifecycle.records() == [] and plugin_id
        # A later action that succeeds clears both lines.
        manifest.write_text(json.dumps({**value, "vool": {**value["vool"], "network": []}}, indent=2), encoding="utf-8")
        page.get_by_label("Plugin folder location", exact=True).fill(str(package))
        page.get_by_role("button", name="Review folder", exact=True).click()
        page.get_by_role("dialog", name="Review plugin package", exact=True).wait_for()
        assert page.locator(".plugin-maker-note").count() == 0
        page.screenshot(path=str(tmp_path / "plugin-maker-note.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        lifecycle.reset_for_tests()
