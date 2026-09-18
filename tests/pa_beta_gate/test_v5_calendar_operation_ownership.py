"""pa_beta_gate -- revision-5 NOVEL cases for calendar operation ownership.

The real SQLite approval store and real separate processes; provider adapters are LABELLED
synthetic doubles (no account, no network). Different data and different entry states from the
independent review's live-owner probe: a worker that dies holding a never-dispatched claim, a live
reconciling owner in another process, stale-token writes, late downgrades of a completed
operation, a store with no database file, and a revision-4 `executing` row with no owner record.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.kas.contract import CalendarRefusedError
from core.operator import approvals
from core.operator import calendar_provider as cp
from core.operator.models import OperatorActionIntent
from tests.pa_beta_gate.test_calendar_v2_independent_review import event, staged

_SESSION = "ownership-session"
_TABLE = ("CREATE TABLE IF NOT EXISTS operator_action_requests (action_id TEXT PRIMARY KEY, session_id TEXT, task_id TEXT, "
          "action_kind TEXT, scope_json TEXT, result_json TEXT, status TEXT, created_at TEXT, updated_at TEXT, executed_at TEXT)")


def _connection(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return "2026-09-14T15:00:00+00:00"


def _stage(tmp_path, *, title, status="pending_approval"):
    config, original = staged("caldav", title)
    path = tmp_path / "operator.sqlite"
    with _connection(path) as conn:
        conn.execute(_TABLE)
    aid = approvals.create_pending_action(session_id=_SESSION, task_id="ownership-task", action_kind="provider_calendar_event",
                                          scope=json.loads(original["scope_json"]), now_fn=_now, get_connection_fn=lambda: _connection(path))
    if status != "pending_approval":
        with _connection(path) as conn:
            conn.execute("UPDATE operator_action_requests SET status = ? WHERE action_id = ?", (status, aid))
    return str(path), aid, config


def _row(path, aid):
    return cp.load_action_any_state(session_id=_SESSION, action_kind="provider_calendar_event", action_id=aid,
                                    get_connection_fn=lambda: _connection(path))


def _fns(path):
    kwargs = dict(now_fn=_now, get_connection_fn=lambda: _connection(path))
    return kwargs


def _execute(path, aid, config, adapter):
    kwargs = _fns(path)
    return cp.execute_proposed_event(
        OperatorActionIntent(kind="approve_calendar_event", action_id=aid),
        task_id="ownership-task", session_id=_SESSION, adapter=adapter, config=config,
        pending_row=_row(path, aid), load_pending_action_fn=lambda **k: _row(path, aid),
        claim_action_fn=lambda key: approvals.claim_pending_action(key, **kwargs),
        mark_action_executed_fn=lambda key, **k: approvals.mark_action_executed(key, **kwargs, **k),
        mark_outcome_unproven_fn=lambda key, **k: approvals.mark_action_outcome_unproven(key, **kwargs, **k),
        mark_effect_unrecorded_fn=lambda key, **k: approvals.mark_action_effect_unrecorded(key, **kwargs, **k),
        requeue_interrupted_fn=lambda key, **k: approvals.requeue_interrupted_action(key, **kwargs, **k),
        audit_log_fn=lambda *a, **k: None,
    )


def _exact(scope):
    return replace(event(scope["intent_uid"], "caldav", scope["title"]), tz_name="UTC")


def _claim_then_die(path, aid):
    assert approvals.claim_pending_action(aid, now_fn=_now, get_connection_fn=lambda: _connection(path))
    os._exit(9)  # dies holding the claim, before any provider call


def _hold_as_live_owner(path, aid, config, entered, release, calls_log):
    scope = json.loads(_row(path, aid)["scope_json"])
    existing = _exact(scope)
    created = []

    def note(label):
        with open(calls_log, "a", encoding="utf-8") as handle:
            handle.write(label + "\n")

    def pause(label):
        note(label)
        if not entered.is_set():
            entered.set()
            if not release.wait(15):
                raise RuntimeError("synchronization timeout")

    def events_in_range(*a, **k):
        pause("worker-range")
        return []

    def create_event(cal, value):
        note("worker-create")
        created.append(replace(value, tz_name="UTC"))
        return created[-1]

    def get_event(cal, uid):
        pause("worker-read")
        return created[-1] if created else existing

    result = _execute(path, aid, config, SimpleNamespace(provider_id="caldav", get_event=get_event,
                                                       events_in_range=events_in_range, create_event=create_event))
    if not result.ok:
        raise SystemExit(5)


def test_worker_that_died_before_dispatch_is_recovered_as_never_sent(tmp_path):
    path, aid, config = _stage(tmp_path, title="Boiler room inspection")
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_claim_then_die, args=(path, aid))
    child.start()
    child.join(20)
    assert child.exitcode == 9
    held = _row(path, aid)
    assert held["status"] == "executing"
    record = json.loads(held["result_json"])["operation"]
    assert record["phase"] == "unsent" and record["owner"]["entry_status"] == "pending_approval"

    created = []

    def get_event(cal, uid):
        if created:
            return created[-1]
        raise CalendarRefusedError(404, reason="not_found")

    result = _execute(path, aid, config, SimpleNamespace(provider_id="caldav", events_in_range=lambda *a, **k: [], get_event=get_event,
                                                       create_event=lambda cal, value: (created.append(replace(value, tz_name="UTC")) or created[-1])))
    assert result.ok and result.status == "executed", result.response_text
    assert len(created) == 1
    final = json.loads(_row(path, aid)["result_json"])
    events = [entry["event"] for entry in final["operation"]["history"]]
    assert "owner_gone:pending_approval" in events and final["operation"]["phase"] == "recorded"
    assert final["operation"]["fence"] == 2, "the dead owner's fence was superseded, never reused"


@pytest.mark.parametrize("entry_status,title,expected_calls", [
    ("outcome_unproven", "Novel ward handover", ["worker-read"]),
    ("pending_approval", "Novel cold-chain audit", ["worker-range", "worker-create", "worker-read"]),
])
def test_live_owner_in_another_process_is_neither_displaced_nor_altered(tmp_path, entry_status, title, expected_calls):
    path, aid, config = _stage(tmp_path, title=title, status=entry_status)
    calls_log = tmp_path / "provider-calls.txt"
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_as_live_owner, args=(path, aid, config, entered, release, str(calls_log)))
    child.start()
    parent_calls = []
    try:
        assert entered.wait(20), f"child never reached its provider call (exit={child.exitcode})"
        held = _row(path, aid)
        assert held["status"] == "executing"
        adapter = SimpleNamespace(provider_id="caldav",
                                  get_event=lambda *a, **k: parent_calls.append("parent-read"),
                                  events_in_range=lambda *a, **k: parent_calls.append("parent-range") or [],
                                  create_event=lambda *a, **k: parent_calls.append("parent-create"))
        refused = _execute(path, aid, config, adapter)
        assert refused.status == "concurrent_approval", refused.response_text
        assert "Nothing was executed a second time" in refused.response_text
        during = _row(path, aid)
        assert (during["status"], during["result_json"]) == (held["status"], held["result_json"]), "a refused approval altered the live owner's durable state"
    finally:
        release.set()
        child.join(20)
    assert child.exitcode == 0, child.exitcode
    assert parent_calls == [] and calls_log.read_text().splitlines() == expected_calls
    assert _row(path, aid)["status"] == "executed"


def test_stale_owner_cannot_write_over_a_newer_owner(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Loading dock rota")
    kwargs = _fns(path)
    assert approvals.claim_pending_action(aid, **kwargs)
    claim = approvals.held_claim(aid)
    # Another worker has since become the owner (as recovery would record it): token and fence moved on.
    with _connection(path) as conn:
        result = json.loads(conn.execute("SELECT result_json FROM operator_action_requests WHERE action_id = ?", (aid,)).fetchone()[0])
        result["operation"]["owner"] = {"token": "newer-owner", "fence": 7, "entry_status": "pending_approval"}
        result["operation"]["fence"] = 7
        conn.execute("UPDATE operator_action_requests SET result_json = ? WHERE action_id = ?", (json.dumps(result), aid))
    before = _row(path, aid)["result_json"]
    assert claim.record(phase="dispatching") is False, "a stale owner must not cross the effect boundary"
    with pytest.raises(approvals.OperationOwnershipError):
        approvals.mark_action_executed(aid, result={"uid": "stale-receipt"}, **kwargs)
    with pytest.raises(approvals.OperationOwnershipError):
        approvals.mark_action_outcome_unproven(aid, result={"reason": "stale"}, **kwargs)
    with pytest.raises(approvals.OperationOwnershipError):
        claim.release(note="stale release")
    after = _row(path, aid)
    assert after["status"] == "executing" and after["result_json"] == before
    assert approvals.held_claim(aid) is None, "a failed release still drops the stale handle"


def test_completed_operation_refuses_late_downgrades_but_accepts_its_own_receipt(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Quarterly fire drill")
    kwargs = _fns(path)
    assert approvals.claim_pending_action(aid, **kwargs)
    receipt = {"uid": "drill-uid-17", "etag": "v1", "title": "Quarterly fire drill"}
    approvals.mark_action_executed(aid, result=receipt, **kwargs)
    assert approvals.held_claim(aid) is None
    approvals.mark_action_executed(aid, result=receipt, **kwargs)
    with pytest.raises(approvals.OperationStateError):
        approvals.mark_action_outcome_unproven(aid, result={"reason": "late"}, **kwargs)
    with pytest.raises(approvals.OperationStateError):
        approvals.mark_action_executed(aid, result={"uid": "some-other-event"}, **kwargs)
    final = _row(path, aid)
    stored = json.loads(final["result_json"])
    assert final["status"] == "executed" and stored["uid"] == "drill-uid-17"
    assert stored["operation"]["evidence"]["provider_uid"] == "drill-uid-17"


def test_store_without_a_database_file_fails_closed(tmp_path):
    uri = "file:ownership-memory-store?mode=memory&cache=shared"
    anchor = sqlite3.connect(uri, uri=True)
    try:
        anchor.execute(_TABLE)
        anchor.commit()

        def connect():
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        aid = approvals.create_pending_action(session_id=_SESSION, task_id="t", action_kind="provider_calendar_event", scope={"title": "x"},
                                              now_fn=_now, get_connection_fn=connect)
        assert approvals.claim_pending_action(aid, now_fn=_now, get_connection_fn=connect) is False
        with connect() as conn:
            conn.execute("UPDATE operator_action_requests SET status = 'executing' WHERE action_id = ?", (aid,))
        assert approvals.requeue_interrupted_action(aid, entry_state="outcome_unproven", note="n", now_fn=_now, get_connection_fn=connect) is False
        with connect() as conn:
            assert conn.execute("SELECT status FROM operator_action_requests WHERE action_id = ?", (aid,)).fetchone()[0] == "executing"
    finally:
        anchor.close()


def test_relinquished_claim_is_recovered_by_evidence_not_by_memory(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Greenhouse venting check")
    kwargs = _fns(path)
    assert approvals.claim_pending_action(aid, **kwargs) is True
    assert approvals.claim_pending_action(aid, **kwargs) is False
    assert approvals.requeue_interrupted_action(aid, entry_state="outcome_unproven", note="same process", **kwargs) is False
    approvals.relinquish_claim(aid)
    assert _row(path, aid)["status"] == "executing", "dropping the handle writes nothing"
    assert approvals.claim_pending_action(aid, **kwargs) is False
    assert approvals.requeue_interrupted_action(aid, entry_state="outcome_unproven", note="owner gone", **kwargs) == "pending_approval"
    assert approvals.claim_pending_action(aid, **kwargs) is True
    approvals.relinquish_claim(aid)


def test_revision4_executing_row_without_owner_record_recovers_as_crossed(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Kitchen deep clean", status="executing")
    kwargs = _fns(path)
    assert approvals.requeue_interrupted_action(aid, entry_state="outcome_unproven", note="revision-4 row", **kwargs) == "outcome_unproven"
    stored = json.loads(_row(path, aid)["result_json"])
    assert stored["interrupted_attempts"] == ["revision-4 row"]
    assert stored["operation"]["phase"] == "dispatching" and stored["operation"]["owner"] is None


def test_claim_the_store_no_longer_records_cannot_write_its_receipt(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Night shift briefing")
    kwargs = _fns(path)
    assert approvals.claim_pending_action(aid, **kwargs)
    # The store moved on without this worker, as a recovery elsewhere would have recorded it.
    with _connection(path) as conn:
        result = json.loads(conn.execute("SELECT result_json FROM operator_action_requests WHERE action_id = ?", (aid,)).fetchone()[0])
        result["operation"]["owner"] = None
        conn.execute("UPDATE operator_action_requests SET status = 'pending_approval', result_json = ? WHERE action_id = ?", (json.dumps(result), aid))
    before = _row(path, aid)
    with pytest.raises(approvals.OperationOwnershipError):
        approvals.mark_action_executed(aid, result={"uid": "late-receipt"}, **kwargs)
    with pytest.raises(approvals.OperationOwnershipError):
        approvals.mark_action_effect_unrecorded(aid, result={"uid": "late-receipt"}, **kwargs)
    after = _row(path, aid)
    assert (after["status"], after["result_json"]) == (before["status"], before["result_json"])
    approvals.relinquish_claim(aid)


def test_process_holding_no_claim_cannot_write_to_an_operation_owned_elsewhere(tmp_path):
    path, aid, _config = _stage(tmp_path, title="Ferry crew change")
    kwargs = _fns(path)
    # Another worker owns the operation right now; this process holds no claim at all.
    owned = {"operation": {"phase": "dispatching", "fence": 3, "evidence": {},
                           "owner": {"token": "owner-in-another-process", "fence": 3, "entry_status": "pending_approval"}}}
    with _connection(path) as conn:
        conn.execute("UPDATE operator_action_requests SET status = 'executing', result_json = ? WHERE action_id = ?", (json.dumps(owned), aid))
    assert approvals.held_claim(aid) is None
    before = _row(path, aid)
    for mark in (approvals.mark_action_executed, approvals.mark_action_outcome_unproven, approvals.mark_action_effect_unrecorded):
        with pytest.raises(approvals.OperationOwnershipError):
            mark(aid, result={"uid": "not-mine", "reason": "foreign write"}, **kwargs)
    after = _row(path, aid)
    assert (after["status"], after["result_json"]) == (before["status"], before["result_json"])
