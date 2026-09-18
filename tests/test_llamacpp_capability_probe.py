from __future__ import annotations

import json
from unittest import mock

from core.llamacpp_capability_probe import (
    gpu_vendor_to_backend_candidates,
    probe_llamacpp_capability,
)


def test_gpu_vendor_to_backend_candidates_nvidia_prefers_cuda_with_vulkan_fallback() -> None:
    candidates = gpu_vendor_to_backend_candidates("nvidia", "GeForce GTX 1080")

    assert [item.backend for item in candidates] == ["cuda", "vulkan"]
    assert candidates[0].requires_runtime_asset == "cudart-llama-bin-win-cuda-12.4-x64.zip"
    assert candidates[1].requires_runtime_asset == ""


def test_gpu_vendor_to_backend_candidates_amd_prefers_hip_with_vulkan_fallback() -> None:
    candidates = gpu_vendor_to_backend_candidates("amd", "Radeon RX 7900 XTX")

    assert [item.backend for item in candidates] == ["hip", "vulkan"]


def test_gpu_vendor_to_backend_candidates_unknown_vendor_gets_vulkan_only() -> None:
    candidates = gpu_vendor_to_backend_candidates("unknown", "Some Weird GPU")

    assert [item.backend for item in candidates] == ["vulkan"]


def test_probe_skips_when_no_gpu_present(tmp_path) -> None:
    result = probe_llamacpp_capability(
        gpu_name="",
        gpu_vendor="",
        binary_path=tmp_path / "llama-server.exe",
        tiny_model_path=tmp_path / "probe.gguf",
        backend_tested="cpu",
        binary_release_tag="b9856",
        runtime_home=tmp_path,
    )

    assert result.status == "skipped_no_gpu"
    assert not result.usable


def test_probe_reports_launch_failure_when_binary_missing(tmp_path) -> None:
    result = probe_llamacpp_capability(
        gpu_name="GeForce GTX 1080",
        gpu_vendor="nvidia",
        binary_path=tmp_path / "does-not-exist.exe",
        tiny_model_path=tmp_path / "does-not-exist.gguf",
        backend_tested="cuda",
        binary_release_tag="b9856",
        runtime_home=tmp_path,
    )

    assert result.status == "gpu_launch_failed"
    assert not result.usable


def test_probe_confirms_fast_when_measured_speedup_is_high(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[36.0, 2.0],
    ):
        result = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert result.status == "gpu_confirmed_fast"
    assert result.usable
    assert result.verdict_backend == "cuda"
    assert result.speedup_ratio > 2.5


def test_probe_rejects_slow_gpu_below_absolute_floor(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[2.0, 1.5],
    ):
        result = probe_llamacpp_capability(
            gpu_name="Ancient Integrated GPU",
            gpu_vendor="intel",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="vulkan",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert result.status == "gpu_rejected_slow"
    assert not result.usable
    assert result.verdict_backend == "cpu"


def test_probe_rejects_slow_gpu_when_ratio_below_marginal_threshold(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[5.0, 4.5],
    ):
        result = probe_llamacpp_capability(
            gpu_name="Weak GPU",
            gpu_vendor="unknown",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="vulkan",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert result.status == "gpu_rejected_slow"
    assert not result.usable


def test_probe_marginal_between_thresholds(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[10.0, 5.5],
    ):
        result = probe_llamacpp_capability(
            gpu_name="Mid GPU",
            gpu_vendor="amd",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="hip",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert result.status == "gpu_confirmed_marginal"
    assert result.usable
    assert result.verdict_backend == "hip"


def test_probe_cache_is_scoped_per_backend_not_just_per_gpu(tmp_path) -> None:
    # Regression test: an orchestrator trying cuda (rejected), then vulkan, for the
    # SAME gpu_name must not have the vulkan attempt silently served the cuda
    # attempt's cached "rejected" verdict — each backend needs its own cache entry.
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[2.0, 1.8],  # cuda: rejected (low speedup)
    ):
        cuda_result = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[36.0, 2.0],  # vulkan: fast, should genuinely run, not be cached-skipped
    ) as vulkan_run_mock:
        vulkan_result = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="vulkan",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert cuda_result.status == "gpu_rejected_slow"
    assert vulkan_run_mock.call_count == 2  # genuinely ran, not served from cuda's cache entry
    assert vulkan_result.status == "gpu_confirmed_fast"
    assert vulkan_result.usable

    cache_path = tmp_path / "config" / "llamacpp-capability-probe.json"
    cached_raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(cached_raw) == 2  # cuda and vulkan each got their own entry


def test_probe_result_is_cached_and_not_recomputed(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[36.0, 2.0],
    ) as run_mock:
        first = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )
        second = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )

    assert run_mock.call_count == 2  # only the first probe call ran the benchmark
    assert first.status == second.status == "gpu_confirmed_fast"
    assert second.gpu_tokens_per_second == first.gpu_tokens_per_second

    cache_path = tmp_path / "config" / "llamacpp-capability-probe.json"
    assert cache_path.exists()
    cached_raw = json.loads(cache_path.read_text(encoding="utf-8"))
    assert len(cached_raw) == 1


def test_probe_force_flag_bypasses_cache(tmp_path) -> None:
    binary_path = tmp_path / "llama-server.exe"
    model_path = tmp_path / "probe.gguf"
    binary_path.write_bytes(b"fake")
    model_path.write_bytes(b"fake")

    with mock.patch(
        "core.llamacpp_capability_probe._timed_single_run",
        side_effect=[36.0, 2.0, 1.0, 1.0],
    ) as run_mock:
        probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
        )
        second = probe_llamacpp_capability(
            gpu_name="GeForce GTX 1080",
            gpu_vendor="nvidia",
            binary_path=binary_path,
            tiny_model_path=model_path,
            backend_tested="cuda",
            binary_release_tag="b9856",
            runtime_home=tmp_path,
            force=True,
        )

    assert run_mock.call_count == 4
    assert second.status == "gpu_rejected_slow"
