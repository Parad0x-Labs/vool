"""Operation-owned schemas reach the same reserved provider call and local validator."""
import json
from types import SimpleNamespace

import pytest

from core.agent_runtime.turn_planner_hook import build_conductor_ask_model, build_pinned_paid_turn_scope
from core.conductor.node import ConductorNode
from core.conductor.registry import NodeContext
from core.conductor.result_presentation import _render, _run
from tests import test_pinned_paid_conductor_generation as paid_helpers

reservation_recorder = paid_helpers.reservation_recorder


@pytest.mark.parametrize("clause,label,value,unit", [
    ("Present the result in a compact table and finish with a short explanation of the final portfolio.",
     "GBP after FX fee", 2362.80487804878, "GBP"),
    ("Show the refrigerated shipment count in a table and briefly explain the outcome.",
     "Refrigerated parcels", 144, "parcels"),
], ids=["owner-presentation", "new-cold-chain"])
def test_presentation_schema_reaches_reserved_provider(reservation_recorder, clause, label, value, unit):
    agent, context = paid_helpers._Agent(), paid_helpers._owner_context()
    scope = build_pinned_paid_turn_scope(agent, context)
    ask = build_conductor_ask_model(agent, context, paid_scope=scope)
    original = agent.memory_router._invoke_manifest

    def invoke(**kwargs):
        original(**kwargs)
        request = kwargs["request"]
        assert request.output_mode == kwargs["output_mode"] == "json_object"
        schema = request.contract["json_schema"]
        assert schema["required"] == ["explanation"]
        allowed = schema["properties"]["explanation"]["items"]["properties"]["ref"]["enum"]
        assert allowed == [None, "dependency_1_value_1"]
        assert json.loads(request.prompt)["results"][0]["value"] == value
        return None, SimpleNamespace(output_text=json.dumps({
            "explanation": [{"text": "The computed outcome is ", "ref": allowed[1]}]})), None

    agent.memory_router._invoke_manifest = invoke
    node = ConductorNode("present", "result_presentation", clause,
                         arguments={"clause": clause, "explanation_requested": True}, depends_on=("calculated",))
    ctx = NodeContext(run_generation=ask,
        run_structured_generation=lambda system, prompt, schema: ask(system, prompt, json_schema=dict(schema)),
        dependency_results={"calculated": {"steps": [{"label": label, "value": value,
            "unit": unit, "unit_dimensions": {unit: 1}, "unit_authority": "source_expression"}]}})
    result = _run(node, ctx)
    assert result["explanation"] == [{"text": "The computed outcome is "}, {"ref": "dependency_1_value_1"}]
    assert not result["cannot_determine"]
    assert len(reservation_recorder) == len(agent.memory_router.invocations) == 1
    assert context["pinned_paid_helper_receipts"][0]["result"] == "completed"


def test_plain_calls_keep_plain_contract_and_share_the_same_callback(reservation_recorder):
    agent, context = paid_helpers._Agent(), paid_helpers._owner_context()
    ask = build_conductor_ask_model(agent, context, paid_scope=build_pinned_paid_turn_scope(agent, context))
    assert ask("Explain the weather", "Why does fog form?") == "node answer"
    request = agent.memory_router.invocations[0]["request"]
    assert request.reasoning_mode == "auto"
    assert request.output_mode == "plain_text"
    assert request.contract == {}


@pytest.mark.parametrize("clause", ["Explain the portfolio table", "Explain the refrigerated shipment totals"])
def test_provider_cutoff_reaches_presentation_as_a_typed_failure(reservation_recorder, clause):
    agent, context = paid_helpers._Agent(), paid_helpers._owner_context()
    ask = build_conductor_ask_model(agent, context, paid_scope=build_pinned_paid_turn_scope(agent, context))
    agent.memory_router._invoke_manifest = lambda **_: (None, SimpleNamespace(
        output_text="", finish_reason="length", usage={}, constraint_result={"response_control": {
            "provider_completion": {"final": {"incomplete": True, "reasons": ["provider_finish_reason:length"]}}}}), None)
    node = ConductorNode("present", "result_presentation", clause,
        arguments={"clause": clause, "explanation_requested": True}, depends_on=("calculated",))
    result = _run(node, NodeContext(
        run_structured_generation=lambda s, p, schema: ask(s, p, json_schema=schema),
        dependency_results={"calculated": {"steps": [{"label": "Count", "value": 144}]}}))
    assert result["rows"] and not result["explanation"]
    assert result["cannot_determine"] == ["The provider stopped before completing the requested explanation."]
    assert context["pinned_paid_helper_receipts"][0]["result"] == "failed:provider_output_incomplete"


def test_schema_does_not_replace_local_reference_validation():
    node = ConductorNode("present", "result_presentation", "Explain this table",
        arguments={"clause": "Explain this table", "explanation_requested": True}, depends_on=("calculated",))
    ctx = NodeContext(run_structured_generation=lambda *_: '{"explanation":[{"ref":"invented"}]}',
        dependency_results={"calculated": {"steps": [{"label": "Count", "value": 144}]}})
    result = _run(node, ctx)
    assert not result["explanation"]
    assert result["cannot_determine"]


@pytest.mark.parametrize("part", [{"text": "Invented 99", "ref": None},
                                  {"text": "", "ref": None}, {"text": "Total", "ref": "invented"}])
def test_uniform_segments_keep_numeric_and_reference_guards(part):
    node = ConductorNode("present", "result_presentation", "Explain this table",
        arguments={"clause": "Explain this table", "explanation_requested": True}, depends_on=("calculated",))
    ctx = NodeContext(run_structured_generation=lambda *_: json.dumps({"explanation": [part]}),
        dependency_results={"calculated": {"steps": [{"label": "Count", "value": 144}]}})
    result = _run(node, ctx)
    assert not result["explanation"]
    assert result["cannot_determine"]


@pytest.mark.parametrize("before,value,unit,after,expected", [
    ("The GBP amount is", 2362.8049, "GBP", ".", "is 2,362.8049 GBP."),
    ("The refrigerated shipment count in parcels is", 144, "parcels", ".", "is 144 parcels."),
    ("The temperature change is", -4, "K", ".", "is -4 K."),
    ("(", 84, "EUR", ")", "(84 EUR)"),
])
def test_explanation_segments_cannot_glue_words_to_values(before, value, unit, after, expected):
    result = {"rows": [{"ref": "r", "label": "Result", "calculation": "", "value": value, "unit": unit}],
              "explanation": [{"text": before}, {"ref": "r"}, {"text": after}], "cannot_determine": []}
    assert expected in _render(None, result)
