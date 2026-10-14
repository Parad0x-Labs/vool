"""The served Settings page, driven in a real browser: Settings > Email > "Add email account" to a saved
account, a refused password, and Remove. The mail server is the loopback fixture from
test_email_add_account (real TLS; the fixture certificate is trusted the same documented way)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from tests._credential_intelligence_support import isolated_home, vault_home
from tests.test_email_add_account import ADDRESS, PASSWORD, SLOT, _trust_fixture_ca, mail


def test_add_an_email_account_from_settings_then_remove_it(mail, monkeypatch, vault_home):
    from core import credential_store
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    runtime = RuntimeServices(display_name="email add fixture")
    errors: list[str] = []
    posts: list[tuple[str, dict]] = []
    leaked: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def send(self, res):
            if PASSWORD.encode() in (res.body or b""):
                leaked.append(self.path)
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
            path = urlsplit(self.path).path
            posts.append((path, {k: ("<set>" if k == "password" else v) for k, v in body.items()}))
            self.send(dispatch_post(path=path, body=body, headers=dict(self.headers), runtime=runtime,
                                    model_name="fixture", client_host="127.0.0.1", workspace_root_provider=lambda: str(vault_home)))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    try:
        page = browser.new_page(viewport={"width": 1120, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}/settings#email")
        page.locator("#emailAccounts").filter(has_text="No email account is set up yet").wait_for()
        page.get_by_role("button", name="Add email account", exact=True).click()
        dialog = page.locator("dialog.email-add-dialog")
        dialog.wait_for()
        password = dialog.get_by_label("Password", exact=True)
        assert password.get_attribute("type") == "password" and password.input_value() == ""
        assert posts == []  # opening the dialog sends nothing

        dialog.get_by_label("Email address", exact=True).fill(ADDRESS)
        password.fill("Not-The-Password-1234")
        dialog.get_by_label("Incoming mail server (IMAP)", exact=True).fill("localhost")
        dialog.get_by_label("Incoming server port", exact=True).fill(str(service.imap_tls_port))
        dialog.get_by_role("button", name="Check and save", exact=True).click()
        dialog.locator(".email-add-state").filter(has_text="did not accept this username and password").wait_for()
        assert password.input_value() == ""  # cleared after the attempt
        assert not credential_store.has_credential(f"email.imap.{SLOT}")

        password.fill(PASSWORD)
        dialog.get_by_label("Outgoing mail server (SMTP), optional", exact=True).fill("localhost")
        dialog.get_by_label("Outgoing server port", exact=True).fill(str(service.smtp_tls_port))
        dialog.get_by_role("button", name="Check and save", exact=True).click()
        dialog.wait_for(state="detached")
        page.locator("#emailAccountsStatus").filter(has_text=f"Saved. VOOL can now use {ADDRESS}.").wait_for()
        row = page.locator(f".email-account[data-email-account='{SLOT}']")
        row.wait_for()
        assert "ready" in row.inner_text()
        assert [p for p, _ in posts] == ["/api/email/accounts/add", "/api/email/accounts/add"]
        assert posts[1][1] == {"address": ADDRESS, "password": "<set>", "imap_host": "localhost", "imap_port": service.imap_tls_port,
                               "replace": False, "smtp_host": "localhost", "smtp_port": service.smtp_tls_port}
        assert credential_store.has_credential(f"email.imap.{SLOT}") and credential_store.has_credential(f"email.smtp.{SLOT}")

        page.on("dialog", lambda d: d.accept())
        row.get_by_role("button", name="Remove", exact=True).click()
        row.wait_for(state="detached")
        page.locator("#emailAccountsStatus").filter(has_text=f"Removed {SLOT}.").wait_for()
        assert posts[-1] == ("/api/email/accounts/remove", {"account": SLOT})
        assert not credential_store.has_credential(f"email.imap.{SLOT}")
        assert not credential_store.has_credential(f"email.smtp.{SLOT}")
        assert leaked == [], leaked
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()
