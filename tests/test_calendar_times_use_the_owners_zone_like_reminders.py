"""Calendar windows and lines are read on the owner's zone when none is saved, like reminders (CUT-LIST 39).

A fresh profile has no saved zone. Reminders already used this computer's own zone (`owner_timezone`), but the calendar
agenda, availability and alert lines fell back to UTC: "what's on tomorrow?" on a +02:00 Mac was a UTC day, two hours
off. All names and values here are invented for this contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import local_operator_actions, user_preferences
from core.operator import calendar_agenda, calendar_alerts, calendar_provider

NOW = datetime(2026, 12, 3, 9, 15, tzinfo=timezone.utc)


@pytest.fixture
def no_saved_zone(monkeypatch):
    monkeypatch.setattr(user_preferences, "load_user_timezone", lambda: "")
    zone = {"host": "Asia/Tokyo"}
    monkeypatch.setattr(user_preferences, "host_timezone_name", lambda: zone["host"])
    return zone


@pytest.mark.parametrize("host", ["Asia/Tokyo", "America/Denver", "Europe/Vilnius"])
def test_an_agenda_day_is_the_computers_own_day(no_saved_zone, host):
    from zoneinfo import ZoneInfo

    no_saved_zone["host"] = host
    start, end, _ = calendar_agenda.parse_agenda_window("tomorrow", now_fn=lambda: NOW)
    local_day = NOW.astimezone(ZoneInfo(host)).date() + timedelta(days=1)
    assert start == datetime.combine(local_day, datetime.min.time(), ZoneInfo(host)).astimezone(timezone.utc)
    assert end - start == timedelta(days=1) or end - start == timedelta(hours=23) or end - start == timedelta(hours=25)


def test_availability_alerts_and_agenda_lines_name_the_same_zone(no_saved_zone):
    assert calendar_alerts._user_zone_name() == "Asia/Tokyo"
    assert local_operator_actions.owner_zone_name() == "Asia/Tokyo"
    start, _end, _ = calendar_provider.parse_availability_window("tomorrow afternoon", now_fn=lambda: NOW)
    if start is not None:
        from zoneinfo import ZoneInfo

        assert start.astimezone(ZoneInfo("Asia/Tokyo")).date() == datetime(2026, 12, 4).date()


def test_a_saved_zone_still_wins(monkeypatch):
    monkeypatch.setattr(user_preferences, "load_user_timezone", lambda: "Europe/Lisbon")
    monkeypatch.setattr(user_preferences, "host_timezone_name", lambda: "Asia/Tokyo")
    assert calendar_alerts._user_zone_name() == "Europe/Lisbon"
    assert local_operator_actions.owner_zone_name() == "Europe/Lisbon"


def test_nothing_readable_is_utc(monkeypatch):
    monkeypatch.setattr(user_preferences, "load_user_timezone", lambda: "")
    monkeypatch.setattr(user_preferences, "host_timezone_name", lambda: "")
    assert calendar_alerts._user_zone_name() == "UTC"
    assert local_operator_actions.owner_zone_name() == "UTC"
