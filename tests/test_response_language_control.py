from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest, ModelResponse
from core.agent_runtime.response import _validate_final_chat_output
from core.memory_first_router import MemoryFirstRouter
from core.response_language_policy import (
    ResponseLanguageCheck,
    check_response_language,
    response_language_policy_for_text,
)
from storage.model_provider_manifest import ModelProviderManifest

SET5_15 = (
    "I have 1,000,000 units of local currency in Pyongyang, North Korea. I travel to Seoul, "
    "South Korea, and try to spend it. What is the currency of North Korea, what is the "
    "currency of South Korea, and why would this transaction fail? Do NOT use live exchange "
    "tools."
)
SET5_15_LIVE_OUTPUT = (
    "In North Korea, the local currency is the North Korean won "
    "(官方名称：朝鮮人民兌，韩文：원，朝鲜文：won）。而在韩国首尔，则使用的是韩元"
    "（韩文：원，朝鲜文：圆）。\n\n"
    "你携带的1,000,000单位北韩当地货币在首尔无法直接使用的原因有几点：\n\n"
    "1. **货币兑换机制**：各国货币之间需要进行官方或合法的兑换操作。"
    "个人携带大量当地货币入境通常会被边境检查站拦截并要求兑换。\n"
    "2. **外汇管制**：韩国对外国货币和交易有严格的监管，非正规渠道难以实现货币转换。\n"
    "3. **安全因素**：携带大量现金存在安全隐患，银行和金融机构可能不愿意接收未经官方兑换的外币。\n\n"
    "因此，你携带北韩当地货币前往首尔后想要直接消费是无法实现的。"
    "你需要通过正式渠道进行货币兑换，并且通常需要提供合理的购买或入境理由来解释携带如此多现金的原因。"
)
ENGLISH_REPAIR = (
    "North Korea uses the North Korean won (KPW), while South Korea uses the South Korean "
    "won (KRW). KPW is not generally accepted or freely convertible in Seoul, so it cannot "
    "be spent there directly."
)
ENGLISH_POLICY = {"expected_language": "en", "reason": "english_user_turn"}


def _manifest() -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="language-test",
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


@pytest.fixture(autouse=True)
def _authorship_certified():
    """Certify this exact model identity before every test in this file.

    These tests drive `_invoke_manifest` for its LANGUAGE control. A later lane put
    an authorship fence in front of that path: an answer from a model with no
    completed tool-certification run is refused before the language repair is ever
    reached, so the three tests below were failing on an unmet precondition, not on
    the behaviour they name. The certification helper is the same one the served
    language-parity tests use, so this proves the intended machinery executes rather
    than routing around the fence.
    """
    from tests._authorship_certification import certify_for_authorship

    certify_for_authorship(_manifest())


def _request() -> ModelRequest:
    return ModelRequest(
        task_kind="conversation",
        prompt=SET5_15,
        messages=[{"role": "user", "content": SET5_15}],
        max_output_tokens=472,
        metadata={
            "ordinary_chat_output_policy": {
                "mode": "ordinary_chat",
                "prompt_profile": "chat_minimal",
            },
            "response_language_policy": ENGLISH_POLICY,
            "defer_stream_until_verified": True,
        },
    )


@pytest.mark.parametrize(
    "answer",
    (
        SET5_15_LIVE_OUTPUT,
        "完全中文",
        "这是一个完全使用中文书写的回答，其中包含足够多的文字来解释数据库索引。",
        "これは日本語だけで書かれた詳しい回答であり、検索の仕組みを説明します。",
        "이 답변은 한국어로만 작성되었으며 데이터베이스 검색 방법을 자세히 설명합니다.",
        "Этот ответ полностью написан по-русски и подробно объясняет работу индекса.",
        "هذه إجابة مكتوبة بالكامل باللغة العربية وتشرح كيفية عمل فهرس قاعدة البيانات.",
        "यह उत्तर पूरी तरह हिन्दी में लिखा गया है और खोज अनुक्रमणिका समझाता है।",
        "คำตอบนี้เขียนเป็นภาษาไทยทั้งหมดและอธิบายการทำงานของดัชนีฐานข้อมูล",
        "Αυτή η απάντηση είναι ολόκληρη γραμμένη στα ελληνικά και εξηγεί το ευρετήριο.",
        "תשובה זו כתובה כולה בעברית ומסבירה בפירוט כיצד פועל אינדקס מסד נתונים.",
        "Այս պատասխանը ամբողջությամբ հայերեն է և մանրամասն բացատրում է տվյալների ինդեքսը։",
    ),
)
def test_dominant_unexpected_script_variants_fail_english_policy(answer: str) -> None:
    result = check_response_language(answer, ENGLISH_POLICY)

    assert not result.compliant
    assert result.violations == ("unexpected_output_language",)


@pytest.mark.parametrize(
    "answer",
    (
        "Use an index to reduce the number of rows scanned.",
        "The Korean name 원 is a single label in an otherwise English answer.",
        "Pyongyang (평양) is the capital named in this English explanation.",
        "The official label 朝鮮民主主義人民共和國 appears here as a proper name, not prose.",
        "The result is 42 ✅, and the units remain USD.",
        "El índice reduce the scan because Latin-script words remain operator-readable.",
        "L'index accélère la recherche while preserving the requested explanation.",
        "API, HTTP, SQL, and JSON are ordinary technical labels.",
        "A short annotation 中文 is acceptable.",
        "Use `данные_пользователя` as the identifier in this English explanation.",
        "```python\nсообщение = 'данные пользователя'\nprint(сообщение)\n```",
        "English prose stays dominant even with labels 한국어 中文 العربية русский.",
    ),
)
def test_english_policy_preserves_code_names_and_bounded_foreign_labels(answer: str) -> None:
    assert check_response_language(answer, ENGLISH_POLICY).compliant


