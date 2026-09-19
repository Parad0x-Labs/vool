"""A disposable, STRICT CalDAV service for protocol-level execution evidence.

This is a REAL HTTP server speaking the CalDAV subset the adapter uses -- PROPFIND discovery, REPORT calendar-query
with a time range (optionally CALDAV:expand), GET with ETag, PUT/DELETE with If-Match/If-None-Match -- so tests
exercise the actual wire protocol through the actual VOOL transport, not a mocked handler returning success. State is
in-memory per instance, keyed by calendar collection and RESOURCE NAME (the ``<name>.ics`` of the resource URL).

Resource model -- RFC 4791 section 4.1, enforced as the section 5.3.2.1 PUT preconditions:

* components sharing a UID live in ONE calendar object resource: a recurring series' master and every RECURRENCE-ID
  exception it has. A resource's components all carry that one UID, are of one component type besides VTIMEZONE and
  carry no METHOD (CALDAV:valid-calendar-object-resource);
* a PUT -- or a test seed -- giving a second resource a UID another resource already uses is refused with
  CALDAV:no-uid-conflict naming that resource, and so is a PUT that would change a resource's UID.

Query semantics -- RFC 4791 sections 9.9 and 9.6.5:

* a resource matches a time range when ANY of its instances overlaps it, and an exception is judged at its OWN times,
  never at the slot it replaced;
* recurrence is generated in the master's own zone (TZID), so a weekly 12:00 Europe/Athens series stays at 12:00 local
  across a DST change; an EXDATE removes exactly the instance it names;
* an expanded reply holds one DAV:response per matching resource, whose calendar data carries one VEVENT per instance
  overlapping the range, each with RECURRENCE-ID and UTC times and without recurrence properties or VTIMEZONE.

Policy the RFC leaves to the server is an explicit knob: ``expand_cancelled_instances`` (default False) serves a
STATUS:CANCELLED exception in expanded replies, with its status, instead of omitting it.

Failure injection is first-class, because the mission's negative controls need REAL provider behaviors: 401/403
(credential refusal), 429 (rate limit), 412 (stale ETag -- the server's own If-Match check), and write-then-hang
(timeout AFTER the provider applied the change -- the outcome-unproven case). A deliberately damaged object a test
seeds (no UID, unreadable times) is stored and served as given, so the client meets it.

Not a product component: it lives under the test tree, is imported only by tests, and reads iCalendar on its own --
it never imports the adapter it checks.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DAV = "DAV:"
_CALDAV = "urn:ietf:params:xml:ns:caldav"
ALLOCATED_PORT = 12462  # the mission's allocated test port; falls back if occupied
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
#: Properties an expanded instance does not carry (its identity and times are written fresh, in UTC).
_INSTANCE_REWRITTEN = frozenset({"DTSTART", "DTEND", "DURATION", "RECURRENCE-ID", "RRULE", "RDATE", "EXRULE", "EXDATE"})
_MAX_RULE_STEPS = 200_000  # a generation bound no series in these tests approaches
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UNREADABLE = (ValueError, KeyError, IndexError, ZoneInfoNotFoundError)


class UidConflict(Exception):
    """CALDAV:no-uid-conflict: another resource in the collection already uses this UID (``href`` names it)."""

    def __init__(self, href: str) -> None:
        super().__init__(href)
        self.href = href


# -- reading iCalendar (independently of the adapter under test) ---------------------------


def _unfold(ics: str) -> list[str]:
    """RFC 5545 section 3.1: a line starting with a space or tab continues the previous one."""
    lines: list[str] = []
    for raw in str(ics).replace("\r\n", "\n").split("\n"):
        if not raw:
            continue
        if raw[0] in {" ", "\t"} and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """``NAME;PARAM=V;...:value`` -> (NAME, params, value); a quoted parameter value may hold ':' or ';'."""
    in_quotes = False
    head, value = line, ""
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            head, value = line[:index], line[index + 1:]
            break
    pieces: list[str] = []
    current = ""
    in_quotes = False
    for char in head:
        if char == '"':
            in_quotes = not in_quotes
        if char == ";" and not in_quotes:
            pieces.append(current)
            current = ""
        else:
            current += char
    pieces.append(current)
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, param_value = piece.partition("=")
        params[key.upper()] = param_value.strip('"')
    return pieces[0].upper(), params, value


class _Component:
    """One top-level component of a calendar object: its kind and unfolded lines, BEGIN/END included."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.kind = lines[0].strip().upper()[len("BEGIN:"):]

    def props(self, name: str) -> list[tuple[dict[str, str], str]]:
        """Every TOP-LEVEL occurrence of a property (a nested VALARM's lines are not the component's own)."""
        found: list[tuple[dict[str, str], str]] = []
        depth = 0
        for line in self.lines[1:-1]:
            upper = line.strip().upper()
            if upper.startswith("BEGIN:"):
                depth += 1
            elif upper.startswith("END:"):
                depth -= 1
            elif depth == 0:
                prop, params, value = _split(line)
                if prop == name:
                    found.append((params, value))
        return found

    def prop(self, name: str) -> tuple[dict[str, str], str] | None:
        found = self.props(name)
        return found[0] if found else None


