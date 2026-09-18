"""R1d — a planned turn's children are real, concurrent-safe, and recoverable.

WHAT THIS FILE PINS
-------------------
R1c put every attempt of a turn on one chain. Four operational truths were still wrong
underneath that shape:

* a RESUMED turn re-interpreted its restored request through the persisting intake, so
  one external turn wrote TWO user dialogue rows — the canonical one and a second one
  nobody addresses;
* the planner admitted a request as servable and then `planned_subturn` made the
  live-data lane refuse it, so a planned sub-request could not reach the lane that would
  have answered it — the planner promised what the runtime then declined;
* `ux_one_live_attempt` keyed executing exclusivity on the chain root alone, so two
  DISTINCT planner tasks of one turn could not be live at once: `run_plan` runs a wave
  concurrently by default, and the second child's claim was refused by the database;
* a follow-up resolved ONE answer-bearing row, so a turn answered by several children
  could only ever recall the entities of whichever child was selected.

THE SEAMS THESE TESTS USE, AND WHY
----------------------------------
Two things are stood in for, both named:

1. `plan_turn`'s SPLIT is stated instead of asked of a planner model — the split is the
   one step of the planner that needs a provider. Everything below it is real:
   `run_plan`, `build_planner_run_one`, a real `_run_once_inner` sub-turn, the real
   live-data lane, real attempt rows, the real merge.
2. The live-data lane is asked to decline the PARENT message only. Measured while
   writing this: that lane claims every message carrying a live entity — including
   "explain how a hash table works and tell me the weather in Kaunas" — so a message
   whose PARTS are live-data is never offered to the planner in the current ladder. The
   seam constructs the one condition under which it is: the parent unclaimed, the parts
   still live. Sub-turns run through the unpatched lane.
"""
from __future__ import annotations

import threading
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from core.turn_contract import TURN_REQUEST_KEY
from tests.test_turn_attempt_chain import _INCIDENT, _Harness, _incident_mocks

_PARENT = "market prices for gold and bitcoin plus weather in Kaunas"
_TASKS = ("market prices for gold and bitcoin", "weather in Kaunas")
_ENTITY_QUESTION = "Which assets and cities did I originally ask for?"


def _planner_seams(tasks: tuple[str, ...] = _TASKS):
    """The two named stand-ins (see the module docstring), as one context manager."""
    import contextlib

    from core.agent_runtime.turn_planner import PlannedTask

    real_live_data = VoolAgent._maybe_answer_live_data_turn

    def _decline_the_parent(self, *, effective_input, **kwargs):
        if str(effective_input or "").strip() == _PARENT:
            return None
        return real_live_data(self, effective_input=effective_input, **kwargs)

    def _stated_split(_text, **_kwargs):
        return [PlannedTask(index=i, request=text) for i, text in enumerate(tasks)]

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(VoolAgent, "_maybe_answer_live_data_turn", _decline_the_parent))
    stack.enter_context(mock.patch("core.agent_runtime.turn_planner.plan_turn", side_effect=_stated_split))
    return stack


def _planned_turn(harness: _Harness, *, session_id: str | None = None) -> tuple[dict, dict]:
    with _planner_seams():
        return harness.turn(_PARENT, session_id=session_id)


def _children(harness: _Harness, context: dict, *, session_id: str | None = None) -> list[dict]:
    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    return [
        a for a in harness.attempts(session_id)
        if str(a["attempt_id"]) != door
    ]


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1d-{request.node.name[:40]}")
    try:
        yield h
    finally:
        h.close()


# ------------------------------------------------------------------ 1. one user turn, one row


