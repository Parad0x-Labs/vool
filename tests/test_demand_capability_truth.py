"""CAPABILITY TRUTH — the ledger names what actually served each demand.

THE CONTRADICTION
-----------------
The served proof recorded the interpretation demand as

    u3 | capability=unsupported | dispatch=SUCCEEDED | receipt=1

Three statements that cannot all be true. Something answered it — the model lane
did, on route `model_minimal:qwen2.5:7b` — so "unsupported" was not a fact about
the demand, it was the pre-dispatch coverage reading leaking into a post-execution
record. `unsupported` answers "does a deterministic lane CLAIM this?"; a discharge
ledger is answering "what RAN this?", and those are different questions.

THE REPAIR, at the owning registry
----------------------------------
`core.lane_registry` already resolved a served route to a lane
(`finalization_family`). It now also resolves a lane to the capability it PROVIDES
when it executes (`executed_capability`), which is a new `LaneSpec.executes` field
declared only where a lane's claim and its execution differ — the model lane, whose
coverage is `fallback_plan` (it deliberately claims no unit, or every mixed turn
would read as covered by one lane) and whose execution is `model_reasoning`.

The sub-turn's route reaches the ledger as a FACT it stamped on itself, carried on
`TURN_DEMAND_EXECUTORS_KEY`. The parent never infers an executor; inferring is how
the contradiction was written in the first place.
"""
from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from core.agent_runtime.answer_coverage import COVERAGE_CONTEXT_KEY
from core.agent_runtime.demand_ownership import (
    CAPABILITY_UNATTRIBUTED,
    CAPABILITY_UNSUPPORTED,
    DEMAND_EXECUTED,
    DEMAND_FAILED,
    DEMAND_PENDING_APPROVAL,
    DemandRecord,
    executor_capability,
)
from core.lane_registry import (
    CAPABILITY_MODEL_REASONING,
    executed_capability,
    find_spec,
)
from core.turn_contract import TURN_DEMAND_EXECUTORS_KEY, TURN_DEMAND_LEDGER_KEY
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness

_LINE_ONE = "ALPHA-LINE-ONE the ledger was reconciled"
_LINE_TWO = "BRAVO-LINE-TWO she jogged to the market"
_MIXED = (
    "Return exactly the second line of notes.txt. "
    "Calculate 37 x 19. "
    "Decide whether that line describes walking."
)
_NOTICE = "`qwen2.5:7b` was blocked by the local memory-safety admission gate."


