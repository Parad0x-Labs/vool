"""pa_beta_gate -- revision-5 NOVEL cases: calendar effect phase and provider identity stay monotonic.

Different providers, data and failure points from the independent review's originals: the real
Microsoft Graph and Google adapters over strict synthetic wires, the real SQLite approval store,
and real child processes that die on either side of the effect boundary. Provider behaviour is a
LABELLED synthetic double; no account, credential or network is used.
"""
from __future__ import annotations

import json
import multiprocessing
import os
from dataclasses import replace
from types import SimpleNamespace

from core.kas.adapters.google_calendar import google_event_id
from core.kas.contract import CalendarRefusedError, CalEvent, TransportUnknownError
from core.operator import approvals
from tests.pa_beta_gate.test_calendar_v2_independent_review import adapter as wire_adapter
from tests.pa_beta_gate.test_calendar_v2_independent_review import response, staged
from tests.pa_beta_gate.test_v5_calendar_operation_ownership import (
    _SESSION,
    _TABLE,
    _connection,
    _execute,
    _fns,
    _now,
    _row,
)


def _stage(tmp_path, *, provider, title, status="pending_approval", result=None):
    config, original = staged(provider, title)
    path = tmp_path / "operator.sqlite"
    with _connection(path) as conn:
        conn.execute(_TABLE)
    aid = approvals.create_pending_action(session_id=_SESSION, task_id="phase-task", action_kind="provider_calendar_event",
                                          scope=json.loads(original["scope_json"]), now_fn=_now, get_connection_fn=lambda: _connection(path))
    if status != "pending_approval" or result is not None:
        with _connection(path) as conn:
            conn.execute("UPDATE operator_action_requests SET status = ?, result_json = ? WHERE action_id = ?",
                         (status, json.dumps(result or {}), aid))
    return str(path), aid, config, json.loads(original["scope_json"])


def _stored(path, aid):
    row = _row(path, aid)
    return row["status"], json.loads(row["result_json"])


def _exact(scope, uid=None):
    return CalEvent(provider_id="caldav", uid=uid or scope["intent_uid"], calendar_id="work", summary=scope["title"],
                    start_utc=scope["start_utc"], end_utc=scope["end_utc"], tz_name="UTC")


# ---------------------------------------------------------------------------------------------
# Accepted-but-unverified keeps the provider identity and never reopens as never-sent
# ---------------------------------------------------------------------------------------------


def test_graph_create_accepted_then_read_back_unavailable_keeps_identity_and_recovers_by_reading_it(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel tenant walkthrough")
    store, posts, read_failures = {}, [], [503]

    def wire(req):
        if req.method == "POST":
            body = json.loads(req.body)
            assigned = f"graph-novel-{len(store) + 7}"
            store[assigned] = dict(body, id=assigned, **{"@odata.etag": 'W/"v1"'})
            posts.append(assigned)
            return response(store[assigned], 201)
        if "calendarview" in req.url:
            return response({"value": list(store.values())})
        if read_failures:
            return response({"error": {"code": "ServiceUnavailable"}}, read_failures.pop(0))
        uid = req.url.rsplit("/", 1)[-1]
        return response(store[uid]) if uid in store else response({}, 404)

    graph = wire_adapter("graph", wire)
    first = _execute(path, aid, config, graph)
    status, result = _stored(path, aid)
    assert first.status == "outcome_unproven" and "NOT reported as never created" in first.response_text, first.response_text
    assert status == "outcome_unproven" and result["provider_uid"] == "graph-novel-7"
    assert result["operation"]["phase"] == "accepted" and result["operation"]["evidence"]["accepted"] is True

    second = _execute(path, aid, config, graph)
    status, result = _stored(path, aid)
    assert second.ok and second.details["uid"] == "graph-novel-7", second.response_text
    assert status == "executed" and posts == ["graph-novel-7"], "recovery read the retained id; nothing was POSTed twice"


def test_verified_google_effect_that_vanished_is_reported_gone_and_never_recreated(tmp_path):
    config, original = staged("google", "Novel boiler certificate renewal")
    scope = json.loads(original["scope_json"])
    mapped = google_event_id(scope["intent_uid"])
    receipt = {"uid": mapped, "provider_uid": mapped, "title": scope["title"], "receipt_error": "disk full while recording"}
    path, aid, config, _ = _stage(tmp_path, provider="google", title="Novel boiler certificate renewal", status="effect_unrecorded", result=receipt)
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.url)
            return response({}, 500)
        if "/events?" in req.url:
            return response({"items": []})
        return response({"error": {"code": 404}}, 404)

    google = wire_adapter("google", wire)
    for _attempt in range(2):
        gone = _execute(path, aid, config, google)
        assert gone.status == "effect_missing" and "did NOT re-create" in gone.response_text, gone.response_text
    status, result = _stored(path, aid)
    assert posts == [] and status == "effect_unrecorded"
    assert result["operation"]["evidence"]["last_verification"] == "absent" and result["uid"] == mapped


