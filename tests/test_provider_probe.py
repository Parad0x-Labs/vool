from __future__ import annotations

import json
import subprocess
from unittest import mock

from core.hardware_tier import GPUDevice, MachineProbe
from core.provider_routing import ProviderCapabilityTruth
from installer.provider_probe import (
    _model_installed,
    build_probe_report,
    list_ollama_models,
    remote_env_statuses,
    render_probe_report,
    run_ollama_benchmark,
)


def test_probe_report_prefers_bundle_local_stack_on_24gb_mps_host_with_required_models(monkeypatch) -> None:
    # NOTE: bundle IDs/contents here reflect the always-3-tier (tiny_fast/daily_accelerated/
    # deep_overnight) bundle redesign in core/local_model_bundles.py — bucket C's no-GPU
    # bundle is a triple, not the older dual_qwen3_8b_deepseek_r1_8b pairing.
    # Clear ambient secondary-model env overrides so the assertion below is deterministic:
    # a real install sets VOOL_LLAMACPP_DEEP_MODEL permanently, which would otherwise
    # leak into this recommendation (build_install_recommendation_truth reads os.environ).
    for _env_key in (
        "VOOL_LLAMACPP_DEEP_MODEL", "LLAMACPP_DEEP_MODEL", "VOOL_LLAMACPP_MODEL",
        "LLAMACPP_MODEL", "VOOL_LLAMA_CPP_MODEL", "LLAMA_CPP_MODEL",
    ):
        monkeypatch.delenv(_env_key, raising=False)
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[
                {"name": "qwen3:8b", "id": "a", "size": "5.2 GB", "modified": "today"},
                {"name": "deepseek-r1:8b", "id": "b", "size": "5.2 GB", "modified": "today"},
            ],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_fake_capability_truth_for(["qwen3:8b", "deepseek-r1:8b"]),
        )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["recommended_install_profile_display_id"] == "ollama-only (local-only)"
    assert report["local_multi_llm_fit"] == "pressure_sensitive"
    assert report["capacity_bucket"] == "C"
    assert report["machine"]["ollama_model"] == "qwen3:8b"
    assert report["machine"]["selected_tier"] == "capacity-C"
    assert report["machine"]["param_billions"] == 8.0
    assert report["machine"]["recommended_bundle_models"] == ["qwen3:0.6b", "qwen3:8b", "qwen3:14b"]
    local_only = next(item for item in report["stacks"] if item["stack_id"] == "local_only")
    assert local_only["install_profile_id"] == "local-only"
    assert local_only["install_profile_display_id"] == "ollama-only (local-only)"
    assert local_only["recommended"] is True
    assert local_only["bundle_id"] == "triple_bucket_c_no_gpu"
    assert local_only["primary_model"] == "qwen3:8b"
    recommendation = report["install_recommendation"]
    assert recommendation["recommended_default_profile"] == "local-only"
    assert recommendation["recommended_optional_profile"] == "local-max"
    assert recommendation["primary_local_model"] == "qwen3:8b"
    assert recommendation["recommended_bundle_id"] == "triple_bucket_c_no_gpu"
    assert recommendation["recommended_bundle_models"] == ["qwen3:0.6b", "qwen3:8b", "qwen3:14b"]
    assert recommendation["secondary_local_model"] == "qwen2.5:14b-gguf"
    assert recommendation["secondary_local_supported"] is True
    dual = next(item for item in report["stacks"] if item["stack_id"] == "local_plus_llamacpp")
    assert dual["install_profile_id"] == "local-max"
    assert dual["install_profile_display_id"] == "ollama-max (local-max)"
    assert dual["status"] == "needs_setup"
    assert dual["recommended"] is False
    assert dual["secondary_backend"] == "llama.cpp"
    assert dual["secondary_model"] == "qwen2.5:14b-gguf"


def test_probe_report_hides_remote_lanes_but_keeps_remote_env_truth_and_qvac_honest() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=10, ram_gb=24.0, gpu_name="Apple Silicon", vram_gb=24.0, accelerator="mps"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=4,
                availability_state="ready",
            ),
        ),
        show_unsupported=True,
    )

    qvac = next(item for item in report["unsupported_stacks"] if item["stack_id"] == "local_plus_qvac")

    assert all(item["stack_id"] != "local_plus_kimi" for item in report["stacks"])
    assert all(item["stack_id"] != "local_plus_tether" for item in report["stacks"])
    assert report["remote_env"]["kimi"]["configured"] is True
    assert report["remote_env"]["tether"]["configured"] is True
    assert qvac["status"] == "not_implemented"


