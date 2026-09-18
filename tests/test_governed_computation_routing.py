"""An explicit computation list keeps its quantitative questions in computation."""
import json
from pathlib import Path

import pytest

from core.conductor.operations import governed_computation_clause
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.conductor.shared_context import extract_shared_context
from core.turn_ir import parse_turn_ir

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = (ROOT / "tests/fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "A kiln starts with 250 kg of clay. Processing removes 12%. Compute:\n"
    "1. How much clay remains after processing?\n"
    "2. What percentage of the original mass remains?"
)


def plan_all_calculations(text):
    clauses = parse_turn_ir(text).clauses
    return build_plan_from_clauses([
        ProposedClause(i, clause.request_text, "calculation", ())
        for i, clause in enumerate(clauses)
    ], original_request=text, plan_id="governed-routing")


@pytest.mark.parametrize("text,count", [(ORIGINAL, 6), (NOVEL, 2)], ids=["owner", "new-kiln"])
def test_numeric_questions_keep_the_user_computation_instruction(text, count):
    plan = plan_all_calculations(text)
    clauses = parse_turn_ir(text).clauses
    for clause in clauses[:count]:
        nodes = [n for n in plan.nodes if n.request_text == clause.request_text]
        assert len(nodes) == 1
        assert nodes[0].operation == "quantitative_reasoning"


def test_new_domain_executes_numbers_instead_of_unchecked_explanation():
    plan = plan_all_calculations(NOVEL)
    recipes = iter([
        {"steps": [{"label": "Retained clay", "expression": "fact_1 * (1 - fact_2_share)"}]},
        {"steps": [{"label": "Retained percentage", "expression": "(1 - fact_2_share) * 100", "unit": "%"}]},
    ])
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=lambda *_: json.dumps(next(recipes))))
    assert [o.result["steps"][0]["value"] for o in outcomes] == [220, 88]
    assert all(o.fulfilled for o in outcomes)


def test_owner_currency_question_executes_bound_facts_after_retyping():
    clause = parse_turn_ir(ORIGINAL).clauses[0]
    plan = build_plan_from_clauses([
        ProposedClause(0, clause.request_text, "calculation", ()),
    ], original_request=ORIGINAL, plan_id="owner-currency-routing")
    recipe = {"steps": [{
        "label": "GBP after fee",
        "expression": "fact_1 * fact_2_share * (1 - fact_9_share) / fact_6",
        "unit": "GBP",
    }]}
    outcomes = run_conductor_plan(plan, context=NodeContext(
        run_generation=lambda *_: json.dumps(recipe)))
    currency = [o for o in outcomes if o.node.request_text == clause.request_text]
    assert len(currency) == 1
    assert currency[0].fulfilled
    assert currency[0].result["steps"][0]["value"] == pytest.approx(2362.8048780487807)
    # This deliberately incomplete proposal must not certify the whole request.
    assert any(o.node.operation == "unresolved" and not o.fulfilled for o in outcomes)


def test_computation_heading_cannot_be_borrowed_by_a_foreign_question():
    shared = extract_shared_context(NOVEL)
    assert not governed_computation_clause("How much fuel remains?", shared)
    assert not governed_computation_clause("Delete the kiln records.", shared)


def test_explicit_knowledge_proposal_is_not_retyped_by_computation_heading():
    text = "There are 250 kg of clay. Calculate:\n1. How many continents are there?\n2. Who wrote 1984?"
    clauses = parse_turn_ir(text).clauses
    plan = build_plan_from_clauses([
        ProposedClause(i, c.request_text, "factual_explanation", ())
        for i, c in enumerate(clauses)
    ], original_request=text, plan_id="knowledge-retained")
    assert all(n.operation == "factual_explanation" for n in plan.nodes)


@pytest.mark.parametrize("text", [
    "There are 250 kg of clay and 12 trays. Describe:\n1. How many continents are there?",
    "There are 250 kg of clay and 12 trays.\n1. How many sides does a hexagon have?",
    "There are 250 kg of clay. Calculate:\n1. Who wrote the novel 1984?\n2. When did the Berlin Wall fall?",
])
def test_independent_factual_questions_keep_knowledge_rescue(text):
    plan = plan_all_calculations(text)
    assert any(n.operation == "factual_explanation" for n in plan.nodes)
    assert not any(n.operation == "quantitative_reasoning" for n in plan.nodes)
