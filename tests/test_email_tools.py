"""Native email send: creds gate, quota metering, approval gate, disabled-by-default, fail-closed."""
from __future__ import annotations

import json

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota
from core.runtime_execution_tools import _email_send, execute_runtime_tool


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


def _store_smtp(account: str = "default") -> None:
    credential_store.store_credential(
        f"email.smtp.{account}",
        json.dumps({
            "host": "smtp.example.com", "port": 465, "username": "me@example.com",
            "password": "app-pw", "from_addr": "me@example.com",
        }),
        label="test smtp",
    )


def _fake_smtp(monkeypatch) -> list:
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, context=None, timeout=None):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, _u, _p):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", FakeSMTP)
    return sent


def test_send_without_credentials_fails_closed() -> None:
    r = email_tools.send_email(to="a@b.com", subject="hi", body="yo")
    assert not r.ok and r.status == "needs_credentials"


def test_send_with_creds_delivers_and_meters(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    r = email_tools.send_email(to="alice@example.com", subject="Hello", body="Body")
    assert r.ok and r.status == "executed"
    assert len(sent) == 1 and sent[0]["To"] == "alice@example.com" and sent[0]["Subject"] == "Hello"
    assert usage_quota.usage_today("email.send") == 1  # counted only on a successful send


def test_invalid_recipient(monkeypatch) -> None:
    _store_smtp()
    _fake_smtp(monkeypatch)
    r = email_tools.send_email(to="", subject="x", body="y")
    assert not r.ok and r.status == "invalid_recipient"


def test_send_failure_is_caught(monkeypatch) -> None:
    _store_smtp()

    class Boom:
        def __init__(self, *_a, **_k):
            raise OSError("refused")

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", Boom)
    r = email_tools.send_email(to="a@b.com", subject="x", body="y")
    assert not r.ok and r.status == "send_failed"
    assert usage_quota.usage_today("email.send") == 0  # a failed send is not metered


def test_quota_exceeded(monkeypatch) -> None:
    _store_smtp()
    _fake_smtp(monkeypatch)
    cfg = runtime_paths.active_config_home_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "quota_limits.json").write_text(json.dumps({"free": {"email.send": {"per_day": 1}}}), encoding="utf-8")
    assert email_tools.send_email(to="a@b.com", subject="1", body="y").ok
    r = email_tools.send_email(to="a@b.com", subject="2", body="y")
    assert not r.ok and r.status == "quota_exceeded"


def test_handler_preview_requires_opt_in() -> None:
    r = _email_send({"to": "a@b.com", "subject": "hi", "body": "yo"})
    assert r.handled and not r.ok and r.status == "user_action_required"
    confirm = r.details["action_required"]["confirm_arguments"]
    assert confirm["allow_send"] is True and confirm["approve"] is True and confirm["to"] == "a@b.com"


def test_handler_sends_on_opt_in(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    r = _email_send({"to": "a@b.com", "subject": "hi", "body": "yo", "allow_send": True, "approve": True})
    assert r.ok and r.status == "executed" and len(sent) == 1


def test_email_send_disabled_by_default() -> None:
    # policy email.send_enabled defaults False -> execute_runtime_tool short-circuits to "disabled".
    r = execute_runtime_tool(
        "email.send", {"to": "a@b.com", "subject": "x", "body": "y", "allow_send": True, "approve": True}
    )
    assert r is not None and r.handled and not r.ok and r.status == "disabled"


def test_crlf_in_subject_is_sanitized_and_does_not_crash(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    r = email_tools.send_email(to="a@b.com", subject="Hi\r\nBcc: attacker@evil.com", body="y")
    assert r.ok and r.status == "executed"
    msg = sent[0]
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert msg["Bcc"] is None  # nothing smuggled in as a separate header via the subject


def test_crlf_in_extra_header_is_sanitized(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    r = email_tools.send_email(
        to="a@b.com", subject="x", body="y",
        extra_headers={"In-Reply-To": "<id@host>\r\nBcc: attacker@evil.com"},
    )
    assert r.ok and r.status == "executed"
    assert sent[0]["Bcc"] is None
    assert "\r" not in str(sent[0]["In-Reply-To"]) and "\n" not in str(sent[0]["In-Reply-To"])


def test_non_numeric_port_fails_closed(monkeypatch) -> None:
    credential_store.store_credential(
        "email.smtp.default",
        json.dumps({"host": "smtp.example.com", "port": "not-a-number", "username": "u", "password": "p"}),
        label="bad port",
    )
    _fake_smtp(monkeypatch)
    r = email_tools.send_email(to="a@b.com", subject="x", body="y")
    assert not r.ok and r.status == "send_failed"  # int(port) failure caught -> structured result


def test_disconnect_while_awaiting_reply_is_unknown_not_failed(monkeypatch) -> None:
    """A server that accepted DATA then closed WITHOUT a reply is an UNKNOWN delivery.

    SMTPServerDisconnected subclasses SMTPResponseException, so a naive except-order
    classifies the silent close as a spoken 4xx/5xx failure and invites a blind resend
    (found by evidence/red_repro.py's clearly-marked transport double on the candidate).
    [transport double: socket layer only]"""
    import smtplib as _smtplib

    _store_smtp()

    class DropAfterData(_smtplib.SMTP):
        def __init__(self, *a, **k):
            pass  # never opens a socket: this double exists to raise at the right phase

        def ehlo(self):
            return (250, b"ok")

        def starttls(self, context=None):
            return (220, b"ok")

        def login(self, u, p):
            return (235, b"ok")

        def sendmail(self, *a, **k):
            raise _smtplib.SMTPServerDisconnected("Connection unexpectedly closed: timed out")

        def quit(self):
            return (221, b"bye")

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", DropAfterData)
    r = email_tools.send_email(to="a@b.com", subject="x", body="y")
    assert r.ok and r.status == "delivery_unknown"
    assert "UNKNOWN" in r.message
    assert usage_quota.usage_today("email.send") == 0