def test_probe_report_keeps_local_only_default_when_smaller_host_has_real_remote_lane() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": True},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="openai-compatible-remote:gpt-4.1-mini",
                model_id="gpt-4.1-mini",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=2,
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["install_recommendation"]["recommended_default_profile"] == "local-only"
    assert report["install_recommendation"]["recommended_optional_profile"] == ""
    assert report["install_recommendation"]["secondary_local_supported"] is False
    assert report["remote_env"]["generic_remote"]["configured"] is True
    assert all(item["stack_id"] != "local_plus_remote_openai_compatible" for item in report["stacks"])


def test_probe_report_keeps_local_only_default_when_kimi_is_configured_and_ready() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": True},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="kimi-remote:kimi-k2",
                model_id="kimi-k2",
                role_fit="queen",
                context_window=131072,
                tool_support=("tool_calls", "structured_json"),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="provider",
                locality="remote",
                privacy_class="remote_provider",
                queue_depth=0,
                max_safe_concurrency=4,
                availability_state="ready",
            ),
        ),
    )

    assert report["recommended_stack_id"] == "local_only"
    assert report["recommended_install_profile_id"] == "local-only"
    assert report["recommended_install_profile_display_id"] == "ollama-only (local-only)"
    assert report["install_recommendation"]["recommended_default_profile"] == "local-only"
    assert report["install_recommendation"]["secondary_local_supported"] is False
    assert report["remote_env"]["kimi"]["configured"] is True
    assert all(item["stack_id"] != "local_plus_kimi" for item in report["stacks"])


def test_render_probe_report_surfaces_installed_models_and_recommendation() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": False},
            "qvac": {"configured": False},
        },
        provider_capability_truth=_fake_capability_truth_for(["qwen2.5:7b"]),
    )

    rendered = render_probe_report(report)
    assert "recommended install profile" in rendered.lower()
    assert "recommended stack" in rendered.lower()
    assert "gemma3:4b" in rendered
    assert "ollama-only (local-only)" in rendered
    assert "local_plus_llamacpp" in rendered
    assert "local_plus_remote_openai_compatible" not in rendered
    assert "local_plus_tether" not in rendered
    assert "ollama+tether (hybrid-tether)" not in rendered


def test_probe_report_surfaces_gpu_inventory_and_optional_live_check(monkeypatch) -> None:
    def fake_benchmark(**kwargs) -> dict[str, object]:
        return {
            "schema": "vool.local_model_benchmark.v1",
            "status": "ok",
            "model": kwargs["model_name"],
            "elapsed_seconds": 2.5,
            "rough_output_tokens_per_second": 0.4,
        }

    monkeypatch.setattr("installer.provider_probe.run_ollama_benchmark", fake_benchmark)

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=16,
                ram_gb=32.0,
                gpu_name="NVIDIA GeForce RTX 4090",
                vram_gb=24.0,
                accelerator="cuda",
                accelerator_status="usable",
                gpu_devices=(
                    GPUDevice(
                        index=0,
                        name="NVIDIA GeForce RTX 4090",
                        vendor="nvidia",
                        vram_gb=24.0,
                        backend="cuda",
                        status="usable",
                        source="nvidia-smi",
                    ),
                ),
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            run_benchmark=True,
            provider_capability_truth=_fake_capability_truth_for(["placeholder-unused-model"]),
        )

    rendered = render_probe_report(report)

    assert report["local_model_benchmark"]["status"] == "ok"
    assert "detected GPUs: [0] NVIDIA GeForce RTX 4090 24.0 GB cuda usable active" in rendered
    assert "local model live check: ok on" in rendered
    assert "2.5s wall-clock" in rendered
    assert "rough output tokens/sec: 0.4" in rendered


