from __future__ import annotations

from unittest import mock

from core.hardware_tier import MachineProbe, QwenTier
from core.local_model_bundles import model_active_parameter_billions, model_metadata
from core.model_selection_policy import ModelSelectionRequest, rank_providers
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_install_profiles import build_install_profile_truth, normalize_install_profile_id
from core.runtime_provider_defaults import (
    default_runtime_model_tag,
    ensure_default_runtime_providers,
    preferred_fast_local_model,
)
from storage.model_provider_manifest import ModelProviderManifest


def _mock_registry():
    manifests: dict[tuple[str, str], ModelProviderManifest] = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        values = list(manifests.values())[:limit]
        if enabled_only:
            return [item for item in values if item.enabled]
        return values

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.side_effect = lambda: [
        mock.Mock(provider_id=item.provider_id) for item in _list_manifests(enabled_only=True)
    ]
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    return registry, manifests


def _installed_lane_env() -> dict[str, str]:
    return {
        "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
        "VOOL_INSTALLED_OLLAMA_MODELS": "qwen2.5:0.5b,qwen3:8b,qwen3:14b,qwen2.5:32b",
        # Keep tests deterministic: a fresh install has not persisted a bucket yet, so model
        # registration is exercised against the conservative A row rather than this runner's RAM.
        "VOOL_CONTEXT_BUCKET": "A",
    }


def test_runtime_provider_defaults_register_installed_ollama_lanes(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_DEEP_REASONING", "0")  # thinking off by default
    registry, manifests = _mock_registry()

    changed = ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env=_installed_lane_env(),
        install_profile="local-only",
    )

    assert "ollama-local:qwen2.5:0.5b" in changed
    assert "ollama-local:qwen3:14b" in changed
    assert "ollama-local:qwen2.5:32b" in changed
    assert manifests[("ollama-local", "qwen2.5:0.5b")].metadata["bundle_role"] == "lightweight_utility"
    assert manifests[("ollama-local", "qwen3:8b")].metadata["bundle_role"] == "general"
    assert manifests[("ollama-local", "qwen3:14b")].metadata["bundle_role"] == "reasoning"
    assert manifests[("ollama-local", "qwen2.5:32b")].metadata["bundle_role"] == "heavy_reasoning"
    assert "api_path" not in manifests[("ollama-local", "qwen3:8b")].runtime_config
    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["think"] is False  # thinking off by default
    assert manifests[("ollama-local", "qwen2.5:0.5b")].runtime_config["context_window"] == 4096
    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["context_window"] == 4096
    assert manifests[("ollama-local", "qwen3:14b")].runtime_config["prewarm"]["options"]["num_ctx"] == 4096
    assert manifests[("ollama-local", "qwen2.5:32b")].runtime_config["prewarm"]["options"]["num_ctx"] == 4096


def test_runtime_provider_defaults_uses_persisted_bucket_and_records_sizing_metadata(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "install-profile.json").write_text('{"capacity_bucket":"B"}', encoding="utf-8")
    registry, manifests = _mock_registry()

    changed = ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env={},
        install_profile="local-only",
        runtime_home=str(tmp_path),
    )

    manifest = manifests[("ollama-local", "qwen3:8b")]
    assert changed == ("ollama-local:qwen3:8b",)
    assert manifest.runtime_config["context_window"] == 6144
    assert manifest.runtime_config["prewarm"]["options"]["num_ctx"] == 6144
    assert manifest.metadata["context_sizing_policy"] == "adaptive_persisted_bucket"
    assert manifest.metadata["capacity_bucket"] == "B"
    assert manifest.metadata["model_parameter_billions"] == 8.0
    assert manifest.metadata["selected_num_ctx"] == 6144


def test_runtime_provider_defaults_upgrades_persisted_heavy_reasoning_window() -> None:
    registry, manifests = _mock_registry()
    old_manifest = _manifest(
        "qwen2.5:32b",
        bundle_role="heavy_reasoning",
        capabilities=["summarize", "code_complex", "long_context"],
    )
    old_manifest.runtime_config.update(
        {
            "context_window": 1024,
            "think": False,
            "prewarm": {
                "strategy": "ollama_chat",
                "options": {"num_ctx": 1024, "num_predict": 1},
            },
        }
    )
    old_manifest.metadata["context_window"] = 1024
    registry.register_manifest(old_manifest)

    changed = ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env=_installed_lane_env(),
        install_profile="local-only",
    )

    upgraded = manifests[("ollama-local", "qwen2.5:32b")]
    assert "ollama-local:qwen2.5:32b" in changed
    assert upgraded.runtime_config["context_window"] == 4096
    assert upgraded.runtime_config["prewarm"]["options"]["num_ctx"] == 4096
    assert upgraded.metadata["context_window"] == 4096


