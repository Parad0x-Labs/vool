"""RELAY's half of the TOOLSMITH coordination: verify the schema-RENDERING boundary, not
TOOLSMITH's implementation.

TOOLSMITH's branch (agent/toolsmith-core-tools @ c51e82e6, `fix(tools): declare expected_hash/hash
fields in tool contracts`) changes core/runtime_tool_contracts.py -- NOT touched here, per the
operator's explicit instruction -- to add:

  workspace.write_file       input:  + expected_hash (string, optional)
                              output: + before_hash, after_hash
  workspace.replace_in_file  input:  + expected_hash (string, optional)
                              output: + before_hash, after_hash
  workspace.read_file        output: + hash
  machine.read_file          output: + hash
  workspace.apply_unified_diff (the "patch" tool)
                              output: paths/engine -> paths, changed_paths,
                                      engine: git_apply|python_fallback|patch,
                                      status_on_failure: invalid_patch_syntax|stale_base|
                                                         target_missing|apply_failed|no_change

The tool CONTRACTS RELAY renders into a provider's wire schema come from
`core.tool_intent_executor.runtime_tool_specs()`, which reads `core.runtime_tool_contracts` --
so once that branch lands on `main`, these new fields flow through the EXACT SAME rendering code
already exercised here (`build_cloud_tool_definitions` / `openai_tool_payload` in
core/cloud_tool_call_contract.py) with no RELAY-side change required. This file proves that
rendering path handles the new shape correctly using a spec that mirrors the real diff exactly, so
the coordination claim is checked now rather than assumed.
"""
from __future__ import annotations

import pytest

from core.cloud_tool_call_contract import build_cloud_tool_definitions, openai_tool_payload

# Reproduces workspace.write_file's new input_schema exactly (core/runtime_tool_contracts.py,
# TOOLSMITH branch): {"path": "string", "content": "string", "expected_hash": "string optional (...)"}
WRITE_FILE_SPEC = {
    "intent": "workspace.write_file",
    "description": "Create directories, write files, and replace text in the active workspace.",
    "arguments": {
        "path": "string",
        "content": "string",
        "expected_hash": "string optional (sha256 of the content read_file returned; write fails with status=stale_base if the file changed since)",
    },
}

REPLACE_IN_FILE_SPEC = {
    "intent": "workspace.replace_in_file",
    "description": "Replace text in a workspace file.",
    "arguments": {
        "path": "string",
        "old_text": "string",
        "new_text": "string",
        "replace_all": "boolean optional",
        "expected_hash": "string optional (sha256 of the content read_file returned; edit fails with status=stale_base if the file changed since)",
    },
}


def test_expected_hash_is_optional_in_the_rendered_schema() -> None:
    """The core compatibility claim: an old caller that never learned about expected_hash must
    still be able to call write_file. This runtime's OWN convention (see the next test) encodes
    that as a nullable type with the key still listed in `required` -- required by OpenAI's
    strict-mode contract, which mandates every property appear in `required` -- so "optional"
    here means "the internal validator accepts its absence," proven by
    test_a_call_omitting_expected_hash_still_validates_against_the_rendered_schema below, not
    "absent from the rendered `required` array." """
    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    payload = openai_tool_payload(definitions[0])
    schema = payload["function"]["parameters"]

    assert "expected_hash" in schema["properties"]
    assert schema["properties"]["expected_hash"]["type"] == ["string", "null"]
    assert "path" in schema["required"]
    assert "content" in schema["required"]


def test_expected_hash_nullable_type_lets_a_strict_schema_accept_its_absence() -> None:
    """This runtime encodes "optional" as a nullable type (["string","null"]) with the key still
    present in `required` -- NOT by omitting it from `required` (see _parameters_for_intent's own
    comment: "Strict OpenAI schemas require every property in required; optionality is encoded by
    null."). Confirm expected_hash follows that same convention, not a different, untested one."""
    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    schema = openai_tool_payload(definitions[0])["function"]["parameters"]

    prop = schema["properties"]["expected_hash"]
    assert prop["type"] == ["string", "null"]


def test_replace_in_file_expected_hash_is_also_optional() -> None:
    definitions = build_cloud_tool_definitions([REPLACE_IN_FILE_SPEC])
    schema = openai_tool_payload(definitions[0])["function"]["parameters"]

    assert schema["properties"]["expected_hash"]["type"] == ["string", "null"]


