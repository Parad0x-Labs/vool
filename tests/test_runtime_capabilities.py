from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from core.backend_acceleration_truth import BackendAccelerationProof
from core.hardware_tier import MachineProbe, QwenTier
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_backbone import ProviderRegistrySnapshot
from core.runtime_capabilities import runtime_capability_snapshot, runtime_capability_statuses
from core.runtime_context import RuntimeContext, RuntimeFeatureFlags, RuntimePaths
from core.runtime_install_profiles import InstallProfileTruth


def _context(**feature_overrides: bool) -> RuntimeContext:
    flags = RuntimeFeatureFlags(
        local_only_mode=feature_overrides.get("local_only_mode", False),
        public_hive_enabled=feature_overrides.get("public_hive_enabled", True),
        helper_mesh_enabled=feature_overrides.get("helper_mesh_enabled", True),
        allow_workspace_writes=feature_overrides.get("allow_workspace_writes", False),
        allow_sandbox_execution=feature_overrides.get("allow_sandbox_execution", False),
        allow_remote_only_without_backend=feature_overrides.get("allow_remote_only_without_backend", True),
    )
    paths = RuntimePaths(
        project_root=Path("/tmp/project"),
        runtime_home=Path("/tmp/runtime"),
        data_dir=Path("/tmp/runtime/data"),
        config_home_dir=Path("/tmp/runtime/config"),
        docs_dir=Path("/tmp/project/docs"),
        project_config_dir=Path("/tmp/project/config"),
        workspace_root=Path("/tmp/project/workspace"),
        db_path=Path("/tmp/runtime/data/vool.db"),
    )
    return RuntimeContext(
        mode="test",
        paths=paths,
        log_level="INFO",
        json_logs=True,
        feature_flags=flags,
    )


def test_runtime_capability_statuses_reflect_policy_disabled_surfaces() -> None:
    statuses = {item.name: item for item in runtime_capability_statuses(_context(public_hive_enabled=False, helper_mesh_enabled=False))}

    assert statuses["public_hive_surface"].state == "disabled_by_policy"
    assert statuses["helper_mesh"].state == "disabled_by_policy"
    assert statuses["simulated_payments"].state == "simulated"


def test_runtime_capability_statuses_mark_enabled_helper_and_hive_surfaces_as_implemented() -> None:
    statuses = {item.name: item for item in runtime_capability_statuses(_context())}

    assert statuses["helper_mesh"].state == "implemented"
    assert statuses["public_hive_surface"].state == "implemented"


def test_runtime_capability_snapshot_exposes_feature_flags_and_capability_rows() -> None:
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen2.5:7b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="local-qwen-http:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=12.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
                circuit_open=False,
                last_error=None,
            ),
        ),
    )

    with mock.patch("core.runtime_capabilities.probe_machine", return_value=MachineProbe(8, 12.0, None, None, "cpu")), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ):
        snapshot = runtime_capability_snapshot(
            _context(allow_workspace_writes=True, allow_sandbox_execution=True, allow_remote_only_without_backend=False)
        )

    assert snapshot["mode"] == "test"
    assert snapshot["feature_flags"]["local_only_mode"] is True
    assert snapshot["feature_flags"]["allow_workspace_writes"] is True
    assert snapshot["feature_flags"]["allow_sandbox_execution"] is True
    assert snapshot["feature_flags"]["allow_remote_only_without_backend"] is False
    assert snapshot["install_profile"]["profile_id"] == "local-only"
    assert snapshot["install_recommendation"]["recommended_default_profile"] == "local-only"
    assert snapshot["install_recommendation"]["recommended_optional_profile"] == ""
    assert snapshot["install_recommendation"]["primary_local_model"] == "qwen2.5:7b"
    assert snapshot["install_recommendation"]["secondary_local_supported"] is False
    assert snapshot["provider_capability_truth"][0]["provider_id"] == "local-qwen-http:qwen2.5:7b"
    assert snapshot["provider_capability_truth"][0]["availability_state"] == "ready"
    assert snapshot["provider_capability_truth"][0]["circuit_open"] is False
    assert snapshot["browser_tools"]["web_fetch"]["status"] in {"ok", "disabled_by_policy"}
    assert snapshot["workspace_access"]["machine_tools"]["read_roots"] == ["~/Desktop", "~/Downloads", "~/Documents"]
    assert snapshot["backend_kv_cache"]["status"] == "not_active"
    assert snapshot["speculative_decoding"]["status"] in {"inactive", "supported_not_configured"}
    assert snapshot["eagle_status"]["status"] == "unsupported_by_backend"
    assert snapshot["model_lane_defaults"]["explicit_only_models"][0]["model"] == "qwen3.5:35b-a3b"
    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert capabilities["workspace_write_tools"]["state"] == "implemented"
    assert capabilities["sandbox_execution"]["state"] == "implemented"
    assert capabilities["remote_only_backend_fallback"]["state"] == "disabled_by_policy"


