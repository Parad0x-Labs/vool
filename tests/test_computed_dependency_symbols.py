"""Controlled recipes exercise the real dependency handoff, not model quality."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext, computed_dependency_bindings
from core.conductor.scheduler import _context_for, run_conductor_plan

PORTFOLIO = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
TANK = (
    "A tank holds 960 litres. Split it 35% to line A and 65% to line B.\n"
    "Line A loses 8% during filtering. Calculate:\n"
    "1. The initial volume for each line.\n"
    "2. The retained volume on line A after filtering.\n"
    "3. What percentage of the original volume remains on line A."
)
NOVEL = (
    "A workshop has 480 kg of resin. Assign 30% to moulding; 25% of that portion is waste. "
    "Calculate the moulding allocation, then calculate its usable mass."
)


@pytest.mark.parametrize("text,clauses,first,second,expected,unit", [
    (PORTFOLIO,
     ("The exact USD amount allocated to each asset.",
      "How many British pounds I receive after the FX fee."),
     ("GBP allocation", "fact_1 * fact_2_share", "USD"),
     "dependency_1_value_1 * (1 - fact_9_share) / fact_6", 2362.8048780487807, "GBP"),
    (TANK, ("The initial volume for each line.", "The retained volume on line A after filtering."),
     ("Initial volume for line A", "fact_1 * fact_2_share", "litres"),
     "dependency_1_value_1 * (1 - fact_4_share)", 309.12, "litre"),
    (NOVEL, ("Calculate the moulding allocation", "calculate its usable mass."),
     ("Moulding stock (before waste)", "fact_1 * fact_2_share", "kg"),
     "dependency_1_value_1 * (1 - fact_3_share)", 108, "kg"),
], ids=["owner-portfolio", "captured-tank", "new-resin"])
def test_dependency_symbols_carry_values_and_units(text, clauses, first, second, expected, unit):
    plan = build_plan_from_clauses([
        ProposedClause(0, clauses[0], "quantitative_reasoning", ()),
        ProposedClause(1, clauses[1], "quantitative_reasoning", (0,)),
    ], original_request=text, plan_id="dependency-contract")
    computations = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
    assert len(computations) == 2
    assert computations[1].depends_on == (computations[0].node_id,)
    calls = []

    def generate(system, briefing):
        calls.append((system, briefing))
        label, expression, stated_unit = first if len(calls) == 1 else ("Retained result", second, "wrong")
        return json.dumps({"steps": [{"label": label, "expression": expression, "unit": stated_unit}]})

    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=generate))
    assert len(calls) == 2
    result = next(o.result for o in outcomes if o.node.node_id == computations[1].node_id)
    assert result is not None
    assert result["cannot_determine"] == []
    assert result["steps"][0]["value"] == pytest.approx(expected)
    assert result["steps"][0]["unit"] == unit
    assert "dependency_1_value_1" in calls[1][0]
    assert first[0] in calls[1][1]
    assert "dependency_1_value_1" in calls[1][1]
    rendered = next(o.rendered for o in outcomes if o.node.node_id == computations[1].node_id)
    assert "dependency_" not in rendered
    assert result["expression_bindings"]["dependency_1_value_1"] == pytest.approx(
        outcomes[0].result["steps"][0]["value"])


def test_repeated_labels_do_not_collapse_nodes_or_steps_and_cut_edges_do_not_export():
    def completed(node_id, values):
        return NodeOutcome(
            ConductorNode(node_id, "quantitative_reasoning", "Calculate stock"),
            state=NodeLifecycle.SUCCEEDED,
            result={"values": {"Same label": values[-1]}, "steps": [
                {"label": "Same label", "value": value, "step_id": f"step_{i + 1}",
                 "unit_authority": "source_expression", "unit_dimensions": {"kg": 1}}
                for i, value in enumerate(values)
            ]},
        )

    a, b = completed("a", [36, 18]), completed("b", [7])
    target = ConductorNode("target", "quantitative_reasoning", "Calculate combined stock", depends_on=("a", "b"))
    base = NodeContext()
    context = _context_for(target, base, {"b": b, "a": a})
    assert {k: v["value"] for k, v in computed_dependency_bindings(context.dependency_results).items()} == {
        "dependency_1_value_1": 36, "dependency_1_value_2": 18, "dependency_2_value_1": 7,
    }
    assert _context_for(replace(target, depends_on=()), base, {"a": a, "b": b}).derived_facts == {}
    a.state = NodeLifecycle.FAILED
    assert "a" not in _context_for(target, base, {"a": a, "b": b}).dependency_results


def test_arithmetic_symbols_do_not_duplicate_anaphoric_operands():
    from core.conductor.operations import _anaphoric_arithmetic_binding

    source = ConductorNode("source", "quantitative_reasoning", "Calculate stock")
    outcome = NodeOutcome(source, state=NodeLifecycle.SUCCEEDED, result={
        "values": {"stock": 90}, "steps": [{"label": "stock", "value": 90}],
    })
    target = ConductorNode("target", "quantitative_reasoning", "Divide that result by 6", depends_on=("source",))
    context = _context_for(target, NodeContext(), {"source": outcome})
    assert _anaphoric_arithmetic_binding(target.request_text, context)[1] == 15


def test_held_out_dependency_units_reject_cross_dimension_addition():
    from core.conductor.operations import _quantitative_run
    from core.conductor.shared_context import extract_shared_context

    source = ConductorNode("weigh", "quantitative_reasoning", "Calculate remaining mass")
    prior = NodeOutcome(source, state=NodeLifecycle.SUCCEEDED, result={
        "values": {"Remaining material": 84},
        "steps": [{"label": "Remaining material", "value": 84,
                   "unit_authority": "source_expression", "unit_dimensions": {"kg": 1}}],
    })
    target = ConductorNode("bad", "quantitative_reasoning", "Calculate combined amount", depends_on=("weigh",))
    base = NodeContext(shared_context=extract_shared_context("The second container holds 12 litres."),
        run_generation=lambda *_: json.dumps({"steps": [{"label": "Invalid total",
            "expression": "dependency_1_value_1 + fact_1", "unit": "kg"}]}))
    result = _quantitative_run(target, _context_for(target, base, {"weigh": prior}))
    assert not result["steps"]
    assert result["cannot_determine"]
    assert "not a fact" not in str(result["cannot_determine"])
