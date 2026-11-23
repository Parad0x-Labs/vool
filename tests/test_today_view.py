"""Today in VOOL, read from the stores that own each item: a reminder set, a to-do added and a goal
left running show up on the next read; a calendar synced from a loopback provider shows today's
events and stops showing them once its sync is older than its refresh window; a goal whose worker died
with an earlier VOOL reads as stopped part way, never as running; a store that cannot be read says so.

The calendar is a disposable loopback Google-shaped fixture reached through VOOL's own transport.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core import today_view
from tests._person_turn_rig import person_env
from tests.pa_beta_gate._json_api_service import start_json_calendar_fixture
from tests.pa_beta_gate._pc_calendar_rig import Clock

ZONE = "Europe/Vilnius"
CAL = "home@fixture.test"


def _now() -> datetime:
    # Mid-morning in Vilnius today, so "today" and "tomorrow" are plain in every case.
    local = datetime.now(timezone.utc).astimezone(__import__("zoneinfo").ZoneInfo(ZONE))
    return local.replace(hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@pytest.fixture
def clock(person_env, monkeypatch):
    from core.user_preferences import save_user_timezone

    assert save_user_timezone(ZONE)
    return Clock(_now(), monkeypatch)


def test_reminders_todos_and_goals_are_read_fresh_and_a_dead_worker_is_not_running(clock):
    from core import goal_workflow, pa_tasks
    from core.operator.reminders import schedule_reminder
    from storage.db import get_connection

    empty = today_view.today(now=clock.now)
    assert all(empty[k]["state"] == "ok" and not empty[k]["items"] for k in ("reminders", "todos", "jobs")), empty

    soon = clock.now + timedelta(hours=3)
    week = clock.now + timedelta(days=6)
    late = clock.now - timedelta(hours=1)
    for note, due in (("water the plants", soon), ("renew the passport", week), ("call the bank", late)):
        schedule_reminder(session_id="chat-" + uuid.uuid4().hex[:6], task_id=uuid.uuid4().hex, note=note,
                          due_at_utc=due.isoformat(), tz_name=ZONE, get_connection_fn=get_connection)
    pa_tasks.create_task(title="buy oat milk")
    goal_id = goal_workflow.new_goal_id()
    goal_workflow.save_goal({"goal_id": goal_id, "session_id": "chat-goal", "objective": "tidy the photo folder",
                             "state": "running", "plan": {"steps": [{"id": "1", "state": "completed"}, {"id": "2", "state": "pending"}]},
                             "created_at": time.time(), "updated_at": time.time(), "started_at": time.time()})

    got = today_view.today(now=clock.now)
    reminders = got["reminders"]
    assert [r["note"] for r in reminders["items"]] == ["call the bank", "water the plants"], reminders
    assert reminders["items"][0]["overdue"] is True and reminders["later"] == 1
    assert [t["title"] for t in got["todos"]["items"]] == ["buy oat milk"]
    job = got["jobs"]["items"][0]
    # Nothing is running it in this process: it stopped with an earlier VOOL.
    assert (job["objective"], job["state"], job["done"], job["total"]) == ("tidy the photo folder", "interrupted", 1, 2), job

    pa_tasks.complete_task(got["todos"]["items"][0]["id"])
    assert today_view.today(now=clock.now)["todos"]["items"] == []


def test_todays_calendar_comes_from_the_last_sync_and_is_withheld_once_that_sync_is_old(clock):
    from core.operator import calendar_accounts, calendar_alerts

    server, state, base = start_json_calendar_fixture(dialect="google", calendars={CAL: "Home"})
    try:
        state.seed_event(CAL, "lunch@fixture", summary="Lunch with Rasa", start=clock.now + timedelta(hours=3), minutes=60, tz_name=ZONE)
        state.seed_event(CAL, "dentist@fixture", summary="Dentist", start=clock.now + timedelta(days=1, hours=1), minutes=30, tz_name=ZONE)
        assert today_view.today(now=clock.now)["calendar"] == {"state": "ok", "connected": False, "items": []}
        account = calendar_accounts.add_account(provider="google", base_url=base, label="Fixture")
        assert calendar_accounts.discover_calendars(account["account_id"], now_fn=clock)["ok"]
        assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True)["ok"]
        calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
        assert calendar_alerts.sync_account(account["account_id"], now_fn=clock)["ok"]

        calendar = today_view.today(now=clock.now)["calendar"]
        assert [e["title"] for e in calendar["items"]] == ["Lunch with Rasa"], calendar
        assert calendar["stale"] is False

        clock.advance(hours=2)  # the sync is now older than its refresh window
        later = today_view.today(now=clock.now)["calendar"]
        assert later["stale"] is True and later["items"] == [] and later["last_sync_utc"], later
    finally:
        server.shutdown()
        server.server_close()


def test_a_store_that_cannot_be_read_says_so_and_the_rest_still_show(clock, monkeypatch):
    from core import pa_tasks

    pa_tasks.create_task(title="post the parcel")

    def broken(**_kwargs):
        raise RuntimeError("disk")

    monkeypatch.setattr("core.operator.reminders.list_owner_reminders", broken)
    got = today_view.today(now=clock.now)
    assert got["reminders"]["state"] == "unreadable" and got["reminders"]["items"] == []
    assert [t["title"] for t in got["todos"]["items"]] == ["post the parcel"]
