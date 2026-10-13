"""Models that run paid tools of their own on every call are never pinned or sent to.

A direct-endpoint model such as a compound system or a search model runs web search, website visits
or code execution at the provider and reports only tokens back. Settlement prices tokens, so those
tool fees would land on the owner's key with no spending limit counting them. Pinning one is
refused, and a pin saved before the check existed is refused again before anything is sent.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.model_pricing import unpriced_builtin_tools


@pytest.mark.parametrize(
    "provider, model, refused",
    [
        ("groq", "groq/compound", True),
        ("groq", "groq/compound-mini", True),
        ("groq", "compound-beta", True),
        ("groq-byok:groq/compound", "groq/compound", True),
        ("groq", "llama-3.3-70b-versatile", False),
        ("groq", "moonshotai/kimi-k2-instruct", False),
        ("openai", "gpt-4o-search-preview", True),
        ("openai", "gpt-5-search-api", True),
        ("openai", "o4-mini-deep-research", True),
        ("openai", "gpt-4.1-mini", False),
        # Through OpenRouter the reported total already includes the tool fees.
        ("openrouter", "openai/gpt-4o-search-preview", False),
        ("anthropic", "compound", False),
    ],
)
def test_models_with_unpriced_tools_of_their_own_are_known(provider, model, refused) -> None:
    assert unpriced_builtin_tools(provider, model) is refused


def _manifest(provider_name: str, model_name: str, base_url: str):
    return SimpleNamespace(
        provider_name=provider_name,
        provider_id=f"{provider_name}:{model_name}",
        model_name=model_name,
        adapter_type="openai_compatible",
        source_type="http",
        metadata={"cost_class": "paid_cloud", "runtime_family": "openai-compatible"},
        runtime_config={"base_url": base_url, "api_path": "/chat/completions", "timeout_seconds": 5.0},
    )


def _request() -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="q", messages=[{"role": "user", "content": "q"}], metadata={})


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest("groq-byok", "groq/compound", "https://api.groq.com/openai/v1"),
        _manifest("openai-byok", "gpt-4o-search-preview", "https://api.openai.com/v1"),
    ],
    ids=["groq-compound", "openai-search"],
)
def test_the_adapter_sends_nothing_to_such_a_model(manifest) -> None:
    adapter = OpenAICompatibleAdapter(manifest)
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post:
        with pytest.raises(RuntimeError, match="unpriced_builtin_tools"):
            adapter.run_text_task(_request())
        with pytest.raises(RuntimeError, match="unpriced_builtin_tools"):
            list(adapter.stream_text_task(_request()))
    post.assert_not_called()


def test_an_ordinary_model_on_the_same_provider_is_still_sent() -> None:
    adapter = OpenAICompatibleAdapter(_manifest("groq-byok", "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1"))
    payload = adapter._build_openai_payload(_request(), force_json=False, stream=False)
    assert payload["model"] == "llama-3.3-70b-versatile"


@pytest.mark.parametrize("model", ["groq/compound", "compound-beta-mini"])
def test_such_a_model_cannot_be_pinned_and_the_saved_choice_is_untouched(model) -> None:
    from core import cloud_escalation_policy as cep
    from core.cloud_model_control import set_cloud_model

    before = cep.load_policy()
    ok, message, chosen = set_cloud_model(model, provider="groq", owner_local=True, confirm_paid=True)
    assert ok is False and chosen == ""
    assert "spending limits" in message and "unchanged" in message
    after = cep.load_policy()
    assert (after.model, after.provider) == (before.model, before.provider)
