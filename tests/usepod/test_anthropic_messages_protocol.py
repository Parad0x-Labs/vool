"""Anthropic Messages as a dialect of the one provider contract: translation both ways, strict stream
finalization, and a round trip through the SAME tool-call validators every OpenAI-compatible lane uses.
All payloads are SYNTHETIC.
"""
from __future__ import annotations

import json

import pytest

from core.anthropic_messages_protocol import (
    AnthropicStreamAssembler,
    AnthropicStreamError,
    IncompleteStreamError,
    ProtocolTranslationError,
    anthropic_response_to_openai,
    normalize_anthropic_usage,
    openai_request_to_anthropic,
)
from core.normalized_provider_result import MalformedProviderResponseError

TOOL = {
    "type": "function",
    "function": {
        "name": "workspace__read_file",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
}


def test_a_built_openai_payload_translates_into_a_messages_request() -> None:
    body = openai_request_to_anthropic(
        {
            "model": "lyra-7b-synth",
            "max_tokens": 700,
            "temperature": 0.2,
            "stream": True,
            "stream_options": {"include_usage": True},
            "stop": ["END"],
            "tools": [TOOL],
            "tool_choice": "required",
            "messages": [
                {"role": "system", "content": "Be exact."},
                {"role": "developer", "content": "Use tools."},
                {"role": "user", "content": "What does notes/synthetic-a.log say?"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "workspace__read_file", "arguments": '{"path": "notes/synthetic-a.log"}'}}]},
                {"role": "tool", "tool_call_id": "call_1", "content": "alpha"},
                {"role": "tool", "tool_call_id": "call_2", "content": [{"type": "text", "text": "beta"}]},
            ],
        }
    )
    assert body["system"] == "Be exact.\n\nUse tools."
    assert body["max_tokens"] == 700 and body["stream"] is True and body["stop_sequences"] == ["END"]
    assert "stream_options" not in body
    assert body["tools"] == [{"name": "workspace__read_file", "input_schema": TOOL["function"]["parameters"], "description": "Read a file"}]
    assert body["tool_choice"] == {"type": "any"}
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"] == [{"type": "tool_use", "id": "call_1", "name": "workspace__read_file", "input": {"path": "notes/synthetic-a.log"}}]
    assert [block["tool_use_id"] for block in body["messages"][2]["content"]] == ["call_1", "call_2"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"response_format": {"type": "json_object"}}, "response_format_not_expressible"),
        ({"reasoning": {"enabled": False}}, "reasoning_control_not_expressible"),
        ({"logprobs": True}, "unsupported_request_fields:logprobs"),
        ({"max_tokens": None}, "max_tokens_required"),
        ({"messages": [{"role": "assistant", "content": "hello"}]}, "first_message_must_be_user"),
        ({"messages": [{"role": "user", "content": "x"}, {"role": "tool", "content": "orphan"}]}, "tool_result_missing_call_id"),
        ({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "http://images.example.test/a.png"}}]}]}, "image_url_not_expressible"),
        ({"messages": [{"role": "system", "content": [{"type": "image_url", "image_url": {"url": "https://x.test/a.png"}}]}, {"role": "user", "content": "x"}]}, "non_text_part_in_system"),
        ({"tool_choice": "required"}, "tool_choice_without_tools"),
        ({"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "t", "arguments": "{nope"}}]}]}, "assistant_tool_call_arguments_not_json"),
    ],
)
def test_what_the_messages_dialect_cannot_carry_is_refused_before_sending(payload, code) -> None:
    base = {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}
    base.update(payload)
    with pytest.raises(ProtocolTranslationError) as caught:
        openai_request_to_anthropic(base)
    assert caught.value.code == code


def test_a_messages_response_becomes_the_openai_shape_the_shared_readers_validate() -> None:
    from adapters.openai_compatible_adapter import _extract_native_openai_tool_result
    from core.cloud_provider_contract import CloudToolDefinition

    data = anthropic_response_to_openai(
        {
            "id": "msg_1",
            "type": "message",
            "model": "lyra-7b-synth",
            "content": [
                {"type": "thinking", "thinking": "private reasoning"},
                {"type": "text", "text": "Reading it."},
                {"type": "tool_use", "id": "toolu_1", "name": "workspace__read_file", "input": {"path": "notes/synthetic-b.log"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7},
        }
    )
    assert data["choices"][0]["finish_reason"] == "tool_calls"
    assert "private reasoning" not in json.dumps(data)
    assert data["usage"] == {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 5, "prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22}
    definition = CloudToolDefinition(
        intent="workspace.read_file", name="workspace__read_file", description="Read a file", parameters=TOOL["function"]["parameters"]
    )
    _text, calls = _extract_native_openai_tool_result(data, definitions=(definition,))
    assert calls[0].intent == "workspace.read_file" and calls[0].arguments == {"path": "notes/synthetic-b.log"}


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"type": "error", "error": {"type": "overloaded_error"}}, "anthropic error body"),
        ({"type": "message", "content": "text"}, "content is not a list"),
        ({"type": "message", "content": [{"type": "server_tool_use", "id": "x"}]}, "was not requested"),
        ({"type": "message", "content": [{"type": "tool_use", "id": "t", "name": "x", "input": "not an object"}]}, "tool_use block is incomplete"),
    ],
)
def test_a_malformed_messages_response_is_a_typed_malformed_response(payload, fragment) -> None:
    with pytest.raises(MalformedProviderResponseError) as caught:
        anthropic_response_to_openai(payload)
    assert fragment in str(caught.value)