def test_context_window_override_reaches_the_live_and_prewarm_call(tmp_path) -> None:
    # The override must land on BOTH the chat call (runtime_config) and the prewarm call
    # (prewarm.options), or Ollama reloads the model at a different num_ctx mid-session.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "install-profile.json").write_text('{"capacity_bucket":"B"}', encoding="utf-8")
    registry, manifests = _mock_registry()

    ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env={**_installed_lane_env(), "VOOL_OLLAMA_CONTEXT_WINDOW": "8192"},
        install_profile="local-only",
        runtime_home=str(tmp_path),
    )

    manifest = manifests[("ollama-local", "qwen3:8b")]
    assert manifest.runtime_config["context_window"] == 8192
    assert manifest.runtime_config["prewarm"]["options"]["num_ctx"] == 8192
    assert manifest.metadata["context_window"] == 8192


def test_context_window_override_is_clamped_on_the_conservative_pre_persistence_path() -> None:
    # The explicit conservative bucket models a fresh install independently of host RAM.
    registry, manifests = _mock_registry()

    ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env={**_installed_lane_env(), "VOOL_OLLAMA_CONTEXT_WINDOW": "99999"},
        install_profile="local-only",
    )

    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["context_window"] == 4096


def test_runtime_provider_defaults_window_is_unchanged_without_an_override() -> None:
    # The conservative A bucket keeps every registered lane at the flat 4096-token baseline.
    registry, manifests = _mock_registry()

    ensure_default_runtime_providers(
        registry,
        model_tag="qwen3:8b",
        env=_installed_lane_env(),
        install_profile="local-only",
    )

    assert manifests[("ollama-local", "qwen2.5:0.5b")].runtime_config["context_window"] == 4096
    assert manifests[("ollama-local", "qwen3:8b")].runtime_config["context_window"] == 4096
    assert manifests[("ollama-local", "qwen2.5:32b")].runtime_config["context_window"] == 4096


def test_runtime_provider_defaults_exposes_installed_text_models_not_embedding_lanes() -> None:
    registry, manifests = _mock_registry()

    ensure_default_runtime_providers(
        registry,
        model_tag="gemma3:4b",
        env={
            "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
            "VOOL_INSTALLED_OLLAMA_MODELS": "gemma3:4b,qwen2.5:7b,nomic-embed-text:latest",
        },
        install_profile="local-only",
    )

    assert ("ollama-local", "gemma3:4b") in manifests
    assert ("ollama-local", "qwen2.5:7b") in manifests
    assert ("ollama-local", "nomic-embed-text:latest") not in manifests
    ranked = rank_providers(
        [item for item in manifests.values() if item.enabled],
        ModelSelectionRequest(task_kind="action_plan", output_mode="action_plan"),
    )
    assert ranked[0].model_name == "qwen2.5:7b"


def test_default_runtime_model_prefers_installed_fast_nothink_lane() -> None:
    env = {
        "VOOL_INSTALLED_OLLAMA_MODELS": "qwen3:8b,vool-qwen3-30b-a3b:nothink,qwen3.5:35b-a3b",
        "VOOL_FAST_DEFAULT_MIN_RAM_GB": "0",
    }

    assert preferred_fast_local_model(env=env) == "vool-qwen3-30b-a3b:nothink"
    assert default_runtime_model_tag(env=env) == "vool-qwen3-30b-a3b:nothink"


def test_goblin_model_metadata_records_hybrid_moe_truth() -> None:
    qwen35 = model_metadata("qwen3.5:35b-a3b")
    gemma_qat = model_metadata("gemma3:12b-qat")

    assert qwen35["architecture"] == "hybrid_moe"
    assert qwen35["constant_vram"] is True
    assert qwen35["max_context"] == 262144
    assert model_active_parameter_billions("qwen3.5:35b-a3b") == 3.3
    assert gemma_qat["qat"] is True
    assert gemma_qat["license_name"] == "Gemma Terms"


def test_goblin_stack_profile_selects_required_local_lanes(tmp_path) -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )
    tier = QwenTier("heavy", "qwen2.5:32b", 32.0, 20.0, 48.0)
    capability_truth = (
        _capability_truth("qwen3:0.6b", role_fit="drone", tokens_per_second=220.0),
        _capability_truth("qwen3:8b", role_fit="drone", tokens_per_second=130.0),
        _capability_truth("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=75.0),
    )

    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(256.0)):
        profile = build_install_profile_truth(
            requested_profile="goblin_stack",
            probe=probe,
            tier=tier,
            provider_capability_truth=capability_truth,
            env={
                "VOOL_REGISTER_INSTALLED_OLLAMA_MODELS": "1",
                "VOOL_INSTALLED_OLLAMA_MODELS": "qwen3:0.6b,qwen3:8b,qwen3.5:35b-a3b",
            },
            runtime_home=tmp_path,
        )

    assert normalize_install_profile_id("goblin_stack") == "goblin-stack"
    assert profile.profile_id == "goblin-stack"
    assert profile.ready is True
    assert profile.selected_models == ("qwen3:0.6b", "qwen3:8b", "qwen3.5:35b-a3b")
    assert profile.optional_models == ("qwen3:30b-a3b", "qwen3:14b")
    assert ("heavy_reasoning", "qwen3.5:35b-a3b") in profile.selected_model_roles


