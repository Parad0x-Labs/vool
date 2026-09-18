"""Calculation punctuation is data, never Markdown emphasis."""
import json

import pytest

from core.conductor.node import ConductorNode
from core.conductor.operations import _quantitative_render
from tests.test_chat_export_copy_ui import SHIM, _run_node, _slice, requires_node
from tests.test_quantitative_table_ui import Cells


@requires_node
@pytest.mark.parametrize("expression,value", [
    ("12,500 * 0.75 * 0.55", 5156.25),
    ("960 * 0.35 * 0.92", 309.12),
])
def test_computed_expression_survives_visible_table_render(expression, value):
    node = ConductorNode(node_id="calculation", operation="quantitative_reasoning", request_text="")
    md = _quantitative_render(node, {"steps": [
        {"label": "amount", "expression": expression, "value": value},
        {"label": "control", "expression": "2 + 3", "value": 5},
    ]})
    script = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    html = _run_node(SHIM + script + "\nconst el = new El(); renderRichText(el,"
                     + json.dumps(md) + "); process.stdout.write(el.innerHTML);")
    parser = Cells()
    parser.feed(html)
    assert parser.rows[1] == ["amount", expression, f"{value:,.10g}"]
    assert parser.rows[2] == ["control", "2 + 3", "5"]
    assert "<em>" not in html