def test_a_resumed_turn_still_persists_one_user_dialogue_row(harness):
    """One external turn is one user turn in the conversation's memory. A resume
    re-interprets its restored request — internal work — and must not file that
    re-reading as a second thing the user said."""
    from storage.dialogue_memory import recent_dialogue_turns

    resumed = {
        "state": "resumed",
        "effective_input": "weather in Kaunas",
        "source_context": {
            "surface": "openclaw",
            "platform": "openclaw",
            "runtime_checkpoint_id": "ckpt-r1d-resume",
        },
    }
    with mock.patch.object(harness.agent, "_prepare_runtime_checkpoint", return_value=resumed):
        with _incident_mocks():
            _result, context = harness.turn("continue", session_id=harness.session_id)

    request = context[TURN_REQUEST_KEY]
    user_rows = [
        row for row in recent_dialogue_turns(harness.session_id, limit=16)
        if str(row.get("speaker_role") or "user") == "user"
    ]
    ids = [str(row.get("turn_id") or "") for row in user_rows]
    assert ids == [request.turn_id], (
        f"one external turn wrote {len(ids)} user dialogue rows ({ids}) — the resume's "
        f"re-interpretation was filed as a second user turn beside {request.turn_id!r}"
    )


# ------------------------------------------------------------------ 2. the planner reaches the lane


