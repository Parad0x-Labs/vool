from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.compute_mode import ComputeBudget, set_active_compute_budget
from core.prompt_normalizer import normalize_prompt


def _context_result() -> SimpleNamespace:
    return SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.35,
        assembled_context=lambda: "Prior note: the user prefers direct, useful answers.",
        context_snippets=lambda: [],
        report=SimpleNamespace(
            retrieval_confidence=0.35,
            total_tokens_used=lambda: 18,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )


def _normalize_request(
    prompt: str,
    *,
    task_class: str,
    task_kind: str,
    output_mode: str,
    history_messages: list[dict[str, str]] | None = None,
) -> SimpleNamespace:
    return normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary=prompt),
        classification={"task_class": task_class, "risk_flags": []},
        interpretation=SimpleNamespace(
            reconstructed_text=prompt,
            topic_hints=[],
            understanding_confidence=0.84,
        ),
        context_result=_context_result(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode=output_mode,
        task_kind=task_kind,
        trace_id="trace-1",
        surface="openclaw",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": list(history_messages or []),
        },
    )


def _to_model_request(internal_request: SimpleNamespace) -> ModelRequest:
    return ModelRequest(
        task_kind=internal_request.task_kind,
        prompt=internal_request.user_prompt(),
        system_prompt=internal_request.system_prompt(),
        context=internal_request.context_summary,
        temperature=internal_request.temperature,
        max_output_tokens=internal_request.max_output_tokens,
        messages=internal_request.as_openai_messages(),
        output_mode=internal_request.output_mode,
        trace_id=internal_request.trace_id,
        contract={"mode": internal_request.output_mode},
        metadata=internal_request.metadata,
        attachments=internal_request.attachments,
    )


@pytest.mark.parametrize(
    ("task_class", "prompt"),
    [
        ("chat_conversation", "Do you think boredom is useful, or is it mostly a signal that I need better constraints?"),
        ("business_advisory", "How should I position a B2B analytics product that keeps getting dismissed as another dashboard?"),
        ("debugging", "How do I fix this Python traceback without turning the parser into spaghetti?"),
        ("system_design", "Design a clean local Telegram bot runtime that stays debuggable."),
        ("chat_research", "Tell me about stoicism, but keep it conversational and grounded."),
    ],
)
def test_plain_text_chat_generation_profile_is_hotter_and_adaptive(task_class: str, prompt: str) -> None:
    request = _normalize_request(
        prompt,
        task_class=task_class,
        task_kind="normalization_assist",
        output_mode="plain_text",
        history_messages=[
            {"role": "assistant", "content": "Tell me what tradeoff feels most painful."},
            {"role": "user", "content": "It keeps sounding generic and replaceable."},
        ],
    )

    profile = dict(request.metadata.get("generation_profile") or {})
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})

    assert profile["profile_id"] == "chat_plain_text"
    assert request.temperature == pytest.approx(0.72)
    assert profile["top_p"] == pytest.approx(0.92)
    assert request.max_output_tokens > 240
    assert profile["adaptive_length"] is True
    assert chat_truth["generation_profile_id"] == "chat_plain_text"
    assert chat_truth["adaptive_length"] is True


def test_chat_research_generation_profile_now_uses_same_plain_text_lane() -> None:
    research_request = _normalize_request(
        "Tell me about stoicism, but compare the main schools and where modern pop-stoicism usually distorts them.",
        task_class="chat_research",
        task_kind="normalization_assist",
        output_mode="plain_text",
        history_messages=[{"role": "assistant", "content": "Do you want the historical version or the productivity-bro version?"}],
    )

    profile = dict(research_request.metadata.get("generation_profile") or {})

    assert profile["profile_id"] == "chat_plain_text"
    assert research_request.temperature == pytest.approx(0.72)
    assert profile["top_p"] == pytest.approx(0.92)
    assert profile["adaptive_length"] is True


def test_exact_plain_text_chat_generation_profile_stays_short_and_deterministic() -> None:
    request = _normalize_request(
        "Reply with exactly GREENLOOP-WARMUP-4 and nothing else.",
        task_class="chat_conversation",
        task_kind="normalization_assist",
        output_mode="plain_text",
    )

    profile = dict(request.metadata.get("generation_profile") or {})
    chat_truth = dict(request.metadata.get("chat_truth_prompt") or {})
    messages = request.as_openai_messages()

    assert profile["profile_id"] == "chat_exact_plain_text"
    assert request.temperature == pytest.approx(0.05)
    assert profile["top_p"] == pytest.approx(0.15)
    assert request.max_output_tokens <= 32
    assert profile["adaptive_length"] is False
    assert profile["stop_sequences"] == ["\n"]
    assert chat_truth["generation_profile_id"] == "chat_exact_plain_text"
    assert chat_truth["context_attached"] is False
    assert len(messages) == 2
    assert messages[-1] == {"role": "user", "content": "Reply with exactly GREENLOOP-WARMUP-4 and nothing else."}


