from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.hardware_tier import MachineProbe, _select_accelerator, detect_gpu_devices, tier_summary
from core.install_recommendations import cpu_fallback_model
from core.local_model_bundles import model_parameter_billions
from core.memory_first_router import resolve_fallback_budget_seconds
from core.model_registry import ModelRegistry
from core.runtime_provider_defaults import _ensure_local_ollama_provider, _resolve_ollama_num_gpu

# --- B) hardware_tier nvidia-smi parse WITH new columns + back-compat WITHOUT them ---------------


def _smi_run(stdout: str):
    def fake_run(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=stdout, stderr="")

    return fake_run


def test_nvidia_smi_parses_new_free_vram_and_driver_columns(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())
    # index, name, memory.total(MiB), memory.free(MiB), driver_version, compute_cap
    stdout = "0, NVIDIA GeForce GTX 1080, 8192, 6041, 566.36, 6.1\n"
    monkeypatch.setattr("core.hardware_tier.subprocess.run", _smi_run(stdout))
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()

    assert len(devices) == 1
    device = devices[0]
    assert device.name == "NVIDIA GeForce GTX 1080"
    assert device.vram_gb == 8192.0 / 1024.0
    assert device.driver_version == "566.36"
    assert device.vram_free_gb is not None
    assert abs(device.vram_free_gb - 6041.0 / 1024.0) < 1e-6
    # New fields surface through to_dict so they flow into tier_summary rows / JSON.
    row = device.to_dict()
    assert row["driver_version"] == "566.36"
    assert row["vram_free_gb"] == round(6041.0 / 1024.0, 1)


def test_nvidia_smi_backcompat_three_column_rows_still_parse(monkeypatch) -> None:
    # An older driver omits the trailing columns; the len(parts) guards must keep parsing
    # (this is the exact 3-column shape the multi-GPU inventory test feeds).
    monkeypatch.delenv("VOOL_ALLOW_LEGACY_CUDA", raising=False)
    monkeypatch.setattr("core.hardware_tier._try_torch_cuda_devices", lambda: ())
    stdout = "0, NVIDIA GeForce GTX 1080, 8192\n1, NVIDIA GeForce RTX 4090, 24576\n"
    monkeypatch.setattr("core.hardware_tier.subprocess.run", _smi_run(stdout))
    with mock.patch("core.hardware_tier.platform.system", return_value="Windows"):
        devices = detect_gpu_devices()

    assert [d.name for d in devices] == ["NVIDIA GeForce GTX 1080", "NVIDIA GeForce RTX 4090"]
    # Missing columns degrade to neutral defaults rather than raising.
    assert devices[0].driver_version == ""
    assert devices[0].vram_free_gb is None
    assert devices[0].vram_gb == 8.0


def test_probe_level_driver_and_free_vram_surface_in_tier_summary() -> None:
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=16.0,
        gpu_name="NVIDIA GeForce GTX 1080",
        vram_gb=8.0,
        accelerator="cuda",
        accelerator_status="usable",
        driver_version="566.36",
        vram_free_gb=5.9,
    )
    summary = tier_summary(probe)
    assert summary["driver_version"] == "566.36"
    assert summary["vram_free_gb"] == 5.9


def test_select_accelerator_return_shape_unchanged() -> None:
    # The new fields must not change the 5-tuple contract callers unpack.
    result = _select_accelerator(())
    assert result == (None, None, "cpu", "cpu", "")
    assert len(result) == 5


# --- C) VOOL_OLLAMA_NUM_GPU threads into manifest options (chat + prewarm) ----------------------


def test_resolve_ollama_num_gpu_env_semantics() -> None:
    assert _resolve_ollama_num_gpu({"VOOL_OLLAMA_NUM_GPU": "0"}) == 0  # 0 must survive (pure CPU)
    assert _resolve_ollama_num_gpu({"VOOL_OLLAMA_NUM_GPU": "12"}) == 12
    assert _resolve_ollama_num_gpu({"VOOL_OLLAMA_NUM_GPU": "-3"}) == 0  # negatives clamp to 0
    assert _resolve_ollama_num_gpu({}) is None  # unset -> Ollama decides layers (current behavior)
    assert _resolve_ollama_num_gpu({"VOOL_OLLAMA_NUM_GPU": "abc"}) is None


