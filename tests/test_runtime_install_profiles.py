from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.hardware_tier import MachineProbe, QwenTier
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_install_profiles import (
    build_install_profile_truth,
    default_ollama_models_path,
    format_install_profile_id,
    install_profile_display_choices,
    installed_profile_selected_model,
    normalize_install_profile_id,
    preferred_install_profile_id,
)


def _fake_disk_usage_with_free_gb(free_gb: float) -> mock.Mock:
    fake_usage = mock.Mock()
    fake_usage.free = int(free_gb * 1024**3)
    return fake_usage


def test_auto_profile_bundle_sizing_uses_ollama_model_store_disk(monkeypatch, tmp_path) -> None:
    model_store = (tmp_path / "ollama" / "models").resolve()
    model_store.mkdir(parents=True)
    seen_paths: list[Path] = []

    def fake_disk_usage(path: str | Path) -> mock.Mock:
        seen_paths.append(Path(path).resolve())
        return _fake_disk_usage_with_free_gb(222.0)

    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=8.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("lite", "qwen2.5:3b", 3.0, 2.0, 6.0)
    monkeypatch.setenv("OLLAMA_MODELS", str(model_store))
    monkeypatch.setattr("core.runtime_install_profiles.shutil.disk_usage", fake_disk_usage)

    profile = build_install_profile_truth(
        requested_profile="auto-recommended",
        probe=probe,
        tier=tier,
        env={"VOOL_INSTALLED_OLLAMA_MODELS": "[]", "OLLAMA_MODELS": str(model_store)},
        runtime_home=str(tmp_path / "vool-runtime"),
    )

    assert model_store in seen_paths
    assert profile.capacity_bucket == "A"
    assert profile.profile_id == "local-only"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Exercises Windows drive-letter (F:\\) path semantics that POSIX pathlib can't simulate.",
)
def test_default_ollama_models_path_skips_missing_windows_override(tmp_path) -> None:
    model_store = (tmp_path / "Ollama" / "models").resolve()
    model_store.mkdir(parents=True)

    with mock.patch("core.runtime_install_profiles.platform.system", return_value="Windows"), mock.patch(
        "core.runtime_install_profiles._windows_ollama_models_env_paths",
        return_value=(model_store,),
    ):
        path = default_ollama_models_path({"OLLAMA_MODELS": "F:\\.ollama\\models"})

    assert path == model_store


def test_normalize_install_profile_id_accepts_user_friendly_ollama_aliases() -> None:
    assert normalize_install_profile_id("ollama-only") == "local-only"
    assert normalize_install_profile_id("ollama-max") == "local-max"
    assert normalize_install_profile_id("ollama+kimi") == "hybrid-kimi"
    assert normalize_install_profile_id("ollama+tether") == "hybrid-tether"


def test_install_profile_display_helpers_prefer_operator_facing_aliases() -> None:
    assert preferred_install_profile_id("local-only", allow_auto=False) == "ollama-only"
    assert format_install_profile_id("local-only", allow_auto=False) == "ollama-only (local-only)"
    assert install_profile_display_choices() == (
        "auto-recommended",
        "ollama-only (local-only)",
        "ollama-max (local-max)",
    )
    assert "ollama+kimi (hybrid-kimi)" in install_profile_display_choices(include_legacy=True)
    assert "ollama+tether (hybrid-tether)" in install_profile_display_choices(include_legacy=True)


def test_explicit_request_reason_stays_explicit_instead_of_claiming_env_override() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            requested_profile="ollama-only",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={"VOOL_INSTALLED_OLLAMA_MODELS": json.dumps(["qwen2.5:14b"])},
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert any("requested explicitly as `ollama-only`" in reason for reason in profile.reasons)


