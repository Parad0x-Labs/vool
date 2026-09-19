"""pa_beta_gate -- correction 3 (F2, F3): a recurring CalDAV series is ONE resource, and an occurrence write is verified.

F2 -- RFC 4791 sections 4.1 and 5.3.2.1: every component sharing a UID lives in one calendar object resource. An approved
occurrence move or cancellation reads the series resource at the href the server reported, adds or edits only that
RECURRENCE-ID exception inside the same VCALENDAR (master, other exceptions, EXDATE, attendees, alarms, unknown
properties, timezone definitions, folding and zone identity kept as stored) and PUTs it back to that href under the
reviewed ETag. A concurrent change surfaces as a refusal, never a forced overwrite.

F3 -- a provider acknowledging the PUT is not proof. A cancellation is verified only by positive stored evidence for
exactly that RECURRENCE-ID that the served view does not contradict; a move is verified by reading the occurrence back by
its RECURRENCE-ID wherever it now is. Accepted-but-unproven outcomes rest verify-only through re-approval and a restart,
and nothing is sent twice.

LABELLED: loopback strict CalDAV fixture (``_caldav_service``) reached through the real VOOL transport; injected clock;
synthetic provider faults injected at that fixture (a write acknowledged without effect, a lost reply, a failed read-back,
a concurrent writer, a misapplied exception); a spawned fresh interpreter for the restart probe. No model is involved:
the ordinary request text is dispatched at the operator boundary.
"""
from __future__ import annotations

import http.client
import json
import multiprocessing
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import pytest

from . import _caldav_service
from ._caldav_service import UidConflict, start_caldav_fixture
from ._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc)  # Monday 10:00 Vilnius
CAL = "/calendars/team/"
CRLF = "\r\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    prepared = prepare_home(tmp_path, monkeypatch)
    from core import local_operator_actions

    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: T0.astimezone(ZoneInfo("Europe/Athens")))
    return prepared


@pytest.fixture
def caldav(home, monkeypatch):
    Clock(T0, monkeypatch)
    server, state, base, port = start_caldav_fixture(calendars={CAL: "Team"})
    from core.operator import calendar_accounts

    account = calendar_accounts.add_account(provider="caldav", base_url=base, label="Team")
    assert calendar_accounts.discover_calendars(account["account_id"])["ok"]
    assert calendar_accounts.select_calendar(account["account_id"], CAL, selected=True, default_write=True)["ok"]
    calendar_accounts.set_opt_in(account["account_id"], sync_enabled=True, alerts_enabled=False)
    try:
        yield state, port
    finally:
        server.shutdown()
        server.server_close()


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed the request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _approve(proposal, session_id: str):
    assert proposal.status == "approval_required", proposal.response_text
    return _run(f"approve calendar {proposal.details['action_id']}", session_id=session_id)


def _approval_status(session_id: str, action_id: str, kind: str) -> str:
    from core.operator.calendar_provider import load_action_any_state

    return str((load_action_any_state(session_id=session_id, action_kind=kind, action_id=action_id) or {}).get("status"))


def _calendar(*components: str) -> str:
    return CRLF.join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Fixture//CalDAV//EN", *components, "END:VCALENDAR"]) + CRLF


def _vevent(*lines: str) -> str:
    return CRLF.join(["BEGIN:VEVENT", *lines, "END:VEVENT"])


def _weekly(uid: str, summary: str, first: str, last: str, byday: str, *extra: str) -> str:
    return _vevent(f"UID:{uid}", "DTSTAMP:20260901T000000Z", f"DTSTART:{first}", f"DTEND:{last}", f"SUMMARY:{summary}",
                   f"RRULE:FREQ=WEEKLY;BYDAY={byday}", *extra)


RETRO = _weekly("retro@f", "Retro", "20260922T090000Z", "20260922T100000Z", "TU")  # the review's series


def _vevents(ics: str) -> list[str]:
    blocks, rest = [], ics
    while "BEGIN:VEVENT" in rest:
        start = rest.index("BEGIN:VEVENT")
        end = rest.index("END:VEVENT", start) + len("END:VEVENT")
        blocks.append(rest[start:end])
        rest = rest[end:]
    return blocks


