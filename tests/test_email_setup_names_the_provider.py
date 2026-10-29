"""Settings > Email > Add email account, for a person who has never heard of IMAP.

Measured on 1cbf433 (fresh-user test, 2026-10-29): the dialog was a raw form -- address, username,
password, IMAP server, port, security, SMTP server, port, security -- with `imap.example.com`
placeholders. Typing a Gmail, Outlook or iCloud address filled nothing, and the only help was "Some
providers need an app password". A non-technical person could not finish it.

Driven in a real browser: the provider is recognised from the address (or picked with one tap), its
servers are filled, its own app-password steps and page are shown, and the server fields stay
reachable for anyone who needs them. Nothing is sent to any server here.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def dialog():
    from core.vool_settings_page import render_vool_settings_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get
    from tests.served_browser import launch_chromium

    runtime = RuntimeServices(display_name="email presets fixture")
    posts: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/settings":
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(render_vool_settings_html().encode()); return
            res = dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime, model_name="fixture",
                               client_host="127.0.0.1")
            self.send_response(res.status); self.send_header("Content-Type", res.content_type)
            self.end_headers(); self.wfile.write(res.body)

        def do_POST(self):
            posts.append(self.path)
            self.send_response(500); self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    errors: list[str] = []
    try:
        page = browser.new_page(viewport={"width": 1120, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}/settings#email")
        page.get_by_role("button", name="Add email account", exact=True).click()
        dlg = page.locator("dialog.email-add-dialog")
        dlg.wait_for()
        yield dlg
        assert errors == []
        assert not [p for p in posts if "/api/email/" in p], "choosing a provider must contact nothing"
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()


def _value(dlg, label: str) -> str:
    return dlg.get_by_label(label, exact=True).input_value()


def test_a_gmail_address_fills_the_servers_and_shows_the_app_password_steps(dialog) -> None:
    dialog.get_by_label("Email address", exact=True).fill("sam.example@gmail.com")

    assert dialog.locator('[data-provider="gmail"]').get_attribute("aria-pressed") == "true"
    assert _value(dialog, "Incoming mail server (IMAP)") == "imap.gmail.com"
    assert _value(dialog, "Incoming server port") == "993"
    assert _value(dialog, "Incoming connection security") == "ssl"
    assert _value(dialog, "Outgoing mail server (SMTP), optional") == "smtp.gmail.com"
    assert _value(dialog, "Outgoing server port") == "465"
    help_text = dialog.locator(".email-provider-help").inner_text()
    assert "app password" in help_text and "2-Step Verification" in help_text
    link = dialog.locator(".email-provider-help a")
    assert link.get_attribute("href") == "https://myaccount.google.com/apppasswords"
    assert link.get_attribute("rel") == "noopener noreferrer"
    # The server names are filled in, out of the way, and still reachable.
    servers = dialog.locator("details.email-servers")
    assert servers.evaluate("d => d.open") is False
    assert "Filled in for Gmail" in servers.text_content()
    assert dialog.get_by_label("Password", exact=True).input_value() == ""


@pytest.mark.parametrize(
    "address,provider,imap,smtp,smtp_port,smtp_security,link",
    [
        ("pat@icloud.com", "icloud", "imap.mail.me.com", "smtp.mail.me.com", "587", "starttls", "https://account.apple.com"),
        ("pat@hotmail.co.uk", "outlook", "outlook.office365.com", "smtp-mail.outlook.com", "587", "starttls",
         "https://account.microsoft.com/security"),
        ("pat@yahoo.com", "yahoo", "imap.mail.yahoo.com", "smtp.mail.yahoo.com", "465", "ssl",
         "https://login.yahoo.com/account/security"),
    ],
)
def test_each_provider_gets_its_own_servers_and_steps(dialog, address, provider, imap, smtp, smtp_port, smtp_security, link) -> None:
    dialog.get_by_label("Email address", exact=True).fill(address)

    assert dialog.locator(f'[data-provider="{provider}"]').get_attribute("aria-pressed") == "true"
    assert _value(dialog, "Incoming mail server (IMAP)") == imap
    assert _value(dialog, "Outgoing mail server (SMTP), optional") == smtp
    assert _value(dialog, "Outgoing server port") == smtp_port
    assert _value(dialog, "Outgoing connection security") == smtp_security
    assert dialog.locator(".email-provider-help a").get_attribute("href") == link


def test_outlook_says_plainly_that_it_may_be_refused(dialog) -> None:
    dialog.get_by_label("Email address", exact=True).fill("pat@outlook.com")

    assert "may be refused" in dialog.locator(".email-provider-help").inner_text()


def test_an_unknown_provider_shows_the_server_fields_to_fill(dialog) -> None:
    dialog.get_by_label("Email address", exact=True).fill("pat@gmail.com")
    dialog.get_by_label("Email address", exact=True).fill("pat@mail.example.test")

    assert dialog.locator('[data-provider="other"]').get_attribute("aria-pressed") == "true"
    assert dialog.locator("details.email-servers").evaluate("d => d.open") is True
    # Gmail's servers are not this mailbox's servers.
    assert _value(dialog, "Incoming mail server (IMAP)") == ""
    assert _value(dialog, "Outgoing mail server (SMTP), optional") == ""
    assert "IMAP settings" in dialog.locator("details.email-servers").inner_text()


def test_a_provider_picked_by_hand_is_kept_while_the_address_is_typed(dialog) -> None:
    dialog.locator('[data-provider="icloud"]').click()
    dialog.get_by_label("Email address", exact=True).fill("pat@custom-domain.example")

    assert dialog.locator('[data-provider="icloud"]').get_attribute("aria-pressed") == "true"
    assert _value(dialog, "Incoming mail server (IMAP)") == "imap.mail.me.com"
