"""The daily check for removed add-ons on the real served Add-ons page: off until the person ticks it,
nothing posted before they do, and the choice still there after a reload."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from core import plugin_lifecycle
from core.registry import recall

SWITCH = "Check once a day for removed add-ons"


def test_the_person_turns_the_daily_check_on_and_off_from_the_skill_codex_tab(tmp_path, monkeypatch):
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
    plugin_lifecycle.reset_for_tests()
    runtime = RuntimeServices(display_name="VOOL")
    posted = []

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
            posted.append(body)
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime, model_name="fixture",
                                   workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            return self.reply(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()

    def open_tab(page):
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
        return page.get_by_label(SWITCH, exact=True)

    try:
        page = browser.new_page(viewport={"width": 1280, "height": 960})
        switch = open_tab(page)
        switch.wait_for()
        assert not switch.is_checked() and posted == [], "off, and opening the tab posts nothing"
        assert "Nothing about you, your chats or your add-ons is sent." in page.locator("#pluginsBody").inner_text()
        assert recall.status()["enabled"] is False

        switch.check()
        page.get_by_text("It starts once you have an add-on from the Skill Codex.", exact=True).wait_for()
        assert posted == [{"action": "registry_recall", "enabled": True}]
        assert recall.status()["enabled"] is True

        switch = open_tab(page)  # a fresh load reads the saved choice
        switch.wait_for()
        page.wait_for_function("document.querySelector('.registry-recall input').checked === true")
        assert len(posted) == 1

        switch.uncheck()
        page.wait_for_function("document.querySelector('.registry-recall input').checked === false")
        assert posted[-1] == {"action": "registry_recall", "enabled": False} and recall.status()["enabled"] is False
        page.screenshot(path=str(tmp_path / "removed-addon-check.png"), full_page=True)
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        plugin_lifecycle.reset_for_tests()
