"""Deterministic typed range resolution for relative and calendar date phrases.

Provides a bounded grammar that resolves expressions like "today", "yesterday",
"last Wednesday", "three hours ago", "last 3 hours", etc. into typed
:class:`TimeRange` or :class:`TimeInstant` results.

Every resolution is performed in the *user* timezone, then converted to UTC
for persistence and query.

DST law:
    Calendar-day boundaries (today, yesterday, etc.) are generated in the user
    timezone, then converted to UTC.  This means ``today`` on a 23-hour DST
    transition day still covers exactly the local calendar day, not ``now - 24h``.

Unsupported expressions:
    Return :data:`UNRESOLVED` — never a guessed timestamp.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimeRangeKind(Enum):
    """What kind of temporal range the resolver produced."""

    POINT = auto()  # A single instant, e.g. "three hours ago"
    RANGE = auto()  # An elapsed-duration range, e.g. "last 3 hours"
    CALENDAR_RANGE = auto()  # A calendar-aligned range, e.g. "today", "last Wednesday"
    UNSUPPORTED = auto()  # Expression recognised but not resolvable


#: Sentinel returned for unsupported/unresolvable phrases.
UNRESOLVED: dict[str, Any] = {"kind": "UNSUPPORTED", "phrase": None}


@dataclass
class TimeInstant:
    """A single deterministic time instant."""

    utc_dt: datetime
    description: str
    timezone: str = "UTC"

    def iso(self) -> str:
        return self.utc_dt.isoformat()


@dataclass
class TimeRange:
    """A deterministic time range [start_utc, end_utc).

    ``kind`` distinguishes calendar semantics from elapsed-duration semantics,
    which matters for DST correctness.
    """

    start_utc: datetime
    end_utc: datetime
    kind: TimeRangeKind
    description: str
    timezone: str = "UTC"

    @property
    def start_iso(self) -> str:
        return self.start_utc.isoformat()

    @property
    def end_iso(self) -> str:
        return self.end_utc.isoformat()


# ── Weekday name constants ──────────────────────────────────────────────────

_WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_WEEKDAY_PATTERN = "|".join(_WEEKDAY_NAMES)


# ── Bounded grammar regexes ─────────────────────────────────────────────────

#: ISO date: 2026-08-25 or 2026-8-25
_ISO_DATE_RE = re.compile(r"^(\d{4})\s*-\s*(\d{1,2})\s*-\s*(\d{1,2})$")

#: Month-day: "August 25", "25 August", "Aug 25", "25 Aug"
_MONTH_DAY_RE = re.compile(
    r"^(?:(\d{1,2})\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"(?:\s+(\d{1,2}))?$",
    re.IGNORECASE,
)

#: Weekday-based: "last Wednesday", "this Wednesday", "next Friday"
_WEEKDAY_RE = re.compile(
    rf"^(last|this|next|coming)\s+({_WEEKDAY_PATTERN})$", re.IGNORECASE
)

#: Elapsed hours: "three hours ago", "3 hours ago", "last 3 hours", "past 3 hours"
_ELAPSED_HOURS_RE = re.compile(
    r"^(?:last|past)\s+(\d+)\s+hours?$"
    r"|^(\d+)\s+hours?\s+ago$"
    r"|^(three|four|five|six|seven|eight|nine|ten|two|one)\s+hours?\s+ago$"
    r"|^(?:last|past)\s+(three|four|five|six|seven|eight|nine|ten|two|one)\s+hours?$",
    re.IGNORECASE,
)

#: Elapsed minutes: "30 minutes ago", "last 30 minutes"
_ELAPSED_MINUTES_RE = re.compile(
    r"^(?:last|past)\s+(\d+)\s+minutes?$"
    r"|^(\d+)\s+minutes?\s+ago$",
    re.IGNORECASE,
)

#: Three hours ago (word-number variant) already covered by _ELAPSED_HOURS_RE group 3-4

#: "this week", "last week", "this month", "last month"
_WEEK_MONTH_RE = re.compile(
    r"^(this|last)\s+(week|month)$", re.IGNORECASE
)

#: "yesterday", "today", "tomorrow"
_TODAY_RE = re.compile(r"^(today|yesterday|tomorrow)$", re.IGNORECASE)

#: "between 2pm and 5pm today"
_BETWEEN_TODAY_RE = re.compile(
    r"^between\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+and\s+"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s+today$",
    re.IGNORECASE,
)

#: "August 25 2026" or "25 August 2026"
_FULL_DATE_RE = re.compile(
    r"^(?:(\d{1,2})\s+)?"
    r"(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"(?:\s+(\d{1,2}))?"
    r"(?:\s+(\d{4}))?$",
    re.IGNORECASE,
)

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_MONTH_INDEX = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _local_midnight(dt: datetime, tz: ZoneInfo) -> datetime:
    """Return the local midnight (00:00:00) for the calendar day containing *dt*."""
    local_dt = dt.astimezone(tz)
    midnight_local = local_dt.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight_local.astimezone(timezone.utc)


def _next_local_midnight(dt: datetime, tz: ZoneInfo) -> datetime:
    """Return the UTC instant of the *next* local midnight."""
    local_dt = dt.astimezone(tz)
    midnight_local = local_dt.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    return midnight_local.astimezone(timezone.utc)


def _resolve_today(now_utc: datetime, tz: ZoneInfo) -> TimeRange:
    """today = local midnight → next local midnight."""
    start = _local_midnight(now_utc, tz)
    end = _next_local_midnight(now_utc, tz)
    tz_name = str(tz) if hasattr(tz, "key") else tz.tzname(now_utc)
    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description="today",
        timezone=str(tz),
    )


def _resolve_yesterday(now_utc: datetime, tz: ZoneInfo) -> TimeRange:
    """yesterday = the local calendar day before today.

    NOT ``now - 24h`` — must be calendar-day aligned for DST correctness.
    """
    local_dt = now_utc.astimezone(tz)
    today_midnight_local = local_dt.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_local = today_midnight_local - timedelta(days=1)
    start = yesterday_local.astimezone(timezone.utc)
    end = (yesterday_local + timedelta(days=1)).astimezone(timezone.utc)
    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description="yesterday",
        timezone=str(tz),
    )


def _resolve_tomorrow(now_utc: datetime, tz: ZoneInfo) -> TimeRange:
    """tomorrow = the local calendar day after today."""
    local_dt = now_utc.astimezone(tz)
    tomorrow_local = local_dt.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    start = tomorrow_local.astimezone(timezone.utc)
    end = (tomorrow_local + timedelta(days=1)).astimezone(timezone.utc)
    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description="tomorrow",
        timezone=str(tz),
    )


def _resolve_weekday(
    direction: str, weekday_name: str, now_utc: datetime, tz: ZoneInfo
) -> TimeRange:
    """Resolve "last <weekday>", "this <weekday>".

    *last*: the most recent occurrence strictly before today.
    *this*: today if it matches, otherwise the upcoming occurrence.
    """
    target_weekday = _WEEKDAY_NAMES[weekday_name.lower()]
    local_dt = now_utc.astimezone(tz)
    today_midnight = local_dt.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    current_weekday = today_midnight.weekday()

    direction_lower = direction.lower()

    if direction_lower in ("this", "next", "coming"):
        if current_weekday == target_weekday:
            # "this Wednesday" on Wednesday means today
            start = today_midnight.astimezone(timezone.utc)
            end = (today_midnight + timedelta(days=1)).astimezone(timezone.utc)
        else:
            # days until next occurrence (wrapping around)
            days_ahead = (target_weekday - current_weekday) % 7
            if days_ahead == 0:
                days_ahead = 7
            occurrence = today_midnight + timedelta(days=days_ahead)
            start = occurrence.astimezone(timezone.utc)
            end = (occurrence + timedelta(days=1)).astimezone(timezone.utc)
    else:  # "last"
        days_behind = (current_weekday - target_weekday) % 7
        if days_behind == 0:
            days_behind = 7
        occurrence = today_midnight - timedelta(days=days_behind)
        start = occurrence.astimezone(timezone.utc)
        end = (occurrence + timedelta(days=1)).astimezone(timezone.utc)

    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description=f"{direction} {weekday_name}",
        timezone=str(tz),
    )


def _resolve_week_month(
    direction: str, unit: str, now_utc: datetime, tz: ZoneInfo
) -> TimeRange:
    """Resolve "this week", "last week", "this month", "last month"."""
    local_dt = now_utc.astimezone(tz)
    tz_name = str(tz)

    if unit == "week":
        # Week starts on Monday (weekday() == 0)
        current_weekday = local_dt.weekday()
        if direction == "this":
            start_local = local_dt.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=current_weekday)
            end_local = start_local + timedelta(days=7)
        else:  # "last week"
            start_local = local_dt.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=current_weekday + 7)
            end_local = start_local + timedelta(days=7)
    else:  # month
        if direction == "this":
            start_local = local_dt.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            if start_local.month == 12:
                end_local = start_local.replace(year=start_local.year + 1, month=1)
            else:
                end_local = start_local.replace(month=start_local.month + 1)
        else:  # "last month"
            first_of_this = local_dt.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            if first_of_this.month == 1:
                start_local = first_of_this.replace(
                    year=first_of_this.year - 1, month=12
                )
            else:
                start_local = first_of_this.replace(month=first_of_this.month - 1)
            end_local = first_of_this

    start = start_local.astimezone(timezone.utc)
    end = end_local.astimezone(timezone.utc)
    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description=f"{direction} {unit}",
        timezone=str(tz),
    )


def _resolve_elapsed_hours(
    now_utc: datetime, hours: int, kind: str
) -> TimeRange | TimeInstant:
    """Resolve "last N hours" (RANGE) or "N hours ago" (POINT)."""
    if kind == "range":
        start = now_utc - timedelta(hours=hours)
        return TimeRange(
            start_utc=start,
            end_utc=now_utc,
            kind=TimeRangeKind.RANGE,
            description=f"last {hours} hours",
        )
    else:  # point
        point = now_utc - timedelta(hours=hours)
        return TimeInstant(
            utc_dt=point,
            description=f"{hours} hours ago",
        )


def _resolve_iso_date(
    year: int, month: int, day: int, now_utc: datetime, tz: ZoneInfo
) -> TimeRange:
    """Resolve a concrete ISO date to a calendar-day range."""
    local_dt = now_utc.astimezone(tz)
    # Construct the date in the user timezone
    try:
        date_local = datetime(year, month, day, tzinfo=tz)
    except ValueError:
        return UNRESOLVED
    start = date_local.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    end = (date_local.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)).astimezone(timezone.utc)
    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.CALENDAR_RANGE,
        description=f"{year}-{month:02d}-{day:02d}",
        timezone=str(tz),
    )


def _resolve_between_today(
    h1: int, m1: int, ap1: str, h2: int, m2: int, ap2: str,
    now_utc: datetime, tz: ZoneInfo,
) -> TimeRange:
    """Resolve "between 2pm and 5pm today"."""
    # Normalise to 24h
    def _to_24h(h: int, ap: str | None) -> int:
        if ap and ap.lower() == "pm" and h < 12:
            return h + 12
        if ap and ap.lower() == "am" and h == 12:
            return 0
        return h

    h1_24 = _to_24h(h1, ap1)
    h2_24 = _to_24h(h2, ap2)

    today_start = _local_midnight(now_utc, tz)
    local_start = today_start.astimezone(tz)
    start = local_start.replace(
        hour=h1_24, minute=m1, second=0, microsecond=0
    ).astimezone(timezone.utc)
    end = local_start.replace(
        hour=h2_24, minute=m2, second=0, microsecond=0
    ).astimezone(timezone.utc)

    return TimeRange(
        start_utc=start,
        end_utc=end,
        kind=TimeRangeKind.RANGE,
        description=f"between {h1}:{m1:02d}{ap1 or ''} and {h2}:{m2:02d}{ap2 or ''} today",
        timezone=str(tz),
    )


def resolve(
    phrase: str,
    now_utc: datetime | None = None,
    timezone_name: str | None = None,
) -> TimeRange | TimeInstant | dict[str, Any]:
    """Resolve a bounded temporal phrase to a deterministic result.

    Args:
        phrase: The expression to resolve (e.g. "today", "last Wednesday",
            "three hours ago").
        now_utc: Reference UTC instant (defaults to ``datetime.now(timezone.utc)``).
        timezone_name: IANA timezone for calendar calculations.  When ``None``
            or empty, UTC is used (so calendar phrases default to UTC days).

    Returns:
        A :class:`TimeRange`, :class:`TimeInstant`, or :data:`UNRESOLVED`
        dict.  Never a guessed timestamp.
    """
    if not phrase or not isinstance(phrase, str):
        return UNRESOLVED

    cleaned = str(phrase).strip().lower()
    if not cleaned:
        return UNRESOLVED

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    tz_name = str(timezone_name or "").strip() or "UTC"
    tz = ZoneInfo(tz_name)

    # ── Priority 1: Today / yesterday / tomorrow ────────────────────────
    m_today = _TODAY_RE.match(cleaned)
    if m_today:
        day = m_today.group(1).lower()
        if day == "today":
            return _resolve_today(now_utc, tz)
        if day == "yesterday":
            return _resolve_yesterday(now_utc, tz)
        if day == "tomorrow":
            return _resolve_tomorrow(now_utc, tz)

    # ── Priority 2: Weekday references ──────────────────────────────────
    m_wd = _WEEKDAY_RE.match(cleaned)
    if m_wd:
        direction, weekday_name = m_wd.groups()
        return _resolve_weekday(direction, weekday_name, now_utc, tz)

    # ── Priority 3: This/last week/month ────────────────────────────────
    m_wm = _WEEK_MONTH_RE.match(cleaned)
    if m_wm:
        direction, unit = m_wm.groups()
        return _resolve_week_month(direction.lower(), unit.lower(), now_utc, tz)

    # ── Priority 4: Elapsed hours (range or point) ──────────────────────
    m_elapsed = _ELAPSED_HOURS_RE.match(cleaned)
    if m_elapsed:
        groups = m_elapsed.groups()
        hours: int = 0
        kind: str = "point"
        for g in groups:
            if g is not None:
                if g in _WORD_NUMBERS:
                    hours = _WORD_NUMBERS[g]
                else:
                    hours = int(g)
                break
        if m_elapsed.group(1) is not None or m_elapsed.group(4) is not None:
            kind = "range"
        else:
            kind = "point"
        return _resolve_elapsed_hours(now_utc, hours, kind)

    # ── Priority 5: Elapsed minutes ────────────────────────────────────
    m_min = _ELAPSED_MINUTES_RE.match(cleaned)
    if m_min:
        minutes_str = m_min.group(1) or m_min.group(2)
        if minutes_str:
            minutes = int(minutes_str)
            if m_min.group(1) is not None:
                kind = "range"
            else:
                kind = "point"
            if kind == "range":
                start = now_utc - timedelta(minutes=minutes)
                return TimeRange(
                    start_utc=start,
                    end_utc=now_utc,
                    kind=TimeRangeKind.RANGE,
                    description=f"last {minutes} minutes",
                )
            else:
                point = now_utc - timedelta(minutes=minutes)
                return TimeInstant(
                    utc_dt=point,
                    description=f"{minutes} minutes ago",
                )

    # ── Priority 6: ISO dates ──────────────────────────────────────────
    m_iso = _ISO_DATE_RE.match(cleaned)
    if m_iso:
        year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(
            m_iso.group(3)
        )
        return _resolve_iso_date(year, month, day, now_utc, tz)

    # ── Priority 7: Between hours today ────────────────────────────────
    m_bt = _BETWEEN_TODAY_RE.match(cleaned)
    if m_bt:
        h1 = int(m_bt.group(1))
        m1 = int(m_bt.group(2) or 0)
        ap1 = m_bt.group(3)
        h2 = int(m_bt.group(4))
        m2 = int(m_bt.group(5) or 0)
        ap2 = m_bt.group(6)
        return _resolve_between_today(h1, m1, ap1, h2, m2, ap2, now_utc, tz)

    # ── Priority 8: Month + day ────────────────────────────────────────
    m_md = _FULL_DATE_RE.match(cleaned)
    if m_md:
        # Try to extract full date with year
        g1, month_name, g2, g3 = m_md.groups()
        month = _MONTH_INDEX.get(month_name.lower())
        if month is None:
            return UNRESOLVED
        if g3:  # Has year
            year = int(g3)
            day = int(g1 if g1 else g2 or 1)
        elif g1 and g2 and not g3:  # "August 25" or "25 August"
            # This phrase ended the match; try to figure out which is day
            # since the regex is ambiguous, try both
            try:
                day = int(g1)
            except (TypeError, ValueError):
                day = 1
            year = now_utc.astimezone(tz).year
            # If day > 31, it's a year not a day - but that's covered
        elif g1 and not g2:  # "January" only — month, no day
            day = 1
            year = now_utc.astimezone(tz).year
        else:
            return UNRESOLVED
        return _resolve_iso_date(year, month, day, now_utc, tz)

    # ── No match ────────────────────────────────────────────────────────
    return UNRESOLVED