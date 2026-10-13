"""Timezone-aware due-time parsing for the operator scheduling lane.

One deterministic authority that turns "remind me tomorrow at 9am", "2026-09-14 09:30",
"in 20 minutes" and "next monday 8:15 Europe/Athens" into an exact UTC instant, and --
just as important -- says WHY it could not when it cannot. It never guesses: an ambiguous
or nonexistent local time (DST fold/gap), an unknown zone, or a missing time yields a typed
clarification request, not an invented instant.

All clock and zone access is injected, so tests drive fixed instants and the parser stays
pure. The returned payload carries both the UTC instant and the wall-clock it was read as,
so every artifact and receipt can show the operator their own words back.
"""
from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from core.time_authority import CLOCK

# Zone words users actually type. An IANA path always contains "/" (no English word does),
# so a slash token anywhere is an unambiguous zone name; the "... time" phrasings cover
# "9:00 Vilnius time". Bare "EST"/"CET" style military-zone abbreviations are ambiguous
# across jurisdictions and deliberately NOT mapped -- a named zone is required for them
# (an honest clarification instead of a silently wrong offset).
_ZONE_RE = re.compile(
    r"\b(?:in|at|to)\s+((?:[A-Za-z_]+(?:/[A-Za-z_\-]+){1,2}))\s+(?:time|timezone|time zone)\b"
    r"|\b((?:[A-Za-z_]+(?:/[A-Za-z_\-]+){1,2}))\s+(?:time|timezone|time zone)\b",
    re.IGNORECASE,
)
_ZONE_SLASH_RE = re.compile(r"\b([A-Za-z][A-Za-z_]*(?:/[A-Za-z0-9_+\-]+){1,2})\b")
_ZONE_ROOTS = frozenset(z.split("/")[0] for z in available_timezones() if "/" in z)
_ISO_DATETIME_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?\b")
_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(?:at\s+)(\d{1,2}):(\d{2})\b"
    # A bare 24-hour clock ("tomorrow 15:00"), the form this lane's own clarifications suggest.
    r"|(?<![\d:.\-/])\b(\d{1,2}):(\d{2})\b(?![:.]?\d)",
    re.IGNORECASE,
)
_IN_RELATIVE_RE = re.compile(
    r"\bin\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|h|days?|d|weeks?|w)\b",
    re.IGNORECASE,
)
# A calendar date in words: "5 November", "5th of Nov", "November 5th", with an optional year.
# The day number must stand alone (not part of a clock time like "18:30" or a decimal), so
# "may" or "march" next to a clock time is never a date.
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_WORD = r"(?P<{name}>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b\.?"
_DAY_NUMBER = r"(?<![\d:.\-/])(?P<{name}>\d{{1,2}})(?:st|nd|rd|th)?(?![\d:]|\.\d|\s*(?:am|pm)\b)"
_YEAR = r"(?:,?\s+(?P<{name}>20\d{{2}})\b(?!:))?"
_WORD_DATE_RE = re.compile(
    r"\b" + _DAY_NUMBER.format(name="day_a") + r"\s+(?:of\s+)?" + _MONTH_WORD.format(name="month_a") + _YEAR.format(name="year_a")
    + r"|\b" + _MONTH_WORD.format(name="month_b") + r"\s+(?:the\s+)?" + _DAY_NUMBER.format(name="day_b") + _YEAR.format(name="year_b"),
    re.IGNORECASE,
)
# A month and year with no day ("May 2026") names no single date: asked about, never dropped.
_MONTH_YEAR_RE = re.compile(r"\b" + _MONTH_WORD.format(name="month") + r",?\s+20\d{2}\b(?!:)", re.IGNORECASE)
_DAY_RE = re.compile(r"\b(yesterday|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)
_WEEKDAY_ORDER = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

MAX_FUTURE_DAYS = 366 * 2


@dataclass
class WhenResolution:
    ok: bool
    due_at_utc: str = ""
    due_wall: str = ""
    tz_name: str = ""
    basis: str = ""  # human phrase the instant was read from
    problem: str = ""  # typed clarification request when ok is False
    kind: str = ""  # absolute | relative | wallclock
    details: dict[str, Any] = field(default_factory=dict)


def format_due_time(due_at_utc: str, tz_name: str = "", due_wall: str = "") -> str:
    """Render the stored instant without changing scheduling or inventing a zone."""
    raw = str(due_at_utc or due_wall or "")
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if instant.tzinfo is None:
        return raw
    if tz_name:
        # Preserve the stored offset if a legacy zone cannot be resolved.
        with contextlib.suppress(ZoneInfoNotFoundError, ValueError):
            instant = instant.astimezone(ZoneInfo(tz_name))
    return instant.strftime("%d %b %Y at %H:%M:%S %Z")


def has_passed(resolution: WhenResolution) -> bool:
    """True when a resolved instant is before the instant it was read at.

    "In 0 minutes" resolves to the read instant itself: that is due now, not passed. Only a
    caller that needs a FUTURE instant (a reminder) asks this; a calendar search or move may
    legitimately name a past time, so the parser itself never refuses one.
    """
    if not resolution.ok:
        return False
    try:
        due = datetime.fromisoformat(str(resolution.due_at_utc).replace("Z", "+00:00"))
        read_at = datetime.fromisoformat(str(resolution.details.get("read_at_utc") or ""))
    except ValueError:
        return False
    return due < read_at


def parse_when_expression(
    text: str,
    *,
    now_fn: Callable[[], datetime] = CLOCK.now_utc,
) -> WhenResolution:
    now = now_fn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    resolution = _parse_when_expression(text, now_fn=lambda: now)
    if resolution.ok:
        resolution.details = {**resolution.details, "read_at_utc": now.astimezone(timezone.utc).isoformat()}
    return resolution


def _parse_when_expression(
    text: str,
    *,
    now_fn: Callable[[], datetime],
) -> WhenResolution:
    text = str(text or "").strip()
    if not text:
        return WhenResolution(ok=False, problem="When should this happen? Give me a date and time (for example 'tomorrow at 9:00' or '2026-09-14 09:30').")

    now = now_fn()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    host_zone = now.tzinfo

    try:
        tz_name, zone = _resolve_zone(text, host_zone)
    except ValueError as exc:
        return WhenResolution(ok=False, problem=str(exc))

    relative = _IN_RELATIVE_RE.search(text)
    if relative:
        value = float(relative.group(1))
        unit = relative.group(2).lower()
        if unit.startswith(("second", "sec")):
            seconds = value
        elif unit.startswith(("minute", "min")):
            seconds = value * 60
        elif unit.startswith(("hour", "hr", "h")):
            seconds = value * 3600
        elif unit.startswith(("day", "d")):
            seconds = value * 86400
        else:
            seconds = value * 7 * 86400
        if not math.isfinite(seconds) or seconds > MAX_FUTURE_DAYS * 86400:
            return WhenResolution(ok=False, problem="That is too far in the future to schedule reliably; give me a date within the next two years.")
        if seconds < 0:
            return WhenResolution(ok=False, problem="Give me a delay that is not negative.")
        delta = timedelta(seconds=seconds)
        due = now.astimezone(timezone.utc) + delta
        return WhenResolution(
            ok=True,
            due_at_utc=due.isoformat(),
            due_wall=due.astimezone(zone).isoformat(),
            tz_name=tz_name,
            basis=relative.group(0),
            kind="relative",
            details={"relative_seconds": delta.total_seconds()},
        )

    iso = _ISO_DATETIME_RE.search(text)
    if iso:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        if not iso.group(4):
            time_match = _TIME_RE.search(text)
            if time_match is None:
                return WhenResolution(ok=False, problem="Which time on that date? Include a clock time.")
            try:
                hour, minute = _clock_from_match(time_match)
            except ValueError as exc:
                return WhenResolution(ok=False, problem=str(exc))
        else:
            hour, minute = int(iso.group(4)), int(iso.group(5))
        return _resolve_wall(year, month, day, hour, minute, zone, tz_name, basis=iso.group(0), now=now, kind="absolute")

    word_date = _WORD_DATE_RE.search(text)
    if word_date:
        return _resolve_word_date(word_date, text, zone, tz_name, now=now)
    month_year = _MONTH_YEAR_RE.search(text)
    if month_year:
        return WhenResolution(
            ok=False, tz_name=tz_name,
            problem=f"Which day in {month_year.group(0).strip()}? Give me the date and a clock time (for example '5 {month_year.group(0).strip()} at 9:00').",
        )

    day_match = _DAY_RE.search(text)
    time_match = _TIME_RE.search(text)
    if time_match:
        try:
            hour, minute = _clock_from_match(time_match)
        except ValueError as exc:
            return WhenResolution(ok=False, problem=str(exc))
    if day_match and time_match:
        target_date = _date_for_day_word(day_match.group(1), now.astimezone(zone))
        return _resolve_wall(
            target_date.year, target_date.month, target_date.day, hour, minute,
            zone, tz_name, basis=f"{day_match.group(1)} {time_match.group(0)}", now=now, kind="wallclock",
        )
    if day_match and not time_match:
        return WhenResolution(
            ok=False,
            problem=f"Which time on {day_match.group(1)}? Give me a clock time too (for example '{day_match.group(1)} at 9:00').",
            tz_name=tz_name,
        )
    if time_match and not day_match:
        zone_now = now.astimezone(zone)
        target = zone_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= zone_now:
            target = target + timedelta(days=1)
        return _resolve_wall(
            target.year, target.month, target.day, hour, minute,
            zone, tz_name, basis=time_match.group(0), now=now, kind="wallclock",
        )

    return WhenResolution(
        ok=False,
        problem="I could not read a date or time in that. Try 'tomorrow at 9:00', 'in 20 minutes', or '2026-09-14 09:30' (you can add a named timezone like Europe/Athens).",
        tz_name=tz_name,
    )


def time_expression_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Where `parse_when_expression` could read a time in `text`: relative delays, ISO dates, clock times and day words.

    Recognition only, with no clock. A reader that needs to know WHETHER a request names a time uses this instead of a second
    copy of these patterns. The operator's approval reading does (`core.operator.approval_polarity`): the time a calendar
    request takes is the event's time, and any other time a request names defers the action.
    """
    value = str(text or "")
    patterns = (_IN_RELATIVE_RE, _ISO_DATETIME_RE, _WORD_DATE_RE, _TIME_RE, _DAY_RE)
    return tuple(sorted({match.span() for pattern in patterns for match in pattern.finditer(value)}))


def _resolve_word_date(
    match: re.Match, text: str, zone: Any, tz_name: str, *, now: datetime,
) -> WhenResolution:
    """A date written in words. With no year it is this year's date, read as said: a date
    already behind us stays in the past (a caller that needs a future instant refuses it via
    `has_passed`) instead of being rolled into next year. A weekday or day word that names a
    different day than the date is a contradiction to ask about, never one to pick from."""
    day = int(match.group("day_a") or match.group("day_b"))
    month = _MONTHS[(match.group("month_a") or match.group("month_b")).lower()]
    year_text = match.group("year_a") or match.group("year_b")
    zone_now = now.astimezone(zone)
    year = int(year_text) if year_text else zone_now.year
    said = match.group(0).strip()
    try:
        target = datetime(year, month, day).date()
    except ValueError:
        return WhenResolution(ok=False, problem=f"{said} is not a real date. Check the day and month and tell me again.", tz_name=tz_name)
    rest = text[:match.start()] + " " + text[match.end():]
    for day_word in _DAY_RE.finditer(rest):
        if _date_for_day_word_on(day_word.group(1), target, zone_now) is False:
            return WhenResolution(
                ok=False, tz_name=tz_name,
                problem=f"{target:%A %d %B %Y} is not {day_word.group(1)}. Which day did you mean?",
            )
    time_match = _TIME_RE.search(rest)
    if time_match is None:
        return WhenResolution(ok=False, problem=f"Which time on {said}? Give me a clock time too (for example '{said} at 9:00').", tz_name=tz_name)
    try:
        hour, minute = _clock_from_match(time_match)
    except ValueError as exc:
        return WhenResolution(ok=False, problem=str(exc))
    return _resolve_wall(
        target.year, target.month, target.day, hour, minute,
        zone, tz_name, basis=f"{said} {time_match.group(0).strip()}", now=now, kind="absolute",
    )


def _date_for_day_word_on(day_word: str, target: Any, zone_now: datetime) -> bool:
    """Whether a day word ("Thursday", "tomorrow") agrees with a written date."""
    word = day_word.strip().lower()
    if word in _WEEKDAY_ORDER:
        return target.weekday() == _WEEKDAY_ORDER.index(word)
    return _date_for_day_word(word, zone_now) == target


def _resolve_zone(text: str, host_zone: Any) -> tuple[str, Any]:
    match = _ZONE_RE.search(text)
    if match:
        candidate = (match.group(1) or match.group(2) or "").strip()
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise ValueError(f"I could not recognize timezone {candidate}. Give me a valid named timezone.") from None
    # An IANA path token ("Europe/Athens") with no "time" word after it.
    for slash_match in _ZONE_SLASH_RE.finditer(text):
        candidate = slash_match.group(1)
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            if candidate.split("/")[0] in _ZONE_ROOTS:
                raise ValueError(f"I could not recognize timezone {candidate}. Give me a valid named timezone.") from None
    utc = re.search(r"\b(?:UTC|GMT)([+-])(\d{1,2})(?::?(\d{2}))?\b|\b(?:UTC|GMT)\b|(?<=\d)Z\b", text, re.IGNORECASE)
    if utc:
        if utc.group(1):
            hours, minutes = int(utc.group(2)), int(utc.group(3) or 0)
            if hours > 23 or minutes > 59:
                raise ValueError('Give a valid UTC offset, such as UTC+02:00.')
            delta = timedelta(hours=hours, minutes=minutes) * (-1 if utc.group(1) == '-' else 1)
            return utc.group(0), timezone(delta)
        return 'UTC', timezone.utc
    return str(getattr(host_zone, 'key', '') or host_zone), host_zone


def _clock_from_match(match: re.Match) -> tuple[int, int]:
    if match.group(1) is not None:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = str(match.group(3) or "").lower()
        if not 1 <= hour <= 12 or not 0 <= minute <= 59:
            raise ValueError("Give me a valid clock time: hours 1–12 with am/pm and minutes 00–59.")
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0
        return hour, minute
    if match.group(4) is not None:
        hour, minute = int(match.group(4)), int(match.group(5))
    else:
        hour, minute = int(match.group(6)), int(match.group(7))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Give me a valid clock time: hours 00–23 and minutes 00–59.")
    return hour, minute


def _date_for_day_word(day_word: str, zone_now: datetime) -> datetime:
    word = day_word.strip().lower()
    if word == "today":
        return zone_now.date()
    if word == "tomorrow":
        return zone_now.date() + timedelta(days=1)
    if word == "yesterday":
        # Read as said: a past day stays in the past, and a caller that needs a future
        # instant refuses it (see `has_passed`), instead of rolling it into the future.
        return zone_now.date() - timedelta(days=1)
    target_weekday = _WEEKDAY_ORDER.index(word)
    days_ahead = (target_weekday - zone_now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # "next monday" said on a monday means the NEXT one
    return zone_now.date() + timedelta(days=days_ahead)


def _resolve_wall(
    year: int, month: int, day: int, hour: int, minute: int,
    zone: Any, tz_name: str, *, basis: str, now: datetime, kind: str,
) -> WhenResolution:
    try:
        naive = datetime(year, month, day, hour, minute)
    except ValueError as exc:
        return WhenResolution(ok=False, problem=f"That date does not exist ({exc}). Check the day and month and tell me again.")
    try:
        aware = naive.replace(tzinfo=zone)
        # Detect DST gap: the round-trip through UTC shifts the wall clock.
        roundtrip = aware.astimezone(timezone.utc).astimezone(zone)
    except (OverflowError, OSError, ValueError) as exc:
        return WhenResolution(ok=False, problem=f"That local time is not representable in {tz_name or 'your timezone'} ({exc}). Pick a time on either side of the change.")
    if roundtrip.replace(tzinfo=None) != naive:
        return WhenResolution(
            ok=False,
            problem=(
                f"{naive.strftime('%Y-%m-%d %H:%M')} does not exist in {tz_name or 'the host timezone'} -- "
                "the clocks jump across it (daylight-saving change). Tell me a time that exists, for example 30 minutes later."
            ),
            tz_name=tz_name,
        )
    # Both UTC instants are valid: the operator must choose, not the timezone default.
    try:
        second = naive.replace(tzinfo=zone, fold=1).astimezone(timezone.utc)
        if second != aware.astimezone(timezone.utc):
            return WhenResolution(
                ok=False, tz_name=tz_name, details={"dst": "ambiguous"},
                problem=f"{naive:%Y-%m-%d %H:%M} occurs twice in {tz_name or 'your timezone'}. "
                f"Choose an unambiguous time or give the intended UTC time ({aware.astimezone(timezone.utc):%H:%M} or {second:%H:%M}).",
            )
    except (OverflowError, OSError, ValueError):
        pass
    if aware.astimezone(timezone.utc) - now.astimezone(timezone.utc) > timedelta(days=MAX_FUTURE_DAYS):
        return WhenResolution(ok=False, problem="That is too far in the future to schedule reliably; give me a date within the next two years.")
    return WhenResolution(
        ok=True,
        due_at_utc=aware.astimezone(timezone.utc).isoformat(),
        due_wall=aware.isoformat(),
        tz_name=tz_name,
        basis=basis,
        kind=kind,
    )
