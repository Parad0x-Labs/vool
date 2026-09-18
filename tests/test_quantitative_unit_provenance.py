import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_run
from core.conductor.registry import NodeContext
from core.conductor.shared_context import evaluate_grounded_expression, extract_shared_context


def run(text, steps):
    return _quantitative_run(ConductorNode(node_id="units", operation="quantitative_reasoning",
        request_text=text, arguments={"clause": text}), NodeContext(
        shared_context=extract_shared_context(text),
        run_generation=lambda *_: json.dumps({"steps": steps})))


def test_original_portfolio_retains_each_computed_unit():
    root = Path(__file__).resolve().parents[1]
    text = (root / "tests/fixtures/portfolio_last_run.txt").read_text()
    evidence = json.loads((root / "tests/fixtures/model_orchestration_boundary/portfolio-ling-3.0-flash-runtime-conversions-v1.json").read_text())
    recipe = json.loads(evidence["calls"][0]["output"])
    result = run(text, recipe["steps"])
    units = {s["label"]: s["unit"] for s in result["steps"]}
    assert units["USD to GBP allocation"] == "USD"
    assert units["GBP received after FX fee"] == "GBP"
    assert units["Silver troy ounces after premium"] == "troy ounces"
    assert units["Silver grams after premium"] == "g"
    assert units["Platinum grams after premium"] == "g"
    assert units["Combined fee/premium total"] == "USD"


def test_fresh_mass_calculation_does_not_trust_a_wrong_model_label():
    result = run("A kiln receives 840 kg of clay. Calculate the 12.5% discarded, in kg and grams.", [
        {"label": "discarded", "expression": "fact_1 * fact_2_share", "unit": "USD"},
        {"label": "grams", "expression": "step_1 * unit_kg_to_g", "unit": "kg"},
    ])
    assert [(s["value"], s["unit"]) for s in result["steps"]] == [(105, "kg"), (105000, "g")]


def test_no_conversion_means_no_grams_label():
    result = run("Silver costs 40 USD per troy ounce. Calculate how much silver 800 USD buys.", [
        {"label": "silver", "expression": "fact_2 / fact_1", "unit": "grams"},
    ])
    assert result["steps"][0]["unit"] == "troy ounces"


def test_incompatible_addition_is_not_published_as_a_quantity():
    result = run("There are 40 kg of clay and 800 USD. Calculate their combined amount.", [
        {"label": "invalid", "expression": "fact_1 + fact_2", "unit": "USD"},
    ])
    assert not result["steps"]
    assert any("different units" in reason for reason in result["cannot_determine"])


def test_a_grounded_literal_retains_its_supplied_quantity_unit():
    result = run("Calculate the total of 200 USD and 400 USD.", [
        {"label": "total", "expression": "fact_1 + 400", "unit": "grams"},
    ])
    assert result["steps"][0]["value"] == 600
    assert result["steps"][0]["unit"] == "USD"


def test_upstream_values_cannot_replace_source_quantity_values():
    context = extract_shared_context("Calculate the total of 200 USD and 400 USD.")
    assert evaluate_grounded_expression("fact_1 + fact_2", facts=context.facts,
        symbols={"fact_1": 999}) == 600


@pytest.mark.parametrize("text", [
    "Calculate the sum of $200 USD and $400 CAD.",
    "Calculate the sum of $200USD and $400CAD.",
])
def test_each_amount_owns_its_currency_not_the_whole_message(text):
    result = run(text, [{"label": "invalid sum", "expression": "fact_1 + fact_2", "unit": "USD"}])
    assert not result["steps"]
    assert any("different units" in reason for reason in result["cannot_determine"])


def test_neutral_zero_does_not_destroy_quantity_units():
    result = run("Calculate the total for 200 USD.", [
        {"label": "total", "expression": "0 + fact_1 - 0", "unit": "grams"},
    ])
    assert result["steps"][0]["unit"] == "USD"


def test_dimensionless_result_does_not_certify_a_display_scale():
    result = run("Calculate the fraction 200 USD is of 400 USD.", [
        {"label": "fraction", "expression": "fact_1 / fact_2", "unit": "ratio"},
    ])
    assert result["steps"][0]["value"] == 0.5
    assert result["steps"][0]["unit_dimensions"] == {}
    assert result["steps"][0]["unit_authority"] == "dimensionless_expression"
