"""The provider-backed calendar vertical: VOOL's side of the KAS calendar boundary.

Ownership is exact. This module owns NOTHING the transport or the adapter owns: no socket,
no credential, no permission decision. It owns what VOOL alone may own per
docs/KAS_BOUNDARY.md — user intent, task state, approval sequencing and truthful delivery:

* **configuration** — a provider is configured by name, URL and optional credential binding
  id; a request with no configuration is refused with the typed reason, never routed to a
  pretend provider;
* **availability** — free slots are COMPUTED from the provider's real events for a window,
  in the user's zone, and presented as options the user picks from; nothing is silently
  defaulted (no guessed title, time, zone, calendar or attendee);
* **effect approval** — create/update/cancel go through the SAME pending-action store and
  approval door the local operator actions use; execution re-checks conflicts and ETags so
  a change between preview and execution is rejected, not overwritten;
* **outcome truth** — a mutating request whose reply never arrived is reported as
  OUTCOME-UNPROVEN (the transport's TransportUnknownError), never as failure (which would
  invite a double-booking retry) and never as success.

An .ics file this runtime writes elsewhere remains what it always was: a local DRAFT,
labelled as one. Only a provider receipt (uid + etag + href) is an event that exists on a
calendar, and only that is ever described as one.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from core.kas.contract import (
    CalendarReadUnusableError,
    CalendarRefusedError,
    CalendarWriteAcceptedError,
    CalEvent,
    TransportDeniedError,
    TransportUnknownError,
)
from core.operator.calendar_agenda import SelectionsUnavailableError
from core.operator.effect_lifecycle import PHASE_ACCEPTED, PHASE_DISPATCHING, phase_rank
from core.operator.models import OperatorActionIntent, OperatorActionResult

_MAX_SLOT_OPTIONS = 6
_AVAILABILITY_SLOT_STEP_MINUTES = 15
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class CalendarProviderConfig:
    provider: str
    base_url: str
    auth_binding: str
    calendar_id: str
    timeout: str

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.base_url)


def load_provider_config(env: dict[str, str] | None = None) -> CalendarProviderConfig | None:
    """The one reader of calendar-provider configuration.

    Returns None when no provider is fully named — the caller reports the typed
    missing-configuration refusal. A provider named without a URL is the same honest
    absence: the configuration is incomplete, so nothing can be served.
    """
    settings = _settings_choice()
    if settings is not None:
        return settings
    source = dict(env if env is not None else os.environ)
    provider = str(source.get("VOOL_CALENDAR_PROVIDER") or "").strip().lower()
    base_url = str(source.get("VOOL_CALENDAR_URL") or "").strip()
    if not provider or not base_url:
        return None
    return CalendarProviderConfig(
        provider=provider,
        base_url=base_url,
        auth_binding=str(source.get("VOOL_CALENDAR_AUTH_BINDING") or "").strip(),
        calendar_id=str(source.get("VOOL_CALENDAR_ID") or "").strip(),
        timeout=str(source.get("VOOL_CALENDAR_TIMEOUT") or "20").strip(),
    )


def load_read_provider_config(env: dict[str, str] | None = None) -> CalendarProviderConfig | None:
    """The read-side configuration: like load_provider_config, but a Settings account with any
    selected opted-in calendar qualifies even before a default write calendar is chosen.

    Reads never guess a WRITE target: they only need a provider and a calendar to read, and
    the create path keeps requiring the explicit default write choice.
    """
    settings = _settings_choice(require_default_write=False)
    if settings is not None:
        return settings
    return load_provider_config(env=env)


def _settings_choice(*, require_default_write: bool = True) -> CalendarProviderConfig | None:
    """The account the owner chose in Settings, when that choice is complete.

    Precedence is explicit: an account with a selected default write calendar that is not
    disconnected owns the chat's calendar actions, so a leftover VOOL_CALENDAR_* environment
    default cannot silently receive them. The environment remains the documented legacy path
    while Settings has no such choice (and after a disconnect), so existing setups keep working.
    """
    try:
        from core.operator.calendar_accounts import list_accounts, list_selections
    except Exception:
        return None
    try:
        accounts = {str(row["account_id"]): row for row in list_accounts()}
        for account_id, account in accounts.items():
            if str(account.get("status") or "") == "disconnected":
                continue
            for row in list_selections(account_id):
                chosen = row.get("is_default_write") if require_default_write else (row.get("selected") and row.get("present", True))
                if chosen and row.get("selected") and row.get("present", True):
                    provider = str(account.get("provider") or "").strip().lower()
                    base_url = str(account.get("base_url") or "").strip()
                    if provider == "eventkit":
                        base_url = base_url or "eventkit://local"
                    if not provider or not base_url:
                        continue
                    return CalendarProviderConfig(
                        provider=provider,
                        base_url=base_url,
                        auth_binding=str(account.get("auth_binding") or "").strip(),
                        calendar_id=str(row.get("calendar_id") or ""),
                        timeout="20",
                    )
    except SelectionsUnavailableError:
        raise  # an UNKNOWN selection set is not "no choice": callers refuse honestly
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None  # verified pre-selections store: the legacy path applies
        raise SelectionsUnavailableError(f"the calendar selections store is unreadable ({exc})") from None
    except sqlite3.Error as exc:
        raise SelectionsUnavailableError(f"the calendar selections store is unreadable ({exc})") from None
    except Exception:
        return None
    return None


def build_provider_adapter(config: CalendarProviderConfig, *, source_context: dict[str, Any] | None = None) -> Any:
    """The configured provider's adapter, built by the one registry factory.

    The transport VOOL builds is the adapter's entire egress: permission, effect lifecycle,
    credential attachment and host pinning all happen inside it, so nothing here repeats
    those decisions.
    """
    from core.kas.registry import calendar_adapter

    return calendar_adapter(
        config.provider,
        base_url=config.base_url,
        auth_binding=config.auth_binding,
        source_context=source_context,
        options={"timeout": config.timeout},
    )


# ---------------------------------------------------------------------------
# Availability: computed from real provider events, presented as options
# ---------------------------------------------------------------------------


def parse_availability_window(
    text: str, *, now_fn: Any, zone_name: str | None = None
) -> tuple[datetime | None, datetime | None, str]:
    """The day(+part-of-day) window a user asked to check, as UTC bounds.

    "Tuesday afternoon" -> that Tuesday 12:00-18:00 in the user's zone. "Thursday" alone is
    the whole day. The zone is the user's stored zone unless the request names one — never
    a guess. Part-of-day words are the fixed public vocabulary (morning/afternoon/evening);
    an unreadable phrase is a typed clarification, not a default window. `zone_name` stands in
    for the stored zone when a caller reads the request apart from this machine's settings.
    """
    from core.user_preferences import load_user_timezone

    lowered = str(text or "").lower()
    day_match = re.search(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", lowered)
    if not day_match:
        return None, None, "Which day should I check? Tell me the date or weekday (for example 'Tuesday afternoon')."
    zone_name = zone_name or load_user_timezone() or "UTC"
    zone_match = re.search(r"\b([A-Za-z][A-Za-z_]*(?:/[A-Za-z0-9_+\-]+){1,2})\b", str(text or ""))
    if zone_match:
        try:
            ZoneInfo(zone_match.group(1))
            zone_name = zone_match.group(1)
        except Exception:
            pass  # an unreadable token is not a zone; keep the user's stored zone
    zone = ZoneInfo(zone_name)
    now = now_fn().astimezone(timezone.utc)
    local_now = now.astimezone(zone)
    word = day_match.group(1)
    if word == "today":
        target_date = local_now.date()
    elif word == "tomorrow":
        target_date = local_now.date() + timedelta(days=1)
    else:
        days_ahead = (_WEEKDAYS.index(word) - local_now.weekday()) % 7 or 7
        target_date = local_now.date() + timedelta(days=days_ahead)
    part: tuple[time, time] | None = None
    if re.search(r"\bmorning\b", lowered):
        part = (time(8, 0), time(12, 0))
    elif re.search(r"\bafternoon\b", lowered):
        part = (time(12, 0), time(18, 0))
    elif re.search(r"\bevening\b", lowered):
        part = (time(18, 0), time(22, 0))
    start_local = datetime.combine(target_date, part[0] if part else time(0, 0), tzinfo=zone)
    end_local = datetime.combine(target_date, part[1] if part else time(0, 0), tzinfo=zone)
    if part is None:
        end_local += timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), zone_name


def parse_duration_minutes(text: str) -> int | None:
    """The requested duration in minutes, exactly as asked. No minimum is imposed.

    Matches "30-minute", "free30-minute" (no space), "15 mins", "for 45m", "a half-hour"
    wherever they appear; a bare date/time never matches (no unit word follows it).
    """
    raw = str(text or "")
    match = re.search(
        r"(\d+)\s*[-\s]?\s*(minutes?|mins?|hours?|hrs?|h\b|m\b)"
        r"|\b(?:a|an)\s+half(?:-|\s)?hour\b",
        raw, re.IGNORECASE,
    )
    if not match:
        return None
    if not match.group(1):
        return 30
    value = int(match.group(1))
    unit = match.group(2).lower()
    return value * 60 if unit.startswith("h") else value


def _event_interval(event: CalEvent) -> tuple[datetime, datetime]:
    """A timed event's [start, end) in UTC, or the typed unreadable answer.

    An event whose times cannot be placed on the timeline -- not a readable time, a wall time with no
    zone, an end before its start -- is never skipped: skipping it would make busy time look free and
    a taken slot look conflict-free.
    """
    label = str(event.uid or "")[:16] or "(no id)"
    try:
        start = datetime.fromisoformat(str(event.start_utc or "").replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(event.end_utc).replace("Z", "+00:00")) if event.end_utc else start
    except ValueError:
        raise CalendarReadUnusableError(detail=f"the time of event {label} is not readable") from None
    if start.tzinfo is None or end.tzinfo is None:
        raise CalendarReadUnusableError(detail=f"event {label} has a wall time with no zone, so it cannot be placed")
    if end < start:
        raise CalendarReadUnusableError(detail=f"event {label} ends before it starts")
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def compute_free_slots(
    events: list[CalEvent],
    *,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
    tz_name: str,
) -> list[dict[str, str]]:
    """Free slot options inside the window, earliest fit first.

    Overlap is computed on UTC intervals for timed events; all-day events busy their whole
    date in the EVENT's own terms (reported as busy-all-day, never converted to invented
    instants).
    """
    duration = timedelta(minutes=max(1, int(duration_minutes)))
    busy: list[tuple[datetime, datetime]] = []
    for event in events:
        if event.all_day:
            busy.append((window_start, window_end))
            continue
        start, end = _event_interval(event)
        if end > start:
            busy.append((start, end))
    busy.sort()
    zone = ZoneInfo(tz_name)
    options: list[dict[str, str]] = []
    cursor = window_start
    index = 0
    step = timedelta(minutes=_AVAILABILITY_SLOT_STEP_MINUTES)
    while cursor + duration <= window_end and len(options) < _MAX_SLOT_OPTIONS:
        if index < len(busy):
            busy_start, busy_end = busy[index]
            if cursor + duration <= busy_start:
                options.append(_slot(cursor, duration, zone))
                cursor += step
            else:
                cursor = max(cursor, busy_end)
                index += 1
        else:
            options.append(_slot(cursor, duration, zone))
            cursor += step
    return options


def _slot(start: datetime, duration: timedelta, zone: ZoneInfo) -> dict[str, str]:
    tz_name = str(getattr(zone, "key", "") or "UTC")
    return {
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": (start + duration).astimezone(timezone.utc).isoformat(),
        "start_local": start.astimezone(zone).strftime("%Y-%m-%d %H:%M"),
        "end_local": (start + duration).astimezone(zone).strftime("%H:%M"),
        "tz_name": tz_name,
    }


def _format_event_line(event: CalEvent, tz_name: str) -> str:
    if event.all_day:
        return f"- {event.summary or '(untitled)'}: all-day on {event.start_date or 'an unreadable date'}"
    try:
        zone = ZoneInfo(event.tz_name or tz_name or "UTC")
        start = datetime.fromisoformat(event.start_utc.replace("Z", "+00:00")).astimezone(zone)
        end = datetime.fromisoformat(event.end_utc.replace("Z", "+00:00")).astimezone(zone) if event.end_utc else start
        return f"- {event.summary or '(untitled)'}: {start:%Y-%m-%d %H:%M}–{end:%H:%M} ({getattr(zone, 'key', tz_name or 'UTC')})"
    except Exception:
        return f"- {event.summary or '(untitled)'}: {event.start_utc} (zone not readable)"


def resolve_calendar(config: CalendarProviderConfig, adapter: Any, *, named: str | None) -> tuple[str, str]:
    """The calendar to act on: the named one, else the configured one, else the only one.

    Never a silent cross-calendar guess: several calendars and no selection is a typed
    clarification.
    """
    calendars = adapter.list_calendars()
    if not calendars:
        raise CalendarRefusedError(404, reason="no_calendars", detail="the provider serves no calendar collections")
    wanted = str(named or "").strip().casefold()
    if wanted:
        for calendar in calendars:
            tail = calendar.calendar_id.strip("/").rpartition("/")[2].casefold()
            if calendar.display_name.casefold() == wanted or tail == wanted:
                return calendar.calendar_id, calendar.display_name
        raise CalendarRefusedError(404, reason="unknown_calendar", detail=f"no calendar named {wanted!r} on this provider")
    if config.calendar_id:
        for calendar in calendars:
            if calendar.calendar_id == config.calendar_id or calendar.calendar_id.rstrip("/").endswith(config.calendar_id.strip("/")):
                return calendar.calendar_id, calendar.display_name
        raise CalendarRefusedError(404, reason="configured_calendar_missing", detail="the configured calendar is not served by this provider")
    if len(calendars) == 1:
        return calendars[0].calendar_id, calendars[0].display_name
    names = ", ".join(calendar.display_name for calendar in calendars[:8])
    raise CalendarRefusedError(400, reason="calendar_choice_required", detail=f"several calendars are served ({names}); name which one")


# ---------------------------------------------------------------------------
# Proposal parsing: title + explicit time, no silent defaults
# ---------------------------------------------------------------------------


def offered_slot_number(text: str) -> int | None:
    """The offered slot a follow-up names ('option 2, propose ...'), as its 1-based number, or None."""
    match = re.search(r"\boption\s+(\d+)\b", str(text or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def parse_event_proposal(text: str) -> dict[str, Any]:
    """A proposal needs a TITLE. The time is parsed by the caller's when-authority.

    Title sources, in order: an explicit quoted title; 'called/named/titled X'; the noun
    phrase between the propose-verb and the time clause ("propose a project review on
    Tuesday 15:00" -> "project review"). An unreadable request returns an empty title and
    the caller asks — it never becomes "Meeting".
    """
    raw = str(text or "")
    quoted = re.findall(r'["\u201c]([^"\u201d]+)["\u201d]', raw)
    title = quoted[0].strip() if quoted else ""
    if not title:
        after = re.search(
            r"\b(?:propose|schedule|create|set\s+up|book)\s+(?:an?\s+)?(?:meeting|event|call|review|sync|appointment)\s+(?:called|named|titled)\s+([^\n,.]+)",
            raw, re.IGNORECASE,
        )
        if after:
            title = after.group(1).strip()
    if not title:
        subject = re.search(
            r"\b(?:propose|schedule|create|set\s+up|book)\s+(?:an?\s+)?(?:project\s+|quick\s+|team\s+|prep\s+|kickoff\s+|planning\s+|release\s+)?([A-Za-z][\w \-']{2,40}?)\s+(?=\"|on\b|at\b|for\b|tomorrow\b|today\b|next\b|this\b|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d)",
            raw, re.IGNORECASE,
        )
        if subject:
            title = subject.group(1).strip()
    return {"title": title}


_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def parse_attendees(text: str) -> list[str]:
    """Attendee addresses the request EXPLICITLY names, in order, deduplicated.

    Only an explicit address makes someone an attendee: creating an ordinary personal event
    never invites anyone, and a bare name without an address is a question, not a guess.
    """
    lowered = str(text or "")
    if not re.search(r"\b(?:with|invite|inviting|attendees?|guests?|participants?)\b", lowered, re.IGNORECASE):
        return []
    seen: dict[str, None] = {}
    for match in _EMAIL_RE.findall(lowered):
        seen.setdefault(match.strip().lower(), None)
    return list(seen)


def _attendees_preview_lines(attendees: list[str], *, provider: str = "") -> list[str]:
    if not attendees:
        return ["- Attendees: none (no invitations are requested; an ordinary personal event)"]
    policy = {
        "google": "the create is sent with sendUpdates=all, Google's documented request to email every guest",
        "graph": "Microsoft 365 notifies attendees of new events with attendees by its own documented behavior",
        "caldav": "the attendees are written as RFC 5545 ATTENDEE lines for the server's scheduling service -- whether THIS server delivers invitations is its capability",
    }.get(str(provider or "").lower(), "the provider's own attendee-notification behavior applies")
    return [
        "- Attendees (their invitation is part of this reviewed action):",
        *(f"    - {address}" for address in attendees),
        f"- Invitation effect: {policy}. What the receipt can prove is the REQUEST being accepted -- each guest's actual delivery and response stay theirs.",
    ]


_RRULE_DAYS = {"monday": "MO", "tuesday": "TU", "wednesday": "WE", "thursday": "TH",
               "friday": "FR", "saturday": "SA", "sunday": "SU"}


def parse_recurrence_rule(text: str) -> tuple[str, str]:
    """(rrule fragment, plain-words description) for a basic repeating-event request, or ("", "").

    Supported shapes only: every day; every <weekday>(s); every month [on the Nth]. Anything
    richer ("every second Tuesday", "until March", counts) is left to the provider's own UI —
    the refusal text says so rather than approximating.
    """
    lowered = str(text or "").lower()
    if re.search(r"\bevery day\b|\bdaily\b|\beach day\b|\beveryday\b", lowered):
        return "FREQ=DAILY", "repeats every day"
    if re.search(r"\bevery month\b|\bmonthly\b", lowered):
        month_nth = re.search(r"\bon the ([0-9]{1,2})(?:st|nd|rd|th)\b", lowered)
        if month_nth and 1 <= int(month_nth.group(1)) <= 31:
            day = int(month_nth.group(1))
            return f"FREQ=MONTHLY;BYMONTHDAY={day}", f"repeats every month on day {day}"
        if not month_nth:
            return "FREQ=MONTHLY", "repeats every month (same day of month as the first occurrence)"
    weekdays = re.findall(r"\bevery ((?:(?:mon|tues|wednes|thurs|fri|satur|sun)day)(?:s)?(?: and (?:mon|tues|wednes|thurs|fri|satur|sun)days?)*)\b", lowered)
    if weekdays:
        days = []
        for chunk in weekdays:
            for word in re.split(r"\band\b|\s*,\s*", chunk):
                word = word.strip()
                if word and word.rstrip("s") in _RRULE_DAYS:
                    days.append(_RRULE_DAYS[word.rstrip("s")])
        if days:
            names = {code: word for word, code in _RRULE_DAYS.items()}
            return ("FREQ=WEEKLY;BYDAY=" + ",".join(dict.fromkeys(days)),
                    "repeats weekly on " + ", ".join(names[code].title() for code in dict.fromkeys(days)))
    return "", ""


def _other_selected_conflicts(config: CalendarProviderConfig | None, calendar_id: str,
                              start: datetime, end: datetime) -> tuple[list[Any], list[dict[str, Any]]]:
    """(conflicts, unreadable sources) across every OTHER opted-in selected calendar.

    The pre-effect availability check reads the complete relevant selected set, not only the
    create target: a conflict that appeared on another chosen calendar after the preview stops
    the effect. A source that cannot be read is returned as a failure -- availability is never
    claimed from a partial read.
    """
    from core.operator.calendar_agenda import AgendaSource, busy_cal_events

    target = AgendaSource(
        account_id="", provider=str(config.provider if config is not None else ""), base_url=str(config.base_url if config is not None else ""),
        auth_binding=str(config.auth_binding if config is not None else ""), account_label="",
        calendar_id=str(calendar_id or ""), calendar_name="",
    )
    events, failures, _sources = busy_cal_events(start_utc=start, end_utc=end, exclude=target)
    # No uid exclusion here: the TARGET calendar is excluded by source identity, and an event
    # with the moved event's uid in a DIFFERENT calendar is that calendar's own conflict -- a
    # uid match is not identity across calendars.
    conflicts = _find_conflicts(events, start, end)
    return conflicts, failures


def _unreadable_sources_text(failures: list[dict[str, Any]]) -> str:
    names = ", ".join(f"{failure['source']} ({failure['status']})" for failure in failures[:4])
    return names or "a chosen calendar"


_OCCURRENCE_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                      "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


def named_occurrence_date(text: str) -> tuple[Any, ...] | None:
    """The occurrence date a request names, read before any event is consulted.

    ("date", year, month, day) for a marked or ISO date; ("month_day", month, day) for 'October 7' or '7 October';
    None when the request names no date. A date the request itself marks as the occurrence ("the 2026-10-07
    occurrence", "occurrence on 2026-10-07") names it even when another date -- a move's destination -- comes first in
    the text. Unmarked, the first date is read.
    """
    raw = str(text or "")
    marked = re.search(r"\bthe\s+(20\d{2})-(\d{2})-(\d{2})\s+occurrence\b|\boccurrence\s+(?:on|of|dated)\s+(20\d{2})-(\d{2})-(\d{2})\b",
                       raw, re.IGNORECASE)
    if marked:
        year, month, day = [group for group in marked.groups() if group]
        return "date", year, month, day
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", raw)
    if iso:
        return "date", iso.group(1), iso.group(2), iso.group(3)
    month_day = re.search(r"\b(" + "|".join(_OCCURRENCE_MONTHS) + r")\s+(\d{1,2})\b|\b(\d{1,2})\s+(" + "|".join(_OCCURRENCE_MONTHS) + r")\b",
                          raw, re.IGNORECASE)
    if month_day:
        return "month_day", _OCCURRENCE_MONTHS[(month_day.group(1) or month_day.group(4)).lower()], int(month_day.group(2) or month_day.group(3))
    return None


def _named_occurrence(text: str, matching: list[Any]) -> tuple[list[Any], str]:
    """(filtered matches, note) when the request NAMES a date: one series' occurrences match
    their titles; the dated ones are the target. An unnamed multi-match stays ambiguous."""
    named = named_occurrence_date(text)
    target = None
    if named is not None and named[0] == "date":
        target = f"{named[1]}-{named[2]}-{named[3]}"
    elif named is not None:
        _kind, month, day = named
        from datetime import date as _date

        # resolve the year against the nearest future occurrence of that month/day
        candidates = sorted({str(getattr(event, "start_utc", "") or "")[:10] for event in matching})
        for stamp in [*candidates, f"{_date.today().year + 1}-{month:02d}-{day:02d}"]:
            if stamp.endswith(f"-{month:02d}-{day:02d}"):
                target = stamp
                break
    if target is None:
        return matching, ""
    dated = [event for event in matching if str(getattr(event, "start_utc", "") or "").startswith(target)]
    if len(dated) == 1:
        return dated, f"the occurrence on {target}"
    return dated, f"the occurrences on {target}" if dated else ""


def _is_series_master(event: Any) -> bool:
    """Canonical series identity: the typed provider-seam flag, with the raw RRULE text as a
    belt for representations parsed before the flag existed. Graph's JSON recurrence and
    seriesMaster type arrive through the same flag, so no provider is missed."""
    return bool(getattr(event, "recurring", False)) or "RRULE:" in str(getattr(event, "raw_component", "") or "").upper()


def _names_series_scope(text: str) -> bool:
    return bool(re.search(r"\b(?:whole|entire|all|full)\b[^\n!?.]{0,40}?\b(?:series|occurrences?)\b|\bevery occurrence\b", str(text or ""), re.IGNORECASE))


def _series_scope_question(event: Any, text: str) -> str:
    """A repeating event was resolved WITHOUT the user naming its scope: ask, never guess.

    CalDAV serves the series master as one object, so an unnamed 'move/cancel the X event'
    would silently rewrite every occurrence. Google and Graph occurrences carry their own
    ids (a single instance resolves to that instance), so only the master case needs the
    question. Saying 'the whole series' (or 'all occurrences') answers it in one word.
    """
    if not _is_series_master(event):
        return ""
    if _names_series_scope(text):
        return ""
    title = str(getattr(event, "summary", "") or "this event")
    return (f"\"{title}\" is a repeating series, and this calendar serves it as ONE object covering every "
            f"occurrence. Say \"the whole {title} series\" to change every occurrence, or name the exact date of "
            f"the one occurrence you mean — I will not guess between them.")


def _recurrence_unsupported_text(text: str) -> str:
    return ("That repeat pattern is not one of the basic shapes VOOL creates (every day, every week on named "
            "weekdays, every month on a day of the month). Propose the first occurrence without the repeat and set "
            "the richer rule in the calendar app, where its provider's own semantics apply.")


def _refusal_text(exc: CalendarRefusedError) -> str:
    if exc.reason == "not_found":
        return "The calendar provider answered, but no such event exists there now. It may have been moved or cancelled already."
    if exc.reason in {"unreadable_response", "unparseable_reply"}:
        return ("The calendar provider answered, but what it returned could not be read as calendar data, so I can't tell what is "
                f"on the calendar there. Nothing is reported as free, empty or missing. ({exc.detail or exc.reason})")
    if exc.reason == "incomplete_read":
        return ("The calendar provider's listing could not be read to the end, so it is incomplete. Nothing is reported as free "
                f"or conflict-free. ({exc.detail or exc.reason})")
    if exc.reason == "precondition_failed":
        return "The event changed on the provider after you last saw it (version mismatch). Nothing was overwritten: re-check the event and approve again."
    if exc.reason == "rate_limited":
        return "The calendar provider is rate-limiting requests right now. Nothing was changed; try again shortly."
    if exc.reason in {"http_401", "http_403", "authorization_expired"}:
        return "The calendar provider refused the request as unauthorized (the connected account's authorization is missing or expired). Nothing was changed; the credential needs attention."
    if exc.reason == "unknown_calendar":
        return f"The provider does not serve that calendar. {exc.detail}"
    if exc.reason == "calendar_choice_required":
        return f"Several calendars are configured on this provider — name which one. {exc.detail}"
    return f"The calendar provider refused the request. {exc.detail or exc.reason}"


def _unknown_outcome_text(exc: TransportUnknownError) -> str:
    return (
        "The calendar request left this machine but its result could not be proven "
        f"({exc.reason}). It may or may not have been applied. I have NOT retried it and I am NOT "
        "claiming success or failure. Ask me to check the event to see the provider's actual state before doing anything else."
    )


def _overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _find_conflicts(events: list[CalEvent], start: datetime, end: datetime) -> list[CalEvent]:
    conflicts: list[CalEvent] = []
    for event in events:
        if event.all_day:
            conflicts.append(event)
            continue
        event_start, event_end = _event_interval(event)
        if _overlap(start, end, event_start, event_end):
            conflicts.append(event)
    return conflicts


def _event_brief(event: CalEvent) -> dict[str, str]:
    return {
        "uid": event.uid,
        "summary": event.summary,
        "start_utc": event.start_utc,
        "end_utc": event.end_utc,
        "all_day": str(event.all_day),
        "start_date": event.start_date,
        "etag": event.etag,
        "href": event.href,
        "tz_name": event.tz_name,
        "calendar_id": event.calendar_id,
    }


def _events_titled(adapter: Any, calendar_id: str, label: str, *, now_fn: Any) -> list[CalEvent]:
    now = now_fn().astimezone(timezone.utc)
    events = adapter.events_in_range(
        calendar_id,
        start_utc=(now - timedelta(days=60)).isoformat(),
        end_utc=(now + timedelta(days=366)).isoformat(),
    )
    wanted = str(label or "").strip().casefold()
    if not wanted:
        return []
    return [event for event in events if event.summary.casefold() == wanted or event.uid == wanted or event.uid.startswith(wanted)]


# ---------------------------------------------------------------------------
# The vertical's operations (invoked by the dispatch handlers)
# ---------------------------------------------------------------------------


def check_availability(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    config: CalendarProviderConfig,
    adapter: Any,
    now_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del session_id
    window_start, window_end, zone_name = parse_availability_window(intent.raw_text, now_fn=now_fn)
    if window_start is None:
        return OperatorActionResult(ok=False, status="invalid_request", response_text=window_end or "Which day should I check?", details={})
    duration = parse_duration_minutes(intent.raw_text)
    try:
        calendar_id, calendar_name = resolve_calendar(config, adapter, named=intent.target_label)
        events = adapter.events_in_range(calendar_id, start_utc=window_start.isoformat(), end_utc=window_end.isoformat())
        # Row 6: busy is EVERY opted-in selected calendar's busy, not only the create
        # target's. A conflict on another chosen calendar makes the slot occupied there too,
        # and each calendar that could not be read is reported, never assumed empty.
        from core.operator.calendar_agenda import AgendaSource, busy_cal_events

        target_source = AgendaSource(
            account_id="", provider=config.provider, base_url=config.base_url, auth_binding=config.auth_binding,
            account_label="", calendar_id=calendar_id, calendar_name=calendar_name,
        )
        others_events, other_failures, other_sources = busy_cal_events(
            start_utc=window_start, end_utc=window_end, exclude=target_source,
        )
        events = list(events) + list(others_events)
        other_names = {source.calendar_id: source.calendar_name for source in other_sources}
        slots = compute_free_slots(events, window_start=window_start, window_end=window_end, duration_minutes=duration, tz_name=zone_name) if duration else []
    except CalendarRefusedError as exc:
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "status_code": exc.status_code})
    except TransportDeniedError as exc:
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason})
    except TransportUnknownError as exc:  # read-only: unknown means unusable data, reported honestly
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason})
    window_local = f"{window_start.astimezone(ZoneInfo(zone_name)):%Y-%m-%d %H:%M}–{window_end.astimezone(ZoneInfo(zone_name)):%H:%M} {zone_name}"
    audit_log_fn(
        "operator_action_check_availability",
        target_id=task_id,
        target_type="task",
        details={"calendar_id": calendar_id, "window_start": window_start.isoformat(), "window_end": window_end.isoformat(),
                 "event_count": len(events), "other_calendar_count": len(other_sources), "failed_sources": other_failures},
    )
    other_count = len(others_events)
    lines = [f"Calendar: {calendar_name} — {window_local} ({len(events) - other_count} event(s) there):"]
    for event in events[:len(events) - other_count]:
        lines.append(_format_event_line(event, zone_name))
    if other_count:
        lines.append(f"Busy on your other chosen calendars ({other_count} event(s)):")
        for event in others_events:
            lines.append(_format_event_line(event, zone_name) + f" — {other_names.get(event.calendar_id, 'another chosen calendar')}")
    for failure in other_failures:
        lines.append(f"One chosen calendar could not be read: {failure['source']} is {failure['status']}; no slot on it is reported as free.")
    details: dict[str, Any] = {"calendar_id": calendar_id, "calendar_name": calendar_name, "events": [_event_brief(event) for event in events], "zone": zone_name,
                               "other_calendar_count": len(other_sources), "failed_sources": other_failures}
    if duration:
        lines.append("")
        if slots:
            lines.append(f"Free {duration}-minute options:")
            for index, slot in enumerate(slots, start=1):
                lines.append(f"{index}. {slot['start_local']}–{slot['end_local']} ({slot['tz_name']})")
            lines.append("")
            lines.append("Pick one by replying with its number and the title, for example: 'option 2, propose \"Project review\"'.")
            details["options"] = slots
            details["duration_minutes"] = duration
        else:
            lines.append(f"There is no free {duration}-minute slot in that window.")
            details["options"] = []
            details["duration_minutes"] = duration
    return OperatorActionResult(ok=True, status="reported", response_text="\n".join(lines), details=details)


def list_calendars(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    adapter: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    del session_id
    try:
        calendars = adapter.list_calendars()
    except CalendarRefusedError as exc:
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason})
    except TransportDeniedError as exc:
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason})
    except TransportUnknownError as exc:
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason})
    audit_log_fn(
        "operator_action_list_calendars",
        target_id=task_id,
        target_type="task",
        details={"count": len(calendars)},
    )
    lines = ["Calendars on the configured provider:"]
    for calendar in calendars:
        marker = " (configured default)" if intent.target_label and calendar.display_name.casefold() == intent.target_label.casefold() else ""
        lines.append(f"- {calendar.display_name}{marker} [{calendar.calendar_id}]")
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"calendars": [{"calendar_id": c.calendar_id, "display_name": c.display_name} for c in calendars]},
    )


def inspect_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    config: CalendarProviderConfig,
    adapter: Any,
    resolve_owned_event_fn: Any,
    now_fn: Any,
    audit_log_fn: Any,
) -> OperatorActionResult:
    """Inspect one event: by receipt (this session's stable identity) or by exact title.

    Text the provider stores is UNTRUSTED DATA: displayed, never executed. A description
    asking for an effect is shown as content and flagged as such.
    """
    label = str(intent.target_label or "").strip() or str(intent.action_id or "").strip()
    calendar_id = ""
    uid = ""
    record = resolve_owned_event_fn(session_id=session_id, label=label) if label else None
    if record is not None and not record.get("selection_error"):
        calendar_id = str(record.get("calendar_id") or "")
        uid = str(record.get("uid") or "")
    try:
        if not calendar_id:
            calendar_id, _name = resolve_calendar(config, adapter, named=intent.destination_path)
        if not uid:
            matching = _events_titled(adapter, calendar_id, label, now_fn=now_fn)
            if len(matching) != 1:
                if not matching:
                    return OperatorActionResult(ok=False, status="not_found", response_text=f"No event titled {label!r} on that calendar right now.", details={"label": label})
                # A dated request resolves same-title expanded occurrences to THE named date:
                # one occurrence, addressed by its own RECURRENCE-ID identity.
                dated, _note = _named_occurrence(intent.raw_text, matching)
                if len(dated) == 1 and dated[0] is not matching[0]:
                    matching = dated
                # A request that NAMES the series scope may resolve every same-title match to
                # the one series they belong to: expanded occurrences share their master.
                elif _names_series_scope(intent.raw_text):
                    series_uids = {str(event.series_id or event.uid) for event in matching}
                    if len(series_uids) == 1:
                        matching = [adapter.get_event(calendar_id, series_uids.pop())]
                if len(matching) != 1:
                    choices = "; ".join(f"{_format_event_line(event, '')} (uid {event.uid[:8]})" for event in matching[:5])
                    return OperatorActionResult(ok=False, status="ambiguous", response_text=f"Several events match {label!r}. Tell me which one: {choices}", details={"matches": len(matching)})
            uid = matching[0].uid
        event = adapter.get_event(calendar_id, uid)
        scope_question = _series_scope_question(event, intent.raw_text)
        if scope_question:
            return OperatorActionResult(ok=False, status="ambiguous", response_text=scope_question,
                                         details={"uid": uid, "series": True})
    except CalendarRefusedError as exc:
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason})
    except TransportDeniedError as exc:
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason})
    except TransportUnknownError as exc:
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason})
    audit_log_fn(
        "operator_action_inspect_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"uid": event.uid, "calendar_id": calendar_id},
    )
    lines = [
        f"Event: {event.summary or '(untitled)'}",
        _format_event_line(event, ""),
        f"Calendar: {calendar_id}",
        f"Identity: uid {event.uid} · version {event.etag or 'unknown'}",
    ]
    if event.description:
        lines.append("Description (untrusted content, shown as stored):")
        for line in str(event.description).splitlines()[:20]:
            lines.append(f"  {line}")
        if re.search(r"\b(approve|authorize|send|email|invite|forward)\b", str(event.description), re.IGNORECASE):
            lines.append("")
            lines.append("Note: the description contains words that look like instructions. Event text is data, not authorization — nothing was acted on.")
    return OperatorActionResult(
        ok=True,
        status="reported",
        response_text="\n".join(lines),
        details={"event": _event_brief(event)},
    )


def propose_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    config: CalendarProviderConfig,
    adapter: Any,
    parse_when_fn: Any,
    now_fn: Any,
    create_pending_action_fn: Any,
    evaluate_local_action_fn: Any,
    audit_log_fn: Any,
    note_path: str = "",
    execute_fn: Any = None,
    slot_duration_minutes: int | None = None,
) -> OperatorActionResult:
    """Preview a REAL provider event and stage it for approval.

    ``slot_duration_minutes`` is the length of an offered slot the user chose: choosing an option
    proposes THAT interval (it was computed free for exactly that long) unless the request names its
    own duration.

    The staged scope carries the exact conflict snapshot the preview showed, so execution
    can prove the situation did not change between preview and approval. Creating an
    event on an external provider is an outward-facing effect: it ALWAYS waits for the
    explicit 'approve calendar <id>' turn, whatever the local autonomy mode is.
    """
    del now_fn, evaluate_local_action_fn, execute_fn
    parsed = parse_event_proposal(intent.raw_text)
    title = str(parsed["title"] or "").strip()
    when = parse_when_fn(intent.raw_text)
    if not title:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text='I can propose an event, but I need its title. Use a form like: propose "Project review" Tuesday 15:00 for 30 minutes.',
            details={},
        )
    if when is None or not when.ok:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=(getattr(when, "problem", "") or "Which time should the event start? Give me a date and time, or pick one of the offered slots."),
            details={},
        )
    start = datetime.fromisoformat(when.due_at_utc)
    duration = parse_duration_minutes(intent.raw_text) or slot_duration_minutes or 30
    end = start + timedelta(minutes=duration)
    try:
        calendar_id, calendar_name = resolve_calendar(config, adapter, named=intent.target_label)
        overlapping = adapter.events_in_range(calendar_id, start_utc=start.isoformat(), end_utc=end.isoformat())
        conflicts = _find_conflicts(overlapping, start, end)
    except CalendarRefusedError as exc:
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason})
    except TransportDeniedError as exc:
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason})
    except TransportUnknownError as exc:
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason})
    if conflicts:
        lines = [f"That time is not free on {calendar_name}:"]
        for conflict in conflicts:
            lines.append(_format_event_line(conflict, when.tz_name))
        lines.append("")
        lines.append("Nothing was created. Ask me to check the day for free slots, or propose a different time.")
        return OperatorActionResult(ok=False, status="conflict", response_text="\n".join(lines), details={"conflicts": [_event_brief(c) for c in conflicts]})
    recurrence_rule, recurrence_words = parse_recurrence_rule(intent.raw_text)
    # Saved contacts named in the invitation part resolve through the ONE Contacts resolver
    # (core.contacts.attendees -- the consumer the contacts lane shipped beside this owner):
    # each named part becomes exactly one saved email endpoint, an ambiguous or address-less
    # name makes the proposal ASK, and a phrase that names no saved contact stays out. An
    # explicit address remains an attendee exactly as written; nothing is guessed, nothing
    # here sends an invitation.
    try:
        from core.contacts.attendees import attendees_from_request

        _resolution = attendees_from_request(intent.raw_text, explicit=parse_attendees(intent.raw_text))
        if _resolution.unresolved:
            return OperatorActionResult(
                ok=False, status="question",
                response_text=(
                    f"{_resolution.question} Nothing was scheduled or sent.".strip()
                ),
                details={"attendee_questions": [dict(row) for row in _resolution.unresolved]},
            )
        attendees = list(_resolution.addresses)
    except Exception:
        attendees = parse_attendees(intent.raw_text)
    if recurrence_rule and config.provider == "eventkit":
        return OperatorActionResult(
            ok=False, status="unavailable",
            response_text=("Repeating events are not created through Apple Calendar in this build (its EventKit "
                           "bridge is not packaged here). Propose a one-off event instead."),
            details={"reason": "recurrence_eventkit_unsupported"},
        )
    intent_uid = f"{uuid.uuid4()}@vool.local"
    action_id = create_pending_action_fn(
        session_id=session_id,
        task_id=task_id,
        action_kind="provider_calendar_event",
        scope={
            "calendar_id": calendar_id,
            "recurrence_rule": recurrence_rule,
            "attendees": attendees,
            "calendar_name": calendar_name,
            "provider": config.provider,
            "provider_url": config.base_url,
            "auth_binding": config.auth_binding,
            "intent_uid": intent_uid,
            "title": title,
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "tz_name": when.tz_name,
            "due_wall": when.due_wall,
            "duration_minutes": duration,
            "conflict_snapshot": [_event_brief(c) for c in overlapping],
            "note_path": note_path,
        },
    )
    audit_log_fn(
        "operator_action_propose_calendar_event",
        target_id=task_id,
        target_type="task",
        details={"action_id": action_id, "calendar_id": calendar_id, "title": title, "start_utc": start.isoformat()},
    )
    wall = when.due_wall or start.isoformat()
    return OperatorActionResult(
        ok=True,
        status="approval_required",
        response_text=(
            f"Event proposal ready (this creates a REAL event on calendar {calendar_name}):\n"
            f"- Title: {title}\n"
            f"- Starts: {wall} ({when.tz_name or 'as parsed'})\n"
            f"- Duration: {duration} minutes\n"
            + (f"- Repeats: {recurrence_words} (the whole series is created at once; later edits ask whether you mean one occurrence or the series)\n" if recurrence_rule else "")
            + "\n".join(_attendees_preview_lines(attendees, provider=config.provider)) + "\n\n"
            f"Reply with: approve calendar {action_id}"
        ),
        details={"action_id": action_id, "title": title, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                 "calendar_id": calendar_id, "tz_name": when.tz_name, "note_path": note_path},
    )


def _binding_mismatch(scope: dict[str, Any], config: CalendarProviderConfig) -> str | None:
    """The approval is bound to provider + ACCOUNT(PRINCIPAL) + calendar as reviewed.

    The account is the authenticated PRINCIPAL — the credential binding id, an opaque
    handle the credential authority already owns, never a token and never a URL: the
    real Google and Graph API base URLs are SHARED between every account, so switching
    the binding from account A to B at the same URL must still refuse. A runtime that
    LOST its binding (staged with one, configured without) also refuses: the approval
    named a principal the runtime can no longer prove it is.
    """
    if not config:
        return ""
    staged_provider = str(scope.get("provider") or "")
    staged_url = str(scope.get("provider_url") or "")
    staged_account = str(scope.get("auth_binding") or "")
    if staged_provider and staged_provider != config.provider:
        return (f"This approval was reviewed against provider `{staged_provider}`, but the runtime is now "
                f"configured for `{config.provider}`. Nothing was executed; re-propose against the current provider.")
    if staged_url and staged_url.rstrip("/") != config.base_url.rstrip("/"):
        return (f"This approval was reviewed against account `{staged_url}`, but the runtime is now connected to "
                f"`{config.base_url}`. Nothing was executed; re-propose against the current account.")
    if staged_account and staged_account != str(config.auth_binding or ""):
        return ("This approval was reviewed against a different connected account (credential binding) than the "
                "one now configured. Nothing was executed; re-propose against the current account.")
    return None


def _recovery_receipt(verified: CalEvent, *, recovered: bool) -> str:
    if not recovered:
        return ""
    return " (recovered: an earlier attempt for this exact event had an unproven outcome; it verifiably exists now, and nothing was duplicated)"


# ---------------------------------------------------------------------------
# The ONE execution/reconciliation lifecycle
# ---------------------------------------------------------------------------

#: Ownership of an approved operation lives in the approval store (core.operator.approvals):
#: an OS owner lock held by the one live worker plus a durable token/fence. Nothing in this
#: module remembers workers; absence from process memory is never evidence that a worker died.


def _canonical_approved_fields(scope: dict[str, Any]) -> dict[str, str]:
    """Every scheduled semantic field the user APPROVED, from the staged scope.

    Recovery certification compares the FULL approved event, not a title-and-start
    prefix: end/duration, timezone, all-day and the description are all part of what
    was reviewed, and an event that differs in any of them is a different effect.
    """
    def _norm(value: Any) -> str:
        return str(value or "").strip().replace("Z", "+00:00")

    return {
        "title": _norm(scope.get("title")),
        "start": _norm(scope.get("start_utc")),
        "end": _norm(scope.get("end_utc")),
        "tz": _norm(scope.get("tz_name")),
        "all_day": str(bool(scope.get("all_day") or False)).lower(),
        "description": _norm(scope.get("description")),
    }


def _canonical_of_event(event: CalEvent) -> dict[str, str]:
    def _norm(value: Any) -> str:
        return str(value or "").strip().replace("Z", "+00:00")

    return {
        "title": _norm(event.summary),
        "start": _norm(event.start_utc),
        "end": _norm(event.end_utc),
        "tz": _norm(event.tz_name),
        "all_day": str(bool(event.all_day)).lower(),
        "description": _norm(event.description),
    }


def _canonically_matches(event: CalEvent, fields: dict[str, str]) -> bool:
    """True only when the provider event IS the approved event, field for field."""
    actual = _canonical_of_event(event)
    differing = sorted(key for key in fields if fields[key] and actual.get(key) != fields[key])
    return not differing


def _differing_fields(event: CalEvent, fields: dict[str, str]) -> list[str]:
    actual = _canonical_of_event(event)
    return sorted(key for key in fields if fields[key] and actual.get(key) != fields[key])


def _pending_evidence(pending: dict[str, Any]) -> dict[str, Any]:
    """What this operation's durable record has already proven (phase, retained identity)."""
    from core.operator.approvals import operation_evidence

    return operation_evidence(pending)


def _mapped_provider_uid(adapter: Any, intent_uid: str) -> str:
    """The adapter's deterministic, client-chosen provider id for a durable intent ("" when the
    provider assigns ids itself). An adapter without the hook addresses the intent uid directly."""
    mapper = getattr(adapter, "durable_to_provider_id", None)
    if not callable(mapper):
        return str(intent_uid or "")
    try:
        return str(mapper(str(intent_uid or "")) or "")
    except Exception:
        return ""


def _resolve_provider_uid(adapter: Any, scope: dict[str, Any], pending: dict[str, Any]) -> tuple[str, str]:
    """The provider event id this operation maps to, and what that identity proves.

    ``(id, "mapped")``   the adapter's deterministic, client-chosen identity for the durable intent
                         (CalDAV UID, Google's id mapping): an exact read answering not_found proves
                         the effect absent.
    ``(id, "recorded")`` an id the provider assigned to this operation's accepted effect, retained
                         durably: its later absence means the effect was removed, never that it did
                         not happen.
    ``("", "")``         the provider assigns ids and none was retained.
    """
    intent_uid = str(scope.get("intent_uid") or "")
    mapped = _mapped_provider_uid(adapter, intent_uid)
    retained = str(_pending_evidence(pending).get("provider_uid") or "")
    if retained and retained != mapped:
        return retained, "recorded"
    if mapped:
        return mapped, "mapped"
    return "", ""


def _record_phase(action_id: str, phase: str, *, evidence: dict[str, Any] | None = None, note: str = "") -> bool | None:
    """Durably advance this attempt's evidence through the claim this process holds.

    None: no durable claim exists (the claim was injected rather than taken through the approval
    store), so there is nothing to fence. False: ownership was lost or the record could not be
    written -- before the effect boundary that means the attempt must not cross it.
    """
    from core.operator.approvals import held_claim

    claim = held_claim(action_id)
    if claim is None:
        return None
    try:
        return claim.record(phase=phase, evidence=evidence, note=note)
    except Exception:
        return False


def _write_refusal_proves_unsent(exc: CalendarRefusedError) -> bool:
    """A definitive client-error answer to the WRITE itself proves it was not applied.

    409/412 name an identity or version conflict (resolved separately); 408, 5xx and any other
    answer can follow an applied write and prove nothing. A refusal the adapter met BEFORE it sent
    anything (``before_send``: its read of the series or its version check) proves it outright."""
    if getattr(exc, "before_send", False):
        return True
    code = int(getattr(exc, "status_code", 0) or 0)
    if exc.reason in {"precondition_failed", "already_exists", "http_409"}:
        return False
    return 400 <= code < 500 and code not in {408, 409, 412}


def _approved_identity(scope: dict[str, Any]) -> dict[str, str]:
    return {"intent_uid": str(scope.get("intent_uid") or ""), "calendar_id": str(scope.get("calendar_id") or "")}


def _create_payload(adapter: Any, provider_name: str, scope: dict[str, Any]) -> CalEvent:
    calendar_id = str(scope.get("calendar_id") or "")
    return CalEvent(
        provider_id=getattr(adapter, "provider_id", provider_name),
        uid=str(scope.get("intent_uid") or ""),
        calendar_id=calendar_id,
        summary=str(scope.get("title") or ""),
        start_utc=datetime.fromisoformat(str(scope.get("start_utc"))).isoformat(),
        end_utc=datetime.fromisoformat(str(scope.get("end_utc"))).isoformat(),
        tz_name=str(scope.get("tz_name") or ""),
        description=str(scope.get("description") or ""),
        all_day=bool(scope.get("all_day") or False),
        recurrence_rule=str(scope.get("recurrence_rule") or ""),
        attendees=tuple(str(address) for address in (scope.get("attendees") or [])),
    )


def _rest_unproven(action_id: str, mark_outcome_unproven_fn: Any, result: dict[str, Any]) -> str:
    """Record that this attempt's effect MAY have happened (or was accepted but not verified).

    Earlier evidence is kept by the store (a verified effect stays verify-only). The outcome of
    the write is returned -- a failure to record is reported, never suppressed.
    """
    try:
        if mark_outcome_unproven_fn is not None:
            mark_outcome_unproven_fn(action_id, result=result)
        else:
            from core.operator.approvals import held_claim, mark_action_outcome_unproven

            claim = held_claim(action_id)
            mark_action_outcome_unproven(action_id, result=result,
                                         now_fn=claim.now_fn if claim else _utcnow_default,
                                         get_connection_fn=claim.get_connection_fn if claim else _connection_default)
        return "recorded"
    except Exception as exc:
        return f"unrecorded:{type(exc).__name__}"


def _rest_accepted(action_id: str, mark_outcome_unproven_fn: Any, scope: dict[str, Any], fields: dict[str, Any],
                   provider_uid: str, reason: str) -> OperatorActionResult:
    """The provider accepted the create but it could not be verified: retain the identity, never
    report it as never sent, and make every later approval verify-only."""
    public = {key: value for key, value in fields.items() if not str(key).startswith("_")}
    recorded = _rest_unproven(action_id, mark_outcome_unproven_fn,
                              {**_approved_identity(scope), **public, "provider_uid": provider_uid, "accepted": True, "reason": reason})
    identity = f" (provider id {provider_uid})" if provider_uid else ""
    return OperatorActionResult(
        ok=False,
        status="outcome_unproven",
        response_text=(
            f"The calendar provider accepted the event{identity}, but I could not read it back to verify it ({reason}). "
            "It was NOT sent again and it is NOT reported as never created. Ask me to check the event; approving again only verifies it."
        ),
        details={"action_id": action_id, "provider_uid": provider_uid, "accepted": True, "reason": reason, "recorded": recorded},
    )


def _durable_correlation(adapter: Any, intent_uid: str) -> str:
    """The provider-supported correlation token this operation's writes carry ("" when the provider
    offers none). Graph: the documented ``transactionId``. It proves which stored object belongs to
    this operation; matching content never does."""
    hook = getattr(adapter, "durable_correlation", None)
    if not callable(hook):
        return ""
    try:
        return str(hook(str(intent_uid or "")) or "")
    except Exception:
        return ""


def _discover_by_correlation(adapter: Any, calendar_id: str, fields: dict[str, Any], correlation: str) -> tuple[str, list[CalEvent]]:
    """Look for this operation's own object in the approved window.

    ("found", [event]) | ("absent", []) | ("ambiguous", events) | ("incomplete", []). A read that
    fails or is refused is INCOMPLETE, never absence; only an event carrying this operation's
    correlation token counts.
    """
    try:
        rows = adapter.events_in_range(calendar_id, start_utc=fields["start"], end_utc=fields["end"])
    except Exception:
        return "incomplete", []
    correlated = [row for row in rows if str(getattr(row, "correlation_id", "") or "") == correlation]
    if len(correlated) == 1:
        return "found", correlated
    if correlated:
        return "ambiguous", correlated
    return "absent", []


def _content_candidates(adapter: Any, calendar_id: str, fields: dict[str, Any]) -> tuple[list[CalEvent], bool]:
    """Events whose visible content equals the approved event: something to SHOW the user, never
    identity. The flag says whether the read completed."""
    try:
        rows = adapter.events_in_range(calendar_id, start_utc=fields["start"], end_utc=fields["end"])
    except Exception:
        return [], False
    return [row for row in rows if _canonically_matches(row, fields)], True


def _enter_attempt(
    intent: OperatorActionIntent,
    *,
    session_id: str,
    action_kind: str,
    pending_row: dict[str, Any] | None,
    load_pending_action_fn: Any,
    config: CalendarProviderConfig | None,
    claim_action_fn: Any,
    requeue_interrupted_fn: Any,
    unknown_result: Any,
    already_executed_result: Any,
) -> tuple[dict[str, Any] | None, OperatorActionResult | None]:
    """Everything that must hold BEFORE an approved operation's attempt may touch the provider.

    One entry for creates, updates and cancellations. Returns ``(row as claimed, None)`` or
    ``(None, typed answer)``. Ownership comes only from the approval store's claim; an
    ``executing`` row is recovered only when its owner lock proves the owner gone; the reviewed
    provider binding is re-checked before any state change; the row the attempt acts on is the
    one just claimed, entered from its claimed state.
    """
    pending = pending_row or load_pending_action_fn(session_id=session_id, action_kind=action_kind, action_id=intent.action_id)
    if pending is None:
        return None, unknown_result(session_id, intent.action_id)
    status = str(pending.get("status"))
    action_id = str(pending.get("action_id"))
    if status == "executed":
        return None, already_executed_result(pending)
    if status == "superseded":
        return None, OperatorActionResult(
            ok=False,
            status="superseded",
            response_text="That proposal was replaced by a newer one in this chat. Nothing was executed; approve the current proposal if you still want it.",
            details={"action_id": action_id},
        )
    if status == "executing":
        note = "owner lock was free: the previous worker exited mid-attempt; recovered by a later approval"
        try:
            if requeue_interrupted_fn is not None:
                recovered = requeue_interrupted_fn(action_id, entry_state="outcome_unproven", note=note)
            else:
                from core.operator.approvals import requeue_interrupted_action

                recovered = requeue_interrupted_action(action_id, entry_state="outcome_unproven", note=note, now_fn=_utcnow_default, get_connection_fn=_connection_default)
        except Exception as exc:
            return None, OperatorActionResult(
                ok=False,
                status="storage_unavailable",
                response_text=f"I could not safely inspect who owns this approval ({type(exc).__name__}). Nothing was executed; say 'check the event' to see the provider's actual state.",
                details={"action_id": action_id, "status": status},
            )
        if not recovered:
            return None, OperatorActionResult(
                ok=False,
                status="concurrent_approval",
                response_text="Another approval of this proposal is executing right now. Nothing was executed a second time; say 'check the event' to see the provider's actual state.",
                details={"action_id": action_id, "status": status},
            )
        status = recovered if isinstance(recovered, str) else "outcome_unproven"
        pending = dict(pending)
        pending["status"] = status
    if status not in {"pending_approval", "outcome_unproven", "effect_unrecorded"}:
        return None, OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="That proposal is not waiting for approval right now. Nothing was executed.",
            details={"action_id": action_id, "status": status},
        )
    scope = json.loads(str(pending.get("scope_json") or "{}"))
    mismatch = _binding_mismatch(scope, config) if config is not None else None
    if mismatch:
        return None, OperatorActionResult(ok=False, status="provider_changed", response_text=mismatch, details={"action_id": action_id})
    if claim_action_fn is not None:
        try:
            won = claim_action_fn(action_id)
        except Exception as exc:
            return None, OperatorActionResult(
                ok=False,
                status="storage_unavailable",
                response_text=f"I could not safely take ownership of this approval ({type(exc).__name__}). Nothing was executed.",
                details={"action_id": action_id},
            )
        if not won:
            row = load_action_any_state(session_id=session_id, action_kind=action_kind, action_id=action_id)
            if row is not None and str(row.get("status")) == "executed":
                return None, already_executed_result(row)
            return None, OperatorActionResult(
                ok=False,
                status="concurrent_approval",
                response_text="Another approval of this proposal is executing or was interrupted mid-flight. Nothing was executed a second time; say 'check the event' to see the provider's actual state.",
                details={"action_id": action_id, "status": (row or {}).get("status")},
            )
    from core.operator.approvals import held_claim

    claim = held_claim(action_id)
    if claim is not None:
        fresh = claim.load()
        if fresh is not None:
            pending = dict(fresh)
            pending["status"] = claim.entry_status
    return pending, None


def execute_proposed_event(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    adapter: Any,
    load_pending_action_fn: Any,
    mark_action_executed_fn: Any,
    audit_log_fn: Any,
    pending_row: dict[str, Any] | None = None,
    config: CalendarProviderConfig | None = None,
    claim_action_fn: Any = None,
    mark_outcome_unproven_fn: Any = None,
    mark_effect_unrecorded_fn: Any = None,
    requeue_interrupted_fn: Any = None,
) -> OperatorActionResult:
    """Execute an APPROVED proposal through ONE complete operation lifecycle.

    Ownership rules, enforced on every path including exceptions:

    * binding is re-verified BEFORE any state change (provider + account principal);
    * the atomic claim guards every effectful resume (pending, uncertain, unrecorded);
    * an `executing` row is recovered ONLY when its owner lock can be taken (the owner exited);
      it is requeued to where its recorded evidence puts it, never blindly replayed, and a live
      owner in any process is never displaced;
    * evidence is recorded on both sides of the effect boundary, and every exit without a
      receipt rests the operation where that evidence puts it;
    * a VERIFIED or ACCEPTED effect is verify-only: it is re-read, never re-created;
    * recovery identity comes from a retained id, a deterministic id or a provider-supported
      correlation -- never from matching content -- and a re-send of a proven-absent effect
      re-checks current availability first;
    * note linkage failure is an explicit partial outcome, never silent.
    """
    pending, early = _enter_attempt(
        intent, session_id=session_id, action_kind="provider_calendar_event", pending_row=pending_row,
        load_pending_action_fn=load_pending_action_fn, config=config, claim_action_fn=claim_action_fn,
        requeue_interrupted_fn=requeue_interrupted_fn,
        unknown_result=lambda sid, aid: _resolve_nonpending_create(sid, aid, adapter),
        already_executed_result=_already_executed_result,
    )
    if early is not None:
        return early
    from core.operator.approvals import relinquish_claim

    action_id = str(pending.get("action_id"))
    entry_state = str(pending.get("status"))
    scope = json.loads(str(pending.get("scope_json") or "{}"))
    try:
        if entry_state in {"outcome_unproven", "effect_unrecorded"}:
            return _reconcile_unproven_create(pending, adapter, task_id=task_id, session_id=session_id, config=config, mark_action_executed_fn=mark_action_executed_fn, mark_outcome_unproven_fn=mark_outcome_unproven_fn, mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)
        return _execute_pending_create(pending, adapter, scope, action_id, task_id=task_id, session_id=session_id, config=config, mark_action_executed_fn=mark_action_executed_fn, mark_outcome_unproven_fn=mark_outcome_unproven_fn, mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)
    finally:
        relinquish_claim(action_id)


def _release_claim(claim_action_fn: Any, mark_outcome_unproven_fn: Any, action_id: str, entry_state: str, *, note: str,
                   proven_not_applied: bool = False, evidence: dict[str, Any] | None = None) -> str:
    """End a claimed attempt WITHOUT a terminal outcome, through the claim that owns it.

    The resting state is decided by durable evidence (core.operator.approvals.OperationClaim.
    release); ``entry_state`` is only a floor and can never lower it. Earlier identities and
    evidence are merged, never replaced. The outcome is RETURNED, not suppressed: "" when no
    durable claim exists (an injected claim that never reached the approval store), the resting
    status on success, or ``unrecorded:<error>`` when the release could not be persisted -- the
    row then stays ``executing`` with its evidence and is recovered once the owner lock is free.
    """
    del claim_action_fn, mark_outcome_unproven_fn
    from core.operator.approvals import held_claim

    claim = held_claim(action_id)
    if claim is None:
        return ""
    try:
        return claim.release(note=note, floor_status=entry_state, proven_not_applied=proven_not_applied, evidence=evidence)
    except Exception as exc:
        return f"unrecorded:{type(exc).__name__}"


def _utcnow_default() -> str:
    from datetime import datetime as _dt

    return _dt.now(timezone.utc).isoformat(timespec="seconds")


def _connection_default():
    from storage.db import get_connection

    return get_connection()


def _verified_receipt(provider: str, calendar_id: str, verified: CalEvent, note_path: str, intent_uid: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "calendar_id": calendar_id,
        "uid": verified.uid,
        "intent_uid": intent_uid,
        "etag": verified.etag,
        "href": verified.href,
        "title": verified.summary,
        "start_utc": verified.start_utc,
        "end_utc": verified.end_utc,
        "note_path": note_path,
        # The invitation effect is part of the reviewed action, so the receipt restates it:
        # these addresses were emailed an invitation by the provider's own create behavior.
        "attendees": list(verified.attendees),
    }


def _mark_receipt(mark_action_executed_fn: Any, mark_effect_unrecorded_fn: Any, action_id: str, receipt: dict[str, Any]) -> str:
    """Persist the receipt; report exactly which truth landed.

    'executed'; 'effect_unrecorded' (verified but the receipt could not be written); 'not_owned: ...'
    (the store no longer records this worker's claim -- nothing was written here); or
    'unrecordable: ...' when even the unrecorded marker failed (the effect still exists).
    """
    from core.operator.approvals import OperationOwnershipError, OperationStateError

    try:
        mark_action_executed_fn(action_id, result=receipt)
        return "executed"
    except (OperationOwnershipError, OperationStateError) as exc:
        return f"not_owned: {exc}"
    except Exception as exec_error:
        try:
            if mark_effect_unrecorded_fn is not None:
                mark_effect_unrecorded_fn(action_id, result={**receipt, "receipt_error": str(exec_error)[:200]})
            else:
                from core.operator.approvals import held_claim, mark_action_effect_unrecorded

                claim = held_claim(action_id)
                mark_action_effect_unrecorded(action_id, result={**receipt, "receipt_error": str(exec_error)[:200]},
                                              now_fn=claim.now_fn if claim else _utcnow_default,
                                              get_connection_fn=claim.get_connection_fn if claim else _connection_default)
            return "effect_unrecorded"
        except Exception:
            return f"unrecordable: {exec_error}"


def _link_note(note_path: str, uid: str, action_id: str) -> bool:
    if not note_path:
        return True
    try:
        from core.operator.notes import link_note_to_event

        return link_note_to_event(note_path, event_uid=uid, action_id=action_id)
    except Exception:
        return False


def _execute_pending_create(
    pending: dict[str, Any],
    adapter: Any,
    scope: dict[str, Any],
    action_id: str,
    *,
    task_id: str,
    session_id: str,
    config: CalendarProviderConfig | None,
    mark_action_executed_fn: Any,
    mark_outcome_unproven_fn: Any,
    mark_effect_unrecorded_fn: Any,
    audit_log_fn: Any,
    claim_action_fn: Any,
) -> OperatorActionResult:
    calendar_id = str(scope.get("calendar_id") or "")
    start = datetime.fromisoformat(str(scope.get("start_utc")))
    end = datetime.fromisoformat(str(scope.get("end_utc")))
    tz_name = str(scope.get("tz_name") or "")
    provider_name = (config.provider if config is not None else "") or getattr(adapter, "provider_id", "") or "caldav"
    fields = _canonical_approved_fields(scope)
    entry = "pending_approval"
    # Current availability, checked BEFORE anything crosses the effect boundary: whatever happens
    # to this read, the operation is still known-unsent.
    try:
        overlapping = adapter.events_in_range(calendar_id, start_utc=start.isoformat(), end_utc=end.isoformat())
        conflicts = _find_conflicts(overlapping, start, end)
    except CalendarRefusedError as exc:
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"availability read refused before sending: {exc.reason}", proven_not_applied=True)
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except TransportDeniedError as exc:
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"refused before sending: {exc.reason}", proven_not_applied=True)
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"availability read failed before sending: {reason}", proven_not_applied=True)
        return OperatorActionResult(
            ok=False,
            status="provider_unreachable",
            response_text=("The calendar provider did not answer the availability check, so the event was NOT sent. "
                           "The approval is still waiting; approve again once the provider is reachable."),
            details={"reason": reason, "action_id": action_id, "resting_status": rested},
        )
    try:
        other_conflicts, unreadable = _other_selected_conflicts(config, calendar_id, start, end)
    except SelectionsUnavailableError as exc:
        # The chosen-calendar SET is unreadable (corrupt/locked store): unknown selections are
        # not "no selections", and no effect is sent over an unknown set.
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry,
                                note="selections store unreadable before sending", proven_not_applied=True)
        return OperatorActionResult(
            ok=False, status="storage_unavailable",
            response_text=(f"I could not read which calendars are chosen for sync ({exc}), so I cannot prove the "
                           "time is free across your calendars. The event was NOT sent; the approval is still waiting."),
            details={"reason": "selections_unavailable", "action_id": action_id, "resting_status": rested},
        )
    if unreadable:
        # Availability cannot be proven over a chosen calendar that will not answer: the effect
        # is NOT sent, the refusal names the source, and the approval rests unchanged.
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry,
                                note="a selected calendar could not be read before sending", proven_not_applied=True)
        return OperatorActionResult(
            ok=False, status="provider_unreachable",
            response_text=(f"One of your chosen calendars could not be read ({_unreadable_sources_text(unreadable)}), so I "
                           "cannot prove the time is free across your calendars. The event was NOT sent; the approval is "
                           "still waiting — approve again once it answers."),
            details={"unreadable_sources": unreadable, "action_id": action_id, "resting_status": rested},
        )
    conflicts = conflicts + other_conflicts
    if conflicts:
        from core.operator.calendar_agenda import selected_sources

        calendar_names = {source.calendar_id: source.calendar_name for source in selected_sources()}
        lines = ["The situation changed since the preview — that time is now taken:"]
        for conflict in conflicts:
            where = calendar_names.get(conflict.calendar_id)
            attribution = f" — {where}" if where and conflict.calendar_id != calendar_id else ""
            lines.append(_format_event_line(conflict, tz_name) + attribution)
        lines.append("")
        lines.append("Nothing was created. Check the day again or propose a different time.")
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="conflict at execution; nothing was sent", proven_not_applied=True)
        return OperatorActionResult(ok=False, status="conflict", response_text="\n".join(lines), details={"conflicts": [_event_brief(c) for c in conflicts], "action_id": action_id, "resting_status": rested})
    return _dispatch_create(pending, adapter, scope, fields, action_id, entry=entry, task_id=task_id, session_id=session_id, provider_name=provider_name,
                            mark_action_executed_fn=mark_action_executed_fn, mark_outcome_unproven_fn=mark_outcome_unproven_fn,
                            mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)