def test_no_provider_dialect_marks_expected_hash_required_differently_than_the_other() -> None:
    """Ollama and OpenRouter/cloud both render through this SAME function
    (adapters/openai_compatible_adapter.py's _native_tools and _ollama_native_tools both call
    openai_tool_payload on the identical CloudToolDefinition tuple) -- there is no second,
    independent schema-rendering path that could diverge and accidentally treat expected_hash
    differently (e.g. mark it required on one lane, nullable-optional on the other)."""
    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    cloud_schema = openai_tool_payload(definitions[0])["function"]["parameters"]
    # The Ollama lane calls openai_tool_payload on the same definitions with only `strict`
    # overridden (adapters/openai_compatible_adapter.py:_ollama_native_tools /
    # _build_ollama_payload) -- reproduced directly here rather than importing the adapter, to
    # keep this test scoped to the rendering function RELAY owns.
    from dataclasses import replace

    ollama_schema = openai_tool_payload(replace(definitions[0], strict=False))["function"]["parameters"]

    assert cloud_schema["required"] == ollama_schema["required"]
    assert cloud_schema["properties"]["expected_hash"] == ollama_schema["properties"]["expected_hash"]


def test_local_and_cloud_schemas_represent_the_identical_contract() -> None:
    """The explicit ask: local (Ollama) and OpenRouter schemas must represent the SAME contract.
    They differ only in `strict` (Ollama accepts but does not enforce it -- see the adapter's own
    comment); the argument schema itself -- names, types, required set -- must be byte-identical."""
    from dataclasses import replace

    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC, REPLACE_IN_FILE_SPEC])
    for definition in definitions:
        cloud_payload = openai_tool_payload(definition)
        local_payload = openai_tool_payload(replace(definition, strict=False))
        assert cloud_payload["function"]["parameters"] == local_payload["function"]["parameters"]
        assert cloud_payload["function"]["name"] == local_payload["function"]["name"]
        assert cloud_payload["function"]["strict"] is True
        assert local_payload["function"]["strict"] is False  # the one, documented, deliberate difference


def test_a_call_omitting_expected_hash_still_validates_against_the_rendered_schema() -> None:
    """End-to-end proof an OLD caller (one that never learned about expected_hash) is not broken:
    a tool call with no expected_hash key at all must pass this runtime's own argument validator,
    not just "look optional" in the rendered JSON schema."""
    from core.cloud_tool_call_contract import parse_native_tool_calls

    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    native_name = definitions[0].name
    raw_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": native_name, "arguments": '{"path": "x.py", "content": "print(1)"}'},
        }
    ]
    parsed = parse_native_tool_calls(raw_calls, definitions=definitions)
    assert parsed[0].arguments == {"path": "x.py", "content": "print(1)"}


def test_a_call_that_does_supply_expected_hash_also_validates() -> None:
    """Control: a NEW caller that does supply expected_hash must also validate correctly."""
    from core.cloud_tool_call_contract import parse_native_tool_calls

    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    native_name = definitions[0].name
    raw_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": native_name,
                "arguments": '{"path": "x.py", "content": "print(1)", "expected_hash": "abc123"}',
            },
        }
    ]
    parsed = parse_native_tool_calls(raw_calls, definitions=definitions)
    assert parsed[0].arguments["expected_hash"] == "abc123"


def test_a_missing_non_optional_argument_still_fails_validation() -> None:
    """Control for the _validate_arguments fix itself: this must NOT have turned every argument
    permissive -- a genuinely non-optional key (no "null" in its allowed types) that is missing
    must still raise. `content` has no "optional" marker in WRITE_FILE_SPEC, so its type has no
    "null" member and omitting it must still fail."""
    from core.cloud_tool_call_contract import parse_native_tool_calls

    definitions = build_cloud_tool_definitions([WRITE_FILE_SPEC])
    native_name = definitions[0].name
    raw_calls = [{"id": "call-1", "type": "function", "function": {"name": native_name, "arguments": '{"path": "x.py"}'}}]
    with pytest.raises(ValueError, match="missing required key: content"):
        parse_native_tool_calls(raw_calls, definitions=definitions)


def test_replace_in_file_omitting_expected_hash_and_replace_all_both_validate() -> None:
    """Both of replace_in_file's optional arguments (expected_hash, replace_all) may be omitted
    together -- not just the one this coordination pass happens to be about."""
    from core.cloud_tool_call_contract import parse_native_tool_calls

    definitions = build_cloud_tool_definitions([REPLACE_IN_FILE_SPEC])
    native_name = definitions[0].name
    raw_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": native_name, "arguments": '{"path": "x.py", "old_text": "a", "new_text": "b"}'},
        }
    ]
    parsed = parse_native_tool_calls(raw_calls, definitions=definitions)
    assert parsed[0].arguments == {"path": "x.py", "old_text": "a", "new_text": "b"}
