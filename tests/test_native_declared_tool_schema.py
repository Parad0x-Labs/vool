"""The provider receives the executor's declared schema without losing its guidance."""
from copy import deepcopy

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions, parse_native_tool_calls


def test_runtime_coding_step_preserves_inner_tool_contract():
    from core.runtime_execution_tools import runtime_execution_tool_specs
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = runtime_tool_contract_map()["code.task.step"]
    spec = next(row for row in runtime_execution_tool_specs() if row["intent"] == contract.intent)
    definition, = build_cloud_tool_definitions([spec])
    assert definition.parameters == contract.json_schema
    assert "workspace.read_file" in definition.parameters["properties"]["intent"]["description"]
    assert "required" in definition.parameters
    assert not definition.strict  # Arbitrary contract schemas do not promise OpenAI strict form.


def test_declared_nested_constraints_survive_and_are_not_mutated():
    schema = {
        "type": "object", "required": ["change"], "additionalProperties": False,
        "properties": {"change": {"type": "object", "required": ["operation"],
            "additionalProperties": False, "properties": {
                "operation": {"type": "string", "enum": ["inspect", "replace"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5},
            }}},
    }
    before = deepcopy(schema)
    definition, = build_cloud_tool_definitions([{
        "intent": "example.change", "description": "Inspect or replace a fixture.",
        "arguments": {"change": "object"}, "json_schema": schema,
    }])
    assert definition.parameters == schema
    definition.parameters["properties"]["change"]["properties"]["limit"]["maximum"] = 9
    assert schema == before


def test_legacy_optional_descriptor_still_accepts_omission():
    definition, = build_cloud_tool_definitions([{
        "intent": "example.inspect", "arguments": {"path": "string", "limit": "integer optional"},
    }])
    calls = parse_native_tool_calls([{"function": {
        "name": definition.name, "arguments": {"path": "sample.txt"},
    }}], definitions=(definition,))
    assert calls[0].arguments == {"path": "sample.txt"}
    assert definition.strict


def test_plugin_projection_keeps_its_registered_schema(monkeypatch):
    from dataclasses import replace

    from core.execution.capabilities import _plugin_tool_specs
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = replace(runtime_tool_contract_map()["code.task.step"],
                       intent="fixture.perform", source="plugin:fixture")
    monkeypatch.setattr("core.runtime_flags.flag_enabled", lambda name: True)
    monkeypatch.setattr("core.tool_registry.registered_tools", lambda: [contract])
    monkeypatch.setattr("core.execution.capabilities._plugin_available", lambda source: True)
    definition, = build_cloud_tool_definitions(_plugin_tool_specs())
    assert definition.parameters == contract.json_schema


def test_conflicting_declared_contracts_are_not_silently_merged():
    base = {"intent": "fixture.inspect", "arguments": {"path": "string"}}
    with pytest.raises(ValueError, match="conflicting declared schemas"):
        build_cloud_tool_definitions([
            {**base, "json_schema": {"type": "object", "required": ["path"]}},
            {**base, "json_schema": {"type": "object", "required": []}},
        ])
