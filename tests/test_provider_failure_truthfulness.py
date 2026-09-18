"""Genuinely new coverage for the truthful provider-failure family (mission 2026-09-18).

The pre-existing diagnostic (tests/test_provider_credential_dispatch.py) pinned the pre-dispatch
gate and the 401/missing-key wording. These cases are materially different carriers and causes:
a 429 carried on ``attempt_timings`` (not ``block_reason``), a cancelled call that must never read
as "no usable reply", an HTTP 403 that must not be claimed as an authentication rejection, and the
structured-task dispatch path refusing before the wire with the recovery action in the error.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import credential_store
from core.agent_runtime.memory_runtime import (
    _provider_auth_failure_hint,
    chat_surface_honest_degraded_response,
)


def _decision(details: dict) -> SimpleNamespace:
    return SimpleNamespace(source="selected_model_blocked", details=details)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(_live_info_mode=lambda *a, **kw: "")


def _remote_lane(**config) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="openrouter-byok:other-model", provider_name="openrouter-byok",
        model_name="other-model", metadata={}, runtime_config={
            "base_url": "https://openrouter.ai/api/v1",
            "api_path": "/chat/completions", "health_path": "/models",
            "credential_key": "llm.cloud.openrouter", **config,
        },
    ))


def test_throttle_429_on_attempt_timings_names_the_limit_not_auto_fallback():
    decision = _decision({
        "attempted": ["openrouter-byok:other-model"],
        "attempt_timings": [{"error": "429 Client Error: Too Many Requests for url: https://openrouter.ai/api/v1/chat/completions"}],
    })
    text = chat_surface_honest_degraded_response(_agent(), decision, user_input="Summarize the release notes.")
    assert "429" in text and "throttled" in text
    # WORDING LAW (2026-09-18 follow-up): "this is temporary — wait a moment" is NOT established
    # for every quota refusal, so without typed Retry-After evidence the message names the
    # rate/quota limit and explicitly does not promise a short wait resolves it.
    assert "wait a moment" not in text and "This is temporary" not in text
    assert "rate or quota limit" in text
    assert "only model" not in text and "Auto" not in text


def test_throttle_429_quotes_the_providers_own_retry_after_when_carried():
    decision = _decision({
        "attempted": ["openrouter-byok:other-model"],
        "attempted_error_reasons": [
            "429 Client Error: Too Many Requests — provider said: rate limited [retry_after=27s]",
        ],
        "attempt_kinds": ["UNAVAILABLE"],
    })
    text = _provider_auth_failure_hint(decision)
    assert text and "429" in text
    assert "27" in text and "Retry-After" in text


def test_a_local_credential_refusal_after_a_wire_attempt_is_not_nothing_was_sent():
    """An early timeout followed by a missing key is TWO facts; "nothing was sent" is false."""
    decision = _decision({
        "attempted": ["openrouter-byok:slow-model", "openrouter-byok:other-model"],
        "attempted_error_reasons": [
            "HTTPSConnectionPool(host='openrouter.ai', port=443): Read timed out.",
            "provider_credential_unavailable: saved key is unavailable",
        ],
        "attempt_kinds": ["TIMEOUT", "REFUSED"],
    })
    text = _provider_auth_failure_hint(decision)
    assert text
    assert "Nothing was sent and nothing was charged" not in text
    assert "reached" in text and "TIMEOUT" in text
    assert "key was not available" in text


def test_a_pure_local_credential_turn_keeps_the_nothing_sent_wording():
    decision = _decision({
        "attempted": ["openrouter-byok:other-model"],
        "attempted_error_reasons": ["provider_credential_unavailable: saved key is unavailable"],
        "attempt_kinds": ["REFUSED"],
    })
    text = _provider_auth_failure_hint(decision)
    assert text
    assert "Nothing was sent and nothing was charged" in text
    assert "API Keys" in text


def test_per_attempt_kinds_fall_back_to_derivation_when_the_decision_lacks_them():
    decision = _decision({
        "attempted": ["openrouter-byok:slow-model", "openrouter-byok:other-model"],
        "attempted_error_reasons": [
            "Read timed out. (read timeout=59.99)",
            "provider_credential_unavailable: saved key is unavailable",
        ],
    })
    text = _provider_auth_failure_hint(decision)
    assert text
    assert "Nothing was sent and nothing was charged" not in text
    assert "TIMEOUT" in text


def test_the_adapter_carries_a_numeric_retry_after_into_the_429_error_string():
    """The transport for typed Retry-After evidence is the attempt's own error string."""
    import requests

    from adapters.openai_compatible_adapter import _raise_for_status_with_cause

    def _response(status: int, headers: dict, body: str = "rate limited"):
        raw = requests.models.Response()
        raw.status_code = status
        raw.headers.update(headers)
        raw._content = body.encode("utf-8")
        raw.url = "https://openrouter.ai/api/v1/chat/completions"
        return raw

    with pytest.raises(requests.HTTPError) as caught:
        _raise_for_status_with_cause(_response(429, {"Retry-After": "27"}))
    assert "[retry_after=27s]" in str(caught.value)

    with pytest.raises(requests.HTTPError) as caught_no_date:
        _raise_for_status_with_cause(_response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))
    assert "retry_after=" not in str(caught_no_date.value)

    with pytest.raises(requests.HTTPError) as caught_5xx:
        _raise_for_status_with_cause(_response(503, {"Retry-After": "9"}))
    assert "retry_after=" not in str(caught_5xx.value)


