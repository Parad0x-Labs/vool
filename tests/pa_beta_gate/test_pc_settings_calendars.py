"""pa_beta_gate -- product completion: calendar accounts and notification settings, set up from Settings.

Rows 1-4 and the Settings side of row 15: the routes the two Settings panels call and the page model that mounts them.

* ``GET /api/calendar/accounts`` -- the calendar sources VOOL can connect with the capability of each, the credential
  bindings an account may use (binding ids and non-secret labels, never a secret), and every account with its
  calendars, state and recovery step.
* ``POST /api/calendar/accounts/add``, ``/discover``, ``/select``, ``/opt-in``, ``/disconnect`` and ``/reconnect`` --
  the setup steps, each through the one account authority (core/operator/calendar_accounts.py).
* ``GET /api/notifications/preferences`` -- what the notifications panel shows and saves.

All owner-local, like every Settings route.

LABELLED: loopback Google Calendar and CalDAV fixtures; a verified credential binding written into the isolated home's
binding index with its credential in the isolated credential store. The Google fixture then requires that credential as a
bearer token, so a discovery that succeeds proves the binding was resolved inside VOOL. No Playwright Chromium is
installed on this machine, so the panels' behaviour in a browser is recorded as browser evidence with the delivery.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from ._caldav_service import start_caldav_fixture
from ._json_api_service import start_json_calendar_fixture
from ._pc_calendar_rig import T0, Clock, api_get, api_post, prepare_home

pytestmark = [pytest.mark.pa_beta]

WORK, FAMILY = "work@fixture.test", "family@fixture.test"
TEAM = "/calendars/team/"
SECRET = "fixture-bearer-7c1d0b5e"
BINDING = "cb_fixture_google"


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


def _write_binding(binding_id, *, provider_id, status="verified", slot="fixture.google.token", secret=SECRET):
    from core.credential_intelligence.binding import load_index, save_index
    from core.credential_store import store_credential

    store_credential(slot, secret, label="Fixture calendar credential")
    rows = load_index()
    rows[provider_id] = {
        "binding_id": binding_id, "provider_id": provider_id, "provider_label": "Fixture calendar", "account": "owner@fixture.test",
        "capability_family": "calendar", "status": status, "last_verified_at": "2026-09-20T09:00:00+00:00",
        "created_at": "2026-09-20T09:00:00+00:00", "slot": slot, "auth_scheme": "bearer",
    }
    save_index(rows)


@pytest.fixture
def google(home):
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={WORK: "Work", FAMILY: "Family"})
    state.set_listing(WORK, accessRole="owner", primary=True)
    state.set_listing(FAMILY, accessRole="reader")
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def caldav(home):
    server, state, base, _port = start_caldav_fixture(calendars={TEAM: "Team"}, preferred_port=0)
    yield state, base
    server.shutdown()
    server.server_close()


def _ok(path, body):
    status, payload = api_post(path, body)
    assert status == 200 and payload.get("ok"), (path, status, payload)
    return payload


def test_a_google_account_set_up_through_the_settings_routes_reaches_alerts(google, monkeypatch):
    """ORIGINAL: the Settings sequence -- pick a source and a verified binding, add, refresh calendars, choose one as the
    default for new events, opt in, set a lead time, sync -- ends with an alert on the schedule. No route returns the
    credential."""
    state, base = google
    Clock(T0, monkeypatch)
    _write_binding(BINDING, provider_id="fixture_google")
    state.require_bearer = SECRET
    state.seed_event(WORK, "evt-planning", summary="Planning", start=T0 + timedelta(minutes=40), minutes=30, tz_name="Europe/Berlin")

    status, empty = api_get("/api/calendar/accounts")
    assert status == 200 and empty["ok"] and empty["accounts"] == [], empty
    assert set(empty["providers"]) == {"google", "graph", "caldav", "eventkit"}, empty
    assert [(binding["binding_id"], binding["status"]) for binding in empty["bindings"]] == [(BINDING, "verified")], empty

    added = _ok("/api/calendar/accounts/add", {"provider": "google", "base_url": base, "label": "Work Google", "auth_binding": BINDING})
    account_id = added["account_id"]
    assert (added["status"], added["sync_enabled"], added["alerts_enabled"]) == ("configured", False, False), added
    discovered = _ok("/api/calendar/accounts/discover", {"account_id": account_id})
    assert discovered["status"] == "connected", discovered
    assert {row["display_name"]: (row["can_write"], row["provider_default"]) for row in discovered["calendars"]} == {
        "Work": (True, True), "Family": (False, False)}, discovered
    _ok("/api/calendar/accounts/select", {"account_id": account_id, "calendar_id": WORK, "selected": True, "default_write": True})
    _ok("/api/calendar/accounts/opt-in", {"account_id": account_id, "sync_enabled": True, "alerts_enabled": True})
    _ok("/api/calendar/alerts/policy", {"account_id": account_id, "lead_minutes": [10]})
    synced = _ok("/api/calendar/sync", {"account_id": account_id})
    [report] = synced["reports"]
    assert report["ok"] and report["alerts_scheduled"] == 1, report

    status, view = api_get("/api/calendar/accounts")
    [account] = view["accounts"]
    assert status == 200 and account["status"] == "connected" and account["credential_binding"] == BINDING, account
    assert {row["display_name"]: (row["selected"], row["is_default_write"]) for row in account["calendars"]} == {
        "Work": (True, True), "Family": (False, False)}, account
    assert account["default_lead_minutes"] == [10] and account["capability"]["label"] == "Google Calendar", account
    for payload in (empty, added, discovered, synced, view):
        assert SECRET not in json.dumps(payload), "a Settings route returned the credential"


def test_setup_refuses_what_cannot_work_and_a_reconnected_account_waits_for_a_new_opt_in(caldav, monkeypatch):
    """NOVEL: a CalDAV account without a server address, with a binding that does not exist, with a binding that is not
    verified, and a source VOOL does not have are refused before anything is stored. A working CalDAV account,
    disconnected and reconnected, fetches nothing until sync is turned on again. Apple Calendar in a VOOL process without
    the EventKit bridge is added but reports the unsupported state with its reason."""
    state, base = caldav
    Clock(T0, monkeypatch)
    _write_binding("cb_fixture_caldav_unverified", provider_id="fixture_caldav", status="unauthorized", slot="fixture.caldav.password")
    for body, reason in (
        ({"provider": "caldav", "label": "No address"}, "base_url_required"),
        ({"provider": "caldav", "base_url": base, "auth_binding": "cb_does_not_exist"}, "unknown_credential_binding"),
        ({"provider": "caldav", "base_url": base, "auth_binding": "cb_fixture_caldav_unverified"}, "credential_binding_not_verified"),
        ({"provider": "exchange-2003"}, "unsupported_provider"),
    ):
        status, refused = api_post("/api/calendar/accounts/add", body)
        assert status == 400 and refused["reason"] == reason, (body, status, refused)
    assert api_get("/api/calendar/accounts")[1]["accounts"] == [], "a refused setup stores nothing"

    state.seed_event(TEAM, "standup-1", summary="Stand-up", start=T0 + timedelta(minutes=50), minutes=15)
    account_id = _ok("/api/calendar/accounts/add", {"provider": "caldav", "base_url": base, "label": "Team server"})["account_id"]
    _ok("/api/calendar/accounts/discover", {"account_id": account_id})
    _ok("/api/calendar/accounts/select", {"account_id": account_id, "calendar_id": TEAM, "selected": True})
    _ok("/api/calendar/accounts/opt-in", {"account_id": account_id, "sync_enabled": True, "alerts_enabled": True})
    assert _ok("/api/calendar/sync", {"account_id": account_id})["reports"][0]["alerts_scheduled"] == 1
    gone = _ok("/api/calendar/accounts/disconnect", {"account_id": account_id})
    assert gone["status"] == "disconnected" and gone["alerts_cancelled"] == 1, gone
    back = _ok("/api/calendar/accounts/reconnect", {"account_id": account_id})
    assert (back["status"], back["sync_enabled"], back["alerts_enabled"]) == ("configured", False, False), back
    requests_before = state.request_count
    status, synced = api_post("/api/calendar/sync", {"account_id": account_id})
    assert status == 200 and synced["reports"][0]["status"] == "sync_off" and state.request_count == requests_before, synced

    apple = _ok("/api/calendar/accounts/add", {"provider": "eventkit", "label": "This Mac"})
    status, looked = api_post("/api/calendar/accounts/discover", {"account_id": apple["account_id"]})
    assert status == 200 and looked["ok"] is False and looked["status"] == "unsupported" and looked["recovery"], looked
    assert "EventKit" in looked["detail"], looked


def test_calendar_account_and_notification_preference_routes_are_owner_local(home):
    for path in ("/api/calendar/accounts/add", "/api/calendar/accounts/discover", "/api/calendar/accounts/select",
                 "/api/calendar/accounts/opt-in", "/api/calendar/accounts/disconnect", "/api/calendar/accounts/reconnect"):
        status, payload = api_post(path, {"account_id": "x", "provider": "caldav"}, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", (path, payload)
    for path in ("/api/calendar/accounts", "/api/notifications/preferences"):
        status, payload = api_get(path, host="203.0.113.9")
        assert status == 403 and payload.get("error") == "owner_local_required", (path, payload)


def test_settings_mounts_the_calendars_and_notifications_panels_and_reads_back_saved_preferences(home):
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    groups = {group["id"]: group for group in settings_groups()}
    assert [row["widget"] for row in groups.get("calendars", {}).get("rows", [])] == ["calendar_accounts"], groups.keys()
    assert [row["widget"] for row in groups.get("notifications", {}).get("rows", [])] == ["notifications"], groups.keys()
    html = render_vool_settings_html()
    for marker in ("w === 'calendar_accounts'", "w === 'notifications'", "VoolCalendarSettings", "VoolNotificationSettings",
                   "/api/calendar/accounts/add", "/api/calendar/accounts/discover", "/api/notifications/native/status",
                   "/api/notifications/native/test", "/api/notifications/native/authorize", "/api/notifications/preferences",
                   "No calendar accounts yet"):
        assert marker in html, marker

    status, saved = api_post("/api/notifications/preferences",
                             {"preferences": {"quiet_hours": {"enabled": True, "start": "21:30", "end": "07:15"}, "lock_screen": "hidden"}})
    assert status == 200 and saved["ok"], saved
    status, read = api_get("/api/notifications/preferences")
    assert status == 200 and read["preferences"]["quiet_hours"] == {"enabled": True, "start": "21:30", "end": "07:15"}, read
    assert read["preferences"]["lock_screen"] == "hidden", read
