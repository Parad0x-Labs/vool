"""The outbound switch ("Go online on its own", network.outbound_enabled) is the master over online calendars.

While it is off, no online calendar is contacted: not by the background sync, not for an account whose sync the
user turned on, not by Sync now and not by Refresh calendar list. Each refusal says so, the account keeps what its
last real attempt proved (no failure backoff, no lost alerts), and an unreadable switch counts as off. Apple
Calendar on this Mac and a server address on this computer are not online and are not refused, and the online
accounts held back never crowd them out of a worker tick. With the switch on the same account goes back to its
provider.

How "contacted" is observed: the real worker, the real adapter factory and the served routes run with a recorder
on the outbound door's dispatch point (``remote_fetch_policy.note_remote_fetch_attempt``, called after every gate
has passed and immediately before the request is handed to urllib). The recorder refuses every request that would
leave this computer right there, as a network failure, so the tests never go online themselves.
"""
from __future__ import annotations

import copy
from urllib.parse import urlsplit

import pytest

from core.provider_http import is_loopback_url

from ._pc_calendar_rig import T0, Clock, api_get, api_post, prepare_home

ONLINE = "https://caldav.example.net/dav"
CALENDAR = "/dav/calendars/work/"


def _set_switch(monkeypatch, on: bool) -> None:
    from core import policy_engine

    policy = copy.deepcopy(policy_engine.load())
    policy.setdefault("network", {})["outbound_enabled"] = on
    policy.setdefault("system", {})["local_only_mode"] = False
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)


@pytest.fixture
def dialled(monkeypatch):
    """Hosts of every request the outbound door was about to dispatch that would leave this computer. Such a request
    is refused right there as a network failure, so nothing leaves this computer."""
    from urllib.error import URLError

    from core import remote_fetch_policy

    hosts: list[str] = []
    real = remote_fetch_policy.note_remote_fetch_attempt

    def record(url, *args, **kwargs):
        if not is_loopback_url(str(url)):
            hosts.append(str(urlsplit(str(url)).hostname or url))
            raise URLError("refused at the outbound door by the test")
        return real(url, *args, **kwargs)

    monkeypatch.setattr(remote_fetch_policy, "note_remote_fetch_attempt", record)
    return hosts


class _Calendar:
    def __init__(self, calendar_id: str, name: str) -> None:
        self.calendar_id, self.display_name, self.can_write, self.provider_default = calendar_id, name, True, True


class _Adapter:
    """Stands in for the provider ONLY during setup (discovery with the switch on); the sync under test uses the
    real adapter factory."""

    def list_calendars(self):
        return [_Calendar(CALENDAR, "Work")]


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    return Clock(T0, monkeypatch)


def _connect_online_account(clock, monkeypatch, base_url: str = ONLINE) -> str:
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base_url, label="Work server")
    _set_switch(monkeypatch, True)
    found = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock, adapter_factory=lambda _a: _Adapter())
    assert found["ok"], found
    assert calendar_accounts.select_calendar(account["account_id"], CALENDAR, selected=True)["ok"]
    assert calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=True)["ok"]
    return account["account_id"]


def test_background_sync_of_an_opted_in_account_stays_offline_with_the_switch_off(home, monkeypatch, dialled):
    from core.operator import calendar_accounts, calendar_alerts

    account_id = _connect_online_account(home, monkeypatch)
    before = calendar_accounts.load_account(account_id)
    _set_switch(monkeypatch, False)
    worker = calendar_alerts.CalendarAlertSync(now_fn=home)
    for _tick in range(3):
        assert worker.run_due_once() == [], "a held-back online account must not be synced or take a worker slot"
        assert calendar_alerts.sync_account(account_id, now_fn=home)["status"] == "online_access_off"
        home.advance(minutes=11)
    assert dialled == [], f"the background calendar sync went online with the switch off: {dialled}"
    after = calendar_accounts.load_account(account_id)
    for field in ("status", "status_detail", "sync_failures", "last_sync_ok_at", "sync_enabled"):
        assert after[field] == before[field], field

    # A calendar server on this computer is not online: the switch does not hold it back.
    local_id = _connect_online_account(home, monkeypatch, base_url="http://127.0.0.1:9/dav")
    _set_switch(monkeypatch, False)
    assert calendar_alerts.sync_account(local_id, now_fn=home)["status"] != "online_access_off"
    assert dialled == []

    # The same account and the same real worker go to the provider once the switch is on.
    _set_switch(monkeypatch, True)
    reports = worker.run_due_once()
    assert [r["account_id"] for r in reports] == [account_id]
    assert dialled == ["caldav.example.net"], dialled