def _parse_calendar(ics: str) -> tuple[list[str], list[_Component]]:
    """(VCALENDAR-level property lines, top-level components) of ONE calendar object; ValueError otherwise."""
    lines = _unfold(ics)
    if len(lines) < 2 or lines[0].strip().upper() != "BEGIN:VCALENDAR" or lines[-1].strip().upper() != "END:VCALENDAR":
        raise ValueError("the body is not one VCALENDAR object")
    header: list[str] = []
    components: list[_Component] = []
    depth = 0
    current: list[str] = []
    for line in lines[1:-1]:
        upper = line.strip().upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            current.append(line)
        elif upper.startswith("END:"):
            if depth == 0:
                raise ValueError("an END without its BEGIN")
            depth -= 1
            current.append(line)
            if depth == 0:
                components.append(_Component(current))
                current = []
        elif depth:
            current.append(line)
        else:
            header.append(line)
    if depth:
        raise ValueError("a component without its END")
    return header, components


def _uid_of(component: _Component) -> str:
    found = component.prop("UID")
    return found[1].strip() if found else ""


def _uids_of(ics: str) -> set[str]:
    """The UIDs a stored object's components carry; an object that cannot be read carries none this service can see."""
    try:
        _header, components = _parse_calendar(ics)
    except ValueError:
        return set()
    return {uid for uid in (_uid_of(component) for component in components if component.kind != "VTIMEZONE") if uid}


def _instant(params: dict[str, str], value: str) -> tuple[datetime, bool]:
    """(UTC instant, is_date) of a DATE or DATE-TIME value. Dates and floating times read as UTC: this service
    advertises no CALDAV:calendar-timezone."""
    text = str(value).strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", text):
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc), True
    if text.endswith("Z"):
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
    naive = datetime.strptime(text, "%Y%m%dT%H%M%S")
    zone = ZoneInfo(params["TZID"]) if params.get("TZID") else timezone.utc
    return naive.replace(tzinfo=zone).astimezone(timezone.utc), False


_DURATION_RE = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _duration(text: str) -> timedelta:
    match = _DURATION_RE.match(str(text).strip().upper())
    if not match:
        raise ValueError(f"unreadable DURATION {text!r}")
    sign, weeks, days, hours, minutes, seconds = match.groups()
    span = timedelta(weeks=int(weeks or 0), days=int(days or 0), hours=int(hours or 0),
                     minutes=int(minutes or 0), seconds=int(seconds or 0))
    return -span if sign == "-" else span


def _interval(component: _Component) -> tuple[datetime, datetime, bool]:
    """A component's own [start, end) in UTC, and whether its start is a DATE (RFC 5545 section 3.6.1 defaults)."""
    start_prop = component.prop("DTSTART")
    if start_prop is None:
        raise ValueError("a VEVENT without DTSTART")
    start, is_date = _instant(*start_prop)
    end_prop = component.prop("DTEND")
    duration_prop = component.prop("DURATION")
    if end_prop is not None:
        end = _instant(*end_prop)[0]
    elif duration_prop is not None:
        end = start + _duration(duration_prop[1])
    else:
        end = start + (timedelta(days=1) if is_date else timedelta(0))
    return start, end, is_date


