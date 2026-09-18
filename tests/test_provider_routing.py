from __future__ import annotations

from core.model_health import record_provider_failure, reset_provider_health
from core.model_registry import ModelRegistry
from core.provider_routing import (
    estimated_local_model_resident_gb,
    provider_capability_truth_for_manifest,
    rank_provider_candidates,
    resolve_provider_routing_plan,
)
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


def test_resident_estimate_uses_measured_weights_and_never_unknown_moe_active_parameters() -> None:
    measured_gib = 18_556_698_002 / (1024.0 ** 3)

    assert estimated_local_model_resident_gb(
        "vool-qwen3-30b-a3b:nothink",
        artifact_size_gb=measured_gib,
    ) == round(measured_gib + 1.5, 2)
    assert estimated_local_model_resident_gb("unknown-qwen-30b-a3b:nothink") == 24.0


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _register_default_manifests(registry: ModelRegistry):
    local_manifest = registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:14b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
            "enabled": True,
        }
    )
    kimi_manifest = registry.register_manifest(
        {
            "provider_name": "kimi-remote",
            "model_name": "kimi-k2",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format", "long_context", "code_complex", "structured_json"],
            "runtime_config": {"base_url": "https://kimi.example", "api_key_env": "KIMI_API_KEY"},
            "metadata": {"deployment_class": "cloud", "orchestration_role": "queen"},
            "enabled": True,
        }
    )
    remote_manifest = registry.register_manifest(
        {
            "provider_name": "remote-generic",
            "model_name": "helper",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format"],
            "runtime_config": {"base_url": "https://remote.example"},
            "metadata": {"deployment_class": "cloud"},
            "enabled": True,
        }
    )
    return local_manifest, kimi_manifest, remote_manifest


def test_drone_role_prefers_local_qwen_lane() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)

    ranked = rank_provider_candidates(
        registry,
        task_kind="summarization",
        output_mode="summary_block",
        role="drone",
        swarm_size=2,
    )

    assert ranked
    assert ranked[0].provider_name == "local-qwen-http"
    assert {manifest.provider_name for manifest in ranked[:2]} == {"local-qwen-http", "remote-generic"}


def test_queen_role_prefers_kimi_when_present() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
        swarm_size=2,
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "kimi-remote"
    assert plan.candidate_provider_ids[0] == "kimi-remote:kimi-k2"


def test_queen_role_falls_back_to_best_local_when_remote_absent() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:32b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "long_context", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local"},
            "enabled": True,
        }
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "local-qwen-http"
    assert plan.candidate_provider_ids == ("local-qwen-http:qwen2.5:32b",)


