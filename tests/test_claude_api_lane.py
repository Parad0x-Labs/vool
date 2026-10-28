"""The first-party Claude API lane speaks the Messages dialect current models accept.

On main 8e0a887 every call through an Anthropic key failed: the lane posted VOOL's OpenAI payload to
the compatibility endpoint, which refused ``temperature`` beside ``top_p`` and
``response_format: json_object``, and the catalog offered ids the API no longer serves.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.anthropic_messages_protocol import claude_output_schema, openai_request_to_claude_api
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.model_output_contracts import json_schema_for_mode


def _claude_adapter(model_name: str = "claude-haiku-5-5", **config) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="anthropic-byok",
            provider_id=f"anthropic-byok:{model_name}",
            model_name=model_name,
            metadata={"runtime_family": "openai-compatible", "cost_class": "paid_cloud"},
            runtime_config={
                "base_url": "https://api.anthropic.com/v1",
                # A lane registered before the dialect was recorded: the host alone decides.
                "api_path": "/chat/completions",
                "timeout_seconds": 5.0,
                "supports_json_mode": True,
                "headers": {"anthropic-version": "2023-06-01"},
                **config,
            },
        )
    )


def _ok(body: dict) -> mock.Mock:
    response = mock.Mock()
    response.status_code = 200
    response.headers = {"request-id": "req_1"}
    response.raise_for_status.return_value = None
    response.json.return_value = body
    return response


def _message(content: list, stop: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop,
        "usage": {"input_tokens": 12, "output_tokens": 5},
    }


def _sent(post: mock.Mock) -> tuple[str, dict, dict]:
    call = post.call_args
    return call.args[0], call.kwargs["json"], call.kwargs["headers"]


def test_chat_goes_to_messages_with_the_key_in_x_api_key_and_no_sampling_knobs():
    adapter = _claude_adapter()
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        return_value=_ok(_message([{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Hello."}])),
    ) as post:
        result = adapter.run_text_task(
            ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}], temperature=0.7)
        )
    url, body, headers = _sent(post)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant-test"
    assert "Authorization" not in headers
    assert headers["anthropic-version"] == "2023-06-01"
    assert "temperature" not in body and "top_p" not in body
    assert "response_format" not in body
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] > 0
    assert result.output_text == "Hello."
    assert result.usage["prompt_tokens"] == 12 and result.usage["completion_tokens"] == 5
    assert result.provider_attested_model == "claude-haiku-5-5"


def test_structured_turn_uses_output_config_format_and_never_json_object():
    adapter = _claude_adapter(supports_json_schema=True)
    schema = json_schema_for_mode("summary_block")
    request = ModelRequest(
        task_kind="summarize",
        prompt="sum up",
        messages=[{"role": "user", "content": "sum up"}],
        output_mode="summary_block",
        contract={"json_schema": schema},
    )
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        return_value=_ok(_message([{"type": "text", "text": '{"summary": "ok"}'}])),
    ) as post:
        result = adapter.run_structured_task(request)
    _, body, _ = _sent(post)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "response_format" not in body
    assert json.loads(result.output_text) == {"summary": "ok"}


def test_open_json_object_turn_is_asked_for_json_in_the_instructions():
    body = openai_request_to_claude_api(
        {
            "model": "claude-sonnet-5-5",
            "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "x"}],
            "max_tokens": 300,
            "temperature": 0.2,
            "top_p": 0.9,
            "response_format": {"type": "json_object"},
        }
    )
    assert "output_config" not in body
    assert body["system"].startswith("Be brief.")
    assert "exactly one JSON object" in body["system"]


def test_schema_translation_tightens_declared_objects_and_refuses_open_maps():
    # Declared properties with extra keys left open: tightened, every answer still fits the original.
    tightened = claude_output_schema(json_schema_for_mode("action_plan"))
    assert tightened["additionalProperties"] is False
    # An object that is only an open map cannot be tightened without changing its meaning.
    assert claude_output_schema(json_schema_for_mode("tool_intent")) is None
    assert claude_output_schema({"type": "object", "additionalProperties": True}) is None
    # Unsupported keywords are dropped from what is sent; VOOL validates the full contract after.
    stripped = claude_output_schema(
        {"type": "object", "properties": {"n": {"type": "string", "minLength": 1, "format": "slug"}}}
    )
    assert stripped["properties"]["n"] == {"type": "string"}


def test_required_tool_call_is_sent_as_auto_with_an_instruction_and_the_call_is_read_back():
    adapter = _claude_adapter()
    tools = build_cloud_tool_definitions(
        [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string"}}]
    )
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="pwd",
        messages=[{"role": "user", "content": "show the current directory"}],
        output_mode="tool_intent",
        tools=tools,
    )
    reply = _message(
        [{"type": "tool_use", "id": "toolu_1", "name": tools[0].name, "input": {"command": "pwd"}}], stop="tool_use"
    )
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=_ok(reply)
    ) as post:
        result = adapter.invoke(request)
    _, body, _ = _sent(post)
    assert body["tool_choice"] == {"type": "auto"}  # forced choice is refused by current models
    assert "calling one of the provided tools" in body["system"]
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert [call.arguments for call in result.tool_calls] == [{"command": "pwd"}]


def test_reasoning_disabled_goes_out_as_low_effort_with_the_thinking_reserve_kept():
    adapter = _claude_adapter()
    request = ModelRequest(
        task_kind="chat",
        prompt="hi",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_mode="disabled",
        max_output_tokens=200,
    )
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        return_value=_ok(_message([{"type": "text", "text": "Hi."}])),
    ) as post:
        adapter.run_text_task(request)
    _, body, _ = _sent(post)
    assert body["output_config"]["effort"] == "low"
    assert "thinking" not in body
    assert body["max_tokens"] > 200  # Opus 5.5 cannot stop thinking: room for it is kept


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (401, {"type": "authentication_error", "message": "invalid x-api-key"}, "refused the API key"),
        (404, {"type": "not_found_error", "message": "model: claude-nope"}, "does not know that model"),
        (400, {"type": "invalid_request_error", "message": "Your credit balance is too low"}, "no credit left"),
        (529, {"type": "overloaded_error", "message": "Overloaded"}, "having trouble"),
    ],
)
def test_a_refusal_reaches_the_person_as_one_plain_sentence(status, error, expected):
    adapter = _claude_adapter()
    response = mock.Mock()
    response.status_code = status
    response.headers = {}
    response.json.return_value = {"type": "error", "error": error}
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=response
    ):
        with pytest.raises(Exception) as caught:
            adapter.run_text_task(ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}]))
    assert expected in str(caught.value)
    assert getattr(caught.value, "response", None) is response
    assert "sk-ant-test" not in str(caught.value)


def test_stream_is_assembled_from_messages_events_and_refuses_a_cut_off_stream():
    adapter = _claude_adapter()
    events = [
        'event: message_start',
        'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-haiku-5-5","usage":{"input_tokens":9}}}',
        "",
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        "",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}',
        "",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}',
        "",
        'data: {"type":"content_block_stop","index":0}',
        "",
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
        "",
        'data: {"type":"message_stop"}',
        "",
    ]

    def _stream(lines):
        response = mock.Mock()
        response.status_code = 200
        response.headers = {}
        response.raise_for_status.return_value = None
        response.iter_lines.return_value = list(lines)
        return response

    request = ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}])
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=_stream(events)
    ) as post:
        chunks = list(adapter.stream_text_task(request))
    url, body, _ = _sent(post)
    assert url.endswith("/v1/messages") and body["stream"] is True and "stream_options" not in body
    assert "".join(chunk.delta_text for chunk in chunks) == "Hello"
    assert chunks[-1].done and chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage["prompt_tokens"] == 9 and chunks[-1].usage["completion_tokens"] == 3

    from core.anthropic_messages_protocol import IncompleteStreamError

    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-ant-test"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=_stream(events[:8])
    ):
        with pytest.raises(IncompleteStreamError):
            list(adapter.stream_text_task(request))


def test_openai_compatible_lanes_are_unchanged():
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openai-byok",
            provider_id="openai-byok:gpt-4.1-mini",
            model_name="gpt-4.1-mini",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={"base_url": "https://api.openai.com/v1", "api_path": "/chat/completions", "timeout_seconds": 5.0},
        )
    )
    reply = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "model": "gpt-4.1-mini"}
    with mock.patch.object(adapter, "_dispatch_credential", return_value="sk-proj-x"), mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=_ok(reply)
    ) as post:
        adapter.run_text_task(ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}]))
    url, body, headers = _sent(post)
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-proj-x"
    assert "temperature" in body


def test_catalog_and_prices_name_current_claude_models():
    from core.cloud_model_catalog import curated_models
    from core.cloud_providers import PROVIDERS
    from core.model_pricing import lookup_model_price

    ids = [row["id"] for row in curated_models("anthropic")]
    assert ids == ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
    assert PROVIDERS["anthropic"].default_model in ids
    assert PROVIDERS["anthropic"].chat_protocol == "anthropic_messages"
    price = lookup_model_price("anthropic-byok:claude-opus-5-5", "claude-opus-5-5")
    assert price.known and round(price.prompt_usd_per_token * 1_000_000, 2) == 4.00
    haiku_long = lookup_model_price("anthropic-byok:claude-haiku-5-5", "claude-haiku-5-5", prompt_tokens=150_000)
    assert round(haiku_long.prompt_usd_per_token * 1_000_000, 2) == 0.50