def test_manifest_carries_num_gpu_zero_in_chat_and_prewarm_when_forced() -> None:
    registry = ModelRegistry()
    changed = _ensure_local_ollama_provider(
        registry, model_tag="qwen2.5:7b", bundle_role="general", env={"VOOL_OLLAMA_NUM_GPU": "0"}
    )
    assert changed is True
    manifest = registry.get_manifest("ollama-local", "qwen2.5:7b")
    assert manifest is not None
    # Live chat path reads top-level runtime_config.num_gpu.
    assert manifest.runtime_config["num_gpu"] == 0
    # Prewarm path reads prewarm.options.num_gpu (copied verbatim by the adapter).
    assert manifest.runtime_config["prewarm"]["options"]["num_gpu"] == 0


def test_manifest_omits_num_gpu_when_env_unset() -> None:
    registry = ModelRegistry()
    _ensure_local_ollama_provider(registry, model_tag="qwen2.5:7b", bundle_role="general", env={})
    manifest = registry.get_manifest("ollama-local", "qwen2.5:7b")
    assert manifest is not None
    assert "num_gpu" not in manifest.runtime_config
    assert "num_gpu" not in manifest.runtime_config["prewarm"]["options"]


def _ollama_adapter(num_gpu: object) -> OpenAICompatibleAdapter:
    runtime_config = {
        "base_url": "http://127.0.0.1:11434",
        "think": False,
        "context_window": 4096,
        "prewarm": {
            "strategy": "ollama_chat",
            "keep_alive": "15m",
            "message": " ",
            "options": {"num_ctx": 4096, "num_predict": 1},
        },
    }
    if num_gpu is not None:
        runtime_config["num_gpu"] = num_gpu
        runtime_config["prewarm"]["options"]["num_gpu"] = num_gpu
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            metadata={"runtime_family": "ollama"},
            runtime_config=runtime_config,
        )
    )


def _request() -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt="hi", messages=[{"role": "user", "content": "hi"}])


def test_build_ollama_payload_carries_num_gpu_zero() -> None:
    adapter = _ollama_adapter(num_gpu=0)
    payload = adapter._build_ollama_payload(_request(), force_json=False, stream=False)
    assert payload["options"]["num_gpu"] == 0  # 0 is falsy — must not be dropped


def test_build_ollama_payload_omits_num_gpu_when_absent() -> None:
    adapter = _ollama_adapter(num_gpu=None)
    payload = adapter._build_ollama_payload(_request(), force_json=False, stream=False)
    assert "num_gpu" not in payload["options"]


def test_prewarm_request_carries_num_gpu_zero() -> None:
    adapter = _ollama_adapter(num_gpu=0)
    prewarm_config = dict(adapter.manifest.runtime_config["prewarm"])
    _url, payload = adapter._ollama_prewarm_request(
        base_url="http://127.0.0.1:11434", strategy="ollama_chat", prewarm_config=prewarm_config
    )
    assert payload["options"]["num_gpu"] == 0


# --- D) CPU-aware provider-fallback budget selection --------------------------------------------


def test_cpu_only_run_uses_larger_budget() -> None:
    # Drives the REAL router decision function, not a mirror of it.
    assert resolve_fallback_budget_seconds("daily", forced_cpu=True, no_usable_gpu=False) == 180.0
    # No usable GPU also counts as CPU-only.
    assert resolve_fallback_budget_seconds("tiny", forced_cpu=False, no_usable_gpu=True) == 180.0


def test_gpu_run_uses_default_sixty_second_budget() -> None:
    assert resolve_fallback_budget_seconds("daily", forced_cpu=False, no_usable_gpu=False) == 60.0