def test_run_ollama_benchmark_records_marker_and_elapsed(monkeypatch) -> None:
    clock = mock.Mock(side_effect=[10.0, 12.5])
    monkeypatch.setattr("installer.provider_probe.time.perf_counter", clock)

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        assert args[0][:3] == ["ollama", "run", "gemma3:4b"]
        assert kwargs["timeout"] == 9
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="VOOL_BENCH_OK",
            stderr="",
        )

    monkeypatch.setattr("installer.provider_probe.subprocess.run", fake_run)

    result = run_ollama_benchmark(
        model_name="gemma3:4b",
        ollama_binary="ollama",
        timeout_seconds=9,
    )

    assert result["status"] == "ok"
    assert result["marker_seen"] is True
    assert result["elapsed_seconds"] == 2.5
    assert result["rough_output_tokens_per_second"] == 0.4


def test_probe_report_surfaces_accelerator_warning_and_model_pull_plan() -> None:
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=8,
                ram_gb=8.0,
                gpu_name="NVIDIA GeForce GTX 1080",
                vram_gb=8.0,
                accelerator="cpu",
                accelerator_status="legacy_cuda_cpu_recommended",
                accelerator_advice="Legacy CUDA fallback active.",
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_fake_capability_truth_for(["qwen2.5:7b"]),
        )

    # 8GB RAM/legacy-CUDA host is capacity bucket A, whose no-GPU bundle is the always-3-tier
    # (tiny_fast/daily_accelerated/deep_overnight) bundle - so all 3 models are recommended,
    # not a single "gemma3:4b" (that was the pre-3-tier-bundle-redesign behavior).
    assert report["machine"]["accelerator"] == "cpu"
    assert report["machine"]["accelerator_status"] == "legacy_cuda_cpu_recommended"
    assert report["local_model_plan"]["recommended_models"] == ["qwen3:0.6b", "qwen3:4b", "gemma3:4b"]
    assert report["local_model_plan"]["missing_recommended_models"] == ["qwen3:0.6b", "qwen3:4b", "gemma3:4b"]
    assert report["local_model_plan"]["pull_commands"] == [
        "ollama pull qwen3:0.6b",
        "ollama pull qwen3:4b",
        "ollama pull gemma3:4b",
    ]
    assert report["local_model_plan"]["status"] == "needs_setup"
    assert report["stacks"][0]["status"] == "needs_setup"

    rendered = render_probe_report(report)

    assert "accelerator status: legacy_cuda_cpu_recommended" in rendered
    assert "missing recommended models: qwen3:0.6b, qwen3:4b, gemma3:4b" in rendered
    assert "ollama pull gemma3:4b" in rendered


def _fake_capability_truth_for(model_names: list[str]) -> tuple[ProviderCapabilityTruth, ...]:
    return tuple(
        ProviderCapabilityTruth(
            provider_id=f"ollama-local:{name}",
            model_id=name,
            role_fit="drone",
            context_window=4096,
            tool_support=("tool_calls", "structured_json"),
            structured_output_support=True,
            tokens_per_second=0.0,
            ram_budget_gb=0.0,
            vram_budget_gb=0.0,
            quantization="",
            locality="local",
            privacy_class="local_private",
            queue_depth=0,
            max_safe_concurrency=1,
            availability_state="ready",
        )
        for name in model_names
    )


