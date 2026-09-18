"""A persisted provider entry cannot stand in for a readable dispatch credential."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import credential_store
from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response


def adapter(**config):
    return OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="openrouter-byok:test-model", provider_name="openrouter-byok",
        model_name="test-model", metadata={}, runtime_config={
            "base_url": "https://openrouter.ai/api/v1",
            "api_path": "/chat/completions", "health_path": "/models",
            "credential_key": "llm.cloud.openrouter", **config,
        },
    ))


@pytest.mark.parametrize("failure", [None, credential_store.CredentialReadError("locked")])
def test_missing_or_unreadable_saved_key_never_sends_health_or_completion(monkeypatch, failure):
    def read(_):
        if failure:
            raise failure
        return None
    monkeypatch.setattr(credential_store, "get_credential", read)
    wire = Mock(side_effect=AssertionError("unauthenticated network request"))
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.get", wire)
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", wire)
    lane = adapter()
    result = lane.health_check()
    assert result["ok"] is False
    assert "provider_credential_unavailable" in result["error"]
    with pytest.raises(RuntimeError, match="provider_credential_unavailable"):
        lane.run_text_task(ModelRequest(task_kind="chat", prompt="Explain an immutable value."))
    wire.assert_not_called()


def test_restoring_saved_key_enables_same_lane_without_manifest_recreation(monkeypatch):
    saved = {}
    monkeypatch.setattr(credential_store, "get_credential", lambda name: saved.get(name))
    lane = adapter()
    with pytest.raises(RuntimeError, match="provider_credential_unavailable"):
        lane._dispatch_binding()
    saved["llm.cloud.openrouter"] = "synthetic-key"
    base, key = lane._dispatch_binding()
    assert lane._headers(base_url=base, api_key=key)["Authorization"] == "Bearer synthetic-key"


def test_environment_key_and_keyless_local_runner_remain_available(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_KEY", "synthetic-env-key")
    monkeypatch.setattr(credential_store, "get_credential", Mock(side_effect=AssertionError("unnecessary vault read")))
    assert adapter(api_key_env="TEST_PROVIDER_KEY")._headers()["Authorization"] == "Bearer synthetic-env-key"
    local = adapter(base_url="http://127.0.0.1:11434/v1", credential_key="")
    assert "Authorization" not in local._headers()


@pytest.mark.parametrize("details", [
    {"block_reason": "401 Client Error: None for url: https://openrouter.ai/api/v1/chat/completions"},
    {"attempted_error_reasons": ["401 Client Error: Unauthorized"]},
    {"attempt_timings": [{"error": "401 Client Error: Unauthorized"}]},
])
def test_real_pin_and_fallback_auth_errors_reach_the_answer(details):
    details = {"attempted": ["openrouter-byok:test-model"], **details}
    decision = SimpleNamespace(source="selected_model_blocked", details=details)
    agent = SimpleNamespace(_live_info_mode=lambda *a, **kw: "")
    text = chat_surface_honest_degraded_response(agent, decision, user_input="Explain an immutable value.")
    assert "OpenRouter" in text
    assert "authentication" in text
    assert "API Keys" in text
    assert "only model" not in text and "Auto" not in text


def test_missing_credential_is_not_reported_as_provider_rejection():
    decision = SimpleNamespace(source="selected_model_blocked", details={
        "attempted": ["openrouter-byok:test-model"],
        "block_reason": "provider_credential_unavailable: saved key is unavailable",
    })
    agent = SimpleNamespace(_live_info_mode=lambda *a, **kw: "")
    text = chat_surface_honest_degraded_response(agent, decision, user_input="Explain an immutable value.")
    assert "OpenRouter" in text and "before sending" in text and "API Keys" in text
    assert "rejected" not in text and "expired" not in text


def test_price_identifier_containing_401_is_not_an_auth_error():
    from core.agent_runtime.memory_runtime import _provider_auth_failure_hint
    assert _provider_auth_failure_hint(SimpleNamespace(
        details={"block_reason": "route_price_above_approved_bound: 1401000"},
    )) == ""
