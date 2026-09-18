"""CalDAV, as a KAS adapter: RFC 4791 wire shapes in, typed VOOL data out.

Nothing here decides anything. It has no socket, no credential, no policy and no opinion
about whether a call should happen — it builds a URL and an XML/ICAL payload, hands the
request to the transport VOOL gave it, and reads the reply into the shared calendar
vocabulary (``CalCalendar`` / ``CalEvent``). Swap it for a different CalDAV server, or a
Google/Microsoft adapter implementing the same contract, and the operator vertical above it
does not change one line — which is the point of the single contract.

Protocol truth this adapter preserves:

* discovery is PROPFIND (Depth 1) reading ``resourcetype`` for ``calendar`` collections;
* range reads are REPORT ``calendar-query`` with a ``time-range`` filter — the server, not
  the client, decides overlap;
* identity is the VEVENT ``UID`` plus its collection href, and version is the ``getetag``
  the server hands back with every representation;
* components sharing a UID live in ONE calendar object resource (RFC 4791 section 4.1): a
  series master and its RECURRENCE-ID exceptions are read and written together, as that one
  resource at its own href, never as separate resources;
* mutations are PUT/DELETE carrying ``If-Match``/``If-None-Match`` so the server — not this
  code — rejects a stale or concurrent edit (412 stays 412, surfaced as
  ``CalendarRefusedError(precondition_failed)``);
* event text (SUMMARY, DESCRIPTION) is untrusted data: parsed for transport, escaped for
  storage, and never interpreted. A description that asks for an effect stays text.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.kas.contract import (
    AdapterConfig,
    CalCalendar,
    CalendarAdapter,
    CalendarReadUnusableError,
    CalendarRefusedError,
    CalEvent,
    KasRequest,
    KasResponse,
    TransportAcceptedError,
    TransportUnknownError,
    accepted_reply_lost,
    accepted_write_error,
)
from core.kas.registry import register_adapter

_DAV = "DAV:"
_CALDAV = "urn:ietf:params:xml:ns:caldav"

_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:propfind xmlns:D="DAV:">'
    "<D:prop>"
    "<D:resourcetype/>"
    "<D:displayname/>"
    "<D:current-user-privilege-set/>"
    "</D:prop>"
    "</D:propfind>"
)

_LIST_PAGES = 5  # a calendar-server home pages its collections; bounded like every listing


def _calendar_query_body(*, start_utc: str, end_utc: str, uid: str = "", expand: bool = False) -> str:
    """A REPORT calendar-query: time-range overlap, or an exact UID when asked.

    ``expand`` asks the server (RFC 4791 CALDAV:expand) to return each RECURRENCE OCCURRENCE of
    a repeating event as its own instance carrying RECURRENCE-ID, instead of the series master
    alone. A server that cannot expand answers with an error the caller may retry unexpanded.
    """
    uid_filter = ""
    if uid:
        uid_filter = (
            "<C:prop-filter name=\"UID\">"
            f"<C:text-match collation=\"i;ascii-casemap\">{_xml_escape(uid)}</C:text-match>"
            "</C:prop-filter>"
        )
    calendar_data = (
        f'<C:calendar-data><C:expand start="{_ical_utc(start_utc)}" end="{_ical_utc(end_utc)}"/></C:calendar-data>'
        if expand else "<C:calendar-data/>"
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        "<D:prop>"
        "<D:getetag/>"
        + calendar_data +
        "</D:prop>"
        "<C:filter>"
        '<C:comp-filter name="VCALENDAR">'
        '<C:comp-filter name="VEVENT">'
        f'<C:time-range start="{_ical_utc(start_utc)}" end="{_ical_utc(end_utc)}"/>'
        f"{uid_filter}"
        "</C:comp-filter>"
        "</C:comp-filter>"
        "</C:filter>"
        "</C:calendar-query>"
    )


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ical_utc(iso_utc: str) -> str:
    instant = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ical_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ical_unescape(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            if nxt in {"n", "N"}:
                out.append("\n")
            elif nxt in {";", ",", "\\"}:
                out.append(nxt)
            else:
                out.append(nxt)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _unfold(text: str) -> list[str]:
    """RFC 5545 3.1: a CRLF followed by a space continues the line."""
    lines: list[str] = []
    for raw in str(text).replace("\r\n", "\n").split("\n"):
        if not raw:
            continue
        if raw[0] in {" ", "\t"} and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _fold(text: str) -> str:
    """RFC 5545 3.1: emit no content line longer than 75 octets."""
    if len(text.encode("utf-8")) <= 75:
        return text
    out = []
    current = ""
    for char in text:
        candidate = current + char
        if len(candidate.encode("utf-8")) > 74:  # leave room for the leading space
            out.append(current)
            current = " " + char
        else:
            current = candidate
    if current.lstrip(" ") or current.strip():
        out.append(current)
    return "\r\n".join(out)


def _split_unquoted(text: str, separator: str) -> list[str]:
    """Split on ``separator`` outside double quotes (RFC 5545 3.1: a quoted parameter value may hold ``:;,``)."""
    pieces: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in text:
        if char == '"':
            in_quotes = not in_quotes
        if char == separator and not in_quotes:
            pieces.append("".join(current))
            current = []
        else:
            current.append(char)
    pieces.append("".join(current))
    return pieces


def _split_property(line: str) -> tuple[str, dict[str, str], str]:
    """`DTSTART;TZID=Europe/Berlin:20260915T140000` -> (name, params, value).

    The value starts after the first colon outside quotes, so `LABEL="Room: 17"` stays a parameter.
    """
    name_part = _split_unquoted(line, ":")[0]
    value = line[len(name_part) + 1:] if len(name_part) < len(line) else ""
    pieces = _split_unquoted(name_part, ";")
    name = pieces[0].upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, param_value = piece.partition("=")
        params[key.upper()] = param_value.strip('"')
    return name, params, value


def _http_uri(value: str) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith(("https://", "http://")) else ""


_JOIN_FEATURES = frozenset({"VIDEO", "AUDIO", "SCREEN"})


def _conference_join_uri(conferences: list[tuple[dict[str, str], str]]) -> str:
    """RFC 7986 CONFERENCE lines -> the address to join the meeting, or "".

    A line whose FEATURE names VIDEO comes first; then one naming AUDIO or SCREEN, or naming no feature. A line
    that only names PHONE, CHAT, FEED or MODERATOR is not a join link, and neither is a tel: or xmpp: address.
    Within a rank an https address comes before an http one.
    """
    ranked: list[tuple[int, int, int, str]] = []
    for index, (params, value) in enumerate(conferences):
        uri = _http_uri(value)
        if not uri:
            continue
        features = {part.strip().upper() for part in str(params.get("FEATURE") or "").split(",") if part.strip()}
        if features and not features & _JOIN_FEATURES:
            continue
        ranked.append((0 if "VIDEO" in features else 1, 0 if uri.lower().startswith("https://") else 1, index, uri))
    return min(ranked)[3] if ranked else ""


def _stated_write_access(node: ET.Element) -> bool | None:
    """RFC 3744 DAV:current-user-privilege-set -> whether this user may add and change events, or None.

    DAV:write contains DAV:bind (add a member) and DAV:write-content; DAV:all contains every privilege. A server
    that does not serve the property (or answers it in a non-200 propstat) leaves the capability unknown.
    """
    for propstat in node.iter(f"{{{_DAV}}}propstat"):
        status = (propstat.findtext(f"{{{_DAV}}}status") or "").strip()
        if status and "200" not in status:
            continue
        privilege_set = propstat.find(f"{{{_DAV}}}prop/{{{_DAV}}}current-user-privilege-set")
        if privilege_set is not None:
            names = {child.tag.rpartition("}")[2] for privilege in privilege_set.findall(f"{{{_DAV}}}privilege") for child in privilege}
            return bool(names & {"all", "write", "bind"})
    return None


def _parse_instant(value: str, params: dict[str, str]) -> tuple[str, bool, str, str]:
    """One DTSTART/DTEND value into (iso_utc, all_day, iso_date, tz_name).

    Returns ("", False, date, zone) for all-day values and (instant, False, "", zone) for
    timed ones. A naive timed value (no Z, no TZID) keeps the VCALENDAR semantics: floating
    time, interpreted in the USER's zone at read time — the adapter cannot know that zone,
    so it reports the wall time with an empty tz_name and lets the caller decide. That is
    the honest choice: inventing UTC for a floating time would silently move the event.
    """
    value = str(value).strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        parsed = date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
        return "", True, parsed.isoformat(), ""
    if value.endswith("Z"):
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return parsed.isoformat(), False, "", "UTC"
    tz_name = str(params.get("TZID") or "")
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    if tz_name:
        try:
            aware = parsed.replace(tzinfo=ZoneInfo(tz_name))
        except (ZoneInfoNotFoundError, ValueError):
            return parsed.isoformat(), False, "", ""  # unknown zone: report the wall time, claim nothing
        return aware.astimezone(timezone.utc).isoformat(), False, "", tz_name
    return parsed.isoformat(), False, "", ""


def _render_instant(iso_utc: str, tz_name: str) -> tuple[str, str]:
    """An instant back to (ical value, params fragment). Zulu unless a named zone was kept."""
    instant = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    if tz_name and tz_name != "UTC":
        try:
            local = instant.astimezone(ZoneInfo(tz_name))
            return local.strftime("%Y%m%dT%H%M%S"), f";TZID={tz_name}"
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), ""


def _vevent_blocks(calendar_data: str) -> list[list[str]]:
    """Every VEVENT component of one calendar object, as its unfolded lines with BEGIN/END included.

    A CalDAV calendar object resource holds EVERY component sharing its UID -- a series master and each RECURRENCE-ID
    exception (RFC 4791 section 4.1) -- and an expanded reply holds one component per instance (section 9.6.5). A VEVENT
    left unterminated at the end of the data is kept as it stands, as the single-component reader before it did.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    depth = 0
    for line in _unfold(calendar_data):
        upper = line.strip().upper()
        if current is None:
            if upper == "BEGIN:VEVENT":
                current, depth = [line], 0
            continue
        current.append(line)
        if upper.startswith("BEGIN:"):
            depth += 1
        elif upper.startswith("END:"):
            if depth:
                depth -= 1
            else:
                blocks.append(current)
                current = None
    if current is not None:
        blocks.append(current)
    return blocks