def _dispatch_create(
    pending: dict[str, Any],
    adapter: Any,
    scope: dict[str, Any],
    fields: dict[str, Any],
    action_id: str,
    *,
    entry: str,
    task_id: str,
    session_id: str,
    provider_name: str,
    mark_action_executed_fn: Any,
    mark_outcome_unproven_fn: Any,
    mark_effect_unrecorded_fn: Any,
    audit_log_fn: Any,
    claim_action_fn: Any,
) -> OperatorActionResult:
    """Cross the effect boundary ONCE for this attempt, with evidence recorded on both sides.

    Before: the attempt is durably marked dispatching, and a worker that cannot record that (lost
    ownership, storage down) does not send. After: an accepted create's provider id is retained at
    once, and every later failure -- a refused, unanswered or undecodable read-back -- rests the
    operation as accepted-unverified, never as never-sent. Only a definitive refusal of the write
    itself returns it to its entry state.
    """
    calendar_id = str(scope.get("calendar_id") or "")
    intent_uid = str(scope.get("intent_uid") or "")
    identity = _approved_identity(scope)
    public = {key: value for key, value in fields.items() if not str(key).startswith("_")}
    context = dict(task_id=task_id, session_id=session_id, provider_name=provider_name, mark_action_executed_fn=mark_action_executed_fn,
                   mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)
    if _record_phase(action_id, PHASE_DISPATCHING, evidence=identity, note="sending the create") is False:
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="the attempt could not be recorded before sending; nothing was sent", proven_not_applied=True)
        return OperatorActionResult(
            ok=False,
            status="ownership_lost",
            response_text=("I could not safely record this attempt before sending it — another worker may own this approval, or the store "
                           "is unavailable. Nothing was sent; say 'check the event' to see the provider's actual state."),
            details={"action_id": action_id, "resting_status": rested},
        )
    try:
        created = adapter.create_event(calendar_id, _create_payload(adapter, provider_name, scope))
    except CalendarWriteAcceptedError as exc:
        _record_phase(action_id, PHASE_ACCEPTED, evidence={**identity, "provider_uid": exc.provider_uid, "accepted": True}, note=f"create accepted; read-back failed: {exc.reason}")
        return _rest_accepted(action_id, mark_outcome_unproven_fn, scope, fields, exc.provider_uid, exc.reason)
    except CalendarRefusedError as exc:
        if exc.reason in {"precondition_failed", "already_exists", "http_409"}:
            return _resolve_identity_conflict(pending, adapter, scope, fields, action_id, entry=entry, reason=exc.reason, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)
        if _write_refusal_proves_unsent(exc):
            rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"create refused: {exc.reason}", proven_not_applied=True)
            return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, **public, "reason": exc.reason})
        return OperatorActionResult(
            ok=False,
            status="outcome_unproven",
            response_text=(f"The calendar provider answered the create with {exc.reason}, which does not prove whether the event was stored. "
                           "It was NOT sent again and I am NOT claiming success or failure. Ask me to check the event before doing anything else."),
            details={"reason": exc.reason, "action_id": action_id, "recorded": recorded},
        )
    except TransportDeniedError as exc:
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"refused before any socket: {exc.reason}", proven_not_applied=True)
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except TransportUnknownError as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, **public, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason, "action_id": action_id, "intent_uid": intent_uid, "recorded": recorded})
    except Exception as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, **public, "reason": f"unexpected_{type(exc).__name__}"})
        return OperatorActionResult(
            ok=False,
            status="outcome_unproven",
            response_text=(f"The create request failed unexpectedly after it was sent ({type(exc).__name__}); whether the event was stored is "
                           "unproven. It was NOT retried; ask me to check the event."),
            details={"reason": type(exc).__name__, "action_id": action_id, "recorded": recorded},
        )
    provider_uid = str(getattr(created, "uid", "") or "")
    _record_phase(action_id, PHASE_ACCEPTED, evidence={**identity, "provider_uid": provider_uid, "accepted": True}, note="provider accepted the create")
    if not provider_uid:
        return _rest_accepted(action_id, mark_outcome_unproven_fn, scope, fields, "", "accepted_without_identity")
    try:
        verified = adapter.get_event(calendar_id, provider_uid)
    except Exception as exc:
        return _rest_accepted(action_id, mark_outcome_unproven_fn, scope, fields, provider_uid,
                              f"verification read {getattr(exc, 'reason', '') or type(exc).__name__}")
    if not _canonically_matches(verified, fields):
        differing = _differing_fields(verified, fields)
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "provider_uid": provider_uid, "accepted": True, "identity_mismatch": differing})
        return OperatorActionResult(ok=False, status="identity_mismatch", response_text=f"The provider stored a different event than approved (differing: {', '.join(differing)}). It was not sent again and nothing further was changed; inspect it.", details={"action_id": action_id, "differing": differing, "provider_uid": provider_uid, "recorded": recorded})
    receipt_fields = dict(fields)
    receipt_fields["_note_path"] = str(scope.get("note_path") or "")
    receipt_fields["_intent_uid"] = intent_uid
    return _finalize_verified(pending, adapter, verified, receipt_fields, action_id=action_id, recovered=False, **context)


