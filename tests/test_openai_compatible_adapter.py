from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.cloud_tool_call_contract import build_cloud_tool_definitions


def _ollama_adapter(*, provider_id: str = "ollama-local:qwen2.5:7b", model_name: str = "qwen2.5:7b") -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=provider_id,
            model_name=model_name,
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434/v1", "timeout_seconds": 5.0},
        )
    )


def _request(prompt: str = "hello") -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt=prompt, messages=[{"role": "user", "content": prompt}])


def test_invoke_ollama_chat_records_live_benchmark_from_real_response_fields() -> None:
    # core.local_inference_autopilot's live per-message routing scores providers on
    # tokens_per_second, but that field stays static/zero unless a real Ollama call
    # actually records a benchmark. This is the missing write-side wiring.
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "message": {"content": "hi there"},
        "prompt_eval_count": 120,
        "prompt_eval_duration": 1_500_000_000,
        "eval_count": 40,
        "eval_duration": 2_000_000_000,  # 2s -> 20 tok/s
    }

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ) as record_mock:
        result = adapter.run_text_task(_request("hello there"))

    assert result.output_text == "hi there"
    assert result.usage["prompt_eval_count"] == 120
    assert result.usage["prompt_eval_duration"] == 1_500_000_000
    assert result.usage["eval_duration"] == 2_000_000_000
    record_mock.assert_called_once()
    _, kwargs = record_mock.call_args
    assert kwargs["provider_id"] == "ollama-local:qwen2.5:7b"
    assert kwargs["model_id"] == "qwen2.5:7b"
    assert kwargs["prompt"] == "hello there"
    assert kwargs["response_payload"]["eval_count"] == 40


def test_stream_ollama_chat_records_benchmark_on_final_done_event() -> None:
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        '{"message": {"content": "hi"}, "done": false}',
        '{"message": {"content": ""}, "done": true, "eval_count": 8, "eval_duration": 500000000}',
    ]

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ) as record_mock:
        list(adapter.stream_text_task(_request("stream this")))

    record_mock.assert_called_once()
    _, kwargs = record_mock.call_args
    assert kwargs["response_payload"]["eval_count"] == 8


def _cloud_adapter(
    *,
    model_name: str = "anthropic/claude",
    verified_free: bool = False,
) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id=f"openrouter-byok:{model_name}",
            model_name=model_name,
            metadata={"runtime_family": "openai-compatible", "verified_free": verified_free},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1",
                "api_path": "/chat/completions",
                "timeout_seconds": 5.0,
                "supports_json_mode": True,
            },
        )
    )


def test_cloud_stream_requests_include_usage_and_captures_terminal_usage() -> None:
    # Without stream_options.include_usage OpenRouter omits the terminal usage block, so a
    # streamed cloud turn would record no tokens/cost. The final chunk must carry the totals.
    adapter = _cloud_adapter()
    response = mock.Mock()
    response.headers = {"X-Request-ID": "request-public", "Authorization": "never-record"}
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        'data: {"id": "generation-id", "model": "upstream-model", "provider": "provider-node", "choices": [{"delta": {"role": "assistant"}}]}',
        'data: {"choices": [{"delta": {"content": "hi"}}]}',
        'data: {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 10, "cost": 0.0021}}',
        "data: [DONE]",
    ]
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        chunks = list(adapter.stream_text_task(_request("stream this")))

    assert post.call_args.kwargs["json"]["stream_options"] == {"include_usage": True}
    final = chunks[-1]
    assert final.provider_metadata["response_headers"] == {"x-request-id": "request-public"}
    assert final.provider_metadata["response_metadata"]["id"] == "generation-id"
    assert final.provider_metadata["response_metadata"]["provider"] == "provider-node"
    assert final.done is True
    assert final.usage == {"prompt_tokens": 50, "completion_tokens": 10, "cost": 0.0021}


def _native_tool_request() -> ModelRequest:
    tools = build_cloud_tool_definitions(
        [
            {
                "intent": "sandbox.run_command",
                "description": "Run a bounded command.",
                "arguments": {"command": "string", "cwd": "string optional"},
            }
        ]
    )
    return ModelRequest(
        task_kind="tool_intent",
        prompt="show the current directory",
        messages=[{"role": "user", "content": "show the current directory"}],
        output_mode="tool_intent",
        tools=tools,
        tool_choice="required",
    )