def _top_properties(lines: list[str]) -> list[tuple[int, str, dict[str, str], str]]:
    """(position, NAME, params, value) of each of one component's OWN properties; a nested component's (a VALARM's) are not."""
    found: list[tuple[int, str, dict[str, str], str]] = []
    depth = 0
    last = len(lines) - 1 if len(lines) > 1 and lines[-1].strip().upper().startswith("END:") else len(lines)
    for position in range(1, last):
        upper = lines[position].strip().upper()
        if upper.startswith("BEGIN:"):
            depth += 1
        elif upper.startswith("END:"):
            depth -= 1
        elif depth == 0:
            name, params, value = _split_property(lines[position])
            found.append((position, name, params, value))
    return found


def _parse_component(lines: list[str], *, provider_id: str, calendar_id: str, href: str, etag: str) -> CalEvent:
    """One VEVENT component -- its unfolded lines -- in the shared calendar vocabulary.

    ``ValueError`` names what makes it unusable: no UID, no DTSTART, or a DTSTART/DTEND/RECURRENCE-ID that is not a
    readable date or time. A floating or unknown-zone time is not unusable here: it is reported as its wall time with
    no zone (see ``_parse_instant``) for the caller to place.
    """
    props: dict[str, tuple[dict[str, str], str]] = {}
    conferences: list[tuple[dict[str, str], str]] = []
    for _position, name, params, value in _top_properties(lines):
        if name == "CONFERENCE":  # RFC 7986: repeatable, one line per way to join
            conferences.append((params, value))
        elif name and name not in props:  # the first occurrence is the event's own value
            props[name] = (params, value)
    if not _ical_unescape(props.get("UID", ({}, ""))[1]).strip():
        raise ValueError("the event has no UID")
    start_params, start_value = props.get("DTSTART", ({}, ""))
    end_params, end_value = props.get("DTEND", ({}, ""))
    if not start_value.strip():
        raise ValueError("the event has no DTSTART")
    try:
        start_utc, all_day, start_date, start_zone = _parse_instant(start_value, start_params)
    except ValueError:
        raise ValueError("the event's DTSTART is not a readable date or time") from None
    try:
        end_utc, _, end_date, _ = _parse_instant(end_value, end_params) if end_value.strip() else ("", False, "", "")
    except ValueError:
        raise ValueError("the event's DTEND is not a readable date or time") from None
    if start_utc and not end_utc and not end_date:
        # RFC 5545 3.6.1: a timed DTSTART with no DTEND/DURATION has zero duration.
        end_utc = start_utc
    elif all_day and start_date and not end_date:
        end_date = (date.fromisoformat(start_date) + timedelta(days=1)).isoformat()
    description_params, description_value = props.get("DESCRIPTION", ({}, ""))
    del description_params
    recurrence_params, recurrence_value = props.get("RECURRENCE-ID", ({}, ""))
    original_start = ""
    if recurrence_value.strip():
        try:
            recurrence_utc, _recurrence_all_day, recurrence_date, _recurrence_zone = _parse_instant(recurrence_value, recurrence_params)
        except ValueError:
            raise ValueError("the event's RECURRENCE-ID is not a readable date or time") from None
        original_start = recurrence_utc or recurrence_date
    return CalEvent(
        provider_id=provider_id,
        uid=_ical_unescape(props["UID"][1]).strip(),
        calendar_id=calendar_id,
        # Typed recurrence identity: an RRULE line on this component means THIS object covers
        # the series (a recurrence exception carries RECURRENCE-ID instead, handled below).
        recurring="RRULE" in props,
        etag=str(etag),
        summary=_ical_unescape(props.get("SUMMARY", ({}, ""))[1]),
        start_utc=start_utc,
        end_utc=end_utc,
        all_day=all_day,
        start_date=start_date,
        end_date=end_date,
        tz_name=start_zone,
        description=_ical_unescape(description_value),
        href=href,
        raw_component="\r\n".join(lines),
        location=" ".join(_ical_unescape(props.get("LOCATION", ({}, ""))[1]).split()),
        meeting_url=_conference_join_uri(conferences),
        web_url=_http_uri(props.get("URL", ({}, ""))[1]),
        cancelled=props.get("STATUS", ({}, ""))[1].strip().upper() == "CANCELLED",
        series_id=_ical_unescape(props["UID"][1]).strip() if original_start else "",
        original_start=original_start,
    )