def _resolve_identity_conflict(
    pending: dict[str, Any],
    adapter: Any,
    scope: dict[str, Any],
    fields: dict[str, Any],
    action_id: str,
    *,
    entry: str,
    reason: str,
    mark_outcome_unproven_fn: Any,
    **context: Any,
) -> OperatorActionResult:
    """The provider says this operation's durable identity already exists: read THAT identity and
    verify it -- never create a second, never pick an event by content."""
    del entry
    calendar_id = str(scope.get("calendar_id") or "")
    identity = _approved_identity(scope)
    mapped = _mapped_provider_uid(adapter, identity["intent_uid"])
    if not mapped:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "reason": reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text="The provider reports this proposal's identity already exists, but it assigns event ids itself, so that event cannot be read directly. Nothing was sent again; ask me to check the event.", details={"action_id": action_id, "reason": reason, "recorded": recorded})
    try:
        existing = adapter.get_event(calendar_id, mapped)
    except Exception as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "reason": f"{reason}; identity read {getattr(exc, 'reason', '') or type(exc).__name__}"})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text="The provider reports this proposal's identity already exists, but reading it back failed. Nothing was sent again and I am not claiming success or failure; ask me to check the event.", details={"action_id": action_id, "reason": reason, "recorded": recorded})
    if _canonically_matches(existing, fields):
        receipt_fields = dict(fields)
        receipt_fields["_note_path"] = str(scope.get("note_path") or "")
        receipt_fields["_intent_uid"] = identity["intent_uid"]
        return _finalize_verified(pending, adapter, existing, receipt_fields, action_id=action_id, recovered=True, **context)
    differing = _differing_fields(existing, fields)
    recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "provider_uid": mapped, "accepted": True, "identity_mismatch": differing})
    return OperatorActionResult(
        ok=False,
        status="conflict",
        response_text=("An event with this proposal's exact identity already exists on the provider but does not match what was approved. Nothing was created or overwritten; inspect it and propose afresh."),
        details={"action_id": action_id, "reason": reason, "differing": differing, "recorded": recorded},
    )




