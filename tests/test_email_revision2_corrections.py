"""Revision-2 correction tests: each safety repair with ORIGINAL + NOVEL + negative data.

Covers the six review corrections beyond the review's own regressions
(these stay in tests/test_email_integration_review.py, unchanged):

  C1  unknown delivery is sticky — edit/approve refuse, reconcile-only resolution,
      a NEWLY WORDED request is not permission to resend an unresolved effect;
  C2  the send reservation is durable across CALLERS, RESTARTS and PROCESSES —
      real subprocess tests against the real local service;
  C3  strict security-mode allowlist + real loopback identity, incl. auth-order
      enforcement on the wire (no AUTH before STARTTLS);
  C4  approval binds the canonical final message (subject normalization, signature
      fold, References chain, resolved account identity; alias reconfiguration with
      unchanged identity stays valid);
  C5  search-before-limit with a VISIBLE bound (the cap says so, never a silent
      empty result).

Transports are the real local mail service unless explicitly marked [double].
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

from core import credential_store, email_drafts, email_tools
from tests import test_email_live_workflow as _live_module
from tests.test_email_live_workflow import _isolated, _raw, _store_accounts

# The real local-mail fixture, re-exported so pytest resolves `mail_service` here too.
mail_service = _live_module.mail_service

SESSION = "openclaw:revision2"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _msg(frm: str, subj: str, body: str, mid: str, date: str, irt: str | None = None,
         refs: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "orchard@example.test"
    m["Subject"] = subj
    m["Message-ID"] = mid
    m["Date"] = date
    if irt:
        m["In-Reply-To"] = irt
    if refs:
        m["References"] = refs
    m.set_content(body)
    return m.as_bytes()


def _prepare(mail_service, *, body="Wednesday at 10:00 works.", subject="Delivery scheduling",
             account="default", irt="<wro-20260911@willowridge.example.test>",
             refs=("<wro-20260910@willowridge.example.test>",), session=SESSION):
    mail_service.store.add_user("orchard@example.test", "pw-orchard", {"INBOX": [
        _msg("Willow Ridge Supply <supply@willowridge.example.test>", subject,
             "Tuesday at 14:00 or Wednesday at 10:00?", "<wro-20260910@willowridge.example.test>",
             "Wed, 10 Sep 2026 09:15:00 +0000"),
        _msg("Willow Ridge Supply <supply@willowridge.example.test>", f"Re: {subject}",
             "Still need your slot choice.", "<wro-20260911@willowridge.example.test>",
             "Thu, 11 Sep 2026 08:00:00 +0000",
             irt="<wro-20260910@willowridge.example.test>", refs=refs[0] if refs else None),
    ]})
    _store_accounts("orchard@example.test", "pw-orchard", account=account)
    saved = email_drafts.save_draft(
        to="supply@willowridge.example.test", subject=f"Re: {subject}", body=body,
        in_reply_to=irt, references=list(refs or []), kind="reply", account=account,
        session_id=session,
    )
    assert saved.ok, saved.message
    return saved.draft["draft_id"]


# ---------------------------------------------------------------------------
# C1 — unknown delivery is sticky; reconcile is the only resolution
# ---------------------------------------------------------------------------

def test_original_unknown_delivery_survives_reapprove_and_reworded_send(mail_service) -> None:
    draft_id = _prepare(mail_service)
    assert email_drafts.approve_draft(draft_id, session_id=SESSION).ok
    mail_service.drop_after_accept = True
    first = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert first.status == "delivery_unknown" and len(mail_service.store.captured) == 1
    original_mid = first.draft["sent_message_id"]

    mail_service.drop_after_accept = False
    # A DIFFERENTLY WORDED follow-up is not permission to resend the unresolved effect.
    reworded = email_drafts.save_draft(
        draft_id=draft_id, to="supply@willowridge.example.test",
        subject="Re: Delivery scheduling", body="Actually Thursday 09:30 works better.",
        in_reply_to="<wro-20260911@willowridge.example.test>", kind="reply",
        account="default", session_id=SESSION,
    )
    assert reworded.status == "reconciliation_required", reworded.status
    assert email_drafts.approve_draft(draft_id, session_id=SESSION).status == "reconciliation_required"
    again = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert again.status == "delivery_unknown" and len(mail_service.store.captured) == 1
    # The original effect and Message-ID are PRESERVED, not rewritten.
    assert email_drafts.get_draft(draft_id, session_id=SESSION).draft["sent_message_id"] == original_mid

    # Reconcile resolves it — and only then does a fresh decision become possible.
    reconciled = email_drafts.reconcile_draft(draft_id, session_id=SESSION)
    print("RECONCILE:", reconciled.status, "|", reconciled.message[:80], flush=True)
    assert reconciled.ok and reconciled.status == "sent_confirmed"
    # The confirmed effect is immutable: editing that draft is refused, and a NEW
    # draft is the supported path for further words.
    edit = email_drafts.save_draft(
        draft_id=draft_id, to="supply@willowridge.example.test",
        subject="Re: Delivery scheduling", body="One more thing: please bring crates.",
        in_reply_to="<wro-20260911@willowridge.example.test>", kind="reply",
        account="default", session_id=SESSION,
    )
    assert edit.status == "already_sent", edit.status
    fresh = email_drafts.save_draft(
        to="supply@willowridge.example.test", subject="Re: Delivery scheduling",
        body="One more thing: please bring crates.",
        in_reply_to="<wro-20260911@willowridge.example.test>", kind="reply",
        account="default", session_id=SESSION,
    )
    assert fresh.status == "saved" and fresh.draft["draft_id"] != draft_id


def test_novel_unreconciled_inflight_state_is_never_redispatched(mail_service, monkeypatch) -> None:
    """A crash between reservation and receipt (status 'sending' on disk) is read by
    the NEXT request exactly as a restart would read it: no re-dispatch."""
    draft_id = _prepare(mail_service, body="Kiln estimate questions attached.",
                        subject="Kiln repair estimate 8442",
                        irt="<an-1@atelier.example.test>", refs=("<an-0@atelier.example.test>",),
                        session="openclaw:revision2-novel")
    email_drafts.approve_draft(draft_id, session_id="openclaw:revision2-novel")

    from core import email_tools as _et
    original = _et.send_email
    seen: list[dict] = []

    def reservation_then_crash(**kwargs):
        seen.append(kwargs)
        raise KeyboardInterrupt("process died right after the reservation was persisted")

    # Simulate: the reservation IS persisted (send_draft does that first), then the
    # process dies before any network IO. KeyboardInterrupt escapes as a crash would.
    monkeypatch.setattr("core.email_tools.send_email", reservation_then_crash)
    with pytest.raises(KeyboardInterrupt):
        email_drafts.send_draft(draft_id, session_id="openclaw:revision2-novel")
    monkeypatch.setattr("core.email_tools.send_email", original)

    row = email_drafts.get_draft(draft_id, session_id="openclaw:revision2-novel")
    assert row.draft["status"] == "sending" and row.draft["sent_message_id"]

    later = email_drafts.send_draft(draft_id, session_id="openclaw:revision2-novel")
    assert later.status == "send_in_progress" and not mail_service.store.captured
    assert email_drafts.approve_draft(draft_id, session_id="openclaw:revision2-novel").status == "reconciliation_required"
    assert email_drafts.reconcile_draft(draft_id, session_id="openclaw:revision2-novel").status == "send_in_progress"


# ---------------------------------------------------------------------------
# C2 — durable reservation across callers, RESTARTS and PROCESSES
# ---------------------------------------------------------------------------

_DRIVER_SEND = r"""
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import email_drafts
result = email_drafts.send_draft(os.environ["DRAFT_ID"], session_id=os.environ["SESSION"])
print("RESULT:" + result.status)
"""

_DRIVER_PREPARE = r"""
import json, os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import credential_store, email_drafts
credential_store.store_credential(
    "email.smtp.default",
    json.dumps({"host": "127.0.0.1", "port": int(os.environ["SMTP_PORT"]),
                "username": "cross@example.test", "password": "pw",
                "from_addr": "cross@example.test", "security": "plain"}),
    label="cross-process",
)
saved = email_drafts.save_draft(
    to="recipient@example.test", subject="Re: cross-process law", body="one dispatch only.",
    in_reply_to="<cp-parent@example.test>", kind="reply", account="default",
    session_id=os.environ["SESSION"],
)
assert saved.ok, saved.message
approved = email_drafts.approve_draft(saved.draft["draft_id"], session_id=os.environ["SESSION"])
assert approved.ok, approved.message
print("DRAFT_ID:" + saved.draft["draft_id"])
"""


def _spawn(driver: str, env_extra: dict[str, str]) -> subprocess.Popen:
    env = dict(os.environ)
    env.update(REPO_ROOT=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra)
    return subprocess.Popen(
        [sys.executable, "-B", "-c", driver],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def test_real_cross_process_double_send_dispatches_once(mail_service) -> None:
    """Three REAL OS processes, one shared runtime home, one live SMTP service.

    The FIRST process (like a fresh daemon) configures the account, drafts and
    approves; then two processes race to send. Every writer is a plain interpreter
    with identical key-derivation conditions, so the proof exercises only the
    store's cross-process locking and the durable reservation: exactly one
    dispatch, and the loser reads the winner's state instead of re-sending."""
    session = "openclaw:cross-process"
    mail_service.store.add_user("cross@example.test", "pw")
    prep = _spawn(_DRIVER_PREPARE, {"SESSION": session, "SMTP_PORT": str(mail_service.smtp_port)})
    out, err = prep.communicate(timeout=30)
    assert prep.returncode == 0, (out, err[-400:])
    draft_id = next(line.partition("DRAFT_ID:")[2].strip() for line in out.splitlines()
                    if line.startswith("DRAFT_ID:"))

    procs = [_spawn(_DRIVER_SEND, {"SESSION": session, "DRAFT_ID": draft_id}) for _ in range(2)]
    outputs = [proc.communicate(timeout=30) for proc in procs]
    statuses = [line.partition("RESULT:")[2].strip()
                for out, _err in outputs for line in out.splitlines() if line.startswith("RESULT:")]
    assert len(statuses) == 2 and len(mail_service.store.captured) == 1, (statuses, len(mail_service.store.captured))
    # The loser either read the winner's in-flight reservation ('send_in_progress') or
    # its completed terminal receipt ('sent') — either way it dispatched nothing.
    assert set(statuses) <= {"send_in_progress", "sent"}, statuses