def test_accepted_graph_effect_later_absent_is_not_resurrected(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel freight booking")
    approvals.mark_action_outcome_unproven(aid, result={"provider_uid": "graph-accepted-9", "accepted": True, "reason": "read_back_http_503"}, **_fns(path))
    posts = []

    def wire(req):
        if req.method == "POST":
            posts.append(req.url)
            return response({"id": "must-not-exist"}, 201)
        if "calendarview" in req.url:
            return response({"value": []})
        return response({"error": {"code": "ErrorItemNotFound"}}, 404)

    result = _execute(path, aid, config, wire_adapter("graph", wire))
    assert result.status == "effect_missing" and posts == [], result.response_text
    assert _stored(path, aid)[0] == "outcome_unproven"


# ---------------------------------------------------------------------------------------------
# Only a definitive refusal of the write itself returns an operation to never-sent
# ---------------------------------------------------------------------------------------------


def test_definitive_create_refusal_is_unsent_and_one_new_approval_sends_once(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="caldav", title="Novel vineyard spraying window")
    creates, stored = [], []

    def create_event(cal, value):
        creates.append(value.uid)
        if len(creates) == 1:
            raise CalendarRefusedError(403, reason="http_403", detail="calendar is read-only for this principal")
        stored.append(replace(value, tz_name="UTC"))
        return stored[-1]

    def get_event(cal, uid):
        if stored:
            return stored[-1]
        raise CalendarRefusedError(404, reason="not_found")

    adapter = SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [], create_event=create_event, get_event=get_event)
    refused = _execute(path, aid, config, adapter)
    status, result = _stored(path, aid)
    assert refused.status == "provider_refused" and status == "pending_approval", (refused.status, status)
    assert result["operation"]["phase"] == "unsent"
    sent = _execute(path, aid, config, adapter)
    assert sent.ok and len(creates) == 2 and len(stored) == 1, sent.response_text
    assert _stored(path, aid)[0] == "executed" and scope["intent_uid"] == sent.details["uid"]


def test_ambiguous_5xx_on_create_is_unproven_then_reconciled_by_exact_identity(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="caldav", title="Novel lab fridge defrost")
    provider, creates = {}, []

    def create_event(cal, value):
        creates.append(value.uid)
        if len(creates) == 1:
            raise CalendarRefusedError(502, reason="http_502", detail="bad gateway")
        provider[value.uid] = replace(value, tz_name="UTC")
        return provider[value.uid]

    def get_event(cal, uid):
        if uid in provider:
            return provider[uid]
        raise CalendarRefusedError(404, reason="not_found")

    adapter = SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [], create_event=create_event, get_event=get_event)
    first = _execute(path, aid, config, adapter)
    assert first.status == "outcome_unproven" and _stored(path, aid)[0] == "outcome_unproven", first.response_text
    second = _execute(path, aid, config, adapter)
    assert second.ok and len(creates) == 2 and len(provider) == 1, second.response_text


def test_unanswered_availability_check_leaves_the_operation_unsent(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="caldav", title="Novel courier pickup")
    creates = []

    def unanswered(*a, **k):
        raise TransportUnknownError("read_timeout")

    adapter = SimpleNamespace(provider_id="caldav", events_in_range=unanswered, create_event=lambda *a, **k: creates.append(a),
                              get_event=lambda *a, **k: None)
    result = _execute(path, aid, config, adapter)
    status, stored = _stored(path, aid)
    assert result.status == "provider_unreachable" and "NOT sent" in result.response_text, result.response_text
    assert status == "pending_approval" and stored["operation"]["phase"] == "unsent" and creates == []


