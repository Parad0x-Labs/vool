"""pa_beta_gate -- revision-6: unreadable calendar data never becomes free time, a send, or a recovery verdict.

Novel cases for the typed calendar read contract through the production operator paths:

* availability, proposals and the send-time re-check through ``dispatch_operator_action`` against the
  disposable Microsoft Graph loopback service (real HTTP and the real VOOL transport), with one stored
  event served unreadable;
* create, update and cancel recovery through the real approval executor and SQLite approval store over
  synthetic wires behind the real Google and Graph adapters.

Before revision 6 each of these read an unusable reply as an empty calendar or as ``not_found``: slots
were offered over a busy event, recovery re-sent a create, reported an accepted event as removed, called
an updated event vanished, or recorded a cancellation of an event that was still stored. Every test
counts the writes the provider received and reads durable approval state. LABELLED: synthetic providers
and a local protocol fixture; no account, credential or live service.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import pytest

from core.kas.adapters.google_calendar import google_event_id
from core.kas.contract import CalendarWriteAcceptedError, KasResponse, TransportUnknownError
from tests.pa_beta_gate import test_v5_calendar_update_cancel_lifecycle as lifecycle
from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter as wire_adapter
from tests.pa_beta_gate.test_calendar_v2_independent_review import response
from tests.pa_beta_gate.test_popular_provider_adapters import (
    H_CAL,
    _run,
    graph_env,
)
from tests.pa_beta_gate.test_v5_calendar_effect_phase import _stage, _stored
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import _execute

pytestmark = [pytest.mark.pa_beta]


# ---------------------------------------------------------------------------------------------
# Loopback Graph service: availability, proposals, the send-time re-check
# ---------------------------------------------------------------------------------------------


def _fix_now(monkeypatch):
    from zoneinfo import ZoneInfo

    from core import local_operator_actions

    fixed = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)  # Saturday; Thursday = 2026-09-17
    monkeypatch.setattr(local_operator_actions, "_scheduling_now", lambda: fixed.astimezone(ZoneInfo("UTC")))


def _serve_unreadable(monkeypatch, state, uids):
    """The service keeps valid stored times (its own filtering works) but serves these events with
    unreadable start/end values. Returns the mutable id set: clearing it makes the data readable."""
    broken = set(uids)
    real_render = state.render

    def render(uid, row):
        event = real_render(uid, row)
        if uid in broken:
            event["start"], event["end"] = {"dateTime": "not-a-date"}, {"dateTime": "also-invalid"}
        return event

    monkeypatch.setattr(state, "render", render)
    return broken


def _count_posts(monkeypatch, server):
    posts: list[str] = []
    handler = server.RequestHandlerClass
    real_post = handler.do_POST

    def do_POST(self):
        posts.append(self.path)
        return real_post(self)

    monkeypatch.setattr(handler, "do_POST", do_POST)
    return posts


def _titles(state) -> set[str]:
    return {row["summary"] for row in state.snapshot()[H_CAL].values()}


def test_unreadable_event_makes_availability_and_proposals_unreadable_until_the_data_is_readable(request, monkeypatch):
    env = request.getfixturevalue("graph_env")
    state, server = env["state"], env["server"]
    session = "v6-graph-unreadable-availability"
    _fix_now(monkeypatch)
    state.seed_event(H_CAL, "hall@fixture", summary="Tenant town hall", start=datetime(2026, 9, 17, 9, 30, tzinfo=timezone.utc), minutes=60)
    state.seed_event(H_CAL, "opaque@fixture", summary="Opaque hold", start=datetime(2026, 9, 17, 10, 30, tzinfo=timezone.utc), minutes=30)
    broken = _serve_unreadable(monkeypatch, state, {"opaque@fixture"})
    posts = _count_posts(monkeypatch, server)

    _intent, check = _run("check Thursday morning for a free 15-minute slot", session_id=session)
    assert not check.ok and check.status == "provider_refused" and check.details["reason"] == "unreadable_response", check.response_text
    assert "can't tell" in check.response_text and "options" not in check.details, check.response_text
    _intent, chosen = _run('option 2, propose "Client prep"', session_id=session)
    assert chosen.status != "approval_required", chosen.response_text
    _intent, direct = _run('propose "Client prep" on 2026-09-17 13:30 Europe/Athens for 30m', session_id=session)
    assert direct.status == "provider_refused" and direct.details["reason"] == "unreadable_response", direct.response_text
    assert posts == [] and "Client prep" not in _titles(state)

    broken.clear()
    _intent, readable = _run("check Thursday morning for a free 15-minute slot", session_id=session)
    assert readable.ok and readable.details["options"], readable.response_text
    for option in readable.details["options"]:
        start = datetime.fromisoformat(option["start_utc"])
        assert not (start < datetime(2026, 9, 17, 11, tzinfo=timezone.utc) and datetime(2026, 9, 17, 10, 30, tzinfo=timezone.utc) < start + timedelta(minutes=15)), option


def test_approval_whose_send_time_recheck_is_unreadable_sends_nothing_and_stays_approvable(request, monkeypatch):
    env = request.getfixturevalue("graph_env")
    state, server = env["state"], env["server"]
    session = "v6-graph-unreadable-recheck"
    _fix_now(monkeypatch)
    _intent, proposal = _run('propose "Boiler flush" on 2026-09-17 16:00 Europe/Athens for 30m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    action_id = proposal.details["action_id"]
    state.seed_event(H_CAL, "late-hold@fixture", summary="Late hold", start=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc), minutes=30)
    broken = _serve_unreadable(monkeypatch, state, {"late-hold@fixture"})
    posts = _count_posts(monkeypatch, server)

    _intent, unreadable = _run(f"approve calendar {action_id}", session_id=session)
    assert unreadable.status == "provider_refused" and unreadable.details["reason"] == "unreadable_response", unreadable.response_text
    assert posts == [] and unreadable.details["resting_status"] == "pending_approval", unreadable.details

    broken.clear()
    _intent, taken = _run(f"approve calendar {action_id}", session_id=session)
    assert taken.status == "conflict" and "Late hold" in taken.response_text and posts == [], taken.response_text

    state.calendars[H_CAL]["events"].pop("late-hold@fixture")
    _intent, created = _run(f"approve calendar {action_id}", session_id=session)
    assert created.ok and len(posts) == 1 and "Boiler flush" in _titles(state), created.response_text


# ---------------------------------------------------------------------------------------------
# Synthetic wires behind the real adapters: create, update and cancel recovery
# ---------------------------------------------------------------------------------------------


class GoogleWire:
    """LABELLED synthetic Google provider: events keyed by the client-chosen id."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.posts: list[str] = []
        self.lose_next_post = False
        self.blank_event_reads = False

    def __call__(self, req):
        path = urlparse(req.url).path
        if req.method == "POST":
            body = json.loads(req.body)
            self.posts.append(body["id"])
            if self.lose_next_post:
                self.lose_next_post = False
                raise TransportUnknownError("connection_reset_before_acceptance")
            self.store[body["id"]] = {**body, "etag": '"v1"'}
            return response(self.store[body["id"]])
        if path.endswith("/events"):
            return response({"items": list(self.store.values())})
        if self.blank_event_reads:
            return response({})
        event_id = unquote(path.rsplit("/", 1)[-1])
        return response(self.store[event_id]) if event_id in self.store else response({"error": {"code": 404}}, 404)


