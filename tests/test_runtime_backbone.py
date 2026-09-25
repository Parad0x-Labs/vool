from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_cli import cmd_providers
from core.hardware_tier import MachineProbe, QwenTier
from core.model_registry import ProviderAuditRow
from core.runtime_backbone import (
    ProviderRegistrySnapshot,
    _filter_snapshot_to_provider_ids,
    _visible_provider_ids_for_install_profile,
    build_provider_registry_snapshot,
    build_runtime_backbone,
)
from core.runtime_bootstrap import BootstrappedRuntime, RuntimeBackendSelection
from core.runtime_install_profiles import InstallProfileProvider, InstallProfileTruth
from storage.model_provider_manifest import ModelProviderManifest


def _install_profile_truth_with_provider_ids(*provider_ids: str) -> InstallProfileTruth:
    provider_mix = tuple(
        InstallProfileProvider(
            provider_id=provider_id,
            role="general" if index == 0 else "reasoning",
            locality="local",
            required=True,
            configured=True,
            availability_state="ready",
        )
        for index, provider_id in enumerate(provider_ids)
    )
    return InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="test",
        selected_model="qwen3:8b",
        provider_mix=provider_mix,
        estimated_download_gb=0.0,
        estimated_disk_footprint_gb=0.0,
        minimum_free_space_gb=0.0,
        ram_expectation_gb=0.0,
        vram_expectation_gb=0.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
        selected_models=tuple(item.provider_id.split(":", 1)[1] for item in provider_mix),
    )


@pytest.mark.parametrize("provider_env", [{}, {"OPENROUTER_API_KEY": "synthetic-test-provider-key"}])
def test_build_provider_registry_snapshot_collects_rows_and_warnings_from_registry(provider_env) -> None:
    row = ProviderAuditRow(
        provider_id="local-qwen-http:qwen2.5:14b",
        source_type="http",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        runtime_dependency="ollama",
        weight_location="user-supplied",
        weights_bundled=False,
        redistribution_allowed=True,
        warnings=["missing health path"],
    )
    registry = mock.Mock()
    registry.startup_warnings.return_value = ["missing health path"]
    registry.provider_audit_rows.return_value = [row]
    # ModelRegistry returns None for an absent manifest, not a truthy child Mock.
    registry.get_manifest.return_value = None
    registry.list_manifests.return_value = []

    snapshot = build_provider_registry_snapshot(registry, env=provider_env)

    assert snapshot.warnings == ("missing health path",)
    assert snapshot.audit_rows == (row,)
    assert snapshot.prewarm_results == tuple()


def test_build_provider_registry_snapshot_auto_registers_kimi_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "KIMI_API_KEY": "test-key",
            "KIMI_BASE_URL": "https://kimi.example/v1",
            "VOOL_KIMI_MODEL": "kimi-latest",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    kimi_manifest = manifests[("kimi-remote", "kimi-latest")]
    assert kimi_manifest.adapter_type == "openai_compatible"
    assert kimi_manifest.runtime_config["base_url"] == "https://kimi.example/v1"
    assert kimi_manifest.runtime_config["api_key_env"] == "KIMI_API_KEY"
    assert any(item.provider_id == "kimi-remote:kimi-latest" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_honors_local_only_install_profile_for_remote_gating() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "KIMI_API_KEY": "test-key",
            "VOOL_INSTALL_PROFILE": "local-only",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry, honor_install_profile=True)

    assert any(item.provider_id.startswith("ollama-local:") for item in snapshot.capability_truth)
    assert not any(item.provider_id.startswith("kimi-remote:") for item in snapshot.capability_truth)


def test_local_only_profile_falls_back_to_registered_local_truth_when_profile_ids_are_stale() -> None:
    capability_truth = (
        SimpleNamespace(
            provider_id="ollama-local:registered-model",
            locality="local",
        ),
        SimpleNamespace(
            provider_id="kimi-remote:configured-model",
            locality="remote",
        ),
    )

    with mock.patch(
        "core.runtime_backbone.build_install_profile_truth",
        return_value=_install_profile_truth_with_provider_ids("ollama-local:stale-profile-model"),
    ):
        visible = _visible_provider_ids_for_install_profile(
            capability_truth=capability_truth,
            requested_profile="local-only",
            runtime_home=None,
            env={},
        )

    assert visible == ("ollama-local:registered-model",)


