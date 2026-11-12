"""A sent email's stored Message-ID must equal the header value, in one canonical form.

Long ids fold when the message is serialised: the generator moves the whole id to a
continuation line the moment ``Message-ID: <id>`` passes the 78-column limit, and a
long domain or a long local part gets there easily (the generated local part grows
with every extra digit of its random component, which is why the journey test only
failed some runs). Every reader then unfolds differently — an app runtime's compat32
parse reads the fold back with a leading newline, a policy.default parse with a space,
and a mid-value fold (a References-style header, or a wild server copy wrapped mid-id)
keeps the newline inside the value — so ids are made, read, stored and compared in ONE
whitespace-free ``<...>`` form at every boundary of core.email_tools.

What is exercised: the send-path serialisation (build -> as_bytes -> parse back), the
receipt boundary send_email returns to the draft store, the threading headers a reply
carries, and the lookups over server copies that arrive already folded. No network, no
real account: the provider transport is a recorded-shape double that hands send_email
exactly the receipt values real runtimes were observed to produce.
"""
from __future__ import annotations

import email as email_lib
import json
from email import policy as mail_policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota
from core.email_tools import EmailResult, _generate_message_id, _header_safe
from core.email_tools import (
    _references_of,
    _summary_from_parsed,
    open_message,
    send_email,
    threading_headers,
)

AUDIT_DOMAIN = "orchard-farm.example.test"  # the account the intermittent failure came from
#: The serialiser folds ``Message-ID: <id>`` onto a continuation line only when the
#: whole header passes 78 columns AND the continuation itself still fits: a total id
#: length of 67-77 chars. The generated ids for AUDIT_DOMAIN land at 66-68 (the random
#: local part grows by a digit now and then), which is exactly why the journey test
#: only failed on some runs; the two built ids below sit inside the window so the
#: fold is deterministic.
FOLDED_LONG_DOMAIN_ID = "<" + "x" * 20 + "@" + "y" * 40 + ".test>"
FOLDED_LONG_LOCAL_ID = "<" + "l" * 55 + "@x.example.test>"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_TIER", raising=False)
    runtime_paths.configure_runtime_home(tmp_path)
    usage_quota.reset_usage()
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _smtp_account():
    credential_store.store_credential(
        "email.smtp.default",
        json.dumps({"host": "127.0.0.1", "port": 1587, "username": f"me@{AUDIT_DOMAIN}",
                    "password": "pw", "from_addr": f"me@{AUDIT_DOMAIN}", "security": "plain"}),
        label="test smtp",
    )


def _send_path_wire(message_id: str, *, extra_headers: dict[str, str] | None = None) -> bytes:
    """Serialise exactly the way send_email does before the wire."""
    message = EmailMessage()
    message["From"] = _header_safe(f"me@{AUDIT_DOMAIN}")
    message["To"] = _header_safe("supply@willowridge-orchard.example.test")
    message["Subject"] = _header_safe("Re: Delivery scheduling")
    message["Message-ID"] = message_id
    for key, value in (extra_headers or {}).items():
        message[key] = _header_safe(value)
    message.set_content("Wednesday 10:00 is confirmed.")
    return message.as_bytes()


class _ReceiptAdapter:
    """A recorded-shape provider transport: it hands back the SAME receipt id the
    provider adapters build (a compat32 read of the raw MIME, nothing more), or one
    of the whitespace-carrying reads other runtimes were observed to produce for the
    same folded header. send_email must canonicalise every one of them."""

    def __init__(self, receipt_id: str = ""):
        self.receipt_id = receipt_id
        self.wire = b""

    def send(self, *, raw_mime: bytes, thread_id: str = "", reply_to_rfc: str = "",
             expected_identity: str = "") -> EmailResult:
        self.wire = raw_mime
        message_id = self.receipt_id or str(email_lib.message_from_bytes(raw_mime).get("Message-ID") or "")
        return EmailResult(True, "executed", "Provider accepted the message for sending.",
                           details={"to": ["supply@willowridge-orchard.example.test"],
                                    "subject": "Re: Delivery scheduling", "message_id": message_id,
                                    "provider": "gmail", "acceptance": "provider_accepted"})


