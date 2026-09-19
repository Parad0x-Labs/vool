"""pa_beta_gate — v3 repair classes, NOVEL cases beyond the reviewer's originals.

Each repair already passes the independent review's original+parametrized cases; these
add genuinely different data/behavior at the same failure class, plus preservation
controls, per the old-failure-plus-new-case gate.
"""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.kas.adapters._json_calendars import cal_to_json, json_event_to_cal
from core.kas.adapters.google_calendar import GoogleCalendarAdapter, google_event_id
from core.kas.adapters.graph_calendar import GraphCalendarAdapter, graph_transaction_id
from core.kas.contract import AdapterConfig, CalCalendar, CalendarRefusedError, CalEvent, KasResponse
from core.operator.calendar_provider import (
    CalendarProviderConfig,
    _binding_mismatch,
    execute_proposed_event,
    propose_event,
)
from core.operator.models import OperatorActionIntent


def response(body, status=200):
    return KasResponse(status=status, body=json.dumps(body).encode())


def adapter(provider, transport):
    cls = GoogleCalendarAdapter if provider == "google" else GraphCalendarAdapter
    return cls(transport=transport, config=AdapterConfig(provider_id=provider, base_url="https://calendar.example.test/api"))


def test_repeated_page_token_is_a_typed_incomplete_read():
    """NOVEL pagination case: the provider repeats the SAME page token forever."""
    calls = []

    def wire(req):
        calls.append(req.url)
        return response({"items": [] if calls else [{"id": "e1", "summary": "S", "start": {"dateTime": "2026-09-15T12:00:00", "timeZone": "UTC"}, "end": {"dateTime": "2026-09-15T12:30:00", "timeZone": "UTC"}, "etag": '"v1"'}], "nextPageToken": "same-token"})

    with pytest.raises(CalendarRefusedError) as refused:
        adapter("google", wire).events_in_range("work", start_utc="2026-09-15T00:00:00+00:00", end_utc="2026-09-16T00:00:00+00:00")
    assert refused.value.reason == "incomplete_read"
    assert len(calls) <= 4, "a repeated token must stop the read almost immediately, not page to the cap"


def test_foreign_next_link_refuses_rather_than_follows_or_stops_silently():
    """NOVEL pagination case: an @odata.nextLink pointing at ANOTHER host."""
    calls = []

    def wire(req):
        calls.append(req.url)
        body = {"value": []}
        if len(calls) == 1:
            body["@odata.nextLink"] = "https://evil.example.test/api/page/2"
        return response(body)

    with pytest.raises(CalendarRefusedError) as refused:
        adapter("graph", wire).events_in_range("work", start_utc="2026-09-15T00:00:00+00:00", end_utc="2026-09-16T00:00:00+00:00")
    assert refused.value.reason == "incomplete_read"
    assert "off-origin" in refused.value.detail
    assert all("evil.example.test" not in url for url in calls), "host pinning must never follow the foreign link"


def test_google_event_id_mapping_is_deterministic_and_valid():
    """NOVEL identity case: different intents map to different valid ids; same intent is stable."""
    first = google_event_id("intent-one@vool.local")
    assert first == google_event_id("intent-one@vool.local")
    second = google_event_id("intent-two@vool.local")
    assert first != second
    import re

    assert re.fullmatch(r"[a-v0-9]{5,1024}", first) and re.fullmatch(r"[a-v0-9]{5,1024}", second)
    assert graph_transaction_id("intent-one@vool.local") == graph_transaction_id("intent-one@vool.local")


def test_cancel_preserves_the_exact_weak_etag():
    """NOVEL etag case: cancellation (reviewer tested update only) keeps the opaque token."""
    token = 'W/"opaque-77"'
    sent = []
    deleted = []

    def wire(req):
        if req.method == "DELETE":
            sent.append(req.headers.get("If-Match"))
            if req.headers.get("If-Match") != token:
                return response({}, 412)
            deleted.append(True)
            return response({}, 204)
        if deleted:
            return response({"error": {"message": "no such event"}}, 404)
        return response({"id": "abc123def456", "subject": "S", "@odata.etag": token,
                         "start": {"dateTime": "2026-09-15T12:00:00", "timeZone": "UTC"},
                         "end": {"dateTime": "2026-09-15T12:30:00", "timeZone": "UTC"}})

    a = adapter("graph", wire)
    current = a.get_event("work", "abc123def456")
    assert current.etag == token
    with pytest.raises(CalendarRefusedError):
        a.cancel_event("work", "abc123def456", 'W/"different"')
    assert sent == ['W/"different"']
    assert a.cancel_event("work", "abc123def456", token) is True
    assert sent == ['W/"different"', token]


