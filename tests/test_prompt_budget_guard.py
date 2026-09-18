from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.memory_first_router import MemoryFirstRouter, _emit_grounding_dropped_event
from core.model_health import circuit_is_open, get_provider_health, reset_provider_health
from core.prompt_budget import (
    PromptBudgetExceededError,
    estimate_message_tokens,
    fit_messages_to_context_window,
)
from core.provider_invocation_gateway import ProviderInvocationValidationError

# Real qwen2.5:7b token counts (Ollama prompt_eval_count) for the exact samples below.
# The estimate must never read BELOW these -- a low read is what lets an oversized prompt
# through to be left-truncated by the provider.
_CJK_SENTENCE = "本地优先的智能代理运行时会把每一条收据都保存在磁盘上，绝不把任何字节发送到它无法控制的服务器。"
_ENGLISH_SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the local runtime keeps every "
    "receipt on disk and never sends a single byte to a server it does not control. "
)


@pytest.fixture(autouse=True)
def _isolated_author_certification():
    """Synthetic manifests must not inherit another test's cached local certification."""
    from core.final_answer_authorship import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


def _pad(seed: str, chars: int) -> str:
    return (seed * (chars // len(seed) + 1))[:chars]


def _augmented_messages() -> tuple[list[dict[str, str]], str, str]:
    system_prompt = "SYSTEM RULES MUST SURVIVE " + ("S" * 200)
    memory_prefix = "PERSISTENT MEMORY " + ("M" * 600)
    messages = [
        {"role": "system", "content": f"{memory_prefix}\n\n---\n\n{system_prompt}"},
        {"role": "user", "content": "<retrieved_context>" + ("R" * 800) + "</retrieved_context>"},
        {"role": "user", "content": "OLDEST HISTORY " + ("A" * 500)},
        {"role": "assistant", "content": "NEWER HISTORY " + ("B" * 500)},
        {"role": "user", "content": "CURRENT USER TURN " + ("U" * 100)},
    ]
    return messages, system_prompt, memory_prefix


def test_prompt_budget_sheds_stale_history_before_grounding_on_a_retrieval_turn() -> None:
    """Grounding answers the current question; stale chit-chat does not, so it goes first."""
    messages, system_prompt, memory_prefix = _augmented_messages()
    without_history = [messages[0], messages[1], messages[4]]
    available = estimate_message_tokens(without_history)

    result = fit_messages_to_context_window(
        messages,
        num_ctx=available + 240,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
        memory_prefix=memory_prefix,
    )

    assert result.messages == without_history
    assert result.telemetry["dropped_history_messages"] == 2
    assert result.telemetry["dropped_retrieved_messages"] == 0
    assert result.telemetry["grounding_dropped"] is False
    assert result.telemetry["retrieval_present"] is True
    assert result.telemetry["dropped_memory"] is False
    assert result.telemetry["system_prompt_preserved"] is True


def test_prompt_budget_keeps_grounding_when_shedding_only_some_history_is_enough() -> None:
    """Only the turns that must go, go: the rest of history outlives nothing it need not."""
    system_prompt = "You are VOOL. Never fabricate."
    retrieved = "<retrieved_context>Project Zephyr runs PostgreSQL 16.</retrieved_context>"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": retrieved},
        {"role": "user", "content": "oldest chit chat " + ("a" * 4000)},
        {"role": "assistant", "content": "older reply " + ("b" * 4000)},
        {"role": "user", "content": "recent chit chat " + ("c" * 200)},
        {"role": "assistant", "content": "recent reply " + ("d" * 200)},
        {"role": "user", "content": "What database does Project Zephyr use?"},
    ]
    keep_recent = [messages[0], messages[1], *messages[4:]]
    available = estimate_message_tokens(keep_recent)

    result = fit_messages_to_context_window(
        messages,
        num_ctx=available + 240,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    assert result.messages == keep_recent
    assert result.telemetry["dropped_history_messages"] == 2
    assert result.telemetry["dropped_retrieved_messages"] == 0
    assert result.telemetry["grounding_dropped"] is False
    assert "PostgreSQL 16" in "".join(str(m["content"]) for m in result.messages)


def test_prompt_budget_sheds_grounding_first_when_no_history_shed_could_save_it() -> None:
    """Grounding that cannot fit even with every turn gone must not cost history as well."""
    system_prompt = "You are VOOL. Never fabricate."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "<retrieved_context>" + ("r" * 40000) + "</retrieved_context>"},
        {"role": "user", "content": "keepable history " + ("h" * 100)},
        {"role": "assistant", "content": "keepable reply " + ("i" * 100)},
        {"role": "user", "content": "current turn?"},
    ]

    result = fit_messages_to_context_window(
        messages,
        num_ctx=4096,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    assert result.messages == [messages[0], *messages[2:]]
    assert result.telemetry["dropped_retrieved_messages"] == 1
    assert result.telemetry["dropped_history_messages"] == 0
    assert result.telemetry["grounding_dropped"] is True
    assert result.telemetry["retrieval_present"] is True


def test_prompt_budget_reports_no_grounding_signal_when_no_retrieval_was_present() -> None:
    system_prompt = "You are VOOL. Never fabricate."
    result = fit_messages_to_context_window(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "hello"},
        ],
        num_ctx=4096,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    assert result.telemetry["retrieval_present"] is False
    assert result.telemetry["grounding_dropped"] is False


