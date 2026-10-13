"""Gmail message bodies in the shapes real mail arrives in, through the VOOL read seam.

Shipping and order mail usually carries an attachment or is HTML-only. Gmail returns those as a
nested multipart tree (format=full), so the body must be found below the top level, and an
HTML-only message must give its visible text, as the IMAP reader does. Driven against the local
recorded-shape Gmail API server (tests/provider_api_fixture.py); not proof of the live provider.
"""
from __future__ import annotations

import base64
import json

import pytest

from core import credential_store, email_drafts, email_tools, runtime_paths
from tests.provider_api_fixture import ProviderApiServer

ADDRESS = "me@gmail.example.test"


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.fixture()
def gmail(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    server = ProviderApiServer()
    server.start()
    refresh = server.add_oauth_client("gmail", "gmail-acct", ADDRESS)
    credential_store.store_credential(
        "email.oauth.gmail.gmail-acct",
        json.dumps({"client_id": "test-client", "client_secret": "test-secret", "refresh_token": refresh,
                    "token_url": server.token_url, "account_email": ADDRESS}),
        label="test oauth handle",
    )
    blob = {"provider": "gmail", "from_addr": ADDRESS, "api_base": server.gmail_base, "token_url": server.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.gmail-acct", json.dumps(blob), label=f"test {kind}")
    try:
        yield server
    finally:
        server.stop()
        runtime_paths.configure_runtime_home(None)


def _open(server, payload: dict) -> str:
    server.gmail_inbox(ADDRESS, [{
        "from": "Shop <orders@shop.example.test>", "subject": "Your order has shipped",
        "date": "Mon, 28 Sep 2026 09:00:00 +0000", "message_id": "<ship-1@shop.example.test>",
        "body": "Your order has shipped", "provider_id": "gm-ship-1", "payload": payload,
    }])
    opened = email_tools.open_message(account="gmail-acct", message_id="gm-ship-1")
    assert opened.ok, opened.message
    return opened.messages[0]["body"]


def test_body_nested_under_an_attachment_is_read(gmail):
    body = _open(gmail, {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Tracking number: TEST-0000\nCarrier: Example Post")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Tracking number: TEST-0000</p>")}},
        ]},
        {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "att-1", "size": 10}},
    ]})
    assert "TEST-0000" in body and "Example Post" in body and "<p>" not in body, body


def test_html_only_mail_gives_its_visible_text(gmail):
    body = _open(gmail, {"mimeType": "text/html", "body": {"data": _b64("<html><body><b>Shipped</b> via Example Post</body></html>")}})
    assert body == "Shipped via Example Post", body


def test_a_text_attachment_is_not_the_body(gmail):
    body = _open(gmail, {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/plain", "filename": "notes.txt", "body": {"data": _b64("attachment words")}},
        {"mimeType": "text/plain", "body": {"data": _b64("the message itself")}},
    ]})
    assert body == "the message itself", body


def test_single_part_plain_mail_is_unchanged(gmail):
    body = _open(gmail, {"mimeType": "text/plain", "body": {"data": _b64("plain words")}})
    assert body == "plain words", body
