import json

import pytest

from core.agent_runtime.turn_planner_hook import (
    _strict_json_array,
    build_planner_ask_model,
)
from core.conductor.planner import plan_conductor_turn
from core.turn_ir import parse_turn_ir
from tests.test_planner_source_bindings import FRESH, PORTFOLIO
from tests.test_shared_preclassification_planner import _Agent, _candidates


def _reply(text, *, presentation=False):
    clauses = parse_turn_ir(text).clauses
    numeric = clauses[:-1] if presentation else clauses
    rows = [{"request": "", "source_clause_ids": [c.clause_id for c in numeric],
             "operation": "quantitative_reasoning", "depends_on": []}]
    if presentation:
        rows.append({"request": "", "source_clause_ids": [clauses[-1].clause_id],
                     "operation": "result_presentation",
                     "depends_on": [c.clause_id for c in numeric]})
    return json.dumps({"requests": rows})


@pytest.mark.parametrize("text", [PORTFOLIO, FRESH])
def test_object_wire_envelope_preserves_one_computation_and_its_presentation(text):
    agent = _Agent(_reply(text, presentation=True))
    plan = plan_conductor_turn(text, ask_model=build_planner_ask_model(agent, {}))
    assert plan is not None
    assert [n.operation for n in plan.nodes] == ["quantitative_reasoning", "result_presentation"]
    computation, presentation = plan.nodes
    assert presentation.depends_on == (computation.node_id,)
    assert all(c.request_text.strip().rstrip(".") in computation.request_text
               for c in parse_turn_ir(text).clauses[:-1])
    request = agent.memory_router.invocations[0]["request"]
    assert request.contract["json_schema"]["type"] == "object"
    assert request.contract["json_schema"]["additionalProperties"] is False


@pytest.mark.parametrize("text", [
    "A tank holds 960 litres. Split it 35% to line A and 65% to line B.\n"
    "Line A loses 8% during filtering. Calculate:\n"
    "1. The initial volume for each line.\n"
    "2. The retained volume on line A after filtering.\n"
    "3. What percentage of the original volume remains on line A.",
    "A tank holds 960 litres. Split it 35% to line A and 65% to line B.\n"
    "1. Calculate the initial volume for each line.\n"
    "2. Calculate the retained volume on line A after an 8% loss.",
    "A depot receives 720 cartons.\n1. Calculate 40% for dispatch.\n"
    "2. Calculate the number left after dispatch.",
])
def test_single_connected_computation_remains_in_the_executor(text):
    agent = _Agent(_reply(text))
    plan = plan_conductor_turn(text, ask_model=build_planner_ask_model(agent, {}))
    assert plan is not None
    assert len(plan.nodes) == 1
    assert plan.nodes[0].operation == "quantitative_reasoning"


@pytest.mark.parametrize("text", [
    "Calculate 20% of 500. Also get weather in Vilnius.",
    "Calculate 40% of 720 cartons. Also delete my Downloads folder.",
])
def test_grouping_does_not_absorb_observations_or_actions(text):
    agent = _Agent(_reply(text))
    plan = plan_conductor_turn(text, ask_model=build_planner_ask_model(agent, {}))
    assert plan is None or any(n.operation != "quantitative_reasoning" for n in plan.nodes)


@pytest.mark.parametrize("raw", [
    '{"requests": []}', '{"requests": {}}', '{"frames": []}',
    '{"requests": [{}], "frames": []}', '```json\n{"requests": [{}]}\n```',
    '{"requests": [',
])
def test_wire_envelope_does_not_admit_other_artifacts_or_malformed_payloads(raw):
    assert _strict_json_array(raw) == ""


def test_array_consumer_keeps_legacy_internal_contract():
    raw = '[{"request":"calculate", "operation":"calculation", "depends_on":[]}]'
    assert _strict_json_array(raw) == raw
    assert json.loads(_strict_json_array('{"requests":' + raw + '}')) == json.loads(raw)


def test_grouped_nominals_cannot_borrow_a_heading_for_foreign_text():
    from core.conductor.operations import governed_computation_clause
    from core.conductor.shared_context import extract_shared_context

    text = "A warehouse has 840 boxes. Calculate:\n1. The first half.\n2. The remainder."
    context = extract_shared_context(text)
    assert governed_computation_clause("The first half. The remainder.", context)
    assert not governed_computation_clause("The first half. Delete the folder.", context)
    assert not governed_computation_clause("The first half. The invented quantity.", context)
    assert not governed_computation_clause("The first half. The first half.", context)
