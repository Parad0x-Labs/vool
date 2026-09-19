"""pa_beta_gate -- a bounded row-28 pass: persistence across restart, idempotent migration,
and no cross-account leakage with multiple configured accounts.

A simulated restart is fresh store connections and a fresh dispatcher instance over the SAME
durable home -- the mechanism a relaunch or a second window uses.

LABELLED: isolated home, loopback CalDAV fixtures, injected clock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ._caldav_service import start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home, sweep

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)
WORK_CAL = "/calendars/work/"
OTHER_CAL = "/calendars/other/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    return prepared


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def test_migration_idempotent_and_effects_survive_restart(home, monkeypatch):
    """Re-running migrations over a live store duplicates nothing; a delivered reminder and its
    repeat successor, a pending approval, and Settings selections all survive a fresh-instance
    restart; a second dispatcher does not redeliver."""
    clock = Clock(T0, monkeypatch)
    server, state, base, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    try:
        from core.operator import calendar_accounts
        from storage.db import get_connection
        from storage.migrations import run_migrations

        account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Work")
        assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
        assert calendar_accounts.select_calendar(account["account_id"], WORK_CAL, selected=True, default_write=True)["ok"]
        calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=True)

        proposed = _run('propose "Persisted" on 2026-09-22 15:00 Europe/Athens for 30m', session_id="r28")
        assert proposed.status == "approval_required", proposed.response_text
        reminded = _run("remind me to stretch on 2026-10-24 17:00 every day", session_id="r28")
        assert reminded.ok, reminded.response_text

        run_migrations()
        run_migrations()  # restart-time re-migration over the live store

        clock.advance(days=33, hours=11)
        first = sweep(clock)
        assert first["delivered"] >= 1 and first.get("repeat_scheduled", first.get("repeat_repaired", 0)) >= 1, first

        second = sweep(clock)  # a second dispatcher instance: nothing redelivered
        assert second["delivered"] == 0, second
        rows = get_connection().execute(
            "SELECT status, COUNT(*) FROM reminder_requests GROUP BY status").fetchall()
        counts = {row[0]: row[1] for row in rows}
        assert counts.get("delivered", 0) == 1 and counts.get("scheduled", 0) == 1, counts

        # the staged approval still answers after the restart, bound to its reviewed identity
        approved = _run(f"approve calendar {proposed.details['action_id']}", session_id="r28")
        assert approved.ok, approved.response_text
        assert state.snapshot()[WORK_CAL.rstrip("/") + "/"][approved.details["uid"]]["summary"] == "Persisted"

        # Settings selections survived
        selections = calendar_accounts.list_selections(account["account_id"])
        assert selections and selections[0]["selected"] and selections[0]["is_default_write"]
    finally:
        server.shutdown(); server.server_close()


def test_two_accounts_do_not_leak_into_each_other(home, monkeypatch):
    """With two configured accounts, an agenda attributes every event to its own calendar and
    a create goes to the chosen default write calendar only; the other account's provider
    never receives the event."""
    Clock(T0, monkeypatch)
    server_a, state_a, base_a, _ = start_caldav_fixture(calendars={WORK_CAL: "Work"})
    server_b, state_b, base_b, _ = start_caldav_fixture(calendars={OTHER_CAL: "Personal"})
    try:
        from core.operator import calendar_accounts

        for base, label, cal in ((base_a, "Work account", WORK_CAL), (base_b, "Personal account", OTHER_CAL)):
            account = calendar_accounts.add_account(provider="caldav", base_url=base, label=label)
            assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
            assert calendar_accounts.select_calendar(account["account_id"], cal, selected=True,
                                                     default_write=(label == "Work account"))["ok"]
            calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
        state_b.seed_event(OTHER_CAL, "priv@f", summary="Private appointment",
                           start=T0 + timedelta(days=1, hours=2), minutes=30)

        agenda = _run("What do I have on my calendar tomorrow", session_id="leak")
        assert agenda.ok, agenda.response_text
        entries = [row for row in agenda.details["events"] if row["summary"] == "Private appointment"]
        assert entries and entries[0]["calendar"] == "Personal", entries

        proposed = _run('propose "Work only" on 2026-09-23 10:00 Europe/Athens for 30m', session_id="leak")
        approved = _run(f"approve calendar {proposed.details['action_id']}", session_id="leak")
        assert approved.ok, approved.response_text
        assert list(state_b.snapshot()[OTHER_CAL.rstrip("/") + "/"]) == ["priv@f"], "the personal account never received it"
        assert approved.details["calendar_id"] == WORK_CAL
    finally:
        server_a.shutdown(); server_a.server_close()
        server_b.shutdown(); server_b.server_close()