def test_prompt_budget_sheds_oldest_history_after_retrieval() -> None:
    messages, system_prompt, memory_prefix = _augmented_messages()
    expected = [messages[0], messages[4]]
    available = estimate_message_tokens(expected)

    result = fit_messages_to_context_window(
        messages,
        num_ctx=available + 240,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
        memory_prefix=memory_prefix,
    )

    assert result.messages == expected
    assert result.telemetry["dropped_retrieved_messages"] == 1
    assert result.telemetry["dropped_history_messages"] == 2
    assert result.telemetry["dropped_memory"] is False


def test_prompt_budget_sheds_memory_last_and_preserves_system_prompt() -> None:
    messages, system_prompt, memory_prefix = _augmented_messages()
    expected = [
        {"role": "system", "content": system_prompt},
        messages[-1],
    ]
    available = estimate_message_tokens(expected)

    result = fit_messages_to_context_window(
        messages,
        num_ctx=available + 240,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
        memory_prefix=memory_prefix,
    )

    assert result.messages == expected
    assert result.telemetry["dropped_retrieved_messages"] == 1
    assert result.telemetry["dropped_history_messages"] == 2
    assert result.telemetry["dropped_memory"] is True
    assert result.telemetry["estimated_prompt_tokens_after"] <= available


@pytest.mark.parametrize("openai_compatible", [False, True])
def test_ollama_payload_guard_applies_to_both_live_api_paths(openai_compatible: bool) -> None:
    runtime_config = {"context_window": 512}
    if openai_compatible:
        runtime_config["api_path"] = "/v1/chat/completions"
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            metadata={"runtime_family": "ollama"},
            runtime_config=runtime_config,
        )
    )
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="CURRENT USER",
        system_prompt="SYSTEM MUST SURVIVE",
        max_output_tokens=64,
        messages=[
            {"role": "system", "content": "SYSTEM MUST SURVIVE"},
            {"role": "system", "content": "<retrieved_context>" + ("R" * 1800) + "</retrieved_context>"},
            {"role": "user", "content": "OLD HISTORY " + ("H" * 600)},
            {"role": "user", "content": "CURRENT USER"},
        ],
    )

    payload = (
        adapter._build_openai_payload(request, force_json=False, stream=False)
        if openai_compatible
        else adapter._build_ollama_payload(request, force_json=False, stream=False)
    )

    reserve = int(payload.get("max_tokens") or payload["options"].get("num_predict") or 0)
    assert estimate_message_tokens(payload["messages"]) <= payload["options"]["num_ctx"] - reserve
    assert payload["messages"][0]["content"] == "SYSTEM MUST SURVIVE"
    assert request.metadata["prompt_budget"]["status"] == "trimmed"
    assert request.metadata["prompt_budget"]["dropped_retrieved_messages"] == 1


def test_ollama_payload_guard_sheds_real_memory_prefix_after_history() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            metadata={"runtime_family": "ollama"},
            runtime_config={"context_window": 160},
        )
    )
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="CURRENT USER",
        system_prompt="SYSTEM MUST SURVIVE",
        max_output_tokens=64,
        messages=[
            {"role": "system", "content": "SYSTEM MUST SURVIVE"},
            {"role": "user", "content": "OLD HISTORY " + ("H" * 500)},
            {"role": "user", "content": "CURRENT USER"},
        ],
    )

    with mock.patch(
        "adapters.openai_compatible_adapter.build_memory_prefix_for_request",
        return_value="PERSISTENT MEMORY " + ("M" * 500),
    ):
        payload = adapter._build_ollama_payload(request, force_json=False, stream=False)

    assert payload["messages"] == [
        {"role": "system", "content": "SYSTEM MUST SURVIVE"},
        {"role": "user", "content": "CURRENT USER"},
    ]
    assert request.metadata["prompt_budget"]["dropped_history_messages"] == 1
    assert request.metadata["prompt_budget"]["dropped_memory"] is True


