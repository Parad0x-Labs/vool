"""pa_beta_gate -- Settings > Calendars: a calendar account can be removed (UX finding SB-07).

``POST /api/calendar/accounts/remove`` forgets one account and what VOOL copied from it: the account record, its
calendar choices, its event projections and its pending alerts. It never reaches the provider (the real calendar is
unchanged) and never touches the credential binding the account named, which belongs to API Keys. An alert already
shown stays in the bell. An unknown id is refused; an account being synced right now is refused with "try again".

LABELLED: two disposable loopback CalDAV services from this package (``_caldav_service.py``) reached through the VOOL
transport, so a removal that called the provider would show in the service's request count.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import T0, Clock, api_get, api_post, bell, prepare_home, sweep

pytestmark = [pytest.mark.pa_beta]

TEAM, HOME = "/calendars/team/", "/calendars/home/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def two_servers(home):
    started = [start_caldav_fixture(calendars={href: name}, preferred_port=0) for href, name in ((TEAM, "Team"), (HOME, "Home"))]
    yield [(state, base) for _server, state, base, _port in started]
    for server, _state, _base, _port in started:
        server.shutdown()
        server.server_close()


def _ok(path, body):
    status, payload = api_post(path, body)
    assert status == 200 and payload.get("ok"), (path, status, payload)
    return payload


def _set_up(base, href, label):
    account_id = _ok("/api/calendar/accounts/add", {"provider": "caldav", "base_url": base, "label": label})["account_id"]
    _ok("/api/calendar/accounts/discover", {"account_id": account_id})
    _ok("/api/calendar/accounts/select", {"account_id": account_id, "calendar_id": href, "selected": True})
    _ok("/api/calendar/accounts/opt-in", {"account_id": account_id, "sync_enabled": True, "alerts_enabled": True})
    _ok("/api/calendar/alerts/policy", {"account_id": account_id, "lead_minutes": [10]})
    return account_id


def _stored(account_id):
    """What VOOL holds for one account, read straight from the store."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        keys = [row["event_key"] for row in conn.execute(
            "SELECT event_key FROM calendar_event_projections WHERE account_id = ?", (account_id,)).fetchall()]
        marks = ",".join("?" for _ in keys) or "''"
        return {
            "account": conn.execute("SELECT COUNT(*) FROM calendar_accounts WHERE account_id = ?", (account_id,)).fetchone()[0],
            "calendars": conn.execute("SELECT COUNT(*) FROM calendar_selections WHERE account_id = ?", (account_id,)).fetchone()[0],
            "events": len(keys),
            "alerts": conn.execute(
                f"SELECT COUNT(*) FROM reminder_requests WHERE source_kind = 'calendar_alert' AND source_ref IN ({marks})", keys,
            ).fetchone()[0],
            "pending": conn.execute(
                f"SELECT COUNT(*) FROM reminder_requests WHERE source_kind = 'calendar_alert' AND status = 'scheduled' AND source_ref IN ({marks})",
                keys,
            ).fetchone()[0],
        }
    finally:
        conn.close()