def test_real_restart_after_reservation_does_not_redispatch(mail_service) -> None:
    """The 'sending' row on disk is all a RESTARTED process sees: a fresh interpreter
    must refuse to dispatch again (simulated crash persisted the reservation)."""
    draft_id = _prepare(mail_service, session="openclaw:revision2-restart")
    email_drafts.approve_draft(draft_id, session_id="openclaw:revision2-restart")
    # Persist the reservation exactly as send_draft would, then 'crash'.
    with email_drafts._store_lock():
        state = email_drafts._load()
        row = state["drafts"][draft_id]
        row["status"] = "sending"
        row["sent_message_id"] = "<restart-probe@orchard@example.test>"
        row["reservation"] = {"message_id": "<restart-probe@orchard@example.test>", "reserved_at": 0.0}
        email_drafts._save(state)

    proc = _spawn(_DRIVER_SEND, {"SESSION": "openclaw:revision2-restart", "DRAFT_ID": draft_id})
    out, _err = proc.communicate(timeout=30)
    status = next(line.partition("RESULT:")[2].strip() for line in out.splitlines() if line.startswith("RESULT:"))
    assert status == "send_in_progress"
    assert not mail_service.store.captured  # the restarted process dispatched nothing


# ---------------------------------------------------------------------------
# C3 — strict security modes + real loopback identity + auth ordering on the wire
# ---------------------------------------------------------------------------

