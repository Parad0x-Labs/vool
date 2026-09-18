from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.hardware_tier import MachineProbe
from core.install_recommendations import build_install_recommendation_truth


def test_install_recommendation_uses_ollama_model_store_disk(monkeypatch, tmp_path) -> None:
    model_store = (tmp_path / "ollama" / "models").resolve()
    model_store.mkdir(parents=True)
    seen_paths: list[Path] = []

    def fake_disk_usage(path: str | Path) -> SimpleNamespace:
        seen_paths.append(Path(path).resolve())
        gib = 1024**3
        return SimpleNamespace(total=500 * gib, used=278 * gib, free=222 * gib)

    monkeypatch.setenv("OLLAMA_MODELS", str(model_store))
    monkeypatch.setattr("core.install_recommendations.shutil.disk_usage", fake_disk_usage)

    recommendation = build_install_recommendation_truth(
        probe=MachineProbe(
            cpu_cores=8,
            ram_gb=8.0,
            gpu_name=None,
            vram_gb=None,
            accelerator="cpu",
        ),
        env={},
    )

    assert seen_paths == [model_store]
    assert recommendation.free_disk_gb == 222.0


# --- GPU capability probe wiring (last-mile: capable GPUs get GPU-sized models) --------

from unittest import mock


def _cap(usable: bool = True) -> SimpleNamespace:
    return SimpleNamespace(usable=usable, gpu_tokens_per_second=35.0, speedup_ratio=4.0)


def _orch(result: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(result=result, attempts=())


def _gpu_probe(accelerator: str, vram, ram: float = 32.0) -> MachineProbe:
    return MachineProbe(cpu_cores=16, ram_gb=ram, gpu_name="Test GPU", vram_gb=vram, accelerator=accelerator)


def _mock_disk(monkeypatch, free_gib: int = 400) -> None:
    def fake_disk_usage(path):
        gib = 1024**3
        return SimpleNamespace(total=1000 * gib, used=(1000 - free_gib) * gib, free=free_gib * gib)

    monkeypatch.setattr("core.install_recommendations.shutil.disk_usage", fake_disk_usage)


def _has_gguf(rec) -> bool:
    return any("q4_k_m" in str(m) for m in rec.recommended_bundle_models)


def test_gpu_bundle_gated_off_by_default(monkeypatch, tmp_path) -> None:
    # Until the GGUF provider lane is wired into install, a capable GPU must NOT be probed by
    # default — a GGUF bundle would fail install-profile validation and abort the install.
    _mock_disk(monkeypatch)
    monkeypatch.delenv("VOOL_ENABLE_GPU_BUNDLE", raising=False)
    monkeypatch.delenv("VOOL_SKIP_GPU_PROBE", raising=False)
    spy = mock.Mock(return_value=_orch(_cap(usable=True)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    rec = build_install_recommendation_truth(probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path))
    spy.assert_not_called()  # gated off -> no benchmark
    assert not _has_gguf(rec)  # ...and the safe CPU/Ollama bundle, so the install can't break


def test_capable_gpu_is_probed_and_gets_gpu_bundle(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    monkeypatch.delenv("VOOL_SKIP_GPU_PROBE", raising=False)
    monkeypatch.setenv("VOOL_ENABLE_GPU_BUNDLE", "1")  # opt in to the GPU-bundle path
    spy = mock.Mock(return_value=_orch(_cap(usable=True)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    rec = build_install_recommendation_truth(probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path))
    spy.assert_called_once()  # a capable discrete GPU triggers the measured benchmark
    assert _has_gguf(rec)  # ...and the recommendation is the llama.cpp GPU bundle


def test_cpu_only_skips_probe_and_gets_cpu_bundle(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    spy = mock.Mock(return_value=_orch(_cap(True)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    rec = build_install_recommendation_truth(probe=_gpu_probe("cpu", None, ram=64.0), runtime_home=str(tmp_path))
    spy.assert_not_called()  # no GPU -> no download/benchmark cost
    assert not _has_gguf(rec)


def test_mps_and_small_vram_skip_probe(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    spy = mock.Mock(return_value=_orch(_cap(True)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    build_install_recommendation_truth(probe=_gpu_probe("mps", 24.0), runtime_home=str(tmp_path))
    build_install_recommendation_truth(probe=_gpu_probe("cuda", 4.0), runtime_home=str(tmp_path))
    spy.assert_not_called()  # MPS never uses the CUDA bundle; <6GB can't hold it


def test_env_skip_gpu_probe(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    monkeypatch.setenv("VOOL_SKIP_GPU_PROBE", "1")
    spy = mock.Mock(return_value=_orch(_cap(True)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    build_install_recommendation_truth(probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path))
    spy.assert_not_called()


def test_probe_failure_falls_back_to_cpu_bundle(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    monkeypatch.delenv("VOOL_SKIP_GPU_PROBE", raising=False)
    monkeypatch.setenv("VOOL_ENABLE_GPU_BUNDLE", "1")

    def _boom(**_kw):
        raise RuntimeError("download failed")

    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", _boom)
    rec = build_install_recommendation_truth(probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path))
    assert not _has_gguf(rec)  # fail-safe: never crashes, sizes for CPU


def test_rejected_gpu_probe_gets_cpu_bundle(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    monkeypatch.delenv("VOOL_SKIP_GPU_PROBE", raising=False)
    monkeypatch.setenv("VOOL_ENABLE_GPU_BUNDLE", "1")
    spy = mock.Mock(return_value=_orch(_cap(usable=False)))
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    rec = build_install_recommendation_truth(probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path))
    spy.assert_called_once()
    assert not _has_gguf(rec)  # measured "not usable" -> honest CPU sizing


def test_injected_gpu_capability_skips_probe(monkeypatch, tmp_path) -> None:
    _mock_disk(monkeypatch)
    spy = mock.Mock()
    monkeypatch.setattr("installer.llamacpp_capability_orchestrator.detect_and_verify_best_backend", spy)
    rec = build_install_recommendation_truth(
        probe=_gpu_probe("cuda", 24.0), runtime_home=str(tmp_path), gpu_capability=_cap(usable=True)
    )
    spy.assert_not_called()  # caller-provided verdict is used directly
    assert _has_gguf(rec)
