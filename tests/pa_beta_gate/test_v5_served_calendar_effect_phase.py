"""pa_beta_gate -- revision-5 NOVEL cases through the production operator dispatch: accepted but
unverified creates, definitive write refusals, and a worker killed mid-write.

Every approval goes through ``dispatch_operator_action`` (real parser, gate, approval store, claim
and executor) against the disposable CalDAV service. LABELLED: a local protocol fixture, not a
live account; the killed worker is a real separate process.
"""
from __future__ import annotations

import json
import multiprocessing
import subprocess
import sys
import time

import pytest

from tests.pa_beta_gate.test_calendar_provider_vertical import (  # noqa: F401 -- vilnius_env is a fixture, requested via getfixturevalue
    _provider_events,
    _run,
    vilnius_env,
)

pytestmark = [pytest.mark.pa_beta]


def _row(session: str, action_id: str) -> dict:
    from core.operator.calendar_provider import load_action_any_state

    return load_action_any_state(session_id=session, action_kind="provider_calendar_event", action_id=action_id)


def _titled(state, title: str) -> list[str]:
    return [uid for uid, row in _provider_events(state).items() if row["summary"] == title]


def test_served_put_accepted_then_read_back_throttled_keeps_uid_and_verifies_on_reapproval(request):
    session = "served-accepted-unverified"
    state = request.getfixturevalue("vilnius_env")["state"]
    _intent, proposal = _run('propose "Novel boiler bleed" on 2026-09-16 09:30 Europe/Berlin for 45m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    action_id = proposal.details["action_id"]
    intent_uid = json.loads(_row(session, action_id)["scope_json"])["intent_uid"]
    state.request_count = 0
    state.rate_limit_after = 2  # the availability REPORT and the PUT are answered; the read-back is throttled
    try:
        _intent, accepted = _run(f"approve calendar {action_id}", session_id=session)
    finally:
        state.rate_limit_after = 0
    assert accepted.status == "outcome_unproven" and "NOT reported as never created" in accepted.response_text, accepted.response_text
    row = _row(session, action_id)
    assert row["status"] == "outcome_unproven" and json.loads(row["result_json"])["provider_uid"] == intent_uid
    assert _titled(state, "Novel boiler bleed") == [intent_uid], "the PUT verifiably landed"

    _intent, verified = _run(f"approve calendar {action_id}", session_id=session)
    assert verified.ok and verified.details["recovered"] is True and verified.details["uid"] == intent_uid, verified.response_text
    assert _titled(state, "Novel boiler bleed") == [intent_uid] and _row(session, action_id)["status"] == "executed"


def test_served_definitive_write_refusal_returns_to_pending_and_reapproval_creates_once(request):
    session = "served-definitive-refusal"
    state = request.getfixturevalue("vilnius_env")["state"]
    _intent, proposal = _run('propose "Novel gate code change" on 2026-09-16 11:00 Europe/Berlin for 20m', session_id=session)
    action_id = proposal.details["action_id"]
    state.refuse_writes_with = 403
    try:
        _intent, refused = _run(f"approve calendar {action_id}", session_id=session)
    finally:
        state.refuse_writes_with = 0
    assert refused.status == "provider_refused", refused.response_text
    assert _row(session, action_id)["status"] == "pending_approval" and _titled(state, "Novel gate code change") == []

    _intent, created = _run(f"approve calendar {action_id}", session_id=session)
    assert created.ok and created.details["recovered"] is False, created.response_text
    assert len(_titled(state, "Novel gate code change")) == 1 and _row(session, action_id)["status"] == "executed"


_FRESH_ROW = (
    "import json, sqlite3, sys\n"
    "conn = sqlite3.connect(sys.argv[1])\n"
    "row = conn.execute('SELECT status, result_json FROM operator_action_requests WHERE action_id = ?', (sys.argv[2],)).fetchone()\n"
    "print(json.dumps({'status': row[0], 'result': json.loads(row[1] or '{}')}))\n"
)


def _fresh_row(store_path: str, action_id: str) -> dict:
    """The durable row as a NEW process reads it -- exactly what a restarted runtime sees.

    Measured in this lane: the pytest parent accumulates many long-lived connections to the shared
    session database (opened elsewhere in the run). After another process committed, a new
    in-process connection in the parent still read the old row while a fresh process saw the commit
    (evidence fix-phase-08), and running this cross-process scenario on that shared database left it
    malformed for later tests (controlled pair diag-02 / diag-03). The scenario therefore runs on its
    own dedicated store, and durable state is read from a fresh process.
    """
    completed = subprocess.run([sys.executable, "-c", _FRESH_ROW, store_path, action_id], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


def _approve_in_fresh_process(session: str, action_id: str, store_path: str, result_path: str) -> None:
    # The same dedicated store as the parent. Both paths are process-local overrides that a spawned
    # process does not inherit, so a fresh process sets them the way one runtime is configured.
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import configure_default_db_path

    configure_default_db_path(store_path)
    configure_runtime_continuity_db_path(store_path)
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    result = dispatch_operator_action(parse_operator_action_intent(f"approve calendar {action_id}"), task_id="served-approval", session_id=session)
    with open(result_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"ok": result.ok, "status": result.status, "details": result.details, "response_text": result.response_text}, default=str))