def test_queen_role_prefers_local_vllm_when_no_remote_queen_exists() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "local-qwen-http",
            "model_name": "qwen2.5:14b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
            "weight_location": "user-supplied",
            "weights_bundled": False,
            "redistribution_allowed": True,
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone"},
            "enabled": True,
        }
    )
    registry.register_manifest(
        {
            "provider_name": "vllm-local",
            "model_name": "qwen2.5:32b-vllm",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "User-managed",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "vllm",
            "capabilities": ["summarize", "classify", "format", "long_context", "code_complex", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:8100/v1", "context_window": 65536},
            "metadata": {"deployment_class": "local", "orchestration_role": "queen", "context_window": 65536},
            "enabled": True,
        }
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
    )

    assert plan.selected is not None
    assert plan.selected.provider_name == "vllm-local"
    assert plan.candidate_provider_ids[0] == "vllm-local:qwen2.5:32b-vllm"


def test_provider_capability_truth_treats_exact_loopback_http_as_local() -> None:
    capability = provider_capability_truth_for_manifest(
        ModelProviderManifest(
            provider_name="vllm-local",
            model_name="qwen2.5:14b",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="User-managed",
            license_reference="user-managed",
            weight_location="external",
            runtime_dependency="vllm",
            runtime_config={"base_url": "http://127.0.0.1:8000/v1"},
            metadata={"orchestration_role": "queen"},
            enabled=True,
        )
    )

    assert capability.locality == "local"


def test_provider_capability_truth_treats_loopback_named_remote_host_as_remote() -> None:
    capability = provider_capability_truth_for_manifest(
        ModelProviderManifest(
            provider_name="kimi-remote",
            model_name="kimi-mock",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Provider",
            license_reference="user-managed",
            weight_location="external",
            runtime_dependency="remote-openai-compatible-provider",
            runtime_config={"base_url": "http://127.0.0.1.nip.io:8011/v1"},
            metadata={"deployment_class": "cloud", "orchestration_role": "queen"},
            enabled=True,
        )
    )

    assert capability.locality == "remote"


def test_drone_role_can_use_local_llamacpp_lane_when_qwen_is_absent() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    registry.register_manifest(
        {
            "provider_name": "llamacpp-local",
            "model_name": "qwen2.5:14b-gguf",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "User-managed",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "llama.cpp",
            "capabilities": ["summarize", "classify", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:8090/v1", "context_window": 16384},
            "metadata": {"deployment_class": "local", "orchestration_role": "drone", "context_window": 16384},
            "enabled": True,
        }
    )
    registry.register_manifest(
        {
            "provider_name": "remote-generic",
            "model_name": "helper",
            "source_type": "http",
            "adapter_type": "openai_compatible",
            "license_name": "Provider",
            "license_reference": "user-managed",
            "weight_location": "external",
            "weights_bundled": False,
            "redistribution_allowed": False,
            "runtime_dependency": "remote-openai-compatible-provider",
            "capabilities": ["summarize", "classify", "format"],
            "runtime_config": {"base_url": "https://remote.example"},
            "metadata": {"deployment_class": "cloud"},
            "enabled": True,
        }
    )

    ranked = rank_provider_candidates(
        registry,
        task_kind="summarization",
        output_mode="summary_block",
        role="drone",
        swarm_size=2,
    )

    assert ranked
    assert ranked[0].provider_name == "llamacpp-local"
    assert ranked[0].provider_id == "llamacpp-local:qwen2.5:14b-gguf"


def test_provider_routing_skips_circuit_open_candidates() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    _register_default_manifests(registry)
    record_provider_failure(
        "kimi-remote:kimi-k2",
        error="timeout",
        timeout=True,
        failure_threshold=1,
        cooldown_seconds=60,
    )

    plan = resolve_provider_routing_plan(
        registry,
        task_kind="action_plan",
        output_mode="action_plan",
        role="queen",
        swarm_size=2,
    )

    assert plan.selected is not None
    assert plan.selected.provider_name != "kimi-remote"
    assert any(item["reason"] == "provider_circuit_open" for item in plan.rejected_candidates)
    assert any("circuit-open" in note for note in plan.selection_notes)


def test_provider_capability_truth_marks_recent_failures_as_degraded() -> None:
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    registry = ModelRegistry()
    local_manifest, _, _ = _register_default_manifests(registry)
    record_provider_failure(
        local_manifest.provider_id,
        error="health_check_failed",
        failure_threshold=5,
        cooldown_seconds=60,
    )

    capability = provider_capability_truth_for_manifest(local_manifest)

    assert capability.availability_state == "degraded"
    assert capability.circuit_open is False
    assert capability.last_error == "health_check_failed"


# --------------------------------------------------------------------------------------
# Finding G, 2026-08-04: `resolve_tool_intent` always calls `_execute_provider_task` with
# `provider_role="drone"`. The free OpenRouter manifest is tagged `orchestration_role="queen"`
# for unrelated (chat-lane escalation) reasons, not because it is unfit for tool selection -- so
# under the OLD `role=="drone"` branch of `_role_bonus` it ate a structural queen-tag (-1.25) +
# remote (-0.65) penalty with no `is_verified_free_cloud_manifest` term anywhere to offset it, a
# ~4.3-4.9pt gap vs. a local drone-tagged model that no ordinal-position base_score in
# `_rank_provider_candidates_internal` could realistically close.
# --------------------------------------------------------------------------------------


def _openrouter_free_manifest(model_name: str = "nvidia/nemotron-3-ultra-550b-a55b:free") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        weight_location="external",
        redistribution_allowed=False,
        runtime_dependency="remote-openai-compatible-provider",
        capabilities=["structured_json", "tool_intent"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"orchestration_role": "queen", "deployment_class": "cloud", "cost_class": "paid_cloud"},
        enabled=True,
    )


def _local_drone_manifest(model_name: str = "qwen3:8b") -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["structured_json", "tool_intent"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"orchestration_role": "drone", "deployment_class": "local"},
        enabled=True,
    )


