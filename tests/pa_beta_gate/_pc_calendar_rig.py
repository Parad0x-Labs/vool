"""Shared rig for the product-completion calendar and notification tests.

LABELLED: the providers are the disposable loopback services in this package (``_json_api_service.py`` for Google
Calendar and Microsoft Graph, ``_caldav_service.py`` for CalDAV) reached through the VOOL transport, or a labelled
EventKit store double. The clock is injected and also stands in for the process clock authority, so a served
route and a module call in one test read the same instant.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

# 07:00 UTC on Monday 2026-09-21 is 10:00 in Vilnius (EEST, UTC+3).
T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime, monkeypatch) -> None:
        from core.time_authority import CLOCK

        self.now = start
        monkeypatch.setattr(CLOCK, "now_utc", lambda: self.now)

    def __call__(self) -> str:
        return self.now.isoformat()

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def prepare_home(tmp_path, monkeypatch, *, zone: str = "Europe/Berlin"):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(zone)
    return tmp_path


def connect(provider, clock, *, base_url="", select=(), lead=(15,), sync=True, alerts=True, adapter_factory=None):
    """Add an account, discover its calendars, choose some and opt in -- the order the settings screen uses."""
    from core.operator import calendar_accounts, calendar_alerts

    account = calendar_accounts.add_account(provider=provider, base_url=base_url, label=f"{provider} fixture account")
    discovered = calendar_accounts.discover_calendars(account["account_id"], now_fn=clock, adapter_factory=adapter_factory)
    assert discovered["ok"], discovered
    for calendar_id in select:
        chosen = calendar_accounts.select_calendar(account["account_id"], calendar_id, selected=True)
        assert chosen["ok"], chosen
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=sync, alerts_enabled=alerts)
    calendar_alerts.set_lead_minutes(account_id=account["account_id"], minutes=list(lead))
    return account["account_id"]


def sweep(clock):
    from core.operator.reminder_dispatcher import ReminderDispatcher
    from storage.db import get_connection

    return ReminderDispatcher(get_connection_fn=get_connection, sleep_fn=lambda _seconds: None, now_fn=clock).sweep_once()


def api_get(path, query=None, host="127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"), model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def api_post(path, body, host="127.0.0.1"):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(path=path, body=body, headers={"content-type": "application/json"}, runtime=RuntimeServices(display_name="N"),
                        model_name="vool", workspace_root_provider=lambda: "/tmp", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def bell(**query):
    status, payload = api_get("/api/notifications", {key: [str(value)] for key, value in query.items()})
    assert status == 200 and payload["ok"], payload
    return payload
