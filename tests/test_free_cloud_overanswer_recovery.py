"""Bounded same-provider recovery for provider output-budget violations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.memory_first_router import MemoryFirstRouter
from core.ordinary_chat_response_guard import (
    inspect_ordinary_chat_output,
    ordinary_chat_output_policy,
    recover_bounded_ordinary_chat_output,
)
from storage.model_provider_manifest import ModelProviderManifest

SET5_01 = (
    '"I need a RUB script to parse a BAM file and output a CAD model." Define what RUB, BAM, '
    "and CAD mean in the context of computer science and 3D modeling. Do NOT trigger any "
    "financial or forex lookup tools."
)
LIGHTNING = "nvidia/nemotron-3.5-lightning:free"


@pytest.mark.parametrize("intro", [
    "Here's a thinking process:",
    "A second sprawling attempt follows.",
    "The requested calculation follows below.",
    "There are several important results.",
    "Here is the answer to your question.",
    "ok heres what i worked out:",
    "lemme break this down for u:",
    "right so the numbers are below",
    "heres ur result now:",
    "yep lets do it this way",
    "The final answer is complete.",
])
def test_recovery_does_not_confuse_an_introduction_with_task_completion(intro: str) -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text",
        user_text="Compute the remaining crates after shipping 35 of 170.",
    )
    text = _overanswer(prefix=intro)
    assert not inspect_ordinary_chat_output(text, policy).allowed
    assert recover_bounded_ordinary_chat_output(text, policy) == ""


@pytest.mark.parametrize("answer", [
    "There are 135 crates remaining.",
    "The delivery departs on Tuesday.",
    "The requested file is unavailable.",
])
def test_recovery_removes_only_the_recognized_unsolicited_tail(answer: str) -> None:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text="Give the result.",
    )
    assert recover_bounded_ordinary_chat_output(
        answer + "\n\nFeel free to ask if you have any other questions.", policy,
    ) == answer


def test_structured_answer_is_preserved_not_replaced_by_its_introduction() -> None:
    prompt = "Allocate 170 crates between two depots in a compact table."
    answer = "Depot allocation:\n\n| Depot | Crates |\n| --- | --- |\n| North | 100 |\n| South | 70 |"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal", output_mode="plain_text", user_text=prompt,
    )
    assert recover_bounded_ordinary_chat_output(answer, policy, current_user_text=prompt) == answer


def _manifest(
    *,
    provider_name: str = "openrouter-byok",
    model_name: str = LIGHTNING,
    base_url: str = "https://openrouter.ai/api/v1",
) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="cloud_fallback_provider",
        license_name="provider terms",
        license_reference="https://example.invalid/terms",
        weight_location="external",
        runtime_dependency="openai-compatible",
        capabilities=["summarize"],
        runtime_config={
            "base_url": base_url,
            "api_path": "/chat/completions",
            "timeout_seconds": 5.0,
        },
        metadata={
            "deployment_class": "cloud",
            "cost_class": "paid_cloud",
            "runtime_family": "openai-compatible",
        },
    )


def _request(
    *,
    prompt: str = SET5_01,
    max_output_tokens: int = 440,
    detail_requested: bool = False,
) -> ModelRequest:
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=(f"Give a detailed answer. {prompt}" if detail_requested else prompt),
    )
    return ModelRequest(
        task_kind="conversation",
        prompt=prompt,
        messages=[{"role": "user", "content": prompt}],
        max_output_tokens=max_output_tokens,
        output_mode="plain_text",
        metadata={
            "ordinary_chat_output_policy": policy,
            "defer_stream_until_verified": True,
        },
    )


def _overanswer(*, prefix: str | None = None) -> str:
    intro = prefix or (
        "RUB is ambiguous and is not a standard computer-science acronym. BAM means Binary "
        "Alignment Map, a bioinformatics alignment format. CAD means computer-aided design, the "
        "software and model representation used for 3D modeling."
    )
    rows = "\n".join(
        f"- Extra item {index} repeats generic implementation background that was not requested."
        for index in range(1, 36)
    )
    return f"{intro}\n\n{rows}\n\nFeel free to ask if you have any other questions."


def _invoke(
    *,
    manifest: ModelProviderManifest,
    request: ModelRequest,
    adapter: mock.Mock,
    reported_cost: str,
):
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    context: dict[str, object] = {}
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        # This helper exercises response control after admission. Paid-authorization policy has its
        # own suite; keep the lane admitted while independently varying the reported spend class
        # that decides whether an automatic repair is free.
        mock.patch("core.memory_first_router.provider_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.reported_cost_class", return_value=reported_cost),
        # Response control starts after authorship admission; do not let certification state
        # left by another test decide whether this controlled adapter can be exercised.
        mock.patch("core.final_answer_authorship.precall_author_verdict", return_value=None),
    ):
        _, response, error = router._invoke_manifest(
            manifest=manifest,
            request=request,
            output_mode="plain_text",
            task=SimpleNamespace(task_id="lightning-overanswer"),
            source_context=context,
        )
    assert error is None
    assert response is not None
    return response, context


@pytest.mark.parametrize("reported_cost", ("free_local", "free_cloud", "paid_cloud"))
def test_automatic_brevity_preserves_complete_answer_without_an_extra_call(reported_cost):
    adapter = mock.Mock()
    answer = _overanswer()
    adapter.run_text_task.return_value = ModelResponse(
        answer, usage={"completion_tokens": 1926}, finish_reason="stop",
        effective_max_output_tokens=2488,
    )
    response, context = _invoke(manifest=_manifest(), request=_request(),
                                adapter=adapter, reported_cost=reported_cost)
    assert response.output_text == answer
    assert adapter.run_text_task.call_count == 1
    assert response.usage["completion_tokens"] == 1926
    control = response.constraint_result["response_control"]
    assert not control["retry_attempted"]
    assert not control["fallback_applied"]
    assert not control["bounded_recovery_applied"]
    assert context["response_control"] == control


def test_brevity_advisory_retains_explicit_completeness_guard():
    policy = {"mode": "ordinary_chat", "brevity_advisory": True, "required_numbered_parts": 2}
    check = inspect_ordinary_chat_output("1. Python is useful for agents.", policy)
    assert not check.allowed and "missing_requested_parts" in check.reasons


def test_explicit_long_detailed_output_is_preserved_without_retry_or_trimming() -> None:
    prompt = "Give a detailed step-by-step list explaining each stage of a compiler pipeline."
    long_answer = _overanswer(prefix="A compiler pipeline has several deliberately detailed stages.")
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_minimal",
        output_mode="plain_text",
        user_text=prompt,
    )
    assert inspect_ordinary_chat_output(long_answer, policy).allowed
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        long_answer,
        usage={"completion_tokens": 900},
        finish_reason="stop",
    )
    response, _ = _invoke(
        manifest=_manifest(),
        request=_request(prompt=prompt, detail_requested=True),
        adapter=adapter,
        reported_cost="free_cloud",
    )
    assert adapter.run_text_task.call_count == 1
    assert response.output_text == long_answer


def test_openrouter_wire_preserves_complete_answer_and_original_request() -> None:
    manifest = _manifest()
    adapter = OpenAICompatibleAdapter(manifest)
    long_http = mock.Mock()
    long_http.raise_for_status.return_value = None
    long_http.json.return_value = {
        "model": LIGHTNING,
        "choices": [{"message": {"content": _overanswer()}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1105, "completion_tokens": 1926},
    }
    repaired_http = mock.Mock()
    repaired_http.raise_for_status.return_value = None
    repaired_http.json.return_value = {
        "model": LIGHTNING,
        "choices": [
            {
                "message": {
                    "content": "RUB is ambiguous; BAM is Binary Alignment Map; CAD is computer-aided design."
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 620, "completion_tokens": 20},
    }
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.provider_cost_class", return_value="free_local"),
        mock.patch("core.memory_first_router.reported_cost_class", return_value="free_cloud"),
        mock.patch(
            "adapters.openai_compatible_adapter.requests.post",
            side_effect=[long_http, repaired_http],
        ) as post,
    ):
        _, response, error = router._invoke_manifest(
            manifest=manifest,
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="lightning-wire-reproduction"),
            source_context={},
        )
    assert error is None and response is not None
    assert post.call_count == 1
    assert [call.kwargs["json"]["model"] for call in post.call_args_list] == [LIGHTNING]
    assert [call.kwargs["json"]["max_tokens"] for call in post.call_args_list] == [2488]
    assert response.output_text == _overanswer()
    assert response.provider_attested_model == LIGHTNING
    assert response.model_name == LIGHTNING


def test_completed_paid_answer_survives_router_and_final_chat_publication():
    from core.agent_runtime.response import _validate_final_chat_output
    answer = "Python is the practical starting point for this runtime. " + " ".join(
        "Keep provider calls separate from task state, and record completed operations before continuing."
        for _ in range(20))
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(answer, usage={"completion_tokens":1482}, finish_reason="stop")
    request = _request(prompt="what is better for an ai runtime agent py or java?")
    response, context = _invoke(manifest=_manifest(), request=request, adapter=adapter, reported_cost="paid_cloud")
    context["ordinary_chat_output_policy"] = request.metadata["ordinary_chat_output_policy"]
    assert _validate_final_chat_output(response.output_text, source_context=context) == answer
    assert adapter.run_text_task.call_count == 1