def test_refused_reconcile_read_keeps_every_retained_identity(tmp_path):
    path, aid, config, _scope = _stage(tmp_path, provider="graph", title="Novel roof inspection")
    approvals.mark_action_outcome_unproven(aid, result={"provider_uid": "graph-kept-3", "accepted": True, "intent_uid": "kept", "reason": "read_back_http_503"}, **_fns(path))

    def throttled(*a, **k):
        raise CalendarRefusedError(429, reason="rate_limited")

    result = _execute(path, aid, config, SimpleNamespace(provider_id="graph", durable_to_provider_id=lambda uid: "", get_event=throttled,
                                                       events_in_range=throttled, create_event=lambda *a, **k: None))
    status, stored = _stored(path, aid)
    assert result.status == "provider_refused" and status == "outcome_unproven", result.response_text
    assert stored["provider_uid"] == "graph-kept-3" and stored["operation"]["evidence"]["accepted"] is True
    assert "release_note" not in stored and stored["operation"]["history"][-1]["event"] == "released:outcome_unproven"


# ---------------------------------------------------------------------------------------------
# Real process death on either side of the boundary; receipts that cannot be written
# ---------------------------------------------------------------------------------------------


def _read_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.loads(handle.read())


def _provider_file_adapter(provider_file, *, on_create):
    def load():
        return _read_json(provider_file)

    def get_event(cal, uid):
        record = load().get(uid)
        if record is None:
            raise CalendarRefusedError(404, reason="not_found")
        return CalEvent(provider_id="caldav", uid=uid, calendar_id=cal, summary=record["summary"], start_utc=record["start"], end_utc=record["end"], tz_name=record["tz"])

    def create_event(cal, value):
        return on_create(value, load)

    return SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [], create_event=create_event, get_event=get_event)


def _store_event(provider_file, value, load):
    data = load()
    data[value.uid] = {"summary": value.summary, "start": value.start_utc, "end": value.end_utc, "tz": value.tz_name}
    with open(provider_file, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data))


def _dispatch_then_die(path, aid, config, provider_file, effect_lands):
    def on_create(value, load):
        if effect_lands:
            _store_event(provider_file, value, load)
        os._exit(11)

    _execute(path, aid, config, _provider_file_adapter(provider_file, on_create=on_create))


def _crash_case(tmp_path, *, title, effect_lands):
    path, aid, config, _scope = _stage(tmp_path, provider="caldav", title=title)
    provider_file = str(tmp_path / "provider-events.json")
    child = multiprocessing.get_context("spawn").Process(target=_dispatch_then_die, args=(path, aid, config, provider_file, effect_lands))
    child.start()
    child.join(20)
    assert child.exitcode == 11
    status, stored = _stored(path, aid)
    assert status == "executing" and stored["operation"]["phase"] == "dispatching", stored["operation"]
    parent_creates = []

    def on_create(value, load):
        parent_creates.append(value.uid)
        _store_event(provider_file, value, load)
        return value

    recovered = _execute(path, aid, config, _provider_file_adapter(provider_file, on_create=on_create))
    events = _read_json(provider_file)
    return recovered, parent_creates, events, _stored(path, aid)


def test_child_that_died_after_the_effect_landed_is_recovered_without_a_second_send(tmp_path):
    recovered, parent_creates, events, (status, stored) = _crash_case(tmp_path, title="Novel ferry deck check", effect_lands=True)
    assert recovered.ok and recovered.details["recovered"] is True, recovered.response_text
    assert parent_creates == [] and len(events) == 1 and status == "executed"
    assert "owner_gone:outcome_unproven" in [entry["event"] for entry in stored["operation"]["history"]]


def test_child_that_died_before_the_effect_landed_is_proven_absent_and_sent_once(tmp_path):
    recovered, parent_creates, events, (status, _stored_result) = _crash_case(tmp_path, title="Novel ferry engine check", effect_lands=False)
    assert recovered.ok and recovered.details["recovered"] is False, recovered.response_text
    assert len(parent_creates) == 1 and len(events) == 1 and status == "executed"


