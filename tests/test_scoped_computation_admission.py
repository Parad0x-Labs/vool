import json
from pathlib import Path

import pytest

from core.conductor.operations import _quantitative_expand, _quantitative_run
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext
from core.conductor.shared_context import extract_shared_context
from core.turn_ir import parse_turn_ir

ORIGINAL = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "A tank holds 960 litres. Split it 35% to line A and 65% to line B.\n"
    "Line A loses 8% during filtering. Calculate:\n"
    "1. The initial volume for each line.\n"
    "2. The retained volume on line A after filtering.\n"
    "3. What percentage of the original volume remains on line A."
)


@pytest.mark.parametrize(("text", "indices"), [(ORIGINAL, range(6)), (NOVEL, range(3))])
def test_original_and_predeclared_novel_computations_are_admitted(text, indices):
    turn = parse_turn_ir(text)
    context = extract_shared_context(text)
    for index in indices:
        assert _quantitative_expand(turn.clauses[index].request_text, context)


def test_parent_heading_does_not_authorize_unrelated_requests_or_presentation():
    context = extract_shared_context(ORIGINAL)
    for request in (
        "What is the weather in Oslo?", "When did the Berlin Wall fall?",
        "Delete report.txt.", parse_turn_ir(ORIGINAL).clauses[-1].request_text,
        "The initial volume for each line.",  # not a source clause of this turn
    ):
        assert not _quantitative_expand(request, context)


def test_noncomputational_parent_does_not_admit_elliptical_requests():
    text = NOVEL.replace("Calculate:", "Describe:")
    context = extract_shared_context(text)
    for clause in parse_turn_ir(text).clauses[:2]:
        assert not _quantitative_expand(clause.request_text, context)


@pytest.mark.parametrize("question", [
    "What proportion of the initial liquid remains?",
    "What is the ratio of the two retained quantities?",
])
def test_explicit_quantitative_interrogatives_still_require_operands(question):
    assert not _quantitative_expand(question)
    assert _quantitative_expand(question, extract_shared_context(NOVEL))


def test_original_allocation_reaches_existing_expression_executor():
    clause = parse_turn_ir(ORIGINAL).clauses[3]
    plan = build_plan_from_clauses(
        [ProposedClause(0, clause.request_text, "quantitative_reasoning", ())],
        original_request=ORIGINAL, plan_id="allocation-admission")
    node = next(n for n in plan.nodes if n.request_text == clause.request_text)
    assert node.operation == "quantitative_reasoning"
    payload = {"steps": [
        {"label": "GBP budget", "expression": "fact_1 * fact_2_share", "unit": "USD"},
        {"label": "Silver budget", "expression": "fact_1 * fact_3_share * fact_4_share", "unit": "USD"},
        {"label": "Platinum budget", "expression": "fact_1 * fact_3_share * fact_5_share", "unit": "USD"},
    ], "cannot_determine": []}
    result = _quantitative_run(node, NodeContext(
        shared_context=extract_shared_context(ORIGINAL),
        run_generation=lambda *_: json.dumps(payload)))
    assert not result["cannot_determine"]
    assert [s["value"] for s in result["steps"]] == pytest.approx([3125, 5156.25, 4218.75])


def test_novel_volumes_and_percentage_reach_the_same_executor():
    turn = parse_turn_ir(NOVEL)
    plan = build_plan_from_clauses(
        [ProposedClause(i, c.request_text, "quantitative_reasoning", ())
         for i, c in enumerate(turn.clauses)],
        original_request=NOVEL, plan_id="tank-admission")
    payloads = [
        (["fact_1 * fact_2_share", "fact_1 * fact_3_share"], [336, 624], "litres"),
        (["fact_1 * fact_2_share * (1 - fact_4_share)"], [309.12], "litres"),
        (["fact_2_share * (1 - fact_4_share) * 100"], [32.2], "%"),
    ]
    assert len(plan.nodes) == 3
    for node, (expressions, expected, unit) in zip(plan.nodes, payloads, strict=True):
        assert node.operation == "quantitative_reasoning"
        payload = {"steps": [{"label": f"result-{i}", "expression": expression, "unit": unit}
                              for i, expression in enumerate(expressions)], "cannot_determine": []}
        result = _quantitative_run(node, NodeContext(
            shared_context=extract_shared_context(NOVEL),
            run_generation=lambda *_, payload=payload: json.dumps(payload)))
        assert not result["cannot_determine"]
        assert [s["value"] for s in result["steps"]] == pytest.approx(expected)
