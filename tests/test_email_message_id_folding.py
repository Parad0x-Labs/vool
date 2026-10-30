"""R10: Message-ID / In-Reply-To / References survive RFC 5322 folding, padding and comments.

A long generated Message-ID is folded onto its own line when the message is serialised
(`Message-ID:\\n <id>`), so the raw header value read back carries `'\\n <id>'`. Every id we
store, compare or put on the wire is the canonical `<left@right>` form, so replies still thread
and reconciliation still finds the sent copy."""
from __future__ import annotations

import email as email_lib
import json

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


def _store_imap(account: str = "default") -> None:
    blob = {"host": "imap.example.com", "port": 993, "username": "me@example.com", "password": "pw",
            "from_addr": "me@example.com"}
    credential_store.store_credential(f"email.imap.{account}", json.dumps(blob), label="test imap")


def _fake_imap(monkeypatch, messages: dict[bytes, bytes]) -> None:
    class FakeIMAP:
        def __init__(self, host, port, ssl_context=None, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def login(self, _u, _p):
            pass

        def select(self, _folder, readonly=False):
            return ("OK", [b"1"])

        def search(self, _charset, *_criteria):
            return ("OK", [b" ".join(messages.keys())])

        def fetch(self, mid, _spec):
            return ("OK", [(b"1 (BODY[] {N}", messages.get(mid, b"")), b")"])

    monkeypatch.setattr(email_tools.imaplib, "IMAP4_SSL", FakeIMAP)


PARENT = "<parent.1234@mail.example.com>"
CHILD = "<child.5678@mail.example.com>"


def _folded(headers: str, body: str = "hello") -> bytes:
    return (headers.replace("\n", "\r\n") + "\r\n\r\n" + body + "\r\n").encode()


@pytest.mark.parametrize("raw, expected", [
    ("<abc@host>", "<abc@host>"),
    ("\n <abc@host>", "<abc@host>"),
    ("\r\n\t<abc@host>  ", "<abc@host>"),
    (" <abc@host> (sent by the gateway)", "<abc@host>"),
    ("(leading (nested) comment) <abc@host>", "<abc@host>"),
    ("<abc@\n host>", "<abc@host>"),
    ("", ""),
    (None, ""),
])
def test_one_message_id_normalises_to_its_canonical_form(raw, expected):
    assert email_tools.normalize_message_id(raw) == expected


def test_references_lists_every_id_in_order_across_folds_and_comments():
    raw = "<a@x>\n <b@x> (forwarded)\n\t<c@x>  <a@x>"
    assert email_tools.message_id_list(raw) == ["<a@x>", "<b@x>", "<c@x>"]
    # A list whose items each hold several ids flattens, in order, without duplicates.
    assert email_tools.message_id_list(["<a@x> <b@x>", "\n <c@x>", "<b@x>"]) == ["<a@x>", "<b@x>", "<c@x>"]


def test_a_long_generated_id_round_trips_through_header_folding():
    """The root of R10: make_msgid with a long domain folds onto its own line on the wire."""
    from email.message import EmailMessage

    long_id = email_tools._generate_message_id("me@" + "sub." * 12 + "example.com")
    message = EmailMessage()
    message["From"] = "me@example.com"
    message["Message-ID"] = long_id
    message.set_content("x")
    raw_value = email_lib.message_from_bytes(message.as_bytes()).get("Message-ID")
    assert email_tools.normalize_message_id(raw_value) == long_id


def test_threading_headers_carry_clean_ids_from_padded_and_multi_id_inputs():
    headers = email_tools.threading_headers(in_reply_to="\n " + CHILD + " ",
                                            references=["\n <root@x>  " + PARENT, " (c) <root@x>"])
    assert headers == {"In-Reply-To": CHILD, "References": f"<root@x> {PARENT} {CHILD}"}


def test_open_message_matches_a_folded_message_id_and_returns_clean_threading(monkeypatch):
    _store_imap()
    child = _folded(
        "From: a@example.com\nSubject: Re: hi\nDate: Mon, 1 Jan 2026 00:01:00 +0000\n"
        f"Message-ID:\n {CHILD}\nIn-Reply-To: (reply)\n {PARENT}\nReferences:\n <root@x>\n\t{PARENT}"
    )
    _fake_imap(monkeypatch, {b"1": child})
    result = email_tools.open_message(message_id=" " + CHILD + "\n")
    assert result.ok, result.message
    entry = result.messages[0]
    assert entry["message_id"] == CHILD
    assert entry["in_reply_to"] == PARENT
    assert entry["references"] == f"<root@x> {PARENT}"
    # A reply built from what was read threads on clean ids.
    headers = email_tools.threading_headers(in_reply_to=entry["message_id"],
                                            references=entry["references"].split())
    assert headers == {"In-Reply-To": CHILD, "References": f"<root@x> {PARENT} {CHILD}"}


def test_open_thread_links_messages_whose_headers_are_folded(monkeypatch):
    _store_imap()
    parent = _folded(f"From: b@example.com\nSubject: hi\nDate: Mon, 1 Jan 2026 00:00:00 +0000\n"
                     f"Message-ID:  {PARENT}  (origin)")
    child = _folded(f"From: a@example.com\nSubject: Re: hi\nDate: Mon, 1 Jan 2026 00:01:00 +0000\n"
                    f"Message-ID:\n {CHILD}\nIn-Reply-To:\n {PARENT}\nReferences:\n {PARENT}")
    _fake_imap(monkeypatch, {b"1": parent, b"2": child})
    result = email_tools.open_thread(message_id=PARENT)
    assert result.ok, result.message
    assert [m["message_id"] for m in result.messages] == [PARENT, CHILD]


def test_sent_folder_reconciliation_finds_a_folded_copy_of_our_id(monkeypatch):
    _store_imap()
    sent = _folded(f"Message-ID:\n {CHILD}")
    _fake_imap(monkeypatch, {b"1": sent})
    found = email_tools.message_in_folder(message_id="\n " + CHILD)
    assert found.ok and found.found, found.message


def test_the_wire_message_keeps_a_long_message_id_on_its_header_line(monkeypatch):
    """The sending side: a long reserved id is not folded off its header line on the wire, so a
    receiver reading the raw header gets exactly the id we reserved (the flaky served test of R10)."""
    from email import policy as mail_policy
    from email.parser import BytesParser

    blob = {"host": "smtp.example.com", "port": 465, "username": "me@example.com", "password": "pw",
            "from_addr": "me@orchard-farm.example.test"}
    credential_store.store_credential("email.smtp.default", json.dumps(blob), label="test smtp")
    sent: list[bytes] = []

    class FakeSMTP:
        def login(self, *_a):
            pass

        def send_message(self, message, *_a, **_k):
            # What smtplib puts on the wire: the message flattened under its own policy.
            sent.append(message.as_bytes(policy=message.policy.clone(linesep="\r\n")))
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(email_tools, "_smtp_connect", lambda *_a, **_k: FakeSMTP())
    # The served test's real reserved id: it fits on a line of its own but not beside "Message-ID: ",
    # which is exactly when the default policy folds it.
    reserved_id = "<179328941894.10714.16952925255350836330@orchard-farm.example.test>"
    result = email_tools.send_email(to="you@example.com", subject="s", body="b",
                                    message_id_override=reserved_id)
    assert result.ok, result.message
    reserved = result.details["message_id"]
    assert reserved == reserved_id and len(f"Message-ID: {reserved}") > 78 >= len(f" {reserved}")
    wire = BytesParser(policy=mail_policy.default).parsebytes(sent[0])
    assert str(wire["Message-ID"]) == reserved


def test_search_results_report_the_canonical_id(monkeypatch):
    _store_imap()
    _fake_imap(monkeypatch, {b"1": _folded(f"From: a@example.com\nSubject: s\nMessage-ID:\n {CHILD}")})
    result = email_tools.read_email(limit=5)
    assert result.ok, result.message
    assert [m["message_id"] for m in result.messages] == [CHILD]