def test_probe_report_skips_pulling_models_already_installed() -> None:
    # 8GB RAM, legacy-CUDA GTX 1080 host. Discover this hardware profile's real
    # recommended bundle first (round-trip, no hardcoded model names — the exact
    # bundle contents are a separate concern owned by core/local_model_bundles.py
    # and can legitimately change), then feed those SAME models back as already
    # installed and confirm the installer correctly skips re-downloading all of
    # them. Regression coverage for "does the installer actually skip
    # already-installed models" — every existing test in this file only
    # exercised the missing-model path, never this one.
    machine = MachineProbe(
        cpu_cores=8,
        ram_gb=8.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cpu",
        accelerator_status="legacy_cuda_cpu_recommended",
        accelerator_advice="Legacy CUDA fallback active.",
    )
    base_env_statuses = {
        "kimi": {"configured": False},
        "generic_remote": {"configured": False},
        "tether": {"configured": False},
        "qvac": {"configured": False},
    }
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        discovery_report = build_probe_report(
            machine=machine,
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses=base_env_statuses,
            # A single placeholder entry, not meant to match anything — its only job is to be a
            # non-empty tuple so build_probe_report skips its live provider-registry snapshot
            # fallback (an empty tuple is falsy and would trigger that live network path).
            provider_capability_truth=_fake_capability_truth_for(["placeholder-unused-model"]),
        )
    recommended = discovery_report["local_model_plan"]["recommended_models"]
    assert recommended, "expected at least one recommended model for this hardware profile"

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=machine,
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": name, "id": name, "size": "1 GB", "modified": "today"} for name in recommended],
            env_statuses=base_env_statuses,
            provider_capability_truth=_fake_capability_truth_for(recommended),
        )

    assert sorted(report["local_model_plan"]["installed_recommended_models"]) == sorted(recommended)
    assert report["local_model_plan"]["missing_recommended_models"] == []
    assert report["local_model_plan"]["pull_commands"] == []
    assert report["local_model_plan"]["estimated_missing_download_gb"] == 0.0
    assert report["local_model_plan"]["status"] == "ready"
    assert report["stacks"][0]["status"] == "ready"

    rendered = render_probe_report(report)
    assert "ollama pull" not in rendered


def test_probe_report_only_pulls_the_missing_model_in_a_partial_match() -> None:
    # A multi-model bundle where one member is already installed and others aren't -
    # only the genuinely missing models should show up in missing/pull_commands; the
    # already-installed one must not get a redundant pull command.
    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(cpu_cores=16, ram_gb=32.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": "qwen3:0.6b", "id": "a", "size": "0.7 GB", "modified": "today"}],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen3:0.6b",
                    model_id="qwen3:0.6b",
                    role_fit="drone",
                    context_window=4096,
                    tool_support=("tool_calls", "structured_json"),
                    structured_output_support=True,
                    tokens_per_second=0.0,
                    ram_budget_gb=0.0,
                    vram_budget_gb=0.0,
                    quantization="",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
            ),
        )

    plan = report["local_model_plan"]
    assert len(plan["recommended_models"]) > 1, "expected a multi-model bundle for this hardware profile"
    assert "qwen3:0.6b" in plan["installed_recommended_models"]
    assert "qwen3:0.6b" not in plan["missing_recommended_models"]
    assert all("qwen3:0.6b" not in cmd for cmd in plan["pull_commands"])
    assert len(plan["missing_recommended_models"]) == len(plan["recommended_models"]) - 1


def test_model_installed_normalizes_bare_name_against_explicit_latest_tag() -> None:
    # Ollama reports a pulled bare-name model (e.g. "gemma3") as "gemma3:latest" in
    # `ollama list`. A recommendation phrased as the bare name must still match.
    assert _model_installed("gemma3", {"gemma3:latest"}) is True


def test_model_installed_normalizes_explicit_latest_recommendation_against_bare_installed_name() -> None:
    # The reverse direction: recommendation explicitly says ":latest" but `ollama list`
    # reports the bare name (this can happen depending on how the model was pulled).
    assert _model_installed("gemma3:latest", {"gemma3"}) is True


def test_model_installed_exact_tag_match() -> None:
    assert _model_installed("qwen3:0.6b", {"qwen3:0.6b"}) is True


def test_model_installed_returns_false_when_genuinely_missing() -> None:
    assert _model_installed("qwen3:8b", {"gemma3:4b", "qwen3:0.6b"}) is False


def test_model_installed_does_not_false_positive_on_substring_tag() -> None:
    # "qwen3:4b" must not be considered installed just because "qwen3:14b" is present -
    # a naive substring check would incorrectly match here.
    assert _model_installed("qwen3:4b", {"qwen3:14b"}) is False


def test_probe_report_blocks_pull_plan_when_safe_disk_floor_is_not_met() -> None:
    with mock.patch("core.install_recommendations._free_gb", return_value=27.0):
        report = build_probe_report(
            machine=MachineProbe(
                cpu_cores=8,
                ram_gb=8.0,
                gpu_name="NVIDIA GeForce GTX 1080",
                vram_gb=8.0,
                accelerator="cpu",
                accelerator_status="legacy_cuda_cpu_recommended",
                accelerator_advice="Legacy CUDA fallback active.",
            ),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_fake_capability_truth_for(["qwen2.5:7b"]),
        )

    # Bucket A's always-3-tier bundle means more total download size than the old
    # single-model recommendation, so the safe-disk-floor gap is larger too.
    assert report["local_model_plan"]["status"] == "needs_space"
    assert report["local_model_plan"]["minimum_space_to_free_gb"] == 4.5
    assert report["stacks"][0]["status"] == "needs_space"

    rendered = render_probe_report(report)

    assert "disk action: free at least 4.5 GB before pulling" in rendered
    assert "target volume is below the safe disk floor" in rendered