def test_translation_bilingual_cjk_user_and_code_prompts_do_not_enable_english_guard() -> None:
    prompts = (
        "Translate this answer to Japanese.",
        "Give me a bilingual English and Korean explanation.",
        "Write a multilingual product description.",
        "Respond using Russian for this entire answer.",
        "Explain the result in both English and Chinese.",
        "请解释为什么数据库索引更快。",
        "Explain this code:\n```python\nprint('안녕')\n```",
    )

    assert all(
        response_language_policy_for_text(prompt).expected_language == "none"
        for prompt in prompts
    )


def test_set5_15_exact_live_failure_gets_one_bounded_local_english_replacement() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(
            output_text=SET5_15_LIVE_OUTPUT,
            usage={"prompt_tokens": 1561, "completion_tokens": 213},
            finish_reason="stop",
        ),
        ModelResponse(
            output_text=ENGLISH_REPAIR,
            usage={"prompt_tokens": 410, "completion_tokens": 48},
            finish_reason="stop",
        ),
    ]
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter
    source_context: dict[str, object] = {}
    events: list[dict[str, object]] = []

    def _capture(_context, *, event_type, message, details=None):
        events.append({"event_type": event_type, "message": message, **dict(details or {})})

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch("core.memory_first_router.emit_runtime_event", side_effect=_capture),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="set5-15-language-reproduction"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert response.output_text == ENGLISH_REPAIR
    assert adapter.run_text_task.call_count == 2
    retry_request = adapter.run_text_task.call_args_list[1].args[0]
    assert retry_request.metadata["response_control_retry"] == 1
    assert retry_request.max_output_tokens == 472
    assert retry_request.messages[-2] == {
        "role": "assistant",
        "content": SET5_15_LIVE_OUTPUT,
    }
    assert retry_request.messages[-1] == {
        "role": "user",
        "content": "Return the answer in English only, while preserving the answer's meaning.",
    }
    control = response.constraint_result["response_control"]
    assert control["retry_attempted"] is True
    assert control["retry_succeeded"] is True
    assert control["fallback_applied"] is False
    assert control["response_language"] == {"compliant": True, "violations": []}
    completed = next(event for event in events if event["event_type"] == "model.call_completed")
    assert completed["response_control"] == control
    assert source_context["response_control"] == control
    retry_event = next(
        event for event in events if event["event_type"] == "model.response_control_retry"
    )
    assert retry_event["violations"] == ["unexpected_output_language"]


def test_failed_language_repair_fails_honestly_and_canonical_seam_records_fallback() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.side_effect = [
        ModelResponse(output_text=SET5_15_LIVE_OUTPUT, finish_reason="stop"),
        ModelResponse(
            output_text="这次替换答案仍然完全使用中文，因此不能作为英语回答提交。",
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
            task=SimpleNamespace(task_id="set5-15-language-repair-failed"),
            source_context=source_context,
        )

    assert error is None
    assert response is not None
    assert adapter.run_text_task.call_count == 2
    assert response.output_text == "I couldn't return that response in English. Please try again."
    control = response.constraint_result["response_control"]
    assert control["retry_attempted"] is True
    assert control["retry_succeeded"] is False
    assert control["fallback_applied"] is True
    assert control["response_language"] == {
        "compliant": False,
        "violations": ["unexpected_output_language"],
    }

    canonical_context = {"response_language_policy": ENGLISH_POLICY}
    canonical = _validate_final_chat_output(
        SET5_15_LIVE_OUTPUT,
        source_context=canonical_context,
    )
    assert canonical == "I couldn't return that response in English. Please try again."
    assert canonical_context["response_control"]["final_ui"] == {
        "ordinary_chat_output": {"allowed": True, "reasons": [], "signal_groups": []},
        "response_language": {
            "compliant": False,
            "violations": ["unexpected_output_language"],
        },
        "answer_completeness": {
            "incomplete": False,
            "reasons": [],
            "has_content": True,
            "degenerate": False,
        },
        # A failed language repair is not a leaked tool call; the two verdicts stay separate.
        "tool_invocation_rejected": False,
        "fallback_applied": True,
    }


def test_language_detector_is_load_bearing_in_router_control() -> None:
    adapter = mock.Mock()
    adapter.run_text_task.return_value = ModelResponse(
        output_text=SET5_15_LIVE_OUTPUT,
        finish_reason="stop",
    )
    router = MemoryFirstRouter(registry=mock.Mock())
    router.registry.build_adapter.return_value = adapter

    with (
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
        mock.patch("core.memory_first_router.circuit_is_open", return_value=False),
        mock.patch(
            "core.memory_first_router.check_response_language",
            return_value=ResponseLanguageCheck(compliant=True),
        ),
    ):
        _, response, error = router._invoke_manifest(
            manifest=_manifest(),
            request=_request(),
            output_mode="plain_text",
            task=SimpleNamespace(task_id="language-detector-sabotage"),
            source_context={},
        )

    assert error is None
    assert response is not None
    assert response.output_text == SET5_15_LIVE_OUTPUT
    assert adapter.run_text_task.call_count == 1
