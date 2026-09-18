"""The final pre-invocation boundary: a tools-required call must never reach the wire without them.

Ranking already checks a manifest's DECLARED capabilities (core/model_selection_policy.py). This
is the second, independent check the operator's closure pass asked for: right before each adapter
sends the ACTUAL built envelope, `assert_envelope_carries_tools` (core/execution_requirements.py)
re-verifies the real payload -- not the request object, not what ranking assumed -- carries either
native tools or a legitimate structured-output fallback. A stale/incorrect manifest, an empty
schema builder, or an adapter that silently drops the tools field is caught here even when
everything upstream looked fine.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
from core.cloud_provider_contract import CloudModelRequest
from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.execution_requirements import RequiredToolsNotOfferedError, assert_envelope_carries_tools

TOOLS = build_cloud_tool_definitions(
    [{"intent": "sandbox.run_command", "description": "Run a bounded command.", "arguments": {"command": "string", "cwd": "string optional"}}]
)


def _cloud_adapter(*, supports_fallback: bool = False) -> OpenAICompatibleAdapter:
    runtime_config = {
        "base_url": "https://openrouter.ai/api/v1",
        "api_path": "/chat/completions",
        "timeout_seconds": 5.0,
    }
    if supports_fallback:
        runtime_config["supports_json_mode"] = True
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="openrouter-byok",
            provider_id="openrouter-byok:vendor/model",
            model_name="vendor/model",
            metadata={"runtime_family": "openai-compatible"},
            runtime_config=runtime_config,
        )
    )


def _ollama_adapter() -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="local-qwen-http",
            provider_id="local-qwen-http:qwen-local",
            model_name="qwen-local",
            metadata={"runtime_family": "ollama"},
            runtime_config={"base_url": "http://127.0.0.1:11434", "timeout_seconds": 5.0},
        )
    )


def _tool_intent_request(*, tools=TOOLS, tools_required: bool = True) -> ModelRequest:
    return ModelRequest(
        task_kind="tool_intent",
        prompt="show the current directory",
        messages=[{"role": "user", "content": "show the current directory"}],
        output_mode="tool_intent",
        tools=tools,
        tool_choice="required",
        tools_required=tools_required,
    )


# --- the pure gate function ---------------------------------------------------------------------


def test_gate_passes_when_tools_are_not_required() -> None:
    assert_envelope_carries_tools(
        tools_required=False, offered_tool_count=0, native_tools_in_envelope=False, structured_fallback_in_envelope=False
    )  # must not raise


def test_gate_raises_when_schema_builder_returned_nothing() -> None:
    with pytest.raises(RequiredToolsNotOfferedError, match="offered=0"):
        assert_envelope_carries_tools(
            tools_required=True, offered_tool_count=0, native_tools_in_envelope=False, structured_fallback_in_envelope=False
        )


def test_gate_raises_when_envelope_carries_neither_native_nor_fallback() -> None:
    """The manifest was offered tools (offered_tool_count > 0), but the built envelope -- what
    will actually be sent -- carries neither. This is the "adapter ignores the tools field" shape."""
    with pytest.raises(RequiredToolsNotOfferedError, match="neither native tools nor"):
        assert_envelope_carries_tools(
            tools_required=True, offered_tool_count=1, native_tools_in_envelope=False, structured_fallback_in_envelope=False
        )


def test_gate_accepts_a_legitimate_structured_output_fallback() -> None:
    """A model with no native tool-calling gets the documented compatibility dialect instead --
    that is allowed, not a defect. Only carrying NEITHER is."""
    assert_envelope_carries_tools(
        tools_required=True, offered_tool_count=1, native_tools_in_envelope=False, structured_fallback_in_envelope=True
    )  # must not raise


# --- adapter-level: OpenAICompatibleAdapter (cloud lane) ----------------------------------------


def test_cloud_adapter_fails_closed_when_tools_required_but_none_offered() -> None:
    """Sabotage shape: the schema builder returns an empty list for a tools-required turn."""
    adapter = _cloud_adapter()
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post:
        with pytest.raises(RequiredToolsNotOfferedError):
            adapter.run_structured_task(_tool_intent_request(tools=()))
    post.assert_not_called()  # never reaches the wire


def test_cloud_adapter_fails_closed_when_neither_native_nor_fallback_available() -> None:
    """Sabotage shape: manifest offers tools but can carry neither native calling nor a
    structured-output fallback (no supports_json_mode/supports_json_schema) -- the "manifest
    claims tool support but envelope drops tools" case, caught even though ranking let it through."""
    adapter = _cloud_adapter(supports_fallback=False)
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post, mock.patch.object(
        adapter, "_native_tools", return_value=()
    ):
        with pytest.raises(RequiredToolsNotOfferedError):
            adapter.run_structured_task(_tool_intent_request())
    post.assert_not_called()