def test_ollama_prompt_budget_fails_closed_before_network_when_protected_prompt_cannot_fit() -> None:
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen3:8b",
            model_name="qwen3:8b",
            metadata={"runtime_family": "ollama"},
            runtime_config={
                "base_url": "http://127.0.0.1:11434/v1",
                "context_window": 128,
                "timeout_seconds": 5.0,
            },
        )
    )
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="CURRENT USER",
        system_prompt="PROTECTED SYSTEM " + ("S" * 600),
        max_output_tokens=64,
    )

    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post_mock:
        with pytest.raises(PromptBudgetExceededError, match="prompt_budget_exceeded"):
            adapter.run_text_task(request)

    post_mock.assert_not_called()
    assert request.metadata["prompt_budget"]["status"] == "rejected"
    assert request.metadata["prompt_budget"]["system_prompt_preserved"] is True


def _budget_rejecting_router(exc: PromptBudgetExceededError) -> tuple[Any, Any]:
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.run_text_task.side_effect = exc
    registry = mock.Mock()
    registry.build_adapter.return_value = adapter
    manifest = SimpleNamespace(
        provider_id="ollama-local:qwen2.5:7b",
        model_name="qwen2.5:7b",
        metadata={"runtime_family": "ollama", "cost_class": "free_local"},
        runtime_config={"context_window": 4096},
        adapter_type="openai_compatible",
    )
    return MemoryFirstRouter(registry=registry), manifest


def test_prompt_budget_rejection_does_not_mark_a_healthy_provider_unhealthy() -> None:
    """A prompt-shape problem is not a provider fault: no other provider fits it better."""
    exc = PromptBudgetExceededError("prompt_budget_exceeded: too big", telemetry={"status": "rejected"})
    router, manifest = _budget_rejecting_router(exc)
    reset_provider_health(manifest.provider_id)

    with mock.patch("core.memory_first_router.record_provider_failure") as failure_mock:
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(
                task_kind="normalization_assist",
                prompt="CURRENT USER",
                system_prompt="SYSTEM MUST SURVIVE",
                max_output_tokens=64,
            ),
            output_mode="text",
            task=SimpleNamespace(task_id="task-1"),
            source_context=None,
        )

    failure_mock.assert_not_called()
    assert response is None
    assert error is not None
    assert error.startswith("prompt_budget_exceeded")
    assert get_provider_health(manifest.provider_id).consecutive_failures == 0
    assert circuit_is_open(manifest.provider_id) is False


def test_repeated_prompt_budget_rejections_never_open_the_circuit() -> None:
    """Five real faults open the circuit; five oversized prompts must not."""
    exc = PromptBudgetExceededError("prompt_budget_exceeded: too big", telemetry={"status": "rejected"})
    router, manifest = _budget_rejecting_router(exc)
    reset_provider_health(manifest.provider_id)

    for _ in range(6):
        router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(
                task_kind="normalization_assist",
                prompt="CURRENT USER",
                system_prompt="SYSTEM MUST SURVIVE",
                max_output_tokens=64,
            ),
            output_mode="text",
            task=SimpleNamespace(task_id="task-1"),
            source_context=None,
        )

    assert circuit_is_open(manifest.provider_id) is False
    assert get_provider_health(manifest.provider_id).consecutive_failures == 0


def test_manifest_validation_rejection_does_not_open_the_circuit() -> None:
    router, manifest = _budget_rejecting_router(
        PromptBudgetExceededError("x", telemetry={})
    )
    router.registry.build_adapter.return_value.run_text_task.side_effect = (
        ProviderInvocationValidationError(
            "selected context source 0 is missing content_hash"
        )
    )
    reset_provider_health(manifest.provider_id)

    with mock.patch("core.memory_first_router.record_provider_failure") as failure_mock:
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(
                task_kind="normalization_assist",
                prompt="CURRENT USER",
                system_prompt="SYSTEM MUST SURVIVE",
                max_output_tokens=64,
            ),
            output_mode="text",
            task=SimpleNamespace(task_id="task-1"),
            source_context=None,
        )

    failure_mock.assert_not_called()
    assert response is None
    assert error is not None
    assert error.startswith("context_manifest_invalid:")
    assert get_provider_health(manifest.provider_id).consecutive_failures == 0
    assert circuit_is_open(manifest.provider_id) is False