def _wire_message_id(raw: bytes) -> str:
    return str(BytesParser(policy=mail_policy.default).parsebytes(raw)["Message-ID"])


def _compat32_message_id(raw: bytes) -> str:
    return str(email_lib.message_from_bytes(raw).get("Message-ID") or "")


def test_long_domain_id_folds_on_the_wire_and_reads_back_in_one_form() -> None:
    """A long sender domain folds the header on the wire; both parsers and the stored
    form must agree on ONE id. The generated id for the audit's own account sits at the
    fold boundary, so the same invariant is asserted for it whatever this run rolled."""
    message_id = _generate_message_id(f"me@{AUDIT_DOMAIN}")
    assert message_id.startswith("<") and message_id.endswith(">") and " " not in message_id
    generated_raw = _send_path_wire(message_id)
    assert _compat32_message_id(generated_raw).strip() == message_id
    assert _wire_message_id(generated_raw).strip() == message_id

    raw = _send_path_wire(FOLDED_LONG_DOMAIN_ID)
    assert b"Message-ID:\n" in raw  # the fold this whole file is about really happened
    compat32 = _compat32_message_id(raw)
    unfolded = _wire_message_id(raw)
    assert compat32 != "" and unfolded != ""
    assert "".join(compat32.split()) == FOLDED_LONG_DOMAIN_ID
    assert "".join(unfolded.split()) == FOLDED_LONG_DOMAIN_ID


def test_long_local_part_id_folds_on_the_wire_and_reads_back_in_one_form() -> None:
    """Same law for a long local part: the fold happens, the id survives it in one form."""
    raw = _send_path_wire(FOLDED_LONG_LOCAL_ID)
    assert b"Message-ID:\n" in raw
    assert "".join(_compat32_message_id(raw).split()) == FOLDED_LONG_LOCAL_ID
    assert "".join(_wire_message_id(raw).split()) == FOLDED_LONG_LOCAL_ID
    summary = _summary_from_parsed(email_lib.message_from_bytes(raw))
    assert summary["message_id"] == FOLDED_LONG_LOCAL_ID


def test_receipt_message_id_is_canonical_whatever_the_runtime_read(monkeypatch) -> None:
    """The stored sent id equals the header value: send_email returns ONE canonical
    receipt id even when the adapter's parse kept the fold's whitespace (observed
    cross-runtime reads of the same folded header)."""
    message_id = _generate_message_id(f"me@{AUDIT_DOMAIN}")
    adapter = _ReceiptAdapter()
    monkeypatch.setattr(email_tools, "_provider_adapter", lambda account, creds: adapter)
    observed_reads = [
        "",  # adapter parses the wire itself, exactly like the provider transport
        "\n " + message_id,  # an app runtime's compat32 read of the folded header
        " " + message_id,  # the same header read through policy.default
    ]
    for read in observed_reads:
        adapter.receipt_id = read
        result = send_email(to="supply@willowridge-orchard.example.test",
                            subject="Re: Delivery scheduling", body="Confirmed.", account="default",
                            message_id_override=message_id)
        assert result.ok and result.status == "executed", result.message
        stored = result.details["message_id"]
        assert stored == message_id, (read, stored)
        assert _wire_message_id(adapter.wire) == message_id


def test_reply_to_a_folded_parent_carries_clean_threading_headers(monkeypatch) -> None:
    """A parent id read off a folded header must not carry the fold into the reply:
    clean In-Reply-To, complete clean References — on the wire too."""
    folded_parent = "\n <wro-20260910@willowridge-orchard.example.test>"
    headers = threading_headers(in_reply_to=folded_parent,
                                references=["<root-1@willowridge-orchard.example.test>",
                                            "\n <mid-2@willowridge-orchard.example.test>"])
    assert headers is not None
    assert headers["In-Reply-To"] == "<wro-20260910@willowridge-orchard.example.test>"
    assert headers["References"] == ("<root-1@willowridge-orchard.example.test> "
                                     "<mid-2@willowridge-orchard.example.test> "
                                     "<wro-20260910@willowridge-orchard.example.test>")

    adapter = _ReceiptAdapter()
    monkeypatch.setattr(email_tools, "_provider_adapter", lambda account, creds: adapter)
    result = email_tools.reply_email(to="supply@willowridge-orchard.example.test",
                                     subject="Delivery scheduling", body="Confirmed.",
                                     in_reply_to=folded_parent,
                                     references=["<root-1@willowridge-orchard.example.test>",
                                                 "\n <mid-2@willowridge-orchard.example.test>"])
    assert result.ok, result.message
    wire = BytesParser(policy=mail_policy.default).parsebytes(adapter.wire)
    assert str(wire["In-Reply-To"]) == "<wro-20260910@willowridge-orchard.example.test>"
    assert str(wire["References"]).split() == ["<root-1@willowridge-orchard.example.test>",
                                               "<mid-2@willowridge-orchard.example.test>",
                                               "<wro-20260910@willowridge-orchard.example.test>"]


