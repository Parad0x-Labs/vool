"""pa_beta_gate -- product completion: what an alert knows about the event it announces.

Rows: where the event is held and how to join it (5, 14), each calendar's write capability and the provider's own
default (2), cancelled events (17), and occurrence identity inside a repeating series (8, 17). One case per adapter
family: Google Calendar, Microsoft Graph, CalDAV and EventKit.

Provider semantics, read from each provider's current reference before the adapters changed:

* Google Calendar event: ``location`` is free text; ``hangoutLink``; ``conferenceData.entryPoints[]`` whose
  ``entryPointType`` is video, phone, sip or more, a video ``uri`` being an http(s) address; ``htmlLink``;
  ``status`` cancelled. calendarList ``accessRole`` is freeBusyReader, reader, writerWithoutPrivateAccess, writer or
  owner (the last three may write); ``primary`` marks the user's main calendar.
* Microsoft Graph: event ``location.displayName``, ``onlineMeeting.joinUrl``, ``webLink``, ``isCancelled``,
  ``seriesMasterId`` and ``originalStart``; calendar ``canEdit`` and ``isDefaultCalendar``.
* iCalendar and CalDAV: RFC 5545 LOCATION, URL, STATUS and RECURRENCE-ID; RFC 7986 CONFERENCE, a URI with a FEATURE
  parameter such as VIDEO or PHONE and an optional LABEL; RFC 3744 DAV:current-user-privilege-set, where DAV:write
  contains DAV:bind and DAV:write-content and DAV:all contains everything.
* EventKit: EKCalendarItem ``location`` and ``URL``; EKEvent ``status`` (none, confirmed, tentative, canceled),
  ``occurrenceDate`` (the original occurrence date of an event in a recurring series) and ``isDetached``; EKCalendar
  ``allowsContentModifications``.

LABELLED: loopback provider fixtures reached through the VOOL transport, and an EventKit store double. No live
account, no native event store and no OS notification are exercised in this file.
"""
from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from ._caldav_service import start_caldav_fixture
from ._json_api_service import start_json_calendar_fixture
from ._pc_calendar_rig import T0, Clock, api_get, bell, connect, prepare_home, sweep

pytestmark = [pytest.mark.pa_beta]

WORK, FAMILY, HOLIDAYS, SHARED = "work@fixture.test", "family@fixture.test", "holidays@fixture.test", "shared@fixture.test"
OPERATIONS, PERSONAL = "AAMkAGOperations=", "AAMkAGPersonal="
TEAM, HOME = "/calendars/team/", "/calendars/home/"


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def google(home):
    server, state, base = start_json_calendar_fixture(dialect="google", calendars={WORK: "Work", FAMILY: "Family"})
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def graph(home):
    server, state, base = start_json_calendar_fixture(dialect="graph", calendars={OPERATIONS: "Operations"})
    yield state, base
    server.shutdown()
    server.server_close()


@pytest.fixture
def caldav(home):
    server, state, base, _port = start_caldav_fixture(calendars={TEAM: "Team"}, preferred_port=0)
    yield state, base
    server.shutdown()
    server.server_close()


def _google_when(instant):
    return {"dateTime": instant.astimezone(timezone.utc).isoformat(timespec="seconds"), "timeZone": "Europe/Berlin"}


def _graph_when(instant):
    """Graph's wire shape: a wall time without an offset and the zone named beside it."""
    return {"dateTime": instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


def _ics(*lines):
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN", "BEGIN:VEVENT",
                        "DTSTAMP:20260914T120000Z", *lines, "END:VEVENT", "END:VCALENDAR"]) + "\r\n"


# ---------------------------------------------------------------------------------------------
# Where and how to join: original, novel and control
# ---------------------------------------------------------------------------------------------


