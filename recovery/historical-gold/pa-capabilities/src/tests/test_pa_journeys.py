"""Cross-cutting PA journeys (goal-mandated): stale state + preference revocation.

MOCKED transports where email is involved; everything else is local state. These
pin the last two journeys from the PA goal list not covered by the per-feature
suites: stale calendar/approval state handled honestly, and preference
revocation flowing into the email lane.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core import credential_store, email_tools, runtime_paths, usage_quota
from core.local_operator_actions import dispatch_operator_action
from core.operator.parser import parse_operator_action_intent
from core.runtime_execution_tools import _email_send


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


def _no_smtp(monkeypatch) -> None:
    class Refused:
        def __init__(self, *_a, **_k):
            raise OSError("no wire in this suite")

    monkeypatch.setattr(email_tools.smtplib, "SMTP_SSL", Refused)


# -- stale email previews ------------------------------------------------------

def test_day_old_email_preview_is_refused_not_approved(monkeypatch) -> None:
    from core import email_approval
    from storage.db import get_connection

    _store_smtp()
    _no_smtp(monkeypatch)
    preview = _email_send({"to": "a@x.com", "subject": "stale", "body": "old draft"})
    assert preview.status == "approval_required"
    action_id = preview.details["action_id"]

    # Age the preview past the TTL directly in the store (the clock is the only lie).
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE operator_action_requests SET created_at = ?, updated_at = ? WHERE action_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=email_approval.DEFAULT_PREVIEW_TTL_SECONDS + 60)).isoformat(),) * 2
            + (action_id,),
        )
        conn.commit()
    finally:
        conn.close()

    intent = parse_operator_action_intent(f"approve send {action_id}")
    result = dispatch_operator_action(intent, task_id="t1", session_id="s1")
    assert not result.ok and result.status == "expired"
    assert "fresh preview" in result.response_text


# -- stale calendar state --------------------------------------------------------

def test_past_meeting_time_is_created_with_an_honest_note(tmp_path, monkeypatch) -> None:
    # A pending calendar approval that sat past its start time must not silently
    # re-parse or pretend freshness: the event is created AS APPROVED, with a note.
    from unittest import mock

    base = tmp_path / "cal"
    agent_intent = parse_operator_action_intent(
        'schedule a meeting "Old Sync" on 2026-01-02 15:30 for 30m'
    )
    assert agent_intent is not None and agent_intent.kind == "schedule_calendar_event"

    preview = dispatch_operator_action(agent_intent, task_id="t1", session_id="s1")
    if preview.status == "approval_required":
        approve_intent = parse_operator_action_intent(f"approve calendar {preview.details['action_id']}")
        assert approve_intent is not None and approve_intent.approval_requested
        result = dispatch_operator_action(approve_intent, task_id="t1", session_id="s1")
    else:
        result = preview  # autonomy mode allowed immediate execution
    assert result.ok and result.status == "executed"
    assert "already in the past" in result.response_text


def test_future_meeting_has_no_past_note(tmp_path) -> None:
    agent_intent = parse_operator_action_intent(
        'schedule a meeting "Future Sync" on 2027-03-08 15:30 for 30m'
    )
    assert agent_intent is not None
    result = dispatch_operator_action(agent_intent, task_id="t1", session_id="s1")
    if result.status == "approval_required":
        approve_intent = parse_operator_action_intent(f"approve calendar {result.details['action_id']}")
        result = dispatch_operator_action(approve_intent, task_id="t1", session_id="s1")
    assert result.ok and "already in the past" not in result.response_text


# -- preference revocation flowing into the email lane --------------------------

def test_default_account_preference_remember_then_revoke() -> None:
    from core import operator_profile
    from core.operator_profile import OWNER_PRINCIPAL

    _store_smtp("work")
    assert email_tools.resolve_default_account("smtp") == "default"  # nothing bound yet

    change = operator_profile.remember(
        OWNER_PRINCIPAL, "default_account.email",
        {"binding_ref": "credential:email.smtp.work"},
    )
    assert change.kind in {"saved", "updated", "unchanged"}
    assert email_tools.resolve_default_account("smtp") == "work"  # the binding steers the lane

    # REVOKE: forget the preference (tombstone), the lane falls back honestly.
    item = operator_profile.resolve(OWNER_PRINCIPAL, "default_account.email")
    assert item is not None and item.value.get("binding_ref") == "credential:email.smtp.work"
    gone = operator_profile.forget_item(item.item_id)
    assert gone.kind == "forgotten"
    assert email_tools.resolve_default_account("smtp") == "default"


def test_signature_preference_remember_then_revoke() -> None:
    from core import operator_profile
    from core.operator_profile import OWNER_PRINCIPAL

    operator_profile.remember(OWNER_PRINCIPAL, "email_signature", "-- sent by NULLA")
    assert email_tools.email_signature_for() == "-- sent by NULLA"
    item = operator_profile.resolve(OWNER_PRINCIPAL, "email_signature")
    operator_profile.forget_item(item.item_id)
    assert email_tools.email_signature_for() == ""  # revoked means absent, not a default lie
