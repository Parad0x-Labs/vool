"""Composition must preserve independently rendered blocks in the shipped UI."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.conductor.compose import compose_answer
from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.operations import _quantitative_render
from core.conductor.product_decision import ExecutionReport, reduce_execution_report
from tests.test_chat_export_copy_ui import SHIM, _run_node, _slice, requires_node
from tests.test_quantitative_table_ui import Cells

ROOT = Path(__file__).resolve().parents[1]


def rendered_outcomes(case):
    if case == "owner":
        evidence = json.loads((ROOT / "tests/fixtures/model_orchestration_boundary/portfolio-ling-3.0-flash-scheduled-311cd939.json").read_text())
        for index, row in enumerate(evidence["outcomes"][:4]):
            args = evidence["component_plan"][index]["arguments"]
            node = ConductorNode(row["node_id"], row["operation"], args["clause"], arguments=args)
            yield NodeOutcome(node, state=NodeLifecycle.SUCCEEDED, result=row["result"],
                              rendered=_quantitative_render(node, row["result"]))
    else:
        for name, rows in [("Incoming stock", [("Pallets", 24), ("Crates", 72)]),
                           ("Outgoing stock", [("Cartons", 18), ("Bundles", 54)])]:
            node = ConductorNode(name, "quantitative_reasoning", name)
            result = {"steps": [{"label": label, "expression": str(value), "value": value}
                                 for label, value in rows]}
            yield NodeOutcome(node, state=NodeLifecycle.SUCCEEDED, result=result,
                              rendered=_quantitative_render(node, result))


@requires_node
@pytest.mark.parametrize("case,count", [("owner", 4), ("new-warehouse", 2)])
def test_composed_tables_keep_independent_headers_and_every_cell(case, count):
    outcomes = tuple(rendered_outcomes(case))
    plan = SimpleNamespace(nodes=tuple(o.node for o in outcomes))
    decision = reduce_execution_report(ExecutionReport(
        node_outcomes=outcomes, planned_node_ids=tuple(o.node.node_id for o in outcomes)))
    answer = compose_answer(plan, outcomes, decision)
    source = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)

    def render(text):
        return _run_node(SHIM + source + "\nconst el = new El(); renderRichText(el,"
                         + json.dumps(text) + "); process.stdout.write(el.innerHTML);")

    html = render(answer.text)
    assert html.count("<table") == count
    actual = Cells()
    actual.feed(html)
    expected = []
    for outcome in outcomes:
        cells = Cells()
        cells.feed(render(outcome.rendered))
        expected.extend(cells.rows)
        assert answer.provenance[outcome.node.node_id] == outcome.rendered
    assert actual.rows == expected
    assert answer.answered_node_ids == tuple(o.node.node_id for o in outcomes)
