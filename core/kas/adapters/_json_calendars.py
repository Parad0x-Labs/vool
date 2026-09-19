"""Shared JSON-event translation for the REST calendar providers (Google, Microsoft).

Translation only: no I/O, no policy, no environment. Both providers speak JSON events
with the same bones (id, summary, start/end as dateTime+offset or date, etag, description,
recurrence markers), so the field mapping lives here ONCE and each adapter owns only its
wire shapes. Text fields are UNTRUSTED DATA and pass through verbatim.

Reading is strict on purpose: a reply that is not a collection, a row that is not an event, and an
event whose identity or times cannot be read are the typed ``CalendarReadUnusableError``. Such data
is never skipped, so it can never make a calendar look empty or a slot look free.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.kas.contract import CalendarReadUnusableError, CalEvent, KasResponse


def json_body(response: KasResponse, *, what: str) -> Any:
    """The decoded JSON of a success reply; a body that is not JSON is the typed unreadable answer."""
    try:
        return response.json()
    except ValueError:
        raise CalendarReadUnusableError(int(response.status), detail=f"{what}: the reply is not JSON") from None


def collection_rows(payload: Any, *, key: str, status: int, what: str, kind: str = "") -> list[dict[str, Any]]:
    """The rows of one collection page: a list of objects under ``key`` (Graph ``value``, Google ``items``).

    A Google page that names its own collection ``kind`` and carries no ``items`` is an empty page
    (the API's client idiom reads a missing list as no rows). Any other shape is not a readable
    collection and is never read as an empty one.
    """
    if not isinstance(payload, dict):
        raise CalendarReadUnusableError(status, detail=f"{what}: the reply is not a collection")
    if key not in payload:
        if kind and payload.get("kind") == kind:
            return []
        raise CalendarReadUnusableError(status, detail=f"{what}: the reply carries no {key} list")
    rows = payload[key]
    if not isinstance(rows, list):
        raise CalendarReadUnusableError(status, detail=f"{what}: {key} is not a list")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise CalendarReadUnusableError(status, detail=f"{what}: row {index} is not an object")
    return rows


def _zone(name: str) -> Any:
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _when(block: Any, *, side: str) -> tuple[str, str, str]:
    """One Google/Graph start/end block -> (iso_utc, iso_date, tz_name); exactly one of the first two is set.

    ``ValueError`` names what makes the block unusable.
    """
    if not isinstance(block, dict):
        raise ValueError(f"the event has no {side}")
    if block.get("date"):
        try:
            return "", date.fromisoformat(str(block["date"])).isoformat(), ""
        except ValueError:
            raise ValueError(f"the event's {side} date is not a readable date") from None
    raw = block.get("dateTime")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"the event's {side} has neither a date nor a time")
    tz_name = str(block.get("timeZone") or "")
    try:
        instant = datetime.fromisoformat(raw.strip())
    except ValueError:
        raise ValueError(f"the event's {side} is not a readable time") from None
    if instant.tzinfo is None:
        # Graph serves local wall times with the zone named beside them (UTC unless a Prefer header
        # asked for another); a wall time without a zone this runtime can resolve cannot be placed.
        zone = _zone(tz_name) if tz_name else None
        if zone is None:
            raise ValueError(f"the event's {side} is a local time without a resolvable zone")
        instant = instant.replace(tzinfo=zone)
    return instant.astimezone(timezone.utc).isoformat(), "", tz_name


def _all_day_date(block: Any, *, side: str) -> str:
    """Graph all-day: the calendar date of a midnight ``dateTime`` (or a bare ``date``)."""
    raw = ""
    if isinstance(block, dict):
        raw = str(block.get("dateTime") or block.get("date") or "")
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        raise ValueError(f"the all-day event's {side} is not a readable date") from None


def _all_day_zone(block: Any) -> str:
    if not isinstance(block, dict) or not block.get("dateTime"):
        return ""
    name = str(block.get("timeZone") or "")
    return name if name and _zone(name) is not None else ""


def _meeting_url(payload: dict[str, Any]) -> str:
    """The join address the provider stores, or "".

    Microsoft Graph documents ``onlineMeeting.joinUrl`` as the join address (``onlineMeetingUrl`` is the older
    property it plans to retire). Google documents ``hangoutLink`` and ``conferenceData.entryPoints[]``, where a
    ``video`` entry point's ``uri`` is an http(s) address. An https value comes before an http one, and only
    http(s) values are returned; a presenter decides whether to show one.
    """
    candidates: list[Any] = []
    online = payload.get("onlineMeeting")
    if isinstance(online, dict):
        candidates.append(online.get("joinUrl"))
    candidates.append(payload.get("onlineMeetingUrl"))
    candidates.append(payload.get("hangoutLink"))
    conference = payload.get("conferenceData")
    if isinstance(conference, dict):
        for entry in conference.get("entryPoints") or []:
            if isinstance(entry, dict) and entry.get("entryPointType") == "video":
                candidates.append(entry.get("uri"))
    texts = [value.strip() for value in candidates if isinstance(value, str) and value.strip()]
    for scheme in ("https://", "http://"):
        for text in texts:
            if text.lower().startswith(scheme):
                return text
    return ""


def _event(payload: Any, *, provider_id: str, calendar_id: str, etag: str, href: str) -> CalEvent:
    """The strict translation; ``ValueError`` names what makes the event unusable."""
    if not isinstance(payload, dict):
        raise ValueError("the event is not an object")
    raw_uid = payload.get("id")
    uid = raw_uid.strip() if isinstance(raw_uid, str) else ""
    if not uid:
        raise ValueError("the event has no id")
    start, end = payload.get("start"), payload.get("end")
    if bool(payload.get("isAllDay")):
        # Graph marks all-day with isAllDay and midnight dateTimes: the dates are the
        # event's own calendar dates, END exclusive; never a timed instant.
        start_utc = end_utc = ""
        start_date, end_date = _all_day_date(start, side="start"), _all_day_date(end, side="end")
        all_day, tz_name = True, _all_day_zone(start)
    else:
        start_utc, start_date, tz_name = _when(start, side="start")
        end_utc, end_date, _end_zone = _when(end, side="end")
        all_day = bool(start_date)
        if bool(end_date) != all_day:
            raise ValueError("the event's start and end disagree on whether it is all-day")
    if all_day:
        if end_date <= start_date:
            raise ValueError("the all-day event ends on or before the day it starts")
    elif datetime.fromisoformat(end_utc) < datetime.fromisoformat(start_utc):
        raise ValueError("the event ends before it starts")
    body = payload.get("body", {})
    description = (str(payload.get("description") or body.get("content") or "") if isinstance(body, dict)
                   else str(payload.get("description") or ""))
    place = payload.get("location")
    location = str(place.get("displayName") or "") if isinstance(place, dict) else (place if isinstance(place, str) else "")
    original = payload.get("originalStartTime")
    if isinstance(original, dict):
        original_start = str(original.get("dateTime") or original.get("date") or "")
    else:
        original_start = str(payload.get("originalStart") or "") if isinstance(payload.get("originalStart"), str) else ""
    series = payload.get("recurringEventId") or payload.get("seriesMasterId")
    link = payload.get("htmlLink") or payload.get("webLink")
    # Recurrence identity, typed once here: Google states an RRULE array; Microsoft states a
    # seriesMaster type (and a recurrence object on masters). An occurrence is NOT recurring
    # itself -- it names its master through series_id.
    recurrence = payload.get("recurrence")
    recurring = bool(recurrence) or str(payload.get("type") or "") == "seriesMaster"
    attendees = []
    for entry in (payload.get("attendees") if isinstance(payload.get("attendees"), list) else []):
        if isinstance(entry, dict):
            address = entry.get("email") or (entry.get("emailAddress") or {}).get("address")
            if isinstance(address, str) and address.strip():
                attendees.append(address.strip().lower())
    return CalEvent(
        provider_id=provider_id,
        uid=uid,
        calendar_id=calendar_id,
        # OPAQUE, preserved EXACTLY as the provider wrote it: quoted ("v1"), weak
        # (W/"v1") or bare tokens all round-trip byte-for-byte. Normalizing here made
        # strict providers reject updates with 412 over an UNCHANGED event.
        etag=str(etag or ""),
        summary=str(payload.get("summary") or payload.get("subject") or ""),
        start_utc=start_utc,
        end_utc=end_utc,
        all_day=all_day,
        start_date=start_date,
        end_date=end_date,
        tz_name=tz_name,
        description=description,
        href=str(href or ""),
        recurring=recurring,
        attendees=tuple(attendees),
        raw_component=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        correlation_id=str(payload.get("transactionId") or ""),
        location=location,
        meeting_url=_meeting_url(payload),
        web_url=link.strip() if isinstance(link, str) else "",
        cancelled=str(payload.get("status") or "").lower() == "cancelled" or payload.get("isCancelled") is True,
        series_id=series if isinstance(series, str) else "",
        original_start=original_start,
    )


def event_from_json(payload: Any, *, provider_id: str, calendar_id: str, status: int, etag_key: str, href_key: str, what: str) -> CalEvent:
    """One event a provider served -> CalEvent, or the typed unreadable answer naming why."""
    fields = payload if isinstance(payload, dict) else {}
    try:
        return _event(payload, provider_id=provider_id, calendar_id=calendar_id,
                      etag=str(fields.get(etag_key) or ""), href=str(fields.get(href_key) or ""))
    except ValueError as exc:
        raise CalendarReadUnusableError(status, detail=f"{what}: {exc}") from None


def json_event_to_cal(payload: Any, *, provider_id: str, calendar_id: str, etag: str, href: str) -> CalEvent | None:
    """One provider JSON event -> the shared CalEvent vocabulary, or None if unusable.

    The same strict reading the adapters use through :func:`event_from_json`, which raises the typed
    answer instead of returning None.
    """
    try:
        return _event(payload, provider_id=provider_id, calendar_id=calendar_id, etag=etag, href=href)
    except ValueError:
        return None


def cal_to_json(event: CalEvent, *, uid: str, provider: str) -> dict[str, Any]:
    """The shared vocabulary -> one provider's create/update body.

    Times round-trip as UTC instants (offset form) unless the event is all-day; the
    provider renders in whatever zone the UI asks for. Recurrence rules, attendees and
    provider extensions ride on ``raw_component``-derived fields ONLY on update, where
    the adapter patches; a create from the vocabulary is intentionally minimal.
    """
    body: dict[str, Any] = {"id": uid} if uid else {}
    summary_key = "summary" if provider == "google" else "subject"
    body[summary_key] = str(event.summary or "")
    if event.all_day and event.start_date:
        end = event.end_date or (datetime.fromisoformat(event.start_date) + timedelta(days=1)).date().isoformat()
        if provider == "graph":
            # Graph has no bare date form: all-day events are dateTime at midnight in a
            # named zone with isAllDay=true, and the END stays the EXCLUSIVE date.
            zone = event.tz_name or "UTC"
            body["isAllDay"] = True
            body["start"] = {"dateTime": f"{event.start_date}T00:00:00.0000000", "timeZone": zone}
            body["end"] = {"dateTime": f"{end}T00:00:00.0000000", "timeZone": zone}
        else:
            body["start"] = {"date": event.start_date}
            body["end"] = {"date": end}
    else:
        start = datetime.fromisoformat(str(event.start_utc).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(event.end_utc).replace("Z", "+00:00")) if event.end_utc else start
        block = {"dateTime": start.astimezone(timezone.utc).isoformat(timespec="seconds"), "timeZone": "UTC"}
        end_block = {"dateTime": end.astimezone(timezone.utc).isoformat(timespec="seconds"), "timeZone": "UTC"}
        if event.tz_name:
            from zoneinfo import ZoneInfo

            try:
                zone = ZoneInfo(event.tz_name)
                block = {"dateTime": start.astimezone(zone).isoformat(timespec="seconds"), "timeZone": event.tz_name}
                end_block = {"dateTime": end.astimezone(zone).isoformat(timespec="seconds"), "timeZone": event.tz_name}
            except Exception:
                pass
        body["start"] = block
        body["end"] = end_block
    if event.description:
        if provider == "graph":
            body["body"] = {"contentType": "text", "content": str(event.description)}
        else:
            body["description"] = str(event.description)
    if event.attendees:
        if provider == "graph":
            # Graph: attendees carry an emailAddress object; creating with attendees makes
            # Microsoft send the invitations (current documented behavior).
            body["attendees"] = [{"emailAddress": {"address": address}, "type": "required"} for address in event.attendees]
        else:
            # Google: attendee objects with an email; creating with attendees makes Google
            # email the invitations (current documented behavior).
            body["attendees"] = [{"email": address} for address in event.attendees]
    if str(event.recurrence_rule or "").strip():
        body["recurrence"] = _recurrence_json(str(event.recurrence_rule).strip(), provider=provider,
                                              event=event)
    return body


def _recurrence_json(rule: str, *, provider: str, event: CalEvent) -> Any:
    """One basic RRULE -> the provider's create-body form.

    Google takes the RRULE line verbatim. Microsoft Graph has no RRULE: its create body
    carries a pattern/range object (current official semantics: pattern types daily, weekly
    with daysOfWeek, absoluteMonthly with dayOfMonth; range type noEnd starts at the first
    occurrence's date). A rule outside the basic shapes is refused rather than approximated.
    """

    if provider != "graph":
        return [f"RRULE:{rule}"]
    parts = dict(item.split("=", 1) for item in rule.split(";") if "=" in item)
    freq = str(parts.get("FREQ") or "").upper()
    start_date = ""
    if event.start_utc:
        try:
            start_date = datetime.fromisoformat(str(event.start_utc).replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            start_date = ""
    elif event.start_date:
        start_date = str(event.start_date)
    if not start_date:
        raise ValueError("a recurring event needs a start to anchor its range")
    if freq == "DAILY" and set(parts) == {"FREQ"}:
        pattern = {"type": "daily", "interval": 1}
    elif freq == "WEEKLY" and set(parts) <= {"FREQ", "BYDAY"}:
        _byday = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday",
                  "FR": "Friday", "SA": "Saturday", "SU": "Sunday",
                  "MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
                  "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"}
        raw_days = [day.strip().upper() for day in str(parts.get("BYDAY") or "").split(",") if day.strip()]
        days = [_byday[day] for day in raw_days if day in _byday]
        if not days or len(days) != len(raw_days):
            raise ValueError(f"weekly recurrence needs weekday names: {rule}")
        pattern = {"type": "weekly", "interval": 1, "daysOfWeek": days}
    elif freq == "MONTHLY" and set(parts) <= {"FREQ", "BYMONTHDAY"}:
        day = int(parts.get("BYMONTHDAY") or 0)
        if not 1 <= day <= 31:
            raise ValueError(f"monthly recurrence needs a day of month 1-31: {rule}")
        pattern = {"type": "absoluteMonthly", "interval": 1, "dayOfMonth": day}
    else:
        raise ValueError(f"this recurrence shape is not supported for Microsoft calendars: {rule}")
    return {"pattern": pattern, "range": {"type": "noEnd", "startDate": start_date}}


__all__ = ["cal_to_json", "collection_rows", "event_from_json", "json_body", "json_event_to_cal"]