def test_missing_usage_keys_stay_missing() -> None:
    assert normalize_anthropic_usage({"output_tokens": 3}) == {"output_tokens": 3, "completion_tokens": 3}
    assert normalize_anthropic_usage(None, {}) == {}


def _sse(kind: str, data: dict) -> list[str]:
    return [f"event: {kind}", f"data: {json.dumps({'type': kind, **data})}", ""]


def _feed(assembler: AnthropicStreamAssembler, lines: list[str]) -> list[str]:
    deltas: list[str] = []
    for line in lines:
        for event in assembler.feed_line(line):
            deltas.append(event["choices"][0]["delta"]["content"])
    return deltas


def _complete_stream() -> list[str]:
    return [
        *_sse("message_start", {"message": {"id": "msg_s", "model": "lyra-7b-synth", "usage": {"input_tokens": 12}}}),
        *_sse("ping", {}),
        *_sse("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
        *_sse("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hel"}}),
        *_sse("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "lo"}}),
        *_sse("content_block_stop", {"index": 0}),
        *_sse("content_block_start", {"index": 1, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "workspace__read_file", "input": {}}}),
        *_sse("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"path": "notes/'}}),
        *_sse("content_block_delta", {"index": 1, "delta": {"type": "input_json_delta", "partial_json": 'synthetic-c.log"}'}}),
        *_sse("content_block_stop", {"index": 1}),
        *_sse("brand_new_event", {"anything": 1}),
        *_sse("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}}),
        *_sse("message_stop", {}),
    ]


def test_a_complete_stream_yields_deltas_and_finalizes_with_tools_and_usage() -> None:
    assembler = AnthropicStreamAssembler()
    assert _feed(assembler, _complete_stream()) == ["Hel", "lo"]
    final = assembler.finalize()
    assert final["text"] == "Hello" and final["finish_reason"] == "tool_calls" and final["model"] == "lyra-7b-synth"
    assert final["usage"]["prompt_tokens"] == 12 and final["usage"]["completion_tokens"] == 9
    assert json.loads(final["tool_calls"][0]["function"]["arguments"]) == {"path": "notes/synthetic-c.log"}


def test_a_stream_that_ends_before_message_stop_keeps_what_arrived_and_is_not_a_success() -> None:
    lines = _complete_stream()
    cut = lines[: lines.index(next(line for line in lines if '"message_delta"' in line)) - 1]
    assembler = AnthropicStreamAssembler()
    _feed(assembler, cut)
    with pytest.raises(IncompleteStreamError) as caught:
        assembler.finalize()
    assert caught.value.partial_text == "Hello" and caught.value.usage.get("prompt_tokens") == 12


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (_sse("error", {"error": {"type": "overloaded_error"}}), AnthropicStreamError),
        (_sse("content_block_delta", {"index": 4, "delta": {"type": "text_delta", "text": "x"}}), MalformedProviderResponseError),
        (["data: {not json", ""], MalformedProviderResponseError),
        (_sse("message_stop", {}) + _sse("content_block_start", {"index": 0, "content_block": {"type": "text"}}), MalformedProviderResponseError),
    ],
    ids=["error-event", "unopened-block", "bad-json", "after-stop"],
)
def test_a_broken_stream_is_a_typed_failure(lines, expected) -> None:
    assembler = AnthropicStreamAssembler()
    with pytest.raises(expected):
        _feed(assembler, lines)


def test_a_tool_use_block_that_never_closes_cannot_become_a_call() -> None:
    assembler = AnthropicStreamAssembler()
    _feed(
        assembler,
        [
            *_sse("message_start", {"message": {"id": "m", "usage": {}}}),
            *_sse("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "t1", "name": "workspace__read_file"}}),
            *_sse("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 2}}),
            *_sse("message_stop", {}),
        ],
    )
    with pytest.raises(MalformedProviderResponseError):
        assembler.finalize()
