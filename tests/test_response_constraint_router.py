from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.memory_first_router import MemoryFirstRouter
from core.raw_output_contract import parse_raw_output_contract
from storage.model_provider_manifest import ModelProviderManifest
from tests.live.runtime_model_gauntlet import load_cases


def _manifest(*, local: bool) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="constraint-test",
        model_name="test-model",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="test",
        license_reference="test",
        weight_location="external",
        runtime_dependency="test",
        capabilities=["summarize"],
        runtime_config={
            "base_url": (
                "http://127.0.0.1:11434"
                if local
                else "https://example.invalid"
            )
        },
        metadata={
            "deployment_class": "local" if local else "remote",
            "cost_class": "free_local" if local else "remote_unknown",
        },
    )


def _certified_manifest(*, local: bool) -> ModelProviderManifest:
    """The probe manifest, certified to author a final answer.

    `core.final_answer_authorship` refuses an uncertified loopback model BEFORE its adapter is
    built, so an uncertified probe never reaches the response-constraint router at all and this
    file would test the authorship fence instead of the router it names. Certifying is what an
    operator does; it does not soften the authority. Same remedy the authorship lane applies to
    its own probes in `tests/test_v050_fastpath_authority_and_call_accounting.py`.
    """
    manifest = _manifest(local=local)
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    return manifest


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt="Is it ready? Answer in one word.",
        messages=[
            {
                "role": "user",
                "content": "Is it ready? Answer in one word.",
            }
        ],
        metadata={
            "response_constraint": {
                "exact_words": 1,
                "max_words": 1,
                "exact_sentences": None,
                "max_sentences": None,
            },
            "defer_stream_until_verified": True,
        },
    )


def _ordinary_chat_request(*, language: bool = False) -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt="Explain the library code briefly.",
        messages=[{"role": "user", "content": "Explain the library code briefly."}],
        metadata={
            "ordinary_chat_output_policy": {
                "mode": "ordinary_chat",
                "prompt_profile": "chat_minimal",
            },
            "response_language_policy": (
                {"expected_language": "en", "reason": "english_user_turn"}
                if language
                else {}
            ),
            "defer_stream_until_verified": True,
        },
    )


def _raw_output_request() -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt="Write a haiku. No markdown, no internal thought.",
        messages=[
            {
                "role": "user",
                "content": "Write a haiku. No markdown, no internal thought.",
            }
        ],
        metadata={
            "raw_output_contract": {
                "raw_only": True,
                "no_markdown": True,
                "no_internal_thought": True,
                "no_json": False,
            },
            "defer_stream_until_verified": True,
        },
    )


def test_provider_output_is_cleaned_before_it_becomes_a_candidate() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text=(
            "Snow rests on pine\nMoonlight crosses fields\nDawn warms frozen air\n\n"
            "Identify core concept...\nSelect concrete imagery...\nVerify syllable counts..."
        ),
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_raw_output_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="raw-output-local"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Snow rests on pine\nMoonlight crosses fields\nDawn warms frozen air"
    assert response.constraint_result["response_control"]["raw_output"] == {
        "changed": True,
        "rejected": False,
        "compliant": True,
        "violations": [],
        "actions": ["trailing_scratchpad_removed"],
    }


def test_unrestricted_multiline_output_passes_once_without_raw_repair() -> None:
    adapter = mock.Mock()
    answer = (
        "1. Heat moves from warmer material to cooler material.\n"
        "2. 28 × 13 = 364.\n"
        "3. The object {keeps its original braces}."
    )
    adapter.run_text_task.return_value = ModelResponse(output_text=answer)
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_ordinary_chat_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="unrestricted-multiline"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == answer
    assert adapter.run_text_task.call_count == 1
    assert "raw_output" not in response.constraint_result["response_control"]


