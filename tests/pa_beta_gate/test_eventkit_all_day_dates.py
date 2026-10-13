"""pa_beta_gate -- Apple Calendar all-day events keep their day on either side of UTC.

EventKit keeps an all-day event as floating local midnights and ends it at the last second of its
last day. The projection must read the day in the Mac's zone (the zone create_event writes in) and
carry the contract's exclusive end, whatever the zone.

LABELLED: native-shaped event doubles (accessors are calls, dates answer timeIntervalSince1970);
never a real event store. The process zone is set with TZ/tzset and restored.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

pytestmark = [pytest.mark.pa_beta]


class _Date:
    def __init__(self, at: float) -> None:
        self._at = at

    def timeIntervalSince1970(self) -> float:
        return self._at


class _AllDayEvent:
    def __init__(self, start: datetime, end: datetime) -> None:
        self._start, self._end = start.timestamp(), end.timestamp()  # naive = this process's zone

    def eventIdentifier(self):
        return "ek-day"

    def isAllDay(self):
        return True

    def startDate(self):
        return _Date(self._start)

    def endDate(self):
        return _Date(self._end)

    def lastModifiedDate(self):
        return "modified-1"

    def calendar(self):
        return None

    def title(self):
        return "Day off"


@pytest.fixture(params=["Europe/Athens", "America/Los_Angeles", "Pacific/Auckland", "UTC"])
def zone(request, monkeypatch):
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _project(event):
    from core.kas.adapters.eventkit_mac import _ek_to_cal

    return _ek_to_cal(event, provider_id="eventkit", foundation=None)


def test_a_one_day_event_reads_as_its_own_day(zone):
    day = datetime(2026, 10, 24)
    projected = _project(_AllDayEvent(day, day.replace(hour=23, minute=59, second=59)))
    assert projected.all_day
    assert (projected.start_date, projected.end_date) == ("2026-10-24", "2026-10-25"), (zone, projected)


def test_a_next_midnight_end_is_already_exclusive(zone):
    day = datetime(2026, 10, 24)
    projected = _project(_AllDayEvent(day, day + timedelta(days=1)))
    assert (projected.start_date, projected.end_date) == ("2026-10-24", "2026-10-25"), (zone, projected)


def test_a_three_day_event_spans_three_days(zone):
    first = datetime(2026, 12, 30)
    last_second = datetime(2027, 1, 1, 23, 59, 59)
    projected = _project(_AllDayEvent(first, last_second))
    assert (projected.start_date, projected.end_date) == ("2026-12-30", "2027-01-02"), (zone, projected)


def test_a_day_across_the_october_change_keeps_its_date(zone):
    day = datetime(2026, 10, 25)  # the European change happens inside this day
    projected = _project(_AllDayEvent(day, day.replace(hour=23, minute=59, second=59)))
    assert projected.start_date == "2026-10-25", (zone, projected)