def _live(text: str, title: str, *, session_id: str) -> list[str]:
    """Starts of that title's rows in the ordinary agenda answer. The fixture's default policy serves no cancelled exception
    at all; the test that makes the server serve cancelled instances checks the answer's own [cancelled] marker instead."""
    answer = _run(text, session_id=session_id)
    assert answer.ok, answer.response_text
    return [row["start_utc"] for row in answer.details["events"] if row["summary"] == title]


def _writes(monkeypatch) -> list[str]:
    """Every PUT the service receives, by decoded path, counted at the service."""
    seen: list[str] = []
    real = _caldav_service._Handler.do_PUT

    def counted(handler):
        seen.append(unquote(urlparse(handler.path).path))
        return real(handler)

    monkeypatch.setattr(_caldav_service._Handler, "do_PUT", counted)
    return seen


def _drop_next_success_reply(monkeypatch, method: str) -> None:
    """SYNTHETIC FAULT: the service applies the next ``method``, then closes the connection without any reply."""
    real = _caldav_service._Handler._reply
    armed = {"on": True}

    def reply(handler, status, body=b"", content_type="text/plain", headers=None):
        if armed["on"] and handler.command == method and 200 <= status < 300:
            armed["on"] = False
            handler.close_connection = True
            return None
        return real(handler, status, body, content_type, headers)

    monkeypatch.setattr(_caldav_service._Handler, "_reply", reply)


# ---------------------------------------------------------------------------------------------------------------------
# The fixture's own contract: what a compliant server does, checked before anything relies on it
# ---------------------------------------------------------------------------------------------------------------------


