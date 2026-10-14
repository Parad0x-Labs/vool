"""The served Settings page, driven in a real browser: the guided X setup from "Connect X" to a saved,
tested and removed account. X is the loopback fixture from test_x_guided_setup."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_x_guided_setup import BEARER, KEYS, FakeX


def test_connect_x_with_both_paths_then_test_and_remove(vault_home, monkeypatch):
    from core import credential_store, x_setup
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    runtime = RuntimeServices(display_name="X setup fixture")
    errors: list[str] = []
    with FakeX() as fake:
        monkeypatch.setattr(x_setup, "API_ORIGIN", fake.url)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def send(self, res):
                self.send_response(res.status); self.send_header("Content-Type", res.content_type)
                self.end_headers(); self.wfile.write(res.body)

            def do_GET(self):
                u = urlsplit(self.path)
                if u.path == "/settings":
                    self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                    self.wfile.write(render_vool_settings_html().encode()); return
                self.send(dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime, model_name="fixture", client_host="127.0.0.1"))

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or "{}")
                self.send(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers), runtime=runtime,
                                        model_name="fixture", client_host="127.0.0.1", workspace_root_provider=lambda: str(vault_home)))

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        manager, browser = launch_chromium()
        try:
            page = browser.new_page(viewport={"width": 1120, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}/settings#keys")
            page.get_by_role("button", name="Connect X", exact=True).click()
            dialog = page.locator("dialog.x-setup-dialog")
            dialog.wait_for()
            assert fake.requests == []  # opening the guide asks nothing

            dialog.get_by_role("button", name="Research public posts (Bearer Token)", exact=True).click()
            assert "Keys and tokens" in dialog.inner_text()
            dialog.get_by_label("Bearer Token", exact=True).fill(BEARER)
            dialog.get_by_role("button", name="Check and save", exact=True).click()
            dialog.locator(".x-setup-state").filter(has_text="it cannot post").wait_for()
            assert [r["path"] for r in fake.requests] == ["/2/users/by/username/XDevelopers"]

            dialog.get_by_role("button", name="Back", exact=True).click()
            dialog.get_by_role("button", name="I have my keys (my X account)", exact=True).click()
            for label, name in (("Consumer Key (API Key)", "api_key"), ("Consumer Key Secret (API Key Secret)", "api_secret"),
                                ("Access Token", "access_token"), ("Access Token Secret", "access_token_secret")):
                dialog.get_by_label(label, exact=True).fill(KEYS[name])
            dialog.get_by_role("button", name="Check my X account", exact=True).click()
            dialog.locator(".x-setup-state").filter(has_text="@fixture_owner").wait_for()
            assert "access_token" not in json.loads(credential_store.get_credential("x.api.default"))  # not saved before confirm
            assert dialog.get_by_label("Access Token Secret", exact=True).input_value() == ""
            dialog.get_by_role("button", name="Save this X account", exact=True).click()
            dialog.locator(".x-setup-state").filter(has_text="Connected as @fixture_owner").wait_for()
            saved = json.loads(credential_store.get_credential("x.api.default"))
            assert saved["bearer_token"] == BEARER and saved["access_token"] == KEYS["access_token"]
            assert len(fake.requests) == 2
            dialog.get_by_role("button", name="Close", exact=True).click()

            test_button = page.locator("button.key-test[data-provider=default]")
            test_button.wait_for()
            test_button.click()
            page.locator(".key-test-state[data-provider=default]").filter(has_text="accepted").wait_for()
            assert [r["path"] for r in fake.requests[2:]] == ["/2/users/by/username/XDevelopers", "/2/users/me"]
            assert all(r["method"] == "GET" for r in fake.requests)
            page.on("dialog", lambda d: d.accept())
            row = page.locator(".inline").filter(has=test_button)
            row.get_by_role("button", name="Remove", exact=True).click()
            test_button.wait_for(state="detached")
            assert not credential_store.has_credential("x.api.default")
            assert not errors, errors
        finally:
            browser.close(); manager.stop(); server.shutdown(); server.server_close()
