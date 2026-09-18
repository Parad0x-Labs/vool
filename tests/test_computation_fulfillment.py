"""Executed recipes are not proof that their requested calculations were answered."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.conductor.compose import compose_answer
from core.conductor.planner import ProposedClause, build_plan_from_clauses, demands_this_plan_served
from core.conductor.product_decision import ExecutionReport, reduce_execution_report
from core.conductor.realization import BoundExecutionPlan, RealizationBinding, RealizationLedger, RequirementRealization
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = (ROOT / "tests/fixtures/portfolio_last_run.txt").read_text()
NOVEL = "There are 180 litres of solution. Remove 20%. Calculate the retained volume and its shipping cost."


@pytest.mark.parametrize("bound", [False, True], ids=["unbound", "bound"])
@pytest.mark.parametrize("text,clause,expression,expected", [
    (ORIGINAL, "The dollar amount paid in each fee/premium and their combined total.",
     "fact_1 * fact_2_share * fact_9_share", 25),
    (NOVEL, "Calculate the retained volume and its shipping cost.",
     "fact_1 * (1 - fact_2_share)", 144),
], ids=["owner-portfolio", "new-solution"])
def test_partial_calculation_cannot_certify_a_whole_demand(bound, text, clause, expression, expected):
    plan = build_plan_from_clauses([ProposedClause(0, clause, "quantitative_reasoning", ())],
                                   original_request=text, plan_id="partial-computation")
    node = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    # Only this demand is under examination, with unchanged source context and
    # the same production node/graph/scheduler. Other portfolio asks stay outside this test.
    plan = replace(plan, graph=type(plan.graph)(nodes=(node,)))
    if bound:
        realization = RequirementRealization("r1", "q1", "quantitative_reasoning", clause,
                                             display_subject=clause)
        plan = replace(plan, bound_plan=BoundExecutionPlan(
            RealizationLedger((realization,)), (RealizationBinding("r1", (node.node_id,)),)),
            requirement_nodes={"q1": (node.node_id,)})
    recipe = {"steps": [{"label": "Established part", "expression": expression},
                         {"label": "Unestablished part", "expression": "unknown_input * 2"}]}
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda *_: json.dumps(recipe)))
    result = outcomes[0].result
    assert result["steps"][0]["value"] == pytest.approx(expected)
    assert result["cannot_determine"]
    decision = reduce_execution_report(ExecutionReport(
        bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
        planned_node_ids=(node.node_id,), requirement_nodes=plan.requirement_nodes))
    assert decision.disposition.value == "partially_fulfilled"
    assert decision.runtime_task_outcome.fulfillment_status.value == "partially_fulfilled"
    if bound:
        assert not decision.outcome_set.satisfied
    answer = compose_answer(plan, outcomes, decision)
    assert "Established part" in answer.text
    assert "Unestablished part" in answer.text
    start, end = node.clause_span
    assert start >= 0
    units = [SimpleNamespace(unit_id="u1", start=start, end=end, text=clause)]
    assert demands_this_plan_served(plan, outcomes, units) == ()


def test_captured_live_tank_rejection_is_not_successful_fulfillment():
    evidence = json.loads((ROOT / "tests/fixtures/model_orchestration_boundary/tank-ling-3.0-flash-scheduled-5d5de4e0.json").read_text())
    text = evidence["calls"][0]["prompt"]
    clause = "The retained volume on line A after filtering."
    plan = build_plan_from_clauses([ProposedClause(0, clause, "quantitative_reasoning", ())],
                                  original_request=text, plan_id="empty-computation")
    recipe = evidence["calls"][2]["output"]
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda *_: recipe))
    decision = reduce_execution_report(ExecutionReport(node_outcomes=tuple(outcomes),
        planned_node_ids=tuple(n.node_id for n in plan.nodes)))
    assert all(not (o.result or {}).get("steps") for o in outcomes)
    assert decision.disposition.value == "failed"


@pytest.mark.parametrize("text,steps,expected", [
    ("There are 72 kg. Remove all 72 kg. Calculate the remaining mass.",
     [{"label": "Zero remains", "expression": "72 - 72"}], "fulfilled"),
    ("Calculate how many 8 kg containers fit in 72 kg and their shipping cost.",
     [{"label": "Valid part", "expression": "72 / 8"}, None], "partially_fulfilled"),
    ("Calculate how many 8 kg containers fit in 72 kg.", [], "failed"),
], ids=["zero-is-a-result", "malformed-sibling", "empty-recipe"])
def test_independent_result_shapes_reach_receipts(text, steps, expected):
    from core.conductor.node import node_receipt

    plan = build_plan_from_clauses([ProposedClause(0, text, "quantitative_reasoning", ())],
                                   original_request=text, plan_id="receipt-control")
    outcomes = run_conductor_plan(plan, context=NodeContext(
        # These forged fields must not authorize either an empty or partial recipe.
        run_generation=lambda *_: json.dumps({"steps": steps, "fulfilled": True,
                                              "fulfillment_status": "fulfilled"})))
    assert len(outcomes) == 1
    outcome = outcomes[0]
    receipt = node_receipt(outcome, plan_id=plan.plan_id)
    assert receipt["ok"] == (expected != "failed")
    assert receipt["fulfillment_status"] == expected
    assert receipt["fulfilled"] == (expected == "fulfilled")
    decision = reduce_execution_report(ExecutionReport(node_outcomes=tuple(outcomes),
        planned_node_ids=(outcome.node.node_id,)))
    assert decision.disposition.value == expected


def test_lifecycle_does_not_publish_a_premature_fulfillment_failure():
    from core.conductor.node import ConductorNode, NodeFailureCode, NodeLifecycle, NodeOutcome, node_receipt

    outcome = NodeOutcome(ConductorNode("lifecycle", "calculation", "Calculate 3 + 4"))
    for state in (NodeLifecycle.PLANNED, NodeLifecycle.RUNNING):
        outcome.state = state
        assert outcome.to_dict()["fulfillment_status"] is None
        assert node_receipt(outcome, plan_id="lifecycle")["fulfillment_status"] is None
        assert not outcome.fulfilled
    outcome.state = NodeLifecycle.DEPENDENCY_FAILED
    assert outcome.fulfillment_status.value == "blocked"
    outcome.state = NodeLifecycle.FAILED
    outcome.failure_code = NodeFailureCode.CANCELLED
    assert outcome.fulfillment_status.value == "cancelled"


def test_partial_parent_keeps_its_successfully_executed_dependent_result():
    text = ("We have 72 kg and containers holding 8 kg. Calculate the container count and delivery cost. "
            "Multiply the container count by 3.")
    clauses = ("Calculate the container count and delivery cost.", "Multiply the container count by 3.")
    plan = build_plan_from_clauses([
        ProposedClause(0, clauses[0], "quantitative_reasoning", ()),
        ProposedClause(1, clauses[1], "quantitative_reasoning", (0,)),
    ], original_request=text, plan_id="partial-parent")
    assert len(plan.nodes) == 2
    first, second = plan.nodes
    realizations = (
        RequirementRealization("r1", "q1", "quantitative_reasoning", clauses[0], display_subject=clauses[0]),
        RequirementRealization("r2", "q2", "quantitative_reasoning", clauses[1], display_subject=clauses[1],
                               prerequisite_realization_ids=("r1",)),
    )
    plan = replace(plan, bound_plan=BoundExecutionPlan(RealizationLedger(realizations), (
        RealizationBinding("r1", (first.node_id,)), RealizationBinding("r2", (second.node_id,)),
    )))
    recipes = iter([
        {"steps": [{"label": "Container count", "expression": "fact_1 / fact_2"},
                   {"label": "Delivery cost", "expression": "unknown_price"}]},
        {"steps": [{"label": "Triple count", "expression": "dependency_1_value_1 * fact_3"}]},
    ])
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda *_: json.dumps(next(recipes))))
    assert outcomes[0].partially_fulfilled
    assert outcomes[1].result["steps"][0]["value"] == 27
    report = ExecutionReport(bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
                             planned_node_ids=tuple(n.node_id for n in plan.nodes))
    decision = reduce_execution_report(report)
    assert decision.disposition.value == "partially_fulfilled"
    assert decision.outcome_set.state_of("r2").value == "satisfied"
    assert "Triple count" in compose_answer(plan, outcomes, decision).text
    # A partial prerequisite is not a generic waiver: the executed dependency edge is required.
    detached = replace(outcomes[1], node=replace(second, depends_on=()))
    broken = reduce_execution_report(replace(report, node_outcomes=(outcomes[0], detached)))
    assert broken.disposition.value == "integrity_failure"
