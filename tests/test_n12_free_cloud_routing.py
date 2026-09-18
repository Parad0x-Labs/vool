from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.agent_runtime.fast_command_surface import maybe_handle_cloud_switch_intent
from core.cloud_model_control import set_cloud_model
from core.memory_first_router import MemoryFirstRouter, ModelExecutionDecision, execution_visibility
from core.model_output_contracts import json_schema_for_mode


def test_cloud_model_auto_enables_the_verified_free_lane(monkeypatch, tmp_path) -> None:
    from core import cloud_escalation_policy as cep
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.setattr("core.openrouter_catalog.pick_auto_free_models", lambda **kwargs: {"general": "vendor/free:free"})
    monkeypatch.setattr("core.cloud_model_control._cloud_key_usable", lambda provider=None: False)
    try:
        ok, _message, chosen = set_cloud_model("auto", provider="openrouter", owner_local=True)
        assert ok is True and chosen == "auto"
        assert cep.load_policy().free_cloud_enabled is True
    finally:
        runtime_paths.configure_runtime_home(None)


def test_resolve_builds_real_requirements_for_key_present_owner_chat(monkeypatch) -> None:
    router = MemoryFirstRouter()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    monkeypatch.setattr("core.memory_first_router._openrouter_key_present", lambda: True)

    def capture(**kwargs):
        captured.update(kwargs)
        return ModelExecutionDecision(source="captured", task_hash="hash")

    router._execute_provider_task = capture  # type: ignore[method-assign]
    router.resolve(
        task=SimpleNamespace(task_id="task", task_summary="answer this"),
        classification={"task_class": "chat_conversation"},
        interpretation=SimpleNamespace(reconstructed_text="answer this"),
        context_result=SimpleNamespace(report=SimpleNamespace(total_tokens_used=lambda: 321)),
        persona=SimpleNamespace(),
        force_model=True,
        surface="cli",
        source_context={"surface": "cli"},
    )
    requirements = captured["source_context"]["cloud_task_requirements"]  # type: ignore[index]
    assert requirements.min_context_tokens == 321
    assert requirements.privacy_class.value == "public"
    assert requirements.required_capabilities == ()


def test_natural_free_model_switch_reports_openrouter_provider(monkeypatch) -> None:
    model = SimpleNamespace(
        model_id="tencent/hy3:free",
        name="HY3 Free",
        context_length=128000,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        output_modalities=("text",),
    )
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda **kwargs: ((model,), 0.0))
    monkeypatch.setattr("core.cloud_model_control.set_cloud_model", lambda *args, **kwargs: (True, "switched", args[0]))
    monkeypatch.setattr("core.runtime_provider_defaults.activate_provider_byok", lambda provider: "openrouter-byok")
    action = maybe_handle_cloud_switch_intent("lets go with hy3", owner_local=True)
    assert action["success"] is True
    assert action["details"]["provider_id"] == "openrouter-byok"
    assert action["details"]["provider_activated"] is True


def test_openai_compatible_structured_request_uses_native_schema() -> None:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": '{"intent":"machine.inspect_specs"}'}}], "usage": {}}
    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="openrouter-byok:free",
            model_name="vendor/free:free",
            metadata={},
            runtime_config={
                "base_url": "https://openrouter.ai/api/v1",
                "supports_json_mode": True,
                "supports_json_schema": True,
                "supports_strict_json_schema": True,
                "timeout_seconds": 5,
            },
        )
    )
    request = ModelRequest(
        task_kind="tool_intent",
        prompt="inspect",
        messages=[{"role": "user", "content": "inspect"}],
        output_mode="tool_intent",
        contract={"mode": "tool_intent", "json_schema": json_schema_for_mode("tool_intent")},
        max_output_tokens=100,
    )
    with mock.patch("adapters.openai_compatible_adapter.requests.post", return_value=response) as post:
        adapter.run_structured_task(request)
    response_format = post.call_args.kwargs["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert "intent" in response_format["json_schema"]["schema"]["required"]


def test_execution_visibility_distinguishes_local_residency_from_active_inference() -> None:
    cached = execution_visibility(ModelExecutionDecision(source="memory_hit", task_hash="cached"))
    remote = execution_visibility(
        ModelExecutionDecision(
            source="free_cloud_boost",
            task_hash="remote",
            provider_id="openrouter-byok",
            model_name="vendor/free:free",
            used_model=True,
            details={"locality": "remote", "active_inference": True},
        )
    )
    assert cached["residency"] == "local" and cached["active_inference"] is False
    assert remote["residency"] == "remote" and remote["active_inference"] is True
