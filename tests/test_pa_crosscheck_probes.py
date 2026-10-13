"""Personal-assistant cross-check probes (calendar, reminders, email) at the main head.

Each probe drives a real production seam with an isolated home, an injected clock and
loopback fixtures; nothing reaches a provider, a model or the owner's accounts. They were
written independently, before the fixes, as strict expected failures recording each defect
the cross-check found; with the assistant fixes in place every one must pass.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tests.pa_beta_gate._pc_calendar_rig import Clock, prepare_home

# Saturday 2026-10-24 10:00 in Europe/Athens (EEST, UTC+3).
T0 = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)


def _fixed_clock(monkeypatch):
    """Pin both clock doors the scheduling lane reads: the UTC instant and the zoned 'now'."""
    from zoneinfo import ZoneInfo

    from core.time_authority import CLOCK

    clock = Clock(T0, monkeypatch)
    monkeypatch.setattr(CLOCK, "now_for_timezone", lambda name: clock.now.astimezone(ZoneInfo(name)))
    return clock


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending(session_id: str):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session_id, get_connection_fn=get_connection)


# --------------------------------------------------------------------------- reminders


def test_reminder_for_a_time_already_passed_today_is_refused(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    _fixed_clock(monkeypatch)
    result = _run("remind me today at 8am to call the bank", session_id="probe-past")
    assert not result.ok, result.response_text
    assert _pending("probe-past") == []


def test_reminder_for_yesterday_is_not_moved_into_the_future(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    _fixed_clock(monkeypatch)
    result = _run("remind me yesterday at 9am to call the bank", session_id="probe-yesterday")
    assert not result.ok, result.response_text
    assert _pending("probe-yesterday") == []


def test_reminder_with_a_bare_24_hour_time(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    _fixed_clock(monkeypatch)
    result = _run("remind me tomorrow 15:00 to call the bank", session_id="probe-24h")
    assert result.ok, result.response_text
    [row] = _pending("probe-24h")
    assert row["due_at_utc"].startswith("2026-10-25T13:00"), row["due_at_utc"]  # 15:00 EET (after the change)


def test_scheduling_zone_follows_the_operator_profile_timezone(tmp_path, monkeypatch):
    # The rig's prepare_home calls save_user_timezone(), which no production code does. A real
    # home only has the Operator Profile timezone (what the user tells VOOL), so start from that.
    prepare_home(tmp_path, monkeypatch)
    from core.user_preferences import load_preferences, save_preferences

    prefs = load_preferences()
    prefs.timezone = ""
    save_preferences(prefs)
    from core import operator_profile

    change = operator_profile.remember(operator_profile.OWNER_PRINCIPAL, "timezone", "Europe/Athens")
    assert operator_profile.resolve(operator_profile.OWNER_PRINCIPAL, "timezone") is not None, change
    _fixed_clock(monkeypatch)
    result = _run("remind me tomorrow at 9am to call the bank", session_id="probe-zone")
    assert result.ok, result.response_text
    [row] = _pending("probe-zone")
    assert row["due_at_utc"].startswith("2026-10-25T07:00"), row["due_at_utc"]  # 09:00 EET, not 09:00 UTC


# --------------------------------------------------------------------------- calendar


def test_model_availability_tool_carries_the_request_words():
    from core.execution.operator_tools import build_operator_action_intent

    intent = build_operator_action_intent("check_availability", {"text": "am I free tomorrow afternoon?"})
    assert "tomorrow afternoon" in intent.raw_text


class _NSDate:
    def __init__(self, ts: float) -> None:
        self._ts = ts

    def timeIntervalSince1970(self) -> float:
        return self._ts


def test_eventkit_all_day_date_is_the_local_day(monkeypatch):
    from core.kas.adapters.eventkit_mac import _ek_to_cal

    monkeypatch.setenv("TZ", "Europe/Athens")
    time.tzset()
    try:
        start = datetime(2026, 10, 24, 0, 0).timestamp()  # local midnight, as EventKit stores an all-day day
        end = datetime(2026, 10, 24, 23, 59, 59).timestamp()
        event = SimpleNamespace(
            eventIdentifier=lambda: "ek-1", startDate=lambda: _NSDate(start), endDate=lambda: _NSDate(end),
            isAllDay=lambda: True, calendar=lambda: None, status=lambda: 0, title=lambda: "Day off",
            hasRecurrenceRules=lambda: False, isDetached=lambda: False, notes=lambda: "", location=lambda: "",
            URL=lambda: None, lastModifiedDate=lambda: _NSDate(start),
        )
        projected = _ek_to_cal(event, provider_id="eventkit", foundation=None)
        assert projected.start_date == "2026-10-24", projected.start_date
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


# --------------------------------------------------------------------------- email


def test_gmail_body_of_a_message_with_an_attachment():
    import base64

    from core.email_providers.gmail import GmailAdapter

    def b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    data = {
        "id": "m1", "threadId": "t1", "snippet": "Your order shipped",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "Subject", "value": "Your order has shipped"}],
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Tracking number: TEST-0000")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>Tracking number: TEST-0000</p>")}},
                ]},
                {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "a1"}},
            ],
        },
    }
    entry = GmailAdapter._entry_from_full(SimpleNamespace(), data)
    assert "TEST-0000" in entry["body"], entry["body"]
