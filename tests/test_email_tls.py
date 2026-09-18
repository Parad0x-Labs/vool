"""Real-TLS email transports: STARTTLS and implicit TLS against a throwaway cert.

The local mail service generates a real certificate (localhost SAN); the credential's
`security` selects the production code path — "starttls" upgrades on the plain port,
"ssl" wraps from the first byte on the TLS port. The client trusts the fixture CA via
a cafile-injecting context patch: the TLS handshake, record layer and certificate
validation are REAL; only the trust anchor is the fixture's own CA, exactly like a
corporate test CA. No transport doubles.

Also covers the downgrade refusal: `security: "plain"` on a NON-loopback host is
rejected before any socket opens, so a real account can never be silently moved to
cleartext.
"""
from __future__ import annotations

import json
import ssl
from email.message import EmailMessage
from pathlib import Path

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota

pytestmark = [pytest.mark.email_live]

SMTP_PORT = 12461
IMAP_PORT = 0        # ephemeral: companion ports 12462-12464 belong to other missions
SMTP_TLS_PORT = 0
IMAP_TLS_PORT = 0


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def tls_service(tmp_path):
    from tests.local_mail_service import LocalMailService

    cert_dir = tmp_path / "certs"
    service = LocalMailService(
        smtp_port=SMTP_PORT, imap_port=IMAP_PORT, cert_dir=cert_dir,
        smtp_tls_port=SMTP_TLS_PORT, imap_tls_port=IMAP_TLS_PORT,
    )
    # cert is minted for "localhost": every TLS connection must use that name
    inbox = [
        _msg("TLS Sender <tls@secure.example.test>", "STARTTLS probe", "over starttls", "<tls-1@secure.example.test>"),
    ]
    service.store.add_user("tls@example.test", "pw-tls", {"INBOX": inbox})
    service.start()
    try:
        yield service, cert_dir
    finally:
        service.stop()


def _msg(frm: str, subj: str, body: str, mid: str) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "tls@example.test"
    m["Subject"] = subj
    m["Message-ID"] = mid
    m["Date"] = "Thu, 11 Sep 2026 08:00:00 +0000"
    m.set_content(body)
    return m.as_bytes()


def _trust_fixture_ca(monkeypatch, cert_dir: Path):
    real_default = ssl.create_default_context

    def _context(*args, **kwargs):
        kwargs["cafile"] = str(cert_dir / "server-cert.pem")
        return real_default(*args, **kwargs)

    monkeypatch.setattr(email_tools.ssl, "create_default_context", _context)


def _store(kind: str, host: str, port: int, security: str) -> None:  # port from the live service
    blob = {"host": host, "port": port, "username": "tls@example.test", "password": "pw-tls",
            "security": security}
    if kind == "smtp":
        blob["from_addr"] = "tls@example.test"
    credential_store.store_credential(f"email.{kind}.default", json.dumps(blob), label=f"test {kind} {security}")


def test_starttls_read_and_send_roundtrip(tls_service, monkeypatch) -> None:
    service, cert_dir = tls_service
    _trust_fixture_ca(monkeypatch, cert_dir)
    _store("imap", "localhost", service.imap_port, "starttls")
    _store("smtp", "localhost", service.smtp_port, "starttls")

    result = email_tools.search_email(sender="secure.example.test")
    assert result.ok and result.status == "executed", result.message
    assert len(result.messages) == 1
    assert result.messages[0]["subject"] == "STARTTLS probe"

    sent = email_tools.send_email(to="tls@example.test", subject="Re: STARTTLS probe", body="reply over starttls")
    assert sent.ok and sent.status == "executed", sent.message
    assert len(service.store.captured) == 1


def test_implicit_tls_read_and_send_roundtrip(tls_service, monkeypatch) -> None:
    service, cert_dir = tls_service
    _trust_fixture_ca(monkeypatch, cert_dir)
    _store("imap", "localhost", service.imap_tls_port, "ssl")
    _store("smtp", "localhost", service.smtp_tls_port, "ssl")

    result = email_tools.read_email(limit=5)
    assert result.ok and len(result.messages) == 1, result.message
    assert "over starttls" in result.messages[0]["snippet"]

    sent = email_tools.send_email(to="tls@example.test", subject="TLS probe", body="over implicit tls")
    assert sent.ok and sent.status == "executed", sent.message
    assert len(service.store.captured) == 1


def test_plain_security_refused_off_loopback(tls_service, monkeypatch) -> None:
    _, cert_dir = tls_service
    _trust_fixture_ca(monkeypatch, cert_dir)
    _store("smtp", "smtp.real-provider.example", 25, "plain")

    result = email_tools.send_email(to="x@y.test", subject="s", body="b")
    assert not result.ok and result.status == "send_failed"
    assert "loopback" in result.message  # the refusal names the reason

    _store("imap", "imap.real-provider.example", 143, "plain")
    read = email_tools.read_email()
    assert not read.ok and read.status == "read_failed"
    assert "loopback" in read.message


def test_untrusted_certificate_fails_closed(tls_service) -> None:
    # No cafile patch here: the default context does not trust the fixture CA, so the
    # connection must fail closed with a structured result, never a raised traceback.
    _store("smtp", "localhost", tls_service[0].smtp_tls_port, "ssl")
    result = email_tools.send_email(to="x@y.test", subject="s", body="b")
    assert not result.ok and result.status == "send_failed"
    assert "CERTIFICATE_VERIFY_FAILED" in result.message or "certificate" in result.message.lower()
