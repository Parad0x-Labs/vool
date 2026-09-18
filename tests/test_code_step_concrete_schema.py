"""The coding wrapper must offer concrete inner argument fields to native tool decoders."""

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions, openai_tool_payload, parse_native_tool_calls
from core.runtime_execution_tools import runtime_execution_tool_specs


@pytest.mark.parametrize("intent", ["code.task.step", "code.task.propose"])
def test_wire_schema_names_file_command_and_content_fields(intent):
    spec = next(row for row in runtime_execution_tool_specs() if row["intent"] == intent)
    (definition,) = build_cloud_tool_definitions([spec])
    wire = openai_tool_payload(definition)
    inner = wire["function"]["parameters"]["properties"]["arguments"]
    for name in ("path", "command", "content", "expected_hash"):
        assert inner.get("properties", {}).get(name, {}).get("type") == "string", name
    assert inner.get("additionalProperties") is True
    assert not inner.get("required"), "Requirements depend on the selected inner tool."
    args = {"task_id": "ct-example", "intent": "workspace.read_file", "arguments": {"path": "src/other.py"}}
    if intent == "code.task.propose":
        args["rationale"] = "Fix the owning implementation after reproducing its failure."
    (call,) = parse_native_tool_calls(
        [{"function": {"name": definition.name, "arguments": args}}], definitions=(definition,)
    )
    assert call.arguments == args
