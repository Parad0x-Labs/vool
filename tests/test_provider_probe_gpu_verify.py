from __future__ import annotations

from typing import Any
from unittest import mock

from core.hardware_tier import GPUDevice, MachineProbe
from core.provider_routing import ProviderCapabilityTruth
from installer.provider_probe import build_probe_report, render_probe_report, run_gpu_inference_check


def _capability_truth(model_names: list[str]) -> tuple[ProviderCapabilityTruth, ...]:
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


def _cuda_probe() -> MachineProbe:
    return MachineProbe(
        cpu_cores=16,
        ram_gb=32.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cuda",
        accelerator_status="usable",
        gpu_devices=(
            GPUDevice(
                index=0,
                name="NVIDIA GeForce GTX 1080",
                vendor="nvidia",
                vram_gb=8.0,
                backend="cuda",
                status="usable",
                source="nvidia-smi",
                driver_version="566.36",
                vram_free_gb=5.9,
            ),
        ),
    )


def test_gpu_verify_runs_by_default_and_surfaces_crash_verdict(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_check(**kwargs) -> dict[str, Any]:
        seen.update(kwargs)
        return {
            "schema": "vool.gpu_inference_verdict.v1",
            "model": kwargs["model_name"],
            "outcome": "driver_too_old",
            "reason": "The GPU driver is too old for the local inference backend.",
            "recommend_cpu_fallback": True,
            "recommended_num_gpu": 0,
            "suggested_action": "update_driver",
            "driver_version": "566.36",
            "vram_free_gb": 5.9,
            "cpu_fallback_model": "qwen2.5:3b",
            "applied_cpu_fallback": True,
            "advisory": "The GPU driver is too old, so VOOL is running on CPU with qwen2.5:3b.",
        }

    monkeypatch.setattr("installer.provider_probe.run_gpu_inference_check", fake_check)

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=_cuda_probe(),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_capability_truth(["placeholder-unused-model"]),
        )

    verdict = report["gpu_inference_verdict"]
    assert verdict["outcome"] == "driver_too_old"
    assert verdict["applied_cpu_fallback"] is True
    assert verdict["cpu_fallback_model"] == "qwen2.5:3b"
    # The seam received the recommended primary model and the probe.
    assert seen["probe"].accelerator == "cuda"
    assert seen["model_name"]

    rendered = render_probe_report(report)
    assert "gpu inference check: driver_too_old" in rendered
    assert "gpu inference advice:" in rendered
    assert "recommending CPU fallback with qwen2.5:3b" in rendered


def test_gpu_verify_skipped_on_cpu_host(monkeypatch) -> None:
    called = mock.Mock()
    monkeypatch.setattr("installer.provider_probe.run_gpu_inference_check", called)

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=MachineProbe(cpu_cores=8, ram_gb=12.0, gpu_name=None, vram_gb=None, accelerator="cpu"),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_capability_truth(["placeholder-unused-model"]),
        )

    called.assert_not_called()
    assert "gpu_inference_verdict" not in report


def test_gpu_verify_skipped_when_ollama_binary_missing(monkeypatch) -> None:
    called = mock.Mock()
    monkeypatch.setattr("installer.provider_probe.run_gpu_inference_check", called)
    # No explicit binary AND detection finds none -> the live GPU check must not run.
    monkeypatch.setattr("installer.provider_probe.detect_ollama_binary", lambda: "")

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=_cuda_probe(),
            ollama_binary="",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_capability_truth(["placeholder-unused-model"]),
        )

    called.assert_not_called()
    assert "gpu_inference_verdict" not in report


def test_gpu_verify_can_be_disabled_explicitly(monkeypatch) -> None:
    called = mock.Mock()
    monkeypatch.setattr("installer.provider_probe.run_gpu_inference_check", called)

    with mock.patch("core.install_recommendations._free_gb", return_value=120.0):
        report = build_probe_report(
            machine=_cuda_probe(),
            ollama_binary="/usr/local/bin/ollama",
            ollama_models=[],
            env_statuses={
                "kimi": {"configured": False},
                "generic_remote": {"configured": False},
                "tether": {"configured": False},
                "qvac": {"configured": False},
            },
            provider_capability_truth=_capability_truth(["placeholder-unused-model"]),
            verify_gpu=False,
        )

    called.assert_not_called()
    assert "gpu_inference_verdict" not in report


def test_run_gpu_inference_check_threads_driver_and_fallback(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_verify(**kwargs) -> Any:
        captured.update(kwargs)
        return SimpleVerdict(
            outcome="vram_insufficient",
            reason="model too big",
            raw_excerpt="cudaMalloc failed: out of memory",
            recommend_cpu_fallback=True,
            recommended_num_gpu=0,
            suggested_action="free_vram_or_smaller_model",
        )

    advisory_args: dict[str, Any] = {}

    def fake_advisory(verdict, **kwargs) -> str:
        advisory_args.update(kwargs)
        return "The model does not fit the free VRAM, so VOOL is using CPU."

    monkeypatch.setattr("installer.provider_probe.verify_gpu_inference", fake_verify)
    monkeypatch.setattr("installer.provider_probe.gpu_capability_advisory", fake_advisory)

    result = run_gpu_inference_check(model_name="qwen3:8b", probe=_cuda_probe())

    assert result["outcome"] == "vram_insufficient"
    assert result["applied_cpu_fallback"] is True
    assert result["driver_version"] == "566.36"
    assert result["vram_free_gb"] == 5.9
    # The live verify call was told the GPU is present and given the model + base url.
    assert captured["gpu_present"] is True
    assert captured["model"] == "qwen3:8b"
    # The advisory was handed the driver, free VRAM, and a CPU fallback model to name.
    assert advisory_args["driver_version"] == "566.36"
    assert advisory_args["vram_free_gb"] == 5.9
    assert advisory_args["cpu_fallback_model"]


class SimpleVerdict:
    def __init__(
        self,
        *,
        outcome: str,
        reason: str,
        raw_excerpt: str,
        recommend_cpu_fallback: bool,
        recommended_num_gpu: int,
        suggested_action: str,
    ) -> None:
        self.outcome = outcome
        self.reason = reason
        self.raw_excerpt = raw_excerpt
        self.recommend_cpu_fallback = recommend_cpu_fallback
        self.recommended_num_gpu = recommended_num_gpu
        self.suggested_action = suggested_action