def test_unknown_security_mode_fails_before_any_socket(tmp_path) -> None:
    credential_store.store_credential(
        "email.smtp.probe", json.dumps({"host": "127.0.0.1", "port": 1, "username": "u",
                                        "password": "p", "security": "starttlss"}),  # one-letter typo
        label="probe",
    )
    result = email_tools.send_email(to="a@b.test", subject="s", body="b", account="probe")
    assert not result.ok and result.status == "send_failed"
    assert "unknown security mode" in result.message


@pytest.mark.parametrize("deceptive", ["localhost.attacker.example", "127.0.0.1.evil.example",
                                       "notlocalhost.example"])
def test_deceptive_hostnames_are_not_loopback(deceptive) -> None:
    assert not email_tools._is_loopback_host(deceptive)


@pytest.mark.parametrize("genuine", ["127.0.0.1", "::1", "localhost", "[::1]", "127.0.0.1:0"])
def test_genuine_loopback_identities_pass(genuine) -> None:
    assert email_tools._is_loopback_host(genuine)


def test_valid_loopback_valid_tls_and_cert_failure_are_distinguished(mail_service) -> None:
    """[real] plain works on real loopback; an untrusted TLS target fails closed with
    the certificate named — never a silent downgrade."""
    mail_service.store.add_user("orchard@example.test", "pw-orchard")
    _store_accounts("orchard@example.test", "pw-orchard")
    result = email_tools.send_email(to="x@y.test", subject="s", body="b")  # plain, real loopback service
    assert result.ok and result.status == "executed"

    import tempfile

    from tests.local_mail_service import LocalMailService

    with LocalMailService(smtp_port=0, imap_port=0, cert_dir=Path(tempfile.mkdtemp()),
                          smtp_tls_port=0, imap_tls_port=0) as tls_service:
        if not tls_service._ssl_context:
            pytest.skip("cert generation unavailable")
        credential_store.store_credential(
            "email.smtp.untrusted", json.dumps({"host": "localhost", "port": tls_service.smtp_tls_port,
                                                "username": "u", "password": "p", "security": "ssl"}),
            label="untrusted",
        )
        refused = email_tools.send_email(to="x@y.test", subject="s", body="b", account="untrusted")
        assert not refused.ok and refused.status == "send_failed"
        assert "CERTIFICATE_VERIFY_FAILED" in refused.message or "certificate" in refused.message.lower()