class GraphWire:
    """LABELLED synthetic Microsoft Graph provider: one calendar ("work"), provider-assigned ids, and knobs
    that serve unreadable data or lose a write's reply."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.writes: list[str] = []
        self.unusable: set[str] = set()
        self.blank_event_reads = False
        self.blank_reads_after_delete = False
        self.fail_event_reads: list[int] = []
        self.lose_next_write = False
        self.lose_reply_after_post = False

    def seed(self, uid, title):
        self.events[uid] = {"id": uid, "subject": title, "start": {"dateTime": "2026-09-15T12:00:00+00:00", "timeZone": "UTC"},
                            "end": {"dateTime": "2026-09-15T12:30:00+00:00", "timeZone": "UTC"}, "@odata.etag": 'W/"v1"'}
        return wire_adapter("graph", self).get_event("work", uid)

    def _served(self, row):
        if row["id"] in self.unusable:
            return {**row, "start": {"dateTime": "not-a-date"}, "end": {"dateTime": "also-invalid"}}
        return row

    def __call__(self, req):
        path = urlparse(req.url).path
        event_id = unquote(path.rsplit("/", 1)[-1])
        if req.method in {"POST", "PATCH", "DELETE"}:
            self.writes.append(req.method)
            if self.lose_next_write:
                self.lose_next_write = False
                raise TransportUnknownError("reply_lost_before_applying")
            if req.method == "POST":
                assigned = f"graph-{len(self.events) + 1}"
                self.events[assigned] = {**json.loads(req.body), "id": assigned, "@odata.etag": 'W/"v1"'}
                if self.lose_reply_after_post:
                    self.lose_reply_after_post = False
                    raise TransportUnknownError("reply_lost_after_acceptance")
                return response(self.events[assigned], 201)
            if event_id not in self.events:
                return response({"error": {"code": "ErrorItemNotFound"}}, 404)
            if req.method == "DELETE":
                del self.events[event_id]
                self.blank_event_reads = self.blank_event_reads or self.blank_reads_after_delete
                return KasResponse(status=204)
            self.events[event_id] = {**self.events[event_id], **json.loads(req.body), "@odata.etag": 'W/"v2"'}
            return response(self.events[event_id])
        if path.endswith("/calendarview"):
            return response({"value": [self._served(row) for row in self.events.values()]})
        if self.fail_event_reads:
            return response({"error": {"code": "ServiceUnavailable"}}, self.fail_event_reads.pop(0))
        if self.blank_event_reads:
            return response({})
        return response(self._served(self.events[event_id])) if event_id in self.events else response({"error": {"code": "ErrorItemNotFound"}}, 404)


def test_google_identity_read_that_is_unusable_is_not_absence_and_never_refires(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="google", title="Novel chiller descale")
    wire = GoogleWire()
    wire.lose_next_post = True
    assert _execute(path, aid, config, wire_adapter("google", wire)).status == "outcome_unproven"

    wire.blank_event_reads = True
    unusable = _execute(path, aid, config, wire_adapter("google", wire))
    assert not unusable.ok and "remains unproven" in unusable.response_text, unusable.response_text
    assert wire.posts == [google_event_id(scope["intent_uid"])] and _stored(path, aid)[0] == "outcome_unproven"

    wire.blank_event_reads = False
    resent = _execute(path, aid, config, wire_adapter("google", wire))
    assert resent.ok and len(wire.posts) == 2 and _stored(path, aid)[0] == "executed", "an authentic 404 still permits exactly one more send"


def test_graph_retained_identity_read_that_is_unusable_is_not_a_removed_effect(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel lift inspection")
    wire = GraphWire()
    wire.fail_event_reads = [503]
    accepted = _execute(path, aid, config, wire_adapter("graph", wire))
    assert accepted.status == "outcome_unproven" and _stored(path, aid)[1]["provider_uid"] == "graph-1", accepted.response_text

    wire.blank_event_reads = True
    unusable = _execute(path, aid, config, wire_adapter("graph", wire))
    assert unusable.status != "effect_missing" and "no longer has it" not in unusable.response_text, unusable.response_text
    assert _stored(path, aid)[0] == "outcome_unproven" and wire.writes == ["POST"]

    wire.blank_event_reads = False
    verified = _execute(path, aid, config, wire_adapter("graph", wire))
    assert verified.ok and _stored(path, aid)[0] == "executed" and wire.writes == ["POST"], verified.response_text


def test_graph_correlation_search_over_unreadable_rows_is_incomplete_and_never_refires(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel sump pump swap")
    wire = GraphWire()
    wire.lose_reply_after_post = True
    assert _execute(path, aid, config, wire_adapter("graph", wire)).status == "outcome_unproven"

    wire.unusable.update(wire.events)
    unusable = _execute(path, aid, config, wire_adapter("graph", wire))
    assert not unusable.ok and wire.writes == ["POST"] and len(wire.events) == 1, unusable.response_text
    assert _stored(path, aid)[0] == "outcome_unproven"

    wire.unusable.clear()
    recovered = _execute(path, aid, config, wire_adapter("graph", wire))
    assert recovered.ok and wire.writes == ["POST"] and len(wire.events) == 1, recovered.response_text


def test_update_recovery_over_an_unusable_read_is_not_a_vanished_event(tmp_path):
    wire = GraphWire()
    graph = wire_adapter("graph", wire)
    event = wire.seed("graph-update", "Novel cooling tower check")
    kind = "provider_calendar_update"
    path, aid = lifecycle._stage(tmp_path, kind, lifecycle._update_scope(event))
    wire.lose_next_write = True
    assert lifecycle._approve(path, kind, aid, graph).status == "outcome_unproven"

    wire.blank_event_reads = True
    unusable = lifecycle._approve(path, kind, aid, graph)
    assert unusable.status != "not_found" and "no longer on the provider" not in unusable.response_text, unusable.response_text
    assert lifecycle._row(path, kind, aid)["status"] == "outcome_unproven" and wire.writes == ["PATCH"]

    wire.blank_event_reads = False
    applied = lifecycle._approve(path, kind, aid, graph)
    assert applied.ok and wire.writes == ["PATCH", "PATCH"], applied.response_text
    assert wire.events["graph-update"]["subject"] == "Novel greenhouse vent check (moved)"


def test_cancel_recovery_over_an_unusable_read_never_reports_the_event_gone(tmp_path):
    wire = GraphWire()
    graph = wire_adapter("graph", wire)
    event = wire.seed("graph-cancel", "Novel roof hatch inspection")
    kind = "provider_calendar_cancel"
    path, aid = lifecycle._stage(tmp_path, kind, {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    wire.lose_next_write = True
    assert lifecycle._approve(path, kind, aid, graph).status == "outcome_unproven"

    wire.blank_event_reads = True
    unusable = lifecycle._approve(path, kind, aid, graph)
    assert not unusable.ok and "gone" not in unusable.response_text.lower(), unusable.response_text
    assert lifecycle._row(path, kind, aid)["status"] == "outcome_unproven"
    assert "graph-cancel" in wire.events and wire.writes == ["DELETE"], "the event is still stored and nothing was sent again"

    wire.blank_event_reads = False
    cancelled = lifecycle._approve(path, kind, aid, graph)
    assert cancelled.ok and "verified gone" in cancelled.response_text and "graph-cancel" not in wire.events, cancelled.response_text
    assert wire.writes == ["DELETE", "DELETE"]


def test_cancel_read_back_that_is_unusable_is_accepted_but_unverified(tmp_path):
    wire = GraphWire()
    event = wire.seed("graph-read-back", "Novel boiler room lock change")
    wire.blank_reads_after_delete = True
    with pytest.raises(CalendarWriteAcceptedError):
        wire_adapter("graph", wire).cancel_event("work", event.uid, event.etag)

    approved = GraphWire()
    event = approved.seed("graph-read-back-2", "Novel loading dock lock change")
    approved.blank_reads_after_delete = True
    kind = "provider_calendar_cancel"
    path, aid = lifecycle._stage(tmp_path, kind, {"uid": event.uid, "calendar_id": "work", "etag_seen": event.etag, "title": event.summary})
    result = lifecycle._approve(path, kind, aid, wire_adapter("graph", approved))
    assert result.status == "outcome_unproven" and result.details["accepted"] is True, result.response_text
    assert "verified gone" not in result.response_text and lifecycle._row(path, kind, aid)["status"] == "outcome_unproven"
