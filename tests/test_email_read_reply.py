"""Native email read (IMAP) + reply (signature, Re:, threading), fully mocked, no live connection."""
from __future__ import annotations

import json
from email.message import EmailMessage

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota
from core.runtime_execution_tools import _email_read, _email_reply


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


def _store(kind: str, account: str = "default") -> None:
    blob = {
        "host": f"{kind}.example.com", "port": 993 if kind == "imap" else 465,
        "username": "me@example.com", "password": "pw", "from_addr": "me@example.com",
    }
    credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"test {kind}")


def _raw(frm: str, subj: str, body: str, mid: str) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = subj
    m["Message-ID"] = mid
    m["Date"] = "Mon, 1 Jan 2026 00:00:00 +0000"
    m.set_content(body)
    return m.as_bytes()


def _fake_imap(monkeypatch, messages: dict) -> None:
    class FakeIMAP:
        def __init__(self, host, port):
            self.host, self.port = host, port

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, _u, _p):
            pass

        def select(self, _folder, readonly=False):
            return ("OK", [b"1"])

        def search(self, _charset, _criteria):
            return ("OK", [b" ".join(messages.keys())])

        def fetch(self, mid, _spec):
            return ("OK", [(b"1 (BODY[] {N}", messages.get(mid, b"")), b")"])

    monkeypatch.setattr(email_tools.imaplib, "IMAP4_SSL", FakeIMAP)


def _fake_smtp(monkeypatch) -> list:
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, context=None, timeout=None):
            pass

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


def test_read_without_credentials() -> None:
    r = email_tools.read_email()
    assert not r.ok and r.status == "needs_credentials"


def test_read_returns_parsed_messages(monkeypatch) -> None:
    _store("imap")
    _fake_imap(monkeypatch, {
        b"1": _raw("alice@x.com", "Hello", "Hi there", "<a>"),
        b"2": _raw("bob@x.com", "Ping", "Yo", "<b>"),
    })
    r = email_tools.read_email(limit=10)
    assert r.ok and r.status == "executed" and len(r.messages) == 2
    assert {m["subject"] for m in r.messages} == {"Hello", "Ping"}
    assert any("Hi there" in m["snippet"] for m in r.messages)


def test_read_failure_is_caught(monkeypatch) -> None:
    _store("imap")

    class Boom:
        def __init__(self, *_a, **_k):
            raise OSError("refused")

    monkeypatch.setattr(email_tools.imaplib, "IMAP4_SSL", Boom)
    r = email_tools.read_email()
    assert not r.ok and r.status == "read_failed"


def test_reply_adds_signature_re_prefix_and_threading(monkeypatch) -> None:
    _store("smtp")
    # The signature lives in the Operator Profile (the one authority), not the JSON preferences.
    from tests.operator_profile_rig import set_owner_email_signature

    set_owner_email_signature("-- aiask")
    try:
        sent = _fake_smtp(monkeypatch)
        r = email_tools.reply_email(to="alice@x.com", subject="Hello", body="Sounds good", in_reply_to="<orig>")
        assert r.ok
        msg = sent[0]
        assert msg["Subject"] == "Re: Hello"
        assert msg["In-Reply-To"] == "<orig>"
        assert "aiask" in msg.get_content()
    finally:
        set_owner_email_signature("")


def test_reply_does_not_double_prefix_re(monkeypatch) -> None:
    _store("smtp")
    sent = _fake_smtp(monkeypatch)
    email_tools.reply_email(to="a@x.com", subject="Re: Hi", body="ok")
    assert sent[0]["Subject"] == "Re: Hi"


def test_read_handler_returns_messages(monkeypatch) -> None:
    _store("imap")
    _fake_imap(monkeypatch, {b"1": _raw("a@x.com", "S", "B", "<i>")})
    res = _email_read({"limit": 5})
    assert res.ok and res.status == "executed" and res.details["messages"]


def test_reply_handler_preview_then_send(monkeypatch) -> None:
    _store("smtp")
    preview = _email_reply({"to": "a@x.com", "subject": "Hi", "body": "yo"})
    assert not preview.ok and preview.status == "user_action_required"
    sent = _fake_smtp(monkeypatch)
    done = _email_reply({"to": "a@x.com", "subject": "Hi", "body": "yo", "allow_send": True, "approve": True})
    assert done.ok and len(sent) == 1


def test_reply_with_crlf_message_id_does_not_crash(monkeypatch) -> None:
    _store("smtp")
    sent = _fake_smtp(monkeypatch)
    # A crafted/folded Message-ID from an incoming email must not crash the read -> reply path.
    r = email_tools.reply_email(
        to="a@x.com", subject="Hi", body="ok", in_reply_to="<orig@host>\r\nBcc: attacker@evil.com"
    )
    assert r.ok and r.status == "executed"
    assert sent[0]["Bcc"] is None
    assert "\r" not in str(sent[0]["In-Reply-To"]) and "\n" not in str(sent[0]["In-Reply-To"])


def test_read_with_non_numeric_port_fails_closed() -> None:
    credential_store.store_credential(
        "email.imap.default",
        json.dumps({"host": "imap.example.com", "port": "xyz", "username": "u", "password": "p"}),
        label="bad imap port",
    )
    r = email_tools.read_email()
    assert not r.ok and r.status == "read_failed"  # int(port) failure caught -> structured result