def test_role_bonus_drone_prefers_a_verified_free_cloud_manifest_over_local(monkeypatch) -> None:
    """Re-confirmed live 2026-08-04 (final-skeptic pass): removing the structural lock-out (this
    test's earlier form) only stopped the free lane from scoring below zero -- local still won
    100% of unpinned drives (measured: local +3.07 vs free-cloud -0.03). The operator's mandate is
    an explicit PRIORITY ORDER, not a tie-breaker: "the best of Cloud and local llm (priority is
    cloud[cloud] free openrouter ai's)" -- free-cloud first, local second. A manifest that is both
    verified-free and declares `tool_intent` must now outscore a comparable local drone candidate.
    """
    from core import openrouter_catalog
    from core.model_selection_policy import is_verified_free_cloud_manifest
    from core.provider_routing import _role_bonus

    verified_free = openrouter_catalog.OpenRouterModel(
        model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        name="Nemotron 3 Ultra (free)",
        context_length=131072,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-08-04T00:00:00+00:00",
    )
    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **_kwargs: ((verified_free,), 0.0))

    local = _local_drone_manifest()
    free_cloud = _openrouter_free_manifest()
    assert is_verified_free_cloud_manifest(free_cloud) is True
    assert "tool_intent" in free_cloud.capabilities

    local_score = _role_bonus(local, "drone")
    free_score = _role_bonus(free_cloud, "drone")

    assert free_score >= local_score, (
        f"a verified-free, tool_intent-capable cloud manifest must now outrank a comparable local "
        f"drone candidate on role_bonus: local={local_score} free_cloud={free_score}"
    )


def test_role_bonus_drone_does_not_boost_a_verified_free_manifest_without_tool_intent(monkeypatch) -> None:
    """The new bonus is keyed on the manifest actually declaring `tool_intent` -- a verified-free
    manifest that cannot select a tool from a schema must not win purely for being free."""
    from core import openrouter_catalog
    from core.provider_routing import _role_bonus

    verified_free = openrouter_catalog.OpenRouterModel(
        model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        name="Nemotron 3 Ultra (free)",
        context_length=131072,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-08-04T00:00:00+00:00",
    )
    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **_kwargs: ((verified_free,), 0.0))

    local = _local_drone_manifest()
    free_cloud_no_tool_intent = _openrouter_free_manifest()
    free_cloud_no_tool_intent = free_cloud_no_tool_intent.model_copy(update={"capabilities": ["structured_json"]})

    local_score = _role_bonus(local, "drone")
    free_score = _role_bonus(free_cloud_no_tool_intent, "drone")

    assert local_score > free_score, (
        "without a declared tool_intent capability the free-cloud bonus must not apply: "
        f"local={local_score} free_cloud={free_score}"
    )


def test_role_bonus_drone_still_charges_the_full_queen_penalty_to_an_ordinary_paid_remote(monkeypatch) -> None:
    """The exemption is keyed on `is_verified_free_cloud_manifest`, not on being remote/queen in
    general -- an ordinary PAID queen-tagged remote manifest must keep the full penalty."""
    from core import openrouter_catalog
    from core.provider_routing import _role_bonus

    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **_kwargs: ((), 0.0))
    paid_remote = _openrouter_free_manifest(model_name="nvidia/nemotron-3-ultra-550b-a55b")  # no ":free" suffix
    # Same manifest shape as the verified-free case but NOT catalog-verified free -- must reproduce
    # the old, full penalty (queen -1.25, remote -0.65), not the reduced one.
    assert _role_bonus(paid_remote, "drone") < -1.5


def test_drone_role_tool_intent_ranking_now_prefers_free_cloud_over_local(monkeypatch) -> None:
    """The AUTO ranking default for tool_intent/drone now honors the operator's stated priority:
    free-cloud first, local second, whenever both are genuinely viable and tool_intent-capable."""
    run_migrations()
    reset_provider_health()
    _clear_manifests()
    from core import openrouter_catalog

    verified_free = openrouter_catalog.OpenRouterModel(
        model_id="nvidia/nemotron-3-ultra-550b-a55b:free",
        name="Nemotron 3 Ultra (free)",
        context_length=131072,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-08-04T00:00:00+00:00",
    )
    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **_kwargs: ((verified_free,), 0.0))

    registry = ModelRegistry()
    registry.register_manifest(_local_drone_manifest())
    registry.register_manifest(_openrouter_free_manifest())

    ranked = rank_provider_candidates(
        registry,
        task_kind="tool_intent",
        output_mode="tool_intent",
        role="drone",
        swarm_size=2,
    )

    provider_names = [m.provider_name for m in ranked]
    assert "ollama-local" in provider_names, (
        "local must remain a live candidate -- the fix reorders the default, it does not remove "
        "the local lane"
    )
    assert ranked[0].provider_name == "openrouter-byok", (
        "a verified-free, tool_intent-capable cloud manifest must now win the AUTO default over a "
        f"comparable local drone candidate, got order: {provider_names}"
    )
