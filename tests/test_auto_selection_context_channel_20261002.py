"""The call-size channel of the live ranking API, ``rank_provider_candidates(min_context_tokens=)``.

The ordinary Auto chat ranks before it builds the provider request, so today it passes no size
and this channel stays at 0 (no filtering). These cases pin what the channel does once the
caller supplies the serialized size of the call: a declared window that cannot hold it is not
eligible, an unknown window stays eligible but never outranks a window known to hold it.
"""
from __future__ import annotations

from core.model_health import reset_provider_health
from core.model_registry import ModelRegistry
from core.provider_routing import rank_provider_candidates
from storage.db import get_connection
from storage.model_provider_manifest import ModelProviderManifest

CAPS = ["summarize", "classify", "format", "extract", "structured_json", "code_basic", "tool_intent"]


def _local(provider: str, model: str, window: int | None) -> ModelProviderManifest:
    metadata = {"context_window": window} if window else {}
    return ModelProviderManifest(
        provider_name=provider,
        model_name=model,
        source_type="subprocess",
        license_name="MIT",
        license_reference="https://example.com/license",
        runtime_dependency="ollama",
        capabilities=CAPS,
        metadata=metadata,
    )


def _registry() -> ModelRegistry:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()
    reset_provider_health()
    registry = ModelRegistry()
    # Equal scores tie-break on provider name, descending: small, then unknown, then wide.
    registry.register_manifest(_local("ccc-local", "smallwin:8b", 4096))
    registry.register_manifest(_local("bbb-local", "unknownwin:8b", None))
    registry.register_manifest(_local("aaa-local", "widewin:8b", 32768))
    return registry


def _rank(registry: ModelRegistry, **kwargs) -> list[str]:
    ranked = rank_provider_candidates(
        registry, task_kind="normalization_assist", output_mode="plain_text", swarm_size=4, **kwargs
    )
    return [manifest.model_name for manifest in ranked]


def test_no_supplied_size_leaves_the_ranking_unchanged() -> None:
    registry = _registry()
    assert _rank(registry) == ["smallwin:8b", "unknownwin:8b", "widewin:8b"]


def test_a_supplied_size_drops_known_small_windows_and_ranks_unknown_last() -> None:
    registry = _registry()
    assert _rank(registry, min_context_tokens=12000) == ["widewin:8b", "unknownwin:8b"]


def test_the_same_law_holds_after_role_rescoring() -> None:
    registry = _registry()
    ranked = [
        manifest.model_name
        for manifest in rank_provider_candidates(
            registry,
            task_kind="tool_intent",
            output_mode="tool_intent",
            role="drone",
            swarm_size=4,
            min_context_tokens=12000,
        )
    ]
    assert ranked == ["widewin:8b", "unknownwin:8b"]


def test_a_call_no_known_window_can_hold_keeps_only_unknown_windows() -> None:
    registry = _registry()
    assert _rank(registry, min_context_tokens=40000) == ["unknownwin:8b"]