def test_google_location_and_meet_link_reach_the_alert_card(google, monkeypatch):
    """ORIGINAL: a Google event with a room and a Meet conference; the alert says where it is and how to join."""
    state, base = google
    clock = Clock(T0, monkeypatch)
    start = T0 + timedelta(minutes=45)
    state.put_event(WORK, {
        "id": "evt-weekly-sync", "summary": "Weekly sync", "start": _google_when(start), "end": _google_when(start + timedelta(minutes=30)),
        "location": "Room 4.2, Gedimino pr. 9, Vilnius",
        "hangoutLink": "https://meet.google.com/abc-defg-hij",
        "conferenceData": {"entryPoints": [
            {"entryPointType": "phone", "uri": "tel:+370-5-200-0000"},
            {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
        ]},
    }, etag='"v1"')
    account_id = connect("google", clock, base_url=base, select=[WORK])
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    [upcoming] = calendar_alerts.upcoming_alerts(now_fn=clock)
    assert (upcoming["location"], upcoming["meeting_url"]) == (
        "Room 4.2, Gedimino pr. 9, Vilnius", "https://meet.google.com/abc-defg-hij"), upcoming
    status, view = api_get("/api/calendar/alerts")
    [event] = view["events"]
    assert status == 200 and event["meeting_url"] == "https://meet.google.com/abc-defg-hij", event
    assert event["location"] == "Room 4.2, Gedimino pr. 9, Vilnius", event

    clock.advance(minutes=31)
    assert sweep(clock)["delivered"] == 1
    [item] = bell()["items"]
    card = item["payload"]
    assert (card["location"], card["meeting_url"]) == ("Room 4.2, Gedimino pr. 9, Vilnius", "https://meet.google.com/abc-defg-hij"), card
    assert "Room 4.2" in item["body"], item


def test_graph_teams_link_and_cancelled_occurrences_of_a_series(graph, monkeypatch):
    """NOVEL: Microsoft Graph -- a Teams meeting's join link, and a daily series where one occurrence is cancelled
    before the first sync and the next one is cancelled after its alert was scheduled."""
    state, base = graph
    clock = Clock(T0, monkeypatch)
    review = T0 + timedelta(hours=2)
    state.put_event(OPERATIONS, {
        "id": "AAMkQuarterReview=", "subject": "Quarter review", "type": "singleInstance",
        "start": _graph_when(review), "end": _graph_when(review + timedelta(hours=1)),
        "location": {"displayName": "Microsoft Teams Meeting"}, "isOnlineMeeting": True,
        "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_fixture%40thread.v2/0"},
    }, etag='"q1"')
    for day, cancelled in ((0, True), (1, False)):
        begins = T0 + timedelta(days=day, hours=1, minutes=30)
        state.put_event(OPERATIONS, {
            "id": f"AAMkStandup-{day}=", "subject": "Stand-up", "type": "occurrence", "seriesMasterId": "AAMkStandupSeries=",
            "originalStart": begins.strftime("%Y-%m-%dT%H:%M:%SZ"), "isCancelled": cancelled,
            "start": _graph_when(begins), "end": _graph_when(begins + timedelta(minutes=15)),
        }, etag=f'"s{day}"')
    account_id = connect("graph", clock, base_url=base, select=[OPERATIONS])
    from core.operator import calendar_alerts

    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] and report["events_seen"] == 3 and report["alerts_scheduled"] == 2, report
    alerts = calendar_alerts.upcoming_alerts(now_fn=clock)
    tomorrow = T0 + timedelta(days=1, hours=1, minutes=30)
    assert [(alert["title"], alert["start_utc"]) for alert in alerts] == [
        ("Quarter review", review.isoformat()), ("Stand-up", tomorrow.isoformat())], alerts
    assert alerts[0]["location"] == "Microsoft Teams Meeting", alerts[0]
    assert alerts[0]["meeting_url"] == "https://teams.microsoft.com/l/meetup-join/19%3ameeting_fixture%40thread.v2/0", alerts[0]

    state.calendars[OPERATIONS]["events"]["AAMkStandup-1="]["event"]["isCancelled"] = True
    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] and report["alerts_superseded"] == 1 and report["alerts_scheduled"] == 0, report
    assert [alert["title"] for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == ["Quarter review"]


def test_insecure_or_description_links_never_become_join_links(google, monkeypatch):
    """CONTROL: an http conference address, a script address, a link inside the description and a link typed as the
    location stay what they are; the alert offers no join link."""
    state, base = google
    clock = Clock(T0, monkeypatch)
    start = T0 + timedelta(minutes=45)
    state.put_event(WORK, {
        "id": "evt-vendor-call", "summary": "Vendor call", "start": _google_when(start), "end": _google_when(start + timedelta(minutes=30)),
        "description": "Join here instead: https://join.example.net/vendor",
        "location": "https://maps.example.net/?q=office",
        "hangoutLink": "javascript:alert(1)",
        "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "http://video.example.net/vendor"}]},
    }, etag='"v1"')
    account_id = connect("google", clock, base_url=base, select=[WORK])
    from core.operator import calendar_alerts

    assert calendar_alerts.sync_account(account_id, now_fn=clock)["alerts_scheduled"] == 1
    clock.advance(minutes=31)
    assert sweep(clock)["delivered"] == 1
    [item] = bell()["items"]
    assert item["payload"]["meeting_url"] == "" and item["payload"]["web_url"] == "https://fixture.test/events/evt-vendor-call", item


def test_caldav_conference_location_url_and_cancelled_status(caldav, monkeypatch):
    """NOVEL: a CalDAV server -- RFC 7986 CONFERENCE lines (a phone line first, then a video room whose quoted label
    holds a colon), an escaped LOCATION, the URL property as the calendar link, and STATUS:CANCELLED."""
    state, base = caldav
    clock = Clock(T0, monkeypatch)
    state.put_event(TEAM, "planning-1", _ics(
        "UID:planning-1", "SUMMARY:Sprint planning",
        "DTSTART;TZID=Europe/Berlin:20260921T104500", "DTEND;TZID=Europe/Berlin:20260921T113000",
        "LOCATION:Room 1\\, Floor 2",
        "CONFERENCE;VALUE=URI;FEATURE=PHONE;LABEL=Dial-in:tel:+370-5-200-0001",
        'CONFERENCE;VALUE=URI;FEATURE=VIDEO,SCREEN;LABEL="Room: 17":https://meet.example.org/room-17',
        "URL:https://calendar.example.org/events/planning-1",
    ))
    state.put_event(TEAM, "retro-1", _ics(
        "UID:retro-1", "SUMMARY:Retro", "DTSTART:20260921T080000Z", "DTEND:20260921T083000Z", "STATUS:CANCELLED",
    ))
    account_id = connect("caldav", clock, base_url=base, select=[TEAM])
    from core.operator import calendar_alerts

    report = calendar_alerts.sync_account(account_id, now_fn=clock)
    assert report["ok"] and report["events_seen"] == 2 and report["alerts_scheduled"] == 1, report
    [alert] = calendar_alerts.upcoming_alerts(now_fn=clock)
    assert (alert["title"], alert["location"], alert["meeting_url"]) == (
        "Sprint planning", "Room 1, Floor 2", "https://meet.example.org/room-17"), alert
    clock.advance(minutes=31)
    assert sweep(clock)["delivered"] == 1
    [item] = bell()["items"]
    assert item["payload"]["web_url"] == "https://calendar.example.org/events/planning-1", item


# ---------------------------------------------------------------------------------------------
# Write capability and the provider's default
# ---------------------------------------------------------------------------------------------


def test_discovery_reports_write_capability_and_the_providers_default(google, graph, caldav, monkeypatch):
    """Each provider's own statement of write access and default calendar; "not stated" stays distinct from
    read-only, and a read-only calendar cannot become the default write calendar."""
    google_state, google_base = google
    graph_state, graph_base = graph
    caldav_state, caldav_base = caldav
    clock = Clock(T0, monkeypatch)
    google_state.set_listing(WORK, accessRole="owner", primary=True)
    google_state.set_listing(FAMILY, accessRole="writerWithoutPrivateAccess")
    google_state.add_calendar(HOLIDAYS, "Holidays")
    google_state.set_listing(HOLIDAYS, accessRole="freeBusyReader")
    google_state.add_calendar(SHARED, "Shared")
    graph_state.set_listing(OPERATIONS, canEdit=False, isDefaultCalendar=False)
    graph_state.add_calendar(PERSONAL, "Calendar")
    graph_state.set_listing(PERSONAL, canEdit=True, isDefaultCalendar=True)
    caldav_state.set_privileges(TEAM, ["read", "read-current-user-privilege-set"])
    caldav_state.add_calendar(HOME, "Home")
    caldav_state.set_privileges(HOME, ["read", "write"])
    from core.operator import calendar_accounts

    accounts, capability = {}, {}
    for provider, base in (("google", google_base), ("graph", graph_base), ("caldav", caldav_base)):
        accounts[provider] = connect(provider, clock, base_url=base)
        for row in calendar_accounts.list_selections(accounts[provider]):
            capability[(provider, row["display_name"])] = (row["can_write"], row["provider_default"])
    assert capability == {
        ("google", "Work"): (True, True), ("google", "Family"): (True, False), ("google", "Holidays"): (False, False),
        ("google", "Shared"): (None, False),
        ("graph", "Operations"): (False, False), ("graph", "Calendar"): (True, True),
        ("caldav", "Team"): (False, False), ("caldav", "Home"): (True, False),
    }, capability

    refused = calendar_accounts.select_calendar(accounts["graph"], OPERATIONS, selected=True, default_write=True)
    assert not refused["ok"] and refused["reason"] == "calendar_read_only", refused
    assert calendar_accounts.select_calendar(accounts["graph"], PERSONAL, selected=True, default_write=True)["ok"]
    status, view = api_get("/api/calendar/alerts")
    rows = {(account["provider"], calendar["display_name"]): calendar for account in view["accounts"] for calendar in account["calendars"]}
    assert status == 200 and rows[("graph", "Calendar")]["is_default_write"] and not rows[("graph", "Operations")]["is_default_write"], rows
    assert rows[("graph", "Operations")]["selected"] is False, "a refused choice changes nothing"
    assert rows[("google", "Shared")]["can_write"] is None


# ---------------------------------------------------------------------------------------------
# EventKit: occurrences of one series
# ---------------------------------------------------------------------------------------------


class _NativeDate:
    def __init__(self, instant):
        self._at = instant.timestamp()

    def timeIntervalSince1970(self):
        return self._at


class _NativeCalendar:
    def __init__(self, identifier, title, writable):
        self._identifier, self._title, self._writable = identifier, title, writable

    def calendarIdentifier(self):
        return self._identifier

    def title(self):
        return self._title

    def allowsContentModifications(self):
        return self._writable

    def source(self):
        return None

    def timeZone(self):
        return None


class _NativeOccurrence:
    """One occurrence the way EventKit serves a repeating event: every occurrence carries the series'
    eventIdentifier, and occurrenceDate is its original start."""

    def __init__(self, calendar, *, start, status=1):
        self.calendar_object, self.start, self.original, self.detached, self.state = calendar, start, start, False, status

    def eventIdentifier(self):
        return "EK-SERIES-STANDUP"

    def calendar(self):
        return self.calendar_object

    def title(self):
        return "Stand-up"

    def notes(self):
        return ""

    def location(self):
        return "Kitchen table"

    def URL(self):
        return None

    def isAllDay(self):
        return False

    def startDate(self):
        return _NativeDate(self.start)

    def endDate(self):
        return _NativeDate(self.start + timedelta(minutes=15))

    def occurrenceDate(self):
        return _NativeDate(self.original)

    def isDetached(self):
        return self.detached

    def hasRecurrenceRules(self):
        return True

    def status(self):
        return self.state

    def lastModifiedDate(self):
        return f"modified-{self.start.isoformat()}"


class _NativeStore:
    """LABELLED EventKit store double serving native-shaped objects; never a real event store."""

    def __init__(self, calendars, occurrences):
        self._calendars, self.occurrences = list(calendars), list(occurrences)

    def calendars(self):
        return list(self._calendars)

    def events_in_range(self, start, end, calendar_ids):
        return [item for item in self.occurrences
                if start <= item.start < end and (not calendar_ids or item.calendar_object.calendarIdentifier() in calendar_ids)]


def _eventkit_factory(store):
    from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter
    from core.kas.contract import AdapterConfig

    def factory(_account):
        adapter = EventKitCalendarAdapter(transport=lambda request: None,
                                          config=AdapterConfig(provider_id="eventkit", base_url="eventkit://local"), store=store)
        adapter._foundation = object()  # the selector bridge runs through the doubles; no pyobjc import
        return adapter

    return factory


def test_eventkit_occurrences_of_one_series_keep_their_own_alerts_and_policy(home, monkeypatch):
    """NOVEL: EventKit serves every occurrence of a repeating event under the series' identifier. Each occurrence is
    its own event on the alert schedule, a cancelled occurrence gets none, and a per-occurrence policy stays with
    its occurrence when that occurrence is moved on the Mac."""
    clock = Clock(T0, monkeypatch)
    kitchen = _NativeCalendar("EK-CAL-HOME", "Home", True)
    today = _NativeOccurrence(kitchen, start=T0 + timedelta(hours=1))
    tomorrow = _NativeOccurrence(kitchen, start=T0 + timedelta(days=1, hours=1))
    cancelled = _NativeOccurrence(kitchen, start=T0 + timedelta(hours=5), status=3)
    factory = _eventkit_factory(_NativeStore([kitchen], [today, tomorrow, cancelled]))
    account_id = connect("eventkit", clock, select=["EK-CAL-HOME"], adapter_factory=factory)
    from core.operator import calendar_accounts, calendar_alerts

    [calendar] = calendar_accounts.list_selections(account_id)
    assert calendar["can_write"] is True, calendar
    report = calendar_alerts.sync_account(account_id, now_fn=clock, adapter_factory=factory)
    assert report["ok"] and report["events_seen"] == 3 and report["alerts_scheduled"] == 2, report
    alerts = calendar_alerts.upcoming_alerts(now_fn=clock)
    assert [alert["start_utc"] for alert in alerts] == [today.start.isoformat(), tomorrow.start.isoformat()], alerts
    assert len({alert["event_key"] for alert in alerts}) == 2 and alerts[0]["location"] == "Kitchen table", alerts
    changed = calendar_alerts.set_lead_minutes(event_key=alerts[1]["event_key"], minutes=[45], apply_now=True)
    assert changed["ok"] and changed["applied"] == {"alerts_scheduled": 1, "alerts_superseded": 1}, changed

    tomorrow.start, tomorrow.detached = tomorrow.start + timedelta(minutes=30), True
    report = calendar_alerts.sync_account(account_id, now_fn=clock, adapter_factory=factory)
    assert report["alerts_scheduled"] == 1 and report["alerts_superseded"] == 1 and report["alerts_cancelled"] == 0, report
    assert [(alert["lead_minutes"], alert["due_at_utc"]) for alert in calendar_alerts.upcoming_alerts(now_fn=clock)] == [
        (15, (today.start - timedelta(minutes=15)).isoformat()),
        (45, (tomorrow.start - timedelta(minutes=45)).isoformat()),
    ]