def test_provider_snapshot_keeps_installed_ollama_lanes_visible_under_local_profile(tmp_path) -> None:
    registry, _ = _mock_registry()

    snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        env=_installed_lane_env(),
    )

    provider_ids = {item.provider_id for item in snapshot.capability_truth}
    assert {
        "ollama-local:qwen2.5:0.5b",
        "ollama-local:qwen3:8b",
        "ollama-local:qwen3:14b",
        "ollama-local:qwen2.5:32b",
    } <= provider_ids


def test_provider_snapshot_explicit_installed_ollama_override_filters_stale_lanes(tmp_path) -> None:
    registry, manifests = _mock_registry()
    manifests[("ollama-local", "gemma3:4b")] = _manifest(
        "gemma3:4b",
        bundle_role="lightweight_utility",
        capabilities=["format", "structured_json", "tool_intent"],
    )

    snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        env=_installed_lane_env(),
    )

    provider_ids = {item.provider_id for item in snapshot.capability_truth}
    assert "ollama-local:gemma3:4b" not in provider_ids
    assert "ollama-local:qwen3:8b" in provider_ids
    assert "ollama-local:qwen3:14b" in provider_ids


def _manifest(model_name: str, *, bundle_role: str, capabilities: list[str]) -> ModelProviderManifest:
    orchestration_role = "queen" if bundle_role in {"reasoning", "heavy_reasoning"} else "drone"
    return ModelProviderManifest(
        provider_name="ollama-local",
        model_name=model_name,
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen",
        license_url_or_reference="https://ollama.com/library/qwen",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=capabilities,
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": orchestration_role,
            "bundle_role": bundle_role,
        },
        enabled=True,
    )


def _capability_truth(model_id: str, *, role_fit: str, tokens_per_second: float) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=8192,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state="ready",
    )


def _fake_disk_usage_with_free_gb(free_gb: float) -> mock.Mock:
    fake_usage = mock.Mock()
    fake_usage.free = int(free_gb * 1024**3)
    return fake_usage


def test_model_ranking_uses_utility_general_and_reasoning_lanes() -> None:
    manifests = [
        _manifest(
            "qwen2.5:0.5b",
            bundle_role="lightweight_utility",
            capabilities=["format", "structured_json", "tool_intent"],
        ),
        _manifest(
            "qwen3:8b",
            bundle_role="general",
            capabilities=["format", "structured_json", "tool_intent", "summarize"],
        ),
        _manifest(
            "qwen3:14b",
            bundle_role="reasoning",
            capabilities=["format", "structured_json", "summarize", "code_complex", "long_context"],
        ),
    ]

    utility_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="format", output_mode="plain_text"),
    )
    tool_selection_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="tool_intent", output_mode="tool_intent"),
    )
    chat_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="normalization_assist", output_mode="plain_text"),
    )
    reasoning_ranked = rank_providers(
        manifests,
        ModelSelectionRequest(task_kind="action_plan", output_mode="action_plan"),
    )

    assert utility_ranked[0].model_name == "qwen2.5:0.5b"
    # Tool SELECTION is not utility work and no longer lands on the 0.5B lane: measured, a sub-1B
    # model answers a tool_intent prompt with invented prose instead of dispatching.
    assert tool_selection_ranked[0].model_name == "qwen3:8b"
    assert chat_ranked[0].model_name == "qwen3:8b"
    assert reasoning_ranked[0].model_name == "qwen3:14b"


def test_vision_and_embed_models_are_not_text_chat_lanes() -> None:
    # The router must never pick a vision or embedding model for a plain text turn — moondream
    # returned "A coffee please?" echoes for casual chat before this filter.
    from core.local_ollama_inventory import is_text_generation_ollama_model as is_text

    for text_model in ("qwen3:8b", "qwen3:0.6b", "qwen2.5:7b"):
        assert is_text(text_model) is True, text_model
    for non_text in ("moondream:latest", "llava:7b", "bakllava", "llama3.2-vision", "qwen2.5-vl:7b", "nomic-embed-text:latest"):
        assert is_text(non_text) is False, non_text