def test_cloud_adapter_still_serves_a_normal_tool_intent_call() -> None:
    """Control: the same adapter, same request, with native tools actually engaged, is unaffected."""
    adapter = _cloud_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "sandbox__run_command", "arguments": '{"command":"pwd","cwd":null}'}}
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        result = adapter.run_structured_task(_tool_intent_request())
    assert post.call_count == 1
    assert result.tool_calls[0].intent == "sandbox.run_command"


# --- adapter-level: Ollama lane -------------------------------------------------------------------


def test_ollama_adapter_fails_closed_when_native_tools_disabled_and_no_fallback() -> None:
    """The exact production gap: ollama_native_tools flag off (or the runtime just doesn't
    advertise native support) AND no json_schema/json_mode support declared -- the model would
    get NOTHING telling it about the tool contract. Must fail closed, not silently chat."""
    adapter = _ollama_adapter()
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post, mock.patch(
        "adapters.openai_compatible_adapter.flag_enabled", return_value=False
    ):
        with pytest.raises(RequiredToolsNotOfferedError):
            adapter.run_structured_task(_tool_intent_request())
    post.assert_not_called()


def test_ollama_adapter_still_serves_a_normal_tool_intent_call() -> None:
    """Control: native tools engaged normally (flag on by default) still works."""
    adapter = _ollama_adapter()
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "message": {
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "sandbox__run_command", "arguments": '{"command":"pwd","cwd":null}'}}
            ],
        },
        "done": True,
        "done_reason": "stop",
    }
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        result = adapter.run_structured_task(_tool_intent_request())
    assert post.call_count == 1
    assert result.tool_calls[0].intent == "sandbox.run_command"


# --- adapter-level: OpenRouterCloudProvider (System B / free-cloud-boost lane) -------------------


class _Transport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        for suffix, response in self.responses.items():
            if str(kwargs["url"]).endswith(suffix):
                return response
        raise AssertionError(f"unexpected URL: {kwargs['url']}")


def _cloud_request(*, tools=TOOLS, tools_required: bool = True) -> CloudModelRequest:
    return CloudModelRequest(
        task_id="t", turn_id="turn", subtask_id="", model_call_id="call",
        model_id="vendor/model", messages=({"role": "user", "content": "hi"},),
        max_output_tokens=50, tools=tools, tool_choice="required", tools_required=tools_required,
    )


def test_openrouter_provider_fails_closed_when_tools_required_but_none_offered() -> None:
    provider = OpenRouterCloudProvider()
    transport = _Transport({"/chat/completions": (200, {}, {"choices": [{"message": {"content": "ignored"}}]})})
    with pytest.raises(RequiredToolsNotOfferedError):
        provider.send_request(transport, _cloud_request(tools=()))
    assert transport.calls == []


def test_openrouter_provider_still_serves_a_normal_tool_call() -> None:
    provider = OpenRouterCloudProvider()
    transport = _Transport(
        {
            "/chat/completions": (
                200,
                {},
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {"id": "c1", "type": "function", "function": {"name": "sandbox__run_command", "arguments": '{"command":"pwd","cwd":null}'}}
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                },
            )
        }
    )
    response = provider.send_request(transport, _cloud_request())
    assert response.tool_calls[0].intent == "sandbox.run_command"


# --- router-level: the terminal state must be typed, never ordinary prose ------------------------


class _RouterHarness:
    """Minimal router fixture: one manifest, ranking mocked to return it, no real network I/O."""

    def __init__(self):
        import unittest.mock as um

        from core.human_input_adapter import HumanInputInterpretation
        from core.identity_manager import load_active_persona
        from core.memory_first_router import MemoryFirstRouter
        from core.model_health import reset_provider_health
        from core.model_registry import ModelRegistry
        from core.task_router import classify, create_task_record
        from core.tiered_context_loader import TieredContextResult
        from storage.db import get_connection
        from storage.migrations import run_migrations

        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.execute("DELETE FROM candidate_knowledge_lane")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)
        self.persona = load_active_persona("default")
        self.interpretation = HumanInputInterpretation(
            raw_text="what files are in this project", normalized_text="what files are in this project",
            reconstructed_text="what files are in this project", intent_mode="request", topic_hints=[],
            reference_targets=[], understanding_confidence=0.72, quality_flags=[],
        )
        self.manifest = self.registry.register_manifest(
            {
                "provider_name": "local-qwen-http", "model_name": "qwen-local", "source_type": "http",
                "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json", "tool_intent"],
                "runtime_config": {"base_url": "http://127.0.0.1:1234"}, "enabled": True,
                "metadata": {"orchestration_role": "drone"},
            }
        )
        self.task = create_task_record("show the current directory")
        self.classification = classify(self.task.task_summary, context=self.interpretation.as_context())
        self.context_result = TieredContextResult(
            bootstrap_items=[], relevant_items=[], cold_items=[], local_candidates=[], swarm_metadata=[],
            report=um.Mock(retrieval_confidence="low", swarm_metadata_consulted=False, cold_archive_opened=False),
            retrieval_confidence_score=0.15, cold_decision=um.Mock(allow=False),
        )
        self.context_result.report.to_dict.return_value = {}
        self.context_result.report.total_tokens_used.return_value = 0
        self.context_result.assembled_context = lambda: "No strong local memory."


