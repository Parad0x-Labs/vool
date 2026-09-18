"""P0 AMENDMENT — the SERVED mixed-demand path.

WHAT THIS FILE PINS, AND WHY IT EXISTS SEPARATELY
-------------------------------------------------
`tests/test_mixed_demand_execution_p0.py` proved the in-process runtime executes
every demand of a mixed turn. Driven over the REAL HTTP surface at that commit
(3ad2ca6c), against a real two-line file, with the local lane `qwen2.5:7b`, the
same message behaved differently — because a different lane claimed it:

    POST /api/chat "Return exactly the second line of notes.txt. Calculate 37 x 19.
                    Decide whether that line describes walking."
      -> "37*19 = 703.\n\nCould not be answered:
          - decide whether that line describes walking — the available answer path
            does not safely match this request
          - Return exactly the second line of notes.txt — …"
         route=conductor_multi_intent_plan
         conductor_plan_created: 3 nodes / conductor_plan_completed: 1/3 succeeded
         obligations: u1 indeterminate, u2 satisfied, u3 indeterminate
         execution_truth: ran_tool=false
         runtime_attempt -> SUCCEEDED

Three defects, each pinned below:

1. CLAIM. `LANE_CATALOG` grants a composite planner an unconditional whole-turn
   claim, on the premise it "owns a COMPLETE unit plan". The conductor's plan did
   not: two of its three nodes were UNRESOLVED at PLAN TIME. It took a turn it had
   already computed it could not finish and preempted the composite that can.
2. DISCHARGE. Once the right lane took the turn and served all three demands, the
   durable ledger still read `u1 indeterminate, u2 satisfied, u3 indeterminate`:
   the lane filed no receipt, so the finalization sweep fell back to reading anchors
   off the prose — and a file read answers with the FILE'S bytes, which contain none
   of the request's words. Real work, rewritten as unaccounted.
3. FALSE SUCCESS. With the local lane made unreachable, the interpretation demand
   produced a runtime NOTICE ("`qwen2.5:7b` was blocked by the local memory-safety
   admission gate … Retry the turn."). A notice is a non-empty string with no error,
   so it read as an executed demand and the turn certified `demand_satisfied: 3,
   unresolved: 0` — a failed demand reported as satisfied.

The served evidence for all of it is under
`validation-logs/mixed-demand-p0-20260901/served/`.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core.agent_runtime.demand_ownership import (
    DEMAND_EXECUTED,
    DEMAND_FAILED,
    demand_records,
    registered_lane_for,
)
from core.conductor.planner import demands_this_plan_cannot_execute
from core.turn_contract import TURN_DEMAND_LEDGER_KEY
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_LINE_ONE = "ALPHA-LINE-ONE the ledger was reconciled"
_LINE_TWO = "BRAVO-LINE-TWO she jogged to the market"
_JUDGEMENT = "JUDGEMENT-STAND-IN: jogging is running, not walking."

_MIXED = (
    "Return exactly the second line of notes.txt. "
    "Calculate 37 x 19. "
    "Decide whether that line describes walking."
)


def _model_stand_in(agent, text: str = _JUDGEMENT):
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="served", provider_id="served",
        provider_name="served", model_name="served", output_text=text,
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(agent.memory_router, "resolve", return_value=decision)
    )
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text(f"{_LINE_ONE}\n{_LINE_TWO}\n", encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-served-{request.node.name[:38]}")
    try:
        yield h
    finally:
        h.close()


# ============================================ 1. the composite planner's claim


class _StubNode:
    def __init__(self, operation: str, span: tuple[int, int]) -> None:
        self.operation = operation
        self.clause_span = span


class _StubPlan:
    def __init__(self, nodes) -> None:
        self.nodes = nodes


def _units(text: str):
    from core.agent_runtime.answer_coverage import demand_units

    return demand_units(text)


def test_a_a_demand_with_no_executable_node_is_visible_before_dispatch():
    """The claim-time fact the conductor already had and did not use. Spans are the
    binding, never wording: each unit is matched to the nodes that overlap it, and a
    unit whose overlapping nodes are ALL unresolved is one the plan can run nothing
    for."""
    from core.conductor.registry import UNRESOLVED_OPERATION

    units = _units(_MIXED)
    u1, u2, u3 = units[0], units[1], units[2]
    plan = _StubPlan(
        [
            _StubNode(UNRESOLVED_OPERATION, (u1.start, u1.end)),
            _StubNode("calculation", (u2.start, u2.end)),
            _StubNode(UNRESOLVED_OPERATION, (u3.start, u3.end)),
        ]
    )
    blocked = demands_this_plan_cannot_execute(plan, units)
    assert [unit_id for unit_id, _text in blocked] == ["u1", "u3"], blocked


def test_a_an_unresolved_node_beside_a_working_one_is_not_a_blocked_demand():
    """THE CONTROL that keeps the conductor's partial-shipping contract intact.

    The conductor deliberately ships a truthful partial rather than falling back, so
    a demand that has an executable node BESIDE an unresolved one is still its work.
    Standing down on any unresolved node would delete that contract — measured: a
    real plan of eight nodes with six unresolved is still the right lane for the two
    it can serve."""
    from core.conductor.registry import UNRESOLVED_OPERATION

    units = _units(_MIXED)
    u1 = units[0]
    plan = _StubPlan(
        [
            _StubNode(UNRESOLVED_OPERATION, (u1.start, u1.end)),
            _StubNode("factual_explanation", (u1.start, u1.end)),
        ]
    )
    assert demands_this_plan_cannot_execute(plan, units[:1]) == ()


def test_a_a_demand_no_node_touches_is_not_reported_here():
    """A unit no node overlaps is the `unclaimed` case the proposal already reports —
    a different fact from 'the plan tried to cover this and cannot'."""
    units = _units(_MIXED)
    assert demands_this_plan_cannot_execute(_StubPlan([]), units) == ()


def test_the_registry_says_who_owns_a_demand_the_conductor_cannot_run():
    """The other half of the law, asked of the CANONICAL REGISTRY — and the
    distinction that keeps it narrow. A demand another lane covers is a handoff; a
    demand nothing covers is NOT, because the conductor's truthful 'this runtime
    cannot do that' is the right answer and a model would fabricate one instead."""
    assert registered_lane_for("Return exactly the second line of notes.txt.") == (
        "workspace_read_fast_path"
    )
    assert registered_lane_for("Calculate 37 x 19.") == "direct_math_fast_path"
    # Nothing covers these — the conductor keeps them and answers honestly.
    assert registered_lane_for("Decide whether that line describes walking.") == ""
    assert registered_lane_for("call my mum") == ""