def _finalize_verified(
    pending: dict[str, Any],
    adapter: Any,
    verified: CalEvent,
    fields: dict[str, Any],
    *,
    action_id: str,
    task_id: str,
    session_id: str,
    provider_name: str,
    mark_action_executed_fn: Any,
    mark_effect_unrecorded_fn: Any,
    audit_log_fn: Any,
    claim_action_fn: Any,
    recovered: bool,
) -> OperatorActionResult:
    del pending, session_id, claim_action_fn
    note_path = str(fields.get("_note_path") or "")
    linked = _link_note(note_path, verified.uid, action_id)
    receipt = _verified_receipt(provider_name, verified.calendar_id or "", verified, note_path, intent_uid=str(fields.get("_intent_uid") or ""))
    outcome = _mark_receipt(mark_action_executed_fn, mark_effect_unrecorded_fn, action_id, receipt)
    audit_log_fn("operator_action_create_calendar_event", target_id=task_id, target_type="task", details={"action_id": action_id, "uid": verified.uid, "verified": True, "recovered": recovered, "receipt_outcome": outcome})
    response = (
        f"Event created on the provider and verified: '{verified.summary}' at {verified.start_utc}"
        + (f" ({verified.tz_name})" if verified.tz_name else "")
        + f". Identity uid {verified.uid} (version {verified.etag or 'unknown'})."
        + _recovery_receipt(verified, recovered=recovered)
        + (f" Linked note: {note_path}" if note_path and linked else "")
        + (f" Invitation requests for {', '.join(verified.attendees)} were accepted by the provider with its guest-notification policy in force."
           + " That proves the REQUEST, not each guest's delivery or attendance -- their responses stay theirs."
           if verified.attendees else "")
    )
    details: dict[str, Any] = {"action_id": action_id, "uid": verified.uid, "attendees": list(verified.attendees), "etag": verified.etag, "href": verified.href, "calendar_id": verified.calendar_id, "start_utc": verified.start_utc, "end_utc": verified.end_utc, "verified": True, "recovered": recovered}
    if not linked:
        response += (" PARTIAL: the event is created and verified, but its note link could not be written — "
                     "the note exists without the event reference. Nothing failed silently; relink it by asking me to link them.")
        details["note_link_failed"] = True
        details["destination_partial"] = True
    if outcome == "effect_unrecorded":
        response += (" NOTE: the event verifiably exists on the provider, but its receipt could not be persisted — "
                     "the approval remains resumable through verification only; nothing will be re-created.")
        details["receipt_outcome"] = outcome
    elif outcome.startswith("not_owned"):
        response += (" NOTE: the event verifiably exists, but this approval's record is no longer owned by this attempt "
                     "(another worker completed or recovered it), so no receipt was written here and nothing was created twice.")
        details["receipt_outcome"] = outcome
    elif outcome.startswith("unrecordable"):
        response += (" WARNING: the event verifiably exists on the provider, but NO receipt state could be persisted. "
                     "Ask me to check the event before any further change; nothing will be re-created here.")
        details["receipt_outcome"] = outcome
    return OperatorActionResult(ok=True, status="executed", response_text=response, details=details)