def test_sync_now_and_refresh_list_routes_refuse_calmly_with_the_switch_off(home, monkeypatch, dialled):
    account_id = _connect_online_account(home, monkeypatch)
    _set_switch(monkeypatch, False)

    status, synced = api_post("/api/calendar/sync", {"account_id": account_id})
    assert status == 200 and synced["ok"] is False
    report = synced["reports"][0]
    assert report["status"] == "online_access_off" and report["reason"] == "online_access_off"
    assert "Online access is off" in report["detail"] and "Go online on its own" in report["detail"]

    status, refreshed = api_post("/api/calendar/accounts/discover", {"account_id": account_id})
    assert status == 200 and refreshed["ok"] is False and refreshed["reason"] == "online_access_off", refreshed
    assert [c["calendar_id"] for c in refreshed["calendars"]] == [CALENDAR], "a refused refresh must keep the list"

    status, view = api_get("/api/calendar/accounts")
    assert status == 200 and view["online_access"] is False
    assert dialled == []

    _set_switch(monkeypatch, True)
    status, view = api_get("/api/calendar/accounts")
    assert view["online_access"] is True
    status, synced = api_post("/api/calendar/sync", {"account_id": account_id})
    assert synced["reports"][0]["status"] == "offline", synced
    assert dialled == ["caldav.example.net"], dialled


def test_unreadable_switch_fails_closed(home, monkeypatch, dialled):
    from core import policy_engine
    from core.operator import calendar_alerts

    account_id = _connect_online_account(home, monkeypatch)

    def unreadable():
        raise ValueError("Policy file must contain a YAML mapping/object.")

    monkeypatch.setattr(policy_engine, "automatic_outbound_enabled", unreadable)
    assert calendar_alerts.sync_account(account_id, now_fn=home)["status"] == "online_access_off"
    assert dialled == []


def test_held_back_online_accounts_never_crowd_out_a_calendar_on_this_computer(home, monkeypatch, dialled):
    from core.operator import calendar_alerts
    from core.operator.calendar_alerts import WORKER_ACCOUNTS_PER_TICK
    from storage.db import get_connection

    online = [_connect_online_account(home, monkeypatch, base_url=f"https://caldav{n}.example.net/dav")
              for n in range(WORKER_ACCOUNTS_PER_TICK + 2)]
    local_id = _connect_online_account(home, monkeypatch, base_url="http://127.0.0.1:9/dav")
    conn = get_connection()
    try:  # every online account has waited longer than the local one
        conn.executemany("UPDATE calendar_accounts SET next_sync_due_at = ? WHERE account_id = ?",
                         [("2026-09-21T06:00:00+00:00", account) for account in online])
        conn.execute("UPDATE calendar_accounts SET next_sync_due_at = ? WHERE account_id = ?",
                     ("2026-09-21T06:30:00+00:00", local_id))
        conn.commit()
    finally:
        conn.close()
    _set_switch(monkeypatch, False)
    worker = calendar_alerts.CalendarAlertSync(now_fn=home)
    assert worker.due_accounts() == [local_id]
    assert [r["account_id"] for r in worker.run_due_once()] == [local_id]
    assert dialled == []
    _set_switch(monkeypatch, True)
    assert len(worker.due_accounts()) == WORKER_ACCOUNTS_PER_TICK
