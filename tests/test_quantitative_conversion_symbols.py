"""Trusted conversion factors use the existing quantity table, not model literals."""
import json

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_run, quantity_conversion_bindings
from core.conductor.registry import NodeContext
from core.conductor.shared_context import extract_shared_context


@pytest.mark.parametrize("text,symbol,quantity,expected", [
    ("Convert 2 troy ounces of platinum to grams.", "unit_troy_oz_to_g", 2, 62.2069536),
    ("Calculate how many litres are in 3 petroleum barrels.", "unit_barrel_to_litre", 3, 476.961884784),
    ("Calculate grams from 4 kg of clay.", "unit_kg_to_g", 4, 4000),
    ("Calculate grams from 4kg of clay.", "unit_kg_to_g", 4, 4000),
])
def test_conversion_is_shown_executed_and_recorded(text, symbol, quantity, expected):
    bindings = quantity_conversion_bindings(text)
    assert bindings[symbol]["factor"] == pytest.approx(expected / quantity)
    prompts = []
    def propose(system, prompt):
        prompts.append(prompt)
        return json.dumps({"steps": [{"label": "converted", "expression": f"fact_1 * {symbol}"}]})
    result = _quantitative_run(ConductorNode(node_id="convert", operation="quantitative_reasoning",
        request_text=text, arguments={"clause": text}), NodeContext(
        shared_context=extract_shared_context(text), run_generation=propose))
    assert result["values"]["converted"] == pytest.approx(expected)
    assert symbol in prompts[0]
    assert result["conversion_provenance"][symbol] == bindings[symbol]


def test_cross_dimension_and_unqualified_ounces_are_not_assumed():
    assert quantity_conversion_bindings("Convert 3 kg to litres") == {}
    assert quantity_conversion_bindings("Convert 3 ounces to grams") == {}
    assert quantity_conversion_bindings("Convert 3 marketing packages into grams") == {}
    assert quantity_conversion_bindings("Convert 3 barrels of fluid to litres") == {}
    assert quantity_conversion_bindings("Convert 3 gallons to litres") == {}
    assert quantity_conversion_bindings("Convert 3 tons to kg") == {}


def test_us_gallons_are_explicit_and_not_inferred_from_another_unit():
    assert quantity_conversion_bindings("Convert 3 US gallons to litres")["unit_gal_to_litre"]["factor"] == pytest.approx(3.785411784)


def test_model_literal_still_cannot_substitute_for_a_named_conversion():
    text = "Convert 2 troy ounces of platinum to grams."
    result = _quantitative_run(ConductorNode(node_id="literal", operation="quantitative_reasoning",
        request_text=text, arguments={"clause": text}), NodeContext(
        shared_context=extract_shared_context(text), run_generation=lambda *_: json.dumps({
            "steps": [{"label": "converted", "expression": "fact_1 * 31.1035"}],
        })))
    assert not result["values"]
    assert "31.1035" in result["cannot_determine"][0]
