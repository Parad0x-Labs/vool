"""Adding and removing an email account from Settings: POST /api/email/accounts/add and /remove.

The mail server is tests/local_mail_service.py on loopback with a real certificate minted for
"localhost". Certificate verification is the production code path (email_tools._imap_connect /
_smtp_connect with ssl.create_default_context()); the only test-side change is the trust anchor, injected
the way tests/test_email_tls.py documents it: the default-context factory is wrapped to add the fixture's
own certificate as a cafile. Verification stays on (host name and chain are both checked), and no setting
a running VOOL reads can do this. Without that injection the same server must be refused as tls_failed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import ssl
from pathlib import Path

import pytest

from core import credential_store, email_setup, email_tools
from tests._credential_intelligence_support import isolated_home, vault_home

ADDRESS = "owner@mail.example.test"
PASSWORD = "Fixture-Mailbox-pass-7391"
SLOT = email_setup.slot_for_address(ADDRESS)
JSON_HEADERS = {"Content-Type": "application/json"}


def _service(cert_dir: Path | None, *, user: bool = True):
    from tests.local_mail_service import LocalMailService

    service = LocalMailService(smtp_port=0, imap_port=0, cert_dir=cert_dir,
                               smtp_tls_port=0 if cert_dir else None, imap_tls_port=0 if cert_dir else None)
    if user:
        service.store.add_user(ADDRESS, PASSWORD, {"INBOX": [_message("Welcome", "<welcome-1@mail.example.test>")]})
    service.start()
    return service


@pytest.fixture()
def mail(vault_home, tmp_path):
    cert_dir = tmp_path / "certs"
    service = _service(cert_dir)
    try:
        yield service, cert_dir
    finally:
        service.stop()


@pytest.fixture()
def connects(monkeypatch):
    """Every connection the login checks open, recorded (the real connectors still run)."""
    calls: list[dict] = []
    real_imap, real_smtp = email_tools._imap_connect, email_tools._smtp_connect

    def _recording(kind, real):
        def _connect(creds, **kwargs):
            calls.append({"kind": kind, "host": creds.get("host"), "port": creds.get("port"),
                          "security": creds.get("security"), "addresses": kwargs.get("addresses")})
            return real(creds, **kwargs)
        return _connect

    monkeypatch.setattr(email_tools, "_imap_connect", _recording("imap", real_imap))
    monkeypatch.setattr(email_tools, "_smtp_connect", _recording("smtp", real_smtp))
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
    return json.loads(text)


def _rows() -> dict:
    return {row["account"]: row for row in _accounts()["accounts"]}


def _email_entries() -> list[str]:
    return sorted(row["name"] for row in credential_store.list_credentials() if row["name"].startswith("email."))


def _labels() -> dict[str, str]:
    return {row["name"]: row["label"] for row in credential_store.list_credentials() if row["name"].startswith("email.")}


def _add_body(service, **overrides) -> dict:
    body = {"address": ADDRESS, "password": PASSWORD, "imap_host": "localhost", "imap_port": service.imap_tls_port}
    body.update(overrides)
    return body


def _with_smtp(service, **overrides) -> dict:
    return _add_body(service, smtp_host="localhost", smtp_port=service.smtp_tls_port, **overrides)


def _no_plaintext_password_under(home: Path) -> None:
    for path in home.rglob("*"):
        if path.is_file():
            assert PASSWORD.encode() not in path.read_bytes(), f"plaintext password written to {path.name}"


# ---------------------------------------------------------------------------------------------
# Certificate verification, the stored account, and the real read and send
# ---------------------------------------------------------------------------------------------


def test_an_unverifiable_certificate_is_refused_as_tls_failed_and_nothing_is_stored(mail, connects):
    service, _ = mail
    status, payload = _post("/api/email/accounts/add", _add_body(service))
    assert status == 400 and payload["ok"] is False and payload["error"] == "tls_failed", payload
    assert [c["kind"] for c in connects] == ["imap"] and connects[0]["security"] == "ssl"
    assert _email_entries() == []
    assert _rows() == {}


def test_both_logins_are_checked_then_the_account_reads_and_sends(mail, connects, monkeypatch, caplog, vault_home):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    caplog.set_level(logging.DEBUG)

    status, payload = _post("/api/email/accounts/add", _with_smtp(service))
    assert status == 200 and payload["ok"] is True, payload
    assert payload["account"] == SLOT and payload["address"] == ADDRESS and payload["send"] is True
    assert "incoming and outgoing" in payload["message"]
    assert [(c["kind"], c["security"]) for c in connects] == [("imap", "ssl"), ("smtp", "ssl")]

    rows = _rows()
    assert set(rows) == {SLOT}
    assert rows[SLOT]["address"] == ADDRESS and rows[SLOT]["state"] == "configured", rows
    assert _email_entries() == [f"email.imap.{SLOT}", f"email.smtp.{SLOT}"]

    # The stored account is the one the mail tools use: a read and a send go through it.
    read = email_tools.read_email(account=SLOT, limit=5)
    assert read.ok and [m["subject"] for m in read.messages] == ["Welcome"], read.message
    sent = email_tools.send_email(to="friend@elsewhere.example.test", subject="Hi", body="sent after setup", account=SLOT)
    assert sent.ok, sent.message
    assert len(service.store.captured) == 1

    assert PASSWORD not in caplog.text
    _no_plaintext_password_under(vault_home)


def test_a_read_only_account_says_it_cannot_send(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    status, payload = _post("/api/email/accounts/add", _add_body(service))
    assert status == 200 and payload["send"] is False and "not send" in payload["message"], payload
    assert _rows()[SLOT]["state"] == "read_only"


# ---------------------------------------------------------------------------------------------
# STARTTLS, the explicit security choice, and the port default
# ---------------------------------------------------------------------------------------------


def test_starttls_is_chosen_explicitly_on_a_plain_port_and_stored(mail, connects, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    status, payload = _post("/api/email/accounts/add", _add_body(
        service, imap_port=service.imap_port, imap_security="starttls",
        smtp_host="localhost", smtp_port=service.smtp_port, smtp_security="starttls"))
    assert status == 200, payload
    assert [(c["kind"], c["security"]) for c in connects] == [("imap", "starttls"), ("smtp", "starttls")]
    stored = json.loads(credential_store.get_credential(f"email.imap.{SLOT}"))
    assert stored["security"] == "starttls" and stored["port"] == service.imap_port
    assert email_tools.read_email(account=SLOT, limit=1).ok


def test_port_143_defaults_to_starttls_and_plain_is_never_accepted(mail, connects):
    service, _ = mail
    status, payload = _post("/api/email/accounts/add", _add_body(service, imap_port=143))
    assert status == 400 and payload["error"] in ("cannot_reach", "tls_failed"), payload
    assert connects and connects[0]["security"] == "starttls"
    status, payload = _post("/api/email/accounts/add", _add_body(service, imap_security="plain"))
    assert status == 400 and payload["error"] == "invalid" and payload["field"] == "imap_security", payload


def test_a_server_without_starttls_is_tls_failed_for_both_directions(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    bare = _service(None)
    try:
        status, payload = _post("/api/email/accounts/add", _add_body(service, imap_port=bare.imap_port, imap_security="starttls"))
        assert status == 400 and payload["error"] == "tls_failed", payload
        status, payload = _post("/api/email/accounts/add", _add_body(
            service, smtp_host="localhost", smtp_port=bare.smtp_port, smtp_security="starttls"))
        assert status == 400 and payload["error"] == "smtp_tls_failed", payload
    finally:
        bare.stop()
    assert _email_entries() == []


# ---------------------------------------------------------------------------------------------
# Login failures store nothing
# ---------------------------------------------------------------------------------------------


def test_a_wrong_password_is_auth_failed_and_nothing_is_stored(mail, monkeypatch, caplog):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    caplog.set_level(logging.DEBUG)
    wrong = "Not-The-Password-1234"
    status, payload = _post("/api/email/accounts/add", _add_body(service, password=wrong))
    assert status == 400 and payload["error"] == "auth_failed", payload
    assert wrong not in json.dumps(payload) and wrong not in caplog.text
    assert _email_entries() == []


def test_an_outgoing_server_that_refuses_the_login_is_smtp_auth_failed(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    stranger = _service(cert_dir, user=False)  # same trusted certificate, does not know this mailbox
    try:
        status, payload = _post("/api/email/accounts/add", _add_body(
            service, smtp_host="localhost", smtp_port=stranger.smtp_tls_port))
    finally:
        stranger.stop()
    assert status == 400 and payload["error"] == "smtp_auth_failed", payload
    assert _email_entries() == []


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_servers_that_do_not_answer_are_cannot_reach(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    status, payload = _post("/api/email/accounts/add", _add_body(service, imap_port=_closed_port()))
    assert status == 400 and payload["error"] == "cannot_reach", payload
    status, payload = _post("/api/email/accounts/add", _add_body(service, smtp_host="localhost", smtp_port=_closed_port()))
    assert status == 400 and payload["error"] == "smtp_cannot_reach", payload
    assert _email_entries() == []


def test_a_server_that_never_answers_is_timeout(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    monkeypatch.setattr(email_setup, "VERIFY_TIMEOUT_S", 0.5)
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(4)  # the kernel completes the TCP handshake; nothing ever answers the TLS hello
    try:
        status, payload = _post("/api/email/accounts/add", _add_body(service, imap_port=silent.getsockname()[1]))
        assert status == 400 and payload["error"] == "timeout", payload
        status, payload = _post("/api/email/accounts/add", _add_body(
            service, smtp_host="localhost", smtp_port=silent.getsockname()[1]))
        assert status == 400 and payload["error"] == "smtp_timeout", payload
    finally:
        silent.close()
    assert _email_entries() == []


# ---------------------------------------------------------------------------------------------
# Who may call, and what is kept about the request
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_host", "headers", "status"),
    [
        ("203.0.113.9", {}, 403),                                                    # not this machine
        ("127.0.0.1", {"Sec-Fetch-Site": "cross-site"}, 403),                        # another site
        ("127.0.0.1", {"Sec-Fetch-Site": "same-site"}, 403),                         # another port on this host
        ("127.0.0.1", {"Origin": "https://attacker.example"}, 403),                  # another origin
        ("127.0.0.1", {"Origin": "http://127.0.0.1:5555", "Host": "127.0.0.1:8080"}, 403),  # same host, other port
        ("127.0.0.1", {"Origin": "http://127.0.0.1:8080"}, 403),                     # an Origin with no Host to match
        ("127.0.0.1", {"Content-Type": "text/plain"}, 415),                          # not a JSON request
        ("127.0.0.1", {"Content-Type": ""}, 415),                                    # a typeless body (no preflight)
    ],
)
def test_a_caller_outside_this_app_is_refused_before_any_connection(mail, connects, monkeypatch, client_host, headers, status):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    for path, body in (("/api/email/accounts/add", _add_body(service)), ("/api/email/accounts/remove", {"account": SLOT}),
                       ("/api/x/setup", {"action": "status"})):
        got, payload = _post(path, body, client_host=client_host, headers=headers)
        assert got == status, (path, got, payload)
    assert connects == []
    assert _email_entries() == []


def test_the_app_page_itself_is_let_through(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    same = {"Origin": "http://127.0.0.1:8080", "Host": "127.0.0.1:8080", "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json; charset=utf-8"}
    status, payload = _post("/api/email/accounts/add", _add_body(service), headers=same)
    assert status == 200, payload


def _ledger_digests() -> set[str]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return {str(row[0]) for row in conn.execute("SELECT raw_digest FROM invocation_requests").fetchall()}
    finally:
        conn.close()


def _digest(body: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")).hexdigest()


def test_the_request_digest_never_hashes_the_password_or_x_keys(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    body = _add_body(service)
    assert _post("/api/email/accounts/add", body)[0] == 200
    x_body = {"action": "status", "bearer_token": "fixture-bearer-not-real-0123456789"}
    _post("/api/x/setup", x_body)
    digests = _ledger_digests()
    assert _digest(body) not in digests and _digest(x_body) not in digests
    # The digest that IS kept binds every other field: the same body with the secret withheld.
    assert _digest({**body, "password": "<withheld>"}) in digests
    assert _digest({**x_body, "bearer_token": "<withheld>"}) in digests


# ---------------------------------------------------------------------------------------------
# Local Only, and connecting to exactly what was checked
# ---------------------------------------------------------------------------------------------


def test_local_only_refuses_a_remote_server_before_connecting_but_keeps_this_machine(mail, connects, monkeypatch):
    from core import policy_engine

    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    try:
        for body in (_add_body(service, imap_host="imap.mail.example", imap_port=993),
                     _add_body(service, smtp_host="smtp.mail.example", smtp_port=465),
                     _add_body(service, imap_host="8.8.8.8", imap_port=993)):
            status, payload = _post("/api/email/accounts/add", body)
            assert status == 403 and payload["error"] == "local_only", payload
        assert connects == []
        assert _email_entries() == []
        # A server on this machine is not public egress: Local Only lets it through.
        status, payload = _post("/api/email/accounts/add", _add_body(service))
        assert status == 200 and payload["ok"] is True, payload
    finally:
        policy_engine.set_operator_policy_values({"system.local_only_mode": False})


def test_the_login_connects_to_the_address_that_was_checked(mail, connects, monkeypatch):
    """A name that answers a local address to the check and a public one afterwards (DNS rebinding)
    is looked up once: the connection goes to the checked address, never to the second answer."""
    from core import policy_engine

    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    monkeypatch.setattr(email_setup, "VERIFY_TIMEOUT_S", 2.0)
    real = socket.getaddrinfo
    lookups: list[tuple] = []

    def _rebinding(host, port, *args, **kwargs):
        if host == "localhost":
            lookups.append((host, port))
            ip = "127.0.0.1" if len(lookups) == 1 else "8.8.8.8"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 0)))]
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _rebinding)
    policy_engine.set_operator_policy_values({"system.local_only_mode": True})
    try:
        status, payload = _post("/api/email/accounts/add", _add_body(service))
    finally:
        policy_engine.set_operator_policy_values({"system.local_only_mode": False})
    assert status == 200, payload
    assert len(lookups) == 1
    assert connects[0]["addresses"] == ("127.0.0.1",)


# ---------------------------------------------------------------------------------------------
# Slots: one per mailbox, never overwritten by another one
# ---------------------------------------------------------------------------------------------


def test_addresses_whose_readable_parts_agree_get_different_slots():
    pairs = [("a.b@x.com", "a_b@x.com"), ("me@mail.example", "me@mail-example"),
             ("x" * 50 + "@one.example", "x" * 50 + "@two.example")]
    for left, right in pairs:
        assert email_setup.slot_for_address(left) != email_setup.slot_for_address(right), (left, right)
    assert email_setup.slot_for_address("Me@Mail.Example") == email_setup.slot_for_address("me@mail.example")
    assert all(ch.isalnum() or ch == "-" for ch in email_setup.slot_for_address("a.b+c@x.com"))


def test_a_second_add_is_refused_unless_replace_is_asked(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _with_smtp(service))[0] == 200

    status, payload = _post("/api/email/accounts/add", _add_body(service))
    assert status == 409 and payload["error"] == "exists", payload
    assert _rows()[SLOT]["send"] is True  # untouched

    # Replace without an outgoing server: the old sending entry goes with the old details.
    status, payload = _post("/api/email/accounts/add", _add_body(service, replace=True))
    assert status == 200 and payload["send"] is False, payload
    assert _email_entries() == [f"email.imap.{SLOT}"]


def test_replace_never_overwrites_a_slot_that_holds_another_mailbox(mail, connects, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    other = json.dumps({"host": "imap.other.example", "port": 993, "username": "someone@other.example",
                        "password": "other-secret", "from_addr": "someone@other.example"})
    credential_store.store_credential(f"email.imap.{SLOT}", other, label="someone else")
    status, payload = _post("/api/email/accounts/add", _add_body(service, replace=True))
    assert status == 409 and payload["error"] == "conflict", payload
    assert connects == []
    assert credential_store.get_credential(f"email.imap.{SLOT}") == other


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


# ---------------------------------------------------------------------------------------------
# Remove, the default it named, and rollback when the store fails
# ---------------------------------------------------------------------------------------------


def test_remove_deletes_the_account_and_clears_the_default_that_named_it(mail, monkeypatch):
    from core import operator_profile

    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _with_smtp(service))[0] == 200
    change = operator_profile.remember(operator_profile.OWNER_PRINCIPAL, "default_account.email",
                                       f"credential:email.smtp.{SLOT}", scope="global", actor="operator")
    assert change.kind == "saved", change.report
    assert _accounts()["default"]["account"] == SLOT

    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 200 and payload["ok"] is True and payload["default_cleared"] is True, payload
    assert payload["address"] == ADDRESS
    assert _email_entries() == [] and _rows() == {}
    assert _accounts()["default"]["account"] == ""

    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 404 and payload["error"] == "not_found", payload


def test_a_failed_write_on_replace_restores_the_old_entries_and_labels(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _with_smtp(service))[0] == 200
    before = {name: credential_store.get_credential(name) for name in _email_entries()}
    labels = _labels()
    real_store = credential_store.store_credential

    def _failing(name, value, *, label=""):
        if name == f"email.smtp.{SLOT}" and value != before[name]:
            raise OSError("disk full")
        return real_store(name, value, label=label)

    monkeypatch.setattr(credential_store, "store_credential", _failing)
    status, payload = _post("/api/email/accounts/add", _add_body(
        service, imap_port=service.imap_port, imap_security="starttls",
        smtp_host="localhost", smtp_port=service.smtp_port, smtp_security="starttls", replace=True))
    assert status == 500 and payload["error"] == "store_failed", payload
    assert {name: credential_store.get_credential(name) for name in _email_entries()} == before
    assert _labels() == labels


def test_a_failed_remove_restores_what_it_had_deleted_with_its_label(mail, monkeypatch):
    service, cert_dir = mail
    _trust_fixture_ca(monkeypatch, cert_dir)
    assert _post("/api/email/accounts/add", _with_smtp(service))[0] == 200
    before = {name: credential_store.get_credential(name) for name in _email_entries()}
    labels = _labels()
    real_delete = credential_store.delete_credential

    def _failing(name):
        if name == f"email.smtp.{SLOT}":
            raise OSError("keychain locked")
        return real_delete(name)

    monkeypatch.setattr(credential_store, "delete_credential", _failing)
    status, payload = _post("/api/email/accounts/remove", {"account": SLOT})
    assert status == 500 and payload["error"] == "store_failed", payload
    assert {name: credential_store.get_credential(name) for name in _email_entries()} == before
    assert _labels() == labels and labels[f"email.imap.{SLOT}"]


# ---------------------------------------------------------------------------------------------
# Input that is refused before any connection
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "kind", "field"),
    [
        ({"password": "line\r\nbreak"}, "invalid", "password"),
        ({"password": ""}, "invalid", "password"),
        ({"password": "pässword"}, "password_charset", "password"),
        ({"address": "not-an-address"}, "invalid", "address"),
        ({"imap_host": "imap host.example"}, "invalid", "imap_host"),
        ({"imap_host": "https://imap.example"}, "invalid", "imap_host"),
        ({"imap_port": 70000}, "invalid", "imap_port"),
        ({"imap_port": True}, "invalid", "imap_port"),
        ({"smtp_security": "ssl"}, "invalid", "smtp_host"),
        ({"replace": "yes"}, "invalid", "replace"),
        ({"security": "plain"}, "invalid", "security"),
    ],
)
def test_invalid_details_are_refused_before_any_connection(mail, connects, overrides, kind, field):
    service, _ = mail
    status, payload = _post("/api/email/accounts/add", _add_body(service, **overrides))
    assert status == 400 and payload["error"] == kind and payload.get("field") == field, payload
    assert connects == []
    assert _email_entries() == []