def test_ordinary_provider_faults_still_record_a_provider_failure() -> None:
    """The distinct catch must not swallow real provider faults."""
    router, manifest = _budget_rejecting_router(PromptBudgetExceededError("x", telemetry={}))
    router.registry.build_adapter.return_value.run_text_task.side_effect = RuntimeError("connection refused")
    reset_provider_health(manifest.provider_id)

    with mock.patch("core.memory_first_router.record_provider_failure") as failure_mock:
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=ModelRequest(
                task_kind="normalization_assist",
                prompt="CURRENT USER",
                system_prompt="SYSTEM MUST SURVIVE",
                max_output_tokens=64,
            ),
            output_mode="text",
            task=SimpleNamespace(task_id="task-1"),
            source_context=None,
        )

    failure_mock.assert_called_once()
    assert response is None
    assert "connection refused" in str(error)


def test_completed_call_links_context_selection_to_the_final_payload_receipt() -> None:
    router, manifest = _budget_rejecting_router(
        PromptBudgetExceededError("unused", telemetry={})
    )
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="RAW-PROMPT-DO-NOT-EMIT-913",
        system_prompt="SYSTEM",
        max_output_tokens=64,
        context={
            "context_manifest_id": "context-manifest-turn-401",
            "context_manifest_trace_id": "context-trace-turn-401",
        },
    )

    def completed_provider(call_request: ModelRequest) -> SimpleNamespace:
        call_request.metadata["provider_manifest_id"] = "provider-manifest-turn-402"
        call_request.metadata["provider_payload_hash"] = "a" * 64
        return SimpleNamespace(output_text="safe", usage={})

    router.registry.build_adapter.return_value.run_text_task.side_effect = completed_provider
    source_context = {"request_id": "request-turn-400"}
    with mock.patch("core.memory_first_router._emit_model_routing_event") as emit_mock:
        _adapter, response, error = router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode="text",
            task=SimpleNamespace(task_id="task-turn-403"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert source_context["provider_manifest_links"] == [
        {
            "context_manifest_id": "context-manifest-turn-401",
            "context_manifest_trace_id": "context-trace-turn-401",
            "provider_manifest_id": "provider-manifest-turn-402",
            "payload_hash": "a" * 64,
            "provider_id": manifest.provider_id,
            "model_id": manifest.model_name,
        }
    ]
    completed = [
        call
        for call in emit_mock.call_args_list
        if call.args[1] == "model.call_completed"
    ]
    assert len(completed) == 1
    assert completed[0].kwargs["context_manifest_id"] == "context-manifest-turn-401"
    assert completed[0].kwargs["context_manifest_trace_id"] == "context-trace-turn-401"
    assert completed[0].kwargs["provider_manifest_id"] == "provider-manifest-turn-402"
    assert completed[0].kwargs["provider_payload_hash"] == "a" * 64
    assert completed[0].kwargs["response_control"]["ordinary_chat_output"]["allowed"] is True
    assert "RAW-PROMPT-DO-NOT-EMIT-913" not in str(completed[0].kwargs)


def _emitted_grounding_events(prompt_budget: dict[str, Any]) -> list[Any]:
    request = ModelRequest(
        task_kind="normalization_assist",
        prompt="CURRENT USER",
        system_prompt="SYSTEM MUST SURVIVE",
        max_output_tokens=64,
    )
    request.metadata["prompt_budget"] = prompt_budget
    with mock.patch("core.memory_first_router._emit_model_routing_event") as emit_mock:
        _emit_grounding_dropped_event(
            {"origin": "local"},
            manifest=SimpleNamespace(provider_id="ollama-local:qwen2.5:7b", model_name="qwen2.5:7b"),
            request=request,
            identity={},
        )
    return emit_mock.call_args_list


def test_dropped_grounding_is_announced_as_its_own_event() -> None:
    """Losing the grounding must leave a named signal, not only a nested telemetry dict."""
    calls = _emitted_grounding_events(
        {
            "grounding_dropped": True,
            "retrieval_present": True,
            "dropped_retrieved_messages": 1,
            "num_ctx": 4096,
            "available_prompt_tokens": 3856,
            "estimated_prompt_tokens_before": 9000,
        }
    )

    assert len(calls) == 1
    assert calls[0].args[1] == "model_lane_grounding_dropped"
    assert "not grounded" in calls[0].args[2]
    assert calls[0].kwargs["dropped_retrieved_messages"] == 1
    assert calls[0].kwargs["prompt_budget"]["grounding_dropped"] is True


def test_no_grounding_event_when_grounding_survived() -> None:
    """A retrieval turn that kept its grounding must not cry wolf."""
    assert (
        _emitted_grounding_events(
            {"grounding_dropped": False, "retrieval_present": True, "dropped_history_messages": 2}
        )
        == []
    )
    assert _emitted_grounding_events({}) == []


def _long_session_messages(pairs: int, *, system_prompt: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _CONTEXT_SUMMARY},
    ]
    for index in range(pairs):
        messages.append({"role": "user", "content": f"filler question {index} " + ("x" * 600)})
        messages.append({"role": "assistant", "content": f"filler answer {index} " + ("y" * 600)})
    messages.append({"role": "user", "content": "What database does my project use?"})
    return messages


