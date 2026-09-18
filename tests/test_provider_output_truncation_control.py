from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.incomplete_answer import inspect_provider_completion
from core.memory_first_router import MemoryFirstRouter
from core.normalized_provider_result import normalize_model_response
from storage.model_provider_manifest import ModelProviderManifest

SET5_01 = (
    '"I need a RUB script to parse a BAM file and output a CAD model." Define what RUB, BAM, '
    "and CAD mean in the context of computer science and 3D modeling. Do NOT trigger any "
    "financial or forex lookup tools."
)


def _manifest() -> ModelProviderManifest:
    manifest = ModelProviderManifest(
        provider_name="truncation-test",
        model_name="qwen2.5:7b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="test",
        license_reference="test",
        weight_location="external",
        runtime_dependency="test",
        capabilities=["summarize"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "cost_class": "free_local"},
    )
    # `core.final_answer_authorship` refuses an uncertified loopback model BEFORE its adapter
    # is built, so an uncertified probe never reaches the lane this file names and the test
    # would assert the authorship fence instead. Certifying is what an operator does; it does
    # not soften the authority. Same remedy the authorship lane applies to its own probe in
    # tests/test_v050_fastpath_authority_and_call_accounting.py.
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(manifest)
    return manifest


def _request(*, max_output_tokens: int = 440) -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt=SET5_01,
        messages=[{"role": "user", "content": SET5_01}],
        max_output_tokens=max_output_tokens,
        metadata={
            "ordinary_chat_output_policy": {
                "mode": "ordinary_chat",
                "prompt_profile": "chat_minimal",
            },
            "defer_stream_until_verified": True,
        },
    )


@pytest.mark.parametrize(
    "text",
    [
        "RUB is ambiguous. BAM is Binary Alignment Map. CAD is computer-aided design. This script is",
        "The parser emits a CAD model from each BAM record, and",
        "Use these fields: name, position,",
        "The conversion proceeds in three stages:",
        "Call parse_record(record, options=[\"strict\", \"mapped\"",
        "```python\ndef parse_bam(path):\n    return path",
        "The output contains `mesh vertices",
        "The mapping is (read, reference, coordinate",
        "The script can",
        "First parse BAM; then transform the geometry -",
    ],
)
def test_cap_bound_unfinished_variants_are_detected(text: str) -> None:
    result = inspect_provider_completion(
        text,
        finish_reason="",
        usage={"completion_tokens": 440},
        max_output_tokens=440,
    )

    assert result.incomplete
    assert result.at_output_limit
    assert any(reason.startswith("output_cap:") for reason in result.reasons)


@pytest.mark.parametrize(
    "text",
    [
        "BAM means Binary Alignment Map; CAD means computer-aided design.",
        "YES",
        "42",
        "New York",
        "```python\nprint('done')\n```",
    ],
)
def test_complete_answers_at_the_exact_cap_are_not_false_positives(text: str) -> None:
    result = inspect_provider_completion(
        text,
        finish_reason="stop",
        usage={"completion_tokens": 440},
        max_output_tokens=440,
    )

    assert not result.incomplete
    assert result.at_output_limit


@pytest.mark.parametrize(
    ("output_tokens", "text"),
    [
        (439, "This script is"),
        (438, "The result contains ("),
        (439, "```python\nprint('partial')"),
    ],
)
def test_near_cap_heuristic_never_claims_provider_truncation(
    output_tokens: int, text: str
) -> None:
    result = inspect_provider_completion(
        text,
        finish_reason="stop",
        usage={"completion_tokens": output_tokens},
        max_output_tokens=440,
    )

    assert not result.incomplete
    assert not result.at_output_limit


def test_provider_length_reason_is_authoritative_even_when_text_looks_finished() -> None:
    result = inspect_provider_completion(
        "The final sentence has punctuation.",
        finish_reason="length",
        usage={"completion_tokens": 439},
        max_output_tokens=440,
    )

    assert result.incomplete
    assert result.provider_reported_limit
    assert result.reasons == ("provider_finish_reason:length",)