def test_a_real_planned_turn_reaches_live_data_execution(harness):
    """The planner admitted both requests as servable; the runtime must then let the
    lane that serves them run. Driven end to end: the merged answer carries both parts'
    data, and each task left a typed child attempt on the turn's chain."""
    with _incident_mocks():
        result, context = _planned_turn(harness, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    for token in ("Gold", "Bitcoin", "Kaunas"):
        assert token in answer, (
            f"the planned turn did not serve {token!r} — a planned sub-request the planner "
            f"called servable never reached the live-data lane: {answer!r}"
        )

    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")
    children = _children(harness, context)
    roles = sorted(str(c["attempt_role"]) for c in children)
    assert roles == ["planner_task", "planner_task"], (
        f"the planned turn's tasks left {roles} on the chain, not one typed child each"
    )
    for child in children:
        assert str(child["root_attempt_id"]) == door
        assert str(child["session_id"]) == context[TURN_REQUEST_KEY].session_id


# ------------------------------------------------------------------ 3. concurrency identity


def test_two_distinct_planner_tasks_execute_concurrently(harness):
    """`run_plan` runs an independent wave concurrently by default. Two DISTINCT tasks of
    one turn must both be able to hold a live claim — exclusivity is per task, not per
    chain — and both must leave their own child attempt."""
    from core.agent_runtime.turn_planner import PlannedTask, run_plan
    from core.agent_runtime.turn_planner_hook import build_planner_run_one

    with _incident_mocks():
        _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    request = context[TURN_REQUEST_KEY]
    before = {str(a["attempt_id"]) for a in harness.attempts()}

    # Both tasks are held AFTER their claim to RUNNING and before either finishes, so the
    # two live claims genuinely overlap in the window executing exclusivity indexes.
    # Holding them at creation would prove nothing: a fresh row is RECEIVED, which the
    # index does not cover — measured, by watching this test stay green under the sabotage
    # that removes the slot from the key.
    barrier = threading.Barrier(2, timeout=20)
    real_claim = VoolAgent._record_live_data_plan_subtasks

    def _hold_while_running(self, **kwargs):
        real_claim(self, **kwargs)
        barrier.wait()

    tasks = [PlannedTask(index=i, request=text) for i, text in enumerate(_TASKS)]
    with _incident_mocks():
        with mock.patch.object(VoolAgent, "_record_live_data_plan_subtasks", _hold_while_running):
            outcomes = run_plan(
                tasks,
                run_one=build_planner_run_one(
                    harness.agent, session_id=request.session_id, source_context=context
                ),
                max_workers=2,
            )

    errors = [o.error for o in outcomes if o.error]
    assert not errors, f"a concurrent planner task failed: {errors}"

    # The discriminating assertions are what each task SERVED and what its row reached.
    # Counting rows proves nothing: a refused claim still leaves the RECEIVED row it was
    # refused on, and the lane then answers "could not be retrieved" — measured, by
    # watching an earlier version of this test stay green under the sabotage that drops
    # the slot from the exclusivity key.
    served = {o.task.index: str(o.answer or "") for o in outcomes}
    assert "Gold" in served[0] and "Bitcoin" in served[0], (
        f"the market task did not serve its data while running beside its sibling: {served[0]!r}"
    )
    assert "Kaunas" in served[1], (
        f"the weather task did not serve its data while running beside its sibling: {served[1]!r}"
    )

    children = [a for a in harness.attempts() if str(a["attempt_id"]) not in before]
    assert len(children) == 2, (
        f"two distinct concurrent tasks left {len(children)} child attempts"
    )
    states = sorted(str(c["lifecycle_state"]) for c in children)
    assert states == ["SUCCEEDED", "SUCCEEDED"], (
        f"a concurrent task never got its live claim (states {states}) — exclusivity is "
        "keyed on the chain alone, so the second task of the wave was refused"
    )
    slots = {str(c["execution_slot"]) for c in children}
    assert len(slots) == 2, f"the two tasks share one execution slot: {slots}"


def test_the_same_planner_task_cannot_take_two_live_claims(harness):
    """Exclusivity is not weakened by making it per task: the SAME task of the same turn
    still cannot be executing twice."""
    import sqlite3

    from storage.db import get_connection

    with _incident_mocks():
        _result, context = harness.turn(_INCIDENT, session_id=harness.session_id)
    door = str((context.get("_execution_identity") or {}).get("attempt_id") or "")

    conn = get_connection()
    try:
        def _insert(attempt_id: str, slot: str) -> None:
            conn.execute(
                """
                INSERT INTO runtime_attempts (
                    attempt_id, session_id, execution_id, attempt_role, execution_slot,
                    lifecycle_state, created_at, updated_at
                ) VALUES (?, ?, ?, 'planner_task', ?, 'RUNNING',
                          '2026-08-31T00:00:00Z', '2026-08-31T00:00:00Z')
                """,
                (attempt_id, harness.session_id, door, slot),
            )
            conn.commit()

        _insert("r1d-task-a", "task:0:aaaa")
        _insert("r1d-task-b", "task:1:bbbb")  # a different task may be live at the same time
        with pytest.raises(sqlite3.IntegrityError):
            _insert("r1d-task-a-again", "task:0:aaaa")
    finally:
        conn.close()


# ------------------------------------------------------------------ 4. chain-level recall


def test_a_followup_recovers_every_entity_of_a_multi_child_turn(harness):
    """The turn answered through several children; the follow-up must recall what the
    TURN was asked, not what one of its children happened to hold."""
    with _incident_mocks():
        _result, _context = _planned_turn(harness, session_id=harness.session_id)

    answer = harness.turn(_ENTITY_QUESTION, session_id=harness.session_id, mocked=False)[0]["response"]
    for token in ("gold", "bitcoin", "kaunas"):
        assert token in answer.lower(), (
            f"the follow-up lost {token!r} — it recalled one child of the turn instead of "
            f"the whole chain: {answer!r}"
        )


# ------------------------------------------------------------------ 5. surface parity


def test_http_and_in_process_planned_turns_agree():
    """The same planned message must produce the same served truth and the same chain
    shape whether or not the caller passes a session override."""
    seen = {}
    for label, session in (("http", "sess-r1d-parity-http"), ("in_process", None)):
        h = _Harness("sess-r1d-parity-http")
        try:
            with _incident_mocks():
                result, context = _planned_turn(h, session_id=session)
            request = context[TURN_REQUEST_KEY]
            answer = str(result.get("response") or "")
            children = _children(h, context, session_id=request.session_id)
            seen[label] = {
                "entities": sorted(t for t in ("Gold", "Bitcoin", "Kaunas") if t in answer),
                "roles": sorted(str(c["attempt_role"]) for c in children),
                "one_root": len({str(c["root_attempt_id"]) for c in children}) <= 1,
                "sessions_match": {str(c["session_id"]) for c in children} <= {request.session_id},
            }
        finally:
            h.close()

    assert seen["http"]["entities"] == ["Bitcoin", "Gold", "Kaunas"], seen["http"]
    assert seen["http"] == seen["in_process"], (
        f"HTTP and in-process planned turns disagree: {seen['http']} vs {seen['in_process']}"
    )