def test_openrouter_byok_uses_native_tools_and_returns_canonical_intent() -> None:
    adapter = _cloud_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "byok-native-1",
                            "type": "function",
                            "function": {
                                "name": "sandbox__run_command",
                                "arguments": '{"command":"pwd","cwd":null}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        result = adapter.run_structured_task(_native_tool_request())

    body = post.call_args.kwargs["json"]
    assert body["tool_choice"] == "required"
    assert body["tools"][0]["function"]["name"] == "sandbox__run_command"
    assert body["tools"][0]["function"]["strict"] is False
    assert "response_format" not in body
    assert result.output_text == (
        '{"_native_tool_call_id":"byok-native-1","arguments":{"command":"pwd","cwd":null},'
        '"intent":"sandbox.run_command"}'
    )
    assert [call.call_id for call in result.tool_calls] == ["byok-native-1"]


def test_openrouter_byok_preserves_valid_native_batch_for_unified_executor() -> None:
    adapter = _cloud_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "byok-native-1",
                            "type": "function",
                            "function": {
                                "name": "sandbox__run_command",
                                "arguments": '{"command":"pwd","cwd":null}',
                            },
                        },
                        {
                            "id": "byok-native-2",
                            "type": "function",
                            "function": {
                                "name": "sandbox__run_command",
                                "arguments": '{"command":"git status --short","cwd":null}',
                            },
                        },
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response):
        result = adapter.run_structured_task(_native_tool_request())

    assert [call.arguments["command"] for call in result.tool_calls] == [
        "pwd",
        "git status --short",
    ]
    assert json.loads(result.output_text) == {
        "_native_tool_call_id": "byok-native-1",
        "arguments": {"command": "pwd", "cwd": None},
        "intent": "sandbox.run_command",
    }


def test_openrouter_byok_paid_lane_fails_loudly_without_repeating_malformed_turn() -> None:
    adapter = _cloud_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": '{"tool":"bash","arguments":{"command":"pwd"}}'}}],
        "usage": {"cost": 0.001},
    }
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        with pytest.raises(RuntimeError, match="malformed provider response"):
            adapter.run_structured_task(_native_tool_request())
    assert post.call_count == 1


def test_openrouter_byok_free_lane_retries_one_malformed_turn_then_accepts_native_call() -> None:
    adapter = _cloud_adapter(model_name="vendor/tool-model:free", verified_free=True)
    malformed = mock.Mock()
    malformed.raise_for_status.return_value = None
    malformed.json.return_value = {
        "choices": [{"message": {"content": '{"tool":"bash","arguments":{"command":"pwd"}}'}}],
        "usage": {"cost": 0.0},
    }
    recovered = mock.Mock()
    recovered.raise_for_status.return_value = None
    recovered.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "byok-retry-2",
                            "type": "function",
                            "function": {
                                "name": "sandbox__run_command",
                                "arguments": '{"command":"pwd","cwd":null}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"cost": 0.0},
    }
    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        side_effect=[malformed, recovered],
    ) as post:
        result = adapter.run_structured_task(_native_tool_request())
    assert post.call_count == 2
    assert '"intent":"sandbox.run_command"' in result.output_text
    assert '"_native_tool_call_id":"byok-retry-2"' in result.output_text


def test_ollama_stream_final_chunk_carries_usage() -> None:
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        '{"message": {"content": "hi"}, "done": false}',
        '{"message": {"content": ""}, "done": true, "prompt_eval_count": 120, "eval_count": 8}',
    ]
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark"
    ):
        chunks = list(adapter.stream_text_task(_request("stream this")))

    final = chunks[-1]
    assert final.done is True
    assert final.usage["prompt_eval_count"] == 120
    assert final.usage["eval_count"] == 8
    # ollama family must NOT get the OpenAI-only stream_options field
    assert "stream_options" not in _cloud_stream_payload_is_absent_for_ollama(adapter)


def _cloud_stream_payload_is_absent_for_ollama(adapter: OpenAICompatibleAdapter) -> dict:
    return adapter._build_openai_payload(_request("x"), force_json=False, stream=True)


def test_benchmark_recording_failure_never_breaks_the_chat_response() -> None:
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "still works"}, "eval_count": 5, "eval_duration": 1_000_000_000}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response), mock.patch(
        "core.local_inference_evidence.record_ollama_generate_benchmark",
        side_effect=RuntimeError("db unavailable"),
    ):
        result = adapter.run_text_task(_request("hello"))

    assert result.output_text == "still works"


def _post_kwargs(adapter: OpenAICompatibleAdapter, request: ModelRequest) -> dict:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "ok"}}
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        adapter.run_text_task(request)
    return post.call_args.kwargs


def test_chat_call_uses_connect_read_timeout_tuple() -> None:
    # A single float applies to connect too; a (connect, read) tuple lets a dead Ollama fail
    # fast on connect instead of blocking the endpoint for the full generous read budget.
    adapter = _ollama_adapter()
    adapter.manifest.runtime_config["timeout_seconds"] = 180.0
    assert _post_kwargs(adapter, _request("hi"))["timeout"] == (10.0, 180.0)