def _parse_vevents(calendar_data: str, *, provider_id: str, calendar_id: str, href: str, etag: str) -> list[CalEvent]:
    """Every VEVENT of one calendar object, in stored order. ``ValueError`` when it holds none or one is unusable."""
    blocks = _vevent_blocks(calendar_data)
    if not blocks:
        raise ValueError("the calendar object has no VEVENT")
    return [_parse_component(block, provider_id=provider_id, calendar_id=calendar_id, href=href, etag=etag) for block in blocks]


def _parse_vevent(calendar_data: str, *, provider_id: str, calendar_id: str, href: str, etag: str) -> CalEvent:
    """The event one calendar object stands for: its series master (the component without RECURRENCE-ID), else its first
    VEVENT. A resource keeps a master and its exceptions together (RFC 4791 section 4.1), in no prescribed order."""
    events = _parse_vevents(calendar_data, provider_id=provider_id, calendar_id=calendar_id, href=href, etag=etag)
    return next((event for event in events if not event.original_start), events[0])


# -- a recurring series inside its ONE calendar object resource (RFC 4791 section 4.1) -----------------


def _before_send(error: CalendarRefusedError) -> CalendarRefusedError:
    """Mark a refusal met BEFORE any write was sent: positive evidence that nothing crossed the provider boundary."""
    error.before_send = True
    return error


def _bare_etag(value: str) -> str:
    return str(value or "").strip().strip('"')


def _if_match(etag: str) -> str:
    """The If-Match value for a provider version. An entity-tag is a quoted string (RFC 9110 section 8.8.3), so a token
    kept without its quotes is quoted again; a tag already quoted, or weak, is sent exactly as the provider gave it."""
    text = str(etag or "").strip()
    return text if text.startswith(('"', "W/")) else f'"{text}"'


_DURATION_RE = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _ical_duration(text: str) -> timedelta:
    """An RFC 5545 section 3.3.6 DURATION value."""
    match = _DURATION_RE.match(str(text or "").strip().upper())
    if not match or not any(match.groups()[1:]):
        raise ValueError("unreadable DURATION")
    sign, weeks, days, hours, minutes, seconds = match.groups()
    span = timedelta(weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0), minutes=int(minutes or 0), seconds=int(seconds or 0))
    return -span if sign == "-" else span


def _as_utc(iso: str) -> datetime:
    instant = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return instant if instant.tzinfo is not None else instant.replace(tzinfo=timezone.utc)


def _same_original_start(left: str, right: str) -> bool:
    """Whether two original starts name the same instance: equal instants in whatever zone, or equal dates."""
    first, second = str(left or "").strip(), str(right or "").strip()
    if not first or not second:
        return False
    if len(first) <= 10 or len(second) <= 10:
        return len(first) <= 10 and len(second) <= 10 and first == second
    try:
        return _as_utc(first) == _as_utc(second)
    except ValueError:
        return False


def _names_occurrence(value: str, params: dict[str, str], original_start: str) -> bool:
    """Whether a RECURRENCE-ID or EXDATE value names the instance whose original start is ``original_start``."""
    try:
        instant, all_day, day, _zone = _parse_instant(value, params)
    except ValueError:
        return False
    return _same_original_start(day if all_day else instant, original_start)


def _logical_lines(text: str) -> list[tuple[str, str]]:
    """Each content line as (unfolded text, its exact folded form), so an untouched line is re-emitted byte-for-byte."""
    lines: list[tuple[str, str]] = []
    for raw in str(text).replace("\r\n", "\n").split("\n"):
        if not raw:
            continue
        if raw[0] in {" ", "\t"} and lines:
            unfolded, folded = lines[-1]
            lines[-1] = (unfolded + raw[1:], folded + "\r\n" + raw)
        else:
            lines.append((raw, raw))
    return lines


def _render_value(params: dict[str, str], *, instant: str, floating: bool = False) -> tuple[str, str]:
    """(parameter fragment, value) writing an instant in the zone identity an existing DTSTART/DTEND/RECURRENCE-ID uses:
    a TZID stays that zone's wall time, a floating time stays floating, anything else is written in UTC."""
    moment = _as_utc(instant)
    zone_name = str(params.get("TZID") or "")
    if zone_name:
        try:
            return f";TZID={zone_name}", moment.astimezone(ZoneInfo(zone_name)).strftime("%Y%m%dT%H%M%S")
        except (ZoneInfoNotFoundError, ValueError):
            pass
    if floating:
        return "", moment.strftime("%Y%m%dT%H%M%S")
    return "", moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _component_span(lines: list[str]) -> tuple[str, str, str, str]:
    """A component's own times: (start, end, "", "") instants for a timed one, ("", "", start day, end day) for a dated one."""
    first: dict[str, tuple[dict[str, str], str]] = {}
    for _position, name, params, value in _top_properties(lines):
        first.setdefault(name, (params, value))
    if "DTSTART" not in first:
        raise ValueError("the component has no DTSTART")
    start_utc, all_day, start_day, _zone = _parse_instant(first["DTSTART"][1], first["DTSTART"][0])
    if all_day:
        if "DTEND" in first:
            end_day = _parse_instant(first["DTEND"][1], first["DTEND"][0])[2]
        else:
            days = _ical_duration(first["DURATION"][1]).days if "DURATION" in first else 1
            end_day = (date.fromisoformat(start_day) + timedelta(days=max(days, 1))).isoformat()
        return "", "", start_day, end_day or (date.fromisoformat(start_day) + timedelta(days=1)).isoformat()
    if "DTEND" in first:
        end_utc = _parse_instant(first["DTEND"][1], first["DTEND"][0])[0]
    elif "DURATION" in first:
        end_utc = (_as_utc(start_utc) + _ical_duration(first["DURATION"][1])).isoformat()
    else:
        end_utc = start_utc
    return start_utc, end_utc, "", ""


