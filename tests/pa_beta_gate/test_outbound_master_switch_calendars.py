"""The outbound switch ("Go online on its own", network.outbound_enabled) is the master over online calendars.

While it is off, no online calendar is contacted: not by the background sync, not for an account whose sync the
user turned on, not by Sync now and not by Refresh calendar list. Each refusal says so, the account keeps what its
last real attempt proved (no failure backoff, no lost alerts), and an unreadable switch counts as off. Apple
Calendar on this Mac and a server address on this computer are not online and are not refused. With the switch
on the same account goes back to its provider.

The real worker, the real adapter factory and the served routes run under a socket guard that records and
refuses every connection or name lookup that would leave this computer. With the switch on, the suite's own
network seal stops the dialled request first, so the switch-on half reads the transport failure instead.
"""
from __future__ import annotations

import copy
import ipaddress
import socket

import pytest

from ._pc_calendar_rig import T0, Clock, api_get, api_post, prepare_home

ONLINE = "https://caldav.example.net/dav"
CALENDAR = "/dav/calendars/work/"


def _set_switch(monkeypatch, on: bool) -> None:
    from core import policy_engine

    policy = copy.deepcopy(policy_engine.load())
    policy.setdefault("network", {})["outbound_enabled"] = on
    policy.setdefault("system", {})["local_only_mode"] = False
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)


def _local(host) -> bool:
    name = host.decode() if isinstance(host, bytes) else str(host or "")
    if name in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(name.split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture
def guard(monkeypatch):
    """Records, then refuses, every connection and name lookup that would leave this computer."""
    attempts: list[tuple[str, str]] = []
    real_connect, real_lookup = socket.socket.connect, socket.getaddrinfo

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and not _local(address[0]):
            attempts.append(("connect", str(address[0])))
            raise ConnectionRefusedError("socket guard")
        return real_connect(sock, address)

    def lookup(host, *args, **kwargs):
        if not _local(host):
            attempts.append(("lookup", str(host)))
            raise socket.gaierror(socket.EAI_NONAME, "socket guard")
        return real_lookup(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    return attempts


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


def test_background_sync_of_an_opted_in_account_stays_offline_with_the_switch_off(home, monkeypatch, guard):
    from core.operator import calendar_accounts, calendar_alerts

    account_id = _connect_online_account(home, monkeypatch)
    before = calendar_accounts.load_account(account_id)
    _set_switch(monkeypatch, False)
    worker = calendar_alerts.CalendarAlertSync(now_fn=home)
    for _tick in range(3):
        reports = worker.run_due_once()
        assert [r["status"] for r in reports] == ["online_access_off"], reports
        home.advance(minutes=11)
    assert guard == [], f"the background calendar sync went online with the switch off: {guard}"
    after = calendar_accounts.load_account(account_id)
    for field in ("status", "status_detail", "sync_failures", "last_sync_ok_at", "sync_enabled"):
        assert after[field] == before[field], field

    # A calendar server on this computer is not online: the switch does not hold it back.
    local_id = _connect_online_account(home, monkeypatch, base_url="http://127.0.0.1:9/dav")
    _set_switch(monkeypatch, False)
    assert calendar_alerts.sync_account(local_id, now_fn=home)["status"] != "online_access_off"
    assert guard == []

    # The same account and the same real worker contact the provider once the switch is on.
    _set_switch(monkeypatch, True)
    reports = worker.run_due_once()
    # Under pytest the suite's own network seal stops the request before this guard sees it; the
    # transport failure is the proof the provider was really dialled.
    assert [(r["status"], r["detail"]) for r in reports] == [("offline", "the provider could not be reached (transport_failed)")]


def test_sync_now_and_refresh_list_routes_refuse_calmly_with_the_switch_off(home, monkeypatch, guard):
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
    assert guard == []

    _set_switch(monkeypatch, True)
    status, view = api_get("/api/calendar/accounts")
    assert view["online_access"] is True
    status, synced = api_post("/api/calendar/sync", {"account_id": account_id})
    assert synced["reports"][0]["status"] == "offline", synced


def test_unreadable_switch_fails_closed(home, monkeypatch, guard):
    from core import policy_engine
    from core.operator import calendar_alerts

    account_id = _connect_online_account(home, monkeypatch)

    def unreadable():
        raise ValueError("Policy file must contain a YAML mapping/object.")

    monkeypatch.setattr(policy_engine, "automatic_outbound_enabled", unreadable)
    assert calendar_alerts.sync_account(account_id, now_fn=home)["status"] == "online_access_off"
    assert guard == []
