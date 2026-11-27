"""The calendar card says Connected, and when each chosen calendar was read, as soon as it was really read.

sls on the installed iMac, 2026-11-27 (CUT 61): Apple Calendar answered in chat, yet its card still said "Not refreshed
recently" with "Last good sync: 22/11/2026" after access was granted.

* A read of every chosen calendar in chat (agenda, free/busy) makes the account Connected as of that read.
* Opening the Calendars panel reads a calendar on this computer first when its state is not a fresh good read, and
  leaves a fresh one alone; an online account is never read for it.
* An error that escapes while a sync processes events is recorded as that attempt's state, so the card cannot keep
  an old state while every attempt fails unseen.
* The card names each chosen calendar with the time it was read, and the account's last good sync, without seconds.

LABELLED: a disposable loopback CalDAV fixture (a server on this computer, as Apple Calendar is); injected clock.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
HOME_CAL = "/calendars/home/"


@pytest.fixture
def account(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={HOME_CAL: "Home"})
    state.seed_event(HOME_CAL, "piano@fixture", summary="Piano lesson", start=T0 + timedelta(hours=3), minutes=45)
    from core.operator import calendar_accounts

    made = calendar_accounts.add_account(provider="caldav", base_url=base, label="Home calendar")
    assert calendar_accounts.discover_calendars(made["account_id"])["ok"]
    assert calendar_accounts.select_calendar(made["account_id"], HOME_CAL, selected=True)["ok"]
    calendar_accounts.set_opt_in(made["account_id"], sync_enabled=True, alerts_enabled=False)
    yield made["account_id"], state
    server.shutdown(); server.server_close()


def _age(account_id: str, *, days: int) -> None:
    """The account as sls's was: Connected once, its last good read days ago."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE calendar_accounts SET status = 'connected', last_sync_ok_at = ?, next_sync_due_at = ? "
                     "WHERE account_id = ?", ((T0 - timedelta(days=days)).isoformat(), (T0 + timedelta(hours=1)).isoformat(),
                                              account_id))
        conn.commit()
    finally:
        conn.close()


def _shown(account_id: str) -> dict:
    from core.operator import calendar_accounts

    return next(a for a in calendar_accounts.settings_view()["accounts"] if a["account_id"] == account_id)


def test_a_chat_read_makes_the_card_connected_as_of_that_read(account, monkeypatch):
    account_id, _state = account
    Clock(T0, monkeypatch)
    _age(account_id, days=5)
    assert _shown(account_id)["status"] == "stale"
    from core.operator.calendar_agenda import agenda

    got = agenda(start_utc=T0, end_utc=T0 + timedelta(days=1))
    assert [row["summary"] for row in got["entries"]] == ["Piano lesson"], got
    shown = _shown(account_id)
    assert shown["status"] == "connected" and shown["last_sync_ok_at"] == T0.isoformat(), shown


def test_a_failed_chat_read_does_not_say_connected(account, monkeypatch):
    account_id, state = account
    Clock(T0, monkeypatch)
    _age(account_id, days=5)
    from core.operator import calendar_agenda

    def refuse(_source):
        raise RuntimeError("calendar process gone")

    monkeypatch.setattr(calendar_agenda, "_build_adapter", refuse)
    got = calendar_agenda.agenda(start_utc=T0, end_utc=T0 + timedelta(days=1))
    assert got["failures"], got
    assert _shown(account_id)["status"] == "stale"


def test_opening_the_panel_reads_a_calendar_on_this_computer_that_is_not_fresh(account, monkeypatch):
    account_id, _state = account
    clock = Clock(T0, monkeypatch)
    _age(account_id, days=5)
    from core.operator.calendar_alerts import refresh_on_this_computer

    reports = refresh_on_this_computer(now_fn=clock)
    assert [r["ok"] for r in reports] == [True], reports
    shown = _shown(account_id)
    assert shown["status"] == "connected" and shown["last_sync_ok_at"] == T0.isoformat(), shown
    # Fresh now: opening the panel again a few minutes later reads nothing.
    clock.advance(minutes=3)
    assert refresh_on_this_computer(now_fn=clock) == []


