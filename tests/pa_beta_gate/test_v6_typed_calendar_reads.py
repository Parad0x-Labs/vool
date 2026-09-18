"""pa_beta_gate -- revision-6: a calendar read the runtime cannot use is typed, never empty, free or absent.

The independent review of revision 5 (originals in ``test_calendar_v5_independent_review.py``) showed a
Graph reply of the wrong shape read as an empty calendar, events with unreadable dates silently skipped
by the free-slot computation, and an HTTP 200 without the requested event read as ``not_found`` -- the
one reason recovery treats as proof of absence. The cases here are new data at the same class across
all four adapters (Google, Graph, CalDAV, EventKit) and the operator's own interval owner, plus the
readable forms that must keep working: genuinely empty calendars, cancelled instances, recurring
instances, all-day events, supported zone shapes, pagination and authentic 404/410.

LABELLED: providers are synthetic wires and an EventKit store double; no account, network or native
calendar is touched.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.kas.adapters.caldav import CalDavCalendarAdapter
from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter
from core.kas.contract import AdapterConfig, CalendarReadUnusableError, CalendarRefusedError, CalEvent, KasResponse
from core.operator.calendar_provider import _find_conflicts, compute_free_slots
from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter, response

_WINDOW = {"start_utc": "2026-09-15T12:00:00+00:00", "end_utc": "2026-09-15T18:00:00+00:00"}
_START, _END = datetime(2026, 9, 15, 12, tzinfo=timezone.utc), datetime(2026, 9, 15, 18, tzinfo=timezone.utc)


def _timed(event_id, start, end, **extra):
    return {"id": event_id, "summary": f"row {event_id}", "start": start, "end": end, **extra}


_VALID_G = _timed("g-ok", {"dateTime": "2026-09-15T13:00:00+00:00"}, {"dateTime": "2026-09-15T13:30:00+00:00"})
_VALID_H = _timed("h-ok", {"dateTime": "2026-09-15T13:00:00.0000000", "timeZone": "UTC"}, {"dateTime": "2026-09-15T13:30:00.0000000", "timeZone": "UTC"})


def _read_range(provider, *pages):
    """events_in_range over a synthetic wire serving ``pages`` in order (the last page repeats)."""
    served = list(pages)

    def wire(req):
        body = served.pop(0) if len(served) > 1 else served[0]
        return body if isinstance(body, KasResponse) else response(body)

    return adapter(provider, wire).events_in_range("work", **_WINDOW)


# ---------------------------------------------------------------------------------------------
# Google / Graph collection reads
# ---------------------------------------------------------------------------------------------

_UNREADABLE_RANGES = [
    pytest.param("graph", [{"value": {"id": "h1"}}], id="graph-value-not-a-list"),
    pytest.param("graph", [{"value": [_VALID_H, ["opaque-row"]]}], id="graph-row-not-an-object"),
    pytest.param("graph", [{"value": [{**_VALID_H, "id": ""}]}], id="graph-row-without-id"),
    pytest.param("graph", [{"value": [_timed("h2", {"dateTime": "2026-09-15T14:00:00"}, {"dateTime": "2026-09-15T14:30:00"})]}], id="graph-wall-time-without-zone"),
    pytest.param("graph", [{"value": [_timed("h3", {"dateTime": "2026-09-15T14:00:00.0000000", "timeZone": "Pacific Standard Time"},
                                             {"dateTime": "2026-09-15T14:30:00.0000000", "timeZone": "Pacific Standard Time"})]}], id="graph-unresolvable-zone"),
    pytest.param("graph", [{"value": [{**_timed("h4", {"dateTime": "15/09/2026", "timeZone": "UTC"}, {"dateTime": "16/09/2026", "timeZone": "UTC"}), "isAllDay": True}]}],
                 id="graph-all-day-unreadable-date"),
    pytest.param("google", [{"kind": "calendar#calendarList"}], id="google-page-of-another-collection"),
    pytest.param("google", [{"items": "none"}], id="google-items-not-a-list"),
    pytest.param("google", [{"items": [_timed("g1", {"dateTime": "2026-09-15T14:00:00+00:00"}, None)]}], id="google-row-without-end"),
    pytest.param("google", [{"items": [_timed("g2", {"dateTime": "2026-09-15T15:00:00+00:00"}, {"dateTime": "2026-09-15T14:00:00+00:00"})]}], id="google-end-before-start"),
    pytest.param("google", [{"items": [_timed("g3", {"date": "2026-02-30"}, {"date": "2026-03-01"})]}], id="google-impossible-all-day-date"),
    pytest.param("google", [{"items": [_timed("g6", {"date": "2026-09-16"}, {"date": "2026-09-15"})]}], id="google-all-day-ends-before-it-starts"),
    pytest.param("google", [{"items": [_timed("g4", {"date": "2026-09-15"}, {"dateTime": "2026-09-15T18:00:00+00:00"})]}], id="google-all-day-start-timed-end"),
    pytest.param("google", [KasResponse(status=200, body=b"<html>sign in</html>")], id="google-body-not-json"),
    pytest.param("google", [KasResponse(status=200, body=b"")], id="google-empty-body"),
    pytest.param("google", [{"items": [_VALID_G], "nextPageToken": "p2"}, {"items": [{"id": "g5", "status": "confirmed"}]}], id="google-second-page-row-without-times"),
]


@pytest.mark.parametrize(("provider", "pages"), _UNREADABLE_RANGES)
def test_unreadable_range_reply_is_typed_never_an_empty_or_partial_calendar(provider, pages):
    with pytest.raises(CalendarReadUnusableError) as captured:
        _read_range(provider, *pages)
    assert isinstance(captured.value, CalendarRefusedError) and captured.value.reason == "unreadable_response"


_READABLE_RANGES = [
    pytest.param("graph", [{"value": []}], [], id="graph-genuinely-empty"),
    pytest.param("google", [{"items": []}], [], id="google-genuinely-empty"),
    pytest.param("google", [{"kind": "calendar#events", "summary": "Work"}], [], id="google-empty-page-without-items"),
    pytest.param("google", [{"items": [{"id": "series1_20260915T100000Z", "status": "cancelled", "recurringEventId": "series1"}, _VALID_G]}], ["g-ok"],
                 id="google-cancelled-instance-set-aside"),
    pytest.param("google", [{"items": [_timed("series2_20260915T120000Z", {"dateTime": "2026-09-15T15:00:00+03:00", "timeZone": "Europe/Berlin"},
                                              {"dateTime": "2026-09-15T15:45:00+03:00", "timeZone": "Europe/Berlin"})]}], ["series2_20260915T120000Z"],
                 id="google-recurring-instance"),
    pytest.param("google", [{"items": [_timed("g-zero", {"dateTime": "2026-09-15T16:00:00Z"}, {"dateTime": "2026-09-15T16:00:00Z"})]}], ["g-zero"], id="google-zero-duration"),
    pytest.param("google", [{"items": [_timed("g-day", {"date": "2026-09-15"}, {"date": "2026-09-16"})]}], ["g-day"], id="google-all-day"),
    pytest.param("graph", [{"value": [{**_timed("h-day", {"dateTime": "2026-09-15T00:00:00.0000000", "timeZone": "Pacific Standard Time"},
                                               {"dateTime": "2026-09-16T00:00:00.0000000", "timeZone": "Pacific Standard Time"}), "isAllDay": True}]}], ["h-day"],
                 id="graph-all-day-with-windows-zone-name"),
    pytest.param("graph", [{"value": [_VALID_H], "@odata.nextLink": "https://calendar.example.test/api/me/calendars/work/calendarview?$skiptoken=2"},
                           {"value": [{**_VALID_H, "id": "h-page2"}]}], ["h-ok", "h-page2"], id="graph-two-pages"),
]


@pytest.mark.parametrize(("provider", "pages", "uids"), _READABLE_RANGES)
def test_readable_range_forms_stay_usable(provider, pages, uids):
    rows = _read_range(provider, *pages)
    assert sorted(row.uid for row in rows) == sorted(uids)
    compute_free_slots(rows, window_start=_START, window_end=_END, duration_minutes=30, tz_name="UTC")
    _find_conflicts(rows, _START, _END)


def test_supported_zone_shapes_translate_to_the_same_instant():
    rows = _read_range("graph", {"value": [
        _timed("a", {"dateTime": "2026-09-15T15:00:00.0000000", "timeZone": "Europe/Berlin"}, {"dateTime": "2026-09-15T15:30:00.0000000", "timeZone": "Europe/Berlin"}),
        _timed("b", {"dateTime": "2026-09-15T12:00:00.0000000", "timeZone": "UTC"}, {"dateTime": "2026-09-15T12:30:00.0000000", "timeZone": "UTC"}),
        _timed("c", {"dateTime": "2026-09-15T15:00:00+03:00"}, {"dateTime": "2026-09-15T15:30:00+03:00"}),
        _timed("d", {"dateTime": "2026-09-15T12:00:00Z"}, {"dateTime": "2026-09-15T12:30:00Z"}),
    ]})
    assert {row.start_utc for row in rows} == {"2026-09-15T12:00:00+00:00"}
    assert {row.uid: row.tz_name for row in rows} == {"a": "Europe/Berlin", "b": "UTC", "c": "", "d": ""}


# ---------------------------------------------------------------------------------------------
# Google / Graph single-event reads and calendar lists
# ---------------------------------------------------------------------------------------------

_UNREADABLE_SINGLE = [
    pytest.param("google", response([]), id="google-json-array"),
    pytest.param("google", KasResponse(status=200, body=b"{truncated"), id="google-not-json"),
    pytest.param("google", response({"id": "wanted-id", "status": "cancelled"}), id="google-tombstone-without-times"),
    pytest.param("graph", response({**_VALID_H, "id": "someone-else"}), id="graph-different-event"),
    pytest.param("graph", response({**_VALID_H, "id": "wanted-id", "start": {"dateTime": "soon", "timeZone": "UTC"}}), id="graph-unreadable-start"),
]


@pytest.mark.parametrize(("provider", "reply"), _UNREADABLE_SINGLE)
def test_success_reply_that_is_not_the_event_is_unreadable_not_absent(provider, reply):
    with pytest.raises(CalendarReadUnusableError) as captured:
        adapter(provider, lambda req: reply).get_event("work", "wanted-id")
    assert captured.value.reason == "unreadable_response"


@pytest.mark.parametrize("status", [404, 410])
@pytest.mark.parametrize("provider", ["google", "graph"])
def test_authentic_absence_stays_not_found(provider, status):
    with pytest.raises(CalendarRefusedError) as captured:
        adapter(provider, lambda req: response({"error": {"code": "gone"}}, status)).get_event("work", "wanted-id")
    assert captured.value.reason == "not_found" and not isinstance(captured.value, CalendarReadUnusableError)


@pytest.mark.parametrize("provider", ["google", "graph"])
def test_the_event_asked_for_is_read(provider):
    body = {**(_VALID_G if provider == "google" else _VALID_H), "id": "wanted-id"}
    event = adapter(provider, lambda req: response(body)).get_event("work", "wanted-id")
    assert (event.uid, event.start_utc) == ("wanted-id", "2026-09-15T13:00:00+00:00")


@pytest.mark.parametrize(("provider", "reply", "calendars"), [
    pytest.param("graph", {}, None, id="graph-reply-without-value"),
    pytest.param("google", {"items": [{"summary": "Nameless"}, {"id": "work", "summary": "Work"}]}, None, id="google-calendar-without-id"),
    pytest.param("graph", {"value": [{"name": "Nameless"}, {"id": "work", "name": "Work"}]}, None, id="graph-calendar-without-id"),
    pytest.param("google", {"kind": "calendar#calendarList"}, [], id="google-empty-list-page"),
    pytest.param("graph", {"value": [{"id": "work", "name": "Work"}]}, ["work"], id="graph-one-calendar"),
])
def test_calendar_list_is_never_silently_narrowed(provider, reply, calendars):
    """A calendar dropped from the list could make another one look like the only calendar."""
    instance = adapter(provider, lambda req: response(reply))
    if calendars is None:
        with pytest.raises(CalendarReadUnusableError):
            instance.list_calendars()
    else:
        assert [row.calendar_id for row in instance.list_calendars()] == calendars


# ---------------------------------------------------------------------------------------------
# CalDAV
# ---------------------------------------------------------------------------------------------

_DAV_ROOT = '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">{}</D:multistatus>'
_DAV_ROW = ('<D:response><D:href>/dav/user/work/{uid}.ics</D:href><D:propstat><D:prop><D:getetag>"e1"</D:getetag>'
            '<C:calendar-data>{data}</C:calendar-data></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>')


def _multistatus(*rows):
    return KasResponse(status=207, body=_DAV_ROOT.format("".join(rows)).encode("utf-8"))


def _vcal(*lines, component="VEVENT"):
    return "\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", f"BEGIN:{component}", *lines, f"END:{component}", "END:VCALENDAR"])


def _row(uid, data):
    return _DAV_ROW.format(uid=uid, data=data)


def _caldav(reply):
    return CalDavCalendarAdapter(transport=lambda req: reply, config=AdapterConfig(provider_id="caldav", base_url="https://calendar.example.test/dav/user/"))


_CALDAV_UNREADABLE = [
    pytest.param(_multistatus(_row("a", _vcal("UID:a", "SUMMARY:No start"))), id="vevent-without-dtstart"),
    pytest.param(_multistatus(_row("b", _vcal("UID:b", "DTSTART:2026-09-15T12:00:00Z", "DTEND:2026-09-15T12:30:00Z"))), id="dtstart-not-in-ical-form"),
    pytest.param(_multistatus(_row("c", _vcal("SUMMARY:No uid", "DTSTART:20260915T120000Z", "DTEND:20260915T123000Z"))), id="vevent-without-uid"),
    pytest.param(_multistatus(_row("d", _vcal("UID:d", "DTSTART:20260915T120000Z", component="VTODO"))), id="object-without-vevent"),
    pytest.param(_multistatus(_row("e", "")), id="empty-calendar-data"),
    pytest.param(_multistatus('<D:response><D:href>/dav/user/work/f.ics</D:href><D:propstat><D:prop><C:calendar-data/></D:prop>'
                              '<D:status>HTTP/1.1 404 Not Found</D:status></D:propstat></D:response>'), id="calendar-data-not-served"),
    pytest.param(KasResponse(status=207, body=b'<?xml version="1.0"?><D:error xmlns:D="DAV:"><D:need-privileges/></D:error>'), id="reply-not-a-multistatus"),
    pytest.param(_multistatus(_row("g", _vcal("UID:g", "DTSTART:20260915T120000Z", "DTEND:20260915T123000Z")),
                              _row("h", _vcal("UID:h", "DTSTART;VALUE=DATE:20260945"))), id="second-object-impossible-date"),
]


@pytest.mark.parametrize("reply", _CALDAV_UNREADABLE)
def test_caldav_unreadable_objects_are_typed_for_ranges_and_single_reads(reply):
    with pytest.raises(CalendarReadUnusableError):
        _caldav(reply).events_in_range("/dav/user/work/", **_WINDOW)
    with pytest.raises(CalendarReadUnusableError):
        _caldav(reply).get_event("/dav/user/work/", "a")


def test_caldav_readable_forms_and_authentic_absence():
    empty = _multistatus()
    assert _caldav(empty).events_in_range("/dav/user/work/", **_WINDOW) == []
    with pytest.raises(CalendarRefusedError) as absent:
        _caldav(empty).get_event("/dav/user/work/", "missing-uid")
    assert absent.value.reason == "not_found" and not isinstance(absent.value, CalendarReadUnusableError)
    reply = _multistatus(
        _row("zoned", _vcal("UID:zoned", "DTSTART;TZID=Europe/Berlin:20260915T150000", "DTEND;TZID=Europe/Berlin:20260915T153000")),
        _row("day", _vcal("UID:day", "DTSTART;VALUE=DATE:20260915")),
        _row("instant", _vcal("UID:instant", "DTSTART:20260915T130000Z")),
    )
    rows = {row.uid: row for row in _caldav(reply).events_in_range("/dav/user/work/", **_WINDOW)}
    assert (rows["zoned"].start_utc, rows["zoned"].tz_name) == ("2026-09-15T12:00:00+00:00", "Europe/Berlin")
    assert rows["day"].all_day and (rows["day"].start_date, rows["day"].end_date) == ("2026-09-15", "2026-09-16")
    assert rows["instant"].end_utc == rows["instant"].start_utc, "RFC 5545: a timed DTSTART without DTEND has zero duration"
    assert _caldav(reply).get_event("/dav/user/work/", "zoned").uid == "zoned"


def test_floating_caldav_time_is_reported_by_the_adapter_and_never_placed_as_free():
    reply = _multistatus(_row("float", _vcal("UID:float", "DTSTART:20260915T140000", "DTEND:20260915T150000")))
    rows = _caldav(reply).events_in_range("/dav/user/work/", **_WINDOW)
    assert (rows[0].start_utc, rows[0].tz_name) == ("2026-09-15T14:00:00", "")
    with pytest.raises(CalendarReadUnusableError):
        compute_free_slots(rows, window_start=_START, window_end=_END, duration_minutes=30, tz_name="UTC")
    with pytest.raises(CalendarReadUnusableError):
        _find_conflicts(rows, _START, _END)


# ---------------------------------------------------------------------------------------------
# EventKit (store double)
# ---------------------------------------------------------------------------------------------


class _Date:
    def __init__(self, hour):
        self._at = datetime(2026, 9, 15, hour, tzinfo=timezone.utc).timestamp()

    def timeIntervalSince1970(self):
        return self._at


class _NativeEvent:
    def __init__(self, identifier="ek-1", start=12, end=13):
        self._identifier, self._start, self._end = identifier, start, end

    def eventIdentifier(self):
        return self._identifier

    def isAllDay(self):
        return False

    def lastModifiedDate(self):
        return "modified-1"

    def startDate(self):
        return None if self._start is None else _Date(self._start)

    def endDate(self):
        return None if self._end is None else _Date(self._end)

    def calendar(self):
        return None

    def title(self):
        return "Native appointment"

    def notes(self):
        return ""


class _Store:
    """LABELLED EventKit store double serving native-shaped objects; never a real event store."""

    def __init__(self, *events):
        self.events = list(events)

    def events_in_range(self, start, end, calendar_ids):
        return list(self.events)

    def event_by_id(self, uid):
        for event in self.events:
            if event.eventIdentifier() == uid:
                return event
        if self.events and uid == "served-without-identifier":
            return self.events[0]
        raise CalendarRefusedError(404, reason="not_found", detail="no such event")


def _eventkit(*events):
    instance = EventKitCalendarAdapter(transport=lambda req: None, config=AdapterConfig(provider_id="eventkit", base_url="eventkit://local"), store=_Store(*events))
    instance._foundation = object()  # the selector bridge is exercised through the doubles; no pyobjc import
    return instance


@pytest.mark.parametrize(("native", "why"), [
    pytest.param(_NativeEvent(start=None), "no start date", id="missing-start"),
    pytest.param(_NativeEvent(end=None), "no end date", id="missing-end"),
    pytest.param(_NativeEvent(identifier=""), "no identifier", id="missing-identifier"),
])
def test_eventkit_event_that_cannot_be_placed_is_typed_not_dropped_or_now(native, why):
    with pytest.raises(CalendarReadUnusableError) as captured:
        _eventkit(_NativeEvent(identifier="ek-ok"), native).events_in_range("work", **_WINDOW)
    assert why in captured.value.detail


def test_eventkit_single_read_distinguishes_unreadable_from_absent():
    with pytest.raises(CalendarReadUnusableError):
        _eventkit(_NativeEvent(identifier="", start=None)).get_event("work", "served-without-identifier")
    with pytest.raises(CalendarRefusedError) as absent:
        _eventkit(_NativeEvent(identifier="ek-ok")).get_event("work", "ek-missing")
    assert absent.value.reason == "not_found"
    assert _eventkit(_NativeEvent(identifier="ek-ok")).get_event("work", "ek-ok").start_utc == "2026-09-15T12:00:00+00:00"


# ---------------------------------------------------------------------------------------------
# The operator's interval owner
# ---------------------------------------------------------------------------------------------


def _cal(uid, start, end):
    return CalEvent(provider_id="any", uid=uid, calendar_id="work", summary=uid, start_utc=start, end_utc=end)


@pytest.mark.parametrize("event", [
    pytest.param(_cal("unreadable", "tuesday", "2026-09-15T13:00:00+00:00"), id="unreadable-start"),
    pytest.param(_cal("wall", "2026-09-15T13:00:00", "2026-09-15T14:00:00"), id="wall-time-without-zone"),
    pytest.param(_cal("inverted", "2026-09-15T15:00:00+00:00", "2026-09-15T14:00:00+00:00"), id="ends-before-it-starts"),
    pytest.param(_cal("blank", "", ""), id="no-times-at-all"),
])
def test_consumers_never_skip_an_event_they_cannot_place(event):
    busy = _cal("busy", "2026-09-15T12:00:00+00:00", "2026-09-15T12:30:00+00:00")
    with pytest.raises(CalendarReadUnusableError):
        compute_free_slots([busy, event], window_start=_START, window_end=_END, duration_minutes=30, tz_name="UTC")
    with pytest.raises(CalendarReadUnusableError):
        _find_conflicts([busy, event], datetime(2026, 9, 15, 14, tzinfo=timezone.utc), datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc))


def test_consumer_controls_zero_duration_all_day_and_timed_stay_usable():
    rows = [_cal("zero", "2026-09-15T13:00:00+00:00", "2026-09-15T13:00:00+00:00"), _cal("timed", "2026-09-15T12:00:00Z", "2026-09-15T13:00:00Z")]
    slots = compute_free_slots(rows, window_start=_START, window_end=_END, duration_minutes=30, tz_name="UTC")
    assert slots and slots[0]["start_utc"] == "2026-09-15T13:00:00+00:00"
    overlapping = _find_conflicts(rows, datetime(2026, 9, 15, 12, 15, tzinfo=timezone.utc), datetime(2026, 9, 15, 12, 45, tzinfo=timezone.utc))
    assert [event.uid for event in overlapping] == ["timed"]
    all_day = CalEvent(provider_id="any", uid="day", calendar_id="work", all_day=True, start_date="2026-09-15", end_date="2026-09-16")
    assert compute_free_slots([all_day], window_start=_START, window_end=_END, duration_minutes=30, tz_name="UTC") == []