def test_runtime_capability_snapshot_reports_openclaw_compaction_floor(tmp_path) -> None:
    config_dir = tmp_path / ".openclaw"
    config_dir.mkdir()
    config_path = config_dir / "openclaw.json"
    config_path.write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "compaction": {
                            "mode": "safeguard",
                            "keepRecentTokens": 12000,
                            "reserveTokensFloor": 20000,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen3:8b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(warnings=tuple(), audit_rows=tuple(), capability_truth=tuple())

    with mock.patch("core.runtime_capabilities.Path.home", return_value=tmp_path), mock.patch(
        "core.runtime_capabilities.probe_machine",
        return_value=MachineProbe(8, 12.0, None, None, "cpu"),
    ), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen3:8b", 8.0, 5.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ):
        snapshot = runtime_capability_snapshot(_context())

    compaction = snapshot["compaction_effective_config"]
    assert compaction["status"] == "configured"
    assert compaction["reserveTokensFloor"] == 20000
    assert compaction["can_recover"] is True


def test_runtime_capability_snapshot_uses_measured_provider_truth_for_profile_and_payload() -> None:
    manifest_truth = ProviderCapabilityTruth(
        provider_id="ollama-local:qwen3:8b",
        model_id="qwen3:8b",
        role_fit="drone",
        context_window=8192,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=0.0,
        ram_budget_gb=12.0,
        vram_budget_gb=0.0,
        quantization="",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
    )
    measured_truth = ProviderCapabilityTruth(
        provider_id="ollama-local:qwen3:8b",
        model_id="qwen3:8b",
        role_fit="drone",
        context_window=4096,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=19.5,
        ram_budget_gb=12.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        measurement_source="local_inference_benchmark",
        measured_at="2026-06-16T10:00:00+00:00",
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(manifest_truth,),
    )
    seen_provider_truth: list[tuple[ProviderCapabilityTruth, ...]] = []

    def fake_install_profile(**kwargs: object) -> InstallProfileTruth:
        seen_provider_truth.append(tuple(kwargs["provider_capability_truth"]))  # type: ignore[index]
        return InstallProfileTruth(
            profile_id="local-only",
            label="Local only",
            summary="Single local Ollama lane with no remote provider dependency.",
            selection_source="auto",
            selected_model="qwen3:8b",
            provider_mix=tuple(),
            estimated_download_gb=8.0,
            estimated_disk_footprint_gb=12.0,
            minimum_free_space_gb=11.0,
            ram_expectation_gb=12.0,
            vram_expectation_gb=4.0,
            ready=True,
            degraded=False,
            single_volume_ready=True,
            reasons=tuple(),
            volume_checks=tuple(),
        )

    with mock.patch("core.runtime_capabilities.probe_machine", return_value=MachineProbe(8, 12.0, None, None, "cpu")), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen3:8b", 8.0, 5.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.hydrate_capability_truth_with_benchmarks",
        return_value=(measured_truth,),
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        side_effect=fake_install_profile,
    ):
        snapshot = runtime_capability_snapshot(_context())

    assert seen_provider_truth[0][0].tokens_per_second == 19.5
    assert seen_provider_truth[0][0].measurement_source == "local_inference_benchmark"
    assert snapshot["provider_capability_truth"][0]["tokens_per_second"] == 19.5
    assert snapshot["provider_capability_truth"][0]["measurement_source"] == "local_inference_benchmark"


