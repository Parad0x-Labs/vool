"""The signed manifest must record the output budget the provider was actually sent.

`seal_provider_invocation` recorded `request.max_output_tokens` — the number the caller asked for
BEFORE the adapters adjust it. Both adapters adjust it and neither writes back:

  * `_cloud_tool_output_budget` raises a cloud tool turn to a 3000-token floor, and adds a
    2048-token reasoning reserve for a thinking-capable model;
  * `_thinking_aware_output_budget` adds the same class of reserve on the native Ollama path.

So a turn whose request said 240 could be transmitted with `max_tokens: 2288`, and the manifest —
the signed record of what was sent — said 240. The seal was not describing the invocation it
sealed.

`payload` is the exact body about to be transmitted and is already in scope at the seal, so the
fix is to read it. This matters more once the budget becomes per-lane: the manifest is the only
durable record of what each provider was actually given.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.provider_invocation_gateway import (
    load_provider_manifest,
    seal_provider_invocation,
)
from core.runtime_paths import configure_runtime_home


@pytest.fixture
def provider_home(tmp_path):
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        configure_runtime_home(None)


def _request(max_output_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={},
        context={"chat_id": "chat-seal", "items_included": [], "items_excluded": []},
        max_output_tokens=max_output_tokens,
        task_id="task-seal",
        request_id="request-seal",
    )


def _seal(request, payload) -> dict:
    permit = seal_provider_invocation(
        request=request,
        provider_id="openrouter-byok:test/model",
        model_id="test/model",
        operation="chat",
        payload=payload,
    )
    return load_provider_manifest(permit.manifest.manifest_id)


def test_a_cloud_reserve_is_sealed_at_the_wire_value(provider_home) -> None:
    """The measured shape: request says 240, the adapter sends 2288."""

    manifest = _seal(_request(240), {"model": "test/model", "max_tokens": 2288, "messages": []})
    assert manifest["reserved_output_tokens"] == 2288


def test_an_ollama_reserve_is_sealed_at_the_wire_value(provider_home) -> None:
    """The native lane carries the number under `options.num_predict`, not `max_tokens`."""

    manifest = _seal(
        _request(240),
        {"model": "qwen3:8b", "options": {"num_predict": 2288, "num_ctx": 8192}, "messages": []},
    )
    assert manifest["reserved_output_tokens"] == 2288


def test_the_request_value_is_still_the_fallback(provider_home) -> None:
    """A payload that declares no budget must not seal zero."""

    manifest = _seal(_request(512), {"model": "test/model", "messages": []})
    assert manifest["reserved_output_tokens"] == 512


def test_a_tool_call_floor_is_sealed_not_the_request(provider_home) -> None:
    """`tool_intent` asks for 700; the cloud floor sends 3000."""

    manifest = _seal(_request(700), {"model": "test/model", "max_tokens": 3000, "messages": []})
    assert manifest["reserved_output_tokens"] == 3000


def test_a_payload_that_lowers_the_budget_is_also_honoured(provider_home) -> None:
    """The seal follows the wire in BOTH directions — it is not a max()."""

    manifest = _seal(_request(2000), {"model": "test/model", "max_tokens": 64, "messages": []})
    assert manifest["reserved_output_tokens"] == 64


def test_zero_everywhere_seals_zero(provider_home) -> None:
    manifest = _seal(_request(0), {"model": "test/model", "messages": []})
    assert manifest["reserved_output_tokens"] == 0
