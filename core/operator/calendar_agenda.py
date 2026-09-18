"""Agenda and bounded search across the calendars chosen in Settings.

Row 5 of the product matrix: today/tomorrow/week/date-range agendas and a bounded event
search over EVERY opted-in selected calendar (any number of accounts), and row 6's busy set
for availability. One canonical read path — the account authority's own selections — so the
chat agenda and the alert schedule can never disagree about which calendars exist.

Reads are provider calls: each one crosses the egress door inside the named background
scope ``calendar.agenda_read``. A source that fails is reported with its state (never as an
empty calendar); the other sources still answer. Event text is untrusted data rendered as
text, and a join address is shown only when it is https.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

AGENDA_READ_SCOPE_NAME = "calendar.agenda_read"

#: Bounded by design: one agenda answers from at most this many events per source, and a
#: search reads at most this window, so a chat turn cannot be turned into an unbounded crawl.
MAX_EVENTS_PER_SOURCE = 200
SEARCH_DAYS_BACK = 7
SEARCH_DAYS_AHEAD = 60
MAX_SEARCH_RESULTS = 20

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_WORDS = {**{name: number for name, number in _MONTHS.items()},
                **{name[:3]: number for name, number in _MONTHS.items()}}


@dataclass(frozen=True)
class AgendaSource:
    """One selected calendar on one account, ready to be read."""

    account_id: str
    provider: str
    base_url: str
    auth_binding: str
    account_label: str
    calendar_id: str
    calendar_name: str

    @property
    def label(self) -> str:
        return f"{self.calendar_name} ({self.account_label})" if self.account_label else self.calendar_name


class SelectionsUnavailableError(RuntimeError):
    """The selections store cannot be read, so the chosen-calendar set is UNKNOWN.

    Distinct from an empty selection set: corruption, a lock, or an unreadable store proves
    nothing about what the owner chose. Consumers must refuse to claim availability or admit
    effects over an unknown set -- never treat it as "no calendars selected".
    """


def selected_sources(*, get_connection_fn: Callable[[], Any] | None = None) -> list[AgendaSource]:
    """Every calendar the owner opted in to: account sync on, calendar selected and present.

    A store verified to predate the selections schema (the one honest "no selections" case:
    `no such table`) reads as empty, keeping legacy single-provider runtimes working. Any OTHER
    read failure raises :class:`SelectionsUnavailableError` -- the set is unknown, not empty.
    """
    import sqlite3

    from core.operator import calendar_accounts

    kwargs = {} if get_connection_fn is None else {"get_connection_fn": get_connection_fn}
    try:
        accounts = calendar_accounts.list_accounts(**kwargs)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []  # verified pre-selections store: no selections exist
        raise SelectionsUnavailableError(f"the calendar selections store is unreadable ({exc})") from None
    except sqlite3.Error as exc:
        raise SelectionsUnavailableError(f"the calendar selections store is unreadable ({exc})") from None
    sources: list[AgendaSource] = []
    for account in accounts:
        if not account.get("sync_enabled"):
            continue
        if str(account.get("status") or "") == "disconnected":
            continue
        for row in calendar_accounts.list_selections(str(account["account_id"]), **kwargs):
            if row.get("selected") and row.get("present", True):
                sources.append(AgendaSource(
                    account_id=str(account["account_id"]),
                    provider=str(account.get("provider") or ""),
                    base_url=str(account.get("base_url") or ""),
                    auth_binding=str(account.get("auth_binding") or ""),
                    account_label=str(account.get("label") or ""),
                    calendar_id=str(row.get("calendar_id") or ""),
                    calendar_name=str(row.get("display_name") or row.get("calendar_id") or ""),
                ))
    return sources


def _build_adapter(source: AgendaSource) -> Any:
    from core.kas.registry import calendar_adapter

    return calendar_adapter(
        source.provider,
        base_url=source.base_url or "eventkit://local",
        auth_binding=source.auth_binding,
        options={"timeout": "20"},
    )


def _read_source(source: AgendaSource, *, start_utc: str, end_utc: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """One source's events in range, as presenter-ready rows, or its typed failure."""
    from core.operator.calendar_accounts import classify_calendar_error

    try:
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope(AGENDA_READ_SCOPE_NAME):
            adapter = _build_adapter(source)
            events = adapter.events_in_range(source.calendar_id, start_utc=start_utc, end_utc=end_utc)[:limit]
    except Exception as exc:
        status, detail = classify_calendar_error(exc)
        return [], {"source": source.label, "status": status, "detail": detail}
    return [_entry(event, source) for event in events], None


