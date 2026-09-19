"""pa_beta_gate -- product completion, milestone 1: an event created outside VOOL becomes a VOOL alert.

The first user-facing milestone of the calendar and notifications lane. The provider fixture puts an
event on a selected, opted-in calendar; VOOL never creates it. The flow under test:

1. the calendar alert sync discovers the event inside its named background scope;
2. the event is scheduled on the one reminder and alert schedule (``reminder_requests``);
3. the due dispatcher delivers it into the persistent notification centre;
4. ``GET /api/notifications`` serves it to the bell, and ``POST /api/notifications/action`` snoozes and
   dismisses it;
5. a restarted dispatcher and sync keep that state without duplicate items.

Controls: an unselected calendar, sync or alerts left off, a provider refusal, alerts missed while VOOL was not
running, a disconnected account, the chat reminder's own conversation artifact, owner-local routes and the
bell's existing sources.

LABELLED: the providers are the disposable loopback Google Calendar and Microsoft Graph services
(``tests/pa_beta_gate/_json_api_service.py``) reached through the real VOOL transport. The clock is injected and stands in for the process clock.
No OS notification channel is exercised in this file, and nothing here proves a live Google or Microsoft
account.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from ._json_api_service import start_json_calendar_fixture

pytestmark = [pytest.mark.pa_beta]

# 07:00 UTC on Monday 2026-09-21 is 10:00 in Vilnius (EEST, UTC+3).
T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
WORK, FAMILY = "work@fixture.test", "family@fixture.test"
OPERATIONS = "AAMkAGOperations="


class Clock:
    """An injected clock: the alert schedule's timing semantics without waiting in real time.

    It also stands in for the process clock authority, so a served route and a module call in the same test read
    the same instant.
    """

    def __init__(self, start: datetime, monkeypatch) -> None:
        from core.time_authority import CLOCK

        self.now = start
        monkeypatch.setattr(CLOCK, "now_utc", lambda: self.now)

    def __call__(self) -> str:
        return self.now.isoformat()

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone("Europe/Athens")
    return tmp_path


@pytest.fixture
def google(home):
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={WORK: "Work", FAMILY: "Family"})
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def graph(home):
    server, state, base = start_json_calendar_fixture(dialect="graph", calendars={OPERATIONS: "Operations"})
    yield state, base
    server.shutdown()
    server.server_close()


def _connect(provider, base, clock, *, select, lead=(15,), sync=True, alerts=True):
    from core.operator import calendar_accounts, calendar_alerts

    account = calendar_accounts.add_account(provider=provider, base_url=base, label=f"{provider} fixture account")
    assert not account["sync_enabled"] and not account["alerts_enabled"], "sync and alerts start off until the user opts in"
    discovered = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock)
    assert discovered["ok"], discovered
    for calendar_id in select:
        calendar_accounts.select_calendar(account["account_id"], calendar_id, selected=True)
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=sync, alerts_enabled=alerts)
    calendar_alerts.set_lead_minutes(account_id=account["account_id"], minutes=list(lead))
    return account["account_id"]


def _dispatcher(clock):
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from storage.db import get_connection

    return ReminderDispatcher(get_connection_fn=get_connection, sleep_fn=lambda _seconds: None, now_fn=clock)


def _get(path, query=None, host="127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"), model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def _post(path, body, host="127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path=path, body=body, headers={"content-type": "application/json"}, runtime=RuntimeServices(display_name="N"),
                        model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def _bell(**query):
    status, payload = _get("/api/notifications", {key: [str(value)] for key, value in query.items()})
    assert status == 200 and payload["ok"], payload
    return payload


# ---------------------------------------------------------------------------------------------
# The milestone flow: original and novel
# ---------------------------------------------------------------------------------------------


def test_event_created_outside_vool_alerts_in_the_bell_and_survives_a_restart(google, monkeypatch):
    """ORIGINAL: a Google event nobody created in VOOL, a 15-minute alert, snooze, dismiss and restart."""
    state, base = google
    clock = Clock(T0, monkeypatch)
    state.seed_event(WORK, "evt-design-review", summary="Design review", start=T0 + timedelta(minutes=40), minutes=30,
                     tz_name="Europe/Athens")
    account_id = _connect("google", base, clock, select=[WORK])
    from core.operator import calendar_alerts

    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] and report["events_seen"] == 1 and report["alerts_scheduled"] == 1, report
    [upcoming] = calendar_alerts.upcoming_alerts(now_fn=clock)
    assert (upcoming["title"], upcoming["due_at_utc"], upcoming["lead_minutes"]) == (
        "Design review", (T0 + timedelta(minutes=25)).isoformat(), 15)

    clock.advance(minutes=10)
    _dispatcher(clock).sweep_once()
    assert _bell()["items"] == [], "nothing is shown before the alert is due"

    clock.advance(minutes=16)
    assert _dispatcher(clock).sweep_once()["delivered"] == 1
    bell = _bell()
    [item] = bell["items"]
    assert bell["unread"] == 1 and item["source_kind"] == "calendar_alert" and item["title"] == "Design review", bell
    card = item["payload"]
    assert card["start_local"] == "2026-09-21T10:40:00+03:00" and card["calendar_name"] == "Work", card
    assert card["web_url"] == "https://fixture.test/events/evt-design-review" and card["lead_minutes"] == 15, card

    status, snoozed = _post("/api/notifications/action", {"notification_id": item["notification_id"], "action": "snooze", "minutes": 5})
    assert status == 200 and snoozed["ok"] and snoozed["snoozed_until"] == (clock.now + timedelta(minutes=5)).isoformat(), snoozed
    assert _bell()["unread"] == 0

    # Restart: a new dispatcher over the same store re-surfaces the snoozed alert exactly once.
    clock.advance(minutes=6)
    assert _dispatcher(clock).sweep_once()["delivered"] == 1
    assert _dispatcher(clock).sweep_once()["delivered"] == 0
    bell = _bell(include_dismissed=1)
    assert [entry["title"] for entry in bell["items"]] == ["Design review", "Design review"] and bell["unread"] == 1, bell
    newest = bell["items"][0]
    status, dismissed = _post("/api/notifications/action", {"notification_id": newest["notification_id"], "action": "dismiss"})
    assert status == 200 and dismissed["ok"], dismissed

    # A restarted sync of the unchanged event schedules nothing new, and later sweeps add no items.
    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 0
    clock.advance(minutes=45)
    _dispatcher(clock).sweep_once()
    bell = _bell()
    assert bell["items"] == [] and bell["unread"] == 0, "a dismissed alert stays dismissed and nothing fires twice"
    assert len(_bell(include_dismissed=1)["items"]) == 2


def test_moved_then_deleted_event_supersedes_and_cancels_its_alerts(graph, monkeypatch):
    """NOVEL: a Microsoft Graph event with two lead times, a per-event policy change over the served route, then
    a move and finally a deletion made outside VOOL."""
    state, base = graph
    clock = Clock(T0 + timedelta(hours=2), monkeypatch)
    start = clock.now + timedelta(minutes=50)
    state.seed_event(OPERATIONS, "AAMkSupplierCall=", summary="Supplier call", start=start, minutes=20, tz_name="Europe/Athens")
    account_id = _connect("graph", base, clock, select=[OPERATIONS], lead=(30, 5))
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 2
    status, view = _get("/api/calendar/alerts")
    assert status == 200 and sorted(alert["lead_minutes"] for alert in view["upcoming"]) == [5, 30], view
    event_key = view["upcoming"][0]["event_key"]

    # "Remind me 10 minutes before this one": a per-event override replaces the account's lead times.
    status, changed = _post("/api/calendar/alerts/policy", {"event_key": event_key, "lead_minutes": [10]})
    assert status == 200 and changed["ok"], changed
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["alerts_scheduled"] == 1 and report["alerts_superseded"] == 2, report
    assert [(alert["lead_minutes"], alert["due_at_utc"]) for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == [
        (10, (start - timedelta(minutes=10)).isoformat())]

    # Moved outside VOOL: the obsolete alert never fires for the old time.
    state.seed_event(OPERATIONS, "AAMkSupplierCall=", summary="Supplier call", start=start + timedelta(minutes=60), minutes=20,
                     tz_name="Europe/Athens")
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["alerts_scheduled"] == 1 and report["alerts_superseded"] == 1, report
    clock.advance(minutes=45)
    assert _dispatcher(clock).sweep_once()["delivered"] == 0, "no alert for the old start time"

    # Deleted outside VOOL: its pending alert is cancelled.
    del state.calendars[OPERATIONS]["events"]["AAMkSupplierCall="]
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] and report["alerts_cancelled"] == 1, report
    clock.advance(minutes=90)
    assert _dispatcher(clock).sweep_once()["delivered"] == 0
    assert _bell(include_dismissed=1)["items"] == []


# ---------------------------------------------------------------------------------------------
# Negative controls
# ---------------------------------------------------------------------------------------------


def test_unselected_calendars_and_sync_or_alerts_left_off_schedule_nothing(google, graph, monkeypatch):
    google_state, google_base = google
    graph_state, graph_base = graph
    clock = Clock(T0, monkeypatch)
    google_state.seed_event(FAMILY, "evt-school-run", summary="School run", start=T0 + timedelta(minutes=30), minutes=20)
    google_state.seed_event(WORK, "evt-standup", summary="Standup", start=T0 + timedelta(minutes=30), minutes=15)
    graph_state.seed_event(OPERATIONS, "AAMkShift=", summary="Shift handover", start=T0 + timedelta(minutes=30), minutes=15)
    from core.operator import calendar_accounts, calendar_alerts

    work_only = _connect("google", google_base, clock, select=[WORK])
    report = calendar_alerts.sync_account(work_only, now_fn=clock)
    assert report["events_seen"] == 1, report
    assert [alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == ["Standup"]

    alerts_off = _connect("graph", graph_base, clock, select=[OPERATIONS], alerts=False)
    report = calendar_alerts.sync_account(alerts_off, now_fn=clock)
    assert report["ok"] and report["events_seen"] == 1 and report["alerts_scheduled"] == 0, report

    calendar_accounts.set_opt_in(alerts_off, sync_enabled=False)
    requests_before = graph_state.request_count
    report = calendar_alerts.sync_account(alerts_off, now_fn=clock)
    assert not report["ok"] and report["status"] == "sync_off" and graph_state.request_count == requests_before, report
    assert [alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == ["Standup"]


def test_provider_refusal_keeps_scheduled_alerts_and_reports_the_account_state(google, monkeypatch):
    state, base = google
    clock = Clock(T0, monkeypatch)
    state.seed_event(WORK, "evt-budget", summary="Budget sign-off", start=T0 + timedelta(hours=3), minutes=30)
    account_id = _connect("google", base, clock, select=[WORK])
    from core.operator import calendar_accounts, calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    state.require_bearer = "rotated-fixture-credential"  # the provider now refuses this account
    clock.advance(minutes=10)
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert not report["ok"] and report["status"] in {"revoked", "denied"} and report["alerts_cancelled"] == 0, report
    account = calendar_accounts.load_account(account_id)
    assert account["status"] == report["status"] and account["recovery"], account
    assert [alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == ["Budget sign-off"], \
        "a refused read is not an empty calendar"
    status, view = _get("/api/calendar/alerts")
    [row] = [entry for entry in view["accounts"] if entry["account_id"] == account_id]
    assert status == 200 and row["status"] == report["status"] and row["recovery"], row


def test_alerts_missed_while_vool_was_not_running_collapse_into_one_notice(google, monkeypatch):
    state, base = google
    clock = Clock(T0 - timedelta(hours=2), monkeypatch)
    for minutes_before, title in ((90, "Vendor demo"), (60, "Lunch with the auditors"), (30, "Quarterly review")):
        state.seed_event(WORK, f"evt-missed-{minutes_before}", summary=title, start=T0 - timedelta(minutes=minutes_before), minutes=20)
    state.seed_event(WORK, "evt-board-prep", summary="Board prep", start=T0 + timedelta(minutes=2), minutes=30)
    account_id = _connect("google", base, clock, select=[WORK])
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 4
    clock.now = T0  # VOOL was not running for two hours
    outcome = _dispatcher(clock).sweep_once()
    assert outcome["delivered"] == 1 and outcome["expired"] == 3, outcome
    bell = _bell()
    assert sorted(entry["source_kind"] for entry in bell["items"]) == ["calendar_alert", "calendar_catch_up"], bell
    [late] = [entry for entry in bell["items"] if entry["source_kind"] == "calendar_alert"]
    assert late["title"] == "Board prep" and late["payload"]["late"] is True, late
    [notice] = [entry for entry in bell["items"] if entry["source_kind"] == "calendar_catch_up"]
    assert notice["payload"]["missed"] == 3, notice
    assert sorted(notice["payload"]["titles"]) == ["Lunch with the auditors", "Quarterly review", "Vendor demo"], notice
    assert _dispatcher(clock).sweep_once()["expired"] == 0 and len(_bell()["items"]) == 2


def test_disconnecting_an_account_cancels_only_its_pending_alerts(google, graph, monkeypatch):
    google_state, google_base = google
    graph_state, graph_base = graph
    clock = Clock(T0, monkeypatch)
    google_state.seed_event(WORK, "evt-retro", summary="Sprint retro", start=T0 + timedelta(hours=1), minutes=45)
    graph_state.seed_event(OPERATIONS, "AAMkInventory=", summary="Inventory count", start=T0 + timedelta(hours=1), minutes=60)
    from core.operator import calendar_accounts, calendar_alerts

    google_account = _connect("google", google_base, clock, select=[WORK])
    graph_account = _connect("graph", graph_base, clock, select=[OPERATIONS])
    calendar_alerts.sync_account(google_account, now_fn=clock)
    calendar_alerts.sync_account(graph_account, now_fn=clock)
    assert sorted(alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)) == ["Inventory count", "Sprint retro"]

    result = calendar_accounts.disconnect_account(google_account, now_fn=clock)
    assert result["ok"] and result["alerts_cancelled"] == 1, result
    assert [alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == ["Inventory count"]
    requests_before = google_state.request_count
    report = calendar_alerts.sync_account(google_account, now_fn=clock)
    assert not report["ok"] and report["status"] == "disconnected" and google_state.request_count == requests_before, report
    clock.advance(minutes=50)
    assert _dispatcher(clock).sweep_once()["delivered"] == 1
    assert [entry["title"] for entry in _bell()["items"]] == ["Inventory count"]


# ---------------------------------------------------------------------------------------------
# Preservation
# ---------------------------------------------------------------------------------------------


def test_chat_reminder_keeps_its_conversation_artifact_and_also_reaches_the_bell(home):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
    from core.operator import reminders
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from storage.db import get_connection

    session_id = f"pc-reminder-{uuid.uuid4().hex[:8]}"
    intent = parse_operator_action_intent("remind me to water the ferns in 0 minutes")
    assert intent is not None
    created = dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)
    assert created.ok, created.response_text
    outcome = ReminderDispatcher(get_connection_fn=get_connection, sleep_fn=lambda _seconds: None).sweep_once()
    assert outcome["delivered"] == 1, outcome
    row = reminders.load_reminder(created.details["reminder_id"], get_connection_fn=get_connection)
    assert row["status"] == "delivered" and "session_conversation_log" in row["delivery_receipt_json"], row
    [item] = _bell()["items"]
    assert item["source_kind"] == "reminder" and "water the ferns" in item["title"] and item["session_id"] == session_id, item


def test_notification_and_calendar_alert_routes_are_owner_local(home):
    for path in ("/api/notifications", "/api/calendar/alerts"):
        status, payload = _get(path, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", (path, payload)
    for path, body in (("/api/notifications/action", {"notification_id": "x", "action": "dismiss"}),
                       ("/api/calendar/alerts/policy", {"event_key": "x", "lead_minutes": [5]})):
        status, payload = _post(path, body, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", (path, payload)


def test_bell_keeps_its_existing_sources_and_reads_the_notification_centre():
    """A source-level contract on the rendered fragment; the visible behaviour is proven in a browser stage."""
    from core.notification_fragment import render_notification_fragment

    fragment = render_notification_fragment()
    for existing in ("/api/cloud/market-events", "runFinished", "No notifications yet"):
        assert existing in fragment, existing
    for added in ("/api/notifications?after=", "/api/notifications/action", "'snooze'", "'dismiss'", "vfEventCard"):
        assert added in fragment, added