def test_cancelled_call_is_a_cancellation_not_a_model_failure():
    decision = _decision({
        "attempted": ["openrouter-byok:other-model"],
        "block_reason": "model_call_cancelled",
    })
    text = chat_surface_honest_degraded_response(_agent(), decision, user_input="Explain an immutable value.")
    assert "cancelled" in text
    assert "no usable reply" not in text and "only model" not in text and "Auto" not in text


def test_forbidden_403_is_access_denied_not_an_authentication_rejection():
    decision = _decision({
        "attempted": ["openrouter-byok:other-model"],
        "block_reason": "403 Client Error: Forbidden for url: https://openrouter.ai/api/v1/chat/completions",
    })
    text = chat_surface_honest_degraded_response(_agent(), decision, user_input="Explain an immutable value.")
    assert "access denied" in text
    assert "not necessarily a wrong key" in text
    assert "rejected the stored API key" not in text
    assert "authentication" not in text


def test_structured_dispatch_refuses_before_wire_with_recovery_action(monkeypatch):
    monkeypatch.setattr(credential_store, "get_credential", lambda name: None)
    wire = Mock(side_effect=AssertionError("unauthenticated network request"))
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.get", wire)
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", wire)
    lane = _remote_lane()
    with pytest.raises(RuntimeError, match=r"provider_credential_unavailable.*API Keys") as caught:
        lane.run_structured_task(ModelRequest(task_kind="chat", prompt="Return one keyword."))
    assert "not present" in str(caught.value)
    health = lane.health_check()
    assert health["ok"] is False
    assert health.get("reason") == "provider_credential_unavailable"
    wire.assert_not_called()


def test_unreadable_vault_read_is_refused_not_sent_anonymously(monkeypatch):
    def read(_name):
        raise credential_store.CredentialReadError("keychain locked")
    monkeypatch.setattr(credential_store, "get_credential", read)
    wire = Mock(side_effect=AssertionError("unauthenticated network request"))
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", wire)
    lane = _remote_lane()
    with pytest.raises(RuntimeError, match=r"provider_credential_unavailable.*unreadable.*keychain locked"):
        lane.run_text_task(ModelRequest(task_kind="chat", prompt="Explain an immutable value."))
    wire.assert_not_called()


def test_keyless_local_health_check_still_reaches_its_server(monkeypatch):
    """Control: the gate must not disarm a legitimate keyless loopback lane."""
    lane = OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="ollama-local:qwen2.5:7b", provider_name="ollama-local",
        model_name="qwen2.5:7b", metadata={}, runtime_config={
            "base_url": "http://127.0.0.1:11434/v1",
            "api_path": "/chat/completions", "health_path": "/models",
            "credential_key": "",
        },
    ))
    probe = Mock(return_value=Mock(status_code=200, raise_for_status=lambda: None))
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.get", probe)
    assert lane.health_check()["ok"] is True
    probe.assert_called_once()
