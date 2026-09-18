"""Result transforms consume declared computation results, never invented cells."""
import json
from pathlib import Path

import pytest

from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.turn_ir import parse_turn_ir

ROOT = Path(__file__).resolve().parents[1]
OWNER = (ROOT / "tests/fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "A warehouse holds 360 cartons. Send 40% to the west depot and keep the rest. Calculate:\n"
    "1. The cartons sent west.\n2. The cartons retained.\n"
    "3. Present these results in a compact table and explain the allocation briefly."
)


def plan_for(text, *, dependencies=True):
    clauses = parse_turn_ir(text).clauses
    return build_plan_from_clauses([
        ProposedClause(i, c.request_text,
            "result_presentation" if i == len(clauses) - 1 else "quantitative_reasoning",
            tuple(range(i)) if dependencies and i == len(clauses) - 1 else ())
        for i, c in enumerate(clauses)
    ], original_request=text, plan_id="result-presentation")


def recipes_for(text):
    if text == NOVEL:
        return [{"steps": [{"label": label, "expression": expression}]} for label, expression in [
            ("Cartons sent west", "fact_1 * fact_2_share"),
            ("Cartons retained", "fact_1 * (1 - fact_2_share)"),
        ]]
    evidence = json.loads((ROOT / "tests/fixtures/model_orchestration_boundary/portfolio-ling-3.0-flash-scheduled-311cd939.json").read_text())
    recipes = [json.loads(c["output"]) for c in evidence["calls"][1:]]
    fees = ["fact_1 * fact_2_share * fact_9_share",
            "fact_1 * fact_3_share * fact_4_share * fact_10_share / (1 + fact_10_share)",
            "fact_1 * fact_3_share * fact_5_share * fact_11_share / (1 + fact_11_share)"]
    recipes.append({"steps": [{"label": f"Fee {i}", "expression": expr}
                               for i, expr in enumerate(fees)] + [
        {"label": "Combined charges", "expression": "step_1 + step_2 + step_3"}]})
    recipes.append({"steps": [{"label": "Percentage lost",
        "expression": "(" + " + ".join(f"({expr})" for expr in fees) + ") / fact_1 * 100",
        "unit": "%"}]})
    return recipes


@pytest.mark.parametrize("text", [OWNER, NOVEL], ids=["owner", "new-warehouse"])
def test_summary_executes_over_owned_results_and_keeps_numeric_cells(text):
    plan = plan_for(text)
    target = plan.nodes[-1]
    assert target.operation == "result_presentation"
    assert len(target.depends_on) == len(parse_turn_ir(text).clauses) - 1
    recipes = iter(recipes_for(text))

    def generate(system, prompt):
        if "result-presentation.v2" in system:
            return json.dumps({"explanation": ["These results retain the stated allocation and charges."]})
        return json.dumps(next(recipes))

    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=generate))
    summary = next(o for o in outcomes if o.node.node_id == target.node_id)
    assert summary.fulfilled
    assert summary.rendered.count("| Result |") == 1
    represented = [(r["label"], r["value"]) for r in summary.result["rows"]]
    expected = [(s["label"], s["value"]) for o in outcomes if o is not summary
                for s in (o.result or {}).get("steps", [])]
    assert represented == expected
    assert "dependency_" not in summary.rendered
    assert summary.result["source_node_ids"] == list(target.depends_on)
    if text == NOVEL:
        assert [value for _, value in represented] == [144, 216]


def test_summary_without_declared_inputs_stays_unresolved():
    plan = plan_for(NOVEL, dependencies=False)
    assert plan.nodes[-1].operation == "unresolved"


@pytest.mark.parametrize("explanation", [
    ["The allocation is 999 cartons."], [{"ref": "foreign_value"}], [],
])
def test_bad_explanation_preserves_values_but_cannot_claim_full_delivery(explanation):
    plan = plan_for(NOVEL)
    recipes = iter(recipes_for(NOVEL))
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda system, prompt:
        json.dumps({"explanation": explanation}) if "result-presentation.v2" in system
        else json.dumps(next(recipes))))
    summary = outcomes[-1]
    assert summary.partially_fulfilled
    assert not summary.fulfilled
    assert [row["value"] for row in summary.result["rows"]] == [144, 216]
    assert summary.result["explanation"] == []
    assert "999" not in summary.rendered and "foreign_value" not in summary.rendered


def test_plain_calculation_results_can_be_presented_without_generation():
    text = "Calculate 17 * 9. Present the result as a table."
    plan = build_plan_from_clauses([
        ProposedClause(0, "Calculate 17 * 9.", "calculation", ()),
        ProposedClause(1, "Present the result as a table.", "result_presentation", (0,)),
    ], original_request=text, plan_id="plain-calculation-table")
    outcomes = run_conductor_plan(plan, context=NodeContext())
    assert outcomes[-1].fulfilled
    assert [row["value"] for row in outcomes[-1].result["rows"]] == [153]


def test_declared_scope_is_not_widened_to_other_results():
    from core.conductor.node import ConductorNode
    from core.conductor.result_presentation import _run

    node = ConductorNode("view", "result_presentation", "Present the result as a table.", depends_on=("a",))
    with pytest.raises(ValueError, match="declared result scope"):
        _run(node, NodeContext(dependency_results={"a": {"value": 4}, "foreign": {"value": 8}}))


def test_partial_source_and_invalid_value_never_become_a_complete_summary():
    from core.conductor.node import ConductorNode
    from core.conductor.result_presentation import _fulfillment, _run

    node = ConductorNode("view", "result_presentation", "Present the result as a table.", depends_on=("a",))
    result = _run(node, NodeContext(dependency_results={"a": {
        "steps": [{"label": "Known", "value": 84}, {"label": "Broken", "value": float("nan")}],
        "cannot_determine": ["Shipping cost is unavailable."],
    }}))
    assert [r["value"] for r in result["rows"]] == [84]
    assert _fulfillment(result).value == "partially_fulfilled"
    assert "Shipping cost is unavailable." in result["cannot_determine"]


def test_valid_explanation_references_use_exact_scoped_values():
    from core.conductor.node import ConductorNode
    from core.conductor.result_presentation import _render, _run

    node = ConductorNode("view", "result_presentation", "Present the result as a table.",
        arguments={"clause": "Present the result as a table and explain it.", "explanation_requested": True},
        depends_on=("a",))
    result = _run(node, NodeContext(dependency_results={"a": {"value": 153}},
        run_generation=lambda *_: json.dumps({"explanation": ["The result is ", {"ref": "dependency_1_value_1"}, "."]})))
    assert "The result is 153." in _render(node, result)
    assert not result["cannot_determine"]


@pytest.mark.parametrize("dimensions,authority,unit,expected", [
    ({}, "dimensionless_expression", "percent", "percent"),
    ({}, "dimensionless_expression", "%", "%"),
    ({"kg": 1}, "source_expression", "USD", "kg"),
    ({}, "dimensionless_expression", "USD", ""),
    ({"kg": 1}, "model", "kg", ""),
])
def test_summary_preserves_only_runtime_established_units(dimensions, authority, unit, expected):
    from core.conductor.node import ConductorNode
    from core.conductor.result_presentation import _run

    node = ConductorNode("view", "result_presentation", "Present as a table", depends_on=("a",))
    result = _run(node, NodeContext(dependency_results={"a": {"steps": [{
        "label": "Retained share", "value": 32.2, "unit_dimensions": dimensions,
        "unit_authority": authority, "unit": unit,
    }]}}))
    assert result["rows"][0]["unit"] == expected
