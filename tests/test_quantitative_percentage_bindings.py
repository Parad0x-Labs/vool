"""Percentage shares have explicit semantics without changing raw fact identities."""
import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_expand, _quantitative_render, _quantitative_run
from core.conductor.registry import NodeContext
from core.conductor.shared_context import (
    UngroundedExpressionError,
    evaluate_grounded_expression,
    extract_shared_context,
    render_briefing,
)


@pytest.mark.parametrize("text,total,percent,expected", [
    ((Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text(), 12500, 25, 3125),
    ("A kiln receives 840 kg of clay. Calculate the 12.5% discarded and the remainder.", 840, 12.5, 105),
])
def test_share_binding_is_consistent_from_briefing_through_render(text, total, percent, expected):
    context = extract_shared_context(text)
    amount = next(f for f in context.facts if f.value == total)
    rate = next(f for f in context.facts if f.value == percent and f.unit_kind == "percent")
    share = rate.label + "_share"
    expression = f"{amount.label} * {share}"
    assert f"{share} = {percent / 100:g}" in render_briefing(context)
    assert evaluate_grounded_expression(expression, facts=context.facts) == expected
    # Existing recipes retain their raw-magnitude meaning.
    assert evaluate_grounded_expression(f"{amount.label} * {rate.label} / 100", facts=context.facts) == expected
    arguments = _quantitative_expand(text, context)[0]
    assert share not in arguments["fact_values"]
    node = ConductorNode(node_id="percentage", operation="quantitative_reasoning",
                         request_text=text, arguments=arguments)
    result = _quantitative_run(node, NodeContext(shared_context=context,
        run_generation=lambda _s, _p: json.dumps({"steps": [
            {"label": "allocated", "expression": expression},
        ]})))
    assert result["values"]["allocated"] == expected
    assert result["expression_bindings"][share] == percent / 100
    rendered = _quantitative_render(node, result)
    assert share not in rendered
    assert f"{total:,} * {percent / 100:g}" in rendered


def test_only_percentage_facts_authorize_share_names():
    context = extract_shared_context("Calculate 12% of 840 crates.")
    ordinary = next(f for f in context.facts if f.value == 840)
    with pytest.raises(UngroundedExpressionError):
        evaluate_grounded_expression(f"{ordinary.label}_share + 1", facts=context.facts)


def test_upstream_cannot_redefine_a_percentage_share():
    context = extract_shared_context("Calculate 12% of 840 crates.")
    rate = next(f for f in context.facts if f.unit_kind == "percent")
    assert evaluate_grounded_expression(f"840 * {rate.label}_share", facts=context.facts,
        symbols={rate.label + "_share": 900}) == pytest.approx(100.8)
