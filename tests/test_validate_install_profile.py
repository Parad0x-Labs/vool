from __future__ import annotations

from types import SimpleNamespace

from core.provider_routing import ProviderCapabilityTruth
from installer import validate_install_profile as validator


def test_validate_install_profile_blocks_unready_hybrid_kimi(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(capability_truth=()),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/vool-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-kimi",
    )

    assert ok is False
    assert "KIMI_API_KEY" in message
    assert "hybrid-kimi" in message


def test_validate_install_profile_accepts_ready_hybrid_kimi(monkeypatch) -> None:
    monkeypatch.setenv("KIMI_API_KEY", "test-key")
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(
            capability_truth=(
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
                    queue_depth=0,
                    max_safe_concurrency=4,
                    availability_state="ready",
                ),
            )
        ),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/vool-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-kimi",
    )

    assert ok is True
    assert message == ""


def test_validate_install_profile_blocks_unready_full_orchestrated(monkeypatch) -> None:
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(capability_truth=()),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/vool-runtime",
        selected_model="qwen2.5:14b",
        requested_profile="full-orchestrated",
    )

    assert ok is False
    assert "full-orchestrated" in message
    assert "not ready" in message


def test_validate_install_profile_accepts_ready_hybrid_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(
        validator,
        "build_provider_registry_snapshot",
        lambda **_: SimpleNamespace(
            capability_truth=(
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
            )
        ),
    )

    ok, message = validator.validate_install_profile(
        runtime_home="/tmp/vool-runtime",
        selected_model="qwen2.5:7b",
        requested_profile="hybrid-fallback",
    )

    assert ok is True
    assert message == ""


def test_validate_install_profile_passes_requested_profile_to_registry_snapshot(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)

    def _snapshot(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(capability_truth=())

    monkeypatch.setattr(validator, "build_provider_registry_snapshot", _snapshot)

    ok, _message = validator.validate_install_profile(
        runtime_home="/tmp/vool-runtime",
        selected_model="qwen2.5:14b",
        requested_profile="full-orchestrated",
    )

    assert ok is False
    assert captured["runtime_home"] == "/tmp/vool-runtime"
    assert captured["requested_profile"] == "full-orchestrated"
    assert captured["honor_install_profile"] is True
    # The selected model must be threaded into the snapshot so it registers as the local lane
    # the install is about to provision — otherwise a larger recommended model is judged
    # "unregistered" and the install aborts (the qwen3:8b GPU-host regression).
    assert captured["model_tag"] == "qwen2.5:14b"


def test_validate_install_profile_accepts_recommended_gpu_ollama_model(monkeypatch, tmp_path) -> None:
    # Regression guard for the live install abort: a GPU host recommends qwen3:8b (bucket B),
    # which is NOT the default-registered tag. With the selected model threaded into the
    # snapshot, local-only validation accepts it (Ollama pulls + serves it post-install).
    # Uses the REAL registry snapshot, not a mock, so it exercises the actual gate.
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    # Keep it hermetic: with this flag unset the snapshot never probes live Ollama's inventory
    # (the install .bat sets it via `setx`, so it can leak into a local pytest env).
    monkeypatch.delenv("VOOL_REGISTER_INSTALLED_OLLAMA_MODELS", raising=False)

    ok, message = validator.validate_install_profile(
        runtime_home=str(tmp_path),
        selected_model="qwen3:8b",
        requested_profile="local-only",
    )

    assert ok is True, message
    assert message == ""