def test_tool_intent_gets_the_larger_budget_even_on_a_fast_gpu_lane() -> None:
    """A tool_intent call always carries the full tool catalog with tool_choice required (64
    tools, ~32KB schema, ~9,700 prompt tokens measured live) -- a heavier prompt than the 60s
    ordinary-chat budget was sized for, regardless of lane speed or GPU availability. Measured
    live 2026-08-04: warm model, reasoning already disabled, still 72.1s wall clock (70.8s of it
    prompt processing) on the "tiny" lane -- the 60s default cut it off with zero tool calls ever
    executed, three drives in a row. Keyed on output_mode, not lane, so ordinary plain_text chat
    on the same lane is untouched.
    """
    assert (
        resolve_fallback_budget_seconds(
            "tiny", forced_cpu=False, no_usable_gpu=False, output_mode="tool_intent"
        )
        == 180.0
    )
    # Ordinary chat on the very same lane keeps the fast, fail-quick default.
    assert (
        resolve_fallback_budget_seconds(
            "tiny", forced_cpu=False, no_usable_gpu=False, output_mode="plain_text"
        )
        == 60.0
    )
    # CPU-only still wins regardless of output_mode -- a slow CPU tool_intent call needs no less
    # room than a slow CPU plain_text call.
    assert (
        resolve_fallback_budget_seconds(
            "tiny", forced_cpu=True, no_usable_gpu=False, output_mode="tool_intent"
        )
        == 180.0
    )


def test_deep_lane_stays_unbounded_none() -> None:
    assert resolve_fallback_budget_seconds("deep", forced_cpu=True, no_usable_gpu=True) is None
    assert resolve_fallback_budget_seconds("cloud", forced_cpu=False, no_usable_gpu=False) is None
    assert resolve_fallback_budget_seconds("human", forced_cpu=False, no_usable_gpu=False) is None


def test_no_usable_gpu_probe_scopes_to_cuda_and_mps(monkeypatch) -> None:
    # The router's cached CPU-only probe: only cuda/mps are GPU lanes Ollama offloads to, so
    # cpu/directml/vulkan/absent all read as "no usable GPU" and earn the larger CPU budget.
    import core.memory_first_router as mfr

    def _probe_returning(accelerator: str):
        def _fake() -> MachineProbe:
            return MachineProbe(
                cpu_cores=8, ram_gb=16.0, gpu_name=None, vram_gb=None, accelerator=accelerator
            )

        return _fake

    cases = {"cuda": False, "mps": False, "cpu": True, "": True, "directml": True, "vulkan": True}
    try:
        for accelerator, expected in cases.items():
            mfr._NO_USABLE_GPU_CACHE = None  # reset the once-per-process cache
            monkeypatch.setattr("core.hardware_tier.probe_machine", _probe_returning(accelerator))
            assert mfr._machine_has_no_usable_gpu() is expected
    finally:
        mfr._NO_USABLE_GPU_CACHE = None  # leave the cache clean for other tests


# --- E) cpu_fallback_model helper ----------------------------------------------------------------


def _probe(*, ram_gb: float, accelerator: str = "cpu", vram_gb: float | None = None) -> MachineProbe:
    return MachineProbe(
        cpu_cores=8, ram_gb=ram_gb, gpu_name=None, vram_gb=vram_gb, accelerator=accelerator
    )


def test_cpu_fallback_prefers_qwen25_3b_on_capable_box(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("VOOL_FORCE_OLLAMA_MODEL", raising=False)
    # Plenty of RAM: the hardware pick would be large, but the CPU fallback caps at qwen2.5:3b.
    tag = cpu_fallback_model(_probe(ram_gb=64.0))
    assert tag == "qwen2.5:3b"


def test_cpu_fallback_never_larger_than_hardware_pick(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("VOOL_FORCE_OLLAMA_MODEL", raising=False)
    # A tiny box that can only carry the nano tier must get the smaller tag, not qwen2.5:3b.
    tag = cpu_fallback_model(_probe(ram_gb=2.0))
    assert model_parameter_billions(tag) <= model_parameter_billions("qwen2.5:3b")


def test_cpu_fallback_is_fail_safe_on_probe_error(monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr("core.install_recommendations.select_qwen_tier", _boom)
    # Any error falls back to the preferred small tag rather than raising.
    assert cpu_fallback_model(_probe(ram_gb=16.0)) == "qwen2.5:3b"
