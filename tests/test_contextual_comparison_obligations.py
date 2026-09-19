"""Contextual selections require execution, not automatic formatting credit."""
import pytest

from core.agent_runtime.agent import _record_conductor_demand_receipts
from core.agent_runtime.answer_coverage import demand_units, interpret_request, units_without_own_object
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.scheduler import run_conductor_plan
from core.live_data_plan import SubtaskLifecycle, SubtaskOutcome
from tests.conductor_product import compose_product
from tests.test_answer_integrity_incidents import _mint, _sweep, fresh_store

OWNER = "what is weather in warsaw and moscow? which one is warmer?"
NOVEL = "Get the ETH and ADA market changes. Which is the largest mover?"


@pytest.mark.parametrize("text,question", [
    (OWNER, "which one is warmer?"),
    (NOVEL, "Which is the largest mover?"),
    ("What are the BTC and SOL prices? Which one moved most?", "Which one moved most?"),
])
def test_selection_is_requested_work_not_a_format_rider(text, question):
    unit = next(u for u in interpret_request(text).units if u.text == question)
    assert unit.kind == "request"
    assert unit in demand_units(text)
    assert unit.unit_id not in units_without_own_object(text)


@pytest.mark.parametrize("instruction", ["Show your math.", "Give me a summary."])
def test_delivery_instructions_remain_constraints(instruction):
    unit = next(u for u in interpret_request("Calculate 900 minus 120. " + instruction).units
                if u.text == instruction)
    assert unit.kind == "constraint"
    assert unit.depends_on


@pytest.mark.parametrize("text,first,question,operation,metric,readings,winner", [
    (OWNER, "what is weather in warsaw and moscow?", "which one is warmer?", "weather_lookup",
     "temperature_c", {"warsaw": 26.0, "moscow": 19.0}, "Warsaw"),
    (NOVEL, "Get the ETH and ADA market changes.", "Which is the largest mover?", "market_quote",
     "change_24h_pct", {"ethereum": 3.0, "cardano": -12.0}, "Cardano"),
])
@pytest.mark.parametrize("execute_comparison", [False, True], ids=["lookup-only", "computed"])
@pytest.mark.usefixtures("fresh_store")
def test_only_executed_comparison_can_discharge_the_question(
    monkeypatch, text, first, question, operation, metric, readings, winner, execute_comparison,
):
    def observe(task, **kwargs):
        key = str(task.arguments.get("asset_key") or task.entity).lower()
        value = readings[key]
        return SubtaskOutcome(subtask=task, state=SubtaskLifecycle.SUCCEEDED,
            result={metric: value, "condition": "Clear", "price": 1.0, "currency": "USD",
                    "source": "controlled-observation"})

    target = "_run_weather_subtask" if operation == "weather_lookup" else "_run_market_subtask"
    monkeypatch.setattr("core.agent_runtime.live_data_runner." + target, observe)
    _mint(text)
    clauses = [ProposedClause(0, first, operation, ())]
    if execute_comparison:
        clauses.append(ProposedClause(1, question, "comparison", (0,)))
    plan = build_plan_from_clauses(clauses, original_request=text, plan_id="contextual-comparison")
    outcomes = run_conductor_plan(plan)
    lookups = [o for o in outcomes if o.node.operation == operation]
    assert len(lookups) == 2 and all(o.fulfilled for o in lookups)
    answer = compose_product(plan, outcomes)
    if execute_comparison:
        comparison = next(o for o in outcomes if o.node.operation == "comparison")
        assert sorted(comparison.result["considered"].values()) == sorted(readings.values())
        assert winner in answer.text
        assert comparison.result["winner_value"] == readings[winner.lower()]
        assert answer.decision.disposition.value == "fulfilled"
    _record_conductor_demand_receipts(plan, outcomes, request=text)
    rows, _ = _sweep(answer.text)
    unit = next(u for u in demand_units(text) if u.text == question)
    # This helper records successful consumption, not the dispatch census. Without
    # comparison execution it proves no fulfillment, not a historical non-dispatch.
    assert rows[unit.unit_id]["state"] == ("satisfied" if execute_comparison else "indeterminate")
    if not execute_comparison:
        assert answer.decision.disposition.value != "fulfilled"
        assert question in answer.text