@pytest.mark.parametrize(
    ("output_mode", "task_kind", "expected_profile", "expected_temperature", "expected_top_p", "expected_tokens"),
    [
        ("summary_block", "summarization", "structured_response_low_temp", 0.1, 0.25, 220),
        ("action_plan", "action_plan", "planner_structured_low_temp", 0.08, 0.2, 320),
        ("tool_intent", "tool_intent", "tool_extraction_low_temp", 0.05, 0.15, 700),
    ],
)
def test_structured_generation_profiles_stay_low_temp_and_fixed(
    output_mode: str,
    task_kind: str,
    expected_profile: str,
    expected_temperature: float,
    expected_top_p: float,
    expected_tokens: int,
) -> None:
    request = _normalize_request(
        "Figure out the exact next step.",
        task_class="system_design",
        task_kind=task_kind,
        output_mode=output_mode,
    )

    profile = dict(request.metadata.get("generation_profile") or {})

    assert profile["profile_id"] == expected_profile
    assert request.temperature == pytest.approx(expected_temperature)
    assert profile["top_p"] == pytest.approx(expected_top_p)
    assert request.max_output_tokens == expected_tokens
    assert profile["adaptive_length"] is False


def test_openai_adapter_forwards_different_payloads_for_chat_and_tool_extraction() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="local:test",
            model_name="test-model",
            metadata={},
            runtime_config={
                "base_url": "http://adapter.example.test",
                "supports_json_mode": True,
                "timeout_seconds": 5.0,
            },
        )
    )
    chat_request = _to_model_request(
        _normalize_request(
            "Brainstorm a launch campaign idea for a weird soda brand with a strong point of view.",
            task_class="creative_ideation",
            task_kind="normalization_assist",
            output_mode="plain_text",
        )
    )
    tool_request = _to_model_request(
        _normalize_request(
            "Search the web for the latest OpenClaw release notes.",
            task_class="system_design",
            task_kind="tool_intent",
            output_mode="tool_intent",
        )
    )

    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        adapter.run_text_task(chat_request)
        adapter.run_structured_task(tool_request)

    chat_payload = dict(post_mock.call_args_list[0].kwargs["json"])
    tool_payload = dict(post_mock.call_args_list[1].kwargs["json"])

    assert chat_payload["temperature"] == pytest.approx(0.72)
    assert chat_payload["top_p"] == pytest.approx(0.92)
    assert chat_payload["max_tokens"] == chat_request.max_output_tokens
    assert "response_format" not in chat_payload

    assert tool_payload["temperature"] == pytest.approx(0.05)
    assert tool_payload["top_p"] == pytest.approx(0.15)
    assert tool_payload["max_tokens"] == tool_request.max_output_tokens
    assert tool_payload["response_format"] == {"type": "json_object"}


def test_openai_adapter_prewarm_uses_native_ollama_chat_endpoint() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "ollama"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "think": False,
                "prewarm": {
                    "strategy": "ollama_chat",
                    "keep_alive": "15m",
                    "message": " ",
                    "timeout_seconds": 9,
                },
            },
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"load_duration": 123, "total_duration": 456}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        result = adapter.prewarm()

    assert result["ok"] is True
    assert result["status"] == "prewarmed"
    post_mock.assert_called_once()
    assert post_mock.call_args.args[0] == "http://127.0.0.1:11434/api/chat"
    assert post_mock.call_args.kwargs["json"] == {
        "model": "qwen2.5:14b",
        "messages": [{"role": "user", "content": " "}],
        "stream": False,
        "keep_alive": "15m",
        "options": {"num_predict": 1, **adapter._ollama_runner_options()},
        "think": False,
    }


def test_openai_adapter_prewarm_skips_non_ollama_runtime() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="vllm-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={
                "base_url": "http://127.0.0.1:8000/v1",
                "prewarm": {"strategy": "ollama_chat"},
            },
        )
    )

    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post_mock:
        result = adapter.prewarm()

    assert result["ok"] is True
    assert result["status"] == "skipped"
    assert result["reason"] == "not_ollama_runtime"
    post_mock.assert_not_called()


def test_openai_adapter_prewarm_times_out_without_background_worker() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "ollama"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "prewarm": {
                    "strategy": "ollama_chat",
                    "keep_alive": "15m",
                    "timeout_seconds": 12,
                },
            },
        )
    )

    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post",
        side_effect=requests.exceptions.ReadTimeout("cold load timed out"),
    ) as post_mock:
        result = adapter.prewarm()

    assert result["ok"] is True
    assert result["status"] == "timed_out"
    assert result["reason"] == "cold_start_timeout"
    assert result["keep_alive"] == "15m"
    assert result["timeout_seconds"] == 12
    post_mock.assert_called_once()