def test_served_worker_killed_after_the_provider_applied_the_write_is_recovered_by_a_restarted_runtime(request):
    from core import runtime_continuity
    from core.user_preferences import save_user_timezone
    from storage.db import active_default_db_path, configure_default_db_path
    from storage.migrations import run_migrations

    session = "served-killed-worker"
    state = request.getfixturevalue("vilnius_env")["state"]
    tmp_path = request.getfixturevalue("tmp_path")
    previous_store = active_default_db_path()
    previous_runtime_store = runtime_continuity._DB_PATH_OVERRIDE
    store_path = str(tmp_path / "dedicated-store" / "operator.db")
    configure_default_db_path(store_path)
    runtime_continuity.configure_runtime_continuity_db_path(store_path)
    try:
        run_migrations()
        assert save_user_timezone("Europe/Berlin")
        _served_killed_worker_scenario(session, state, tmp_path, active_default_db_path())
    finally:
        configure_default_db_path(previous_store)
        runtime_continuity.configure_runtime_continuity_db_path(previous_runtime_store)


def _served_killed_worker_scenario(session, state, tmp_path, store_path):
    title = "Novel sprinkler valve test"
    _intent, proposal = _run(f'propose "{title}" on 2026-09-16 14:00 Europe/Berlin for 30m', session_id=session)
    assert proposal.status == "approval_required", proposal.response_text
    action_id = proposal.details["action_id"]
    ctx = multiprocessing.get_context("spawn")
    state.hang_seconds = 30.0  # the fixture applies the PUT, then withholds the reply
    worker = ctx.Process(target=_approve_in_fresh_process, args=(session, action_id, store_path, str(tmp_path / "killed-worker.json")))
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not _titled(state, title) and time.monotonic() < deadline and worker.is_alive():
            time.sleep(0.05)
        assert _titled(state, title), f"the worker never reached the provider write (exit={worker.exitcode})"
        during = _fresh_row(store_path, action_id)
        assert (during["status"], during["result"]["operation"]["phase"]) == ("executing", "dispatching"), during
        worker.kill()
        worker.join(10)
    finally:
        state.hang_seconds = 0.0
        if worker.is_alive():
            worker.kill()
            worker.join(10)
    assert worker.exitcode is not None and worker.exitcode < 0, worker.exitcode

    result_path = tmp_path / "restarted-runtime.json"
    restarted = ctx.Process(target=_approve_in_fresh_process, args=(session, action_id, store_path, str(result_path)))
    restarted.start()
    restarted.join(60)
    assert restarted.exitcode == 0, restarted.exitcode
    recovered = json.loads(result_path.read_text(encoding="utf-8"))
    assert recovered["ok"] and recovered["details"]["recovered"] is True, recovered
    assert "nothing was duplicated" in recovered["response_text"]
    assert len(_titled(state, title)) == 1
    final = _fresh_row(store_path, action_id)
    assert final["status"] == "executed", final
    assert "owner_gone:outcome_unproven" in [entry["event"] for entry in final["result"]["operation"]["history"]]