def _reconcile_unproven_create(
    pending: dict[str, Any],
    adapter: Any,
    *,
    task_id: str,
    session_id: str,
    config: CalendarProviderConfig | None,
    mark_action_executed_fn: Any,
    mark_outcome_unproven_fn: Any,
    mark_effect_unrecorded_fn: Any,
    audit_log_fn: Any,
    claim_action_fn: Any,
) -> OperatorActionResult:
    """Verify-then-reconcile an operation whose earlier attempt ended without a receipt.

    Evidence decides, in order. The identity this operation maps to is read (a retained provider
    id first, else the adapter's deterministic id). A VERIFY-ONLY operation -- verified but
    unrecorded, or accepted by the provider -- is only ever read: found and matching, it is
    recorded; gone, it is reported gone and never re-created under this approval. An operation
    whose exact deterministic identity reads not_found is proven absent and may be sent once more,
    after its original constraints are re-checked against the calendar's current state. A read
    that fails proves nothing and changes nothing.
    """
    scope = json.loads(str(pending.get("scope_json") or "{}"))
    provider_name = (config.provider if config is not None else "") or getattr(adapter, "provider_id", "") or "caldav"
    calendar_id = str(scope.get("calendar_id") or "")
    intent_uid = str(scope.get("intent_uid") or "")
    action_id = str(pending.get("action_id"))
    entry = str(pending.get("status") or "outcome_unproven")
    if entry not in {"outcome_unproven", "effect_unrecorded"}:
        entry = "outcome_unproven"
    fields = _canonical_approved_fields(scope)
    context = dict(task_id=task_id, session_id=session_id, provider_name=provider_name, mark_action_executed_fn=mark_action_executed_fn,
                   mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)
    if not intent_uid:
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="no durable identity; cannot reconcile")
        return OperatorActionResult(ok=False, status="invalid_request", response_text="That proposal predates durable identity minting and cannot be reconciled safely. Nothing was executed; propose it again.", details={"action_id": action_id, "resting_status": rested})
    evidence = _pending_evidence(pending)
    provider_uid, basis = _resolve_provider_uid(adapter, scope, pending)
    verify_only = (entry == "effect_unrecorded" or basis == "recorded" or bool(evidence.get("accepted"))
                   or phase_rank(evidence.get("phase")) >= phase_rank(PHASE_ACCEPTED))
    if provider_uid:
        try:
            existing = adapter.get_event(calendar_id, provider_uid)
        except CalendarRefusedError as exc:
            if exc.reason != "not_found":
                return _unverifiable(action_id, entry, exc.reason, claim_action_fn, mark_outcome_unproven_fn, refusal=exc)
            existing = None
        except Exception as exc:
            return _unverifiable(action_id, entry, str(getattr(exc, "reason", "") or type(exc).__name__), claim_action_fn, mark_outcome_unproven_fn)
        if existing is None:
            if verify_only:
                return _proven_effect_missing(action_id, entry, provider_uid, claim_action_fn, mark_outcome_unproven_fn)
            return _refire_after_proven_absence(pending, adapter, scope, fields, action_id, entry=entry, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)
        return _certify_existing(pending, adapter, existing, scope, fields, action_id, entry=entry, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)
    # No identity to read: the provider assigns ids and none was retained. Identity can then only
    # come from a provider-supported correlation carried by this operation's own writes.
    correlation = _durable_correlation(adapter, intent_uid)
    if correlation:
        outcome, events = _discover_by_correlation(adapter, calendar_id, fields, correlation)
        if outcome == "found":
            return _certify_existing(pending, adapter, events[0], scope, fields, action_id, entry=entry, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)
        if outcome == "ambiguous":
            rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"{len(events)} events carry this operation's correlation")
            return OperatorActionResult(
                ok=False,
                status="identity_ambiguous",
                response_text=(f"{len(events)} events on the provider carry this proposal's identity token, so I cannot tell which one the "
                               "earlier attempt created. Nothing was sent or recorded; inspect the calendar."),
                details={"action_id": action_id, "candidates": [_event_brief(event) for event in events[:5]], "resting_status": rested},
            )
        if outcome == "incomplete":
            return _unverifiable(action_id, entry, "correlation discovery read failed", claim_action_fn, mark_outcome_unproven_fn)
        if verify_only:
            return _proven_effect_missing(action_id, entry, "", claim_action_fn, mark_outcome_unproven_fn)
        return _refire_after_proven_absence(pending, adapter, scope, fields, action_id, entry=entry, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)
    candidates, complete = _content_candidates(adapter, calendar_id, fields)
    rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="no retained id or provider correlation; content is not identity")
    if candidates:
        text = (f"I cannot prove which event, if any, the earlier attempt created: the provider assigns event ids, none was retained, and "
                f"it offers no correlation token here. {len(candidates)} event(s) have exactly the approved content, but identical content is "
                "not proof that this approval created them. Nothing was sent or recorded; inspect the calendar.")
    elif complete:
        text = ("I cannot prove whether the earlier attempt created the event: the provider assigns event ids, none was retained, and it "
                "offers no correlation token here. Nothing was sent again; inspect the calendar before approving anything new.")
    else:
        text = ("I could not read the calendar to look for the earlier attempt, and without a retained id or correlation token its outcome "
                "cannot be established. Nothing was sent again.")
    return OperatorActionResult(ok=False, status="outcome_unproven", response_text=text,
                                details={"action_id": action_id, "candidates": [_event_brief(event) for event in candidates[:5]],
                                         "discovery_complete": complete, "resting_status": rested})


def _unverifiable(action_id: str, entry: str, reason: str, claim_action_fn: Any, mark_outcome_unproven_fn: Any, *,
                  refusal: CalendarRefusedError | None = None) -> OperatorActionResult:
    rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"verification read failed: {reason}")
    prefix = (_refusal_text(refusal) + " ") if refusal is not None else ""
    return OperatorActionResult(
        ok=False,
        status="provider_refused" if refusal is not None else "outcome_unproven",
        response_text=prefix + "The earlier attempt's outcome remains unproven; nothing was sent again.",
        details={"reason": reason, "action_id": action_id, "resting_status": rested},
    )


def _proven_effect_missing(action_id: str, entry: str, provider_uid: str, claim_action_fn: Any, mark_outcome_unproven_fn: Any) -> OperatorActionResult:
    """A previously accepted or verified effect is no longer on the provider: say so, keep the
    operation verify-only, and never resurrect it under the old approval."""
    rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry,
                            note="the proven effect is no longer on the provider; not re-created",
                            evidence={"last_verification": "absent", "provider_uid": provider_uid})
    identity = f" (provider id {provider_uid})" if provider_uid else ""
    return OperatorActionResult(
        ok=False,
        status="effect_missing",
        response_text=(f"This approval's event was already accepted by the provider earlier{identity}, but the provider no longer has it — "
                       "it was removed or moved. I did NOT re-create it under the old approval; if you still want it, ask me to propose it again."),
        details={"action_id": action_id, "provider_uid": provider_uid, "resting_status": rested},
    )


def _certify_existing(pending: dict[str, Any], adapter: Any, existing: CalEvent, scope: dict[str, Any], fields: dict[str, Any], action_id: str, *,
                      entry: str, mark_outcome_unproven_fn: Any, **context: Any) -> OperatorActionResult:
    if _canonically_matches(existing, fields):
        receipt_fields = dict(fields)
        receipt_fields["_note_path"] = str(scope.get("note_path") or "")
        receipt_fields["_intent_uid"] = str(scope.get("intent_uid") or "")
        return _finalize_verified(pending, adapter, existing, receipt_fields, action_id=action_id, recovered=True, **context)
    differing = _differing_fields(existing, fields)
    rested = _release_claim(context.get("claim_action_fn"), mark_outcome_unproven_fn, action_id, entry,
                            note=f"recovered event mismatched fields {differing}",
                            evidence={"identity_mismatch": differing, "provider_uid": existing.uid, "accepted": True})
    return OperatorActionResult(
        ok=False,
        status="identity_mismatch",
        response_text=(f"An event with this proposal's exact identity exists on the provider but its content "
                       f"differs from what was approved (differing: {', '.join(differing)}). Nothing was "
                       "created, overwritten or claimed; inspect the event and propose afresh."),
        details={"action_id": action_id, "existing": _event_brief(existing), "differing": differing, "resting_status": rested},
    )


def _refire_after_proven_absence(pending: dict[str, Any], adapter: Any, scope: dict[str, Any], fields: dict[str, Any], action_id: str, *,
                                 entry: str, mark_outcome_unproven_fn: Any, **context: Any) -> OperatorActionResult:
    """The earlier attempt is proven absent: re-check the original constraints against the
    calendar's CURRENT state (excluding a canonical self-match), then send once."""
    claim_action_fn = context.get("claim_action_fn")
    calendar_id = str(scope.get("calendar_id") or "")
    start = datetime.fromisoformat(fields["start"])
    end = datetime.fromisoformat(fields["end"])
    tz_name = str(scope.get("tz_name") or "")
    # Only this operation's OWN identity is exempt from the conflict check. An event from elsewhere
    # with identical content is a real occupant of the slot, not this approval's effect.
    own_uid = _mapped_provider_uid(adapter, str(scope.get("intent_uid") or ""))
    correlation = _durable_correlation(adapter, str(scope.get("intent_uid") or ""))
    try:
        overlapping = adapter.events_in_range(calendar_id, start_utc=start.isoformat(), end_utc=end.isoformat())
        others = [row for row in overlapping
                  if not ((own_uid and row.uid == own_uid) or (correlation and str(getattr(row, "correlation_id", "") or "") == correlation))]
        conflicts = _find_conflicts(others, start, end)
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=f"re-check read failed: {reason}")
        prefix = (_refusal_text(exc) + " ") if isinstance(exc, CalendarRefusedError) else ""
        return OperatorActionResult(
            ok=False,
            status="provider_refused" if isinstance(exc, CalendarRefusedError) else "outcome_unproven",
            response_text=prefix + "The earlier attempt never landed, but the calendar's current state could not be re-checked, so nothing was sent.",
            details={"reason": reason, "action_id": action_id, "resting_status": rested},
        )
    if conflicts:
        lines = ["The earlier attempt never landed, and that time is now taken:"]
        for conflict in conflicts:
            lines.append(_format_event_line(conflict, tz_name))
        lines.append("")
        lines.append("Nothing was created. Check the day again or propose a different time.")
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="effect proven absent; the slot is now taken", proven_not_applied=True)
        return OperatorActionResult(ok=False, status="conflict", response_text="\n".join(lines), details={"conflicts": [_event_brief(c) for c in conflicts], "action_id": action_id, "resting_status": rested})
    return _dispatch_create(pending, adapter, scope, fields, action_id, entry=entry, mark_outcome_unproven_fn=mark_outcome_unproven_fn, **context)