def test_local_raw_shape_violation_gets_one_bounded_retry() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(
            output_text="Identify a theme\nChoose an image\nCheck the line count",
            usage={"prompt_tokens": 8, "output_tokens": 10},
        ),
        ModelResponse(
            output_text="Iron seeks its hidden north\nQuiet fields pull stars together",
            usage={"prompt_tokens": 12, "output_tokens": 9},
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = ModelRequest(
        task_kind="conversation",
        prompt="Write a two-line poem about attraction. Final answer only.",
        messages=[
            {
                "role": "user",
                "content": "Write a two-line poem about attraction. Final answer only.",
            }
        ],
        metadata={
            "raw_output_contract": {
                "raw_only": True,
                "no_markdown": False,
                "no_internal_thought": False,
                "no_json": False,
                "no_punctuation": False,
                "no_trailing_punctuation": False,
                "no_title": False,
                "exact_text": None,
                "exact_words": None,
                "exact_sentences": None,
                "exact_lines": 2,
                "bullet_count": None,
                "bullet_marker": None,
                "delimiter": None,
            },
            "defer_stream_until_verified": True,
        },
    )

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="raw-output-line-retry"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == (
        "Iron seeks its hidden north\nQuiet fields pull stars together"
    )
    assert response.usage == {"prompt_tokens": 20, "output_tokens": 19}
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.metadata["raw_output_contract_retry"] == 1
    assert "exactly 2 physical lines" in retry_request.messages[-1]["content"]
    assert response.constraint_result["response_control"]["retry_succeeded"] is True


def test_local_shape_violation_gets_one_bounded_retry() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(
            output_text="Yes, it is ready.",
            usage={"prompt_tokens": 5, "output_tokens": 4},
        ),
        ModelResponse(
            output_text="Yes.",
            usage={"prompt_tokens": 8, "output_tokens": 1},
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch(
            "core.memory_first_router.should_probe_health",
            return_value=False,
        ),
        mock.patch(
            "core.memory_first_router.circuit_is_open",
            return_value=False,
        ),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-local"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Yes."
    assert response.usage == {
        "prompt_tokens": 13,
        "output_tokens": 5,
    }
    assert response.constraint_result["retry_attempted"] is True
    assert response.constraint_result["retry_succeeded"] is True
    assert response.constraint_result["compliant"] is True
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.metadata["response_constraint_retry"] == 1
    assert retry_request.messages[-2]["role"] == "assistant"
    assert retry_request.messages[-1]["role"] == "user"


def test_constraint_retry_links_the_payload_that_produced_the_visible_answer() -> None:
    adapter = mock.Mock()
    invocation = 0

    def run_text_task(request: ModelRequest) -> ModelResponse:
        nonlocal invocation
        invocation += 1
        request.metadata["provider_manifest_id"] = (
            f"provider-manifest-retry-{invocation}"
        )
        request.metadata["provider_payload_hash"] = str(invocation) * 64
        return ModelResponse(
            output_text=("Too many words" if invocation == 1 else "Ready."),
        )

    adapter.run_text_task.side_effect = run_text_task
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.context = {
        "context_manifest_id": "context-manifest-retry-1",
        "context_manifest_trace_id": "context-trace-retry-1",
    }
    source_context: dict[str, object] = {}

    with (
        mock.patch(
            "core.memory_first_router.should_probe_health",
            return_value=False,
        ),
        mock.patch(
            "core.memory_first_router.circuit_is_open",
            return_value=False,
        ),
        mock.patch(
            "core.memory_first_router._emit_model_routing_event"
        ) as emit_mock,
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-final-payload"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Ready."
    assert source_context["provider_manifest_links"] == [
        {
            "context_manifest_id": "context-manifest-retry-1",
            "context_manifest_trace_id": "context-trace-retry-1",
            "provider_manifest_id": "provider-manifest-retry-2",
            "payload_hash": "2" * 64,
            "provider_id": _certified_manifest(local=True).provider_id,
            "model_id": "test-model",
        }
    ]
    completed = [
        call
        for call in emit_mock.call_args_list
        if call.args[1] == "model.call_completed"
    ]
    assert len(completed) == 1
    assert (
        completed[0].kwargs["provider_manifest_id"]
        == "provider-manifest-retry-2"
    )


def test_local_low_information_one_word_gets_grounding_retry() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Different."),
        ModelResponse(output_text="Refreshed."),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Answer with one word: how can a room feel after rearranging it?"
    request.messages = [{"role": "user", "content": request.prompt}]

    with (
        mock.patch(
            "core.memory_first_router.should_probe_health",
            return_value=False,
        ),
        mock.patch(
            "core.memory_first_router.circuit_is_open",
            return_value=False,
        ),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-low-information"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Refreshed."
    assert response.constraint_result["initial_violations"] == [
        "low_information_short_answer"
    ]
    assert response.constraint_result["retry_attempted"] is True
    assert response.constraint_result["retry_succeeded"] is True
    assert response.constraint_result["compliant"] is True
    assert adapter.run_text_task.call_count == 2


def test_local_exact_word_fragment_gets_one_bounded_retry() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Clear and"),
        ModelResponse(output_text="Ready now"),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Is it ready? Answer in exactly two words."
    request.messages = [
        {"role": "user", "content": request.prompt},
    ]
    request.metadata["response_constraint"] = {
        "exact_words": 2,
        "max_words": 2,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-incomplete-fragment"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Ready now"
    assert response.constraint_result["initial_violations"] == [
        "incomplete_fragment"
    ]
    assert response.constraint_result["retry_succeeded"] is True
    assert adapter.run_text_task.call_count == 2


def test_local_coordinate_phrase_is_compressed_without_a_retry() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="Clean and organized.",
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Describe it in exactly two words."
    request.messages = [{"role": "user", "content": request.prompt}]
    request.metadata["response_constraint"] = {
        "exact_words": 2,
        "max_words": 2,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-coordinate"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Clean, organized"
    assert response.constraint_result["retry_attempted"] is False
    assert adapter.run_text_task.call_count == 1


def test_local_invalid_retry_uses_an_honest_format_fallback() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Clear and"),
        ModelResponse(output_text="Clean and"),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Describe it in exactly two words."
    request.messages = [{"role": "user", "content": request.prompt}]
    request.metadata["response_constraint"] = {
        "exact_words": 2,
        "max_words": 2,
        "exact_sentences": None,
        "max_sentences": None,
    }
    source_context: dict[str, object] = {}

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-invalid-retry"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "No answer."
    assert response.constraint_result["retry_succeeded"] is False
    assert response.constraint_result["fallback_applied"] is True
    outcome = source_context["response_control"]["fulfillment_outcome"]
    assert outcome["fulfillment_status"] == "failed"
    assert outcome["failure_stage"] == "output_validation"
    assert outcome["retryable"] is True
    assert "response_constraint:incomplete_fragment" in outcome["failure_codes"]


def test_failed_exact_six_retry_preserves_model_generated_initial_answer() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="VOOL is built by Parad0x Labs locally.",
        usage={"prompt_tokens": 12, "output_tokens": 8},
    )
    adapter.run_structured_task.return_value = ModelResponse(
        output_text='{"words":["Unavailable"]}',
        usage={"prompt_tokens": 18, "output_tokens": 1},
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Answer in exactly six words: identify VOOL and its maker."
    request.messages = [{"role": "user", "content": request.prompt}]
    request.metadata["response_constraint"] = {
        "exact_words": 6,
        "max_words": 6,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-preserve-initial"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "VOOL is built by Parad0x Labs"
    assert response.usage == {
        "prompt_tokens": 30,
        "output_tokens": 9,
    }
    assert response.constraint_result["retry_attempted"] is True
    assert response.constraint_result["retry_succeeded"] is False
    assert response.constraint_result["selected_candidate"] == "initial"
    assert response.constraint_result.get("fallback_applied") is not True
    retry_request = adapter.run_structured_task.call_args.args[0]
    schema = retry_request.contract["json_schema"]
    assert schema["properties"]["words"]["minItems"] == 6
    assert schema["properties"]["words"]["maxItems"] == 6
    assert retry_request.max_output_tokens >= 64


def test_exact_six_retry_uses_validated_model_generated_word_array() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="VOOL by Parad0x Labs.",
        usage={"prompt_tokens": 12, "output_tokens": 4},
    )
    adapter.run_structured_task.return_value = ModelResponse(
        output_text='{"words":["VOOL","is","built","by","Parad0x","Labs"]}',
        usage={"prompt_tokens": 18, "output_tokens": 12},
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Use exactly six words to identify VOOL and its maker."
    request.messages = [{"role": "user", "content": request.prompt}]
    request.metadata["response_constraint"] = {
        "exact_words": 6,
        "max_words": 6,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-structured-six"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "VOOL is built by Parad0x Labs"
    assert response.output_mode == "plain_text"
    assert response.constraint_result["retry_attempted"] is True
    assert response.constraint_result["retry_succeeded"] is True
    assert response.constraint_result["selected_candidate"] == "retry"
    assert response.constraint_result.get("fallback_applied") is not True


def test_local_clipped_refusal_gets_a_retry_then_safe_fallback() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Clear and"),
        ModelResponse(output_text="I couldn't"),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.prompt = "Describe it in exactly two words."
    request.messages = [{"role": "user", "content": request.prompt}]
    request.metadata["response_constraint"] = {
        "exact_words": 2,
        "max_words": 2,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-clipped-refusal"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "No answer."
    assert response.constraint_result["retry_succeeded"] is False
    assert response.constraint_result["fallback_applied"] is True
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert "Do not apologize" in retry_request.prompt


def test_constraint_retry_uses_only_current_turn_and_failed_draft() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Clear and"),
        ModelResponse(output_text="Tidy workspace"),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request()
    request.system_prompt = "You are concise."
    request.messages = [
        {"role": "user", "content": "Old unrelated conversation."},
        {"role": "assistant", "content": "Old unrelated response."},
        {"role": "user", "content": "Describe it in exactly two words."},
    ]
    request.metadata["response_constraint"] = {
        "exact_words": 2,
        "max_words": 2,
        "exact_sentences": None,
        "max_sentences": None,
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-current-turn-only"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Tidy workspace"
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.messages == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "Describe it in exactly two words."},
        {"role": "assistant", "content": "Clear and"},
        {"role": "user", "content": retry_request.prompt},
    ]
    assert retry_request.context == {}
    assert retry_request.attachments == []
    assert retry_request.metadata["memory_prompt"] == {"enabled": False}


def test_unknown_cost_lane_is_never_retried_and_is_bounded_locally() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="Ready with a caveat.",
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch(
            "core.memory_first_router.should_probe_health",
            return_value=False,
        ),
        mock.patch(
            "core.memory_first_router.circuit_is_open",
            return_value=False,
        ),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=False),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="constraint-remote"),
            source_context=None,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "Ready"
    assert response.constraint_result["retry_attempted"] is False
    assert response.constraint_result["structurally_trimmed"] is True
    assert adapter.run_text_task.call_count == 1


def test_ordinary_chat_image_prompt_gets_one_bounded_local_repair() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(
            output_text=(
                "Subject: a library card. Camera: wide shot, 35mm lens. "
                "Lighting: cinematic blue hour with film grain."
            ),
        ),
        ModelResponse(output_text="LILAC-8437 is the current library code."),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    source_context: dict[str, object] = {}

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_ordinary_chat_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="ordinary-image-repair"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert response.output_text == "LILAC-8437 is the current library code."
    assert adapter.run_text_task.call_count == 2
    assert response.constraint_result["response_control"]["retry_attempted"] is True
    assert response.constraint_result["response_control"]["fallback_applied"] is False
    assert source_context["response_control"] == response.constraint_result["response_control"]


def test_ordinary_chat_underanswer_gets_one_bounded_local_repair() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Portability."),
        ModelResponse(
            output_text=(
                "A paper notebook can feel more focused, tactile, and independent "
                "of batteries or notifications."
            )
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _ordinary_chat_request()
    request.prompt = "Why might someone keep a paper notebook even when they use a phone?"
    request.messages = [{"role": "user", "content": request.prompt}]

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="ordinary-underanswer-repair"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text.startswith("A paper notebook")
    assert adapter.run_text_task.call_count == 2
    assert response.constraint_result["response_control"]["retry_attempted"] is True
    # The retry is intentionally a different generation shape. A qwen3 initial answer that spends
    # its whole budget reasoning must not blindly repeat that same failure mode.
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.reasoning_mode == "disabled"


def test_ordinary_chat_confirmation_retries_to_preserve_requested_identifier() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="Confirmed."),
        ModelResponse(output_text="BRAMBLE-7194 is confirmed."),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _ordinary_chat_request()
    request.prompt = "Confirm the code BRAMBLE-7194 in one short sentence."
    request.messages = [{"role": "user", "content": request.prompt}]

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="ordinary-confirmation-literal"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "BRAMBLE-7194 is confirmed."
    assert adapter.run_text_task.call_count == 2
    assert response.constraint_result["response_control"]["retry_attempted"] is True


def test_ordinary_chat_budget_retries_then_bounds_the_visible_model_text() -> None:
    long_reply = " ".join(["useful"] * 125) + "."
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=long_reply),
        ModelResponse(output_text=long_reply),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _ordinary_chat_request()
    request.metadata["ordinary_chat_output_policy"] = {
        "mode": "ordinary_chat",
        "detail_requested": "false",
        "max_words": "110",
    }

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="text",
            task=SimpleNamespace(task_id="ordinary-budget"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert len(response.output_text.split()) <= 110
    assert adapter.run_text_task.call_count == 2


def test_unexpected_language_gets_one_bounded_local_repair() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="使用索引可以减少扫描。"),
        ModelResponse(output_text="An index reduces the amount of data scanned."),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=_ordinary_chat_request(language=True),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="ordinary-language-repair"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "An index reduces the amount of data scanned."
    assert adapter.run_text_task.call_count == 2
    assert response.constraint_result["response_control"]["retry_succeeded"] is True


def test_router_build_request_derives_language_policy_from_raw_user_turn() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    interpretation = SimpleNamespace(
        raw_text="Please explain why a library index improves search speed.",
        normalized_text="Context subject: stale Chinese preference",
    )
    request = router._build_request(
        task=SimpleNamespace(task_id="language-policy-derived"),
        classification={},
        interpretation=interpretation,
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={},
    )

    assert request.metadata["response_language_policy"] == {
        "expected_language": "en",
        "reason": "english_user_turn",
    }
    assert request.metadata["defer_stream_until_verified"] is True


def test_canonical_identity_chat_is_held_until_output_validation() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    prompt = "In one sentence, what is VOOL and who builds it?"
    request = router._build_request(
        task=SimpleNamespace(task_id="canonical-identity-stream-guard"),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(raw_text=prompt, normalized_text=prompt),
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={"runtime_event_stream_id": "openclaw:test"},
    )

    assert request.metadata["ordinary_chat_output_policy"]["mode"] == "ordinary_chat"
    assert request.metadata["defer_stream_until_verified"] is True


def test_router_build_request_enforces_spelled_six_word_constraint() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    prompt = "Answer in exactly six words: identify VOOL and its maker."
    request = router._build_request(
        task=SimpleNamespace(task_id="spelled-six-word-constraint"),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(raw_text=prompt, normalized_text=prompt),
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={},
    )

    # Exhaustive on purpose: the serialized contract is what travels to the provider and back, so a
    # field added without a decision about this turn should fail here. `list_items`/
    # `one_item_per_line`/`per_item_sentences` carry the structural layout, and this prompt asks for
    # none, so they are null and False respectively. `presentation_format` is the explicit
    # presentation contract (C19): this prompt requests no format, so it is null.
    assert request.metadata["response_constraint"] == {
        "exact_words": 6,
        "max_words": 6,
        "exact_sentences": None,
        "max_sentences": None,
        "list_items": None,
        "one_item_per_line": False,
        "per_item_sentences": None,
        "presentation_format": None,
    }
    assert request.metadata["defer_stream_until_verified"] is True


def test_router_build_request_enforces_raw_output_contract_and_updates_messages() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    prompt = "Write a haiku. No markdown, no internal thought, no JSON."
    request = router._build_request(
        task=SimpleNamespace(task_id="raw-output-contract"),
        classification={"task_class": "creative_writing"},
        interpretation=SimpleNamespace(raw_text=prompt, normalized_text=prompt),
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={},
    )

    assert request.metadata["raw_output_contract"] == {
        "raw_only": True,
        "no_markdown": True,
        "no_internal_thought": True,
        "no_json": True,
        "no_punctuation": False,
        "no_trailing_punctuation": False,
        "no_title": False,
        "exact_text": None,
        "exact_words": None,
        "exact_sentences": None,
        "exact_lines": None,
        "bullet_count": None,
        "bullet_marker": None,
        "delimiter": None,
        "structured_labels": (),
        "per_item_exact_words": None,
        "row_allowed_values": (),
        "code_deliverable": False,
    }
    assert request.metadata["defer_stream_until_verified"] is True
    assert "Return only the requested deliverable" in request.system_prompt
    assert request.messages[0]["content"] == request.system_prompt


@pytest.mark.parametrize("prompt", tuple(case.prompt for case in load_cases((4,))[:5]))
def test_router_keeps_structured_batch_whole_and_holds_stream_for_binding(prompt) -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    request = router._build_request(
        task=SimpleNamespace(task_id="structured-batch"),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(raw_text=prompt, normalized_text="collapsed but unused"),
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={},
    )

    assert request.prompt == prompt
    assert request.metadata["raw_output_contract"]["structured_labels"] == ("A", "B", "C")
    assert request.metadata["defer_stream_until_verified"] is True
    assert "exactly 3 non-empty physical lines" in request.system_prompt
    assert "LABEL. answer" in request.system_prompt
    assert request.messages[0]["content"] == request.system_prompt
    assert request.messages[-1]["role"] == "user"
    assert request.messages[-1]["content"] == prompt


def test_router_restores_structured_contract_when_downstream_interpretation_is_collapsed() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    prompt = load_cases((4,))[2].prompt
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    collapsed = " ".join(prompt.split())

    request = router._build_request(
        task=SimpleNamespace(task_id="structured-batch-collapsed"),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(raw_text=collapsed, normalized_text=collapsed),
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={"raw_output_contract": contract.to_dict()},
    )

    assert request.metadata["raw_output_contract"] == contract.to_dict()
    assert request.metadata["response_constraint"]["exact_words"] is None
    assert request.metadata["response_constraint"]["list_items"] == 3
    assert request.metadata["response_constraint"]["one_item_per_line"] is True


def test_local_router_repairs_one_bad_batch_then_commits_only_three_bound_rows() -> None:
    prompt = load_cases((4,))[2].prompt
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    request = ModelRequest(
        task_kind="conversation",
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
        metadata={
            "raw_output_contract": contract.to_dict(),
            "ordinary_chat_output_policy": {"mode": "ordinary_chat"},
            "defer_stream_until_verified": True,
        },
    )
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = (
        ModelResponse(output_text="A. cold\nB. slow\nC. down\nAnalysis: verified each answer"),
        ModelResponse(output_text="A) cold\nB] slow\nC> down"),
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_certified_manifest(local=True),
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="structured-batch-repair"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "A. cold\nB. slow\nC. down"
    assert adapter.run_text_task.call_count == 2
    assert response.constraint_result["response_control"]["retry_attempted"] is True
    assert response.constraint_result["response_control"]["retry_succeeded"] is True


def test_router_build_request_keeps_short_answers_grounded_in_the_current_question() -> None:
    router = MemoryFirstRouter(registry=mock.Mock())
    interpretation = SimpleNamespace(
        raw_text="Answer with one word: how can a room feel after rearranging it?",
        normalized_text="Answer with one word: how can a room feel after rearranging it?",
    )
    request = router._build_request(
        task=SimpleNamespace(task_id="short-answer-grounding"),
        classification={},
        interpretation=interpretation,
        context_result=SimpleNamespace(report=SimpleNamespace(to_dict=lambda: {})),
        persona=SimpleNamespace(),
        output_mode="plain_text",
        task_kind="conversation",
        surface="openclaw",
        source_context={},
    )

    assert "First answer the user's actual question" in request.system_prompt
    assert "technical synonym" in request.system_prompt
    assert request.messages[0]["content"] == request.system_prompt
