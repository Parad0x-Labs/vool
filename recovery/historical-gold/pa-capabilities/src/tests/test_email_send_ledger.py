"""B3 (PA lane): send idempotency + ambiguous-outcome honesty.

All SMTP transports here are MOCKED (monkeypatched ``smtplib.SMTP_SSL``) — per the
repo's evidence doctrine these tests prove the ledger's decision logic, not live
provider behavior. No exactly-once SMTP delivery is claimed; what is pinned is
that the runtime refuses to blind-retry content with an unobservable outcome.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core import credential_store, email_send_ledger, email_tools, runtime_paths, usage_quota


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NULLA_HOME", str(tmp_path))
    monkeypatch.delenv("NULLA_TIER", raising=False)
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


def _fake_smtp(monkeypatch, *, send_behavior=None) -> list:
    """Fake SMTP recording send_message calls. `send_behavior(msg)` may raise to
    simulate a transport failure AFTER the server may have accepted the DATA."""
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, context=None):
            self.host, self.port = host, port

        def login(self, _u, _p):
            pass

        def send_message(self, msg):
            if send_behavior is not None:
                send_behavior(msg)
            sent.append(msg)

        def close(self):
            pass

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", FakeSMTP)
    return sent


def test_identical_resend_within_window_is_suppressed(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    first = email_tools.send_email(to="a@b.com", subject="hello", body="same body")
    assert first.ok and first.status == "executed"
    second = email_tools.send_email(to="a@b.com", subject="hello", body="same body")
    assert second.ok and second.status == "duplicate_suppressed"
    assert len(sent) == 1  # no second wire attempt
    assert usage_quota.usage_today("email.send") == 1  # the suppressed copy is not metered


def test_recipient_order_does_not_mint_a_new_key(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    email_tools.send_email(to=["x@b.com", "a@b.com"], subject="s", body="b")
    second = email_tools.send_email(to=["a@b.com", "x@b.com"], subject="s", body="b")
    assert second.status == "duplicate_suppressed" and len(sent) == 1


def test_timeout_after_data_is_unknown_and_retry_is_refused(monkeypatch) -> None:
    import smtplib

    _store_smtp()
    wire: list = []

    def accept_then_disconnect(msg):
        wire.append(msg)  # the server accepted the payload…
        raise smtplib.SMTPServerDisconnected("connection lost after DATA")  # …then died

    sent = _fake_smtp(monkeypatch, send_behavior=accept_then_disconnect)
    first = email_tools.send_email(to="a@b.com", subject="retry me", body="body")
    assert not first.ok and first.status == "send_unknown"
    assert len(wire) == 1
    assert usage_quota.usage_today("email.send") == 0  # unknown is not a counted success

    # A retry of the identical content must be refused, not blind-retried.
    second = email_tools.send_email(to="a@b.com", subject="retry me", body="body")
    assert not second.ok and second.status == "send_unknown"
    assert len(wire) == 1  # still exactly one wire attempt
    assert len(sent) == 0

    row = email_send_ledger.lookup(first.details["send_key"])
    assert row is not None and row["status"] == "unknown"


def test_connect_failure_is_definite_and_retry_sends(monkeypatch) -> None:
    _store_smtp()

    class Refused:
        def __init__(self, *_a, **_k):
            raise OSError("connection refused")

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", Refused)
    first = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert not first.ok and first.status == "send_failed"

    sent = _fake_smtp(monkeypatch)  # provider recovers
    second = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert second.ok and second.status == "executed" and len(sent) == 1


def test_auth_failure_is_definite(monkeypatch) -> None:
    import smtplib

    _store_smtp()

    class BadAuth:
        def __init__(self, *_a, **_k):
            pass

        def login(self, _u, _p):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

        def send_message(self, msg):
            raise AssertionError("must not reach send_message after failed login")

        def close(self):
            pass

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", BadAuth)
    r = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert not r.ok and r.status == "send_failed"
    row = email_send_ledger.lookup(r.details.get("send_key") or "")
    assert row is None or row["status"] != "unknown"


def test_after_dedup_window_a_fresh_attempt_is_allowed(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    email_tools.send_email(to="a@b.com", subject="s", body="b", dedup_window_seconds=60)
    assert len(sent) == 1

    # Advance the ledger's clock past the window: same content, new legitimate attempt.
    real_now = email_send_ledger.lookup(
        email_send_ledger.send_key_for(account="default", recipients=["a@b.com"], subject="s", body="b")
    )
    assert real_now is not None
    later = datetime.now(timezone.utc) + timedelta(seconds=120)
    orig_begin = email_send_ledger.begin_attempt

    def _shifted(**kwargs):
        kwargs["now_fn"] = lambda: later
        return orig_begin(**kwargs)

    monkeypatch.setattr(email_tools.email_send_ledger, "begin_attempt", _shifted)
    second = email_tools.send_email(to="a@b.com", subject="s", body="b", dedup_window_seconds=60)
    assert second.ok and second.status == "executed" and len(sent) == 2


def test_resolve_unknown_sent_suppresses_and_failed_unlocks(monkeypatch) -> None:
    import smtplib

    _store_smtp()
    wire: list = []

    def accept_then_disconnect(msg):
        wire.append(msg)
        raise smtplib.SMTPServerDisconnected("lost")

    _fake_smtp(monkeypatch, send_behavior=accept_then_disconnect)
    first = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert first.status == "send_unknown"
    key = first.details["send_key"]

    # The user checked: it never arrived.
    assert email_send_ledger.resolve_unknown(key, resolved_status="failed", note="not in Sent")
    sent = _fake_smtp(monkeypatch)
    second = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert second.ok and second.status == "executed" and len(sent) == 1

    # The user checked the other branch: it DID arrive.
    assert email_send_ledger.resolve_unknown(
        email_send_ledger.send_key_for(account="default", recipients=["a@b.com"], subject="s2", body="b2"),
        resolved_status="sent",
    ) is False  # no such row -> refuses to invent one


def test_ledger_storage_failure_fails_closed_no_wire_attempt(monkeypatch) -> None:
    # REVISED SEMANTICS: begin_attempt fails CLOSED. No durable reservation
    # means no external execution — the old fail-open behavior is gone.
    _store_smtp()
    sent = _fake_smtp(monkeypatch)

    def broken_connection():
        raise RuntimeError("sqlite unavailable")

    gate = email_send_ledger.begin_attempt(
        send_key="k", account="default", recipients=["a@b.com"], subject="s",
        content_key="ck", get_connection_fn=broken_connection,
    )
    assert gate.get("blocked") is True and gate.get("reason") == "ledger_unavailable"

    # Wire the broken store into the send path at the LEDGER SEAM only (the
    # credential/quota stores stay healthy — this isolates the failure under test).
    real_begin = email_send_ledger.begin_attempt

    def broken_begin(**kwargs):
        kwargs["get_connection_fn"] = broken_connection
        return real_begin(**kwargs)

    monkeypatch.setattr(email_tools.email_send_ledger, "begin_attempt", broken_begin)
    r = email_tools.send_email(to="a@b.com", subject="s", body="b")
    assert not r.ok and r.status == "send_blocked"
    assert "nothing was sent" in r.message
    assert len(sent) == 0  # nothing reached the wire
    assert usage_quota.usage_today("email.send") == 0  # not metered either


def test_same_action_retry_vs_new_action_identical_content(monkeypatch) -> None:
    # IDENTITY LAW: a content hash alone is not action identity.
    _store_smtp()
    sent = _fake_smtp(monkeypatch)
    # Action A (an approval) sends once.
    first = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-A")
    assert first.ok and first.status == "executed"
    # A RETRY of the SAME action (double-approve, crash-before-mark, turn retry)
    # is suppressed — same send_key, sent row.
    retry = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-A")
    assert retry.ok and retry.status == "duplicate_suppressed" and len(sent) == 1
    # A NEW action (a fresh approval of identical content) is a different
    # authorization: it sends, with the recent-identical note as information.
    second = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-B")
    assert second.ok and second.status == "executed" and len(sent) == 2
    assert second.details.get("recent_identical_sends") == 1


def test_unresolved_unknown_gates_every_action_on_that_content(monkeypatch) -> None:
    import smtplib

    _store_smtp()

    def accept_then_disconnect(msg):
        raise smtplib.SMTPServerDisconnected("lost after DATA")

    _fake_smtp(monkeypatch, send_behavior=accept_then_disconnect)
    first = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-A")
    assert first.status == "send_unknown"

    # A DIFFERENT action with identical content is still refused: nobody knows
    # whether the first reached the wire; a fresh approval does not observe it.
    second = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-B")
    assert not second.ok and second.status == "send_unknown"
    assert second.details.get("content_gate") is True

    # The user checks: it never arrived. The gate opens for the new action.
    assert email_send_ledger.resolve_unknown(first.details["send_key"], resolved_status="failed")
    sent = _fake_smtp(monkeypatch)
    third = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-B")
    assert third.ok and third.status == "executed" and len(sent) == 1


def test_outcome_recording_failure_after_wire_success_is_reported_not_hidden(monkeypatch) -> None:
    _store_smtp()
    sent = _fake_smtp(monkeypatch)

    def failing_record(*_a, **_k):
        return False  # the outcome commit fails; the row stays 'attempting'

    monkeypatch.setattr(email_tools.email_send_ledger, "record_outcome", failing_record)
    r = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-A")
    assert r.ok and r.status == "executed" and len(sent) == 1  # the effect happened
    assert r.details.get("ledger_warning") == "outcome_recording_failed"  # and is flagged

    # The stranded 'attempting' row conservatively refuses a same-action retry.
    monkeypatch.undo()
    retry = email_tools.send_email(to="a@b.com", subject="s", body="b", action_key="approval-A")
    assert not retry.ok and retry.status == "send_unknown" and len(sent) == 1