def test_qwen3_auto_follows_the_deep_reasoning_preference(monkeypatch) -> None:
    # This is only the typed wire contract. Live qwen3 falsification proved `think:false` is not a
    # reliable no-reasoning control on the installed runtime, so daily Auto ranking does not use
    # this serialization test as evidence that qwen3 is a reliable ordinary-chat candidate.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")
    assert _post_kwargs(adapter, _request("hi"))["json"]["think"] is False
    monkeypatch.setenv("VOOL_DEEP_REASONING", "1")
    assert _post_kwargs(adapter, _request("hi"))["json"]["think"] is True


def test_qwen3_auto_respects_the_manifest_reasoning_preference(monkeypatch) -> None:
    # Preserve the manifest's wire preference for compatibility. Ranking separately treats a plain
    # qwen3 tag as uncontrolled unless the model artifact itself proves no-think behavior.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")
    adapter.manifest.runtime_config["think"] = False
    monkeypatch.setenv("VOOL_DEEP_REASONING", "1")
    assert _post_kwargs(adapter, _request("hi"))["json"]["think"] is False


def test_a_thinking_model_gets_budget_for_required_reasoning_on_top_of_the_answer() -> None:
    # A thinking model spends the budget reasoning before writing any answer: measured on qwen3:4b,
    # a 200-token budget produced 802 characters of reasoning and an EMPTY content. The caller's
    # budget describes the answer, so the reasoning is paid for on top of it.
    thinking = _ollama_adapter(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")
    plain = _ollama_adapter(provider_id="ollama-local:qwen2.5:7b", model_name="qwen2.5:7b")
    for adapter in (thinking, plain):
        adapter.manifest.runtime_config["context_window"] = 4096
    asked = _request("hi")
    asked.max_output_tokens = 240
    asked.reasoning_mode = "required"
    thinking_budget = _post_kwargs(thinking, asked)["json"]["options"]["num_predict"]
    plain_budget = _post_kwargs(plain, asked)["json"]["options"]["num_predict"]
    assert plain_budget == 240
    assert thinking_budget > plain_budget


def test_an_unset_output_budget_is_left_to_the_provider_not_pinned_to_zero() -> None:
    # An absent num_predict means "provider default"; writing a computed 0 into it would ask the
    # model for an empty answer on every request that does not name a budget.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")
    options = _post_kwargs(adapter, _request("hi"))["json"]["options"]
    assert options.get("num_predict") != 0


def test_the_reasoning_reserve_never_crowds_the_prompt_out_of_a_small_window() -> None:
    # Reserving output tokens takes them from the prompt, so an unbounded reserve against a small
    # window leaves nothing for the system prompt and the turn and the call fails closed rather
    # than answering.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen3:8b", model_name="qwen3:8b")
    adapter.manifest.runtime_config["context_window"] = 160
    asked = _request("hi")
    asked.max_output_tokens = 64
    budget = _post_kwargs(adapter, asked)["json"]["options"]["num_predict"]
    assert 64 <= budget <= 160 // 2


def test_non_thinking_model_is_not_forced_off_by_default() -> None:
    # qwen2.5 is not a thinking model; without explicit config we do not inject think:false.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen2.5:7b", model_name="qwen2.5:7b")
    assert "think" not in _post_kwargs(adapter, _request("hi"))["json"]


def test_non_thinking_model_omits_think_even_when_deep_reasoning_is_on(monkeypatch) -> None:
    # qwen2.5 answers HTTP 400 if `think` is present at all, so the key must be omitted regardless
    # of the deep-reasoning switch.
    adapter = _ollama_adapter(provider_id="ollama-local:qwen2.5:7b", model_name="qwen2.5:7b")
    monkeypatch.setenv("VOOL_DEEP_REASONING", "1")
    assert "think" not in _post_kwargs(adapter, _request("hi"))["json"]


def test_stream_decode_forces_utf8_over_latin1_fallback() -> None:
    # The mojibake bug: SSE with no explicit charset made `requests` fall back to ISO-8859-1, so a
    # UTF-8 em-dash (E2 80 94) decoded as 'â€"' in every cloud reply. The stream readers must force
    # response.encoding to utf-8 before iter_lines(decode_unicode=True).
    import inspect

    from adapters import openai_compatible_adapter as mod

    src = inspect.getsource(mod)
    # every decode_unicode stream loop must be preceded by an explicit utf-8 encoding pin
    assert src.count("decode_unicode=True") == src.count('response.encoding = "utf-8"') and src.count("decode_unicode=True") >= 2
    # and the raw bytes really round-trip once decoded as utf-8
    assert "\xe2\x80\x94".encode("latin-1").decode("utf-8") == "—"
