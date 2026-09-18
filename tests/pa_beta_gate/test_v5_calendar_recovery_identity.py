"""pa_beta_gate -- revision-5 NOVEL cases: recovery identity is proven, never inferred from content.

The real Microsoft Graph and Google adapters over strict synthetic wires (LABELLED: synthetic
providers, no account involved) and the real SQLite approval store. The Graph wire honours the documented
``transactionId`` semantics: stored with the event, returned on reads, and used to deduplicate a
replayed create. Different data and failure shapes from the independent review's two originals.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from core.kas.adapters._json_calendars import json_event_to_cal
from core.kas.adapters.eventkit_mac import EventKitCalendarAdapter
from core.kas.adapters.google_calendar import GoogleCalendarAdapter, google_event_id
from core.kas.adapters.graph_calendar import GraphCalendarAdapter, graph_transaction_id
from core.kas.contract import AdapterConfig, CalEvent, TransportUnknownError
from core.operator import approvals
from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter as wire_adapter
from tests.pa_beta_gate.test_calendar_v2_independent_review import response
from tests.pa_beta_gate.test_v5_calendar_effect_phase import _stage, _stored
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import _execute, _fns


def _instant(block):
    return datetime.fromisoformat(str(block["dateTime"]))


class GraphWire:
    """A strict Graph double: window-filtered calendarView, transactionId stored and deduplicated."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.posts: list[str] = []
        self.transactions: dict[str, str] = {}
        self.lose_reply_after_store = False
        self.lose_reply_before_store = False
        self.view_status = 0

    def seed(self, event_id, body):
        self.store[event_id] = dict(body, id=event_id, **{"@odata.etag": 'W/"seed"'})

    def __call__(self, req):
        if req.method == "POST":
            body = json.loads(req.body)
            tx = str(body.get("transactionId") or "")
            self.posts.append(tx)
            if self.lose_reply_before_store:
                self.lose_reply_before_store = False
                raise TransportUnknownError("connection_reset_before_acceptance")
            if tx and tx in self.transactions:
                return response(self.store[self.transactions[tx]], 201)
            assigned = f"graph-id-{len(self.store) + 1}"
            self.store[assigned] = dict(body, id=assigned, **{"@odata.etag": 'W/"v1"'})
            if tx:
                self.transactions[tx] = assigned
            if self.lose_reply_after_store:
                self.lose_reply_after_store = False
                raise TransportUnknownError("reply_lost_after_acceptance")
            return response(self.store[assigned], 201)
        parsed = urlparse(req.url)
        if "calendarview" in parsed.path:
            if self.view_status:
                return response({"error": {"code": "ServiceUnavailable"}}, self.view_status)
            query = parse_qs(parsed.query)
            window_start = datetime.fromisoformat(unquote(query["startDateTime"][0]))
            window_end = datetime.fromisoformat(unquote(query["endDateTime"][0]))
            rows = [row for row in self.store.values() if _instant(row["start"]) < window_end and window_start < _instant(row["end"])]
            return response({"value": rows})
        event_id = unquote(parsed.path.rsplit("/", 1)[-1])
        return response(self.store[event_id]) if event_id in self.store else response({}, 404)


def _twin_body(scope, *, start=None, end=None):
    return {"subject": scope["title"], "start": {"dateTime": start or scope["start_utc"], "timeZone": "UTC"},
            "end": {"dateTime": end or scope["end_utc"], "timeZone": "UTC"}}


