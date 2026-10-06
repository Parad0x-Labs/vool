"""Explicit-year + statement-anchored deixis regressions (2026-09-30).

Owner probes (closure-owner-review-20260930): an explicit natural-language
year is the speaker's own dating and must win over the statement-year hint
(day-first and month-first, future and historical; malformed stays None);
and a yearless month's deictic meaning is anchored on the RECORD'S OWN
statement moment, so it cannot shift at a later recall merely because the
wall clock moved. Timeless records (no seam time) keep the wall-clock
fallback, disclosed.
"""
from __future__ import annotations

import pytest

from core.temporal_selection import (
    _parse_record_date,
    declared_effective_date,
    effective_window,
)


@pytest.mark.parametrize("phrase,year_hint,expected", [
    ("November 14 2028", 2026, "2028-11-14"),
    ("14 November 2028", 2026, "2028-11-14"),
    ("November 14 2019", 2026, "2019-11-14"),
    ("14 November 2019", 2026, "2019-11-14"),
    ("March 3 2027", 2026, "2027-03-03"),
    ("3 March 2027", 2026, "2027-03-03"),
    ("2028-11-14", 2026, "2028-11-14"),
    ("November 14", 2026, "2026-11-14"),      # yearless: hint applies
    ("14 November", 2026, "2026-11-14"),
    ("31 February 2027", 2026, None),          # malformed
    ("99 November 2027", 2026, None),
    ("0 March 2027", 2026, None),
    ("14 Novembre 2027", 2026, None),          # not a month
])
def test_parse_record_date_honors_explicit_years(phrase, year_hint, expected):
    got = _parse_record_date(phrase, year_hint)
    assert (got.isoformat() if got else None) == expected


@pytest.mark.parametrize("text,expected", [
    ("The botanical night tour starts on 14 November 2028.", "2028-11-14"),
    ("The botanical night tour starts on November 14 2028.", "2028-11-14"),
    ("The botanical night tour starts on 2028-11-14.", "2028-11-14"),
    ("The botanical night tour starts on November 14 2019.", "2019-11-14"),
    ("Effective 3 March 2027 the levy changes.", "2027-03-03"),
    ("Effective March 3 2019 the levy changes.", "2019-03-03"),
])
def test_declared_effective_date_honors_explicit_years(text, expected):
    got = declared_effective_date(text, year_hint=2026)
    assert (got.isoformat() if got else None) == expected


def test_window_dates_capture_their_years_too():
    start, end = effective_window(
        "The causeway is free of charge from April 10 2019 through April 24 2019.",
        2026)
    assert start is not None and start.isoformat() == "2019-04-10"
    assert end is not None and end.isoformat() == "2019-04-24"


def test_yearless_month_is_anchored_on_the_statement_moment(monkeypatch):
    """'from March' said 2026-09-30 means NEXT March (2027-03-31), and that
    meaning is a function of the record's own text + statement time — the
    query wall clock cannot re-resolve it later."""
    statement_at = 1_790_000_000.0  # ~2026-09-28
    from datetime import datetime, timezone

    class _Frozen:
        _now = None

        @classmethod
        def now(cls, tz=None):
            assert cls._now is not None, "wall clock must not be consulted"
            if tz is None:
                return cls._now.replace(tzinfo=None)
            return cls._now

    import core.temporal_selection as ts
    real_datetime = datetime

    class _SpyDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _Frozen.now(tz)

    with monkeypatch.context() as m:
        m.setattr(ts, "datetime", _SpyDatetime)
        _SpyDatetime.__dict__  # noqa: B018 - classmethod binding check
        # freeze the wall clock far past the statement: the anchored
        # resolution must not move
        _Frozen._now = real_datetime(2028, 1, 10, tzinfo=timezone.utc)
        late = declared_effective_date(
            "The tow deposit climbs to 6 pa'anga from March.",
            year_hint=2026, statement_at=statement_at)
        _Frozen._now = real_datetime(2026, 9, 30, tzinfo=timezone.utc)
        early = declared_effective_date(
            "The tow deposit climbs to 6 pa'anga from March.",
            year_hint=2026, statement_at=statement_at)
    assert early is not None and early.isoformat() == "2027-03-31"
    assert late == early


def test_statement_anchored_forward_month_before_its_year_instance():
    """Said in January, 'from March' is THIS March — the roll-forward only
    fires when the month's instance already passed at the statement."""
    from datetime import datetime, timezone
    january_statement = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    got = declared_effective_date(
        "The levy climbs to 6 dram from March.",
        year_hint=2026, statement_at=january_statement)
    assert got is not None and got.isoformat() == "2026-03-31"


def test_timeless_record_keeps_wall_clock_fallback():
    """No seam time at all: the resolution clock governs (disclosed
    limitation — there is no record-owned moment to anchor to)."""
    got = declared_effective_date(
        "The tow deposit climbs to 6 pa'anga from March.", year_hint=2026)
    # deterministic only in shape: a month-end date in March of 2026 or 2027
    assert got is not None and got.month == 3 and got.day in (30, 31)
