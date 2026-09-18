"""SWITCHBOARD Repair 6: two load-bearing guards SWITCHBOARD independently disabled (mutation
testing against reviewed tip 18625e94) with the FULL SUITE staying green throughout.

GUARD A -- core/execution_requirements.py::assert_envelope_carries_tools's first check
(`offered_tool_count <= 0`). Empirically confirmed before writing this file: deleting that check
entirely leaves ALL 13 tests in tests/test_required_tools_final_boundary.py passing, because every
existing scenario there ALSO trips the function's second check (`not native_tools_in_envelope and
not structured_fallback_in_envelope`) -- when `request.tools` is empty, the built envelope
naturally carries neither native tools nor a fallback either, so check #2 silently backstops check
#1 in every tested case. The two checks are answering genuinely different questions (did the
CALLER offer anything vs. did the BUILT PAYLOAD end up carrying anything), and a caller bug that
offers zero tools while some stale/unrelated payload state still looks tool-shaped is exactly what
check #1 alone catches -- so a test must isolate it by making #1 true and #2 false at the same
time, which no existing test does.

GUARD B -- core/cloud_tool_call_contract.py::_check_not_duplicate's call-id branch. Partially
covered incidentally by tests/test_typed_tool_call_error_lane_equivalence.py (added in this same
repair pass, Repair 2, after SWITCHBOARD's original finding) -- but that test only proves an
exception of the right TYPE is raised, not that the batch fails as a whole (a valid FIRST call must
not silently survive and get returned/dispatched just because only the SECOND entry duplicated it).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.cloud_tool_call_contract import (
    DuplicateToolCallError,
    build_cloud_tool_definitions,
    parse_native_tool_calls,
)
from core.execution_requirements import RequiredToolsNotOfferedError, assert_envelope_carries_tools

TOOLS = build_cloud_tool_definitions(
    [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
)
NATIVE_NAME = TOOLS[0].name


# --- Guard A: offered_tool_count <= 0, isolated from the envelope-shape check ------------------


def test_gate_raises_on_zero_offered_even_when_the_envelope_looks_tool_shaped() -> None:
    """Sets native_tools_in_envelope=True specifically so check #2 (`not native_tools_in_envelope
    and not structured_fallback_in_envelope`) would NOT independently raise -- isolating check #1
    as the only thing standing between "raises" and "silently returns None". If check #1 is ever
    disabled or its condition weakened, THIS call stops raising while every existing test in
    tests/test_required_tools_final_boundary.py keeps passing (confirmed by direct mutation before
    writing this test: removing check #1 left all 13 of those green)."""
    with pytest.raises(RequiredToolsNotOfferedError, match="schema builder produced none"):
        assert_envelope_carries_tools(
            tools_required=True,
            offered_tool_count=0,
            native_tools_in_envelope=True,
            structured_fallback_in_envelope=False,
            lane_name="test-lane",
        )


def test_gate_passes_when_offered_and_envelope_agree() -> None:
    """Control: the ordinary, self-consistent case (something offered, envelope carries it) is
    unaffected by isolating check #1."""
    assert_envelope_carries_tools(
        tools_required=True,
        offered_tool_count=1,
        native_tools_in_envelope=True,
        structured_fallback_in_envelope=False,
        lane_name="test-lane",
    )  # must not raise


def test_cloud_adapter_still_fails_closed_when_the_built_payload_claims_tools_it_was_never_offered() -> None:
    """Production-path version of the isolation above: the REQUEST offered zero tools
    (offered_tool_count computed from `request.tools` is 0), but the adapter's own payload builder
    is made to return a `tools` key anyway (a stand-in for a caching/staleness bug in the builder)
    -- proving the production callsite computes `offered_tool_count` from the request, not merely
    from whether the built payload happens to look tool-shaped, and that check #1 alone is what
    catches the mismatch."""
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id="openrouter-byok:vendor/model",
            model_name="vendor/model",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_path": "/chat/completions", "timeout_seconds": 5.0},
        )
    )
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="run a command",
        output_mode="tool_intent",
        messages=[{"role": "user", "content": "run a command"}],
        tools=(),
        tool_choice="required",
        tools_required=True,
    )
    stale_payload = {"model": "vendor/model", "messages": [], "tools": [{"type": "function", "function": {"name": "stale__leftover"}}]}
    with mock.patch.object(adapter, "_build_openai_payload", return_value=stale_payload), mock.patch(
        "adapters.openai_compatible_adapter.requests.post"
    ) as post:
        with pytest.raises(RequiredToolsNotOfferedError):
            adapter.run_structured_task(request)
    post.assert_not_called()  # never reaches the wire despite the stale-looking payload


# --- Guard B: duplicate call id fails the WHOLE batch, not just the duplicate entry -------------


def test_duplicate_id_fails_the_whole_batch_not_only_the_second_entry() -> None:
    """A valid FIRST call must not silently execute just because the SECOND entry in the same
    batch reused its call id -- the whole batch fails closed, matching every other ToolCallParseError
    subclass's documented all-or-nothing contract."""
    raw_calls = [
        {"id": "dup-1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}},
        {"id": "dup-1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"ls"}'}},
    ]
    with pytest.raises(DuplicateToolCallError):
        parse_native_tool_calls(raw_calls, definitions=TOOLS)


def test_duplicate_id_with_a_valid_third_call_still_fails_the_whole_batch() -> None:
    """Three calls, two sharing an id, one genuinely distinct -- proves the valid THIRD call does
    not silently survive as a partial result either; the function raises, it never returns a
    partial list with the duplicate filtered out."""
    raw_calls = [
        {"id": "dup-2", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}},
        {"id": "distinct-1", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"whoami"}'}},
        {"id": "dup-2", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"ls"}'}},
    ]
    with pytest.raises(DuplicateToolCallError):
        parse_native_tool_calls(raw_calls, definitions=TOOLS)


def test_distinct_call_ids_with_identical_arguments_are_not_duplicates() -> None:
    """Control: a provider legitimately calling the SAME tool with the SAME arguments twice, each
    carrying its own distinct id, must not be rejected -- duplication is judged by id (or, absent
    an id, by name+arguments), never by name+arguments alone when ids are present and different."""
    raw_calls = [
        {"id": "call-a", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}},
        {"id": "call-b", "type": "function", "function": {"name": NATIVE_NAME, "arguments": '{"command":"pwd"}'}},
    ]
    parsed = parse_native_tool_calls(raw_calls, definitions=TOOLS)
    assert len(parsed) == 2