def _resolve_nonpending_create(session_id: str, action_id: str | None, adapter: Any) -> OperatorActionResult:
    """An approval id with no PENDING row: already used, interrupted, or unknown."""
    row = load_action_any_state(session_id=session_id, action_kind="provider_calendar_event", action_id=action_id)
    if row is None:
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="I don't have a pending event proposal approval matching that in this chat. Nothing was executed.",
            details={"action_id": action_id},
        )
    status = str(row.get("status"))
    if status == "executed":
        return _already_executed_result(row)
    if status in {"outcome_unproven", "effect_unrecorded"}:
        saved = json.loads(str(row.get("result_json") or "{}"))
        return OperatorActionResult(
            ok=False,
            status="outcome_unproven",
            response_text="That proposal's earlier attempt had an unproven outcome. Say 'approve calendar <id>' again to reconcile it: I will verify the provider's actual state and recover or re-fire the exact same event — never a duplicate.",
            details={"action_id": str(row.get("action_id")), "receipt": saved},
        )
    if status == "executing":
        return OperatorActionResult(
            ok=False,
            status="concurrent_approval",
            response_text="That proposal's execution was interrupted mid-flight or is running elsewhere. Nothing was executed a second time; say 'check the event' to see the provider's actual state.",
            details={"action_id": str(row.get("action_id"))},
        )
    return OperatorActionResult(
        ok=False,
        status="invalid_request",
        response_text="That proposal is not waiting for approval right now. Nothing was executed.",
        details={"action_id": action_id, "status": status},
    )


def _already_executed_result(pending: dict[str, Any]) -> OperatorActionResult:
    saved = json.loads(str(pending.get("result_json") or "{}"))
    return OperatorActionResult(
        ok=False,
        status="already_executed",
        response_text=(
            f"That approval was already used: the event '{saved.get('title')}' exists on the provider "
            f"(uid {str(saved.get('uid') or '')[:8]}). Nothing was created a second time."
        ),
        details={"action_id": str(pending.get("action_id")), "receipt": saved},
    )


def load_action_any_state(*, session_id: str, action_kind: str, action_id: str | None, get_connection_fn: Any = None) -> dict[str, Any] | None:
    """Load an operator action row regardless of status (duplicate-approval diagnosis)."""
    from storage.db import get_connection

    conn = (get_connection_fn or get_connection)()
    try:
        if action_id:
            row = conn.execute(
                "SELECT * FROM operator_action_requests WHERE action_id = ? AND session_id = ? AND action_kind = ?",
                (action_id, session_id, action_kind),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM operator_action_requests WHERE session_id = ? AND action_kind = ? ORDER BY created_at DESC LIMIT 1",
                (session_id, action_kind),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _unknown_or_consumed_approval(session_id: str, action_id: str | None, what: str) -> OperatorActionResult:
    row = load_action_any_state(session_id=session_id, action_kind="provider_calendar_event", action_id=action_id)
    if row is not None and str(row.get("status")) == "executed":
        return _already_executed_result(row)
    return OperatorActionResult(
        ok=False,
        status="invalid_request",
        response_text=f"I don't have a pending {what} approval matching that in this chat. Nothing was executed.",
        details={"action_id": action_id},
    )


def resolve_owned_provider_event(
    *,
    session_id: str,
    action_id: str | None = None,
    label: str | None = None,
    get_connection_fn: Any = None,
) -> dict[str, Any] | None:
    """Resolve exactly ONE provider event this session owns, at its LATEST version.

    The event's current identity is the newest executed receipt for its uid — the create
    receipt, or the update receipt that superseded it — so a user who saw an event at
    version 3 is never silently answered with version 1. A cancel receipt removes the
    event from the owned set. Foreign sessions never match; several matches without a
    precise target are a typed ambiguity, never a guess by creation order.
    """
    from storage.db import get_connection

    conn = (get_connection_fn or get_connection)()
    try:
        rows = conn.execute(
            "SELECT action_id, action_kind, created_at, result_json FROM operator_action_requests "
            "WHERE session_id = ? AND action_kind IN ('provider_calendar_event', 'provider_calendar_update') "
            "AND status = 'executed' AND (? IS NULL OR action_id LIKE ?) ORDER BY created_at DESC LIMIT 401",
            (session_id, action_id, (action_id or "") + "%"),
        ).fetchall()
        cancelled = {
            str(json.loads(str(row["result_json"] or "{}")).get("uid") or "")
            for row in conn.execute(
                "SELECT result_json FROM operator_action_requests "
                "WHERE session_id = ? AND action_kind = 'provider_calendar_cancel' AND status = 'executed'",
                (session_id,),
            ).fetchall()
        }
    finally:
        conn.close()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            result = json.loads(str(row["result_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        uid = str(result.get("uid") or "")
        if not uid or uid in cancelled or uid in latest:
            continue
        latest[uid] = {"action_id": str(row["action_id"]), "created_at": str(row["created_at"] or ""), **{key: str(result.get(key) or "") for key in ("uid", "etag", "href", "title", "start_utc", "end_utc", "calendar_id", "note_path")}}
    if label:
        wanted = str(label).strip().casefold()
        matches = [record for record in latest.values() if record["title"].casefold() == wanted or record["uid"].startswith(wanted)]
    else:
        matches = list(latest.values())
    if action_id and not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if not matches:
        return None
    return {
        "selection_error": "Choose one provider event by its exact title or uid: "
        + ", ".join(f"{record['title']} (uid {record['uid'][:8]})" for record in matches[:6])
    }


def _resolve_update_target(
    *,
    intent: OperatorActionIntent,
    session_id: str,
    resolve_owned_event_fn: Any,
    resolve_by_title_fn: Any,
    label: str | None,
) -> dict[str, Any] | None:
    """The event an update/cancel targets: this session's receipt first; after a restart
    (a new session, same provider) an EXACT title match on the provider, resolved with its
    current etag — the version inspect just showed. Foreign-anything is still refused:
    no receipt and no unique provider title is a typed clarification, never a guess."""
    record = resolve_owned_event_fn(session_id=session_id, label=label or intent.target_label)
    if record is not None and not record.get("selection_error"):
        return _named_occurrence_of_owned_series(record, intent=intent, label=label, resolve_by_title_fn=resolve_by_title_fn) or record
    if record is not None and record.get("selection_error"):
        return record
    if resolve_by_title_fn is not None and (label or intent.target_label):
        return resolve_by_title_fn(str(label or intent.target_label))
    return None


_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
#: A request that NAMES one dated occurrence: "the 2026-10-07 occurrence", "occurrence on 2026-10-07", "... on 2026-10-07",
#: "... on October 7". A move's destination ("to 2026-10-09 10:30") names no occurrence.
_OCCURRENCE_NAMED_RE = re.compile(
    r"\bthe\s+20\d{2}-\d{2}-\d{2}\s+occurrence\b|\boccurrence\s+(?:on|of|dated)\s+20\d{2}-\d{2}-\d{2}\b|\bon\s+20\d{2}-\d{2}-\d{2}\b"
    rf"|\bon\s+(?:{_MONTHS})\s+\d{{1,2}}\b|\bon\s+\d{{1,2}}\s+(?:{_MONTHS})\b",
    re.IGNORECASE,
)


def _named_occurrence_of_owned_series(record: dict[str, Any], *, intent: OperatorActionIntent, label: str | None,
                                      resolve_by_title_fn: Any) -> dict[str, Any] | None:
    """The ONE dated occurrence a request names, when it belongs to the series this chat's receipt is for.

    A receipt names the object an earlier approval changed -- for a recurring series, the series' uid -- and carries no
    occurrence identity. A request that names a dated occurrence of that series acts on THAT occurrence, with its
    RECURRENCE-ID and the provider's current version, never on the whole series through the receipt. Anything short of
    exactly one named occurrence of the same series keeps the receipt; a failed or ambiguous provider read changes nothing.
    """
    wanted = str(label or intent.target_label or "").strip()
    if resolve_by_title_fn is None or not wanted or not _OCCURRENCE_NAMED_RE.search(str(intent.raw_text or "")):
        return None
    try:
        found = resolve_by_title_fn(wanted)
    except Exception:
        return None
    if not isinstance(found, dict) or found.get("selection_error") or not str(found.get("original_start") or ""):
        return None
    if str(found.get("series_id") or found.get("uid") or "") != str(record.get("uid") or ""):
        return None
    return found


def stage_provider_update(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    resolve_owned_event_fn: Any,
    parse_when_fn: Any,
    create_pending_action_fn: Any,
    label: str | None = None,
    resolve_by_title_fn: Any = None,
    provider_binding: dict[str, str] | None = None,
) -> OperatorActionResult:
    """Stage a provider-event update (time and/or title) with the etag the user SAW."""
    record = _resolve_update_target(
        intent=intent, session_id=session_id,
        resolve_owned_event_fn=resolve_owned_event_fn, resolve_by_title_fn=resolve_by_title_fn,
        label=label,
    )
    if record is None or record.get("selection_error"):
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=(record or {}).get("selection_error")
            or "I don't have a provider event matching that in this chat. Give its exact title or uid; local .ics drafts are changed on the draft path.",
            details={"kind": "update_calendar_event"},
        )
    new_title = _parse_rename(intent.raw_text)
    wants_time = _has_day_or_clock(intent.raw_text) or re.search(r"\b(?:move|reschedule|shift|push)\b", intent.raw_text, re.IGNORECASE)
    when = parse_when_fn(intent.raw_text) if wants_time else None
    if when is not None and not when.ok:
        return OperatorActionResult(ok=False, status="invalid_request", response_text=when.problem, details={"kind": "update_calendar_event"})
    if not new_title and (when is None or not when.ok):
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text="What should change? Give a new time (move/reschedule to ...) or a new title (rename ... to \"New title\").",
            details={"kind": "update_calendar_event"},
        )
    action_id = create_pending_action_fn(
        session_id=session_id,
        task_id=task_id,
        action_kind="provider_calendar_update",
        scope={
            "uid": record["uid"],
            "calendar_id": record["calendar_id"],
            "etag_seen": record["etag"],
            "title_seen": record["title"],
            "new_title": new_title or record["title"],
            "new_start_utc": (when.due_at_utc if when and when.ok else record["start_utc"]),
            "series_named": 1 if _names_series_scope(intent.raw_text) else 0,
            "occurrence_series": str(record.get("series_id") or ""),
            "occurrence_original_start": str(record.get("original_start") or ""),
            "action_ref": record["action_id"],
            **(provider_binding or {}),
        },
    )
    preview_bits = [f"'{record['title']}' (uid {record['uid'][:8]})"]
    if new_title:
        preview_bits.append(f"title -> \"{new_title}\"")
    if when and when.ok:
        preview_bits.append(f"start -> {when.due_wall or when.due_at_utc} ({when.tz_name or 'as parsed'})")
    return OperatorActionResult(
        ok=True,
        status="approval_required",
        response_text=(
            "Provider event update ready:\n- " + "\n- ".join(preview_bits) + "\n\n"
            f"Reply with: approve calendar {action_id}"
        ),
        details={"action_id": action_id, "uid": record["uid"], "etag_seen": record["etag"]},
    )


def _has_day_or_clock(text: str) -> bool:
    return bool(re.search(r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(am|pm)\b|\b20\d{2}-\d{2}-\d{2}\b", str(text or ""), re.IGNORECASE))


def _parse_rename(text: str) -> str | None:
    match = re.search(
        r'\b(?:rename|retitle)\b.*?\bto\s+["\u201c]?([^"\u201d\n]+?)["\u201d]?\s*(?:$|[,.])'
        r'|\bchange\s+the\s+title\s+of\s+\S+\s+to\s+["\u201c]?([^"\u201d\n]+?)["\u201d]?\s*(?:$|[,.])'
        r'|\bcall\s+(?:it|the\s+\S+)\s+["\u201c]?([^"\u201d\n]+?)["\u201d]?\s*(?:$|[,.])',
        str(text or ""), re.IGNORECASE,
    )
    if not match:
        return None
    title = next((group for group in match.groups() if group), None)
    return (title or "").strip() or None


def execute_provider_update_approval(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    adapter: Any,
    load_pending_action_fn: Any,
    mark_action_executed_fn: Any,
    audit_log_fn: Any,
    config: CalendarProviderConfig | None = None,
    claim_action_fn: Any = None,
    mark_outcome_unproven_fn: Any = None,
    mark_effect_unrecorded_fn: Any = None,
    requeue_interrupted_fn: Any = None,
    pending_row: dict[str, Any] | None = None,
) -> OperatorActionResult:
    """Execute an approved provider UPDATE through the same operation lifecycle as creates.

    Evidence of application is the target the update would produce (title, start, end), recorded
    before the write. Every exit before the write returns the operation to its entry state; after
    the write it rests unproven or accepted. Recovery verifies: target present -> recorded; the
    event exactly as the user saw it (same version, not accepted) -> proven not applied, sent once
    more under If-Match; anything else -> not attributable, nothing sent or overwritten.
    """
    pending, early = _enter_attempt(
        intent, session_id=session_id, action_kind="provider_calendar_update", pending_row=pending_row,
        load_pending_action_fn=load_pending_action_fn, config=config, claim_action_fn=claim_action_fn,
        requeue_interrupted_fn=requeue_interrupted_fn, unknown_result=_unknown_update_approval,
        already_executed_result=_already_updated_result,
    )
    if early is not None:
        return early
    from core.operator.approvals import relinquish_claim

    action_id = str(pending.get("action_id"))
    try:
        return _run_update_attempt(pending, adapter, action_id, task_id=task_id, mark_action_executed_fn=mark_action_executed_fn,
                                   mark_outcome_unproven_fn=mark_outcome_unproven_fn, mark_effect_unrecorded_fn=mark_effect_unrecorded_fn,
                                   audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn, config=config)
    finally:
        relinquish_claim(action_id)


def _same_instant(left: str, right: str) -> bool:
    try:
        return datetime.fromisoformat(str(left).replace("Z", "+00:00")) == datetime.fromisoformat(str(right).replace("Z", "+00:00"))
    except ValueError:
        return False


def _update_applied(event: CalEvent, target: dict[str, Any]) -> bool:
    return bool(target) and (str(event.summary or "").strip() == str(target.get("title") or "").strip()
                             and _same_instant(event.start_utc, str(target.get("start_utc") or ""))
                             and _same_instant(event.end_utc, str(target.get("end_utc") or "")))


def _pre_write_release(claim_action_fn: Any, mark_outcome_unproven_fn: Any, action_id: str, entry: str, note: str) -> str:
    """Nothing crossed the boundary on this attempt: the operation returns to its entry state."""
    return _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note=note, proven_not_applied=True)


def _run_update_attempt(pending: dict[str, Any], adapter: Any, action_id: str, *, task_id: str, mark_action_executed_fn: Any,
                        mark_outcome_unproven_fn: Any, mark_effect_unrecorded_fn: Any, audit_log_fn: Any, claim_action_fn: Any,
                        config: CalendarProviderConfig | None = None) -> OperatorActionResult:
    scope = json.loads(str(pending.get("scope_json") or "{}"))
    uid = str(scope.get("uid") or "")
    calendar_id = str(scope.get("calendar_id") or "")
    etag_seen = str(scope.get("etag_seen") or "")
    entry = str(pending.get("status") or "pending_approval")
    evidence = _pending_evidence(pending)
    recovering = entry != "pending_approval"
    try:
        current = adapter.get_event(calendar_id, uid)
    except CalendarRefusedError as exc:
        if exc.reason == "not_found":
            if recovering:
                rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="event gone during recovery; earlier update not verifiable")
                return OperatorActionResult(ok=False, status="not_found", response_text="That event is no longer on the provider, so the earlier update cannot be verified. Nothing was sent again.", details={"uid": uid, "action_id": action_id, "resting_status": rested})
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "event no longer exists; nothing sent")
            return OperatorActionResult(ok=False, status="not_found", response_text=_refusal_text(exc) + " Nothing was changed.", details={"uid": uid, "action_id": action_id, "resting_status": rested})
        if recovering:
            return _unverifiable(action_id, entry, exc.reason, claim_action_fn, mark_outcome_unproven_fn, refusal=exc)
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"read refused before sending: {exc.reason}")
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        if recovering:
            return _unverifiable(action_id, entry, reason, claim_action_fn, mark_outcome_unproven_fn)
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"read failed before sending: {reason}")
        status = "blocked" if isinstance(exc, TransportDeniedError) else "provider_unreachable"
        return OperatorActionResult(ok=False, status=status, response_text=f"I could not read the event before changing it ({reason}). Nothing was sent; the approval is still waiting.", details={"reason": reason, "action_id": action_id, "resting_status": rested})
    occurrence_start = str(scope.get("occurrence_original_start") or "")
    series_uid = str(scope.get("occurrence_series") or uid)
    if occurrence_start and not (hasattr(adapter, "update_occurrence") and hasattr(adapter, "find_occurrence")):
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "single-occurrence edits unsupported on this provider")
        return OperatorActionResult(
            ok=False, status="unavailable",
            response_text=("This calendar provider does not support editing one occurrence through VOOL; name "
                           "'the whole <title> series' to change every occurrence instead. Nothing was sent."),
            details={"action_id": action_id, "resting_status": rested},
        )
    if _is_series_master(current) and not scope.get("series_named") and not occurrence_start:
        # A CalDAV-style series master covers every occurrence; an approval that did not name the
        # series must not rewrite it. Nothing was sent; the approval rests at its entry state.
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "series scope not named; nothing sent")
        return OperatorActionResult(
            ok=False, status="ambiguous",
            response_text=(_series_scope_question(current, "") or "that event is a repeating series; name the scope"),
            details={"uid": uid, "action_id": action_id, "resting_status": rested},
        )
    if recovering:
        target = evidence.get("update_target") if isinstance(evidence.get("update_target"), dict) else {}
        observed: CalEvent | None = current
        if occurrence_start:
            # The approved change lives on ONE occurrence, found by its RECURRENCE-ID wherever it now is: the approved
            # target or its original slot. The series master never carries it.
            try:
                observed = adapter.find_occurrence(calendar_id, series_uid, occurrence_start,
                                                   windows=_occurrence_windows(target, occurrence_start, current))
            except CalendarRefusedError as exc:
                if exc.reason != "not_found":
                    return _unverifiable(action_id, entry, exc.reason, claim_action_fn, mark_outcome_unproven_fn, refusal=exc)
                observed = None
            except Exception as exc:
                return _unverifiable(action_id, entry, str(getattr(exc, "reason", "") or type(exc).__name__), claim_action_fn, mark_outcome_unproven_fn)
        if target and observed is not None and _update_applied(observed, target):
            return _finalize_update(action_id, calendar_id, observed, task_id=task_id, mark_action_executed_fn=mark_action_executed_fn,
                                    mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, recovered=True,
                                    occurrence_start=occurrence_start)
        if evidence.get("accepted"):
            # The provider accepted an earlier write, yet the approved change is absent: something
            # changed the event afterwards or the provider stored something else. A version token that
            # did not move (coarse tokens such as a modification date) proves nothing here.
            rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="accepted earlier but the approved change is absent")
            return OperatorActionResult(
                ok=False,
                status="identity_mismatch",
                response_text=(f"The provider accepted the earlier update, but the event '{current.summary}' does not carry the approved change now. "
                               "Nothing was sent again or overwritten; inspect the event."),
                details={"uid": uid, "action_id": action_id, "resting_status": rested},
            )
        if not (etag_seen and current.etag == etag_seen):
            # Changed since the version the user saw: whether the earlier update landed cannot be
            # attributed, and this recovery must not record it as proven unapplied.
            rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="event matches neither the version seen nor the update target")
            return OperatorActionResult(
                ok=False,
                status="stale_etag",
                response_text=(f"The event '{current.summary}' matches neither the version you saw nor what the update would have made it, so I "
                               "cannot tell whether the earlier update landed. Nothing was sent again or overwritten; inspect the event."),
                details={"uid": uid, "action_id": action_id, "resting_status": rested},
            )
        # Exactly the version the user saw and never accepted: the earlier write is proven unapplied.
    if etag_seen and current.etag and current.etag != etag_seen:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "stale version before sending")
        return OperatorActionResult(
            ok=False,
            status="stale_etag",
            response_text=(
                f"The event '{current.summary}' changed on the provider after you last saw it "
                f"(you saw version {etag_seen[:12]}, it is now {current.etag[:12]}). Nothing was overwritten. "
                "Inspect the event and approve a fresh update."
            ),
            details={"uid": uid, "etag_seen": etag_seen, "etag_now": current.etag, "action_id": action_id, "resting_status": rested},
        )
    new_start = str(scope.get("new_start_utc") or current.start_utc)
    try:
        start_dt = datetime.fromisoformat(new_start.replace("Z", "+00:00"))
        old_start = datetime.fromisoformat(current.start_utc.replace("Z", "+00:00")) if current.start_utc else start_dt
        old_end = datetime.fromisoformat(current.end_utc.replace("Z", "+00:00")) if current.end_utc else old_start
    except ValueError:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "unreadable staged start")
        return OperatorActionResult(ok=False, status="invalid_request", response_text="The staged new start time is not readable. Nothing was changed.", details={"action_id": action_id, "resting_status": rested})
    duration = (old_end - old_start) or timedelta(minutes=30)
    new_end = start_dt + duration
    new_title = str(scope.get("new_title") or current.summary)
    if new_start != (current.start_utc or "").replace("Z", "+00:00") or current.end_utc:
        # The ETag protects the EVENT's version, not the CALENDAR's availability: the destination
        # interval must still be free at execution time. The moved event itself is excluded.
        try:
            overlapping = adapter.events_in_range(calendar_id, start_utc=start_dt.isoformat(), end_utc=new_end.isoformat())
            conflicts = _find_conflicts([event for event in overlapping if event.uid != uid], start_dt, new_end)
            other_conflicts, unreadable = _other_selected_conflicts(config, calendar_id, start_dt, new_end)
            conflicts = conflicts + other_conflicts
        except SelectionsUnavailableError as exc:
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "selections store unreadable before sending")
            return OperatorActionResult(
                ok=False, status="storage_unavailable",
                response_text=(f"I could not read which calendars are chosen for sync ({exc}), so I cannot prove the "
                               "destination time is free across your calendars. The event was NOT moved; the approval is still waiting."),
                details={"reason": "selections_unavailable", "action_id": action_id, "resting_status": rested},
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or type(exc).__name__)
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"availability read failed before sending: {reason}")
            return OperatorActionResult(ok=False, status="provider_unreachable", response_text=f"I could not re-check the destination time ({reason}). The event was NOT moved; the approval is still waiting.", details={"reason": reason, "action_id": action_id, "resting_status": rested})
        if unreadable:
            # A chosen calendar that cannot be read is not a free calendar: the move is NOT
            # sent, the refusal names the source, and the approval rests unchanged (same
            # contract as the create path).
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "a selected calendar could not be read before sending")
            return OperatorActionResult(
                ok=False, status="provider_unreachable",
                response_text=(f"One of your chosen calendars could not be read ({_unreadable_sources_text(unreadable)}), so I "
                               "cannot prove the destination time is free across your calendars. The event was NOT moved; the "
                               "approval is still waiting — approve again once it answers."),
                details={"unreadable_sources": unreadable, "action_id": action_id, "uid": uid, "resting_status": rested},
            )
        if conflicts:
            lines = ["The destination is no longer free — another event now occupies that time:"]
            for conflict in conflicts:
                lines.append(_format_event_line(conflict, current.tz_name))
            lines.append("")
            lines.append("The event was NOT moved and keeps its original time. Check the day again or pick a different time.")
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "destination taken before sending")
            return OperatorActionResult(ok=False, status="conflict", response_text="\n".join(lines),
                                        details={"conflicts": [_event_brief(c) for c in conflicts], "action_id": action_id, "uid": uid, "resting_status": rested})
    target = {"title": new_title, "start_utc": start_dt.isoformat(), "end_utc": new_end.isoformat()}
    identity = {"uid": uid, "calendar_id": calendar_id, "provider_uid": uid}
    if _record_phase(action_id, PHASE_DISPATCHING, evidence={**identity, "update_target": target, "etag_seen": etag_seen}, note="sending the update") is False:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "the attempt could not be recorded before sending; nothing was sent")
        return OperatorActionResult(ok=False, status="ownership_lost", response_text="I could not safely record this update before sending it. Nothing was sent; say 'check the event' to see the provider's actual state.", details={"action_id": action_id, "resting_status": rested})
    unproven = {**identity, "update_target": target}
    try:
        if occurrence_start:
            adapter.update_occurrence(calendar_id, CalEvent(
                provider_id=current.provider_id, uid=uid, calendar_id=calendar_id,
                series_id=str(scope.get("occurrence_series") or uid), original_start=occurrence_start,
                etag=etag_seen or current.etag, summary=new_title,
                start_utc=new_start, end_utc=new_end.isoformat(), tz_name=current.tz_name,
                description=current.description, all_day=current.all_day))
        else:
            adapter.update_event(calendar_id, CalEvent(provider_id=current.provider_id, uid=uid, calendar_id=calendar_id, etag=etag_seen or current.etag,
                                                       summary=new_title, start_utc=new_start, end_utc=new_end.isoformat(), tz_name=current.tz_name,
                                                       description=current.description, all_day=current.all_day))
    except CalendarWriteAcceptedError as exc:
        _record_phase(action_id, PHASE_ACCEPTED, evidence={"accepted": True}, note=f"update accepted; read-back failed: {exc.reason}")
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "accepted": True, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=(f"The calendar provider accepted the update, but I could not read the event back to verify it ({exc.reason}). It was NOT sent again and it is NOT reported as unchanged. Ask me to check the event."), details={"uid": uid, "action_id": action_id, "accepted": True, "recorded": recorded})
    except CalendarRefusedError as exc:
        if exc.reason == "precondition_failed":
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "the provider refused the version")
            return OperatorActionResult(ok=False, status="stale_etag", response_text=_refusal_text(exc) + " Nothing was overwritten.", details={"uid": uid, "action_id": action_id, "resting_status": rested})
        if _write_refusal_proves_unsent(exc):
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"update refused: {exc.reason}")
            return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=(f"The calendar provider answered the update with {exc.reason}, which does not prove whether it was applied. It was NOT sent again. Ask me to check the event."), details={"reason": exc.reason, "action_id": action_id, "recorded": recorded})
    except TransportDeniedError as exc:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"refused before any socket: {exc.reason}")
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except TransportUnknownError as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason, "action_id": action_id, "recorded": recorded})
    except Exception as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "reason": f"unexpected_{type(exc).__name__}"})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=f"The update failed unexpectedly after it was sent ({type(exc).__name__}); whether it was applied is unproven. It was NOT retried; ask me to check the event.", details={"reason": type(exc).__name__, "action_id": action_id, "recorded": recorded})
    _record_phase(action_id, PHASE_ACCEPTED, evidence={"accepted": True}, note="provider accepted the update")
    try:
        if occurrence_start:
            # Verify the OCCURRENCE by its RECURRENCE-ID -- at the approved target, or still at its original slot if the
            # provider did not move it -- never the series master it belongs to.
            verified = adapter.find_occurrence(calendar_id, series_uid, occurrence_start,
                                               windows=_occurrence_windows(target, occurrence_start, current))
        else:
            verified = adapter.get_event(calendar_id, uid)
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "accepted": True, "reason": f"verification read {reason}"})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=(f"The calendar provider accepted the update, but I could not read the event back to verify it ({reason}). It was NOT sent again and it is NOT reported as unchanged. Ask me to check the event."), details={"uid": uid, "action_id": action_id, "accepted": True, "recorded": recorded})
    if not _update_applied(verified, target):
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**unproven, "accepted": True, "identity_mismatch": ["title/start/end"]})
        return OperatorActionResult(ok=False, status="identity_mismatch", response_text="The provider accepted the update but now stores something other than the approved change. Nothing further was changed; inspect the event.", details={"uid": uid, "action_id": action_id, "recorded": recorded})
    return _finalize_update(action_id, calendar_id, verified, task_id=task_id, mark_action_executed_fn=mark_action_executed_fn,
                            mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn, recovered=False,
                            occurrence_start=occurrence_start)