_CONTEXT_SUMMARY = (
    "<context_summary>\nProject Zephyr uses PostgreSQL; deploy port 8791.\n</context_summary>"
)


def test_prompt_budget_keeps_context_summary_when_shedding_verbatim_history() -> None:
    """The compacted summary outlives the raw turns it stands in for."""
    system_prompt = "You are VOOL. Never fabricate."
    result = fit_messages_to_context_window(
        _long_session_messages(30, system_prompt=system_prompt),
        num_ctx=4096,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    kept = "".join(str(message.get("content")) for message in result.messages)
    assert result.telemetry["dropped_history_messages"] > 0
    assert result.telemetry["dropped_context_summaries"] == 0
    assert "<context_summary>" in kept
    assert "PostgreSQL" in kept
    assert result.telemetry["system_prompt_preserved"] is True
    assert (
        result.telemetry["estimated_prompt_tokens_after"]
        <= result.telemetry["available_prompt_tokens"]
    )


def test_prompt_budget_sheds_context_summary_only_as_a_last_resort() -> None:
    system_prompt = "You are VOOL. Never fabricate."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "<context_summary>" + ("z" * 80000) + "</context_summary>"},
        {"role": "user", "content": "current turn?"},
    ]

    result = fit_messages_to_context_window(
        messages,
        num_ctx=4096,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    assert result.telemetry["dropped_context_summaries"] == 1
    assert result.telemetry["system_prompt_preserved"] is True


def test_token_estimate_covers_real_cjk_token_count() -> None:
    """8000 chars of Chinese is 5645 real qwen2.5:7b tokens; chars/4 claimed 2006."""
    estimate = estimate_message_tokens([{"role": "user", "content": _pad(_CJK_SENTENCE, 8000)}])

    assert estimate >= 5645


def test_token_estimate_does_not_balloon_on_english_prose() -> None:
    """8000 chars of English is 1640 real tokens: covering CJK must not tax the common case."""
    estimate = estimate_message_tokens([{"role": "user", "content": _pad(_ENGLISH_SENTENCE, 8000)}])

    assert estimate >= 1640
    assert estimate <= 1640 * 2


def test_token_estimate_is_content_aware_not_a_flat_ratio() -> None:
    """Equal character counts of prose and CJK must not produce equal estimates."""
    english = estimate_message_tokens([{"role": "user", "content": _pad(_ENGLISH_SENTENCE, 8000)}])
    cjk = estimate_message_tokens([{"role": "user", "content": _pad(_CJK_SENTENCE, 8000)}])

    assert cjk > english * 2


def test_token_estimate_covers_digit_dense_content() -> None:
    """The tokenizer emits one token per digit: 6000 digits is ~6000 tokens, not 1500."""
    estimate = estimate_message_tokens([{"role": "user", "content": _pad("8471926350", 6000)}])

    assert estimate >= 6000


def test_cjk_prompt_is_rejected_instead_of_silently_left_truncated() -> None:
    """The defect: a CJK turn read as fitting, so the provider truncated the system prompt."""
    system_prompt = "You are VOOL. Never fabricate."
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _pad(_CJK_SENTENCE, 8000)},
    ]

    with pytest.raises(PromptBudgetExceededError, match="prompt_budget_exceeded"):
        fit_messages_to_context_window(
            messages,
            num_ctx=4096,
            output_reserve_tokens=240,
            protected_system_prompt=system_prompt,
        )


def test_prompt_budget_accepts_system_prompt_with_surrounding_whitespace() -> None:
    """A prompt template's trailing newline must not read as a truncated system prompt."""
    system_prompt = "You are VOOL. Never fabricate.\n"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "hello"},
    ]

    result = fit_messages_to_context_window(
        messages,
        num_ctx=4096,
        output_reserve_tokens=240,
        protected_system_prompt=system_prompt,
    )

    assert result.telemetry["status"] == "fit"
    assert result.telemetry["system_prompt_preserved"] is True
