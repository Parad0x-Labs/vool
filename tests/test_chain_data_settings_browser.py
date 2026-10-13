"""The served Settings page, driven in a real browser: guided setup, paste, save, test and remove a
chain-data key. The provider is a loopback fixture answering Alchemy's documented shape."""
from __future__ import annotations

import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import FakeProviderServer, isolated_home, vault_home

ALCHEMY_KEY = "alch_fixture_key_0123456789abcd"


def test_guided_setup_save_test_and_remove_in_the_served_settings_page(vault_home, monkeypatch):
    from core import chain_providers as cp
    from core import credential_store
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    runtime = RuntimeServices(display_name="Chain keys fixture")
    errors: list[str] = []
    block = {"jsonrpc": "2.0", "id": 1, "result": {"number": "0x10", "hash": "0x" + "1" * 64}}
    with FakeProviderServer([(200, block)] * 4) as provider:
        patched = dict(cp.CHAIN_PROVIDERS)
        patched["alchemy"] = dataclasses.replace(patched["alchemy"], verify_url=provider.url.rstrip("/") + "/alchemy/eth-mainnet/v2")
        monkeypatch.setattr(cp, "CHAIN_PROVIDERS", patched)

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
            search = page.get_by_role("searchbox", name="Find a provider", exact=True)
            search.wait_for()
            assert page.locator('.key-guide-category[data-category="chain"]').count() == 1
            search.fill("alchemy")
            page.get_by_role("button", name="Alchemy setup steps", exact=True).click()
            dialog = page.locator("dialog.key-setup-dialog")
            dialog.wait_for()
            assert "Networks tab" in dialog.inner_text() and "Robinhood Chain" in dialog.inner_text()
            assert provider.request_count == 0  # guidance is local; nothing was asked
            dialog.get_by_role("button", name="Add key now", exact=True).click()
            assert page.get_by_role("combobox", name="Provider", exact=True).input_value() == "alchemy"
            assert page.locator("button.key-setup-steps").is_visible()
            page.locator(".key-entry-form input[type=password]").fill(ALCHEMY_KEY)
            page.get_by_role("button", name="Save key", exact=True).click()
            page.locator(".key-save-state").filter(has_text="read-only chain lookups in any chat").wait_for()
            assert credential_store.get_credential("chain.data.alchemy") == ALCHEMY_KEY
            assert provider.request_count == 1 and provider.requests[0]["path"] == "/alchemy/eth-mainnet/v2"
            assert page.locator(".key-entry-form input[type=password]").input_value() == ""
            page.reload()
            test_button = page.locator("button.key-test[data-provider=alchemy]")
            test_button.wait_for()
            assert page.get_by_text("Blockchain data", exact=True).first.is_visible()
            test_button.click()
            page.locator(".key-test-state[data-provider=alchemy]").filter(has_text="accepted").wait_for()
            assert provider.request_count == 2
            page.on("dialog", lambda d: d.accept())
            row = page.locator(".inline").filter(has=test_button)
            row.get_by_role("button", name="Remove", exact=True).click()
            test_button.wait_for(state="detached")
            assert not credential_store.has_credential("chain.data.alchemy")
            assert provider.request_count == 2
            assert not errors, errors
        finally:
            browser.close(); manager.stop(); server.shutdown(); server.server_close()