class _SeriesResource:
    """A recurring series as its ONE calendar object resource holds it (RFC 4791 section 4.1): where it lives, its
    version, and its components.

    Editing is line-exact. The resource is kept as its top-level items in stored order, every content line with its
    folded form, and a change rewrites only the component and properties it names: the master, every other exception,
    unknown properties, nested alarms, timezone definitions and folding go back exactly as the server served them.
    """

    def __init__(self, *, href: str, etag: str, data: str, series_uid: str) -> None:
        self.href = href
        self.etag = etag
        self.series_uid = series_uid
        self.items: list[tuple[str, list[tuple[str, str]]]] = []
        block: list[tuple[str, str]] = []
        kind = ""
        depth = 0
        for unfolded, folded in _logical_lines(data):
            upper = unfolded.strip().upper()
            if depth == 0 and upper.startswith("BEGIN:") and upper != "BEGIN:VCALENDAR":
                block, kind, depth = [(unfolded, folded)], upper[len("BEGIN:"):], 1
            elif depth == 0:
                self.items.append(("", [(unfolded, folded)]))
            else:
                block.append((unfolded, folded))
                if upper.startswith("BEGIN:"):
                    depth += 1
                elif upper.startswith("END:"):
                    depth -= 1
                    if depth == 0:
                        self.items.append((kind, block))
        if depth:
            raise ValueError("a component of the series resource is not closed")
        self.events = [index for index, (item_kind, _lines) in enumerate(self.items) if item_kind == "VEVENT"]
        for index in self.events:
            uid = self.property(index, "UID")
            if uid is None or _ical_unescape(uid[3]).strip() != series_uid:
                raise ValueError("the resource holds a component that is not this series'")
        masters = [index for index in self.events if self.property(index, "RECURRENCE-ID") is None]
        if len(masters) != 1:
            raise ValueError("the resource does not hold exactly one master for the series")
        self.master = masters[0]
        if self.property(self.master, "RRULE") is None:
            raise ValueError("the series master carries no recurrence rule")

    def lines(self, index: int) -> list[str]:
        return [unfolded for unfolded, _folded in self.items[index][1]]

    def properties(self, index: int) -> list[tuple[int, str, dict[str, str], str]]:
        return _top_properties(self.lines(index))

    def property(self, index: int, name: str) -> tuple[int, str, dict[str, str], str] | None:
        return next((prop for prop in self.properties(index) if prop[1] == name), None)

    def exception(self, original_start: str) -> int | None:
        """The item index of THAT instance's RECURRENCE-ID exception, when the resource holds one."""
        found = []
        for index in self.events:
            recurrence = self.property(index, "RECURRENCE-ID")
            if recurrence is not None and _names_occurrence(recurrence[3], recurrence[2], original_start):
                found.append(index)
        if len(found) > 1:
            raise ValueError("two exceptions in the series resource claim the same RECURRENCE-ID")
        return found[0] if found else None

    def cancellation(self, original_start: str) -> str:
        """POSITIVE evidence that instance is cancelled in the stored series: "exception" (its RECURRENCE-ID exception
        carries STATUS:CANCELLED), "exdate" (the master's EXDATE excludes it), or "" (neither)."""
        index = self.exception(original_start)
        if index is not None:
            status = self.property(index, "STATUS")
            return "exception" if status is not None and status[3].strip().upper() == "CANCELLED" else ""
        for _position, name, params, value in self.properties(self.master):
            if name == "EXDATE" and any(_names_occurrence(chunk, params, original_start) for chunk in value.split(",") if chunk.strip()):
                return "exdate"
        return ""

    def slot(self, original_start: str) -> tuple[str, str]:
        """The UTC interval that instance occupies now: its exception's own times, else its original slot (the master's
        length from its original start)."""
        index = self.exception(original_start)
        if index is not None:
            start_utc, end_utc, start_day, end_day = _component_span(self.lines(index))
            if start_day:
                return f"{start_day}T00:00:00+00:00", f"{end_day}T00:00:00+00:00"
            return start_utc, end_utc
        start_utc, end_utc, start_day, end_day = _component_span(self.lines(self.master))
        if start_day:
            days = (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days or 1
            begin = date.fromisoformat(str(original_start)[:10])
            return f"{begin.isoformat()}T00:00:00+00:00", f"{(begin + timedelta(days=days)).isoformat()}T00:00:00+00:00"
        begin_at = _as_utc(original_start)
        return begin_at.isoformat(), (begin_at + (_as_utc(end_utc) - _as_utc(start_utc))).isoformat()

    def render(self, original_start: str, *, moved: CalEvent | None = None, cancel: bool = False) -> str:
        """The resource with ONLY that instance changed -- its existing exception edited in place, or a new exception
        derived from the master added after the last VEVENT -- and every other item exactly as stored."""
        items = list(self.items)
        index = self.exception(original_start)
        if index is not None:
            items[index] = ("VEVENT", self._edited(index, original_start, moved=moved, cancel=cancel))
        else:
            items.insert(self.events[-1] + 1, ("VEVENT", self._derived(original_start, moved=moved, cancel=cancel)))
        return "\r\n".join(folded for _kind, lines in items for _unfolded, folded in lines) + "\r\n"

    def _times(self, template: int, original_start: str, moved: CalEvent | None) -> list[str]:
        """DTSTART/DTEND of the instance -- its current slot, or the approved move -- in the value type and zone identity
        the template component's own DTSTART/DTEND are written in (a dated series stays dated)."""
        first: dict[str, tuple[dict[str, str], str]] = {}
        for _position, name, params, value in self.properties(template):
            first.setdefault(name, (params, value))
        start_params, start_value = first["DTSTART"]
        end_params = first["DTEND"][0] if "DTEND" in first else start_params
        slot_start, slot_end = self.slot(original_start)
        if _parse_instant(start_value, start_params)[1]:
            days = (date.fromisoformat(slot_end[:10]) - date.fromisoformat(slot_start[:10])).days or 1
            begin = (moved.start_date or _as_utc(moved.start_utc).date().isoformat()) if moved is not None else slot_start[:10]
            finish = (date.fromisoformat(begin) + timedelta(days=days)).isoformat()
            return [f"DTSTART;VALUE=DATE:{begin.replace('-', '')}", f"DTEND;VALUE=DATE:{finish.replace('-', '')}"]
        floating = not start_value.strip().endswith("Z") and not start_params.get("TZID")
        begin_at, finish_at = (moved.start_utc, moved.end_utc) if moved is not None else (slot_start, slot_end)
        start_fragment, start_text = _render_value(start_params, instant=begin_at, floating=floating)
        end_fragment, end_text = _render_value(end_params, instant=finish_at, floating=floating)
        return [f"DTSTART{start_fragment}:{start_text}", f"DTEND{end_fragment}:{end_text}"]

    def _derived(self, original_start: str, *, moved: CalEvent | None, cancel: bool) -> list[tuple[str, str]]:
        """A new RECURRENCE-ID exception for that instance, derived from the master: the master's own properties,
        attendees and alarms, without its recurrence-set properties, at the instance's identity and times. The
        RECURRENCE-ID takes the master DTSTART's value type and zone (RFC 5545 section 3.8.4.4)."""
        start = self.property(self.master, "DTSTART")
        if start is None:
            raise ValueError("the series master has no DTSTART")
        _position, _name, start_params, start_value = start
        if _parse_instant(start_value, start_params)[1]:
            recurrence = f"RECURRENCE-ID;VALUE=DATE:{str(original_start)[:10].replace('-', '')}"
        else:
            floating = not start_value.strip().endswith("Z") and not start_params.get("TZID")
            fragment, text = _render_value(start_params, instant=original_start, floating=floating)
            recurrence = f"RECURRENCE-ID{fragment}:{text}"
        added = [f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", recurrence,
                 *self._times(self.master, original_start, moved)]
        skipped = {"RRULE", "RDATE", "EXRULE", "EXDATE", "DTSTART", "DTEND", "DURATION", "RECURRENCE-ID", "DTSTAMP"}
        if cancel:
            added.append("STATUS:CANCELLED")
            skipped.add("STATUS")
        summary = self.property(self.master, "SUMMARY")
        if moved is not None and moved.summary and (summary is None or _ical_unescape(summary[3]) != moved.summary):
            added.append(f"SUMMARY:{_ical_escape(moved.summary)}")
            skipped.add("SUMMARY")
        own = {position for position, name, _params, _value in self.properties(self.master) if name in skipped}
        master_lines = self.items[self.master][1]
        block = [master_lines[0], *((line, _fold(line)) for line in added)]
        block.extend(line for position, line in enumerate(master_lines[1:-1], start=1) if position not in own)
        block.append(master_lines[-1])
        return block

    def _edited(self, index: int, original_start: str, *, moved: CalEvent | None, cancel: bool) -> list[tuple[str, str]]:
        """That instance's existing exception with only the approved properties rewritten in place. New properties go
        before any nested component, where RFC 5545 section 3.6.1 places a component's own properties."""
        lines = self.items[index][1]
        first: dict[str, tuple[int, str]] = {}
        for position, name, _params, value in self.properties(index):
            first.setdefault(name, (position, value))
        replaced: dict[int, str] = {}
        dropped: set[int] = set()
        added: list[str] = []
        if cancel:
            if "STATUS" in first:
                replaced[first["STATUS"][0]] = "STATUS:CANCELLED"
            else:
                added.append("STATUS:CANCELLED")
        if moved is not None:
            start_line, end_line = self._times(index, original_start, moved)
            replaced[first["DTSTART"][0]] = start_line
            if "DTEND" in first:
                replaced[first["DTEND"][0]] = end_line
            else:
                added.append(end_line)
            if "DURATION" in first:
                dropped.add(first["DURATION"][0])
            if moved.summary and "SUMMARY" in first and _ical_unescape(first["SUMMARY"][1]) != moved.summary:
                replaced[first["SUMMARY"][0]] = f"SUMMARY:{_ical_escape(moved.summary)}"
        block = [lines[0], *((line, _fold(line)) for line in added)]
        for position in range(1, len(lines) - 1):
            if position in dropped:
                continue
            block.append((replaced[position], _fold(replaced[position])) if position in replaced else lines[position])
        block.append(lines[-1])
        return block


def _header(response: KasResponse, name: str) -> str:
    wanted = str(name).lower()
    for key, value in dict(response.headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


#: CalDAV preconditions (RFC 4791 sections 1.3 and 5.3.2.1) a refusal names in its DAV:error body, as refusal reasons.
_PRECONDITION_REASONS = {
    "no-uid-conflict": "uid_conflict",
    "valid-calendar-object-resource": "invalid_calendar_object",
    "valid-calendar-data": "invalid_calendar_data",
    "supported-calendar-component": "unsupported_calendar_component",
}


def _refusal(response: KasResponse, *, purpose: str) -> CalendarRefusedError:
    status = int(response.status)
    reason = {404: "not_found", 410: "not_found", 412: "precondition_failed", 429: "rate_limited"}.get(status, f"http_{status}")
    if status in {403, 409}:
        # A failed CalDAV precondition names itself -- and, for a UID conflict, the resource already using that UID.
        try:
            error = ET.fromstring(response.text())
        except ET.ParseError:
            error = None
        if error is not None and error.tag == f"{{{_DAV}}}error":
            for child in error:
                name = child.tag.rpartition("}")[2]
                if name in _PRECONDITION_REASONS:
                    href = (child.findtext(f"{{{_DAV}}}href") or "").strip()
                    return CalendarRefusedError(status, reason=_PRECONDITION_REASONS[name],
                                                detail=f"caldav {purpose} refused: {name}" + (f" ({href})" if href else ""))
    return CalendarRefusedError(status, reason=reason, detail=f"caldav {purpose} refused: HTTP {status}")


@register_adapter
class CalDavCalendarAdapter(CalendarAdapter):
    kind = "calendar"
    provider_id = "caldav"

    def __init__(self, *, transport: Any, config: AdapterConfig) -> None:
        super().__init__(transport=transport, config=config)
        self._timeout = float(str(config.options.get("timeout") or "20"))

    # -- wire helpers ------------------------------------------------------------------

    def _send(self, request: KasRequest) -> KasResponse:
        response = self.send(request)
        if response.ok:
            return response
        raise _refusal(response, purpose=str(request.purpose or "request"))

    def _calendar_href(self, calendar_id: str) -> str:
        calendar = str(calendar_id or "").strip()
        if not calendar:
            raise CalendarRefusedError(400, reason="missing_calendar", detail="no calendar id was given")
        if calendar.startswith(("http://", "https://")):
            return calendar.rstrip("/") + "/"
        # A bare collection path (what multistatus hrefs carry, server-absolute or
        # relative): resolve against THIS server, never another host.
        return urljoin(self.config.base_url.rstrip("/") + "/", quote(calendar.strip("/")) + "/")

    def _absolute_href(self, href: str) -> str:
        """Resolve a provider href to an absolute URL without losing its origin path.

        A leading slash is ROOT-relative by WebDAV semantics: it resolves against the
        server's scheme+host (the ORIGIN), never against the base URL's path — stripping
        it would silently relocate /calendars/x/ under /dav/user/ and address a different
        resource. An already-absolute URL (even off-host) is returned as-is: the
        transport's host pin, not this adapter, decides whether another host is allowed.
        Existing percent-encodings are preserved; anything else unsafe is encoded.
        """
        clean = str(href or "").strip()
        if clean.startswith(("http://", "https://")):
            return clean
        base = urlsplit(self.config.base_url)
        if not base.scheme or not base.netloc:
            raise CalendarRefusedError(400, reason="malformed_base_url", detail="the configured base URL names no origin")
        if clean.startswith("/"):
            path = clean
        else:
            # Collection-relative: resolve against the configured base PATH, not the origin.
            path = urljoin(base.path.rstrip("/") + "/", clean)
        return f"{base.scheme}://{base.netloc}{quote(path, safe='/%')}"

    def _event_href(self, calendar_id: str, uid: str) -> str:
        return urljoin(self._calendar_href(calendar_id), quote(f"{uid}.ics"))

    def _report(self, calendar_id: str, body: str, *, purpose: str, instances: bool = False) -> list[CalEvent]:
        """REPORT and read the reply in server order.

        By default one event per calendar object: its series master, else its first VEVENT. With ``instances`` every
        component of an expanded object is its own event (RFC 4791 section 9.6.5: one VEVENT per instance); an object
        the server returned unexpanded -- a component still carries RRULE -- reads as its master alone.
        """
        response = self._send(
            KasRequest(
                method="REPORT",
                url=self._calendar_href(calendar_id),
                purpose=purpose,
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                body=body.encode("utf-8"),
                timeout=self._timeout,
            )
        )
        events: list[CalEvent] = []
        for index, (href, etag, data) in enumerate(self._multistatus_events(response, purpose=purpose), start=1):
            try:
                parsed = _parse_vevents(data, provider_id=self.provider_id, calendar_id=calendar_id, href=href, etag=etag)
            except ValueError as exc:
                raise CalendarReadUnusableError(response.status, detail=f"caldav {purpose}, object {index}: {exc}") from None
            if instances and not any(event.recurring for event in parsed):
                events.extend(parsed)
            else:
                events.append(next((event for event in parsed if not event.original_start), parsed[0]))
        return events

    def _multistatus_events(self, response: KasResponse, *, purpose: str) -> list[tuple[str, str, str]]:
        try:
            tree = ET.fromstring(response.text())
        except ET.ParseError as exc:
            raise CalendarRefusedError(502, reason="unparseable_reply", detail=f"caldav multistatus is not XML: {exc}") from None
        if tree.tag != f"{{{_DAV}}}multistatus":
            raise CalendarReadUnusableError(response.status, detail=f"caldav {purpose}: the reply is not a multistatus")
        rows: list[tuple[str, str, str]] = []
        for node in tree.iter(f"{{{_DAV}}}response"):
            href = (node.findtext(f"{{{_DAV}}}href") or "").strip()
            etag = ""
            data = ""
            for propstat in node.iter(f"{{{_DAV}}}propstat"):
                status = (propstat.findtext(f"{{{_DAV}}}status") or "").strip()
                if status and "200" not in status:
                    continue
                prop = propstat.find(f"{{{_DAV}}}prop")
                if prop is None:
                    continue
                etag = etag or (prop.findtext(f"{{{_DAV}}}getetag") or "").strip().strip('"')
                data = data or (prop.findtext(f"{{{_CALDAV}}}calendar-data") or "")
            # Every response in a calendar-query reply is a matched calendar object. One named without
            # its href or its calendar data is unreadable -- never skipped, or a busy calendar reads empty.
            if not href or not data.strip():
                raise CalendarReadUnusableError(response.status, detail=f"caldav {purpose}: a matched calendar object came without its href or calendar data")
            rows.append((href, etag, data))
        return rows

    # -- the contract ------------------------------------------------------------------

    def list_calendars(self) -> list[CalCalendar]:
        response = self._send(
            KasRequest(
                method="PROPFIND",
                url=self.config.base_url.rstrip("/") + "/",
                purpose="discover calendar collections",
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                body=_PROPFIND_BODY.encode("utf-8"),
                timeout=self._timeout,
            )
        )
        try:
            tree = ET.fromstring(response.text())
        except ET.ParseError as exc:
            raise CalendarRefusedError(502, reason="unparseable_reply", detail=f"caldav propfind reply is not XML: {exc}") from None
        found: list[CalCalendar] = []
        for node in tree.iter(f"{{{_DAV}}}response"):
            href = (node.findtext(f"{{{_DAV}}}href") or "").strip()
            if not href:
                continue
            resourcetype = node.find(f".//{{{_DAV}}}resourcetype")
            is_calendar = resourcetype is not None and resourcetype.find(f"{{{_CALDAV}}}calendar") is not None
            if not is_calendar:
                continue
            display = (node.findtext(f".//{{{_DAV}}}displayname") or "").strip() or href.rstrip("/").rpartition("/")[2]
            found.append(CalCalendar(provider_id=self.provider_id, calendar_id=href, display_name=display, can_write=_stated_write_access(node)))
            if len(found) >= _LIST_PAGES * 100:
                break
        return found

    def events_in_range(self, calendar_id: str, *, start_utc: str, end_utc: str) -> list[CalEvent]:
        # Expand first (RFC 4791 CALDAV:expand): a repeating series must read as its dated
        # occurrences, each with its own identity, so single-occurrence actions cannot mutate
        # the whole series. A server that does not expand answers with an error; the unexpanded
        # retry then yields the master, which the typed recurring flag still protects.
        try:
            events = self._report(calendar_id, _calendar_query_body(start_utc=start_utc, end_utc=end_utc, expand=True),
                                  purpose="events in time range (expanded)", instances=True)
        except CalendarRefusedError:
            events = self._report(calendar_id, _calendar_query_body(start_utc=start_utc, end_utc=end_utc),
                                  purpose="events in time range")
        timed = [event for event in events if not event.all_day]
        timed.sort(key=lambda event: (event.start_utc or event.start_date, event.uid))
        all_day_events = sorted((event for event in events if event.all_day), key=lambda event: (event.start_date, event.uid))
        # All-day events overlap any window that touches their date; the server already
        # filtered, so both groups are kept and the caller reads `all_day` honestly.
        return all_day_events + timed

    def get_event(self, calendar_id: str, uid: str) -> CalEvent:
        clean_uid = str(uid or "").strip()
        if not clean_uid:
            raise CalendarRefusedError(400, reason="missing_uid", detail="no event uid was given")
        far = datetime.now(timezone.utc) + timedelta(days=3650)
        ago = datetime.now(timezone.utc) - timedelta(days=3650)
        body = _calendar_query_body(start_utc=ago.isoformat(), end_utc=far.isoformat(), uid=clean_uid)
        # An unreadable object raises inside _report; only a readable answer without this exact UID is
        # the server's statement that the event is absent.
        for parsed in self._report(calendar_id, body, purpose="event by uid"):
            if parsed.uid == clean_uid:
                return parsed
        raise CalendarRefusedError(404, reason="not_found", detail=f"no event with uid {clean_uid} in this calendar")

    def create_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        uid = str(event.uid or "").strip() or f"{uuid.uuid4()}@vool.local"
        body = self._render_event(event, uid=uid)
        href = self._event_href(calendar_id, uid)
        try:
            self._send(
                KasRequest(
                    method="PUT",
                    url=href,
                    purpose="create calendar event",
                    headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
                    body=body.encode("utf-8"),
                    timeout=self._timeout,
                    mutating=True,
                    idempotency_key=f"create:{uid}",
                )
            )
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(uid, exc, purpose="create") from None
        # Past this line the server ACCEPTED the PUT: a failed read-back is not a refusal.
        try:
            return self.get_event(calendar_id, uid)
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="create") from exc

    # -- one occurrence of a recurring series, inside the series' own resource -------------------------

    def _series_resource(self, calendar_id: str, series_uid: str) -> _SeriesResource:
        """The ONE calendar object resource holding ``series_uid``: found by UID, read by GET at the href the server reported.

        RFC 4791 section 4.1 keeps a series' master and every exception in that one resource, so an occurrence is changed
        by rewriting it there, never by a second resource sharing the UID. The representation and its ETag come from the
        GET, so a write that follows is conditional on exactly the bytes it edits.
        """
        clean_uid = str(series_uid or "").strip()
        if not clean_uid:
            raise CalendarRefusedError(400, reason="missing_uid", detail="no series uid was given")
        far = datetime.now(timezone.utc) + timedelta(days=3650)
        ago = datetime.now(timezone.utc) - timedelta(days=3650)
        response = self._send(
            KasRequest(
                method="REPORT",
                url=self._calendar_href(calendar_id),
                purpose="series resource by uid",
                headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                body=_calendar_query_body(start_utc=ago.isoformat(), end_utc=far.isoformat(), uid=clean_uid).encode("utf-8"),
                timeout=self._timeout,
            )
        )
        holding: set[str] = set()
        for href, _etag, data in self._multistatus_events(response, purpose="series resource by uid"):
            for block in _vevent_blocks(data):
                if any(name == "UID" and _ical_unescape(value).strip() == clean_uid for _position, name, _params, value in _top_properties(block)):
                    holding.add(href)
        if not holding:
            raise CalendarRefusedError(404, reason="not_found", detail=f"no series with uid {clean_uid} in this calendar")
        if len(holding) > 1:
            raise CalendarReadUnusableError(response.status, detail=("caldav series resource: the server keeps components of one UID "
                                                                     "in more than one resource (RFC 4791 section 4.1)"))
        url = self._absolute_href(holding.pop())
        stored = self._send(KasRequest(method="GET", url=url, purpose="read the series resource",
                                       headers={"Accept": "text/calendar"}, timeout=self._timeout))
        etag = _header(stored, "ETag").strip()
        if not etag:
            raise CalendarRefusedError(409, reason="no_version_token", detail="the provider gave no ETag for the series resource")
        try:
            return _SeriesResource(href=url, etag=etag, data=stored.text(), series_uid=clean_uid)
        except ValueError as exc:
            raise CalendarReadUnusableError(stored.status, detail=f"caldav series resource: {exc}") from None

    def _series_for_write(self, calendar_id: str, series_uid: str, reviewed_etag: str) -> _SeriesResource:
        """The series resource a write will edit, read BEFORE anything is sent: every refusal here is marked
        ``before_send``. A version other than the reviewed one refuses as precondition_failed; nothing is forced."""
        try:
            series = self._series_resource(calendar_id, series_uid)
        except CalendarRefusedError as exc:
            raise _before_send(exc) from None
        except TransportUnknownError as exc:
            raise _before_send(CalendarRefusedError(
                503, reason="series_read_unproven",
                detail=f"the series resource could not be read before writing ({exc.reason}); nothing was sent")) from None
        if reviewed_etag and _bare_etag(reviewed_etag) != _bare_etag(series.etag):
            raise _before_send(CalendarRefusedError(
                412, reason="precondition_failed",
                detail="caldav occurrence write refused: the series changed on the provider after it was reviewed; nothing was sent"))
        return series

    def _put_series(self, series: _SeriesResource, body: str, *, purpose: str) -> None:
        """PUT the edited series back to its OWN href, conditional on the version it was read at. A 412 surfaces as
        precondition_failed and is never retried or forced."""
        try:
            self._send(
                KasRequest(
                    method="PUT",
                    url=series.href,
                    purpose=purpose,
                    headers={"Content-Type": "text/calendar; charset=utf-8", "If-Match": _if_match(series.etag)},
                    body=body.encode("utf-8"),
                    timeout=self._timeout,
                    mutating=True,
                )
            )
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(series.series_uid, exc, purpose=purpose) from None

    def update_occurrence(self, calendar_id: str, occurrence: CalEvent) -> CalEvent:
        """Change ONE occurrence of a series inside the series' own resource, under the reviewed ETag.

        Only that RECURRENCE-ID exception is added or edited (``_SeriesResource``) and the resource is PUT back to its own
        href. After acceptance the occurrence is read back BY ITS RECURRENCE-ID wherever it now is -- the approved target
        or still its original slot -- so a provider that accepted without moving it is observed, never assumed.
        """
        series_uid = str(occurrence.series_id or occurrence.uid)
        if not occurrence.original_start:
            raise _before_send(CalendarRefusedError(409, reason="not_an_occurrence",
                                                    detail="this event is not a dated occurrence of a series; use the series action"))
        series = self._series_for_write(calendar_id, series_uid, occurrence.etag)
        try:
            original_slot = series.slot(occurrence.original_start)
            body = series.render(occurrence.original_start, moved=occurrence)
        except (ValueError, KeyError) as exc:
            raise _before_send(CalendarReadUnusableError(200, detail=f"caldav occurrence update: {exc}")) from None
        self._put_series(series, body, purpose="update one occurrence of a recurring event")
        # Past this line the server ACCEPTED the PUT: a failed read-back is not a refusal.
        try:
            return self.find_occurrence(calendar_id, series_uid, occurrence.original_start,
                                        windows=[(occurrence.start_utc, occurrence.end_utc), original_slot])
        except Exception as exc:
            raise accepted_write_error(series_uid, exc, purpose="occurrence update") from exc

    def cancel_occurrence(self, calendar_id: str, master_uid: str, original_start: str, etag: str) -> bool:
        """Cancel ONE occurrence inside the series' own resource: its RECURRENCE-ID exception carries STATUS:CANCELLED
        (deleting an exception would RESTORE the instance), PUT to that resource's href under If-Match.

        True only on POSITIVE read-back evidence for exactly that occurrence (``occurrence_cancelled``). An accepted write
        the provider does not reflect raises ``still_present``; an unreadable read-back raises the accepted-write error.
        Neither is ever re-sent here.
        """
        series = self._series_for_write(calendar_id, master_uid, etag)
        try:
            if series.cancellation(original_start):
                raise _before_send(CalendarRefusedError(409, reason="already_cancelled",
                                                        detail="that occurrence is already cancelled on the provider; nothing was sent"))
            body = series.render(original_start, cancel=True)
        except (ValueError, KeyError) as exc:
            raise _before_send(CalendarReadUnusableError(200, detail=f"caldav occurrence cancellation: {exc}")) from None
        self._put_series(series, body, purpose="cancel one occurrence of a recurring event")
        # Past this line the server ACCEPTED the PUT: only the read-back decides what it did.
        try:
            cancelled, observed = self.occurrence_cancelled(calendar_id, master_uid, original_start)
        except Exception as exc:
            raise accepted_write_error(master_uid, exc, purpose="occurrence cancellation") from exc
        if not cancelled:
            raise CalendarRefusedError(409, reason="still_present", detail=f"the provider accepted the cancellation, but {observed}")
        return True

    def occurrence_cancelled(self, calendar_id: str, series_uid: str, original_start: str) -> tuple[bool, str]:
        """Whether ONE occurrence is verifiably cancelled on the provider, and what was observed.

        It needs POSITIVE evidence in the stored series resource for exactly that RECURRENCE-ID -- its exception carrying
        STATUS:CANCELLED, or the master's EXDATE excluding it; the series master remaining is expected. Another
        occurrence's cancellation proves nothing for this one, and neither does absence: a missing series raises
        not_found. The served view must not contradict the stored one: an expanded read of the slot that still returns
        that instance as scheduled means it is not cancelled.
        """
        series = self._series_resource(calendar_id, series_uid)
        try:
            evidence = series.cancellation(original_start)
            slot = series.slot(original_start)
        except (ValueError, KeyError) as exc:
            raise CalendarReadUnusableError(200, detail=f"caldav occurrence read-back: {exc}") from None
        if not evidence:
            return False, f"the stored series holds no cancellation of its {original_start} occurrence"
        _expanded, served = self._served_occurrence(calendar_id, series_uid, original_start, [slot])
        if served is not None and not served.cancelled:
            return False, f"the provider still serves its {original_start} occurrence as scheduled"
        return True, f"the stored series marks its {original_start} occurrence cancelled ({evidence})"

    def _served_occurrence(self, calendar_id: str, series_uid: str, original_start: str,
                           windows: list[tuple[str, str]]) -> tuple[bool, CalEvent | None]:
        """(whether the server expanded the series, the instance with that RECURRENCE-ID it serves in any window, or None)."""
        for start, end in windows:
            begin = _as_utc(start) - timedelta(minutes=1)
            finish = max(_as_utc(end), _as_utc(start)) + timedelta(minutes=1)
            try:
                events = self._report(calendar_id, _calendar_query_body(start_utc=begin.isoformat(), end_utc=finish.isoformat(), expand=True),
                                      purpose="occurrence by recurrence id (expanded)", instances=True)
            except CalendarReadUnusableError:
                raise
            except CalendarRefusedError:
                return False, None  # this server does not expand: only the stored series can answer
            matching = [event for event in events if str(event.series_id or event.uid) == series_uid]
            if any(event.recurring for event in matching):
                return False, None  # the series came back unexpanded
            for event in matching:
                if _same_original_start(event.original_start, original_start):
                    return True, event
        return True, None

    def find_occurrence(self, calendar_id: str, series_uid: str, original_start: str, *, windows: list[tuple[str, str]]) -> CalEvent:
        """ONE occurrence by its identity -- series UID and RECURRENCE-ID -- wherever it now is.

        Identity is the RECURRENCE-ID, never a start time or a date prefix: a moved occurrence is found at its new time and
        a neighbouring occurrence is never taken for it. The server decides which instances overlap each window (RFC 4791
        section 9.9 judges an exception at its own times), so the caller names every interval the occurrence may
        legitimately occupy: the approved target and its original slot. A server that does not expand is answered from the
        stored series resource.
        """
        expanded, served = self._served_occurrence(calendar_id, series_uid, original_start, list(windows))
        if served is not None:
            return served
        if not expanded:
            series = self._series_resource(calendar_id, series_uid)
            try:
                index = series.exception(original_start)
                if index is not None:
                    return _parse_component(series.lines(index), provider_id=self.provider_id, calendar_id=calendar_id,
                                            href=series.href, etag=_bare_etag(series.etag))
            except ValueError as exc:
                raise CalendarReadUnusableError(200, detail=f"caldav occurrence read-back: {exc}") from None
        raise CalendarRefusedError(404, reason="not_found",
                                   detail=f"no occurrence of {series_uid} with recurrence id {original_start} where it may be")

    def update_event(self, calendar_id: str, event: CalEvent) -> CalEvent:
        current = self.get_event(calendar_id, event.uid)
        etag = str(event.etag or current.etag).strip()
        if not etag:
            raise CalendarRefusedError(409, reason="no_version_token", detail="the provider gave no etag to protect this update")
        body = self._render_event(event, uid=event.uid, base=current.raw_component)
        try:
            self._send(
                KasRequest(
                    method="PUT",
                    url=self._absolute_href(current.href) or self._event_href(calendar_id, event.uid),
                    purpose="update calendar event",
                    headers={"Content-Type": "text/calendar; charset=utf-8", "If-Match": _if_match(etag)},
                    body=body.encode("utf-8"),
                    timeout=self._timeout,
                    mutating=True,
                )
            )
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(str(event.uid), exc, purpose="update") from None
        # Past this line the server ACCEPTED the PUT: a failed read-back is not a refusal.
        try:
            return self.get_event(calendar_id, event.uid)
        except Exception as exc:
            raise accepted_write_error(str(event.uid), exc, purpose="update") from exc

    def cancel_event(self, calendar_id: str, uid: str, etag: str) -> bool:
        current = self.get_event(calendar_id, uid)
        token = str(etag or current.etag).strip()
        headers = {"If-Match": _if_match(token)} if token else {}
        try:
            self._send(
                KasRequest(
                    method="DELETE",
                    url=self._absolute_href(current.href) or self._event_href(calendar_id, uid),
                    purpose="cancel calendar event",
                    headers=headers,
                    timeout=self._timeout,
                    mutating=True,
                )
            )
        except TransportAcceptedError as exc:
            raise accepted_reply_lost(str(uid), exc, purpose="cancellation") from None
        # Verified absence, not assumed: the provider must now answer not_found.
        try:
            self.get_event(calendar_id, uid)
        except CalendarRefusedError as exc:
            if exc.reason == "not_found":
                return True
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        except Exception as exc:
            raise accepted_write_error(uid, exc, purpose="cancellation") from exc
        raise CalendarRefusedError(409, reason="still_present", detail="the provider still serves the event after deletion")

    # -- rendering ----------------------------------------------------------------------

    def _render_event(self, event: CalEvent, *, uid: str, base: str = "") -> str:
        """The VCALENDAR body for PUT: exactly ONE VEVENT component, whatever the input.

        With ``base`` (the stored representation) unknown properties — alarms, attendee
        data, timezone definitions, X-extensions — are preserved line-for-line and only
        the identity/time/title/description lines are replaced, so an update never
        silently drops server-side extensions. The base component's own
        BEGIN/END:VEVENT delimiters are REUSED rather than wrapped again: nesting a
        second pair produces an invalid object some servers reject outright.
        """
        replacements: dict[str, str] = {}
        if event.all_day and event.start_date:
            replacements["DTSTART"] = f"DTSTART;VALUE=DATE:{event.start_date.replace('-', '')}"
            end = event.end_date or (date.fromisoformat(event.start_date) + timedelta(days=1)).isoformat()
            replacements["DTEND"] = f"DTEND;VALUE=DATE:{end.replace('-', '')}"
        else:
            value, params = _render_instant(event.start_utc, event.tz_name)
            replacements["DTSTART"] = f"DTSTART{params}:{value}"
            end_value, end_params = _render_instant(event.end_utc, event.tz_name)
            replacements["DTEND"] = f"DTEND{end_params}:{end_value}"
        replacements["SUMMARY"] = f"SUMMARY:{_ical_escape(event.summary)}"
        replacements["UID"] = f"UID:{uid}"
        if event.description:
            replacements["DESCRIPTION"] = f"DESCRIPTION:{_ical_escape(event.description)}"
        elif "DESCRIPTION" in base:
            # An explicit clear: the reviewed event carries no description, the stored one did.
            # This is the DESCRIPTION field's own rule -- attendee handling has no say in it.
            replacements["DESCRIPTION"] = ""
        if str(event.recurrence_rule or "").strip():
            replacements["RRULE"] = f"RRULE:{str(event.recurrence_rule).strip()}"
        if event.attendees:
            # RFC 5545 scheduling: ATTENDEE lines are what a CalDAV server's scheduling
            # service delivers invitations from; whether THIS server does is its capability,
            # stated in the approval rather than assumed here.
            replacements["ATTENDEE"] = "\r\n".join(f"ATTENDEE;CN={address};RSVP=TRUE:mailto:{address}" for address in event.attendees)

        component_lines: list[str]
        if base:
            kept = set()
            component_lines = []
            for line in _unfold(base):
                name, _, _ = _split_property(line)
                if name in replacements:
                    replacement = replacements[name]
                    if replacement and name not in kept:
                        component_lines.append(_fold(replacement))
                        kept.add(name)
                    continue
                component_lines.append(line)
            for name, replacement in replacements.items():
                if replacement and name not in kept:
                    component_lines.append(_fold(replacement))
        else:
            inner = [replacements.get("DTSTART", ""), replacements.get("DTEND", ""),
                     replacements.get("SUMMARY", ""), replacements.get("UID", ""),
                     replacements.get("DESCRIPTION", ""), replacements.get("RRULE", ""),
                     replacements.get("ATTENDEE", "")]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            component_lines = [_fold(item) for item in ([f"DTSTAMP:{stamp}", *inner]) if item]

        # Exactly one component, self-delimited: add BEGIN/END only where absent.
        if not component_lines or component_lines[0].strip().upper() != "BEGIN:VEVENT":
            component_lines.insert(0, "BEGIN:VEVENT")
        if component_lines[-1].strip().upper() != "END:VEVENT":
            component_lines.append("END:VEVENT")
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//VOOL//Calendar Vertical//EN\r\n"
            + "\r\n".join(component_lines) + "\r\n"
            "END:VCALENDAR\r\n"
        )


def calendar_url_base(url: str) -> str:
    """The scheme://host:port/ prefix of a CalDAV URL, for host pinning."""
    parts = urlsplit(str(url or ""))
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}/"


__all__ = ["CalDavCalendarAdapter", "calendar_url_base"]