def test_remove_forgets_the_account_and_its_copies_and_leaves_the_other_account_and_the_provider_alone(two_servers, monkeypatch):
    """ORIGINAL: two working CalDAV accounts, each with a synced event and a pending alert. Removing one deletes its record,
    calendar choices, copied events and alerts; the other account keeps all of its own, and the removed account's server
    receives no request and still holds its event. Removing it again, or an id that never existed, is refused."""
    (team, team_base), (house, home_base) = two_servers
    clock = Clock(T0, monkeypatch)
    team.seed_event(TEAM, "standup-1", summary="Stand-up", start=T0 + timedelta(minutes=50), minutes=15)
    house.seed_event(HOME, "dinner-1", summary="Dinner", start=T0 + timedelta(minutes=70), minutes=60)
    gone_id, kept_id = _set_up(team_base, TEAM, "Team server"), _set_up(home_base, HOME, "Home server")
    for account_id in (gone_id, kept_id):
        assert _ok("/api/calendar/sync", {"account_id": account_id})["reports"][0]["alerts_scheduled"] == 1
    assert _stored(gone_id) == {"account": 1, "calendars": 1, "events": 1, "alerts": 1, "pending": 1}
    kept_before = _stored(kept_id)
    requests_before, team_events = team.request_count, json.dumps(team.calendars, sort_keys=True, default=str)

    removed = _ok("/api/calendar/accounts/remove", {"account_id": gone_id})
    assert (removed["removed"], removed["alerts_cancelled"], removed["events_deleted"], removed["calendars_deleted"]) == (True, 1, 1, 1), removed
    assert _stored(gone_id) == {"account": 0, "calendars": 0, "events": 0, "alerts": 0, "pending": 0}
    assert _stored(kept_id) == kept_before, "the other account lost something"
    assert team.request_count == requests_before, "removing an account must not contact its provider"
    assert json.dumps(team.calendars, sort_keys=True, default=str) == team_events, "the real calendar changed"

    status, view = api_get("/api/calendar/accounts")
    assert status == 200 and [account["account_id"] for account in view["accounts"]] == [kept_id], view
    status, alerts = api_get("/api/calendar/alerts")
    assert status == 200 and {alert["account_id"] for alert in alerts["upcoming"]} == {kept_id}, alerts

    for body in ({"account_id": gone_id}, {"account_id": "cal-0000000000000000"}, {}):
        status, refused = api_post("/api/calendar/accounts/remove", body)
        assert status == 404 and refused == {"ok": False, "reason": "unknown_account"}, (body, status, refused)
    clock.advance(minutes=41)   # the removed Stand-up alert was due at +40
    sweep(clock)
    assert bell()["items"] == [], "a removed account's alert fired"
    clock.advance(minutes=20)   # the kept Dinner alert is due at +60
    sweep(clock)
    assert [item["title"] for item in bell()["items"]] == ["Dinner"], "the kept account's alert did not fire"


def test_remove_keeps_the_credential_binding_and_an_alert_already_shown_and_a_new_add_starts_fresh(two_servers, monkeypatch):
    """NOVEL: the account named a verified credential binding and one of its alerts was already shown. After removal the
    binding and its stored secret are untouched, the shown alert is still in the bell, and adding the same server again
    makes a new account that waits for a new opt-in."""
    from core.credential_intelligence.binding import load_index, save_index
    from core.credential_store import get_credential, store_credential

    (team, team_base), _ = two_servers
    clock = Clock(T0, monkeypatch)
    store_credential("fixture.caldav.password", "fixture-caldav-secret", label="Fixture CalDAV password")
    rows = load_index()
    rows["fixture_caldav"] = {
        "binding_id": "cb_fixture_caldav", "provider_id": "fixture_caldav", "provider_label": "Fixture CalDAV", "account": "owner@fixture.test",
        "capability_family": "calendar", "status": "verified", "last_verified_at": "2026-09-20T09:00:00+00:00",
        "created_at": "2026-09-20T09:00:00+00:00", "slot": "fixture.caldav.password", "auth_scheme": "basic",
    }
    save_index(rows)
    index_before = json.dumps(load_index(), sort_keys=True)
    team.seed_event(TEAM, "review-1", summary="Review", start=T0 + timedelta(minutes=20), minutes=30)
    account_id = _ok("/api/calendar/accounts/add", {"provider": "caldav", "base_url": team_base, "label": "Team",
                                                     "auth_binding": "cb_fixture_caldav"})["account_id"]
    from core.operator import calendar_accounts, calendar_alerts

    # The loopback service does not check the password, so discovery and sync run without one being sent.
    assert calendar_accounts.discover_calendars(account_id, now_fn=clock)["ok"]
    calendar_accounts.select_calendar(account_id, TEAM, selected=True)
    calendar_accounts.set_opt_in(account_id, sync_enabled=True, alerts_enabled=True)
    calendar_alerts.set_lead_minutes(account_id=account_id, minutes=[10])
    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    clock.advance(minutes=11)
    sweep(clock)
    assert [item["title"] for item in bell()["items"]] == ["Review"]

    _ok("/api/calendar/accounts/remove", {"account_id": account_id})
    assert json.dumps(load_index(), sort_keys=True) == index_before, "the credential binding changed"
    assert get_credential("fixture.caldav.password") == "fixture-caldav-secret", "the stored credential changed"
    assert [item["title"] for item in bell()["items"]] == ["Review"], "an alert already shown left the bell"
    assert _stored(account_id)["account"] == 0 and _stored(account_id)["events"] == 0

    again = _ok("/api/calendar/accounts/add", {"provider": "caldav", "base_url": team_base, "label": "Team",
                                                "auth_binding": "cb_fixture_caldav"})
    assert again["created"] and again["account_id"] != account_id, again
    assert (again["status"], again["sync_enabled"], again["alerts_enabled"]) == ("configured", False, False), again