def test_openai_adapter_prewarm_keeps_legacy_generate_strategy() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "ollama"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "prewarm": {
                    "strategy": "ollama_generate",
                    "keep_alive": "15m",
                    "prompt": " ",
                    "raw": True,
                },
            },
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"load_duration": 123, "total_duration": 456}

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        result = adapter.prewarm()

    assert result["ok"] is True
    assert result["status"] == "prewarmed"
    assert post_mock.call_args.args[0] == "http://127.0.0.1:11434/api/generate"
    assert post_mock.call_args.kwargs["json"] == {
        "model": "qwen2.5:14b",
        "prompt": " ",
        "stream": False,
        "keep_alive": "15m",
        "raw": True,
    }


def test_openai_adapter_uses_native_ollama_chat_and_applies_compute_threads() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "ollama", "deployment_class": "local"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "timeout_seconds": 5.0,
                "temperature": 0.4,
                "context_window": 2048,
                "think": False,
            },
        )
    )
    request = _to_model_request(
        _normalize_request(
            "Explain why this local-first install surface feels fake.",
            task_class="system_design",
            task_kind="normalization_assist",
            output_mode="plain_text",
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": "native ollama answer"}, "eval_count": 12}

    set_active_compute_budget(
        ComputeBudget(
            mode="balanced",
            cpu_threads=3,
            gpu_memory_fraction=0.5,
            worker_pool_cap=1,
            reason="test",
        )
    )
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        result = adapter.run_text_task(request)

    assert result.output_text == "native ollama answer"
    assert post_mock.call_args.args[0] == "http://127.0.0.1:11434/api/chat"
    assert post_mock.call_args.kwargs["json"]["options"]["num_thread"] == 3
    assert post_mock.call_args.kwargs["json"]["options"]["num_ctx"] == 2048
    assert post_mock.call_args.kwargs["json"]["think"] is False


def test_openai_adapter_prefers_manifest_openai_path_for_ollama_and_keeps_thread_budget() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            metadata={"runtime_family": "ollama", "deployment_class": "local"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434",
                "api_path": "/v1/chat/completions",
                "timeout_seconds": 5.0,
                "temperature": 0.4,
                "context_window": 4096,
            },
        )
    )
    request = _to_model_request(
        _normalize_request(
            "Reply with exactly OPENAI-COMPAT-OK and nothing else.",
            task_class="chat_conversation",
            task_kind="normalization_assist",
            output_mode="plain_text",
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "OPENAI-COMPAT-OK"}}], "usage": {}}

    set_active_compute_budget(
        ComputeBudget(
            mode="balanced",
            cpu_threads=3,
            gpu_memory_fraction=0.5,
            worker_pool_cap=1,
            reason="test",
        )
    )
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post_mock:
        result = adapter.run_text_task(request)

    payload = dict(post_mock.call_args.kwargs["json"])
    assert result.output_text == "OPENAI-COMPAT-OK"
    assert post_mock.call_args.args[0] == "http://127.0.0.1:11434/v1/chat/completions"
    assert payload["options"]["num_thread"] == 3
    assert payload["options"]["num_ctx"] == 4096
    assert payload["max_tokens"] == request.max_output_tokens
    assert payload["temperature"] == pytest.approx(0.05)
    assert payload["top_p"] == pytest.approx(0.15)


def test_openai_adapter_stream_text_task_yields_incremental_chunks() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="kimi-remote:kimi-k2",
            model_name="kimi-k2",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={
                "base_url": "https://kimi.example/v1",
                "supports_json_mode": True,
                "timeout_seconds": 5.0,
            },
        )
    )
    request = _to_model_request(
        _normalize_request(
            "Summarize the runtime boundary in one paragraph.",
            task_class="chat_conversation",
            task_kind="conversation",
            output_mode="plain_text",
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        b'data: {"choices":[{"delta":{"content":"hello"}}]}',
        b'data: {"choices":[{"delta":{"content":" world"}}]}',
        b"data: [DONE]",
    ]

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response):
        chunks = list(adapter.stream_text_task(request))

    assert [chunk.delta_text for chunk in chunks if chunk.delta_text] == ["hello", " world"]
    assert chunks[-1].done is True


def test_openai_adapter_stream_text_task_handles_native_ollama_bytes() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:14b",
            model_name="qwen2.5:14b",
            metadata={"runtime_family": "ollama", "deployment_class": "local"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "timeout_seconds": 5.0,
                "temperature": 0.4,
            },
        )
    )
    request = _to_model_request(
        _normalize_request(
            "Reply with exactly: stream model ok",
            task_class="chat_conversation",
            task_kind="conversation",
            output_mode="plain_text",
        )
    )
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [
        b'{"model":"qwen2.5:14b","message":{"role":"assistant","content":"stream"},"done":false}',
        b'{"model":"qwen2.5:14b","message":{"role":"assistant","content":" model"},"done":false}',
        b'{"model":"qwen2.5:14b","message":{"role":"assistant","content":" ok"},"done":false}',
        b'{"model":"qwen2.5:14b","message":{"role":"assistant","content":""},"done":true}',
    ]

    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response):
        chunks = list(adapter.stream_text_task(request))

    assert [chunk.delta_text for chunk in chunks if chunk.delta_text] == ["stream", " model", " ok"]
    assert chunks[-1].done is True
