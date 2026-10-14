"""Adding and removing an email account from Settings: POST /api/email/accounts/add and /remove.

The mail server is tests/local_mail_service.py on loopback with a real certificate minted for
"localhost". Certificate verification is the production code path (email_tools._imap_connect with
ssl.create_default_context()); the only test-side change is the trust anchor, injected the way
tests/test_email_tls.py documents it: the default-context factory is wrapped to add the fixture's own
certificate as a cafile. Verification stays on (host name and chain are both checked), and no setting a
running VOOL reads can do this. Without that injection the same server must be refused as tls_failed.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
from pathlib import Path

import pytest

from core import credential_store, email_tools
from tests._credential_intelligence_support import isolated_home, vault_home

ADDRESS = "owner@mail.example.test"
PASSWORD = "Fixture-Mailbox-pass-7391"
SLOT = "owner-mail-example-test"
JSON_HEADERS = {"Content-Type": "application/json"}


@pytest.fixture()
def mail(vault_home, tmp_path):
    from tests.local_mail_service import LocalMailService

    cert_dir = tmp_path / "certs"
    service = LocalMailService(smtp_port=0, imap_port=0, cert_dir=cert_dir, smtp_tls_port=0, imap_tls_port=0)
    service.store.add_user(ADDRESS, PASSWORD, {"INBOX": [_message("Welcome", "<welcome-1@mail.example.test>")]})
    service.start()
    try:
        yield service, cert_dir
    finally:
        service.stop()


@pytest.fixture()
def connects(monkeypatch):
    """Every IMAP connection the login check opens, recorded (the real connector still runs)."""
    calls: list[dict] = []
    real = email_tools._imap_connect

    def _recording(creds):
        calls.append({"host": creds.get("host"), "port": creds.get("port"), "security": creds.get("security")})
        return real(creds)

    monkeypatch.setattr(email_tools, "_imap_connect", _recording)
    return calls


def _message(subject: str, message_id: str) -> bytes:
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"] = "Sender <sender@elsewhere.example.test>"
    m["To"] = ADDRESS
    m["Subject"] = subject
    m["Message-ID"] = message_id
    m["Date"] = "Thu, 11 Sep 2026 08:00:00 +0000"
    m.set_content("hello from the fixture")
    return m.as_bytes()


def _trust_fixture_ca(monkeypatch, cert_dir: Path) -> None:
    real_default = ssl.create_default_context

    def _context(*args, **kwargs):
        kwargs["cafile"] = str(cert_dir / "server-cert.pem")
        return real_default(*args, **kwargs)

    monkeypatch.setattr(email_tools.ssl, "create_default_context", _context)


def _rt():
    from core.web.api.runtime import RuntimeServices

    return RuntimeServices(display_name="email add fixture")


def _post(path: str, body: dict, *, client_host: str = "127.0.0.1", headers: dict | None = None) -> tuple[int, dict]:
    from core.web.api.service import dispatch_post

    res = dispatch_post(path=path, body=body, headers={**JSON_HEADERS, **(headers or {})}, runtime=_rt(),
                        model_name="fixture", client_host=client_host, workspace_root_provider=lambda: ".")
    text = res.body.decode() if isinstance(res.body, bytes) else str(res.body)
    assert PASSWORD not in text, "the password came back in a response"
    return res.status, json.loads(text)


def _accounts() -> dict:
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/api/email/accounts", query={}, runtime=_rt(), model_name="fixture", client_host="127.0.0.1")
    text = res.body.decode() if isinstance(res.body, bytes) else str(res.body)
    assert PASSWORD not in text
    assert res.status == 200, text
    return {row["account"]: row for row in json.loads(text)["accounts"]}


def _email_entries() -> list[str]:
    return [row["name"] for row in credential_store.list_credentials() if row["name"].startswith("email.")]


def _add_body(service, **overrides) -> dict:
    body = {"address": ADDRESS, "password": PASSWORD, "imap_host": "localhost", "imap_port": service.imap_tls_port}
    body.update(overrides)
    return body


def _no_plaintext_password_under(home: Path) -> None:
    for path in home.rglob("*"):
        if path.is_file():
            assert PASSWORD.encode() not in path.read_bytes(), f"plaintext password written to {path.name}"


def test_an_unverifiable_certificate_is_refused_as_tls_failed_and_nothing_is_stored(mail, connects):
    service, _ = mail
    status, payload = _post("/api/email/accounts/add", _add_body(service))
    assert status == 400 and payload["ok"] is False and payload["error"] == "tls_failed", payload
    assert connects and connects[0]["security"] == "ssl"
    assert _email_entries() == []
    assert _accounts() == {}


def test_a_verified_login_stores_the_account_and_it_reads_and_sends(mail, connects, monkeypatch, caplog, vault_home):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    caplog.set_level(logging.DEBUG)

    status, payload = _post("/api/email/accounts/add", _add_body(
        service, smtp_host="localhost", smtp_port=service.smtp_tls_port))
    assert status == 200 and payload["ok"] is True, payload
    assert payload["account"] == SLOT and payload["read"] is True and payload["send"] is True
    assert len(connects) == 1

    rows = _accounts()
    assert set(rows) == {SLOT}
    assert rows[SLOT]["address"] == ADDRESS and rows[SLOT]["state"] == "configured", rows
    assert rows[SLOT]["read"] is True and rows[SLOT]["send"] is True
    assert sorted(_email_entries()) == [f"email.imap.{SLOT}", f"email.smtp.{SLOT}"]

    # The stored account is the one the mail tools use: a read and a send go through it.
    read = email_tools.read_email(account=SLOT, limit=5)
    assert read.ok and [m["subject"] for m in read.messages] == ["Welcome"], read.message
    sent = email_tools.send_email(to="friend@elsewhere.example.test", subject="Hi", body="sent after setup", account=SLOT)
    assert sent.ok, sent.message
    assert len(service.store.captured) == 1

    assert PASSWORD not in caplog.text
    _no_plaintext_password_under(vault_home)


def test_a_second_add_is_refused_unless_replace_is_asked(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _add_body(service, smtp_host="localhost", smtp_port=service.smtp_tls_port))[0] == 200

    status, payload = _post("/api/email/accounts/add", _add_body(service))
    assert status == 409 and payload["error"] == "exists", payload
    assert _accounts()[SLOT]["send"] is True  # untouched

    # Replace without a sending server: the old sending entry goes with the old details.
    status, payload = _post("/api/email/accounts/add", _add_body(service, replace=True))
    assert status == 200 and payload["send"] is False, payload
    assert _email_entries() == [f"email.imap.{SLOT}"]
    assert _accounts()[SLOT]["state"] == "read_only"


def test_a_wrong_password_is_auth_failed_and_nothing_is_stored(mail, monkeypatch, caplog):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    caplog.set_level(logging.DEBUG)
    wrong = "Not-The-Password-1234"
    status, payload = _post("/api/email/accounts/add", _add_body(service, password=wrong))
    assert status == 400 and payload["error"] == "auth_failed", payload
    assert wrong not in json.dumps(payload) and wrong not in caplog.text
    assert _email_entries() == []


def test_a_server_that_does_not_answer_is_cannot_reach(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    status, payload = _post("/api/email/accounts/add", _add_body(service, imap_port=closed_port))
    assert status == 400 and payload["error"] == "cannot_reach", payload
    assert _email_entries() == []


@pytest.mark.parametrize(
    ("client_host", "headers"),
    [
        ("203.0.113.9", {}),                                         # not this machine
        ("127.0.0.1", {"Sec-Fetch-Site": "cross-site"}),             # another site in the browser
        ("127.0.0.1", {"Origin": "https://attacker.example"}),       # another origin
        ("127.0.0.1", {"Content-Type": "text/plain"}),               # not a JSON request
    ],
)
def test_a_caller_outside_this_app_is_refused_before_any_connection(mail, connects, monkeypatch, client_host, headers):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    for path, body in (("/api/email/accounts/add", _add_body(service)), ("/api/email/accounts/remove", {"account": SLOT})):
        status, payload = _post(path, body, client_host=client_host, headers=headers)
        assert status in (403, 415), (path, status, payload)
    assert connects == []
    assert _email_entries() == []


def test_local_only_refuses_a_remote_server_before_connecting_but_keeps_this_machine(mail, connects, monkeypatch):
    from core import policy_engine

    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    try:
        for body in (_add_body(service, imap_host="imap.mail.example", imap_port=993),
                     _add_body(service, smtp_host="smtp.mail.example", smtp_port=465)):
            status, payload = _post("/api/email/accounts/add", body)
            assert status == 403 and payload["error"] == "local_only", payload
        assert connects == []
        assert _email_entries() == []
        # A server on this machine is not public egress: Local Only lets it through.
        status, payload = _post("/api/email/accounts/add", _add_body(service))
        assert status == 200 and payload["ok"] is True, payload
    finally:
        policy_engine.set_operator_policy_values({"system.local_only_mode": False})


def test_remove_deletes_the_account_and_an_unknown_one_is_not_found(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _add_body(service, smtp_host="localhost", smtp_port=service.smtp_tls_port))[0] == 200

    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 200 and payload["ok"] is True, payload
    assert _email_entries() == [] and _accounts() == {}

    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 404 and payload["error"] == "not_found", payload


def test_an_account_connected_by_provider_sign_in_is_not_replaced_or_removed_here(mail, connects, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    credential_store.store_credential(f"email.oauth.gmail.{SLOT}", json.dumps({"account_email": ADDRESS}), label="fixture grant")
    credential_store.store_credential(f"email.smtp.{SLOT}", json.dumps({"provider": "gmail", "from_addr": ADDRESS}), label="fixture")

    status, payload = _post("/api/email/accounts/add", _add_body(service, replace=True))
    assert status == 409 and payload["error"] == "managed_elsewhere", payload
    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 409 and payload["error"] == "managed_elsewhere", payload
    assert connects == []
    assert credential_store.has_credential(f"email.smtp.{SLOT}")


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"password": "line\r\nbreak"}, "password"),
        ({"password": ""}, "password"),
        ({"password": "pässword"}, "password"),
        ({"address": "not-an-address"}, "address"),
        ({"imap_host": "imap host.example"}, "imap_host"),
        ({"imap_host": "https://imap.example"}, "imap_host"),
        ({"imap_port": 70000}, "imap_port"),
        ({"imap_port": True}, "imap_port"),
        ({"replace": "yes"}, "replace"),
        ({"security": "plain"}, "security"),
    ],
)
def test_invalid_details_are_refused_before_any_connection(mail, connects, overrides, field):
    service, _ = mail
    status, payload = _post("/api/email/accounts/add", _add_body(service, **overrides))
    assert status == 400 and payload["error"] == "invalid" and payload.get("field") == field, payload
    assert connects == []
    assert _email_entries() == []
