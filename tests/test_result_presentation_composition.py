"""A proven result view may replace its covered blocks, never unrelated work."""
import json
from dataclasses import replace

import pytest

from core.conductor.compose import compose_answer
from core.conductor.product_decision import ExecutionReport, reduce_execution_report
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from tests.test_result_presentation_operation import NOVEL, OWNER, plan_for, recipes_for


def execute(text, *, selected=None, bad_explanation=False):
    plan = plan_for(text)
    if selected is not None:
        nodes = list(plan.nodes)
        nodes[-1] = replace(nodes[-1], depends_on=tuple(nodes[i].node_id for i in selected))
        plan = replace(plan, graph=type(plan.graph)(nodes=tuple(nodes)))
    recipes = iter(recipes_for(text))
    def generate(system, prompt):
        if "result-presentation.v2" in system:
            return json.dumps({"explanation": ["Invented 9999."] if bad_explanation else ["The values preserve the stated allocation."]})
        return json.dumps(next(recipes))
    outcomes = run_conductor_plan(plan, context=NodeContext(run_generation=generate))
    decision = reduce_execution_report(ExecutionReport(
        bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
        planned_node_ids=tuple(n.node_id for n in plan.nodes), requirement_nodes=plan.requirement_nodes))
    return plan, outcomes, decision


@pytest.mark.parametrize("text,working", [(OWNER, "12,500 * 0.25"), (NOVEL, "360 * 0.4")], ids=["owner", "new-warehouse"])
def test_aggregate_replaces_only_proven_blocks_and_preserves_working(text, working):
    plan, outcomes, decision = execute(text)
    before = decision.to_dict()
    answer = compose_answer(plan, outcomes, decision)
    assert answer.text.count("| Result |") == 1
    assert working in answer.text
    for outcome in outcomes[:-1]:
        node_id = outcome.node.node_id
        assert outcome.rendered not in answer.text.split("\n\n")
        assert node_id in answer.answered_node_ids
        assert answer.provenance[node_id] in answer.text
        assert answer.provenance["represented_by:" + node_id] == outcomes[-1].node.node_id
    assert decision.to_dict() == before


@pytest.mark.parametrize("text", [OWNER, NOVEL], ids=["owner", "new-warehouse"])
def test_disabling_representation_restores_duplicate_blocks(text, monkeypatch):
    from core.conductor import compose
    from core.conductor.registry import operation_spec

    plan, outcomes, decision = execute(text)
    monkeypatch.setattr(compose, "operation_spec", lambda name: replace(
        operation_spec(name), represented_dependency_segments=None))
    answer = compose_answer(plan, outcomes, decision)
    assert answer.text == "\n\n".join(o.rendered for o in outcomes)
    assert all(o.rendered in answer.text for o in outcomes)


def test_selective_summary_keeps_the_unselected_calculation():
    plan, outcomes, decision = execute(NOVEL, selected=(0,))
    answer = compose_answer(plan, outcomes, decision)
    assert outcomes[0].rendered not in answer.text
    assert outcomes[1].rendered in answer.text
    assert "represented_by:" + outcomes[1].node.node_id not in answer.provenance


@pytest.mark.parametrize("text", [OWNER, NOVEL], ids=["owner", "new-warehouse"])
def test_failed_explanation_keeps_one_faithful_table_and_the_failure(text):
    plan, outcomes, decision = execute(text, bad_explanation=True)
    assert outcomes[-1].partially_fulfilled
    answer = compose_answer(plan, outcomes, decision)
    assert answer.text.count("| Result |") == 1
    assert "no valid result-bound content" in answer.text
    assert decision.disposition.value != "fulfilled"
    for source in outcomes[:-1]:
        assert answer.provenance[source.node.node_id] in answer.text
        assert answer.provenance["represented_by:" + source.node.node_id] == outcomes[-1].node.node_id


def test_changed_summary_rows_cannot_authorize_hiding_the_originals():
    plan, outcomes, decision = execute(NOVEL)
    outcomes[-1].result["rows"][0]["value"] = 12345
    answer = compose_answer(plan, outcomes, decision)
    assert all(o.rendered in answer.text for o in outcomes[:-1])
