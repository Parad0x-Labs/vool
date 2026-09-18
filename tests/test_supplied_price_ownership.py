"""Predeclared ownership requirements; live retrieval is not a supplied operand."""
import json
from pathlib import Path

import pytest

from core.conductor.operations import _quantitative_run
from core.conductor.planner import ProposedClause, build_plan_from_clauses
from core.conductor.registry import NodeContext
from core.conductor.shared_context import extract_shared_context
from core.turn_ir import parse_turn_ir

ORIGINAL = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "I have 1000 USD. Use these assumptions:\n"
    "Gold = 2500 USD per troy ounce\n"
    "1. How much gold can I buy with 1000 USD?\n"
    "2. Explain the calculation."
)


def plan_for(text):
    turn = parse_turn_ir(text)
    return build_plan_from_clauses(
        [ProposedClause(c.ordinal, c.request_text, "quantitative_reasoning", ())
         for c in turn.clauses],
        original_request=text, plan_id="supplied-price-ownership",
    )


@pytest.mark.parametrize("text", [ORIGINAL, NOVEL])
def test_supplied_prices_do_not_create_live_price_dependencies(text):
    plan = plan_for(text)
    assert not [n for n in plan.nodes if n.operation == "market_quote"]


def test_novel_supplied_price_is_consumed_with_source_provenance():
    plan = plan_for(NOVEL)
    node = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    result = _quantitative_run(node, NodeContext(shared_context=extract_shared_context(NOVEL)))
    assert not result["cannot_determine"]
    assert result["steps"][0]["value"] == pytest.approx(0.4)
    target = result["steps"][0]["operands"]["target"]
    assert target["source"] == "user_supplied"
    assert target["source_url"] == ""
    assert target["retrieved_at"] == ""
    assert target["dep_id"] == ""


def test_missing_price_still_requires_a_live_producer():
    text = NOVEL.replace("Gold = 2500 USD per troy ounce\n", "")
    assert any(n.operation == "market_quote" for n in plan_for(text).nodes)


@pytest.mark.parametrize("replacement", [
    "Gold = 0 USD per troy ounce",
    "Gold = -2500 USD per troy ounce",
    "Gold = 2500 USD per litre",
    "Gold = 2500 USD per troy ounce\nGold = 2700 USD per troy ounce",
])
def test_invalid_or_conflicting_supplied_price_is_not_silently_replaced(replacement):
    text = NOVEL.replace("Gold = 2500 USD per troy ounce", replacement)
    plan = plan_for(text)
    assert not [n for n in plan.nodes if n.operation == "market_quote"]
    node = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    result = _quantitative_run(node, NodeContext(shared_context=extract_shared_context(text)))
    assert not result["steps"]
    assert result["cannot_determine"]


def test_explicit_current_price_keeps_live_authority():
    text = NOVEL.replace("buy with", "buy at the current price with")
    plan = plan_for(text)
    assert any(n.operation == "market_quote" for n in plan.nodes)


def test_unrelated_assignment_cannot_price_the_target():
    text = NOVEL.replace("Gold =", "Silver =")
    assert any(n.operation == "market_quote" and n.arguments["asset_key"] == "gold"
               for n in plan_for(text).nodes)


def test_price_per_gram_converts_to_existing_asset_price_unit():
    text = NOVEL.replace("2500 USD per troy ounce", "80 USD per gram")
    plan = plan_for(text)
    node = next(n for n in plan.nodes if n.operation == "quantitative_reasoning")
    result = _quantitative_run(node, NodeContext(shared_context=extract_shared_context(text)))
    assert not result["cannot_determine"]
    assert result["steps"][0]["value"] == pytest.approx(1000 / (80 * 31.1034768))


def test_real_planner_entry_does_not_bypass_supplied_price_ownership():
    from core.conductor.planner import plan_conductor_turn

    raw = json.dumps([
        {"request": c.request_text, "operation": "quantitative_reasoning", "depends_on": []}
        for c in parse_turn_ir(NOVEL).clauses
    ])
    plan = plan_conductor_turn(NOVEL, ask_model=lambda *_: raw)
    assert plan is not None
    assert not [n for n in plan.nodes if n.operation == "market_quote"]
    from core.conductor.scheduler import run_conductor_plan

    outcomes = run_conductor_plan(plan, context=NodeContext())
    assert outcomes
    assert all(outcome.state.value == "succeeded" for outcome in outcomes)
    assert outcomes[0].result["steps"][0]["value"] == pytest.approx(0.4)


@pytest.mark.parametrize("quote", ["```", '"'])
def test_quoted_assignments_are_not_promoted_to_user_price_authority(quote):
    from core.conductor.supplied_prices import supplied_prices

    text = NOVEL.replace("Gold = 2500 USD per troy ounce", f"{quote}\nGold = 2500 USD per troy ounce\n{quote}")
    assert supplied_prices(extract_shared_context(text), "How much gold can I buy?") == {}


def test_allocation_preserves_current_price_scope_for_every_target():
    from core.conductor.planner import plan_conductor_turn

    text = (
        "Gold = 2500 USD per troy ounce\nSilver = 50 USD per troy ounce\n"
        "I have 1000 USD, split it equally in 2 parts and buy gold and silver at current prices."
    )
    plan = plan_conductor_turn(text, ask_model=lambda *_: "")
    assert plan is not None
    values = {"gold": (2000, 0.25), "silver": (100, 5)}
    for node in plan.nodes:
        if node.operation != "quantitative_reasoning":
            continue
        key = node.arguments["roles"]["target"]["key"]
        price, expected = values[key]
        dependencies = {dep: {"asset_key": key, "entity": key.title(), "price": price,
                              "currency": "USD", "source": "controlled_live_result"}
                        for dep in node.depends_on}
        result = _quantitative_run(node, NodeContext(
            shared_context=extract_shared_context(text), dependency_results=dependencies))
        assert not result["cannot_determine"]
        assert result["steps"][0]["value"] == pytest.approx(expected)
        assert result["steps"][0]["operands"]["target"]["source"] == "controlled_live_result"
    assert len([n for n in plan.nodes if n.operation == "quantitative_reasoning"]) == 2


@pytest.mark.parametrize(("unit", "expected"), [("petroleum barrel", 8), ("US gallon", 600 / (75 * 42))])
def test_held_out_oil_purchase_uses_qualified_source_units(unit, expected):
    from core.conductor.planner import plan_conductor_turn
    from core.conductor.scheduler import run_conductor_plan

    text = f"Brent crude = 75 USD per {unit}\nHow much Brent crude can I buy with 600 USD?"
    plan = plan_conductor_turn(text, ask_model=lambda *_: "")
    assert plan is not None
    assert not any(n.operation == "market_quote" for n in plan.nodes)
    outcomes = run_conductor_plan(plan, context=NodeContext())
    assert len(outcomes) == 1
    result = outcomes[0].result
    assert result and not result["cannot_determine"]
    assert result["steps"][0]["value"] == pytest.approx(expected)
    assert result["steps"][0]["unit"] == "barrels"


@pytest.mark.parametrize("unit", ["barrel", "gallon", "ounce"])
def test_unqualified_source_units_do_not_gain_a_physical_definition(unit):
    from core.conductor.supplied_prices import supplied_prices

    text = f"Brent crude = 75 USD per {unit}"
    binding = supplied_prices(extract_shared_context(text), "How much Brent crude can I buy?")
    assert binding["brent_crude"]["problem"]
