"""Typed computation -> recovered table renderer, not model-authored cells."""
import json
from pathlib import Path

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_expand, _quantitative_render, _quantitative_run
from core.conductor.registry import NodeContext
from core.conductor.shared_context import extract_shared_context

ORIGINAL = (Path(__file__).parent / "fixtures/portfolio_last_run.txt").read_text()
NOVEL = (
    "A workshop has 840 metres of fabric. Reserve 15% for samples. "
    "Calculate the reserved and remaining lengths and present them in a table."
)


@pytest.mark.parametrize("text,recipes,expected", [
    (ORIGINAL, [
        ("GBP budget", "fact_1 * fact_2_share"),
        ("Silver budget", "fact_1 * fact_3_share * fact_4_share"),
        ("Platinum budget", "fact_1 * fact_3_share * fact_5_share"),
    ], [3125, 5156.25, 4218.75]),
    (NOVEL, [("Reserved", "fact_1 * fact_2_share"),
             ("Remaining", "fact_1 * (1 - fact_2_share)")], [126, 714]),
])
def test_executed_multi_result_uses_table_without_reauthoring(text, recipes, expected):
    context = extract_shared_context(text)
    node = ConductorNode(node_id="table", operation="quantitative_reasoning",
                         request_text=text, arguments=_quantitative_expand(text, context)[0])
    result = _quantitative_run(node, NodeContext(
        shared_context=context, run_generation=lambda *_: json.dumps({"steps": [
            {"label": label, "expression": expression} for label, expression in recipes
        ]})))
    assert not result["cannot_determine"]
    assert [s["value"] for s in result["steps"]] == pytest.approx(expected)
    rendered = _quantitative_render(node, result)
    rows = [line for line in rendered.splitlines() if line.startswith("|")]
    assert len(rows) == len(expected) + 2
    assert rows[0] == "| Result | Calculation | Value |"
    for row, (label, _), value in zip(rows[2:], recipes, expected, strict=True):
        assert row.startswith(f"| {label} |")
        assert f"{value:,.10g}" in row
    assert "fact_" not in rendered


def test_scalar_render_remains_unchanged():
    node = ConductorNode(node_id="scalar", operation="quantitative_reasoning", request_text="ignored")
    assert _quantitative_render(node, {"steps": [
        {"label": "total", "expression": "2 + 3", "value": 5, "unit": "kg"},
    ]}) == "total: 2 + 3 = 5 kg"


def test_failed_steps_and_unknowns_remain_disclosed_beside_table():
    node = ConductorNode(node_id="partial", operation="quantitative_reasoning", request_text="ignored")
    rendered = _quantitative_render(node, {"steps": [
        {"label": "A", "expression": "2 + 3", "value": 5},
        {"label": "B", "expression": "2 * 3", "value": 6},
    ], "cannot_determine": ["C: missing starting quantity"]})
    assert "| A |" in rendered and "| B |" in rendered
    assert "not determined: C: missing starting quantity" in rendered


def test_table_cells_cannot_create_extra_rows_or_columns():
    node = ConductorNode(node_id="escaped", operation="quantitative_reasoning", request_text="ignored")
    rendered = _quantitative_render(node, {"steps": [
        {"label": "A|B\nC", "expression": "2 + 3", "value": 5},
        {"label": "D", "expression": "2 * 3", "value": 6},
    ]})
    assert "A\\|B C" in rendered
    assert len(rendered.splitlines()) == 4