def test_graph_recovery_certifies_only_the_event_carrying_this_operations_transaction_id(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel harbour pilot briefing")
    wire = GraphWire()
    wire.lose_reply_after_store = True
    graph = wire_adapter("graph", wire)
    first = _execute(path, aid, config, graph)
    assert first.status == "outcome_unproven" and len(wire.store) == 1, first.response_text
    wire.seed("unrelated-twin", _twin_body(scope))  # same title, time and zone; not this approval's write
    recovered = _execute(path, aid, config, graph)
    assert recovered.ok and recovered.details["uid"] == "graph-id-1", recovered.response_text
    assert wire.posts == [graph_transaction_id(scope["intent_uid"])], "recovery sent nothing"
    status, stored = _stored(path, aid)
    assert status == "executed" and stored["uid"] == "graph-id-1"


def test_graph_absence_proven_by_correlation_resends_once_with_the_same_transaction_id(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel crane load test")
    wire = GraphWire()
    wire.lose_reply_before_store = True
    graph = wire_adapter("graph", wire)
    assert _execute(path, aid, config, graph).status == "outcome_unproven" and wire.store == {}
    resent = _execute(path, aid, config, graph)
    tx = graph_transaction_id(scope["intent_uid"])
    assert resent.ok and resent.details["recovered"] is False, resent.response_text
    assert wire.posts == [tx, tx] and list(wire.store) == ["graph-id-1"]


def test_identical_content_event_from_elsewhere_is_a_conflict_not_this_operation(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel ferry timetable review")
    wire = GraphWire()
    wire.lose_reply_before_store = True
    graph = wire_adapter("graph", wire)
    assert _execute(path, aid, config, graph).status == "outcome_unproven"
    wire.seed("colleague-copy", _twin_body(scope))
    result = _execute(path, aid, config, graph)
    assert result.status == "conflict" and "colleague-copy" not in json.dumps(_stored(path, aid)[1]), result.response_text
    assert len(wire.posts) == 1 and list(wire.store) == ["colleague-copy"]


def test_two_events_carrying_the_same_correlation_are_ambiguous_and_nothing_is_certified(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel generator fuel check", status="outcome_unproven")
    tx = graph_transaction_id(scope["intent_uid"])
    wire = GraphWire()
    wire.seed("copy-a", {**_twin_body(scope), "transactionId": tx})
    wire.seed("copy-b", {**_twin_body(scope), "transactionId": tx})
    result = _execute(path, aid, config, wire_adapter("graph", wire))
    status, stored = _stored(path, aid)
    assert result.status == "identity_ambiguous" and wire.posts == [], result.response_text
    assert status == "outcome_unproven" and "uid" not in stored


def test_incomplete_correlation_discovery_is_not_absence(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel lift certificate", status="outcome_unproven")
    wire = GraphWire()
    wire.view_status = 503
    result = _execute(path, aid, config, wire_adapter("graph", wire))
    assert result.status == "outcome_unproven" and "nothing was sent again" in result.response_text, result.response_text
    assert wire.posts == [] and _stored(path, aid)[0] == "outcome_unproven"


def test_correlated_event_moved_by_the_user_is_identified_not_duplicated(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel tide gauge calibration", status="outcome_unproven")
    tx = graph_transaction_id(scope["intent_uid"])
    wire = GraphWire()
    wire.seed("moved-original", {**_twin_body(scope, start="2026-09-18T08:00:00+00:00", end="2026-09-18T08:30:00+00:00"), "transactionId": tx})
    wire.transactions[tx] = "moved-original"
    result = _execute(path, aid, config, wire_adapter("graph", wire))
    status, stored = _stored(path, aid)
    assert result.status == "identity_mismatch", result.response_text
    assert wire.posts == [tx] and list(wire.store) == ["moved-original"], "the replayed create was deduplicated, never a second event"
    assert status == "outcome_unproven" and stored["provider_uid"] == "moved-original" and stored["operation"]["evidence"]["accepted"] is True


def test_adapter_without_correlation_support_never_certifies_identical_content(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="graph", title="Novel archive box audit", status="outcome_unproven")
    twins = [CalEvent(provider_id="graph", uid=uid, calendar_id="work", summary=scope["title"], start_utc=scope["start_utc"],
                      end_utc=scope["end_utc"], tz_name="UTC") for uid in ("look-alike-1", "look-alike-2")]
    creates = []
    adapter = SimpleNamespace(provider_id="graph", durable_to_provider_id=lambda uid: "", events_in_range=lambda *a, **k: list(twins),
                              get_event=lambda *a, **k: twins[0], create_event=lambda *a, **k: creates.append(a))
    result = _execute(path, aid, config, adapter)
    status, stored = _stored(path, aid)
    assert result.status == "outcome_unproven" and "not proof" in result.response_text, result.response_text
    assert [row["uid"] for row in result.details["candidates"]] == ["look-alike-1", "look-alike-2"]
    assert creates == [] and status == "outcome_unproven" and "uid" not in stored


def test_google_deterministic_identity_still_recovers_by_reading_the_mapped_id(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="google", title="Novel orchard irrigation check")
    mapped = google_event_id(scope["intent_uid"])
    stored_events, posts = {}, []
    twin_present = []  # the identical-content event appears only AFTER the first attempt

    def wire(req):
        if req.method == "POST":
            body = json.loads(req.body)
            posts.append(body["id"])
            stored_events[body["id"]] = dict(body, etag='"v1"')
            if len(posts) == 1:
                twin_present.append(True)
                raise TransportUnknownError("reply_lost_after_acceptance")
            return response(stored_events[body["id"]], 201)
        if "/events?" in req.url:
            twin = {"id": "unrelated00001", "summary": scope["title"], "start": {"dateTime": scope["start_utc"], "timeZone": "UTC"},
                    "end": {"dateTime": scope["end_utc"], "timeZone": "UTC"}, "etag": '"t"'}
            return response({"items": ([twin] if twin_present else []) + [*stored_events.values()]})
        event_id = unquote(req.url.rsplit("/", 1)[-1])
        return response(stored_events[event_id]) if event_id in stored_events else response({}, 404)

    google = wire_adapter("google", wire)
    assert _execute(path, aid, config, google).status == "outcome_unproven"
    recovered = _execute(path, aid, config, google)
    assert recovered.ok and recovered.details["uid"] == mapped and posts == [mapped], recovered.response_text


def test_contract_projects_graph_transaction_id_and_eventkit_has_no_durable_mapping():
    event = json_event_to_cal({"id": "g1", "subject": "S", "transactionId": "tx-17",
                               "start": {"dateTime": "2026-09-15T12:00:00+00:00", "timeZone": "UTC"},
                               "end": {"dateTime": "2026-09-15T12:30:00+00:00", "timeZone": "UTC"}},
                              provider_id="graph", calendar_id="work", etag="", href="")
    assert event.correlation_id == "tx-17"
    config = AdapterConfig(provider_id="x", base_url="https://calendar.example.test/api")
    graph = GraphCalendarAdapter(transport=lambda req: None, config=config)
    google = GoogleCalendarAdapter(transport=lambda req: None, config=config)
    assert graph.durable_correlation("intent@vool.local") == graph_transaction_id("intent@vool.local")
    assert graph.durable_correlation("") == "" and google.durable_correlation("intent@vool.local") == ""
    eventkit = EventKitCalendarAdapter(transport=lambda req: None, config=AdapterConfig(provider_id="eventkit", base_url="eventkit://local"),
                                       store=SimpleNamespace(calendars=lambda: []))
    assert eventkit.durable_to_provider_id("intent@vool.local") == ""
    assert approvals.OPERATION_KEY == "operation" and _fns  # shared helpers stay importable