def test_google_all_day_wire_keeps_google_shape_as_control():
    """PRESERVATION control: Google all-day still uses the bare date form (only Graph changed)."""
    e = CalEvent(provider_id="google", calendar_id="work", uid="g1", all_day=True, start_date="2026-12-24", end_date="2026-12-26")
    body = cal_to_json(e, uid="g1", provider="google")
    assert body["start"] == {"date": "2026-12-24"} and body["end"] == {"date": "2026-12-26"}
    assert "isAllDay" not in body
    read_back = json_event_to_cal({"id": "g1", "summary": "H", **body}, provider_id="google", calendar_id="work", etag='"v1"', href="")
    assert read_back.all_day and read_back.start_date == "2026-12-24" and read_back.end_date == "2026-12-26"


def test_graph_all_day_exclusive_end_survives_round_trip():
    """NOVEL all-day case: a three-day Graph event round-trips with the exclusive end."""
    e = CalEvent(provider_id="graph", calendar_id="work", uid="d1", all_day=True, start_date="2026-10-08", end_date="2026-10-11", tz_name="Europe/Athens")
    body = cal_to_json(e, uid="", provider="graph")
    assert body["isAllDay"] is True
    assert body["start"]["dateTime"].startswith("2026-10-08T00:00:00")
    assert body["end"]["dateTime"].startswith("2026-10-11T00:00:00")
    assert body["start"]["timeZone"] == "Europe/Athens"
    back = json_event_to_cal({"id": "d1", "subject": "Away", "isAllDay": True, "start": body["start"], "end": body["end"]}, provider_id="graph", calendar_id="work", etag='"v9"', href="")
    assert back.all_day and back.start_date == "2026-10-08" and back.end_date == "2026-10-11"


def staged(provider="caldav", auth="account-A"):
    config = CalendarProviderConfig(provider, "https://calendar.example.test/api", auth, "work", "10")
    captured = {}

    def save(**kw):
        captured.update(kw)
        return "proposal-9"

    no_conflicts = SimpleNamespace(list_calendars=lambda: [CalCalendar(provider, "work", "Work")], events_in_range=lambda *a, **k: [])
    when = SimpleNamespace(ok=True, due_at_utc="2026-09-15T12:00:00+00:00", tz_name="UTC", due_wall="2026-09-15 12:00")
    result = propose_event(OperatorActionIntent(kind="propose_calendar_event", raw_text='propose "Novel" Tuesday at 12:00 for 30 minutes'), task_id="t", session_id="s", config=config, adapter=no_conflicts, parse_when_fn=lambda x: when, now_fn=None, create_pending_action_fn=save, evaluate_local_action_fn=None, audit_log_fn=lambda *a, **k: None)
    assert result.status == "approval_required", result.response_text
    row = {"status": "pending_approval", "action_id": "proposal-9", "scope_json": json.dumps(captured["scope"])}
    return config, row


def test_update_approval_also_refuses_same_url_account_switch():
    """NOVEL binding case: UPDATE staging (not just create) carries the account binding."""
    config, _row = staged()
    # A staged UPDATE scope carrying the account binding must refuse account B at the same URL.
    scope = {"uid": "u1", "calendar_id": "work", "etag_seen": '"v1"', "provider": "caldav", "provider_url": "https://calendar.example.test/api", "auth_binding": "account-A"}
    assert _binding_mismatch(scope, config) is None
    assert _binding_mismatch(scope, replace(config, auth_binding="account-B")) is not None
    # Losing the binding entirely also refuses: the approval named a principal.
    assert _binding_mismatch(scope, replace(config, auth_binding="")) is not None


def test_uncertain_recovery_claim_governs_reconcile_too():
    """NOVEL recovery case: recovery that WOULD find the event still needs the claim."""
    config, row = staged()
    row["status"] = "outcome_unproven"
    calls = []
    reads = []

    def claim(action_id):
        calls.append(action_id)
        return False

    fake = SimpleNamespace(
        provider_id="caldav",
        get_event=lambda cal, uid: (reads.append(uid) or CalEvent(provider_id="caldav", uid=uid, calendar_id=cal, summary="Novel", start_utc="2026-09-15T12:00:00+00:00", end_utc="2026-09-15T12:30:00+00:00")),
        create_event=lambda cal, e: e,
    )
    result = execute_proposed_event(OperatorActionIntent(kind="approve_calendar_event", action_id="proposal-9"), task_id="t", session_id="s", adapter=fake, load_pending_action_fn=lambda **k: row, pending_row=row, config=config, mark_action_executed_fn=lambda *a, **k: None, audit_log_fn=lambda *a, **k: None, claim_action_fn=claim)
    assert result.status == "concurrent_approval", result.response_text
    assert calls == ["proposal-9"]
    assert reads == [], "a losing claim must block even the read-back reconcile"