def _model_stand_in(agent, text: str = "JUDGEMENT: jogging is running, not walking."):
    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="cap", provider_id="ollama-local:qwen2.5:7b",
        provider_name="Ollama local", model_name="qwen2.5:7b", output_text=text,
        confidence=0.9, trust_score=0.9, used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(agent.memory_router, "resolve", return_value=decision))
    stack.enter_context(mock.patch("core.agent_runtime.agent.render_response", return_value=text))
    return stack


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "notes.txt").write_text(f"{_LINE_ONE}\n{_LINE_TWO}\n", encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-captruth-{request.node.name[:36]}")
    try:
        yield h
    finally:
        h.close()


def _turn(harness, workspace, *, break_interpretation: bool = False):
    context = dict(_SOURCE_CONTEXT)
    context["workspace"] = workspace
    real_inner = type(harness.agent)._run_once_inner

    def spy(self, text, *, session_id_override=None, source_context=None, turn_request=None, **kw):
        result = real_inner(
            self, text, session_id_override=session_id_override,
            source_context=source_context, turn_request=turn_request, **kw
        )
        if (
            break_interpretation
            and isinstance(result, dict)
            and (source_context or {}).get("planned_subturn")
            and "describes walking" in text
        ):
            # Exactly the production shape: the runtime's own typed marker plus a
            # notice, on the model lane's own route.
            source_context["runtime_notice_not_an_answer"] = True
            result = dict(result)
            result["response"] = _NOTICE
            result["route"] = "model_minimal:qwen2.5:7b"
            result["route_reason"] = "chat_model_unavailable_degraded"
        return result

    with mock.patch.object(type(harness.agent), "_run_once_inner", spy), _model_stand_in(harness.agent):
        answer = harness.agent.run_once(
            _MIXED, source_context=context, session_id_override=harness.session_id
        )
    return str(answer.get("response") or ""), context


def _ledger(context):
    return {row["demand_id"]: row for row in (context.get(TURN_DEMAND_LEDGER_KEY) or [])}


def _dispatches(context):
    record = context.get(COVERAGE_CONTEXT_KEY) or {}
    return {
        next(iter(item["unit_ids"])): item
        for item in (record.get("dispatches") or [])
        if str(item.get("subtask_id") or "").startswith("demand:")
    }


def _receipts(context):
    record = context.get(COVERAGE_CONTEXT_KEY) or {}
    return {
        next(iter(item.get("consumed_units") or ("",)))
        for item in (record.get("answers") or [])
        if item.get("reason") == "demand_owned_mixed_turn"
    }


# ============================================ 1. the registry owns the vocabulary


def test_the_model_lane_declares_what_it_provides_when_it_executes():
    """`coverage` and `executes` answer different questions, and the split is the
    whole repair. The fallback claims no unit — a fallback that claimed every unit
    would make every mixed turn read as covered by one lane and nothing would ever
    decompose — but when it runs one, model reasoning is what ran."""
    spec = find_spec("model_lane")
    assert spec is not None
    assert spec.coverage == "fallback_plan"
    assert executed_capability("model_lane") == CAPABILITY_MODEL_REASONING


def test_a_lane_whose_claim_and_execution_agree_declares_no_second_name():
    """The default must stay silent: only a lane whose two answers DIFFER declares
    `executes`, or the catalog grows a redundant field on every row."""
    for lane_id in ("workspace_read_fast_path", "direct_math_fast_path"):
        spec = find_spec(lane_id)
        assert spec.executes == "", lane_id
        assert executed_capability(lane_id) == spec.coverage, lane_id


@pytest.mark.parametrize(
    "route,reason,expected_lane,expected_capability",
    [
        ("deterministic:workspace_runtime_fast_path", "workspace_runtime_fast_path",
         "workspace_read_fast_path", "workspace_read"),
        ("arithmetic", "pure_arithmetic_expression", "direct_math_fast_path", "arithmetic"),
        ("model_minimal:qwen2.5:7b", "ordinary_plain_text_chat", "model_lane",
         CAPABILITY_MODEL_REASONING),
    ],
)
def test_every_route_a_sub_turn_actually_stamps_resolves(
    route, reason, expected_lane, expected_capability
):
    """The three routes the three demands really run on, taken from a traced turn.
    `arithmetic` was previously unresolvable: the lane records
    reason='direct_math_fast_path' and then overwrites route_reason with
    'pure_arithmetic_expression', so the SERVED fact carried a name the registry
    did not map, and the resolver answered None for a lane that had plainly run."""
    lane, capability = executor_capability({"route": route, "route_reason": reason})
    assert (lane, capability) == (expected_lane, expected_capability)


def test_an_unknown_route_resolves_to_nothing_rather_than_guessing():
    """Fail-closed: an unrecognized route names no lane, so the caller falls back to
    the claim-time reading instead of inventing an executor."""
    assert executor_capability({"route": "who_knows", "route_reason": "nothing"}) == ("", "")
    assert executor_capability(None) == ("", "")
    assert executor_capability({}) == ("", "")


# ================================================ 2. the contradiction is unwritable


@pytest.mark.parametrize("state", [DEMAND_EXECUTED, DEMAND_PENDING_APPROVAL])
def test_a_demand_nothing_supports_can_never_be_recorded_as_served(state):
    with pytest.raises(ValueError, match="cannot have been served"):
        DemandRecord(
            demand_id="u3", request="x", capability=CAPABILITY_UNSUPPORTED,
            lane_id="", attempted=True, terminal_state=state,
        )


def test_an_unsupported_demand_that_failed_is_still_recordable():
    """The falsifiable direction. `unsupported` is a real verdict when nothing ran
    it — the invariant must not delete the honest row along with the dishonest one."""
    row = DemandRecord(
        demand_id="u3", request="x", capability=CAPABILITY_UNSUPPORTED,
        lane_id="", attempted=True, terminal_state=DEMAND_FAILED,
    )
    assert row.terminal_state == DEMAND_FAILED
    assert row.supported is False


def test_an_unnameable_executor_is_named_as_unnameable():
    """"Nobody can serve this" and "somebody served it and we failed to record who"
    are different facts. Collapsing the second into the first is what produced the
    contradiction; collapsing it into a fabricated capability would be worse."""
    from core.agent_runtime.demand_ownership import demand_records
    from core.agent_runtime.turn_planner import PlannedTask, TaskOutcome

    outcome = TaskOutcome(task=PlannedTask(index=2, request="x"), answer="served")
    rows = demand_records(_MIXED, [outcome], {2: {"route": "unknown", "route_reason": ""}})
    assert rows[2].capability == CAPABILITY_UNATTRIBUTED
    assert rows[2].terminal_state == DEMAND_EXECUTED


# ================================================== 3. the served shapes, end to end


def test_a_successful_mixed_turn_records_three_real_capabilities(harness, workspace):
    """Requirement: three non-unsupported capabilities, three successful dispatches
    and three receipts."""
    _answer, context = _turn(harness, workspace)
    ledger, dispatches, receipts = _ledger(context), _dispatches(context), _receipts(context)

    assert {u: row["capability"] for u, row in ledger.items()} == {
        "u1": "workspace_read",
        "u2": "arithmetic",
        "u3": CAPABILITY_MODEL_REASONING,
    }
    assert {u: row["lane_id"] for u, row in ledger.items()} == {
        "u1": "workspace_read_fast_path",
        "u2": "direct_math_fast_path",
        "u3": "model_lane",
    }
    assert all(row["terminal_state"] == DEMAND_EXECUTED for row in ledger.values())
    assert not [r for r in ledger.values() if r["capability"] == CAPABILITY_UNSUPPORTED]
    assert {u: d["state"] for u, d in dispatches.items()} == {
        "u1": "SUCCEEDED", "u2": "SUCCEEDED", "u3": "SUCCEEDED",
    }
    # The dispatch row's operation is the EXECUTED capability, so the durable record
    # and the ledger cannot disagree with each other.
    assert {u: d["operation"] for u, d in dispatches.items()} == {
        "u1": "workspace_read", "u2": "arithmetic", "u3": CAPABILITY_MODEL_REASONING,
    }
    assert receipts == {"u1", "u2", "u3"}


def test_a_forced_model_failure_keeps_the_capability_and_drops_the_receipt(
    harness, workspace
):
    """Requirement: the interpretation capability stays correctly identified, the
    dispatch is FAILED, and no receipt is filed. Identifying the executor and
    succeeding are independent facts — a failure must not erase who was asked."""
    answer, context = _turn(harness, workspace, break_interpretation=True)
    ledger, dispatches, receipts = _ledger(context), _dispatches(context), _receipts(context)

    assert ledger["u3"]["capability"] == CAPABILITY_MODEL_REASONING, ledger["u3"]
    assert ledger["u3"]["lane_id"] == "model_lane", ledger["u3"]
    assert ledger["u3"]["terminal_state"] == DEMAND_FAILED, ledger["u3"]
    assert dispatches["u3"]["state"] == "FAILED"
    assert dispatches["u3"]["operation"] == CAPABILITY_MODEL_REASONING
    assert receipts == {"u1", "u2"}, receipts
    # …and the siblings still reached the reader.
    assert _LINE_TWO in answer and "703" in answer, answer


def test_a_repeated_turn_records_one_dispatch_per_demand(harness, workspace):
    """Requirement: retry produces no duplicate dispatch. Counted from the durable
    record, per turn."""
    for _ in range(2):
        _answer, context = _turn(harness, workspace)
        record = context.get(COVERAGE_CONTEXT_KEY) or {}
        counts: dict[str, int] = {}
        for item in record.get("dispatches") or []:
            if str(item.get("subtask_id") or "").startswith("demand:"):
                unit = next(iter(item["unit_ids"]))
                counts[unit] = counts.get(unit, 0) + 1
        assert counts == {"u1": 1, "u2": 1, "u3": 1}, counts


def test_the_executor_facts_come_from_the_sub_turns_not_the_parent(harness, workspace):
    """The channel itself: one executor fact per task index, each carrying the route
    that sub-turn stamped on itself."""
    _answer, context = _turn(harness, workspace)
    executors = context.get(TURN_DEMAND_EXECUTORS_KEY) or {}
    assert sorted(executors) == [0, 1, 2], executors
    assert executors[0]["route"] == "deterministic:workspace_runtime_fast_path"
    assert executors[1]["route"] == "arithmetic"
    assert executors[2]["route"].startswith("model_minimal:")