def _occurrence_windows(target: dict[str, Any], occurrence_start: str, series: CalEvent) -> list[tuple[str, str]]:
    """Where ONE occurrence may legitimately be: the approved target interval, and its original slot (the series master's
    length from the occurrence's original start)."""
    windows: list[tuple[str, str]] = []
    if target.get("start_utc") and target.get("end_utc"):
        windows.append((str(target["start_utc"]), str(target["end_utc"])))
    try:
        original = datetime.fromisoformat(str(occurrence_start).replace("Z", "+00:00"))
        if original.tzinfo is None:
            original = original.replace(tzinfo=timezone.utc)
        span = timedelta(days=1)
        if series.start_utc and series.end_utc:
            span = datetime.fromisoformat(series.end_utc.replace("Z", "+00:00")) - datetime.fromisoformat(series.start_utc.replace("Z", "+00:00"))
        windows.append((original.isoformat(), (original + max(span, timedelta(minutes=1))).isoformat()))
    except ValueError:
        pass
    return windows


def _finalize_update(action_id: str, calendar_id: str, verified: CalEvent, *, task_id: str, mark_action_executed_fn: Any,
                     mark_effect_unrecorded_fn: Any, audit_log_fn: Any, recovered: bool, occurrence_start: str = "") -> OperatorActionResult:
    receipt = {"uid": verified.uid, "etag": verified.etag, "title": verified.summary, "start_utc": verified.start_utc, "end_utc": verified.end_utc, "calendar_id": calendar_id}
    if occurrence_start:
        receipt["occurrence_original_start"] = occurrence_start
    outcome = _mark_receipt(mark_action_executed_fn, mark_effect_unrecorded_fn, action_id, receipt)
    audit_log_fn("operator_action_update_calendar_event", target_id=task_id, target_type="task",
                 details={"uid": verified.uid, "calendar_id": calendar_id, "etag": verified.etag, "verified": True, "recovered": recovered,
                          "occurrence_original_start": occurrence_start, "receipt_outcome": outcome})
    if occurrence_start:
        response = (f"Event updated on the provider and verified: the {occurrence_start[:10]} occurrence of '{verified.summary}' now starts "
                    f"{verified.start_utc}, read back by its own recurrence identity (series version {verified.etag or 'unknown'}).")
    else:
        response = f"Event updated on the provider and verified: '{verified.summary}' now starts {verified.start_utc} (version {verified.etag or 'unknown'})."
    if recovered:
        response += " (recovered: the earlier update's reply was lost; the event verifiably carries the approved change, and nothing was sent twice)"
    details: dict[str, Any] = {"uid": verified.uid, "etag": verified.etag, "verified": True, "calendar_id": calendar_id, "recovered": recovered}
    if occurrence_start:
        details["occurrence_original_start"] = occurrence_start
    if outcome != "executed":
        response += " NOTE: the change verifiably exists on the provider, but its receipt could not be recorded here; nothing will be sent again."
        details["receipt_outcome"] = outcome
    return OperatorActionResult(ok=True, status="executed", response_text=response, details=details)


def _already_updated_result(row: dict[str, Any]) -> OperatorActionResult:
    saved = json.loads(str(row.get("result_json") or "{}"))
    return OperatorActionResult(
        ok=False,
        status="already_executed",
        response_text=f"That approval was already used: '{saved.get('title')}' was updated (uid {str(saved.get('uid') or '')[:8]}). Nothing was changed a second time.",
        details={"action_id": str(row.get("action_id")), "receipt": saved},
    )


def _unknown_update_approval(session_id: str, action_id: str | None) -> OperatorActionResult:
    row = load_action_any_state(session_id=session_id, action_kind="provider_calendar_update", action_id=action_id)
    if row is not None and str(row.get("status")) == "executed":
        return _already_updated_result(row)
    return OperatorActionResult(
        ok=False,
        status="invalid_request",
        response_text="I don't have a pending event-update approval matching that in this chat. Nothing was executed.",
        details={"action_id": action_id},
    )






def stage_provider_cancel(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    resolve_owned_event_fn: Any,
    create_pending_action_fn: Any,
    label: str | None = None,
    resolve_by_title_fn: Any = None,
    provider_binding: dict[str, str] | None = None,
) -> OperatorActionResult:
    record = _resolve_update_target(
        intent=intent, session_id=session_id,
        resolve_owned_event_fn=resolve_owned_event_fn, resolve_by_title_fn=resolve_by_title_fn,
        label=label,
    )
    if record is None or record.get("selection_error"):
        return OperatorActionResult(
            ok=False,
            status="invalid_request",
            response_text=(record or {}).get("selection_error")
            or "I don't have a provider event matching that in this chat, and I will not cancel something I cannot identify. Give its exact title or uid.",
            details={"kind": "cancel_provider_event"},
        )
    action_id = create_pending_action_fn(
        session_id=session_id,
        task_id=task_id,
        action_kind="provider_calendar_cancel",
        scope={"uid": record["uid"], "calendar_id": record["calendar_id"], "etag_seen": record["etag"], "title": record["title"],
               "series_named": 1 if _names_series_scope(intent.raw_text) else 0,
               "occurrence_series": str(record.get("series_id") or ""),
               "occurrence_original_start": str(record.get("original_start") or ""),
               "action_ref": record["action_id"], **(provider_binding or {})},
    )
    occurrence = str(record.get("original_start") or "")
    what = (f"the {occurrence[:10]} occurrence of '{record['title']}' (series uid {record['uid'][:8]}) will be cancelled on the "
            "calendar; the series and its other occurrences stay"
            if occurrence else f"'{record['title']}' (uid {record['uid'][:8]}) will be deleted from the calendar")
    return OperatorActionResult(
        ok=True,
        status="approval_required",
        response_text=(
            f"Provider event cancellation ready: {what}.\n\n"
            f"Reply with: approve calendar {action_id}"
        ),
        details={"action_id": action_id, "uid": record["uid"]},
    )