def test_no_authentication_before_secure_channel_on_the_wire(mail_service) -> None:
    """[real wire] The service enforces the channel order the client must keep:
    AUTH on a cleartext session (when STARTTLS is offered) is refused by the server,
    and the production client authenticates only after the upgrade."""
    import tempfile

    cert_dir = Path(tempfile.mkdtemp())
    from tests.local_mail_service import LocalMailService

    with LocalMailService(smtp_port=0, imap_port=0, cert_dir=cert_dir) as svc:
        svc.store.add_user("order@example.test", "pw", {"INBOX": []})
        # A raw cleartext client attempts AUTH before STARTTLS.
        with socket.create_connection(("127.0.0.1", svc.smtp_port), timeout=5) as sock:
            fh = sock.makefile("rwb")
            fh.readline()  # greeting
            fh.write(b"EHLO probe\r\n")
            fh.flush()
            while True:
                line = fh.readline()
                if line[3:4] != b"-":
                    break
            fh.write(b"AUTH PLAIN AG9yZGVyQGV4YW1wbGUudGVzdABwdw==\r\n")
            fh.flush()
            reply = fh.readline().decode()
        assert reply.startswith("538"), reply  # encryption required before authentication

        # And the production client, on the same service, upgrades first and succeeds.
        credential_store.store_credential(
            "email.smtp.order", json.dumps({"host": "localhost", "port": svc.smtp_port,
                                            "username": "order@example.test", "password": "pw",
                                            "from_addr": "order@example.test", "security": "starttls"}),
            label="order",
        )
        import ssl as _ssl
        from unittest import mock

        from core import email_tools as et

        real_context = _ssl.create_default_context  # captured BEFORE the patch
        cafile = str(cert_dir / "server-cert.pem")
        with mock.patch.object(et.ssl, "create_default_context",
                               lambda *a, **k: real_context(cafile=cafile)):
            result = email_tools.send_email(to="x@y.test", subject="s", body="b", account="order")
        assert result.ok and result.status == "executed", result.message


# ---------------------------------------------------------------------------
# C4 — approval binds the canonical final message and concrete account
# ---------------------------------------------------------------------------