def test_default_probe_report_hides_unsupported_remote_ideas() -> None:
    report = build_probe_report(
        machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
        ollama_binary="/usr/local/bin/ollama",
        ollama_models=[{"name": "qwen2.5:7b", "id": "b", "size": "4.7 GB", "modified": "today"}],
        env_statuses={
            "kimi": {"configured": False},
            "generic_remote": {"configured": False},
            "tether": {"configured": True},
            "qvac": {"configured": True},
        },
        provider_capability_truth=_fake_capability_truth_for(["qwen2.5:7b"]),
    )

    assert "unsupported_stacks" not in report
    assert all(item["stack_id"] != "local_plus_tether" for item in report["stacks"])
    assert all(item["stack_id"] != "local_plus_qvac" for item in report["stacks"])


def test_list_ollama_models_preserves_size_and_modified_columns(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])

    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "NAME           ID              SIZE      MODIFIED      \n"
                "qwen2.5:14b    7cdf5a0187d5    9.0 GB    3 minutes ago    \n"
            ),
            stderr="",
        )

    monkeypatch.setattr("installer.provider_probe.subprocess.run", fake_run)
    monkeypatch.setattr("installer.provider_probe.detect_ollama_binary", lambda: "/usr/local/bin/ollama")

    rows = list_ollama_models()

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "9.0 GB",
            "modified": "3 minutes ago",
        }
    ]


def test_list_ollama_models_prefers_tags_api_and_avoids_cli_shellout(monkeypatch) -> None:
    monkeypatch.setattr("installer.provider_probe.shutil.which", lambda name: "/usr/bin/curl" if name == "curl" else "")
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_manifests", lambda: [])
    monkeypatch.setattr(
        "installer.provider_probe.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                '{"models":[{"name":"qwen2.5:14b","digest":"7cdf5a0187d5abcd","size":8988124069,'
                '"modified_at":"2026-03-28T09:38:28Z"}]}'
            ),
            stderr="",
        ),
    )

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert rows == [
        {
            "name": "qwen2.5:14b",
            "id": "7cdf5a0187d5",
            "size": "8.4 GB",
            "modified": "2026-03-28T09:38:28Z",
        }
    ]


def test_list_ollama_models_falls_back_to_manifest_inventory(monkeypatch, tmp_path) -> None:
    manifest_root = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5"
    manifest_root.mkdir(parents=True)
    (manifest_root / "14b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2049f5674b1e92b4464e5729975c9689fcfbf0b0e4443ccf10b5339f370f9a54",
                        "size": 8988110688,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (manifest_root / "7b").write_text(
        json.dumps(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.ollama.image.model",
                        "digest": "sha256:2bada8a7450677000f678be90653b85d364de7db25eb5ea54136ada5f3933730",
                        "size": 4683073952,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("installer.provider_probe._list_ollama_models_via_api", lambda *args, **kwargs: [])
    monkeypatch.setattr("installer.provider_probe.default_ollama_models_path", lambda: (tmp_path / "models").resolve())

    rows = list_ollama_models("/usr/local/bin/ollama")

    assert [row["name"] for row in rows] == ["qwen2.5:14b", "qwen2.5:7b"]


def test_remote_env_statuses_accepts_moonshot_aliases_for_kimi() -> None:
    with mock.patch.dict(
        "os.environ",
        {
            "MOONSHOT_API_KEY": "test-key",
            "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
            "MOONSHOT_MODEL": "kimi-k2",
        },
        clear=True,
    ):
        statuses = remote_env_statuses()

    assert statuses["kimi"]["api_key_present"] is True
    assert statuses["kimi"]["base_url_present"] is True
    assert statuses["kimi"]["model_present"] is True
    assert statuses["kimi"]["configured"] is True
