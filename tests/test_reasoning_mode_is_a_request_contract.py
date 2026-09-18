"""Reasoning control is a typed field on the request, not a task-name metadata check.

The incident burned an 8,192-token reasoning allowance on a bounded builder artifact call and then
told the operator the model was unreachable. The cause-side fix already shipped for the audit lane
was `metadata={"workspace_audit_turn": True}` — a TASK-NAME check
(`adapters/openai_compatible_adapter._is_workspace_audit_request`). Growing a second one for the
builder would repeat the mistake per task; AGENT_HANDOVER §1A rule 8 forbids it.

So the request carries `reasoning_mode = auto | disabled | required`. Nomination, proof-artifact
generation and pinned builder artifact generation all declare `disabled`. Adapters translate that
policy only where the knob is supported and keep a truthful error where it is not — an adapter must
never silently claim it honoured a policy it cannot send.

Also pinned here: mandate step 4, a local workspace audit does no adaptive web research.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest


def _request(**overrides):
    fields = {
        "task_kind": "normalization_assist",
        "prompt": "audit this",
        "system_prompt": "you audit",
        "output_mode": "plain_text",
        "max_output_tokens": 700,
    }
    fields.update(overrides)
    return ModelRequest(**fields)


# --------------------------------------------------------------------------------------
# The field itself
# --------------------------------------------------------------------------------------


def test_a_request_carries_a_typed_reasoning_mode_defaulting_to_auto() -> None:
    assert _request().reasoning_mode == "auto"


def test_the_three_reasoning_modes_are_the_whole_vocabulary() -> None:
    from core.model_request_policy import REASONING_MODES, normalize_reasoning_mode

    assert set(REASONING_MODES) == {"auto", "disabled", "required"}
    assert normalize_reasoning_mode("disabled") == "disabled"
    assert normalize_reasoning_mode("nonsense") == "auto"
    assert normalize_reasoning_mode(None) == "auto"


# --------------------------------------------------------------------------------------
# The adapter translates the POLICY, not the task name
# --------------------------------------------------------------------------------------


def _openrouter_adapter():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    manifest = SimpleNamespace(
        provider_id="openrouter-byok:nvidia/nemotron",
        model_name="nvidia/nemotron-3-ultra-550b-a55b:free",
        endpoint="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        runtime_config={},
        metadata={"supported_parameters": ["reasoning"]},
        capabilities=(),
        provider_type="openrouter",
    )
    return OpenAICompatibleAdapter(manifest)


def test_reasoning_disabled_reaches_the_payload_without_any_task_name() -> None:
    adapter = _openrouter_adapter()
    payload = adapter._build_openai_payload(
        _request(reasoning_mode="disabled", metadata={}), stream=False, force_json=False
    )

    assert payload.get("reasoning") == {"enabled": False}


def test_reasoning_auto_leaves_the_knob_alone() -> None:
    adapter = _openrouter_adapter()
    payload = adapter._build_openai_payload(
        _request(reasoning_mode="auto", metadata={}), stream=False, force_json=False
    )

    assert "reasoning" not in payload


@pytest.mark.parametrize("model", ["inclusionai/ling-3.0-flash", "example/unrecognized-helper"])
def test_disabled_policy_does_not_require_a_recognized_model_name(model):
    adapter = _openrouter_adapter()
    adapter.manifest.model_name = model
    adapter.manifest.metadata = {}
    adapter.manifest.runtime_config = {"base_url": "https://openrouter.ai/api/v1"}
    payload = adapter._build_openai_payload(
        _request(reasoning_mode="disabled"), stream=False, force_json=False
    )
    assert payload.get("reasoning") == {"enabled": False}


def test_unknown_strict_endpoint_does_not_receive_an_unsupported_control():
    adapter = _openrouter_adapter()
    adapter.manifest.provider_id = "private:example"
    adapter.manifest.provider_type = "private"
    adapter.manifest.endpoint = "https://example.invalid/v1"
    adapter.manifest.model_name = "unrecognized-helper"
    adapter.manifest.metadata = {}
    adapter.manifest.runtime_config = {"base_url": "https://example.invalid/v1"}
    payload = adapter._build_openai_payload(
        _request(reasoning_mode="disabled"), stream=False, force_json=False
    )
    assert "reasoning" not in payload


def test_the_ollama_think_flag_follows_policy_then_auto_preference(monkeypatch) -> None:
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    manifest = SimpleNamespace(
        provider_id="ollama",
        model_name="qwen3:8b",
        endpoint="http://127.0.0.1:11434",
        api_key="",
        runtime_config={},
        metadata={},
        capabilities=(),
        provider_type="ollama",
    )
    adapter = OpenAICompatibleAdapter(manifest)
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")

    assert adapter._ollama_think_flag(_request(reasoning_mode="disabled", metadata={})) is False
    assert adapter._ollama_think_flag(_request(reasoning_mode="auto", metadata={})) is False
    assert adapter._ollama_think_flag(_request(reasoning_mode="required", metadata={})) is True


def _ollama_adapter():
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    manifest = SimpleNamespace(
        provider_id="ollama",
        model_name="qwen3:8b",
        endpoint="http://127.0.0.1:11434",
        api_key="",
        runtime_config={},
        metadata={},
        capabilities=(),
        provider_type="ollama",
    )
    return OpenAICompatibleAdapter(manifest)


def test_the_ollama_thinking_reserve_is_skipped_when_reasoning_is_explicitly_disabled() -> None:
    """A request-level disable does not reserve a second answer-sized reasoning budget."""

    adapter = _ollama_adapter()
    request = _request(reasoning_mode="disabled", metadata={})

    # context_window=0 means "don't clamp the reserve" (see the function's own docstring), which
    # isolates the thing under test -- whether the reserve is added at all -- from the separate
    # context-window-halving behaviour covered nowhere near this fix.
    assert adapter._thinking_aware_output_budget(3000, context_window=0, request=request) == 3000


def test_the_ollama_thinking_reserve_follows_the_effective_auto_flag(monkeypatch) -> None:

    adapter = _ollama_adapter()
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")

    auto_request = _request(reasoning_mode="auto", metadata={})
    assert adapter._thinking_aware_output_budget(3000, context_window=0, request=auto_request) == 3000
    required = _request(reasoning_mode="required", metadata={})
    assert adapter._thinking_aware_output_budget(3000, context_window=0, request=required) == 3000 + 2048


def test_an_unsupported_policy_is_reported_not_silently_dropped() -> None:
    """`required` on a provider that cannot carry it must surface, not pretend."""
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
    from core.model_request_policy import unsupported_reasoning_policy_error

    manifest = SimpleNamespace(
        provider_id="local-openai-compatible",
        model_name="qwen2.5:7b",
        endpoint="http://127.0.0.1:8000",
        api_key="",
        runtime_config={},
        metadata={},
        capabilities=(),
        provider_type="openai_compatible",
    )
    adapter = OpenAICompatibleAdapter(manifest)
    error = unsupported_reasoning_policy_error(adapter, _request(reasoning_mode="required"))

    assert error, "an unsupportable required-reasoning policy returned no error"
    assert "qwen2.5:7b" in error and "reasoning" in error.lower()


# --------------------------------------------------------------------------------------
# The builder_generation path — rule 8 says this must be covered, not only the audit
# --------------------------------------------------------------------------------------


def test_the_pinned_builder_generation_declares_reasoning_disabled() -> None:
    from core.agent_runtime.builder.pinned_generation import build_pinned_generate_fn

    captured = []

    class _Router:
        def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
            captured.append(request)
            return (None, SimpleNamespace(output_text="print('x')", usage={}), None)

    manifest = SimpleNamespace(provider_id="openrouter-byok:x", model_name="nvidia/nemotron:free")
    budget = SimpleNamespace(
        exhausted=lambda: False, calls=0, seconds_spent=0.0, total_seconds=600.0,
        exhausted_reason="", last_error="",
    )
    generate_fn = build_pinned_generate_fn(
        SimpleNamespace(memory_router=_Router()),
        manifest=manifest,
        task=SimpleNamespace(task_id="t"),
        source_context={},
        budget=budget,
    )
    generate_fn("write median.py")

    assert captured, "the pinned generate_fn made no call"
    assert captured[0].reasoning_mode == "disabled", (
        "a bounded builder artifact call still allows an 8,192-token reasoning burn"
    )


def test_the_builder_no_longer_needs_a_task_name_metadata_check() -> None:
    """Rule 8: do not grow another task-name metadata check. The adapter must decide from policy."""
    import inspect

    from adapters import openai_compatible_adapter

    source = inspect.getsource(openai_compatible_adapter)
    assert "builder_generation" not in source, (
        "the adapter grew a second task-name metadata check instead of reading the policy"
    )


# --------------------------------------------------------------------------------------
# Mandate step 4 — a local workspace audit does no adaptive web research
# --------------------------------------------------------------------------------------


def test_a_local_workspace_audit_suppresses_adaptive_research() -> None:
    from core.agent_runtime.research_tool_loop_facade import ResearchToolLoopFacadeMixin

    class _Curiosity:
        def __init__(self):
            self.calls = 0

        def adaptive_research(self, **kwargs):
            self.calls += 1
            raise AssertionError("adaptive web research ran during a local workspace audit")

    facade = ResearchToolLoopFacadeMixin.__new__(ResearchToolLoopFacadeMixin)
    facade.curiosity = _Curiosity()
    result = facade._collect_adaptive_research(
        task_id="t",
        query_text="audit api/apache/liquefy_apache_repetition_v1.py",
        classification={"task_class": "research"},
        interpretation=None,
        source_context={"workspace_audit_evidence_collected": True},
    )

    assert result.enabled is False
    assert "audit" in str(result.reason or "").lower() or "local" in str(result.reason or "").lower()