def test_set5_01_exact_truncation_gets_one_complete_bounded_replacement() -> None:
    adapter = mock.Mock()
    truncated = (
        "RUB is not a standard computer-science acronym. BAM means Binary Alignment Map, a "
        "bioinformatics format for aligned sequencing reads. CAD means computer-aided design, "
        "which represents a 3D model. This script is"
    )
    repaired = (
        "RUB has no standard computer-science meaning without more context. BAM means Binary "
        "Alignment Map, a bioinformatics file format. CAD means computer-aided design, the "
        "software and model representation used for 3D design."
    )
    adapter.run_text_task.side_effect = [
        ModelResponse(
            output_text=truncated,
            usage={"prompt_tokens": 82, "completion_tokens": 440},
        ),
        ModelResponse(
            output_text=repaired,
            usage={"prompt_tokens": 132, "completion_tokens": 61},
            finish_reason="stop",
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    source_context: dict[str, object] = {}

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="set5-01-truncation-reproduction"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert response.output_text == repaired
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.max_output_tokens > 440
    assert retry_request.metadata["response_control_retry"] == 1
    control = response.constraint_result["response_control"]
    assert control["retry_attempted"] is True
    assert control["retry_succeeded"] is True
    assert control["provider_completion"]["initial"]["at_output_limit"] is True
    assert control["provider_completion"]["final"]["incomplete"] is False


def test_auxiliary_planner_truncation_never_buys_a_response_control_generation() -> None:
    adapter = mock.Mock()
    adapter.run_structured_task.return_value = ModelResponse(
        output_text='[{"request":"first","depends_on":[]}',
        usage={"completion_tokens": 192},
        finish_reason="length",
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request(max_output_tokens=192)
    request.output_mode = "json_object"
    request.allow_response_control_retry = False
    request.allow_provider_retry = False

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=request,
            output_mode="json_object",
            task=None,
            source_context={},
        )

    assert error is None
    assert response is not None
    assert adapter.run_structured_task.call_count == 1
    control = response.constraint_result["response_control"]
    assert control["retry_attempted"] is False
    assert control["provider_completion"]["initial"]["incomplete"] is True
    assert response.output_text == "", "structured output must never contain an appended prose notice"


@pytest.mark.parametrize("ceiling", [1400, 2048])
def test_incomplete_recovery_capacity_can_grow_above_legacy_clamp(ceiling):
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="The requested summary is", finish_reason="length"),
        ModelResponse(output_text="The requested summary is complete.", finish_reason="stop"),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    with mock.patch("core.memory_first_router.should_probe_health", return_value=False), \
         mock.patch("core.memory_first_router.circuit_is_open", return_value=False):
        _, response, error = router._invoke_manifest(manifest=_manifest(),
            request=_request(max_output_tokens=ceiling), output_mode="plain_text",
            task=None, source_context={})
    assert error is None
    assert adapter.run_text_task.call_count == 2
    # REVISED to the complete-replacement contract (2026-09-17): a length-finish is the
    # provider's own fact the ceiling was too small for THIS model's answer, and the +128
    # legacy clamp repaired nothing twice on live lanes (a capability-less UsePod lane and a
    # declared OpenRouter lane both truncated their repairs). The repair now takes one
    # thinking reserve of room; the paid lane's money gate refuses a repair whose projection
    # exceeds the held reservation, so the room never spends unheld authority.
    assert adapter.run_text_task.call_args_list[1].args[0].max_output_tokens == ceiling + 2048
    assert not response.constraint_result["response_control"]["provider_completion"]["final"]["incomplete"]


def test_auxiliary_planner_empty_provider_response_never_buys_transport_retry() -> None:
    adapter = mock.Mock()
    adapter.run_structured_task.side_effect = RuntimeError(
        "provider response did not include choices"
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    request = _request(max_output_tokens=192)
    request.output_mode = "json_object"
    request.allow_response_control_retry = False
    request.allow_provider_retry = False

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=request,
            output_mode="json_object",
            task=None,
            source_context={},
        )

    assert response is None
    assert "did not include choices" in str(error)
    assert adapter.run_structured_task.call_count == 1


def test_detector_is_load_bearing_in_router_control() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="This script is",
        usage={"completion_tokens": 440},
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch(
            "core.memory_first_router.inspect_provider_completion",
            return_value=SimpleNamespace(
                incomplete=False,
                reasons=(),
                has_content=True,
                as_dict=lambda: {"incomplete": False},
            ),
        ),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="detector-sabotage"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == "This script is"
    assert adapter.run_text_task.call_count == 1


def test_failed_single_repair_cannot_be_committed_as_complete() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text="This script is", usage={"completion_tokens": 440}),
        ModelResponse(
            output_text="The replacement is still",
            usage={"completion_tokens": 568},
            finish_reason="length",
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="truncation-repair-failed"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert adapter.run_text_task.call_count == 2
    assert "Incomplete:" in response.output_text
    assert response.constraint_result["response_control"]["fallback_applied"] is True


def test_openai_and_ollama_finish_reasons_are_preserved() -> None:
    cloud = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id="openrouter-byok:test",
            model_name="vendor/model",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1",
                "api_path": "/chat/completions",
                "timeout_seconds": 5.0,
            },
        )
    )
    cloud_http = mock.Mock()
    cloud_http.raise_for_status.return_value = None
    cloud_http.json.return_value = {
        "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 64},
    }
    with mock.patch(
        "adapters.openai_compatible_adapter.requests.post", return_value=cloud_http
    ):
        cloud_result = cloud.run_text_task(_request(max_output_tokens=64))

    assert cloud_result.finish_reason == "length"
    assert normalize_model_response(cloud_result).finish_reason == "length"

    ollama = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="ollama-local",
            provider_id="ollama-local:test",
            model_name="qwen2.5:7b",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434/v1", "timeout_seconds": 5.0},
        )
    )
    ollama_http = mock.Mock()
    ollama_http.raise_for_status.return_value = None
    ollama_http.json.return_value = {
        "message": {"content": "partial"},
        "done": True,
        "done_reason": "length",
        "eval_count": 64,
    }
    with (
        mock.patch(
            "adapters.openai_compatible_adapter.requests.post", return_value=ollama_http
        ),
        mock.patch("core.local_inference_evidence.record_ollama_generate_benchmark"),
    ):
        ollama_result = ollama.run_text_task(_request(max_output_tokens=64))

    assert ollama_result.finish_reason == "length"


def test_completed_call_receipt_carries_provider_finish_reason() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text="The answer is complete.",
        usage={"completion_tokens": 12},
        finish_reason="stop",
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    events: list[dict[str, object]] = []

    def _capture(_context, *, event_type, message, details=None):
        events.append({"event_type": event_type, **dict(details or {})})

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.emit_runtime_event", side_effect=_capture),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="finish-reason-receipt"),
            source_context={"surface": "api"},
        )

    assert error is None
    assert response is not None
    completed = next(
        event for event in events if event["event_type"] == "model.call_completed"
    )
    assert completed["finish_reason"] == "stop"