def _overlaps(start: datetime, end: datetime, range_start: datetime, range_end: datetime) -> bool:
    """RFC 4791 section 9.9: an interval overlaps [range_start, range_end); a zero-length one when it starts inside."""
    if end <= start:
        return range_start <= start < range_end
    return start < range_end and end > range_start


def _cancelled(component: _Component) -> bool:
    status = component.prop("STATUS")
    return status is not None and status[1].strip().upper() == "CANCELLED"


def _rule_starts(master: _Component, horizon: datetime) -> list[datetime]:
    """The UTC starts a master's RRULE generates before ``horizon``, EXDATEs removed.

    Generated in the master's wall-clock zone (TZID); supports the basic shapes the product writes --
    FREQ=DAILY|WEEKLY[;BYDAY=..]|MONTHLY[;BYMONTHDAY=..] with INTERVAL, COUNT and UNTIL. COUNT bounds the rule's own
    instances before EXDATE removes any (RFC 5545 section 3.8.5.3).
    """
    start_params, start_value = master.prop("DTSTART")
    first, is_date = _instant(start_params, start_value)
    rrule = master.prop("RRULE")
    if rrule is None:
        return [first]
    rule = dict(part.split("=", 1) for part in rrule[1].strip().upper().split(";") if "=" in part)
    freq = rule.get("FREQ", "")
    if freq not in {"DAILY", "WEEKLY", "MONTHLY"}:
        raise ValueError(f"FREQ={freq} is outside what this service expands")
    interval = max(1, int(rule.get("INTERVAL") or 1))
    count = int(rule.get("COUNT") or 0)
    until = _instant({}, rule["UNTIL"])[0] if rule.get("UNTIL") else None
    zone = ZoneInfo(start_params["TZID"]) if (start_params.get("TZID") and not is_date) else timezone.utc
    local_first = first.astimezone(zone).replace(tzinfo=None)
    byday = [code for code in rule.get("BYDAY", "").split(",") if code]
    bymonthday = int(rule["BYMONTHDAY"]) if rule.get("BYMONTHDAY", "").isdigit() else 0
    excluded = {_instant(params, chunk)[0] for params, value in master.props("EXDATE") for chunk in value.split(",") if chunk.strip()}
    week_zero = local_first.date() - timedelta(days=local_first.weekday())

    def candidates():
        for step in range(_MAX_RULE_STEPS):
            if freq == "DAILY":
                yield local_first + timedelta(days=step * interval)
            elif freq == "WEEKLY" and not byday:
                yield local_first + timedelta(weeks=step * interval)
            elif freq == "WEEKLY":
                day = local_first + timedelta(days=step)
                if _WEEKDAYS[day.weekday()] in byday and ((day.date() - week_zero).days // 7) % interval == 0:
                    yield day
            else:
                months = (local_first.month - 1) + step * interval
                try:
                    yield local_first.replace(year=local_first.year + months // 12, month=months % 12 + 1,
                                              day=bymonthday or local_first.day)
                except ValueError:
                    continue  # a month without that day has no instance

    starts: list[datetime] = []
    for generated, local in enumerate(candidates(), start=1):
        instant = local.replace(tzinfo=zone).astimezone(timezone.utc)
        if (until is not None and instant > until) or instant >= horizon:
            break
        if instant not in excluded:
            starts.append(instant)
        if count and generated >= count:
            break
    return starts


class _Instance:
    __slots__ = ("component", "end", "is_date", "rid", "start")

    def __init__(self, component: _Component, start: datetime, end: datetime, is_date: bool, rid: datetime | None) -> None:
        self.component, self.start, self.end, self.is_date, self.rid = component, start, end, is_date, rid


def _instances(events: list[_Component], range_start: datetime, range_end: datetime, *, include_cancelled: bool) -> list[_Instance]:
    """The instances of one resource's VEVENTs overlapping [range_start, range_end), in start order."""
    exceptions: dict[datetime, _Component] = {}
    for component in events:
        rid = component.prop("RECURRENCE-ID")
        if rid is not None:
            exceptions[_instant(*rid)[0]] = component
    rows: list[_Instance] = []
    for master in (component for component in events if component.prop("RECURRENCE-ID") is None):
        start, end, is_date = _interval(master)
        if master.prop("RRULE") is None:
            if _overlaps(start, end, range_start, range_end):
                rows.append(_Instance(master, start, end, is_date, None))
            continue
        duration = end - start
        for original in _rule_starts(master, range_end):
            if original in exceptions:
                continue  # that instance is judged at its exception's own times, below
            if _overlaps(original, original + duration, range_start, range_end):
                rows.append(_Instance(master, original, original + duration, is_date, original))
    for rid, exception in exceptions.items():
        if _cancelled(exception) and not include_cancelled:
            continue
        start, end, is_date = _interval(exception)
        if _overlaps(start, end, range_start, range_end):
            rows.append(_Instance(exception, start, end, is_date, rid))
    rows.sort(key=lambda row: row.start)
    return rows


def _instance_lines(instance: _Instance) -> list[str]:
    """One expanded instance as a VEVENT: its component's own properties and nested components, with identity and
    times written fresh in UTC (dates stay dates) and no recurrence properties."""
    kept: list[str] = []
    depth = 0
    for line in instance.component.lines[1:-1]:
        upper = line.strip().upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            kept.append(line)
        elif upper.startswith("END:"):
            depth -= 1
            kept.append(line)
        elif depth > 0 or _split(line)[0] not in _INSTANCE_REWRITTEN:
            kept.append(line)
    if instance.is_date:
        times = [f"DTSTART;VALUE=DATE:{instance.start:%Y%m%d}", f"DTEND;VALUE=DATE:{instance.end:%Y%m%d}",
                 f"RECURRENCE-ID;VALUE=DATE:{instance.rid:%Y%m%d}"]
    else:
        times = [f"DTSTART:{instance.start:%Y%m%dT%H%M%SZ}", f"DTEND:{instance.end:%Y%m%dT%H%M%SZ}",
                 f"RECURRENCE-ID:{instance.rid:%Y%m%dT%H%M%SZ}"]
    return ["BEGIN:VEVENT", *times, *kept, "END:VEVENT"]


def _served_for_query(ics: str, *, wanted_uid: str | None, range_start: datetime | None, range_end: datetime | None,
                      expand: bool, include_cancelled: bool) -> str | None:
    """What one stored resource contributes to a calendar-query reply: None when it does not match; otherwise its whole
    calendar data, or under CALDAV:expand (for a recurring resource) its instances overlapping the range. An object this
    service cannot evaluate -- deliberately damaged test data -- matches by its raw text and is served verbatim."""
    try:
        header, components = _parse_calendar(ics)
        events = [component for component in components if component.kind == "VEVENT"]
        if not events:
            raise ValueError("no VEVENT")
        if wanted_uid is not None and not any(wanted_uid in uid.lower() for uid in (_uid_of(event) for event in events) if uid):
            return None
        if range_start is None or range_end is None:
            return ics
        instances = _instances(events, range_start, range_end, include_cancelled=include_cancelled)
        if not instances:
            return None
        if expand and any(event.prop("RRULE") is not None for event in events):
            lines = ["BEGIN:VCALENDAR", *header]
            for instance in instances:
                lines.extend(_instance_lines(instance))
            lines.append("END:VCALENDAR")
            return "\r\n".join(lines) + "\r\n"
        return ics
    except _UNREADABLE:
        if wanted_uid is not None and wanted_uid not in ics.lower():
            return None
        return ics


def _validated_uid(ics: str) -> str:
    """The one UID a PUT body carries, or ValueError naming the RFC 4791 section 4.1 rule the body breaks."""
    header, components = _parse_calendar(ics)
    if any(_split(line)[0] == "METHOD" for line in header):
        raise ValueError("a calendar object resource must not carry METHOD")
    primary = [component for component in components if component.kind != "VTIMEZONE"]
    if len({component.kind for component in primary}) != 1:
        raise ValueError("a calendar object resource holds exactly one component type besides VTIMEZONE")
    uids = {_uid_of(component) for component in primary}
    if len(uids) != 1 or "" in uids:
        raise ValueError("every component of a calendar object resource carries the same non-empty UID")
    masters = 0
    seen: set[datetime] = set()
    for component in primary:
        rid = component.prop("RECURRENCE-ID")
        if rid is None:
            masters += 1
            continue
        instant = _instant(*rid)[0]
        if instant in seen:
            raise ValueError("two components claim the same RECURRENCE-ID")
        seen.add(instant)
    if masters > 1:
        raise ValueError("a calendar object resource holds at most one master component for its UID")
    return uids.pop()


def _snapshot_row(row: dict) -> dict:
    """A stored resource for test assertions: its master's summary and start (the first component's when it has no master)."""
    summary, start = "", _EPOCH
    try:
        _header, components = _parse_calendar(row["ics"])
        events = [component for component in components if component.kind == "VEVENT"]
        master = next((event for event in events if event.prop("RECURRENCE-ID") is None), events[0] if events else None)
        if master is not None:
            found = master.prop("SUMMARY")
            summary = found[1] if found else ""
            start = _interval(master)[0]
    except _UNREADABLE:
        match = re.search(r"(?m)^SUMMARY:(.*)$", row["ics"].replace("\r\n", "\n"))
        summary = match.group(1) if match else ""
    summary = summary.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return {"etag": row["etag"], "summary": summary, "start_utc": start.astimezone(timezone.utc).isoformat(), "ics": row["ics"]}


def _norm_etag(value: str) -> str:
    """ETags travel quoted on the wire; comparisons happen on the bare token."""
    return str(value or "").strip().strip('"')


class CalDavState:
    """The server's calendar store and its failure-injection knobs (test-controlled)."""

    def __init__(self) -> None:
        self.calendars: dict[str, dict] = {}  # href -> {display_name, events: resource name -> {etag, ics}, privileges}
        self.lock = threading.Lock()
        self.require_bearer = ""        # non-empty: requests without this token get 401/403
        self.rate_limit_after = 0       # after this many requests (0 = never): 429 with Retry-After
        self.hang_seconds = 0.0         # >0: PUT/DELETE applies the change then sleeps (timeout-after-accept)
        self.request_count = 0
        self.refuse_writes_with = 0     # non-zero status: mutations answered with it before applying
        self.expand_cancelled_instances = False  # server policy: serve STATUS:CANCELLED exceptions when expanding
        self._etag_sequence = 0

    def add_calendar(self, href: str, display_name: str) -> None:
        self.calendars[href.rstrip("/") + "/"] = {"display_name": display_name, "events": {}, "privileges": None}

    def set_privileges(self, href: str, privileges: list[str] | None) -> None:
        """The DAV:current-user-privilege-set a PROPFIND reports for one calendar (None: the property is not served)."""
        self.calendars[href.rstrip("/") + "/"]["privileges"] = None if privileges is None else list(privileges)

    def put_event(self, calendar_href: str, uid: str, ics: str) -> str:
        """Store one calendar object resource under its resource NAME (``uid`` names the ``<name>.ics`` resource).

        The collection's UID rule binds a seed exactly as it binds a PUT: a resource carrying a UID another resource
        already uses raises :class:`UidConflict`. Every stored version gets a fresh strong ETag.
        """
        href = calendar_href.rstrip("/") + "/"
        calendar = self.calendars[href]
        name = str(uid)
        held = _uids_of(ics)
        for other, row in calendar["events"].items():
            if other != name and held & _uids_of(row["ics"]):
                raise UidConflict(f"{href}{other}.ics")
        self._etag_sequence += 1
        etag = f'"v{self._etag_sequence}-{abs(hash(name)) % 100000}"'
        calendar["events"][name] = {"etag": etag, "ics": ics}
        return etag

    def seed_event(self, calendar_href: str, uid: str, *, summary: str, start: datetime, minutes: int, tz_name: str = "",
                   description: str = "", location: str = "", url: str = "") -> str:
        zone = ZoneInfo(tz_name) if tz_name else timezone.utc
        local_start = start.astimezone(zone)
        local_end = local_start + timedelta(minutes=minutes)
        if tz_name:
            dtstart = f"DTSTART;TZID={tz_name}:{local_start:%Y%m%dT%H%M%S}"
            dtend = f"DTEND;TZID={tz_name}:{local_end:%Y%m%dT%H%M%S}"
        else:
            dtstart = f"DTSTART:{local_start.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
            dtend = f"DTEND:{local_end.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
        safe_summary = summary.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        lines = [
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN",
            "BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
            dtstart, dtend, f"SUMMARY:{safe_summary}",
        ]
        if description:
            safe_description = description.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
            lines.append(f"DESCRIPTION:{safe_description}")
        if location:
            safe_location = location.replace(";", "\\;").replace(",", "\\,")
            lines.append(f"LOCATION:{safe_location}")
        if url:
            lines.append(f"CONFERENCE;VALUE=URI:{url}")
        lines.extend(["END:VEVENT", "END:VCALENDAR"])
        return self.put_event(calendar_href, uid, "\r\n".join(lines) + "\r\n")

    def snapshot(self) -> dict:
        """Every stored resource by collection and name, for test assertions about provider state."""
        with self.lock:
            return {href: {name: _snapshot_row(row) for name, row in cal["events"].items()} for href, cal in self.calendars.items()}


class _Handler(BaseHTTPRequestHandler):
    state: CalDavState  # injected by the server factory
    base_path = "/"

    def log_message(self, *args):  # quiet: test evidence lives in assertions, not stdout
        pass

    # -- helpers ------------------------------------------------------------------

    def _path(self) -> str:
        return unquote(urlparse(self.path).path)

    def _calendar_for(self, path: str) -> tuple[str | None, str | None]:
        """(calendar_href, resource name) when path addresses a resource; (href, None) for a calendar."""
        clean = "/" + path.strip("/")
        for href in self.state.calendars:
            tail = href.strip("/")
            if clean == "/" + tail or clean == "/" + tail + "/":
                return href, None
            if clean.startswith("/" + tail + "/"):
                name = clean[len("/" + tail + "/"):].strip("/")
                return href, name[:-4] if name.endswith(".ics") else name
        return None, None

    def _check_access(self) -> bool:
        self.state.request_count += 1
        if self.state.rate_limit_after and self.state.request_count > self.state.rate_limit_after:
            self._reply(429, b"rate limited", headers={"Retry-After": "30"})
            return False
        expected = self.state.require_bearer
        if expected:
            auth = self.headers.get("Authorization", "")
            if not auth:
                self._reply(401, b"credential required", headers={"WWW-Authenticate": 'Bearer realm="fixture"'})
                return False
            if auth != f"Bearer {expected}":
                self._reply(403, b"credential refused")
                return False
        return True

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _reply(self, status: int, body: bytes = b"", content_type: str = "text/plain", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _precondition(self, element: str, text: str, *, href: str = ""):
        """RFC 4791 section 1.3: a failed CalDAV precondition is 403 with a DAV:error body naming it."""
        inner = f"<D:href>{escape(href)}</D:href>" if href else ""
        body = (
            f'<?xml version="1.0" encoding="utf-8"?><D:error xmlns:D="{_DAV}" xmlns:C="{_CALDAV}">'
            f"<C:{element}>{inner}</C:{element}><D:responsedescription>{escape(text)}</D:responsedescription></D:error>"
        ).encode()
        return self._reply(403, body, content_type="application/xml")

    # -- CalDAV -------------------------------------------------------------------

    def do_OPTIONS(self):
        if not self._check_access():
            return
        self._reply(200, b"", headers={"DAV": "1, 2, calendar-access", "Allow": "OPTIONS, GET, PUT, DELETE, PROPFIND, REPORT"})

    def do_PROPFIND(self):
        if not self._check_access():
            return
        self._read_body()
        depth = (self.headers.get("Depth") or "1").strip()
        path = self._path()
        entries: list[str] = []
        if path in {"/", ""}:
            for href, calendar in self.state.calendars.items():
                privileges = calendar.get("privileges")
                privilege_xml = "" if privileges is None else (
                    "<D:current-user-privilege-set>"
                    + "".join(f"<D:privilege><D:{name}/></D:privilege>" for name in privileges)
                    + "</D:current-user-privilege-set>"
                )
                entries.append(
                    f"<D:response><D:href>{href}</D:href><D:propstat><D:prop>"
                    f"<D:resourcetype><D:collection/><C:calendar xmlns:C=\"{_CALDAV}\"/></D:resourcetype>"
                    f"<D:displayname>{calendar['display_name']}</D:displayname>{privilege_xml}"
                    "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
                )
        else:
            calendar_href, name = self._calendar_for(path)
            if calendar_href is None:
                return self._reply(404, b"no such collection")
            calendar = self.state.calendars[calendar_href]
            if not name:
                entries.append(
                    f"<D:response><D:href>{calendar_href}</D:href><D:propstat><D:prop>"
                    f"<D:resourcetype><D:collection/><C:calendar xmlns:C=\"{_CALDAV}\"/></D:resourcetype>"
                    f"<D:displayname>{calendar['display_name']}</D:displayname>"
                    "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
                )
                if depth == "1":
                    for resource_name, row in calendar["events"].items():
                        entries.append(self._event_propstat(calendar_href, resource_name, row, with_data=False))
            elif name in calendar["events"]:
                entries.append(self._event_propstat(calendar_href, name, calendar["events"][name], with_data=False))
            else:
                return self._reply(404, b"no such event")
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<D:multistatus xmlns:D="{_DAV}" xmlns:C="{_CALDAV}">' + "".join(entries) + "</D:multistatus>"
        ).encode("utf-8")
        self._reply(207, body, content_type="application/xml")

    def _event_propstat(self, calendar_href: str, name: str, row: dict, *, with_data: bool, data: str | None = None) -> str:
        served = row["ics"] if data is None else data
        payload = f"<![CDATA[{served}]]>" if with_data else ""
        return (
            f"<D:response><D:href>{calendar_href}{name}.ics</D:href><D:propstat><D:prop>"
            f"<D:getetag>{row['etag']}</D:getetag>"
            f"<C:calendar-data xmlns:C=\"{_CALDAV}\">{payload}</C:calendar-data>"
            "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
        )

    def do_REPORT(self):
        if not self._check_access():
            return
        body = self._read_body()
        calendar_href, _name = self._calendar_for(self._path())
        if calendar_href is None:
            return self._reply(404, b"no such collection")
        try:
            tree = ET.fromstring(body)
        except ET.ParseError:
            return self._reply(400, b"bad report body")
        expand = tree.find(f".//{{{_CALDAV}}}expand") is not None
        time_range = tree.find(f".//{{{_CALDAV}}}time-range")
        uid_filter = tree.find(f".//{{{_CALDAV}}}prop-filter[@name='UID']/{{{_CALDAV}}}text-match")
        range_start = range_end = None
        if time_range is not None:
            range_start = self._ical_to_utc(time_range.get("start", ""))
            range_end = self._ical_to_utc(time_range.get("end", ""))
        # RFC 4791 section 9.7.5: text-match is a substring match, i;ascii-casemap by default.
        wanted_uid = (uid_filter.text or "").strip().lower() if uid_filter is not None else None
        with self.state.lock:
            rows = list(self.state.calendars[calendar_href]["events"].items())
            include_cancelled = self.state.expand_cancelled_instances
        entries: list[str] = []
        for name, row in rows:
            served = _served_for_query(row["ics"], wanted_uid=wanted_uid, range_start=range_start, range_end=range_end,
                                       expand=expand, include_cancelled=include_cancelled)
            if served is not None:
                entries.append(self._event_propstat(calendar_href, name, row, with_data=True, data=served))
        reply = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<D:multistatus xmlns:D="{_DAV}" xmlns:C="{_CALDAV}">' + "".join(entries) + "</D:multistatus>"
        ).encode("utf-8")
        self._reply(207, reply, content_type="application/xml")

    @staticmethod
    def _ical_to_utc(value: str) -> datetime:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

    def do_GET(self):
        if not self._check_access():
            return
        calendar_href, name = self._calendar_for(self._path())
        if calendar_href is None or not name:
            return self._reply(404, b"not an event resource")
        with self.state.lock:
            row = self.state.calendars[calendar_href]["events"].get(name)
        if row is None:
            return self._reply(404, b"no such event")
        self._reply(200, row["ics"].encode("utf-8"), content_type="text/calendar", headers={"ETag": row["etag"]})

    def do_PUT(self):
        if not self._check_access():
            return
        if self.state.refuse_writes_with:
            return self._reply(self.state.refuse_writes_with, b"write refused")
        body = self._read_body().decode("utf-8", "replace")
        calendar_href, name = self._calendar_for(self._path())
        if calendar_href is None or not name:
            return self._reply(404, b"no such collection")
        try:
            uid = _validated_uid(body)
        except _UNREADABLE as exc:
            return self._precondition("valid-calendar-object-resource", str(exc))
        refusal: tuple[int, str, str] | None = None
        created = False
        etag = ""
        with self.state.lock:
            calendar = self.state.calendars[calendar_href]
            existing = calendar["events"].get(name)
            if_none = self.headers.get("If-None-Match")
            if_match = self.headers.get("If-Match")
            existing_uids = _uids_of(existing["ics"]) if existing else set()
            if if_none == "*" and existing:
                refusal = (412, "", "event already exists")
            elif if_match and (not existing or _norm_etag(existing["etag"]) != _norm_etag(if_match)):
                refusal = (412, "", "etag mismatch")
            elif existing_uids and uid not in existing_uids:
                refusal = (403, f"{calendar_href}{name}.ics", "the PUT would change this resource's UID")
            else:
                created = not existing
                try:
                    # The provider APPLIES the change here; every injection below happens after.
                    etag = self.state.put_event(calendar_href, name, body)
                except UidConflict as conflict:
                    refusal = (403, conflict.href, "another resource in this collection already uses that UID")
        if refusal is not None:
            status, href, text = refusal
            if status == 412:
                return self._reply(412, text.encode("utf-8"))
            return self._precondition("no-uid-conflict", text, href=href)
        if self.state.hang_seconds > 0:
            # Applied, then the reply never arrives: the honest client outcome is UNKNOWN.
            import time

            time.sleep(self.state.hang_seconds)
        self._reply(201 if created else 204, b"", headers={"ETag": etag})

    def do_DELETE(self):
        if not self._check_access():
            return
        if self.state.refuse_writes_with:
            return self._reply(self.state.refuse_writes_with, b"write refused")
        calendar_href, name = self._calendar_for(self._path())
        if calendar_href is None or not name:
            return self._reply(404, b"not an event resource")
        with self.state.lock:
            row = self.state.calendars[calendar_href]["events"].pop(name, None)
        if row is None:
            return self._reply(404, b"no such event")
        if_match = self.headers.get("If-Match")
        if if_match and _norm_etag(row["etag"]) != _norm_etag(if_match):
            with self.state.lock:
                self.state.calendars[calendar_href]["events"][name] = row  # restore: not deleted
            return self._reply(412, b"etag mismatch")
        if self.state.hang_seconds > 0:
            import time

            time.sleep(self.state.hang_seconds)
        self._reply(204, b"")


def start_caldav_fixture(*, calendars: dict[str, str] | None = None, preferred_port: int = ALLOCATED_PORT):
    """Start the disposable service. Returns (server, state, base_url, port).

    Uses the mission's allocated port when free; otherwise binds an ephemeral port and
    the caller records the substitution (the mission law for occupied ports).
    """
    state = CalDavState()
    for href, display in (calendars or {"/calendars/vilnius/": "Vilnius"}).items():
        state.add_calendar(href, display)

    # A bound subclass per server, not a shared class attribute: two concurrent fixture
    # servers must keep separate states (the class attribute let the second server's state
    # answer requests that arrived at the first server's port).
    class _BoundHandler(_Handler):
        pass

    _BoundHandler.state = state

    def handler_factory(*args, **kwargs):
        return _BoundHandler(*args, **kwargs)
    port = preferred_port
    server = None
    for attempt in (0, 1):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
            break
        except OSError:
            if attempt == 1 or port == 0:
                raise
            port = 0  # ephemeral fallback, reported to the caller
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_port = server.server_address[1]
    return server, state, f"http://127.0.0.1:{actual_port}/", actual_port


__all__ = ["ALLOCATED_PORT", "CalDavState", "UidConflict", "start_caldav_fixture"]