def test_original_subject_and_signature_fold_into_the_approved_hash(mail_service, monkeypatch) -> None:
    from tests.operator_profile_rig import set_owner_email_signature

    set_owner_email_signature("-- orchard desk")
    try:
        draft_id = _prepare(mail_service, body="Wednesday at 10:00 works.")
        row = email_drafts.get_draft(draft_id, session_id=SESSION).draft
        # The CANONICAL form: Re: applied once, signature folded — what the wire gets.
        assert row["subject"] == "Re: Delivery scheduling" and row["body"].endswith("-- orchard desk")
        email_drafts.approve_draft(draft_id, session_id=SESSION)
        sent = email_drafts.send_draft(draft_id, session_id=SESSION)
        assert sent.ok and sent.status == "sent"
        msg = EmailMessage()  # reparse the captured wire message
        import email as email_lib
        msg = email_lib.message_from_bytes(mail_service.store.captured[0]["raw"])
        assert str(msg["Subject"]) == "Re: Delivery scheduling"
        plain = next(p for p in msg.walk() if p.get_content_type() == "text/plain")
        assert "-- orchard desk" in plain.get_payload(decode=True).decode("utf-8", "replace")
    finally:
        set_owner_email_signature("")


def test_novel_account_repoint_same_alias_different_mailbox_needs_reapproval(mail_service) -> None:
    draft_id = _prepare(mail_service, body="Estimate questions follow.",
                        subject="Kiln repair estimate 8442",
                        irt="<an-1@atelier.example.test>", refs=("<an-0@atelier.example.test>",),
                        session="openclaw:revision2-account")
    email_drafts.approve_draft(draft_id, session_id="openclaw:revision2-account")
    # SAME alias, DIFFERENT mailbox behind it (an account reconfiguration).
    credential_store.store_credential(
        "email.smtp.default",
        json.dumps({"host": "127.0.0.1", "port": mail_service.smtp_port,
                    "username": "someone-else@example.test", "password": "pw",
                    "from_addr": "someone-else@example.test", "security": "plain"}),
        label="repointed",
    )
    result = email_drafts.send_draft(draft_id, session_id="openclaw:revision2-account")
    assert result.status == "needs_reapproval" and not mail_service.store.captured
    assert "mailbox" in result.message


def test_credential_refresh_with_unchanged_identity_keeps_approval(mail_service) -> None:
    draft_id = _prepare(mail_service)
    email_drafts.approve_draft(draft_id, session_id=SESSION)
    # A password rotation on the SAME mailbox identity: normal credential refresh.
    mail_service.store.users["orchard@example.test"]["password"] = "a-newer-app-password"
    _store_accounts("orchard@example.test", "a-newer-app-password", account="default")
    result = email_drafts.send_draft(draft_id, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    assert mail_service.store.captured[0]["from"] == "orchard@example.test"


# ---------------------------------------------------------------------------
# C5 — search before limit, with a VISIBLE bound
# ---------------------------------------------------------------------------

def test_original_deep_match_found_behind_many_newer_unrelated_messages(mail_service) -> None:
    target = _msg("supplier@example.test", "Annual estimate", "Target message",
                  "<deep-wanted@example.test>", "Mon, 01 Sep 2026 08:00:00 +0000")
    noise = [_msg("newsletter@example.test", "News digest", "Unrelated",
                  f"<n{i}@example.test>", f"Sat, 1{i % 9} Sep 2026 08:00:00 +0000") for i in range(12)]
    mail_service.store.add_user("deep@example.test", "pw", {"INBOX": [target, *noise]})
    _store_accounts("deep@example.test", "pw")
    result = email_tools.search_email(account="default", sender="supplier@example.test", limit=2)
    assert result.ok and any(m["message_id"] == "<deep-wanted@example.test>" for m in result.messages)
    assert not any(mail_service.store.seen_flags("deep@example.test"))


def test_novel_scan_cap_is_visible_not_silent(mail_service, monkeypatch) -> None:
    """When the scan bound is hit before `limit` matches, the message SAYS so —
    never a confident empty result. [real transport, tiny injected cap]"""
    monkeypatch.setattr(email_tools, "_SEARCH_SCAN_CAP", 3)
    target = _msg("rare@example.test", "Quarterly review", "wanted",
                  "<rare@example.test>", "Mon, 07 Sep 2026 08:00:00 +0000")
    noise = [_msg("newsletter@example.test", "digest", "no",
                  f"<c{i}@example.test>", "Sat, 12 Sep 2026 08:00:00 +0000") for i in range(6)]
    mail_service.store.add_user("cap@example.test", "pw", {"INBOX": [target, *noise]})
    _store_accounts("cap@example.test", "pw")
    result = email_tools.search_email(account="default", sender="rare@example.test", limit=2)
    assert result.ok and result.status == "executed"
    assert "Search bounded" in result.message and "older matches may exist" in result.message