def test_the_law_is_defensive_about_plan_shape():
    """An object that exposes no nodes yields () — the helper can only ever
    under-report a gap, never invent one that would strip a healthy claim."""
    assert demands_this_plan_cannot_execute(object(), _units(_MIXED)) == ()
    assert demands_this_plan_cannot_execute(_StubPlan([]), ()) == ()


# ================================================= 2. the discharge channel


def test_b_executed_demands_file_lane_attested_receipts(harness, workspace):
    """The served defect: the lane did the work and filed no receipt, so the
    finalization sweep guessed from prose and marked a real file read
    `indeterminate`. Every executed demand must leave a lane-attested receipt and a
    SUCCEEDED dispatch row — the two records the honesty sweep actually reads."""
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY

    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )

    ledger = list(context.get(TURN_DEMAND_LEDGER_KEY) or [])
    assert [row["terminal_state"] for row in ledger] == [DEMAND_EXECUTED] * 3, ledger

    record = context.get(COVERAGE_CONTEXT_KEY) or {}
    dispatches = [
        item
        for item in (record.get("dispatches") or [])
        if str(item.get("subtask_id") or "").startswith("demand:")
    ]
    assert {next(iter(item["unit_ids"])) for item in dispatches} == {"u1", "u2", "u3"}
    assert all(item["state"] == "SUCCEEDED" for item in dispatches), dispatches
    # The capability the REGISTRY selected rides the dispatch row, so the ledger
    # names who was asked to do the work, not just that something happened.
    by_unit = {next(iter(item["unit_ids"])): item["operation"] for item in dispatches}
    assert by_unit == {
        "u1": "workspace_read",
        "u2": "arithmetic",
        # The lane that RAN it, not "unsupported" — the dispatch row is an
        # execution record and must never contradict its own SUCCEEDED state.
        "u3": "model_reasoning",
    }

    answers = [
        item
        for item in (record.get("answers") or [])
        if item.get("reason") == "demand_owned_mixed_turn"
    ]
    assert {next(iter(item.get("consumed_units") or ())) for item in answers} == {
        "u1",
        "u2",
        "u3",
    }


def test_b_one_dispatch_and_one_receipt_per_demand(harness, workspace):
    """No duplicate execution: exactly one dispatch row and one receipt per demand."""
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY

    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with _model_stand_in(harness.agent):
        harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )
    record = context.get(COVERAGE_CONTEXT_KEY) or {}
    counts: dict[str, int] = {}
    for item in record.get("dispatches") or []:
        if str(item.get("subtask_id") or "").startswith("demand:"):
            counts[next(iter(item["unit_ids"]))] = counts.get(next(iter(item["unit_ids"])), 0) + 1
    assert counts == {"u1": 1, "u2": 1, "u3": 1}, counts


# ================================================ 3. a notice is not an answer