def test_router_reports_a_typed_terminal_state_not_prose_when_every_candidate_lacks_tools() -> None:
    """End-to-end proof: when EVERY ranked candidate fails specifically because it cannot carry
    the required tool contract, the turn must terminate with source="required_tools_not_offered",
    not the generic all_ranked_providers_failed label, and used_model must stay False -- never an
    ordinary-looking prose answer standing in for a real tool-backed one."""
    harness = _RouterHarness()
    with mock.patch(
        "core.memory_first_router.rank_provider_candidates",
        return_value=[harness.manifest],
    ), mock.patch.object(
        harness.router,
        "_invoke_manifest",
        return_value=(None, None, "required_tools_not_offered:REQUIRED_TOOLS_NOT_OFFERED: lane=local-qwen-http:qwen-local offered=0"),
    ):
        result = harness.router.resolve(
            task=harness.task,
            classification=harness.classification,
            interpretation=harness.interpretation,
            context_result=harness.context_result,
            persona=harness.persona,
            # The A9 routing-plan identity gate fails a turn closed BEFORE ranking when
            # no server-stamped turn identity is present, so this harness carries the
            # keys a real served turn has. Without them the turn dies as
            # `routing_identity_missing` and never reaches the boundary under test.
            source_context={"surface": "api", "turn_id": "reqtools-turn-1", "session_id": "reqtools-session-1"},
        )

    assert result.source == "required_tools_not_offered"
    assert result.used_model is False
    assert result.output_text is None
    assert all(reason.startswith("required_tools_not_offered:") for reason in result.details["attempted_error_reasons"])


def test_router_keeps_the_generic_label_when_failures_are_mixed() -> None:
    """Control: if even ONE candidate failed for a different reason, the generic
    all_ranked_providers_failed label is still correct -- the typed state must not over-trigger."""
    harness = _RouterHarness()
    # Cost class free_local (base_url on 127.0.0.1), like harness.manifest, so BOTH survive the
    # paid-cloud ranking filter and actually reach the invoke loop -- a paid-cloud second manifest
    # would be excluded before ever being tried, making the mixed-failure premise untestable.
    second_manifest = harness.registry.register_manifest(
        {
            "provider_name": "local-second-http", "model_name": "second-local", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied", "weights_bundled": False, "redistribution_allowed": True,
            "runtime_dependency": "openai-compatible-local-runtime",
            "capabilities": ["summarize", "structured_json", "tool_intent"],
            "runtime_config": {"base_url": "http://127.0.0.1:5678"}, "enabled": True,
            "metadata": {"orchestration_role": "drone"},
        }
    )
    calls = {"n": 0}

    def _invoke(*, manifest, **_kwargs):
        calls["n"] += 1
        if manifest.provider_id == harness.manifest.provider_id:
            return None, None, "required_tools_not_offered:REQUIRED_TOOLS_NOT_OFFERED: offered=0"
        return None, None, "connection_refused"

    with mock.patch(
        "core.memory_first_router.rank_provider_candidates",
        return_value=[harness.manifest, second_manifest],
    ), mock.patch.object(harness.router, "_invoke_manifest", side_effect=_invoke):
        result = harness.router.resolve(
            task=harness.task,
            classification=harness.classification,
            interpretation=harness.interpretation,
            context_result=harness.context_result,
            persona=harness.persona,
            source_context={
                "surface": "api", "_owner_local": True,
                "turn_id": "reqtools-turn-2", "session_id": "reqtools-session-2",
            },
        )

    assert result.source == "no_provider_available"
    assert result.details["reason"] == "all_ranked_providers_failed"
