"""An unopened registry has no publish gesture, website link or outbound request."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tests.registry.test_skill_publish_handoff import SKILL, install
from tests.registry.test_skill_publish_handoff import home as home

CLOSED = "Skill Codex is not open yet in this version of VOOL"
LOCAL = {"Content-Type": "application/json", "Host": "127.0.0.1:8080", "Origin": "http://127.0.0.1:8080"}


@pytest.mark.parametrize("action", ["registry_refresh", "registry_prepare", "registry_install", "skill_publish"])
def test_missing_anchor_refuses_every_registry_gesture_before_work(home, tmp_path, monkeypatch, action):
    from core.web.api.addon_api import handle_addon_post

    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(tmp_path / "absent"))
    install("meeting-actions", SKILL)
    payload = {"action": action, **({"name": "meeting-actions"} if action == "skill_publish" else {"approved": True})}
    if action == "registry_install":
        payload = {"action": action, "id": "alice/notes", "version": "1.0.0", "accepted": True, "enable": True}
    status, answer = handle_addon_post(payload, LOCAL, "127.0.0.1")
    assert status == 400 and answer["message"] == CLOSED
    assert "url" not in answer


@pytest.mark.parametrize("contents", [b"", b"[]", b"{}", b'{"signed":{},"signatures":[]}', b"not json"])
def test_invalid_anchor_never_opens_publish(home, tmp_path, monkeypatch, contents):
    from core.web.api.addon_api import handle_addon_post

    path = tmp_path / "broken-root.json"
    path.write_bytes(contents)
    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(path))
    install("meeting-actions", SKILL)
    status, answer = handle_addon_post({"action": "skill_publish", "name": "meeting-actions"}, LOCAL, "127.0.0.1")
    assert status == 400 and answer["message"] == CLOSED


def test_served_fresh_profile_and_closed_registry_send_no_registry_requests(home, tmp_path, monkeypatch):
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    monkeypatch.setenv("VOOL_REGISTRY_ROOT_FILE", str(tmp_path / "absent"))
    install("meeting-actions", SKILL)
    runtime, actions, outbound = RuntimeServices(display_name="VOOL"), [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", kind)
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
            value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path != "/api/addons":
                return self.reply(200, b"{}")
            actions.append(value)
            result = dispatch_post(path=self.path, body=value, headers=dict(self.headers), runtime=runtime,
                                   model_name="fixture", workspace_root_provider=lambda: str(tmp_path), client_host="127.0.0.1")
            self.reply(result.status, result.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        context = browser.new_context(viewport={"width": 1280, "height": 960})
        def local_only(route):
            if urlsplit(route.request.url).hostname != "127.0.0.1":
                outbound.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        context.route("**/*", local_only)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
        page.locator("#homeToggle").click()
        page.get_by_role("button", name="Skills & Plugins", exact=True).click()
        page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
        page.locator("#pluginsBody .addon-controls").get_by_role("button", name="Skills", exact=True).click()
        page.locator("#pluginsBody .plugin-card").filter(has=page.locator(".p-name", has_text="Meeting Actions")).wait_for()
        assert page.locator("#pluginsBody .skill-publish-btn").count() == 0
        assert actions == [] and outbound == []
        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
        page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
        page.get_by_text(CLOSED, exact=False).wait_for()
        assert outbound == []
        assert [item["action"] for item in actions] == ["registry_refresh"]
        assert page.get_by_role("link", name="Report this skill", exact=True).count() == 0
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