def test_remove_waits_for_a_running_sync_and_works_on_a_disconnected_or_last_account(two_servers, monkeypatch):
    """NOVEL: while the account's sync holds its lock, removal is refused with a calm "try again" and nothing is deleted.
    A disconnected account, which is also the last one, can then be removed and the list is empty."""
    from core.operator import calendar_alerts

    (team, team_base), _ = two_servers
    Clock(T0, monkeypatch)
    team.seed_event(TEAM, "standup-2", summary="Stand-up", start=T0 + timedelta(minutes=50), minutes=15)
    account_id = _set_up(team_base, TEAM, "Team server")
    _ok("/api/calendar/sync", {"account_id": account_id})
    lock = calendar_alerts._account_lock(account_id)
    assert lock.acquire(blocking=False)
    try:
        status, busy = api_post("/api/calendar/accounts/remove", {"account_id": account_id})
    finally:
        lock.release()
    assert status == 409 and busy["reason"] == "sync_in_progress" and "Try again" in busy["detail"], busy
    assert _stored(account_id)["account"] == 1 and _stored(account_id)["pending"] == 1, "a refused removal deleted something"

    _ok("/api/calendar/accounts/disconnect", {"account_id": account_id})
    _ok("/api/calendar/accounts/remove", {"account_id": account_id})
    status, view = api_get("/api/calendar/accounts")
    assert status == 200 and view["accounts"] == [], view


def test_a_calendar_list_read_that_ends_after_the_account_was_removed_records_nothing(two_servers, monkeypatch):
    """NOVEL: "Refresh calendar list" reads the provider without the sync lock. If the account is removed while that read
    is in flight, the discovery result is dropped: no calendar row is left behind without its account."""
    from core.operator import calendar_accounts

    (_team, team_base), _ = two_servers
    clock = Clock(T0, monkeypatch)
    account_id = _ok("/api/calendar/accounts/add", {"provider": "caldav", "base_url": team_base, "label": "Team"})["account_id"]

    def removing_adapter(account):
        adapter = calendar_accounts.build_adapter(account)

        class _RemovedMidway:
            def list_calendars(self):
                calendars = list(adapter.list_calendars())
                assert calendar_accounts.remove_account(account_id, now_fn=clock)["ok"]
                return calendars
        return _RemovedMidway()

    result = calendar_accounts.discover_calendars(account_id, now_fn=clock, adapter_factory=removing_adapter)
    assert result == {"ok": False, "status": "unknown_account", "calendars": []}, result
    assert _stored(account_id) == {"account": 0, "calendars": 0, "events": 0, "alerts": 0, "pending": 0}


def test_remove_is_owner_local(home):
    status, payload = api_post("/api/calendar/accounts/remove", {"account_id": "x"}, host="203.0.113.9")
    assert status == 403 and payload.get("error") == "owner_local_required", payload


def test_recovery_steps_point_at_the_card_not_at_settings_and_match_the_catalog():
    """The recovery line is shown on the account's card in Settings, so it never sends the reader to "Settings". The
    panel shows it through ``vcs.recovery.<state>``; the English catalog text is the server text."""
    from core.i18n.catalog import catalog_for, clear_catalog_cache
    from core.operator.calendar_accounts import RECOVERY

    clear_catalog_cache()
    english = catalog_for("en")._messages
    for state, text in RECOVERY.items():
        assert "in Settings" not in text, (state, text)
        if text:
            assert english.get(f"vcs.recovery.{state}") == text, state
    assert "Reconnect" in RECOVERY["disconnected"] and "remove" in RECOVERY["disconnected"]
