"""Guard: a rejected API key must be reported as a rejected API key.

A live drive hit `401 Unauthorized` from OpenRouter and the user was told only "I couldn't get a live
model response in this run". The actionable fact -- the stored credential is being rejected -- was in
the runtime ledger but never surfaced, which reads as "the free models are useless" rather than "your
key needs rotating".
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_runtime.memory_runtime import _provider_auth_failure_hint

REAL_401 = "401 Client Error: Unauthorized for url: https://openrouter.ai/api/v1/chat/completions"


def test_the_real_401_is_reported_as_an_auth_failure():
    decision = SimpleNamespace(
        details={"fallback_reason": REAL_401},
        provider_id="openrouter-byok:nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    message = _provider_auth_failure_hint(decision)
    assert message, "a 401 still degrades to the generic non-answer"
    assert "openrouter" in message.lower()
    assert "key" in message.lower()
    # It must still be clear that nothing was answered from cache/memory.
    assert "cache" in message.lower() or "memory" in message.lower()


@pytest.mark.parametrize(
    "blob",
    ["403 Client Error: Forbidden", "Invalid API key provided", "unauthorized", "invalid_api_key"],
)
def test_other_auth_shapes_are_recognised(blob):
    decision = SimpleNamespace(details={"error": blob}, provider_id="openrouter-byok:x")
    assert _provider_auth_failure_hint(decision)


@pytest.mark.parametrize(
    "details",
    [
        {"fallback_reason": "timeout after 60s"},
        {"rejection_reason": "all_ranked_providers_failed"},
        {"fallback_reason": "provider_fallback_budget_exceeded"},
        {},
        None,
    ],
)
def test_non_auth_failures_do_not_claim_a_key_problem(details):
    """Telling a user to rotate a working key over a timeout would be its own false claim."""
    decision = SimpleNamespace(details=details, provider_id="ollama-local:qwen3:8b")
    assert _provider_auth_failure_hint(decision) == ""