def test_c_a_runtime_notice_is_not_an_executed_demand(harness, workspace):
    """THE false-success defect, at its seam.

    `turn_reasoning` stamps `runtime_notice_not_an_answer` on a turn that produced a
    runtime notice instead of an answer. Returned from the planner hook it is a
    non-empty string with no error, so the demand reads EXECUTED and a failed demand
    is certified satisfied. The hook must raise on the runtime's own typed marker —
    never on the notice's wording.
    """
    from core.agent_runtime import turn_planner_hook
    from core.agent_runtime.turn_planner import PlannedTask

    notice = "`some-model` was blocked by the local memory-safety admission gate."

    def _fake_inner(text, *, session_id_override=None, source_context=None, turn_request=None):
        # Exactly what the production producer does: mark the CONTEXT, return prose.
        if isinstance(source_context, dict):
            source_context["runtime_notice_not_an_answer"] = True
        return {"response": notice}

    run_one = turn_planner_hook.build_planner_run_one(
        harness.agent, session_id=harness.session_id, source_context=dict(_SOURCE_CONTEXT)
    )
    with mock.patch.object(harness.agent, "_run_once_inner", _fake_inner):
        with pytest.raises(RuntimeError) as excinfo:
            run_one(PlannedTask(index=0, request="Decide whether that line describes walking."), {})
    assert notice in str(excinfo.value)


def test_c_an_ordinary_answer_is_still_returned(harness):
    """The control. Without the marker the same path returns the text — the rule
    must cost a healthy sub-turn nothing."""
    from core.agent_runtime import turn_planner_hook
    from core.agent_runtime.turn_planner import PlannedTask

    def _fake_inner(text, *, session_id_override=None, source_context=None, turn_request=None):
        return {"response": "an ordinary answer"}

    run_one = turn_planner_hook.build_planner_run_one(
        harness.agent, session_id=harness.session_id, source_context=dict(_SOURCE_CONTEXT)
    )
    with mock.patch.object(harness.agent, "_run_once_inner", _fake_inner):
        assert run_one(PlannedTask(index=0, request="anything"), {}) == "an ordinary answer"


# ============================== 4. a failed demand is PARTIAL, never SUCCEEDED


def test_d_a_failed_demand_is_recorded_failed_and_its_siblings_survive(
    harness, workspace
):
    """Item 8, at the ledger. One demand's sub-turn fails; the other two must still
    execute and stay visible, the failed one must be typed FAILED (never quietly
    'executed'), and its dispatch row must carry the real cause so the finalization
    sweep can prove `unanswered` rather than shrug `indeterminate`."""
    from core.agent_runtime import turn_planner_hook
    from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY

    real = turn_planner_hook.build_planner_run_one

    def failing(agent, **kwargs):
        inner = real(agent, **kwargs)

        def wrapped(task, done):
            if "describes walking" in str(getattr(task, "request", "")):
                raise RuntimeError("FORCED: the interpretation lane is unavailable")
            return inner(task, done)

        return wrapped

    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    with mock.patch.object(
        turn_planner_hook, "build_planner_run_one", side_effect=failing
    ), _model_stand_in(harness.agent):
        result = harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )

    answer = str(result.get("response") or "")
    assert _LINE_TWO in answer, f"the file read vanished when a sibling failed: {answer!r}"
    assert "703" in answer, f"the arithmetic vanished when a sibling failed: {answer!r}"
    assert "describes walking" in answer, (
        f"the failed demand was not named in the answer: {answer!r}"
    )

    ledger = {row["demand_id"]: row for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])}
    assert ledger["u1"]["terminal_state"] == DEMAND_EXECUTED
    assert ledger["u2"]["terminal_state"] == DEMAND_EXECUTED
    assert ledger["u3"]["terminal_state"] == DEMAND_FAILED, ledger["u3"]
    assert ledger["u3"]["attempted"] is True

    record = context.get(COVERAGE_CONTEXT_KEY) or {}
    by_unit = {
        next(iter(item["unit_ids"])): item
        for item in (record.get("dispatches") or [])
        if str(item.get("subtask_id") or "").startswith("demand:")
    }
    assert by_unit["u1"]["state"] == "SUCCEEDED"
    assert by_unit["u2"]["state"] == "SUCCEEDED"
    assert by_unit["u3"]["state"] == "FAILED"
    assert "FORCED" in by_unit["u3"]["failure_reason"]
    # A failed demand files NO lane-attested receipt — filing one would buy a
    # quieter ledger by hiding the failure, which is the defect inverted.
    served = {
        next(iter(item.get("consumed_units") or ("",)))
        for item in (record.get("answers") or [])
        if item.get("reason") == "demand_owned_mixed_turn"
    }
    assert served == {"u1", "u2"}, served


def test_d_records_never_claim_an_unattempted_demand_ran():
    """The ledger's own law, restated for the served path: a row cannot say a
    demand executed unless an execution was attempted."""
    rows = demand_records(_MIXED)
    assert all(not row.attempted for row in rows)
    assert {row.demand_id for row in rows} == {"u1", "u2", "u3"}