def execute_provider_cancel_approval(
    intent: OperatorActionIntent,
    *,
    task_id: str,
    session_id: str,
    adapter: Any,
    load_pending_action_fn: Any,
    mark_action_executed_fn: Any,
    audit_log_fn: Any,
    config: CalendarProviderConfig | None = None,
    claim_action_fn: Any = None,
    mark_outcome_unproven_fn: Any = None,
    mark_effect_unrecorded_fn: Any = None,
    requeue_interrupted_fn: Any = None,
    pending_row: dict[str, Any] | None = None,
) -> OperatorActionResult:
    """Execute an approved provider CANCELLATION through the same operation lifecycle as creates.

    Evidence of application is the event's absence. Every exit before the DELETE returns the
    operation to its entry state; after it the operation rests unproven or accepted. Recovery
    verifies: gone -> recorded (and said plainly that this cancellation cannot be proven to be what
    removed it); exactly as the user saw it (same version, not accepted) -> proven not applied, sent
    once more under If-Match; changed -> not attributable, nothing sent.
    """
    pending, early = _enter_attempt(
        intent, session_id=session_id, action_kind="provider_calendar_cancel", pending_row=pending_row,
        load_pending_action_fn=load_pending_action_fn, config=config, claim_action_fn=claim_action_fn,
        requeue_interrupted_fn=requeue_interrupted_fn, unknown_result=_unknown_cancel_approval,
        already_executed_result=_already_cancelled_result,
    )
    if early is not None:
        return early
    from core.operator.approvals import relinquish_claim

    action_id = str(pending.get("action_id"))
    try:
        return _run_cancel_attempt(pending, adapter, action_id, task_id=task_id, mark_action_executed_fn=mark_action_executed_fn,
                                   mark_outcome_unproven_fn=mark_outcome_unproven_fn, mark_effect_unrecorded_fn=mark_effect_unrecorded_fn,
                                   audit_log_fn=audit_log_fn, claim_action_fn=claim_action_fn)
    finally:
        relinquish_claim(action_id)


def _run_cancel_attempt(pending: dict[str, Any], adapter: Any, action_id: str, *, task_id: str, mark_action_executed_fn: Any,
                        mark_outcome_unproven_fn: Any, mark_effect_unrecorded_fn: Any, audit_log_fn: Any, claim_action_fn: Any) -> OperatorActionResult:
    scope = json.loads(str(pending.get("scope_json") or "{}"))
    uid = str(scope.get("uid") or "")
    calendar_id = str(scope.get("calendar_id") or "")
    etag_seen = str(scope.get("etag_seen") or "")
    title = scope.get("title")
    entry = str(pending.get("status") or "pending_approval")
    evidence = _pending_evidence(pending)
    recovering = entry != "pending_approval"
    context = dict(task_id=task_id, mark_action_executed_fn=mark_action_executed_fn, mark_effect_unrecorded_fn=mark_effect_unrecorded_fn, audit_log_fn=audit_log_fn)
    cancel_occurrence_start = str(scope.get("occurrence_original_start") or "")
    series_uid = str(scope.get("occurrence_series") or uid)
    try:
        current = adapter.get_event(calendar_id, uid)
    except CalendarRefusedError as exc:
        if exc.reason == "not_found":
            if recovering and cancel_occurrence_start:
                # The whole series is gone: that is no evidence this approval cancelled ONE occurrence of it.
                rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry,
                                        note="series gone during recovery; the occurrence cancellation is not verifiable")
                return OperatorActionResult(
                    ok=False, status="not_found",
                    response_text=(f"The series '{title}' is no longer on the provider, so whether the earlier cancellation of its "
                                   f"{cancel_occurrence_start[:10]} occurrence landed cannot be verified. Nothing was sent again and no "
                                   "cancellation is claimed."),
                    details={"uid": uid, "action_id": action_id, "occurrence_original_start": cancel_occurrence_start, "resting_status": rested},
                )
            if recovering:
                return _finalize_cancel(action_id, calendar_id, uid, title, attribution="unproven", recovered=True, **context)
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "event already gone before sending")
            return OperatorActionResult(
                ok=False,
                status="not_found",
                response_text="That event no longer exists on the provider, so there is nothing to cancel. I am not reporting a cancellation.",
                details={"uid": uid, "action_id": action_id, "resting_status": rested},
            )
        if recovering:
            return _unverifiable(action_id, entry, exc.reason, claim_action_fn, mark_outcome_unproven_fn, refusal=exc)
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"read refused before sending: {exc.reason}")
        return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except Exception as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        if recovering:
            return _unverifiable(action_id, entry, reason, claim_action_fn, mark_outcome_unproven_fn)
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"read failed before sending: {reason}")
        status = "blocked" if isinstance(exc, TransportDeniedError) else "provider_unreachable"
        return OperatorActionResult(ok=False, status=status, response_text=f"I could not read the event before cancelling it ({reason}). Nothing was cancelled; the approval is still waiting.", details={"reason": reason, "action_id": action_id, "resting_status": rested})
    if cancel_occurrence_start and not (hasattr(adapter, "cancel_occurrence") and hasattr(adapter, "occurrence_cancelled")):
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "single-occurrence cancellation unsupported on this provider")
        return OperatorActionResult(
            ok=False, status="unavailable",
            response_text=("This calendar provider does not support cancelling one occurrence through VOOL; name "
                           "'the whole <title> series' to cancel every occurrence instead. Nothing was sent."),
            details={"action_id": action_id, "resting_status": rested},
        )
    if _is_series_master(current) and not scope.get("series_named") and not cancel_occurrence_start:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "series scope not named; nothing sent")
        return OperatorActionResult(
            ok=False, status="ambiguous",
            response_text=(_series_scope_question(current, "") or "that event is a repeating series; name the scope"),
            details={"uid": uid, "action_id": action_id, "resting_status": rested},
        )
    if recovering and cancel_occurrence_start:
        # Verify-only: the stored series must POSITIVELY show exactly that occurrence cancelled. After an accepted write a
        # missing trace keeps the operation unproven and nothing is re-sent; only a series exactly as reviewed and never
        # accepted is proven untouched (below) and may be sent once more under If-Match.
        try:
            cancelled, observed = adapter.occurrence_cancelled(calendar_id, series_uid, cancel_occurrence_start)
        except CalendarRefusedError as exc:
            return _unverifiable(action_id, entry, exc.reason, claim_action_fn, mark_outcome_unproven_fn, refusal=exc)
        except Exception as exc:
            return _unverifiable(action_id, entry, str(getattr(exc, "reason", "") or type(exc).__name__), claim_action_fn, mark_outcome_unproven_fn)
        if cancelled:
            return _finalize_cancel(action_id, calendar_id, uid, title, attribution="verified" if evidence.get("accepted") else "unproven",
                                    recovered=True, occurrence_start=cancel_occurrence_start, **context)
        if evidence.get("accepted"):
            rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry,
                                    note="accepted earlier; the occurrence is not verifiably cancelled yet")
            return OperatorActionResult(
                ok=False, status="outcome_unproven",
                response_text=(f"The calendar provider accepted the earlier cancellation of the {cancel_occurrence_start[:10]} occurrence of "
                               f"'{title}', but it is not verifiably cancelled: {observed}. It was NOT sent again and it is NOT reported as "
                               "cancelled; ask me to check the event again later."),
                details={"uid": uid, "action_id": action_id, "accepted": True, "occurrence_original_start": cancel_occurrence_start,
                         "resting_status": rested},
            )
    if recovering and not (bool(etag_seen) and current.etag == etag_seen and not evidence.get("accepted")):
        rested = _release_claim(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, note="event still present but changed since the version seen")
        return OperatorActionResult(
            ok=False,
            status="stale_etag",
            response_text=(f"The event '{current.summary}' is still on the provider but changed since you last saw it, so I cannot tell what the "
                           "earlier cancellation did. Nothing was cancelled or sent again; inspect the event."),
            details={"uid": uid, "action_id": action_id, "resting_status": rested},
        )
    if etag_seen and current.etag and current.etag != etag_seen:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "stale version before sending")
        return OperatorActionResult(
            ok=False,
            status="stale_etag",
            response_text=(
                f"The event '{current.summary}' changed on the provider after you last saw it. Nothing was cancelled. "
                "Inspect the event and approve a fresh cancellation if that is still what you want."
            ),
            details={"uid": uid, "etag_seen": etag_seen, "etag_now": current.etag, "action_id": action_id, "resting_status": rested},
        )
    identity = {"uid": uid, "calendar_id": calendar_id, "provider_uid": uid}
    if _record_phase(action_id, PHASE_DISPATCHING, evidence={**identity, "etag_seen": etag_seen}, note="sending the cancellation") is False:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "the attempt could not be recorded before sending; nothing was sent")
        return OperatorActionResult(ok=False, status="ownership_lost", response_text="I could not safely record this cancellation before sending it. Nothing was sent; say 'check the event' to see the provider's actual state.", details={"action_id": action_id, "resting_status": rested})
    try:
        if cancel_occurrence_start:
            adapter.cancel_occurrence(calendar_id, str(scope.get("occurrence_series") or uid), cancel_occurrence_start, etag_seen or current.etag)
        else:
            adapter.cancel_event(calendar_id, uid, etag_seen or current.etag)
    except CalendarWriteAcceptedError as exc:
        _record_phase(action_id, PHASE_ACCEPTED, evidence={"accepted": True}, note=f"deletion accepted; read-back failed: {exc.reason}")
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "accepted": True, "reason": exc.reason})
        gone = f"the {cancel_occurrence_start[:10]} occurrence is cancelled" if cancel_occurrence_start else "the event is gone"
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=(f"The calendar provider accepted the cancellation, but I could not confirm {gone} ({exc.reason}). It was NOT sent again and it is NOT reported as still scheduled. Ask me to check the event."), details={"uid": uid, "action_id": action_id, "accepted": True, "recorded": recorded})
    except CalendarRefusedError as exc:
        if exc.reason == "precondition_failed":
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "the provider refused the version")
            return OperatorActionResult(ok=False, status="stale_etag", response_text=_refusal_text(exc) + " Nothing was cancelled.", details={"uid": uid, "action_id": action_id, "resting_status": rested})
        if exc.reason == "not_found":
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "event removed elsewhere before the deletion applied")
            return OperatorActionResult(ok=False, status="not_found", response_text="The event disappeared from the provider before this cancellation could apply. I am not reporting a cancellation.", details={"uid": uid, "action_id": action_id, "resting_status": rested})
        if exc.reason == "still_present":
            _record_phase(action_id, PHASE_ACCEPTED, evidence={"accepted": True}, note="deletion accepted but the event is still served")
            recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "accepted": True, "reason": exc.reason})
            served = f"the {cancel_occurrence_start[:10]} occurrence as scheduled" if cancel_occurrence_start else "the event"
            return OperatorActionResult(ok=False, status="outcome_unproven", response_text=f"The calendar provider accepted the cancellation but still serves {served}. It was NOT sent again; ask me to check the event.", details={"uid": uid, "action_id": action_id, "accepted": True, "recorded": recorded})
        if exc.reason == "already_cancelled":
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, "the occurrence was already cancelled before sending")
            return OperatorActionResult(ok=False, status="not_found", response_text=(f"The {cancel_occurrence_start[:10]} occurrence of '{title}' is already cancelled on the provider, so there is nothing to cancel. Nothing was sent and no cancellation is claimed."), details={"uid": uid, "action_id": action_id, "resting_status": rested})
        if _write_refusal_proves_unsent(exc):
            rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"cancellation refused: {exc.reason}")
            return OperatorActionResult(ok=False, status="provider_refused", response_text=_refusal_text(exc), details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=(f"The calendar provider answered the cancellation with {exc.reason}, which does not prove whether the event was removed. It was NOT sent again. Ask me to check the event."), details={"reason": exc.reason, "action_id": action_id, "recorded": recorded})
    except TransportDeniedError as exc:
        rested = _pre_write_release(claim_action_fn, mark_outcome_unproven_fn, action_id, entry, f"refused before any socket: {exc.reason}")
        return OperatorActionResult(ok=False, status="blocked", response_text=f"I can't reach the calendar provider right now: {exc.reason}", details={"reason": exc.reason, "action_id": action_id, "resting_status": rested})
    except TransportUnknownError as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "reason": exc.reason})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=_unknown_outcome_text(exc), details={"reason": exc.reason, "action_id": action_id, "recorded": recorded})
    except Exception as exc:
        recorded = _rest_unproven(action_id, mark_outcome_unproven_fn, {**identity, "reason": f"unexpected_{type(exc).__name__}"})
        return OperatorActionResult(ok=False, status="outcome_unproven", response_text=f"The cancellation failed unexpectedly after it was sent ({type(exc).__name__}); whether the event was removed is unproven. It was NOT retried; ask me to check the event.", details={"reason": type(exc).__name__, "action_id": action_id, "recorded": recorded})
    return _finalize_cancel(action_id, calendar_id, uid, title, attribution="verified", recovered=False,
                            occurrence_start=cancel_occurrence_start, **context)


def _finalize_cancel(action_id: str, calendar_id: str, uid: str, title: Any, *, attribution: str, recovered: bool, task_id: str,
                     mark_action_executed_fn: Any, mark_effect_unrecorded_fn: Any, audit_log_fn: Any,
                     occurrence_start: str = "") -> OperatorActionResult:
    receipt = {"uid": uid, "calendar_id": calendar_id, "cancelled": True, "title": title, "verified_gone": True, "gone_attribution": attribution}
    if occurrence_start:
        # ONE occurrence: gone from the live schedule, proven by that RECURRENCE-ID's stored cancellation; the series stays.
        receipt["occurrence_original_start"] = occurrence_start
    outcome = _mark_receipt(mark_action_executed_fn, mark_effect_unrecorded_fn, action_id, receipt)
    audit_log_fn("operator_action_cancel_calendar_event", target_id=task_id, target_type="task",
                 details={"uid": uid, "calendar_id": calendar_id, "verified_gone": True, "attribution": attribution,
                          "occurrence_original_start": occurrence_start, "receipt_outcome": outcome})
    if occurrence_start and attribution == "verified":
        response = (f"Occurrence cancelled on the provider and verified: the stored series '{title}' (uid {uid[:8]}) now marks exactly its "
                    f"{occurrence_start[:10]} occurrence cancelled, and the series itself remains.")
        if recovered:
            response += " (recovered: the provider had accepted the earlier cancellation, and it is now verified; nothing was sent twice)"
    elif occurrence_start:
        response = (f"The {occurrence_start[:10]} occurrence of '{title}' (uid {uid[:8]}) now reads as cancelled on the provider. The earlier "
                    "cancellation's reply was lost, so I cannot prove it was this cancellation that did it; nothing was sent again.")
    elif attribution == "verified":
        response = f"Event cancelled on the provider and verified gone: '{title}' (uid {uid[:8]}). Only that exact event was touched."
    else:
        response = (f"The event '{title}' (uid {uid[:8]}) is gone from the provider. The earlier cancellation's reply was lost, so I cannot prove "
                    "it was this cancellation that removed it; nothing else was touched and nothing was sent again.")
    details: dict[str, Any] = {"uid": uid, "verified_gone": True, "calendar_id": calendar_id, "attribution": attribution, "recovered": recovered}
    if occurrence_start:
        details["occurrence_original_start"] = occurrence_start
    if outcome != "executed":
        response += " NOTE: the event is verifiably gone, but the cancellation's receipt could not be recorded here."
        details["receipt_outcome"] = outcome
    return OperatorActionResult(ok=True, status="executed", response_text=response, details=details)


def _already_cancelled_result(row: dict[str, Any]) -> OperatorActionResult:
    saved = json.loads(str(row.get("result_json") or "{}"))
    return OperatorActionResult(
        ok=False,
        status="already_executed",
        response_text=f"That approval was already used: '{saved.get('title')}' was cancelled (uid {str(saved.get('uid') or '')[:8]}). Nothing was cancelled a second time.",
        details={"action_id": str(row.get("action_id")), "receipt": saved},
    )


def _unknown_cancel_approval(session_id: str, action_id: str | None) -> OperatorActionResult:
    row = load_action_any_state(session_id=session_id, action_kind="provider_calendar_cancel", action_id=action_id)
    if row is not None and str(row.get("status")) == "executed":
        return _already_cancelled_result(row)
    return OperatorActionResult(
        ok=False,
        status="invalid_request",
        response_text="I don't have a pending event-cancellation approval matching that in this chat. Nothing was executed.",
        details={"action_id": action_id},
    )






def missing_configuration_result(action: str) -> OperatorActionResult:
    return OperatorActionResult(
        ok=False,
        status="unavailable",
        response_text=(
            f"I can't {action} because no calendar provider is configured on this runtime. "
            "Set VOOL_CALENDAR_PROVIDER, VOOL_CALENDAR_URL (and optionally VOOL_CALENDAR_ID / "
            "VOOL_CALENDAR_AUTH_BINDING) to connect a calendar. Without a provider I can still "
            "write a clearly-labelled local .ics draft — it would not be an event on any calendar."
        ),
        details={"reason": "missing_calendar_configuration"},
    )