@pytest.fixture()
def _imap_account():
    credential_store.store_credential(
        "email.imap.default",
        json.dumps({"host": "127.0.0.1", "port": 1143, "username": f"me@{AUDIT_DOMAIN}",
                    "password": "pw", "security": "plain"}),
        label="test imap",
    )


def _server_copy(message_id_header: str, references_header: str) -> bytes:
    return (f"From: Willow Ridge Orchard Supply <supply@willowridge-orchard.example.test>\n"
            f"To: me@orchard-farm.example.test\n"
            f"Subject: Delivery scheduling\n"
            f"Date: Wed, 10 Sep 2026 09:15:00 +0000\n"
            f"{message_id_header}\n"
            f"{references_header}\n"
            f"\nWe can bring the apple crates on Tuesday.\n").encode()


def test_folded_server_copy_summarises_and_matches_in_one_form(monkeypatch, _imap_account) -> None:
    """A server copy arrives with the header already folded (fold at the value start,
    and a wild mid-value fold): summaries, lookups and references all use the one form."""
    value_folded = _server_copy("Message-ID:\n <wro-20260910@willowridge-orchard.example.test>",
                                "References: <root-1@willowridge-orchard.example.test>\n"
                                " <mid-2@willowridge-orchard.example.test>")
    mid_folded = _server_copy("Message-ID: <kiln-1@north-facility-mail.orchard-farm.example.test>\n"
                              " <kiln-2@north-facility-mail.orchard-farm.example.test>",
                              "References: <k-root@north-facility-mail.orchard-farm.example.test>")
    # A Message-ID carries exactly one msg-id (RFC 5322). The line's parser
    # (core.email_tools.normalize_message_id) keeps the FIRST id of a wild two-id value;
    # summaries, references and lookups all agree on that one form.
    for raw, wanted in ((value_folded, "<wro-20260910@willowridge-orchard.example.test>"),
                        (mid_folded, "<kiln-1@north-facility-mail.orchard-farm.example.test>")):
        parsed = email_lib.message_from_bytes(raw)
        assert _summary_from_parsed(parsed)["message_id"] == wanted
        assert wanted in _references_of(email_lib.message_from_bytes(
            _server_copy("Message-ID: <child@orchard-farm.example.test>",
                         f"References: {wanted}")))
        monkeypatch.setattr(email_tools, "_fetch_all", lambda creds, folder: ([(b"3", raw)], 1))
        monkeypatch.setattr(email_tools, "_needs_credentials", lambda kind, account: None)
        opened = open_message(message_id=wanted)
        assert opened.ok and opened.status == "executed", opened.message
        assert opened.messages[0]["message_id"] == wanted


def test_generated_and_overridden_ids_share_the_one_form(monkeypatch) -> None:
    """The id a draft reserved (even one that somehow carried a fold) and the id on the
    wire are the same canonical value."""
    adapter = _ReceiptAdapter()
    monkeypatch.setattr(email_tools, "_provider_adapter", lambda account, creds: adapter)
    result = send_email(to="supply@willowridge-orchard.example.test",
                        subject="Re: Delivery scheduling", body="Confirmed.",
                        message_id_override="\n <reserved-20260910@orchard-farm.example.test>")
    assert result.ok, result.message
    assert result.details["message_id"] == "<reserved-20260910@orchard-farm.example.test>"
    assert _wire_message_id(adapter.wire) == "<reserved-20260910@orchard-farm.example.test>"