def test_an_online_account_is_not_read_for_the_panel(account, monkeypatch):
    account_id, _state = account
    clock = Clock(T0, monkeypatch)
    _age(account_id, days=5)
    from core.operator import calendar_accounts
    from core.operator.calendar_alerts import refresh_on_this_computer

    monkeypatch.setattr(calendar_accounts, "_reached_on_this_computer", lambda _account: False)
    assert refresh_on_this_computer(now_fn=clock) == []
    assert _shown(account_id)["status"] == "stale"


def test_an_error_while_processing_events_is_the_attempts_state(account, monkeypatch):
    account_id, _state = account
    clock = Clock(T0, monkeypatch)
    _age(account_id, days=5)
    from core.operator import calendar_alerts

    def broken(**_kwargs):
        raise RuntimeError("projection write failed")

    monkeypatch.setattr(calendar_alerts, "_process_calendar", broken)
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] is False and report["status"] == "provider_error", report
    shown = _shown(account_id)
    assert shown["status"] == "provider_error" and shown["last_sync_ok_at"] == (T0 - timedelta(days=5)).isoformat(), shown


def test_the_card_names_each_chosen_calendar_with_its_read_time() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    from core.calendar_settings_fragment import _JS as CAL_JS
    from core.i18n.catalog import catalog_for, clear_catalog_cache
    from tests.test_calendar_settings_remove import _DOM_STUB, _PROVIDERS

    clear_catalog_cache()
    bundle = dict(catalog_for("en")._messages)
    read_at = "2025-01-05T11:05:00+00:00"  # not today, so the day is named too
    account = {
        "account_id": "cal-mac", "provider": "eventkit", "label": "This Mac", "status": "connected", "status_detail": "",
        "recovery": "", "sync_enabled": True, "last_sync_ok_at": read_at, "capability": {"label": "Apple Calendar"},
        "calendars": [
            {"calendar_id": "a", "display_name": "Family", "selected": True, "present": True, "can_write": True},
            {"calendar_id": "b", "display_name": "Work", "selected": True, "present": True, "can_write": True},
            {"calendar_id": "c", "display_name": "Holidays", "selected": False, "present": True, "can_write": False},
        ],
    }
    program = (
        _DOM_STUB
        + "const M = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        + "globalThis.VOOLT = function (key) { var t = M[key]; return (t === undefined || t === '') ? key : t; };\n"
        + "globalThis.VOOLFMT = function (t, p) { return String(t).replace(/\\{(\\w+)\\}/g, function (m, n) {"
          " return (p && p[n] != null) ? String(p[n]) : m; }); };\n"
        + "var accounts = " + json.dumps([account]) + ";\n"
        + "globalThis.fetch = function () {\n"
        + "  var payload = {ok: true, accounts: accounts, bindings: [], providers: " + json.dumps(_PROVIDERS) + "};\n"
        + "  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });\n"
        + "};\n"
        + CAL_JS
        + """
function walk(node, out) { if (!node) return out; out.push(node); (node.children || []).forEach(function (c) { walk(c, out); }); return out; }
var host = elem('div'); document.body.appendChild(host);
VoolCalendarSettings.mountInto(host);
await new Promise(function (r) { setTimeout(r, 30); });
var nodes = walk(host.children[0], []);
var rows = nodes.filter(function (n) { return n.className === 'vcs-cal'; }).map(function (n) {
  return walk(n, []).map(function (c) { return c.attrs && c.attrs['data-testid'] === 'vcs-calendar-read' ? '[' + c.textContent + ']' : ''; }).join(''); });
var chips = nodes.filter(function (n) { return /vcs-chip/.test(n.className || ''); }).map(function (n) { return n.textContent; });
var details = nodes.filter(function (n) { return n.className === 'vcs-detail'; }).map(function (n) { return n.textContent; });
console.log(JSON.stringify({ rows: rows, chips: chips, details: details, expected: new Date('""" + read_at + """').toLocaleString(undefined, {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) }));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr[:1200]
    shown = json.loads(result.stdout.strip().splitlines()[-1])
    when = shown["expected"]
    assert ":00:00" not in when, when
    assert "Connected" in shown["chips"], shown
    # Family and Work are chosen and read; Holidays is not chosen and says no read time.
    assert shown["rows"] == [f"[Read {when}]", f"[Read {when}]", ""], shown
    assert f"Last good sync: {when}" in shown["details"], shown
