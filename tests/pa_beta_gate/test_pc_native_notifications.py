"""pa_beta_gate -- product completion: VOOL alerts as macOS notifications, seen from the runtime's side.

The macOS channel is an extra copy of what the bell holds, turned on separately. VOOL keeps one outbox of OS
requests under identifiers it chooses; the window host's bridge hands them to the Swift helper, which submits them to
the macOS notification center and reports back. The states stay separate: queued (handed to the bridge), submitted
(macOS accepted the request), listed (macOS lists it among delivered notifications), acknowledged (the person clicked
it or chose an action), suppressed (VOOL did not send it, with the reason), failed, withdraw_requested and withdrawn.
Nothing records that a banner was displayed: macOS does not report that, and Focus may hold a banner it accepted.

Upcoming alerts are scheduled in macOS ahead of time, so they can appear while VOOL is closed; when the alert comes
due in the bell, the bell item links to that request instead of submitting a second copy.

LABELLED: a scripted bridge client calls the served routes the way the window host does and posts the reports the
helper would make; it shows nothing. The compiled helper against the actual macOS notification center is exercised in
test_pc_native_helper.py and in the native smoke recorded with the delivery.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from ._json_api_service import start_json_calendar_fixture
from ._pc_calendar_rig import T0, Clock, api_get, api_post, bell, connect, prepare_home, sweep

pytestmark = [pytest.mark.pa_beta]

WORK = "work@fixture.test"
OPERATIONS = "AAMkAGOperations="
SETTINGS_ALLOWED = {"alert": "enabled", "sound": "enabled", "notification_center": "enabled", "lock_screen": "enabled",
                    "previews": "always", "alert_style": "banner"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def google(home):
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={WORK: "Work"})
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def graph(home):
    server, state, base = start_json_calendar_fixture(dialect="graph", calendars={OPERATIONS: "Operations"})
    yield state, base
    server.shutdown()
    server.server_close()


class _Bridge:
    """LABELLED scripted bridge client (see the module docstring)."""

    def __init__(self, bridge_id="bridge-under-test"):
        self.bridge_id = bridge_id

    def outbox(self):
        status, payload = api_post("/api/notifications/native/outbox", {"bridge_id": self.bridge_id, "helper_version": "test"})
        assert status == 200 and payload["ok"], payload
        return payload

    def report(self, *events):
        status, payload = api_post("/api/notifications/native/report", {"bridge_id": self.bridge_id, "events": list(events)})
        assert status == 200 and payload["ok"], payload
        return payload

    def settings(self, authorization):
        return self.report({"event": "settings", "authorization": authorization, **SETTINGS_ALLOWED})


def _enable_native():
    status, saved = api_post("/api/notifications/preferences", {"preferences": {"native_notifications": True}})
    assert status == 200 and saved["ok"] and saved["preferences"]["native_notifications"] is True, saved


def _native_status():
    status, view = api_get("/api/notifications/native/status")
    assert status == 200 and view["ok"], view
    return view


# ---------------------------------------------------------------------------------------------
# The macOS channel: original and novel
# ---------------------------------------------------------------------------------------------


def test_calendar_alert_is_scheduled_in_macos_ahead_and_the_bell_links_to_that_request(google, monkeypatch):
    """ORIGINAL: an upcoming calendar alert is handed to macOS as a request for its due time. When it comes due the
    bell item links to that request instead of submitting a second copy, and macOS's listing and the person's click
    are recorded as separate states."""
    state, base = google
    clock = Clock(T0, monkeypatch)
    state.seed_event(WORK, "evt-design-review", summary="Design review", start=T0 + timedelta(minutes=40), minutes=30,
                     tz_name="Europe/Athens")
    account_id = connect("google", clock, base_url=base, select=[WORK])
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    bridge = _Bridge()
    off = bridge.outbox()
    assert off["enabled"] is False and off["requests"] == [], "the macOS channel starts off"

    _enable_native()
    waiting = bridge.outbox()
    assert waiting["want_authorization"] is True and waiting["want_settings"] is True and waiting["requests"] == [], waiting
    bridge.settings("authorized")
    [ahead] = bridge.outbox()["requests"]
    assert (ahead["op"], ahead["kind"], ahead["deliver_at_utc"]) == ("submit", "scheduled", (T0 + timedelta(minutes=25)).isoformat()), ahead
    assert ahead["identifier"].startswith("vool.s.") and (ahead["title"], ahead["body"]) == ("Design review", ""), ahead
    assert ahead["sound"] is True and ahead["thread"].startswith("evt-"), ahead
    bridge.report({"event": "submitted", "identifier": ahead["identifier"]})
    assert bridge.outbox()["requests"] == [], "a confirmed request is not handed again"

    clock.advance(minutes=26)
    assert sweep(clock)["delivered"] == 1
    assert bridge.outbox()["requests"] == [], "macOS already holds this alert; the bell does not submit a second copy"
    [item] = bell()["items"]
    assert item["channels"]["in_app"]["state"] == "recorded" and item["channels"]["macos"]["state"] == "submitted", item

    bridge.report({"event": "listing", "pending": [], "delivered": [ahead["identifier"]]})
    [item] = bell()["items"]
    assert item["channels"]["macos"]["state"] == "listed", item
    opened = bridge.report({"event": "response", "identifier": ahead["identifier"], "action": "open"})
    assert [entry["notification_id"] for entry in opened["opened"]] == [item["notification_id"]], opened
    listing = bell()
    assert listing["unread"] == 0 and listing["items"][0]["channels"]["macos"]["state"] == "acknowledged", listing

    view = _native_status()
    [row] = view["requests"]
    assert (row["identifier"], row["notification_id"], row["state"]) == (ahead["identifier"], item["notification_id"], "acknowledged"), view
    assert view["connected"] is True and view["authorization"] == "authorized" and view["enabled"] is True, view
    assert "displayed" not in view["states"] and view["explanation"], view


def test_moved_event_replaces_its_macos_request_and_disconnect_withdraws_it(graph, monkeypatch):
    """NOVEL: Microsoft Graph. The event moves outside VOOL: its scheduled macOS request is withdrawn and a request
    for the new time takes its place under a new identifier. Disconnecting the account withdraws that one too."""
    state, base = graph
    clock = Clock(T0 + timedelta(hours=2), monkeypatch)
    start = clock.now + timedelta(minutes=50)
    state.seed_event(OPERATIONS, "AAMkSupplierCall=", summary="Supplier call", start=start, minutes=20, tz_name="Europe/Athens")
    _enable_native()
    bridge = _Bridge()
    bridge.settings("authorized")
    account_id = connect("graph", clock, base_url=base, select=[OPERATIONS])
    from core.operator import calendar_accounts, calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    [first] = bridge.outbox()["requests"]
    bridge.report({"event": "submitted", "identifier": first["identifier"]})

    state.seed_event(OPERATIONS, "AAMkSupplierCall=", summary="Supplier call", start=start + timedelta(minutes=60), minutes=20,
                     tz_name="Europe/Athens")
    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_superseded"] == 1
    handed = bridge.outbox()["requests"]
    withdraws = [request for request in handed if request["op"] == "withdraw"]
    submits = [request for request in handed if request["op"] == "submit"]
    assert [request["identifier"] for request in withdraws] == [first["identifier"]], handed
    assert [request["deliver_at_utc"] for request in submits] == [(start + timedelta(minutes=45)).isoformat()], handed
    assert submits[0]["identifier"] != first["identifier"], handed
    assert bridge.outbox()["requests"] == [], "a withdrawal just handed is not handed again at once"

    bridge.report({"event": "withdraw_requested", "identifier": first["identifier"]}, {"event": "submitted", "identifier": submits[0]["identifier"]})
    bridge.report({"event": "listing", "pending": [submits[0]["identifier"]], "delivered": []})
    states = {row["identifier"]: row["state"] for row in _native_status()["requests"]}
    assert states == {first["identifier"]: "withdrawn", submits[0]["identifier"]: "submitted"}, states

    result = calendar_accounts.disconnect_account(account_id, now_fn=clock)
    assert result["ok"] and result["alerts_cancelled"] == 1, result
    [gone] = bridge.outbox()["requests"]
    assert (gone["op"], gone["identifier"]) == ("withdraw", submits[0]["identifier"]), gone
    clock.advance(minutes=120)
    assert sweep(clock)["delivered"] == 0 and bridge.outbox()["requests"] == []


def test_reminder_in_quiet_hours_reaches_the_bell_not_macos_and_a_denied_permission_is_recorded(home, monkeypatch):
    """NOVEL: a standalone reminder due in quiet hours is not scheduled in macOS; it still reaches its chat and the
    bell, which records that macOS was skipped for quiet hours. After macOS permission is denied, a test notification
    is recorded as skipped for the permission, never as sent."""
    clock = Clock(T0 + timedelta(hours=12), monkeypatch)  # 19:00 UTC is 22:00 in Vilnius
    _enable_native()
    bridge = _Bridge()
    bridge.settings("authorized")
    status, saved = api_post("/api/notifications/preferences", {"preferences": {"quiet_hours": {"enabled": True, "start": "22:00", "end": "07:00"}}})
    assert status == 200 and saved["ok"], saved
    from core.operator import reminders
    from storage.db import get_connection

    reminders.schedule_reminder(session_id="pc-native-quiet", task_id="task-quiet", note="take the laundry out",
                                due_at_utc=(clock.now + timedelta(minutes=30)).isoformat(), tz_name="Europe/Athens",
                                now_fn=clock, get_connection_fn=get_connection)
    assert bridge.outbox()["requests"] == [], "a due time inside quiet hours is not scheduled in macOS"
    clock.advance(minutes=31)
    assert sweep(clock)["delivered"] == 1
    assert bridge.outbox()["requests"] == []
    [item] = bell()["items"]
    assert item["source_kind"] == "reminder" and item["channels"]["session_log"]["state"] == "recorded", item
    assert item["channels"]["macos"]["state"] == "suppressed" and "quiet hours" in item["channels"]["macos"]["detail"], item

    bridge.settings("denied")
    status, sent = api_post("/api/notifications/native/test", {})
    assert status == 200 and sent["ok"], sent
    assert bridge.outbox()["requests"] == []
    [test_item] = [entry for entry in bell()["items"] if entry["source_kind"] == "test"]
    assert test_item["channels"]["macos"]["state"] == "suppressed" and "permission" in test_item["channels"]["macos"]["detail"], test_item
    assert _native_status()["authorization"] == "denied"


def test_snooze_chosen_on_the_macos_notification_snoozes_the_alert_once(google, monkeypatch):
    """NOVEL: an alert that came due with no request scheduled ahead is submitted when the bell gets it. Snooze chosen
    on the macOS notification snoozes it exactly as the bell would; the same response again changes nothing; the next
    fire is a new request under a new identifier."""
    state, base = google
    clock = Clock(T0, monkeypatch)
    state.seed_event(WORK, "evt-standup", summary="Stand-up", start=T0 + timedelta(minutes=15, seconds=30), minutes=15)
    _enable_native()
    bridge = _Bridge()
    bridge.settings("authorized")
    account_id = connect("google", clock, base_url=base, select=[WORK])
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    assert bridge.outbox()["requests"] == [], "an alert due within the minute is left to the bell"
    clock.advance(minutes=1)
    assert sweep(clock)["delivered"] == 1
    [now_request] = bridge.outbox()["requests"]
    assert (now_request["op"], now_request["kind"], now_request["deliver_at_utc"]) == ("submit", "immediate", ""), now_request
    assert now_request["category"] == "vool.alert", now_request
    bridge.report({"event": "submitted", "identifier": now_request["identifier"]})

    first = bridge.report({"event": "response", "identifier": now_request["identifier"], "action": "snooze"})
    assert first["applied"] == 1, first
    [item] = bell(include_dismissed=1)["items"]
    assert item["snoozed_until"] == (clock.now + timedelta(minutes=10)).isoformat() and item["channels"]["macos"]["state"] == "acknowledged", item
    again = bridge.report({"event": "response", "identifier": now_request["identifier"], "action": "snooze"})
    assert again["applied"] == 0, again

    clock.advance(minutes=11)
    assert sweep(clock)["delivered"] == 1
    [next_request] = bridge.outbox()["requests"]
    assert next_request["op"] == "submit" and next_request["identifier"] != now_request["identifier"], next_request


# ---------------------------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------------------------


def test_macos_channel_off_hands_nothing_and_turning_it_off_withdraws_pending_requests(google, monkeypatch):
    state, base = google
    clock = Clock(T0, monkeypatch)
    state.seed_event(WORK, "evt-budget", summary="Budget sign-off", start=T0 + timedelta(hours=3), minutes=30)
    account_id = connect("google", clock, base_url=base, select=[WORK])
    from core.operator import calendar_alerts

    calendar_alerts.sync_account(account_id, now_fn=clock)
    bridge = _Bridge()
    bridge.settings("authorized")
    assert bridge.outbox()["requests"] == [], "a macOS permission is not an opt-in"
    status, refused = api_post("/api/notifications/native/test", {})
    assert status == 409 and refused["reason"] == "native_notifications_off", refused

    _enable_native()
    [ahead] = bridge.outbox()["requests"]
    bridge.report({"event": "submitted", "identifier": ahead["identifier"]})
    status, saved = api_post("/api/notifications/preferences", {"preferences": {"native_notifications": False}})
    assert status == 200 and saved["ok"], saved
    [withdraw] = bridge.outbox()["requests"]
    assert (withdraw["op"], withdraw["identifier"]) == ("withdraw", ahead["identifier"]), withdraw
    assert _native_status()["enabled"] is False


def test_unconfirmed_requests_are_handed_again_under_one_identifier_and_strays_are_withdrawn(home, monkeypatch):
    """CONTROL: a bridge that took a request and never confirmed it (a killed window host) gets the same identifier
    again after a minute -- macOS replaces a pending request with the same identifier instead of adding a second --
    and after three tries the request is recorded as failed. A VOOL request macOS still holds that this store does
    not know (left by an earlier install) is withdrawn; another app's identifier is left alone."""
    clock = Clock(T0, monkeypatch)
    _enable_native()
    bridge = _Bridge()
    bridge.settings("authorized")
    status, sent = api_post("/api/notifications/native/test", {})
    assert status == 200 and sent["ok"], sent
    [first] = bridge.outbox()["requests"]
    assert (first["kind"], first["title"]) == ("immediate", "VOOL test notification"), first
    assert bridge.outbox()["requests"] == []
    again = []
    for _attempt in range(2):
        clock.advance(seconds=61)
        [request] = bridge.outbox()["requests"]
        again.append(request["identifier"])
    assert again == [first["identifier"], first["identifier"]], again
    clock.advance(seconds=61)
    assert bridge.outbox()["requests"] == []
    [row] = _native_status()["requests"]
    assert row["state"] == "failed" and "never confirmed" in row["detail"], row

    bridge.report({"event": "listing", "pending": ["vool.s.left-by-an-earlier-install.0"], "delivered": ["com.example.other.1"]})
    [stray] = bridge.outbox()["requests"]
    assert (stray["op"], stray["identifier"]) == ("withdraw", "vool.s.left-by-an-earlier-install.0"), stray


def test_native_bridge_routes_are_owner_local(home):
    for path, body in (("/api/notifications/native/outbox", {}), ("/api/notifications/native/report", {"events": []}),
                       ("/api/notifications/native/test", {}), ("/api/notifications/native/authorize", {})):
        status, payload = api_post(path, body, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", (path, payload)
    status, payload = api_get("/api/notifications/native/status", host="203.0.113.9")
    assert status == 403 and payload.get("error") == "owner_local_required", payload