def _entry(event: Any, source: AgendaSource) -> dict[str, Any]:
    meeting_url = str(getattr(event, "meeting_url", "") or "")
    provider_url = str(getattr(event, "provider_url", "") or getattr(event, "html_link", "") or "")
    return {
        "uid": str(getattr(event, "uid", "") or ""),
        "summary": str(getattr(event, "summary", "") or ""),
        "all_day": bool(getattr(event, "all_day", False)),
        "start_utc": str(getattr(event, "start_utc", "") or ""),
        "end_utc": str(getattr(event, "end_utc", "") or ""),
        "start_date": str(getattr(event, "start_date", "") or ""),
        "end_date": str(getattr(event, "end_date", "") or ""),
        "tz_name": str(getattr(event, "tz_name", "") or ""),
        "cancelled": bool(getattr(event, "cancelled", False)),
        "location": str(getattr(event, "location", "") or ""),
        "meeting_url": meeting_url if meeting_url.startswith("https://") else "",
        "provider_url": provider_url if provider_url.startswith("https://") else "",
        "calendar": source.calendar_name,
        "account": source.account_label,
    }


def agenda(*, start_utc: datetime, end_utc: datetime, limit: int = MAX_EVENTS_PER_SOURCE,
           get_connection_fn: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Events across every opted-in selected calendar, sorted, bounded, with per-source failures."""
    sources = selected_sources(get_connection_fn=get_connection_fn)
    if not sources:
        # Legacy setups have no Settings choice yet: the read configuration (environment)
        # still answers, as one source, so an agenda does not refuse while migration is pending.
        from core.operator.calendar_provider import load_read_provider_config

        config = load_read_provider_config()
        if config is not None and config.configured:
            sources = [AgendaSource(account_id="", provider=config.provider, base_url=config.base_url,
                                    auth_binding=config.auth_binding, account_label="configured provider",
                                    calendar_id=config.calendar_id, calendar_name=config.calendar_id or "the configured calendar")]
    if not sources:
        return {"ok": False, "reason": "no_calendars_selected",
                "detail": "No calendars are chosen for sync in Settings yet, so there is no agenda to read."}
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for source in sources:
        rows, failure = _read_source(source, start_utc=start_utc.isoformat(), end_utc=end_utc.isoformat(), limit=limit)
        entries.extend(rows)
        if failure is not None:
            failures.append(failure)
    entries.sort(key=lambda row: (row["start_date"] or row["start_utc"] or "", row["start_utc"]))
    return {"ok": bool(entries) or not failures, "entries": entries, "sources": [s.label for s in sources],
            "failures": failures}


def search(*, term: str, now_utc: datetime, days_back: int = SEARCH_DAYS_BACK, days_ahead: int = SEARCH_DAYS_AHEAD,
           limit: int = MAX_SEARCH_RESULTS, get_connection_fn: Callable[[], Any] | None = None) -> dict[str, Any]:
    """A bounded title/description search across the opted-in selected calendars."""
    wanted = str(term or "").strip().casefold()
    if not wanted:
        return {"ok": False, "reason": "empty_query", "detail": "Which event should I search for?"}
    window = agenda(start_utc=now_utc - timedelta(days=days_back), end_utc=now_utc + timedelta(days=days_ahead),
                    get_connection_fn=get_connection_fn)
    if not window.get("ok") and not window.get("entries"):
        return window
    matches = [row for row in window["entries"] if wanted in row["summary"].casefold() or wanted in str(row.get("location") or "").casefold()][:limit]
    return {"ok": True, "entries": matches, "window_days_back": days_back, "window_days_ahead": days_ahead,
            "sources": window["sources"], "failures": window["failures"], "truncated": len(matches) >= limit}


# ---------------------------------------------------------------------------
# The agenda window a request names, as UTC bounds in the user's zone
# ---------------------------------------------------------------------------


def parse_agenda_window(
    text: str, *, now_fn: Callable[[], Any], zone_name: str | None = None
) -> tuple[datetime | None, datetime | None, str]:
    """(start_utc, end_utc, what_was_understood) for an agenda request, or (None, None, why not).

    Understands today, tomorrow, this/next week, a weekday, one date, and 'from X to/until/through Y'.
    The zone is the user's stored zone; an unreadable request is a typed question, never a default range.
    `zone_name` stands in for the stored zone when a caller reads the request apart from this machine's settings.
    """
    from core.user_preferences import load_user_timezone

    lowered = str(text or "").lower()
    zone_name = zone_name or load_user_timezone() or "UTC"
    zone = ZoneInfo(zone_name)
    now = now_fn().astimezone(timezone.utc).astimezone(zone)
    today = now.date()

    def _bounds(start_date, end_date):
        start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=zone)
        end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=zone) + timedelta(days=1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    if re.search(r"\bthis\s+week\b", lowered):
        start = today - timedelta(days=today.weekday())
        return (*_bounds(start, start + timedelta(days=6)), "this week")
    if re.search(r"\bnext\s+week\b", lowered):
        start = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return (*_bounds(start, start + timedelta(days=6)), "next week")

    range_match = re.search(
        r"\bfrom\s+([0-9]{4}-[0-9]{2}-[0-9]{2})\s+(?:to|until|through|thru)\s+([0-9]{4}-[0-9]{2}-[0-9]{2})\b",
        lowered,
    )
    if range_match:
        try:
            first = datetime.strptime(range_match.group(1), "%Y-%m-%d").date()
            last = datetime.strptime(range_match.group(2), "%Y-%m-%d").date()
        except ValueError:
            return None, None, "Those dates could not be read; use for example 'from 2026-09-24 to 2026-09-26'."
        if last < first:
            return None, None, "The end of the range is before its start."
        return (*_bounds(first, last), f"{first} to {last}")

    if re.search(r"\btoday\b", lowered):
        return (*_bounds(today, today), "today")
    if re.search(r"\btomorrow\b", lowered):
        return (*_bounds(today + timedelta(days=1), today + timedelta(days=1)), "tomorrow")
    weekday = next((word for word in _WEEKDAYS if re.search(rf"\b{word}\b", lowered)), None)
    if weekday is not None:
        days_ahead = (_WEEKDAYS.index(weekday) - today.weekday()) % 7 or 7
        target = today + timedelta(days=days_ahead)
        return (*_bounds(target, target), weekday)
    date = _explicit_date(lowered, now_fn=now_fn)
    if date is not None:
        return (*_bounds(date, date), str(date))
    return None, None, "Which days should the agenda cover? For example 'today', 'this week', 'on Friday', or 'from 2026-09-24 to 2026-09-26'."


def _explicit_date(lowered: str, *, now_fn: Callable[[], Any]):
    """An ISO date, or a 'month day' / 'day month' phrase, as a date object."""
    iso = re.search(r"\b([0-9]{4})-([0-9]{2})-([0-9]{2})\b", lowered)
    if iso:
        try:
            from datetime import date as _date

            return _date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    match = re.search(r"\b([a-z]{3,9})\.?\s+([0-9]{1,2})\b|\b([0-9]{1,2})\s+([a-z]{3,9})\.?\b", lowered)
    if not match:
        return None
    month_word = match.group(1) or match.group(4)
    day = int(match.group(2) or match.group(3))
    month = _MONTH_WORDS.get(month_word)
    if month is None or not 1 <= day <= 31:
        return None
    from datetime import date as _date

    today = now_fn().astimezone(timezone.utc).date()
    try:
        candidate = _date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today - timedelta(days=350):
        candidate = _date(today.year + 1, month, day)
    return candidate


def busy_cal_events(*, start_utc: datetime, end_utc: datetime,
                    exclude: "AgendaSource | None" = None,
                    get_connection_fn: Callable[[], Any] | None = None) -> tuple[list[Any], list[dict[str, Any]], list[AgendaSource]]:
    """(events, failures, sources) across the opted-in selected calendars, for free/busy math.

    ``exclude`` drops one source the caller already read itself, so the create target's
    events are not counted twice. Returns the adapters' own CalEvent objects.
    """
    from core.operator.calendar_accounts import classify_calendar_error

    sources = [source for source in selected_sources(get_connection_fn=get_connection_fn)
               if exclude is None or (source.provider, source.base_url.rstrip("/"), source.calendar_id)
               != (exclude.provider, exclude.base_url.rstrip("/"), exclude.calendar_id)]
    events: list[Any] = []
    failures: list[dict[str, Any]] = []
    for source in sources:
        try:
            from core.effect_gateway import named_background_effect_scope

            with named_background_effect_scope(AGENDA_READ_SCOPE_NAME):
                adapter = _build_adapter(source)
                events.extend(adapter.events_in_range(source.calendar_id, start_utc=start_utc.isoformat(), end_utc=end_utc.isoformat()))
        except Exception as exc:
            status, detail = classify_calendar_error(exc)
            failures.append({"source": source.label, "status": status, "detail": detail})
    return events, failures, sources


__all__ = ["SelectionsUnavailableError",
    "AGENDA_READ_SCOPE_NAME", "MAX_EVENTS_PER_SOURCE", "MAX_SEARCH_RESULTS", "SEARCH_DAYS_AHEAD", "SEARCH_DAYS_BACK",
    "AgendaSource", "agenda", "busy_cal_events", "parse_agenda_window", "search", "selected_sources",
]