def test_strict_fixture_holds_one_resource_per_uid_and_judges_exceptions_at_their_own_times():
    """FIXTURE CONTRACT (RFC 4791 sections 4.1, 5.3.2.1, 9.6.5, 9.9): the service refuses a second resource for a UID -- as a
    seed and as a PUT, naming the resource already using it -- refuses a METHOD-bearing object, serves one DAV:response per
    resource with one VEVENT per expanded instance, and judges an exception at its own times, not the slot it left."""
    server, state, _base, port = start_caldav_fixture(calendars={CAL: "Team"})
    try:
        moved = _vevent("UID:retro@f", "DTSTAMP:20260901T000000Z", "RECURRENCE-ID:20260929T090000Z",
                        "DTSTART:20261001T120000Z", "DTEND:20261001T130000Z", "SUMMARY:Retro")
        state.put_event(CAL, "retro@f", _calendar(RETRO, moved))
        with pytest.raises(UidConflict) as seeded:
            state.put_event(CAL, "retro-override", _calendar(moved))
        assert seeded.value.href == f"{CAL}retro@f.ics"

        def send(method, path, body, headers):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request(method, path, body=body.encode("utf-8"), headers=headers)
                reply = connection.getresponse()
                return reply.status, reply.read().decode("utf-8")
            finally:
                connection.close()

        status, body = send("PUT", f"{CAL}retro-override.ics", _calendar(moved), {"Content-Type": "text/calendar; charset=utf-8"})
        assert status == 403 and "no-uid-conflict" in body and f"{CAL}retro@f.ics" in body, (status, body)
        with_method = CRLF.join(["BEGIN:VCALENDAR", "VERSION:2.0", "METHOD:REQUEST",
                                 _vevent("UID:other@f", "DTSTART:20261001T120000Z", "SUMMARY:Other"), "END:VCALENDAR"]) + CRLF
        status, body = send("PUT", f"{CAL}other@f.ics", with_method, {"Content-Type": "text/calendar; charset=utf-8"})
        assert status == 403 and "valid-calendar-object-resource" in body, (status, body)
        assert list(state.snapshot()[CAL]) == ["retro@f"]

        def expanded(start, end):
            query = ('<?xml version="1.0" encoding="utf-8"?><C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                     f'<D:prop><D:getetag/><C:calendar-data><C:expand start="{start}" end="{end}"/></C:calendar-data></D:prop>'
                     '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VEVENT">'
                     f'<C:time-range start="{start}" end="{end}"/></C:comp-filter></C:comp-filter></C:filter></C:calendar-query>')
            status, body = send("REPORT", CAL, query, {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
            assert status == 207, body
            return [(node.findtext("{DAV:}href"), node.findtext(".//{urn:ietf:params:xml:ns:caldav}calendar-data"))
                    for node in ET.fromstring(body).findall("{DAV:}response")]

        week = expanded("20260928T000000Z", "20261005T000000Z")
        assert [href for href, _data in week] == [f"{CAL}retro@f.ics"], week
        [instance] = _vevents(week[0][1])
        assert "RECURRENCE-ID:20260929T090000Z" in instance and "DTSTART:20261001T120000Z" in instance and "RRULE" not in week[0][1]
        assert expanded("20260929T000000Z", "20260930T000000Z") == [], "an exception moved away is not served at the slot it left"
        fortnight = expanded("20261005T000000Z", "20261019T000000Z")
        assert [len(_vevents(data)) for _href, data in fortnight] == [2], fortnight  # 10-06 and 10-13 in ONE response
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------------------------------------------------------------
# F2: the occurrence is written inside the series' own resource
# ---------------------------------------------------------------------------------------------------------------------


def test_original_move_rewrites_only_that_exception_inside_the_series_resource(caldav, monkeypatch):
    """ORIGINAL (the review's F2 data): 'Retro' on 2026-09-29 moved to 13:00 Vilnius. One resource holds UID retro@f and
    received the only write; its master is byte-for-byte as stored; the exception carries the original RECURRENCE-ID and
    the new time; the neighbouring occurrence keeps its time."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    seeded = _calendar(RETRO)
    state.put_event(CAL, "retro@f", seeded)
    proposal = _run('move the "Retro" event to 2026-09-29 13:00 Europe/Athens (the 2026-09-29 occurrence)', session_id="c3-move")
    moved = _approve(proposal, "c3-move")
    assert moved.ok and moved.details["occurrence_original_start"].startswith("2026-09-29T09:00"), moved.response_text

    rows = state.snapshot()[CAL]
    assert list(rows) == ["retro@f"] and puts == [f"{CAL}retro@f.ics"], (list(rows), puts)
    master, exception = _vevents(rows["retro@f"]["ics"])
    assert master == _vevents(seeded)[0], master
    assert "RECURRENCE-ID:20260929T090000Z" in exception and "DTSTART:20260929T100000Z" in exception, exception
    assert "RRULE" not in exception, exception
    assert _live("agenda from 2026-09-28 to 2026-10-04", "Retro", session_id="c3-move-a") == ["2026-09-29T10:00:00+00:00"]
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Retro", session_id="c3-move-b") == ["2026-10-06T09:00:00+00:00"]


VTIMEZONE = CRLF.join([
    "BEGIN:VTIMEZONE", "TZID:Europe/Athens",
    "BEGIN:STANDARD", "DTSTART:19701025T040000", "TZOFFSETFROM:+0300", "TZOFFSETTO:+0200", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "BEGIN:DAYLIGHT", "DTSTART:19700329T030000", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0300", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
    "END:VTIMEZONE",
])
OPS_MASTER = CRLF.join([
    "BEGIN:VEVENT", "UID:ops@f", "DTSTAMP:20260901T000000Z",
    "DTSTART;TZID=Europe/Athens:20260923T150000", "DTEND;TZID=Europe/Athens:20260923T160000",
    "SUMMARY:Ops review",
    "DESCRIPTION:Walk the incident board and confirm an owner for every ope",
    " n item before closing the stale ones",
    "ORGANIZER;CN=Lead:mailto:lead@example.org",
    "ATTENDEE;CN=Ana;RSVP=TRUE:mailto:ana@example.org",
    "X-OPS-TAG:keep-me",
    "RRULE:FREQ=WEEKLY;BYDAY=WE",
    "EXDATE;TZID=Europe/Athens:20261014T150000",
    "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Ops review soon", "TRIGGER:-PT15M", "END:VALARM",
    "END:VEVENT",
])
OPS_LATE = _vevent("UID:ops@f", "DTSTAMP:20260901T000000Z", "RECURRENCE-ID;TZID=Europe/Athens:20260930T150000",
                   "DTSTART;TZID=Europe/Athens:20260930T170000", "DTEND;TZID=Europe/Athens:20260930T180000",
                   "SUMMARY:Ops review (late)", "X-EXCEPTION-NOTE:keep")


def test_novel_zoned_move_days_away_keeps_everything_else_in_the_resource(caldav, monkeypatch):
    """NOVEL: a Europe/Athens series with a VTIMEZONE, EXDATE, organizer, attendee, alarm, an unknown property, a folded
    description and an existing exception. The 2026-10-07 occurrence moves two days, to 2026-10-09 10:30. Only a new
    exception is added -- RECURRENCE-ID and times in the series' own zone, attendees and alarm carried over -- and every
    other line of the resource is sent back as stored. Its original day is empty, its new day holds it, the other
    exception and the EXDATE hole stay, and the zone identity holds across the October DST change."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    seeded = _calendar(VTIMEZONE, OPS_MASTER, OPS_LATE)
    state.put_event(CAL, "ops@f", seeded)
    proposal = _run('move the "Ops review" event to 2026-10-09 10:30 Europe/Athens (the 2026-10-07 occurrence)', session_id="c3-ops")
    assert "2026-10-07" in proposal.response_text or proposal.status == "approval_required", proposal.response_text
    moved = _approve(proposal, "c3-ops")
    assert moved.ok and moved.details["occurrence_original_start"].startswith("2026-10-07T12:00"), moved.response_text
    assert puts == [f"{CAL}ops@f.ics"], puts

    stored = state.snapshot()[CAL]["ops@f"]["ics"]
    assert stored.startswith(seeded[: seeded.index("END:VEVENT", seeded.index("UID:ops@f\r\nDTSTAMP:20260901T000000Z\r\nRECURRENCE-ID"))])
    assert VTIMEZONE in stored and OPS_MASTER in stored and OPS_LATE in stored, stored
    new = [block for block in _vevents(stored) if "RECURRENCE-ID;TZID=Europe/Athens:20261007T150000" in block]
    assert len(new) == 1, stored
    for line in ("DTSTART;TZID=Europe/Athens:20261009T103000", "DTEND;TZID=Europe/Athens:20261009T113000",
                 "ATTENDEE;CN=Ana;RSVP=TRUE:mailto:ana@example.org", "ORGANIZER;CN=Lead:mailto:lead@example.org", "X-OPS-TAG:keep-me",
                 "DESCRIPTION:Walk the incident board and confirm an owner for every ope\r\n n item before closing the stale ones",
                 "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Ops review soon\r\nTRIGGER:-PT15M\r\nEND:VALARM"):
        assert line in new[0], (line, new[0])
    assert "RRULE" not in new[0] and "EXDATE" not in new[0], new[0]

    assert _live("agenda from 2026-10-05 to 2026-10-08", "Ops review", session_id="c3-ops-a") == []
    assert _live("agenda from 2026-10-09 to 2026-10-11", "Ops review", session_id="c3-ops-b") == ["2026-10-09T07:30:00+00:00"]
    assert _live("agenda from 2026-09-21 to 2026-10-04", "Ops review", session_id="c3-ops-c") == ["2026-09-23T12:00:00+00:00"]
    assert _live("agenda from 2026-09-28 to 2026-10-04", "Ops review (late)", session_id="c3-ops-d") == ["2026-09-30T14:00:00+00:00"]
    assert _live("agenda from 2026-10-12 to 2026-10-25", "Ops review", session_id="c3-ops-e") == ["2026-10-21T12:00:00+00:00"]
    assert _live("agenda from 2026-10-26 to 2026-11-01", "Ops review", session_id="c3-ops-f") == ["2026-10-28T13:00:00+00:00"]


def test_concurrent_series_change_refuses_the_write_and_nothing_is_forced(caldav, monkeypatch):
    """REFUSAL: another writer changes the series between the adapter's read and its write. The conditional PUT is refused
    (412), the approval rests, nothing is retried or forced, and the other writer's version stays exactly as it wrote it."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    proposal = _run('cancel the "Retro" event on 2026-10-06', session_id="c3-race")
    theirs = _calendar(RETRO, _vevent("UID:retro@f", "DTSTAMP:20260915T000000Z", "RECURRENCE-ID:20261013T090000Z",
                                      "DTSTART:20261013T110000Z", "DTEND:20261013T120000Z", "SUMMARY:Retro"))
    real_get = _caldav_service._Handler.do_GET
    fired: list[bool] = []

    def get_then_concurrent_write(handler):
        result = real_get(handler)
        if not fired and unquote(urlparse(handler.path).path) == f"{CAL}retro@f.ics":
            fired.append(True)
            state.put_event(CAL, "retro@f", theirs)
        return result

    monkeypatch.setattr(_caldav_service._Handler, "do_GET", get_then_concurrent_write)
    refused = _approve(proposal, "c3-race")
    assert fired and not refused.ok and refused.status == "stale_etag", refused.response_text
    assert puts == [f"{CAL}retro@f.ics"], "exactly the one conditional write, never a forced retry"
    assert state.snapshot()[CAL]["retro@f"]["ics"] == theirs
    assert _approval_status("c3-race", proposal.details["action_id"], "provider_calendar_cancel") == "pending_approval"


# ---------------------------------------------------------------------------------------------------------------------
# F3: acceptance is not verification
# ---------------------------------------------------------------------------------------------------------------------


def test_original_accepted_cancel_without_effect_stays_unproven_then_verifies_without_resending(caldav, monkeypatch):
    """ORIGINAL (the review's F3 data): the provider acknowledges the cancellation PUT for 'Retro' on 2026-10-06 but keeps
    serving it. The answer is unproven, never verified; approving again while it is still live re-sends nothing and stays
    unproven; once the provider has applied it, approving again verifies from the stored series without another write."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    real_put = state.put_event

    def accept_without_change(calendar, name, ics):
        if "STATUS:CANCELLED" in ics:
            return '"accepted-no-effect"'
        return real_put(calendar, name, ics)

    monkeypatch.setattr(state, "put_event", accept_without_change)
    proposal = _run('cancel the "Retro" event on 2026-10-06', session_id="c3-f3")
    action_id = proposal.details["action_id"]
    first = _approve(proposal, "c3-f3")
    assert not first.ok and first.status == "outcome_unproven" and first.details.get("accepted") is True, first.response_text
    assert not first.details.get("verified_gone") and "still serves the 2026-10-06 occurrence" in first.response_text, first.response_text
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Retro", session_id="c3-f3-a") == ["2026-10-06T09:00:00+00:00"]

    again = _run(f"approve calendar {action_id}", session_id="c3-f3")
    assert not again.ok and again.status == "outcome_unproven" and "not verifiably cancelled" in again.response_text, again.response_text
    assert len(puts) == 1 and _approval_status("c3-f3", action_id, "provider_calendar_cancel") == "outcome_unproven"

    monkeypatch.setattr(state, "put_event", real_put)
    applied_late = _vevent("UID:retro@f", "DTSTAMP:20260921T070000Z", "RECURRENCE-ID:20261006T090000Z",
                           "DTSTART:20261006T090000Z", "DTEND:20261006T100000Z", "SUMMARY:Retro", "STATUS:CANCELLED")
    state.put_event(CAL, "retro@f", _calendar(RETRO, applied_late))
    verified = _run(f"approve calendar {action_id}", session_id="c3-f3")
    assert verified.ok and verified.status == "executed" and verified.details["recovered"] is True, verified.response_text
    assert verified.details["attribution"] == "verified" and verified.details["occurrence_original_start"].startswith("2026-10-06T09:00")
    assert len(puts) == 1, "verification never re-sends"
    assert _approval_status("c3-f3", action_id, "provider_calendar_cancel") == "executed"
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Retro", session_id="c3-f3-b") == []
    assert _live("agenda from 2026-10-12 to 2026-10-18", "Retro", session_id="c3-f3-c") == ["2026-10-13T09:00:00+00:00"]


PAIRING = _weekly("pairing@f", "Pairing", "20260924T060000Z", "20260924T070000Z", "TH")
PAIRING_MOVED = _vevent("UID:pairing@f", "DTSTAMP:20260901T000000Z", "RECURRENCE-ID:20261001T060000Z",
                        "DTSTART:20261001T070000Z", "DTEND:20261001T080000Z", "SUMMARY:Pairing")


def test_novel_cancel_whose_reply_is_lost_is_recovered_by_verification_only(caldav, monkeypatch):
    """NOVEL: 'Pairing' on 2026-10-08, beside an existing moved exception. The service applies the cancellation and the
    reply is lost: the outcome is unproven. Approving again verifies exactly that occurrence from the stored series and
    records it -- saying the lost reply leaves attribution unproven -- with no second write; the moved exception is intact."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "pairing@f", _calendar(PAIRING, PAIRING_MOVED))
    proposal = _run('cancel the "Pairing" event on 2026-10-08', session_id="c3-lost")
    action_id = proposal.details["action_id"]
    _drop_next_success_reply(monkeypatch, "PUT")
    lost = _approve(proposal, "c3-lost")
    assert not lost.ok and lost.status == "outcome_unproven", lost.response_text
    stored = state.snapshot()[CAL]["pairing@f"]["ics"]
    assert "RECURRENCE-ID:20261008T060000Z" in stored and PAIRING_MOVED in stored and PAIRING in stored, stored
    assert len(puts) == 1 and _approval_status("c3-lost", action_id, "provider_calendar_cancel") == "outcome_unproven"

    recovered = _run(f"approve calendar {action_id}", session_id="c3-lost")
    assert recovered.ok and recovered.status == "executed" and recovered.details["recovered"] is True, recovered.response_text
    assert recovered.details["attribution"] == "unproven" and "reply was lost" in recovered.response_text, recovered.response_text
    assert len(puts) == 1
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Pairing", session_id="c3-lost-a") == []
    assert _live("agenda from 2026-09-28 to 2026-10-04", "Pairing", session_id="c3-lost-b") == ["2026-10-01T07:00:00+00:00"]
    assert _live("agenda from 2026-10-12 to 2026-10-18", "Pairing", session_id="c3-lost-c") == ["2026-10-15T06:00:00+00:00"]


def test_novel_cancel_whose_read_back_fails_stays_accepted_then_verifies(caldav, monkeypatch):
    """NOVEL: 'Budget sync' on 2026-10-09. The PUT is acknowledged but the read-back of the series fails (503): accepted and
    unproven, never verified or reported absent. Approving again verifies the stored cancellation, with no second write."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "budget@f", _calendar(_weekly("budget@f", "Budget sync", "20260925T130000Z", "20260925T133000Z", "FR")))
    proposal = _run('cancel the "Budget sync" event on 2026-10-09', session_id="c3-readback")
    action_id = proposal.details["action_id"]
    counted_put, real_get = _caldav_service._Handler.do_PUT, _caldav_service._Handler.do_GET
    armed = {"after_put": False, "failed": 0}

    def put_then_arm(handler):
        result = counted_put(handler)
        armed["after_put"] = True
        return result

    def failing_get(handler):
        if armed["after_put"] and not armed["failed"]:
            armed["failed"] += 1
            return handler._reply(503, b"temporarily unavailable")
        return real_get(handler)

    monkeypatch.setattr(_caldav_service._Handler, "do_PUT", put_then_arm)
    monkeypatch.setattr(_caldav_service._Handler, "do_GET", failing_get)
    accepted = _approve(proposal, "c3-readback")
    assert armed["failed"] == 1 and not accepted.ok and accepted.status == "outcome_unproven", accepted.response_text
    assert accepted.details.get("accepted") is True and "could not confirm the 2026-10-09 occurrence is cancelled" in accepted.response_text

    verified = _run(f"approve calendar {action_id}", session_id="c3-readback")
    assert verified.ok and verified.details["recovered"] is True and verified.details["attribution"] == "verified", verified.response_text
    assert len(puts) == 1
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Budget sync", session_id="c3-readback-a") == []


def test_another_occurrences_cancellation_is_not_proof(caldav, monkeypatch):
    """IDENTITY: the provider acknowledges the PUT but stores the cancellation on the NEXT week's occurrence. The receipt
    must not claim 2026-10-06: the outcome is unproven, 2026-10-06 is still live, and nothing is sent again."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    real_put = state.put_event

    def misapplied(calendar, name, ics):
        if "STATUS:CANCELLED" in ics:
            ics = (ics.replace("RECURRENCE-ID:20261006T090000Z", "RECURRENCE-ID:20261013T090000Z")
                      .replace("DTSTART:20261006T090000Z", "DTSTART:20261013T090000Z")
                      .replace("DTEND:20261006T100000Z", "DTEND:20261013T100000Z"))
        return real_put(calendar, name, ics)

    monkeypatch.setattr(state, "put_event", misapplied)
    proposal = _run('cancel the "Retro" event on 2026-10-06', session_id="c3-identity")
    result = _approve(proposal, "c3-identity")
    assert not result.ok and result.status == "outcome_unproven" and not result.details.get("verified_gone"), result.response_text
    assert len(puts) == 1
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Retro", session_id="c3-identity-a") == ["2026-10-06T09:00:00+00:00"]


DESIGN = _weekly("design@f", "Design crit", "20260928T110000Z", "20260928T120000Z", "MO")
DESIGN_MOVED = _vevent("UID:design@f", "DTSTAMP:20260901T000000Z", "RECURRENCE-ID:20261005T110000Z",
                       "DTSTART:20261005T120000Z", "DTEND:20261005T130000Z", "SUMMARY:Design crit", "X-MOVED-BY:ops")


def test_real_single_cancellation_verified_while_the_server_serves_cancelled_instances(caldav, monkeypatch):
    """CONTROL: a server that serves a cancelled exception in expanded replies (with its STATUS). A real cancellation of
    'Design crit' on 2026-10-12 verifies: that instance reads cancelled, not still present; the master and the other
    exception are byte-for-byte as stored. Cancelling the same occurrence again claims nothing and sends nothing."""
    state, _port = caldav
    state.expand_cancelled_instances = True
    puts = _writes(monkeypatch)
    state.put_event(CAL, "design@f", _calendar(DESIGN, DESIGN_MOVED))
    cancelled = _approve(_run('cancel the "Design crit" event on 2026-10-12', session_id="c3-real"), "c3-real")
    assert cancelled.ok and cancelled.status == "executed" and cancelled.details["attribution"] == "verified", cancelled.response_text
    assert "Occurrence cancelled on the provider and verified" in cancelled.response_text, cancelled.response_text
    master, moved, new = _vevents(state.snapshot()[CAL]["design@f"]["ics"])
    assert master == DESIGN and moved == DESIGN_MOVED, (master, moved)
    assert "RECURRENCE-ID:20261012T110000Z" in new and "STATUS:CANCELLED" in new, new
    week = _run("agenda from 2026-10-12 to 2026-10-18", session_id="c3-real-a")
    assert [row["start_utc"] for row in week.details["events"] if row["summary"] == "Design crit"] == ["2026-10-12T11:00:00+00:00"], week.details
    design_lines = [line for line in week.response_text.splitlines() if "Design crit" in line]
    assert len(design_lines) == 1 and "Design crit [cancelled]" in design_lines[0], week.response_text  # served, shown cancelled
    assert _live("agenda from 2026-10-05 to 2026-10-11", "Design crit", session_id="c3-real-b") == ["2026-10-05T12:00:00+00:00"]
    assert _live("agenda from 2026-10-19 to 2026-10-25", "Design crit", session_id="c3-real-c") == ["2026-10-19T11:00:00+00:00"]

    repeated = _run('cancel the "Design crit" event on 2026-10-12', session_id="c3-real-d")
    if repeated.status == "approval_required":
        repeated = _approve(repeated, "c3-real-d")
    assert not repeated.ok and "already cancelled" in repeated.response_text, repeated.response_text
    assert len(puts) == 1


def test_series_gone_during_recovery_is_not_a_cancellation_receipt(caldav, monkeypatch):
    """REFUSAL: the cancellation's reply is lost, then the whole series is deleted elsewhere. Recovery does not turn the
    series' absence into proof that ONE occurrence was cancelled: nothing is recorded as executed or sent again."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    proposal = _run('cancel the "Retro" event on 2026-10-06', session_id="c3-gone")
    action_id = proposal.details["action_id"]
    _drop_next_success_reply(monkeypatch, "PUT")
    assert _approve(proposal, "c3-gone").status == "outcome_unproven"
    state.calendars[CAL]["events"].pop("retro@f")
    after = _run(f"approve calendar {action_id}", session_id="c3-gone")
    assert not after.ok and after.status == "not_found" and "no cancellation is claimed" in after.response_text, after.response_text
    assert len(puts) == 1 and _approval_status("c3-gone", action_id, "provider_calendar_cancel") == "outcome_unproven"


def _approve_in_fresh_interpreter(session_id: str, action_id: str, answer_file: str, db_path: str) -> None:
    """RESTART: one approval in a spawned interpreter bound to the same durable store -- the database file this pytest
    process configured (tests/conftest.py binds each process to its own) and VOOL_HOME / VOOL_WORKSPACE_ROOT from the
    environment. Nothing else carries over."""
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import configure_default_db_path

    configure_default_db_path(db_path)
    configure_runtime_continuity_db_path(db_path)
    from tests.pa_beta_gate.test_c3_caldav_occurrence_contract import _run as child_run

    result = child_run(f"approve calendar {action_id}", session_id=session_id)
    Path(answer_file).write_text(json.dumps({"pid": os.getpid(), "ok": result.ok, "status": result.status,
                                             "text": result.response_text, "details": result.details}, default=str), encoding="utf-8")


def test_days_away_move_with_a_lost_reply_is_verified_after_a_restart_without_a_second_write(caldav, monkeypatch, tmp_path):
    """RESTART and RECOVERY: 'Retro' on 2026-09-29 moved three days, to 2026-10-02 12:00 Vilnius. The service applies the
    move and the reply is lost. A FRESH interpreter approves again: it finds the occurrence by its RECURRENCE-ID at the
    approved time and records it, with no second write. The original day is empty and the new day holds it."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    proposal = _run('move the "Retro" event to 2026-10-02 12:00 Europe/Athens (the 2026-09-29 occurrence)', session_id="c3-restart")
    action_id = proposal.details["action_id"]
    _drop_next_success_reply(monkeypatch, "PUT")
    lost = _approve(proposal, "c3-restart")
    assert not lost.ok and lost.status == "outcome_unproven", lost.response_text
    assert "DTSTART:20261002T090000Z" in state.snapshot()[CAL]["retro@f"]["ics"] and len(puts) == 1

    from storage.db import active_default_db_path

    answer_file = tmp_path / "fresh-approval.json"
    child = multiprocessing.get_context("spawn").Process(target=_approve_in_fresh_interpreter,
                                                         args=("c3-restart", action_id, str(answer_file), active_default_db_path()))
    child.start()
    child.join(120)
    assert child.exitcode == 0, child.exitcode
    answer = json.loads(answer_file.read_text(encoding="utf-8"))
    assert answer["pid"] != os.getpid()
    assert answer["ok"] and answer["status"] == "executed" and answer["details"]["recovered"] is True, answer
    assert answer["details"]["occurrence_original_start"].startswith("2026-09-29T09:00"), answer
    assert len(puts) == 1, "the restarted approval verified; it did not write again"
    assert _live("agenda from 2026-09-28 to 2026-10-01", "Retro", session_id="c3-restart-a") == []
    assert _live("agenda from 2026-10-02 to 2026-10-04", "Retro", session_id="c3-restart-b") == ["2026-10-02T09:00:00+00:00"]


def test_a_dated_occurrence_request_after_an_approved_move_targets_that_occurrence_not_the_series(caldav, monkeypatch):
    """ORIGINAL (found by the served flow): after an approved occurrence move this chat holds a receipt for the series' uid,
    and 'cancel the "Retro" event on 2026-10-13' staged the WHOLE series through it. It must stage THAT occurrence.
    NOVEL: a later move of another named occurrence in the same chat targets its own occurrence too.
    CONTROL: the title with no date names no occurrence, and nothing writes the series without its scope being named."""
    state, _port = caldav
    puts = _writes(monkeypatch)
    state.put_event(CAL, "retro@f", _calendar(RETRO))
    session = "c3-receipt"
    first = _approve(_run('move the "Retro" event to 2026-09-29 13:00 Europe/Athens (the 2026-09-29 occurrence)', session_id=session), session)
    assert first.ok, first.response_text

    staged = _run('cancel the "Retro" event on 2026-10-13', session_id=session)
    assert staged.status == "approval_required" and "the 2026-10-13 occurrence of 'Retro'" in staged.response_text, staged.response_text
    cancelled = _approve(staged, session)
    assert cancelled.ok and cancelled.details["occurrence_original_start"].startswith("2026-10-13T09:00"), cancelled.response_text

    moved = _approve(_run('move the "Retro" event to 2026-10-21 10:00 Europe/Athens (the 2026-10-20 occurrence)', session_id=session), session)
    assert moved.ok and moved.details["occurrence_original_start"].startswith("2026-10-20T09:00"), moved.response_text

    unnamed = _run('cancel the "Retro" event', session_id=session)
    if unnamed.status == "approval_required":
        unnamed = _approve(unnamed, session)
    assert not unnamed.ok and ("repeating series" in unnamed.response_text or "Tell me which one" in unnamed.response_text), unnamed.response_text
    assert len(puts) == 3, puts
    master, *exceptions = _vevents(state.snapshot()[CAL]["retro@f"]["ics"])
    assert master == RETRO and len(exceptions) == 3, exceptions
    assert _live("agenda from 2026-10-12 to 2026-10-18", "Retro", session_id="c3-receipt-a") == []
    assert _live("agenda from 2026-10-19 to 2026-10-25", "Retro", session_id="c3-receipt-b") == ["2026-10-21T07:00:00+00:00"]