def test_runtime_capability_snapshot_does_not_mark_speculative_active_from_env_flag() -> None:
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local llama.cpp lane.",
        selection_source="auto",
        selected_model="qwen3:8b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="llama.cpp-local:qwen3:8b",
                model_id="qwen3:8b",
                role_fit="drone",
                context_window=8192,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=20.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        ),
    )

    with mock.patch.dict("os.environ", {"VOOL_SPECULATIVE_DECODING_ACTIVE": "1"}), mock.patch(
        "core.runtime_capabilities.probe_machine",
        return_value=MachineProbe(8, 12.0, None, None, "cpu"),
    ), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen3:8b", 8.0, 5.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ):
        snapshot = runtime_capability_snapshot(_context())

    assert snapshot["speculative_decoding"]["status"] == "supported_not_configured"
    assert snapshot["backend_kv_cache"]["rows"][0]["status"] == "supported_not_active"
    # llama.cpp build 9690+ supports EAGLE-3; without spec_type/draft model configured it's "supported_not_configured" not "unsupported"
    assert snapshot["eagle_status"]["status"] == "supported_not_configured"


def test_runtime_capability_snapshot_marks_llamacpp_cache_and_speculative_active_only_from_probe() -> None:
    install_profile = InstallProfileTruth(
        profile_id="local-max",
        label="Local max",
        summary="Local llama.cpp specialist lane.",
        selection_source="auto",
        selected_model="qwen2.5:14b-gguf",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="llamacpp-local:qwen2.5:14b-gguf",
                model_id="qwen2.5:14b-gguf",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=12.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        ),
    )
    proof = BackendAccelerationProof(
        backend="llama.cpp",
        kv_cache_status="llama.cpp=cache_active",
        backend_cache_proof={"status": "active", "probe": {"ok": True}},
        speculative_status="active",
        speculative_proof={"status": "active", "configured_method": "prompt-lookup-decoding", "probe": {"ok": True}},
        eagle_status="unsupported_by_backend",
        eagle_proof={"status": "unsupported_by_backend"},
    )

    with mock.patch("core.runtime_capabilities.probe_machine", return_value=MachineProbe(8, 12.0, None, None, "cpu")), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen3:8b", 8.0, 5.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ), mock.patch(
        "core.runtime_capabilities.backend_acceleration_proof",
        return_value=proof,
    ):
        snapshot = runtime_capability_snapshot(_context())

    assert snapshot["backend_kv_cache"]["status"] == "active"
    assert snapshot["backend_kv_cache"]["rows"][0]["status"] == "active"
    assert snapshot["backend_kv_cache"]["rows"][0]["proof"]["probe"]["ok"] is True
    assert snapshot["speculative_decoding"]["status"] == "active"
    assert snapshot["speculative_decoding"]["proof"]["configured_method"] == "prompt-lookup-decoding"
    assert snapshot["eagle_status"]["status"] == "unsupported_by_backend"


def test_runtime_capability_snapshot_disables_remote_fallback_when_profile_has_no_remote_lane() -> None:
    install_profile = InstallProfileTruth(
        profile_id="local-only",
        label="Local only",
        summary="Single local Ollama lane with no remote provider dependency.",
        selection_source="auto",
        selected_model="qwen2.5:7b",
        provider_mix=tuple(),
        estimated_download_gb=8.0,
        estimated_disk_footprint_gb=12.0,
        minimum_free_space_gb=11.0,
        ram_expectation_gb=12.0,
        vram_expectation_gb=4.0,
        ready=True,
        degraded=False,
        single_volume_ready=True,
        reasons=tuple(),
        volume_checks=tuple(),
    )
    provider_snapshot = ProviderRegistrySnapshot(
        warnings=tuple(),
        audit_rows=tuple(),
        capability_truth=(
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=12.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
                circuit_open=False,
                last_error=None,
            ),
        ),
    )

    with mock.patch("core.runtime_capabilities.probe_machine", return_value=MachineProbe(8, 12.0, None, None, "cpu")), mock.patch(
        "core.runtime_capabilities.select_qwen_tier",
        return_value=QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0),
    ), mock.patch(
        "core.runtime_capabilities.build_provider_registry_snapshot",
        return_value=provider_snapshot,
    ), mock.patch(
        "core.runtime_capabilities.build_install_profile_truth",
        return_value=install_profile,
    ):
        snapshot = runtime_capability_snapshot(_context(allow_remote_only_without_backend=True))

    assert snapshot["feature_flags"]["local_only_mode"] is True
    assert snapshot["feature_flags"]["allow_remote_only_without_backend"] is False
    capabilities = {item["name"]: item for item in snapshot["capabilities"]}
    assert capabilities["remote_only_backend_fallback"]["state"] == "disabled_by_profile"
