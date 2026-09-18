from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_ISO_DATETIME_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2}))?\b")
_DURATION_RE = re.compile(r"\bfor\s+(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b", re.IGNORECASE)


def parse_calendar_request(
    text: str,
    *,
    extract_quoted_values_fn: Any,
    data_path_fn: Any,
    now_fn: Any,
) -> dict[str, Any] | None:
    quoted_values = extract_quoted_values_fn(text)
    title = quoted_values[0] if quoted_values else "VOOL Meeting"
    now = now_fn()
    start_dt: datetime | None = None

    iso_match = _ISO_DATETIME_RE.search(text)
    if iso_match:
        date_part = iso_match.group(1)
        time_part = iso_match.group(2) or "09:00"
        start_dt = datetime.fromisoformat(f"{date_part}T{time_part}").replace(tzinfo=now.tzinfo)
    else:
        time_match = _TIME_RE.search(text)
        if time_match and ("today" in text.lower() or "tomorrow" in text.lower()):
            hour = int(time_match.group(1))
            minute = int(time_match.group(2) or 0)
            meridiem = str(time_match.group(3) or "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            target_day = now.date() + timedelta(days=1 if "tomorrow" in text.lower() else 0)
            start_dt = datetime.combine(target_day, datetime.min.time(), tzinfo=now.tzinfo).replace(
                hour=hour,
                minute=minute,
            )
    if start_dt is None:
        return None

    duration_match = _DURATION_RE.search(text)
    duration_value = int(duration_match.group(1)) if duration_match else 30
    duration_unit = str(duration_match.group(2) or "m").lower() if duration_match else "m"
    duration_minutes = duration_value * 60 if duration_unit.startswith("h") else duration_value
    end_dt = start_dt + timedelta(minutes=max(15, duration_minutes))
    outbox_dir = data_path_fn("calendar_outbox")
    return {
        "title": title,
        "start_iso": start_dt.isoformat(),
        "end_iso": end_dt.isoformat(),
        "duration_minutes": max(15, duration_minutes),
        "outbox_dir": str(outbox_dir),
    }


def render_ics(
    *,
    title: str,
    start_iso: str,
    end_iso: str,
    uuid_fn: Any = uuid.uuid4,
    now_fn: Any = lambda: datetime.now(timezone.utc),
) -> str:
    start_dt = datetime.fromisoformat(start_iso).astimezone(timezone.utc)
    end_dt = datetime.fromisoformat(end_iso).astimezone(timezone.utc)
    uid = f"{uuid_fn()}@vool.local"
    dtstamp = now_fn().strftime("%Y%m%dT%H%M%SZ")
    start = start_dt.strftime("%Y%m%dT%H%M%SZ")
    end = end_dt.strftime("%Y%m%dT%H%M%SZ")
    safe_title = title.replace("\\", "\\\\").replace(",", r"\,").replace(";", r"\;").replace("\n", r"\n")
    return (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//VOOL//Closed Test//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:{uid}\n"
        f"DTSTAMP:{dtstamp}\n"
        f"DTSTART:{start}\n"
        f"DTEND:{end}\n"
        f"SUMMARY:{safe_title}\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