def test_verified_effect_whose_receipts_both_failed_is_recovered_by_reading_not_resending(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="caldav", title="Novel water tank flush")
    provider, creates = {}, []

    def create_event(cal, value):
        creates.append(value.uid)
        provider[value.uid] = replace(value, tz_name="UTC")
        return provider[value.uid]

    adapter = SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [], create_event=create_event,
                              get_event=lambda cal, uid: provider[uid])
    kwargs = _fns(path)

    def failing_mark(*a, **k):
        raise OSError("synthetic: receipt storage unavailable")

    from core.operator import calendar_provider as cp
    from core.operator.models import OperatorActionIntent

    first = cp.execute_proposed_event(
        OperatorActionIntent(kind="approve_calendar_event", action_id=aid), task_id="phase-task", session_id=_SESSION,
        adapter=adapter, config=config, pending_row=_row(path, aid), load_pending_action_fn=lambda **k: _row(path, aid),
        claim_action_fn=lambda key: approvals.claim_pending_action(key, **kwargs),
        mark_action_executed_fn=failing_mark, mark_effect_unrecorded_fn=failing_mark,
        mark_outcome_unproven_fn=lambda key, **k: approvals.mark_action_outcome_unproven(key, **kwargs, **k),
        requeue_interrupted_fn=lambda key, **k: approvals.requeue_interrupted_action(key, **kwargs, **k),
        audit_log_fn=lambda *a, **k: None,
    )
    assert first.ok and first.details["receipt_outcome"].startswith("unrecordable"), first.response_text
    status, stored = _stored(path, aid)
    assert status == "executing" and stored["operation"]["phase"] == "accepted" and approvals.held_claim(aid) is None
    second = _execute(path, aid, config, adapter)
    assert second.ok and second.details["recovered"] is True and creates == [scope["intent_uid"]], second.response_text
    assert _stored(path, aid)[0] == "executed"


# ---------------------------------------------------------------------------------------------
# Defence in depth: the store keeps acceptance and identity even against a wrong caller
# ---------------------------------------------------------------------------------------------


def test_acceptance_cannot_be_released_back_to_never_sent(tmp_path):
    path, aid, _config, _scope = _stage(tmp_path, provider="graph", title="Novel pallet recount")
    kwargs = _fns(path)
    assert approvals.claim_pending_action(aid, **kwargs)
    claim = approvals.held_claim(aid)
    assert claim.record(phase="dispatching") is True
    assert claim.record(phase="accepted", evidence={"provider_uid": "graph-pallet-1", "accepted": True}) is True
    rested = claim.release(note="a caller wrongly claims the write was refused", floor_status="pending_approval", proven_not_applied=True)
    status, stored = _stored(path, aid)
    assert rested == status == "outcome_unproven", (rested, status)
    assert stored["operation"]["phase"] == "accepted" and stored["provider_uid"] == "graph-pallet-1"


def test_later_empty_values_never_erase_a_retained_identity(tmp_path):
    path, aid, _config, _scope = _stage(tmp_path, provider="graph", title="Novel dock light repair")
    kwargs = _fns(path)
    approvals.mark_action_outcome_unproven(aid, result={"provider_uid": "graph-dock-4", "calendar_id": "work", "accepted": True}, **kwargs)
    approvals.mark_action_outcome_unproven(aid, result={"provider_uid": "", "calendar_id": "", "reason": "later read refused"}, **kwargs)
    status, stored = _stored(path, aid)
    assert status == "outcome_unproven" and stored["provider_uid"] == "graph-dock-4" and stored["calendar_id"] == "work"
    assert stored["operation"]["evidence"]["provider_uid"] == "graph-dock-4" and stored["reason"] == "later read refused"


def test_verification_read_refused_after_create_answers_accepted_not_refused(tmp_path):
    path, aid, config, scope = _stage(tmp_path, provider="caldav", title="Novel sluice gate service")
    created = []

    def throttled_read(cal, uid):
        raise CalendarRefusedError(503, reason="http_503")

    adapter = SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [],
                              create_event=lambda cal, value: (created.append(value.uid) or value), get_event=throttled_read)
    result = _execute(path, aid, config, adapter)
    status, stored = _stored(path, aid)
    assert result.status == "outcome_unproven" and "NOT reported as never created" in result.response_text, result.response_text
    assert "Nothing was changed" not in result.response_text
    assert status == "outcome_unproven" and stored["provider_uid"] == scope["intent_uid"] and created == [scope["intent_uid"]]