def test_auto_profile_stays_local_only_on_smaller_host_when_kimi_is_configured() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:7b",
            env={"KIMI_API_KEY": "test-key"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="local-qwen-http:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="drone",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
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
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert profile.ready is True
    assert all(item.role != "queen" for item in profile.provider_mix)
    assert any("local-first" in reason or "subscription-free" in reason for reason in profile.reasons)


def test_auto_profile_stays_local_only_when_moonshot_alias_is_configured() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:7b",
            env={"MOONSHOT_API_KEY": "test-key"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="local-qwen-http:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="drone",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
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
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert profile.ready is True
    assert all(item.role != "queen" for item in profile.provider_mix)
    assert any("local-first" in reason or "subscription-free" in reason for reason in profile.reasons)


def test_auto_profile_stays_local_only_on_smaller_host_when_generic_remote_is_configured() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:7b",
            env={"OPENAI_API_KEY": "test-key"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="local-qwen-http:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="drone",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
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
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert profile.ready is True
    assert all(item.role != "queen" for item in profile.provider_mix)
    assert any("local-first" in reason or "subscription-free" in reason for reason in profile.reasons)


def test_auto_profile_stays_local_only_on_smaller_host_when_tether_is_configured() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:7b",
            env={"TETHER_API_KEY": "test-key", "TETHER_BASE_URL": "https://tether.example/v1"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="local-qwen-http:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="drone",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
                ProviderCapabilityTruth(
                    provider_id="tether-remote:tether-sonic",
                    model_id="tether-sonic",
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
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert profile.ready is True
    assert all(item.role != "queen" for item in profile.provider_mix)
    assert any("local-first" in reason or "subscription-free" in reason for reason in profile.reasons)


def test_auto_recommended_override_still_resolves_to_real_auto_profile() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    profile = build_install_profile_truth(
        requested_profile="auto-recommended",
        probe=probe,
        tier=tier,
        env={"KIMI_API_KEY": "test-key"},
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="local-qwen-http:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="drone",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=14.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
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
            ),
        ),
        runtime_home="/tmp/vool-runtime",
    )

    assert profile.profile_id == "local-only"


def test_installed_profile_record_is_used_when_no_env_override_is_present() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        target = Path(tmpdir) / "config" / "install-profile.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"profile_id": "hybrid-kimi"}), encoding="utf-8")
        profile = build_install_profile_truth(
            probe=probe,
            tier=tier,
            env={"KIMI_API_KEY": "test-key"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="local-qwen-http:qwen2.5:7b",
                    model_id="qwen2.5:7b",
                    role_fit="drone",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=14.0,
                    ram_budget_gb=12.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
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
                ),
            ),
            runtime_home=tmpdir,
        )

    assert profile.profile_id == "hybrid-kimi"
    assert profile.selection_source == "installed_default"
    assert any("operator lane `ollama+kimi`" in reason for reason in profile.reasons)


def test_installed_profile_selected_model_reads_persisted_record() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "config" / "install-profile.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"profile_id": "local-only", "selected_model": "gemma3:4b"}),
            encoding="utf-8",
        )

        assert installed_profile_selected_model(tmpdir) == "gemma3:4b"


def test_installed_profile_selected_model_is_empty_when_no_record_exists() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        assert installed_profile_selected_model(tmpdir) == ""


def test_explicit_full_orchestrated_profile_fails_closed_when_keys_and_space_are_missing() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    fake_usage = mock.Mock()
    fake_usage.free = 10 * 1024**3

    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=fake_usage):
        profile = build_install_profile_truth(
            requested_profile="full-orchestrated",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={"VOOL_INSTALLED_OLLAMA_MODELS": "[]"},
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "full-orchestrated"
    assert profile.ready is False
    assert profile.single_volume_ready is False
    assert any("KIMI_API_KEY" in reason for reason in profile.reasons)
    assert any("single target volume" in reason for reason in profile.reasons)
    assert profile.volume_checks
    assert profile.volume_checks[0].required_gb > profile.volume_checks[0].free_gb


def test_local_max_profile_prefers_ollama_coder_even_when_llamacpp_is_listed_first() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=32.0,
        gpu_name="Apple Silicon",
        vram_gb=18.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            requested_profile="local-max",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="llamacpp-local:qwen2.5:14b-gguf",
                    model_id="qwen2.5:14b-gguf",
                    role_fit="verifier",
                    context_window=16384,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=22.0,
                    ram_budget_gb=20.0,
                    vram_budget_gb=0.0,
                    quantization="Q6_K",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-max"
    assert profile.ready is True
    assert any(item.provider_id == "ollama-local:qwen2.5:14b" and item.role == "coder" for item in profile.provider_mix)
    assert any(
        item.provider_id == "llamacpp-local:qwen2.5:14b-gguf" and item.role == "verifier"
        for item in profile.provider_mix
    )


def test_local_max_profile_fails_closed_when_primary_ollama_lane_is_blocked() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=32.0,
        gpu_name="Apple Silicon",
        vram_gb=18.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            requested_profile="local-max",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=4,
                    max_safe_concurrency=1,
                    availability_state="blocked",
                    circuit_open=True,
                    last_error="backend down",
                ),
                ProviderCapabilityTruth(
                    provider_id="llamacpp-local:qwen2.5:14b-gguf",
                    model_id="qwen2.5:14b-gguf",
                    role_fit="verifier",
                    context_window=16384,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=22.0,
                    ram_budget_gb=20.0,
                    vram_budget_gb=0.0,
                    quantization="Q6_K",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.ready is False
    assert profile.degraded is False
    coder = next(item for item in profile.provider_mix if item.role == "coder")
    verifier = next(item for item in profile.provider_mix if item.role == "verifier")
    assert coder.provider_id == "ollama-local:qwen2.5:14b"
    assert coder.availability_state == "blocked"
    assert verifier.provider_id == "llamacpp-local:qwen2.5:14b-gguf"
    assert verifier.availability_state == "ready"
    assert any("beta-ready" in reason for reason in profile.reasons)


def test_auto_profile_falls_back_to_local_only_when_local_max_disk_budget_is_not_ready() -> None:
    probe = MachineProbe(
        cpu_cores=10,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(12.5)):
        profile = build_install_profile_truth(
            requested_profile="auto-recommended",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={"VOOL_INSTALLED_OLLAMA_MODELS": "[]"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-only"
    assert profile.ready is True
    assert any("Auto-selected local-only" in reason for reason in profile.reasons)


def test_local_max_budgets_for_secondary_llamacpp_lane_even_when_primary_ollama_model_is_installed() -> None:
    probe = MachineProbe(
        cpu_cores=10,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(23.0)), mock.patch(
        "core.runtime_install_profiles.default_ollama_models_path",
        return_value=Path("/tmp/.ollama/models").resolve(),
    ):
        profile = build_install_profile_truth(
            requested_profile="local-max",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={"VOOL_INSTALLED_OLLAMA_MODELS": "qwen2.5:14b,qwen2.5:7b"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-max"
    assert profile.ready is False
    assert profile.minimum_free_space_gb == 23.0
    assert profile.volume_checks[0].required_gb == 23.0
    assert profile.volume_checks[0].free_gb == 23.0
    assert any("distinct llama.cpp local verifier lane" in reason for reason in profile.reasons)


def test_local_max_ollama_manifest_detection_does_not_fake_the_secondary_llamacpp_lane(tmp_path) -> None:
    manifest_root = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5"
    manifest_root.mkdir(parents=True)
    (manifest_root / "14b").write_text("{}", encoding="utf-8")
    (manifest_root / "7b").write_text("{}", encoding="utf-8")
    probe = MachineProbe(
        cpu_cores=10,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(23.0)), mock.patch(
        "core.runtime_install_profiles.default_ollama_models_path",
        return_value=(tmp_path / "models").resolve(),
    ):
        profile = build_install_profile_truth(
            requested_profile="local-max",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
            ),
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.profile_id == "local-max"
    assert profile.ready is False
    assert profile.minimum_free_space_gb == 23.0
    assert any("distinct llama.cpp local verifier lane" in reason for reason in profile.reasons)


def test_hybrid_kimi_profile_fails_closed_when_selected_remote_lane_is_blocked() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=12.0,
        gpu_name=None,
        vram_gb=None,
        accelerator="cpu",
    )
    tier = QwenTier("base", "qwen2.5:7b", 7.0, 4.0, 12.0)
    profile = build_install_profile_truth(
        requested_profile="hybrid-kimi",
        probe=probe,
        tier=tier,
        env={"KIMI_API_KEY": "test-key"},
        provider_capability_truth=(
            ProviderCapabilityTruth(
                provider_id="ollama-local:qwen2.5:7b",
                model_id="qwen2.5:7b",
                role_fit="coder",
                context_window=32768,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=14.0,
                ram_budget_gb=12.0,
                vram_budget_gb=0.0,
                quantization="Q4_K_M",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
                availability_state="ready",
            ),
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
                queue_depth=3,
                max_safe_concurrency=1,
                availability_state="blocked",
                circuit_open=True,
                last_error="upstream unavailable",
            ),
        ),
        runtime_home="/tmp/vool-runtime",
    )

    assert profile.ready is False
    assert any(item.availability_state == "blocked" for item in profile.provider_mix if item.role == "queen")
    assert any("beta-ready" in reason for reason in profile.reasons)


def test_full_orchestrated_profile_marks_degraded_required_lane_honestly() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=24.0,
        gpu_name="Apple Silicon",
        vram_gb=24.0,
        accelerator="mps",
    )
    tier = QwenTier("mid", "qwen2.5:14b", 14.0, 10.0, 24.0)
    with mock.patch("core.runtime_install_profiles.shutil.disk_usage", return_value=_fake_disk_usage_with_free_gb(128.0)):
        profile = build_install_profile_truth(
            requested_profile="full-orchestrated",
            probe=probe,
            tier=tier,
            selected_model="qwen2.5:14b",
            env={"KIMI_API_KEY": "test-key"},
            provider_capability_truth=(
                ProviderCapabilityTruth(
                    provider_id="ollama-local:qwen2.5:14b",
                    model_id="qwen2.5:14b",
                    role_fit="coder",
                    context_window=32768,
                    tool_support=("structured_json",),
                    structured_output_support=True,
                    tokens_per_second=16.0,
                    ram_budget_gb=24.0,
                    vram_budget_gb=0.0,
                    quantization="Q4_K_M",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
                ProviderCapabilityTruth(
                    provider_id="vllm-local:qwen2.5:32b-vllm",
                    model_id="qwen2.5:32b-vllm",
                    role_fit="verifier",
                    context_window=65536,
                    tool_support=("structured_json", "code_complex"),
                    structured_output_support=True,
                    tokens_per_second=20.0,
                    ram_budget_gb=20.0,
                    vram_budget_gb=12.0,
                    quantization="provider",
                    locality="local",
                    privacy_class="local_private",
                    queue_depth=0,
                    max_safe_concurrency=1,
                    availability_state="ready",
                ),
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
                    queue_depth=1,
                    max_safe_concurrency=2,
                    availability_state="degraded",
                ),
                ProviderCapabilityTruth(
                    provider_id="openai-compatible-remote:gpt-fallback",
                    model_id="gpt-fallback",
                    role_fit="researcher",
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
            runtime_home="/tmp/vool-runtime",
        )

    assert profile.ready is True
    assert profile.degraded is True
    assert any(item.role == "queen" and item.availability_state == "degraded" for item in profile.provider_mix)
    assert any("not fully healthy" in reason for reason in profile.reasons)


def test_installed_ollama_tags_uses_bounded_http_probe_never_subprocess(monkeypatch, tmp_path) -> None:
    # Regression for the multi-hour install freeze at step 6: `_installed_ollama_model_tags` used
    # subprocess.run(["ollama","list"], capture_output=True, timeout=15), which DEADLOCKS on
    # Windows when the long-running Ollama server inherits the child's stdout pipe — communicate()
    # blocks forever joining reader threads that never see EOF, and the timeout only bounds the
    # child, not the pipe drain (confirmed live via py-spy). It must now use the bounded HTTP probe.
    import core.runtime_install_profiles as rip

    # The deadlock-prone subprocess dependency must be gone from this module entirely.
    assert not hasattr(rip, "subprocess")

    # Force the fallback path: point the manifest scan at a dir with no manifests so tag detection
    # falls through to the model-inventory probe.
    empty_models = tmp_path / "ollama" / "models"
    empty_models.mkdir(parents=True)
    monkeypatch.setattr(rip, "default_ollama_models_path", lambda _env=None: empty_models)

    calls = {"n": 0}

    def fake_probe(*, env=None, **_kw):
        calls["n"] += 1
        return ("qwen3:8b", "llama3:latest")

    monkeypatch.setattr(rip, "installed_ollama_model_names", fake_probe)

    tags = rip._installed_ollama_model_tags(provider_capability_truth=(), env={})

    assert calls["n"] == 1  # the HTTP probe was used
    assert {"qwen3:8b", "llama3:latest"} <= tags