def test_an_empty_local_only_provider_set_filters_every_remote_row() -> None:
    remote_truth = SimpleNamespace(provider_id="kimi-remote:configured-model", locality="remote")
    filtered = _filter_snapshot_to_provider_ids(
        manifests=(SimpleNamespace(provider_id=remote_truth.provider_id),),
        audit_rows=(SimpleNamespace(provider_id=remote_truth.provider_id),),
        capability_truth=(remote_truth,),
        provider_ids=tuple(),
    )

    assert filtered == (tuple(), tuple(), tuple())


def test_build_provider_registry_snapshot_filters_profile_to_active_runtime_model() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch("core.runtime_provider_defaults.default_runtime_model_tag", return_value="gemma3:4b"):
        snapshot = build_provider_registry_snapshot(
            registry,
            model_tag="qwen2.5:7b",
            requested_profile="local-only",
            honor_install_profile=True,
            env={"OLLAMA_MODELS": "G:\\Ollama\\models"},
        )

    assert ("ollama-local", "qwen2.5:7b") in manifests
    assert ("ollama-local", "gemma3:4b") not in manifests
    assert tuple(item.provider_id for item in snapshot.capability_truth) == ("ollama-local:qwen2.5:7b",)


def test_build_provider_registry_snapshot_filters_legacy_enabled_manifests_to_active_profile_mix() -> None:
    manifests: dict[tuple[str, str], ModelProviderManifest] = {
        ("cloud-fallback-http", "cloud"): ModelProviderManifest(
            provider_name="cloud-fallback-http",
            model_name="cloud",
            source_type="http",
            adapter_type="cloud_fallback_provider",
            license_name="Provider",
            license_reference="user-managed",
            license_url_or_reference="user-managed",
            runtime_dependency="remote-openai-compatible-provider",
            runtime_config={"base_url": "https://provider.example"},
            metadata={"deployment_class": "cloud", "orchestration_role": "queen"},
            enabled=True,
        ),
        ("local-qwen-http", "qwen-local"): ModelProviderManifest(
            provider_name="local-qwen-http",
            model_name="qwen-local",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="openai-compatible-local-runtime",
            runtime_config={"base_url": "http://127.0.0.1:1234"},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
        ("ollama-local", "qwen2.5:14b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen2.5:14b",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434"},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
        ("ollama-local", "qwen2.5:7b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen2.5:7b",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434"},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
        ("ollama-local", "qwen3:8b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen3:8b",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434"},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
        ("ollama-local", "deepseek-r1:8b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="deepseek-r1:8b",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434"},
            metadata={"deployment_class": "local", "orchestration_role": "queen"},
            enabled=True,
        ),
    }

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        rows = [item for item in manifests.values() if item.enabled or not enabled_only]
        return rows[:limit]

    def _provider_audit_rows():
        return [
            ProviderAuditRow(
                provider_id=item.provider_id,
                source_type=item.source_type,
                license_name=item.license_name,
                license_reference=item.license_reference,
                runtime_dependency=item.runtime_dependency,
                weight_location=item.weight_location,
                weights_bundled=item.weights_are_bundled,
                redistribution_allowed=item.redistribution_allowed,
                warnings=[],
            )
            for item in manifests.values()
        ]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.side_effect = _provider_audit_rows
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch(
        "core.runtime_backbone.build_install_profile_truth",
        return_value=_install_profile_truth_with_provider_ids(
            "ollama-local:qwen3:8b",
            "ollama-local:deepseek-r1:8b",
        ),
    ):
        snapshot = build_provider_registry_snapshot(
            registry,
            requested_profile="local-only",
            honor_install_profile=True,
            env={},
        )

    assert tuple(item.provider_id for item in snapshot.capability_truth) == (
        "ollama-local:qwen3:8b",
        "ollama-local:deepseek-r1:8b",
    )
    assert tuple(item.provider_id for item in snapshot.audit_rows) == (
        "ollama-local:qwen3:8b",
        "ollama-local:deepseek-r1:8b",
    )


def test_build_provider_registry_snapshot_keeps_local_max_on_primary_ollama_lane_until_llamacpp_is_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch("core.runtime_provider_defaults.default_runtime_model_tag", return_value="qwen2.5:14b"), mock.patch.dict(
        os.environ,
        {
            "VOOL_INSTALL_PROFILE": "local-max",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry, honor_install_profile=True)

    assert ("ollama-local", "qwen2.5:14b") in manifests
    assert ("ollama-local", "qwen2.5:7b") not in manifests
    assert not any(item.provider_id == "ollama-local:qwen2.5:7b" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_includes_local_ollama_prewarm_config() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    registry.prewarm_enabled_providers.return_value = [
        {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"}
    ]

    with mock.patch("core.runtime_provider_defaults.default_runtime_model_tag", return_value="qwen2.5:14b"):
        snapshot = build_provider_registry_snapshot(registry, run_prewarm=True)

    manifest = manifests[("ollama-local", "qwen2.5:14b")]
    assert manifest.runtime_config["prewarm"]["strategy"] == "ollama_chat"
    assert manifest.runtime_config["prewarm"]["timeout_seconds"] == 45
    assert snapshot.prewarm_results == (
        {"ok": True, "provider_id": "ollama-local:qwen2.5:14b", "status": "prewarmed", "keep_alive": "15m"},
    )


def test_build_provider_registry_snapshot_prewarms_only_active_profile_mix() -> None:
    manifests: dict[tuple[str, str], ModelProviderManifest] = {
        ("ollama-local", "qwen3:8b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen3:8b",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434", "prewarm": {"strategy": "ollama_chat"}},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
        ("ollama-local", "deepseek-r1:8b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="deepseek-r1:8b",
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434", "prewarm": {"strategy": "ollama_chat"}},
            metadata={"deployment_class": "local", "orchestration_role": "queen"},
            enabled=True,
        ),
        ("ollama-local", "qwen2.5:7b"): ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen2.5:7b",
            source_type="http",
            adapter_type="local_qwen_provider",
            license_name="Apache-2.0",
            license_reference="https://www.apache.org/licenses/LICENSE-2.0",
            license_url_or_reference="https://www.apache.org/licenses/LICENSE-2.0",
            runtime_dependency="ollama",
            runtime_config={"base_url": "http://127.0.0.1:11434", "prewarm": {"strategy": "ollama_chat"}},
            metadata={"deployment_class": "local", "orchestration_role": "drone"},
            enabled=True,
        ),
    }

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        rows = [item for item in manifests.values() if item.enabled or not enabled_only]
        return rows[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    registry.prewarm_enabled_providers.return_value = [
        {"ok": True, "provider_id": "ollama-local:qwen3:8b", "status": "prewarmed"},
        {"ok": True, "provider_id": "ollama-local:deepseek-r1:8b", "status": "prewarmed"},
    ]

    with mock.patch(
        "core.runtime_backbone.build_install_profile_truth",
        return_value=_install_profile_truth_with_provider_ids(
            "ollama-local:qwen3:8b",
            "ollama-local:deepseek-r1:8b",
        ),
    ):
        snapshot = build_provider_registry_snapshot(
            registry,
            requested_profile="local-only",
            honor_install_profile=True,
            run_prewarm=True,
            env={},
        )

    registry.prewarm_enabled_providers.assert_called_once_with(
        provider_ids=("ollama-local:qwen3:8b", "ollama-local:deepseek-r1:8b")
    )
    assert tuple(item["provider_id"] for item in snapshot.prewarm_results) == (
        "ollama-local:qwen3:8b",
        "ollama-local:deepseek-r1:8b",
    )


def test_build_provider_registry_snapshot_accepts_moonshot_aliases_for_kimi() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "MOONSHOT_API_KEY": "test-key",
            "MOONSHOT_BASE_URL": "https://kimi.example/v1",
            "MOONSHOT_MODEL": "kimi-moonshot",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    kimi_manifest = manifests[("kimi-remote", "kimi-moonshot")]
    assert kimi_manifest.runtime_config["base_url"] == "https://kimi.example/v1"
    assert kimi_manifest.runtime_config["api_key_env"] == "MOONSHOT_API_KEY"
    assert any(item.provider_id == "kimi-remote:kimi-moonshot" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_tether_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "TETHER_API_KEY": "test-key",
            "TETHER_BASE_URL": "https://tether.example/v1",
            "VOOL_TETHER_MODEL": "tether-edge",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    tether_manifest = manifests[("tether-remote", "tether-edge")]
    assert tether_manifest.adapter_type == "openai_compatible"
    assert tether_manifest.runtime_config["base_url"] == "https://tether.example/v1"
    assert tether_manifest.runtime_config["api_key_env"] == "TETHER_API_KEY"
    assert any(item.provider_id == "tether-remote:tether-edge" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_generic_remote_when_openai_is_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "gpt-4.1-mini",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    remote_manifest = manifests[("openai-compatible-remote", "gpt-4.1-mini")]
    assert remote_manifest.adapter_type == "cloud_fallback_provider"
    assert remote_manifest.runtime_config["base_url"] == "https://api.openai.com/v1"
    assert remote_manifest.runtime_config["api_key_env"] == "OPENAI_API_KEY"
    assert any(item.provider_id == "openai-compatible-remote:gpt-4.1-mini" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_vllm_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "VLLM_BASE_URL": "http://127.0.0.1:8100/v1",
            "VOOL_VLLM_MODEL": "qwen2.5:32b-vllm",
            "VLLM_CONTEXT_WINDOW": "65536",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    vllm_manifest = manifests[("vllm-local", "qwen2.5:32b-vllm")]
    assert vllm_manifest.adapter_type == "openai_compatible"
    assert vllm_manifest.runtime_config["base_url"] == "http://127.0.0.1:8100/v1"
    assert vllm_manifest.metadata["orchestration_role"] == "queen"
    assert any(item.provider_id == "vllm-local:qwen2.5:32b-vllm" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_auto_registers_llamacpp_when_configured() -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests

    with mock.patch.dict(
        os.environ,
        {
            "LLAMACPP_BASE_URL": "http://127.0.0.1:8090/v1",
            "VOOL_LLAMACPP_MODEL": "qwen2.5:14b-gguf",
            "LLAMACPP_CONTEXT_WINDOW": "16384",
        },
        clear=False,
    ):
        snapshot = build_provider_registry_snapshot(registry)

    manifest = manifests[("llamacpp-local", "qwen2.5:14b-gguf")]
    assert manifest.adapter_type == "openai_compatible"
    assert manifest.runtime_config["base_url"] == "http://127.0.0.1:8090/v1"
    assert manifest.metadata["orchestration_role"] == "drone"
    assert any(item.provider_id == "llamacpp-local:qwen2.5:14b-gguf" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_reads_persisted_provider_env_from_runtime_home(tmp_path) -> None:
    manifests = {}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    provider_env = tmp_path / "config" / "provider-env.sh"
    provider_env.parent.mkdir(parents=True)
    provider_env.write_text(
        "export LLAMACPP_BASE_URL=http://127.0.0.1:8090/v1\n"
        "export VOOL_LLAMACPP_MODEL=qwen2.5:14b-gguf\n"
        "export LLAMACPP_CONTEXT_WINDOW=16384\n",
        encoding="utf-8",
    )

    snapshot = build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        requested_profile="local-max",
        env={},
    )

    manifest = manifests[("llamacpp-local", "qwen2.5:14b-gguf")]
    assert manifest.runtime_config["base_url"] == "http://127.0.0.1:8090/v1"
    assert manifest.metadata["context_window"] == 16384
    assert any(item.provider_id == "llamacpp-local:qwen2.5:14b-gguf" for item in snapshot.capability_truth)


def test_build_provider_registry_snapshot_refreshes_stale_llamacpp_manifest(tmp_path) -> None:
    stale_manifest = ModelProviderManifest(
        provider_name="llamacpp-local",
        model_name="qwen2.5:14b-gguf",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        weight_location="external",
        runtime_dependency="llama.cpp",
        notes="stale test manifest",
        capabilities=["structured_json"],
        runtime_config={
            "base_url": "http://127.0.0.1:8090/v1",
            "context_window": 32768,
        },
        metadata={
            "runtime_family": "openai-compatible",
            "orchestration_role": "drone",
            "deployment_class": "local",
            "context_window": 32768,
            "max_safe_concurrency": 1,
        },
        enabled=True,
    )
    manifests = {("llamacpp-local", "qwen2.5:14b-gguf"): stale_manifest}

    def _get_manifest(provider_name: str, model_name: str):
        return manifests.get((provider_name, model_name))

    def _register_manifest(manifest):
        manifests[(manifest.provider_name, manifest.model_name)] = manifest
        return manifest

    def _list_manifests(*, enabled_only: bool = False, limit: int = 256):
        return list(manifests.values())[:limit]

    registry = mock.Mock()
    registry.startup_warnings.return_value = []
    registry.provider_audit_rows.return_value = []
    registry.get_manifest.side_effect = _get_manifest
    registry.register_manifest.side_effect = _register_manifest
    registry.list_manifests.side_effect = _list_manifests
    provider_env = tmp_path / "config" / "provider-env.sh"
    provider_env.parent.mkdir(parents=True)
    provider_env.write_text(
        "export LLAMACPP_BASE_URL=http://127.0.0.1:8090/v1\n"
        "export VOOL_LLAMACPP_MODEL=qwen2.5:14b-gguf\n"
        "export LLAMACPP_CONTEXT_WINDOW=4096\n",
        encoding="utf-8",
    )

    build_provider_registry_snapshot(
        registry,
        runtime_home=str(tmp_path),
        honor_install_profile=True,
        requested_profile="local-max",
        env={},
    )

    refreshed = manifests[("llamacpp-local", "qwen2.5:14b-gguf")]
    assert refreshed.runtime_config["context_window"] == 4096
    assert refreshed.metadata["context_window"] == 4096


def test_build_runtime_backbone_reuses_bootstrap_probe_and_provider_facades() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=48.0,
        gpu_name="NVIDIA",
        vram_gb=24.0,
        accelerator="cuda",
    )
    tier = QwenTier("heavy", "qwen2.5:32b", 32.0, 20.0, 48.0)
    fake_boot = BootstrappedRuntime(
        context=SimpleNamespace(mode="chat"),
        backend_selection=RuntimeBackendSelection(
            backend_name="TorchCUDABackend",
            device="cuda",
            reason="CUDA-capable GPU detected.",
            hardware=SimpleNamespace(os_name="linux", machine="x86_64"),
        ),
    )
    provider_snapshot = ProviderRegistrySnapshot(warnings=("warn",), audit_rows=tuple(), capability_truth=tuple())
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen2.5:32b",
        provider_mix=tuple(),
        estimated_download_gb=36.0,
        estimated_disk_footprint_gb=41.0,
        minimum_free_space_gb=39.0,
        ram_expectation_gb=48.0,
        vram_expectation_gb=20.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )

    with mock.patch(
        "core.runtime_backbone.bootstrap_runtime_mode",
        return_value=fake_boot,
    ) as bootstrap_runtime, mock.patch(
        "core.runtime_backbone.probe_machine",
        return_value=probe,
    ) as probe_machine, mock.patch(
        "core.runtime_backbone.select_qwen_tier",
        return_value=tier,
    ) as select_tier, mock.patch(
        "core.runtime_backbone.tier_summary",
        return_value={"accelerator": "cuda", "ram_gb": 48.0, "gpu": "NVIDIA", "vram_gb": 24.0},
    ) as tier_summary_fn, mock.patch(
        "core.runtime_backbone.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ) as provider_snapshot_fn, mock.patch(
        "core.runtime_backbone.build_install_profile_truth",
        return_value=install_profile,
    ) as install_profile_fn:
        backbone = build_runtime_backbone(
            mode="chat",
            force_policy_reload=True,
            resolve_backend=True,
        )

    bootstrap_runtime.assert_called_once_with(
        mode="chat",
        workspace_root=None,
        db_path=None,
        force_policy_reload=True,
        configure_logging=False,
        resolve_backend=True,
        manager=None,
        allow_remote_only=None,
    )
    probe_machine.assert_called_once_with()
    select_tier.assert_called_once_with(probe)
    tier_summary_fn.assert_called_once_with(probe)
    provider_snapshot_fn.assert_called_once_with(
        None,
        runtime_home=None,
        honor_install_profile=True,
        run_prewarm=True,
    )
    install_profile_fn.assert_called_once()
    assert backbone.boot is fake_boot
    assert backbone.local_model_profile.probe is probe
    assert backbone.local_model_profile.tier is tier
    assert backbone.local_model_profile.summary["backend_name"] == "TorchCUDABackend"
    assert backbone.local_model_profile.summary["backend_device"] == "cuda"
    assert backbone.provider_snapshot is provider_snapshot
    assert backbone.install_profile is install_profile


def test_cmd_providers_renders_provider_snapshot_from_runtime_backbone_facade(capsys) -> None:
    row = ProviderAuditRow(
        provider_id="local-qwen-http:qwen2.5:14b",
        source_type="http",
        license_name="Apache-2.0",
        license_reference="https://www.apache.org/licenses/LICENSE-2.0",
        runtime_dependency="ollama",
        weight_location="user-supplied",
        weights_bundled=False,
        redistribution_allowed=True,
        warnings=[],
    )
    snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=(row,), capability_truth=tuple())

    fake_context = SimpleNamespace(paths=SimpleNamespace(runtime_home="/tmp/vool-runtime"))

    with mock.patch("apps.vool_cli._bootstrap_cli_storage") as bootstrap_storage, mock.patch(
        "apps.vool_cli.build_runtime_context",
        return_value=fake_context,
    ) as build_context, mock.patch(
        "apps.vool_cli.build_provider_registry_snapshot",
        return_value=snapshot,
    ) as build_snapshot:
        assert cmd_providers(json_mode=False) == 0

    bootstrap_storage.assert_called_once_with()
    build_context.assert_called_once_with(mode="cli_storage")
    build_snapshot.assert_called_once_with(
        runtime_home="/tmp/vool-runtime",
        honor_install_profile=True,
    )
    out = capsys.readouterr().out
    assert "VOOL model providers" in out
    assert "local-qwen-http:qwen2.5:14b" in out
