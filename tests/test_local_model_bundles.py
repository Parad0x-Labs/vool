from __future__ import annotations

from core.llamacpp_capability_probe import CapabilityProbeResult
from core.local_model_bundles import (
    SPEED_TIER_ROLES,
    bundle_spec,
    llamacpp_offload_layers,
    model_parameter_billions,
    resolve_local_bundle_recommendation,
)


def _fake_capability(*, usable: bool, gpu_tokens_per_second: float = 36.0, speedup_ratio: float = 18.0) -> CapabilityProbeResult:
    return CapabilityProbeResult(
        schema="vool.llamacpp_capability_probe.v1",
        probed_at_epoch=0.0,
        probe_version=1,
        gpu_name="GeForce GTX 1080",
        gpu_vendor="nvidia",
        backend_tested="vulkan",
        binary_release_tag="b9856",
        cpu_baseline_tokens_per_second=2.0,
        gpu_tokens_per_second=gpu_tokens_per_second,
        speedup_ratio=speedup_ratio,
        status="gpu_confirmed_fast" if usable else "gpu_rejected_slow",
        verdict_backend="vulkan" if usable else "cpu",
        detail="",
    )


def test_every_bucket_always_resolves_to_three_speed_tier_roles_without_gpu() -> None:
    # accelerator="cpu" zeroes effective VRAM (core.local_model_bundles._probe_effective_vram_gb),
    # so a CPU-only host is always bucket A regardless of RAM under the current (unchanged)
    # capacity_bucket_for_machine thresholds. To exercise buckets B-E here, give each probe a
    # non-legacy "cuda" accelerator with enough VRAM for that tier — this models a host with a
    # modern GPU Ollama itself already trusts, independent of the llama.cpp gpu_capability probe.
    probes = {
        "A": {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 0.0},
        "B": {"ram_gb": 20.0, "accelerator": "cuda", "vram_gb": 10.0},
        "C": {"ram_gb": 28.0, "accelerator": "cuda", "vram_gb": 16.0},
        "D": {"ram_gb": 40.0, "accelerator": "cuda", "vram_gb": 24.0},
        "E": {"ram_gb": 64.0, "accelerator": "cuda", "vram_gb": 24.0},
    }
    free_disk_by_bucket = {"A": 10.0, "B": 40.0, "C": 80.0, "D": 150.0, "E": 200.0}
    for expected_bucket, probe in probes.items():
        rec = resolve_local_bundle_recommendation(
            probe=probe,
            free_disk_gb=free_disk_by_bucket[expected_bucket],
            secondary_local_model_name="gemma3:4b",
        )
        assert rec.capacity_bucket == expected_bucket
        roles = tuple(item.role for item in rec.recommended_bundle.role_models)
        assert roles == SPEED_TIER_ROLES, f"bucket {expected_bucket} did not resolve to all 3 speed tiers: {roles}"
        assert not rec.gpu_capability_used
        for role_model in rec.recommended_bundle.role_models:
            assert role_model.backend == "ollama"
            assert not role_model.requires_gpu_backend


def test_bucket_a_no_longer_collapses_to_a_single_model() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe, free_disk_gb=355.5, secondary_local_model_name="gemma3:4b",
    )
    assert rec.capacity_bucket == "A"
    assert len(rec.recommended_bundle.role_models) == 3


def test_verified_gpu_capability_unlocks_llamacpp_accelerated_daily_and_deep_tiers() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.gpu_capability_used
    role_map = {item.role: item for item in rec.recommended_bundle.role_models}
    assert role_map["tiny_fast"].backend == "ollama"
    assert role_map["daily_accelerated"].backend == "llamacpp"
    assert role_map["daily_accelerated"].requires_gpu_backend
    assert role_map["daily_accelerated"].expected_tokens_per_second > 30.0
    assert role_map["deep_overnight"].backend == "llamacpp"
    assert role_map["deep_overnight"].requires_gpu_backend
    # fallback must be the CPU-only variant of the same bucket, for a safe degrade
    fallback_roles = {item.role: item for item in rec.fallback_bundle.role_models}
    assert fallback_roles["daily_accelerated"].backend == "ollama"


def test_gpu_capability_rejected_falls_back_to_cpu_bundle() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=False),
    )
    assert not rec.gpu_capability_used
    for role_model in rec.recommended_bundle.role_models:
        assert role_model.backend == "ollama"


def test_detected_nvidia_gpu_gets_vram_sized_ollama_bundle_without_llamacpp_probe() -> None:
    # The Ollama-on-GPU path (the "1080 only got a 4B" regression): a usable GTX 1080 (8GB VRAM,
    # modest 8GB RAM) with NO live llama.cpp probe is still VRAM-sized to bucket B and gets the
    # Ollama (no_gpu) bundle. Its daily model is qwen2.5:7b, not qwen3:8b: an 8B thinking model
    # partial-offloads on a 6-10GB GPU (~47s/turn, some failures) where the 7B fits and answers in
    # ~4s. gpu_capability_used stays False because this is Ollama CUDA offload, not llama.cpp/GGUF.
    probe = {"ram_gb": 8.0, "accelerator": "cuda", "vram_gb": 8.0, "accelerator_status": "usable"}
    rec = resolve_local_bundle_recommendation(
        probe=probe, free_disk_gb=300.0, secondary_local_model_name="gemma3:4b",
    )
    assert rec.capacity_bucket == "B"
    assert rec.recommended_bundle.bundle_id == "triple_bucket_b_no_gpu"
    assert not rec.gpu_capability_used
    daily = {item.role: item for item in rec.recommended_bundle.role_models}["daily_accelerated"]
    assert daily.model == "qwen2.5:7b"
    for role_model in rec.recommended_bundle.role_models:
        assert role_model.backend == "ollama"


def test_verified_gpu_below_6gb_vram_still_gets_cpu_bundle() -> None:
    # A live-verified but small (<6GB) GPU cannot hold the GPU bundle's daily model, so it
    # must get the RAM-sized CPU bundle, not a "-ngl 999 full offload" 7B it can't fit.
    probe = {"ram_gb": 16.0, "accelerator": "cuda", "vram_gb": 4.0, "accelerator_status": "usable"}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert not rec.gpu_capability_used
    assert rec.recommended_bundle.bundle_id.endswith("_no_gpu")
    for role_model in rec.recommended_bundle.role_models:
        assert role_model.backend == "ollama"


def test_mps_never_gets_cuda_gpu_accelerated_bundle() -> None:
    # Apple MPS runs the Ollama/Metal (no_gpu) bundle, never the llama.cpp-CUDA gpu variant,
    # even if a probe reports usable.
    probe = {"ram_gb": 24.0, "accelerator": "mps", "vram_gb": 24.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert not rec.gpu_capability_used
    assert rec.recommended_bundle.bundle_id.endswith("_no_gpu")


def test_verified_legacy_labelled_cuda_card_still_uses_gpu_bundle() -> None:
    # A card the hardware probe downgraded to "cpu" (legacy) but whose LIVE llama.cpp probe
    # measured a real speedup must still get the GPU bundle — the measured verdict wins.
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.gpu_capability_used
    assert rec.recommended_bundle.bundle_id.endswith("_gpu_accelerated")


def test_explicit_selected_model_still_wins_regardless_of_gpu_capability() -> None:
    probe = {"ram_gb": 7.9, "accelerator": "cpu", "vram_gb": 8.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=355.5,
        secondary_local_model_name="gemma3:4b",
        selected_model="qwen2.5:7b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.legacy_mode
    assert rec.recommended_bundle.primary_model == "qwen2.5:7b"


def test_llamacpp_offload_layers_full_offload_when_vram_comfortably_fits() -> None:
    assert llamacpp_offload_layers(model_name="qwen2.5:7b-instruct-q4_k_m", vram_gb=8.0) == 999


def test_llamacpp_offload_layers_partial_offload_when_model_exceeds_vram() -> None:
    layers = llamacpp_offload_layers(model_name="qwen2.5:14b-instruct-q4_k_m", vram_gb=8.0)
    assert 0 < layers < 999


def test_llamacpp_offload_layers_zero_when_no_vram() -> None:
    assert llamacpp_offload_layers(model_name="qwen2.5:7b-instruct-q4_k_m", vram_gb=0.0) == 0


def test_bucket_e_gpu_accelerated_bundle_uses_moe_deep_model() -> None:
    probe = {"ram_gb": 64.0, "accelerator": "cuda", "vram_gb": 24.0}
    rec = resolve_local_bundle_recommendation(
        probe=probe,
        free_disk_gb=300.0,
        secondary_local_model_name="gemma3:4b",
        gpu_capability=_fake_capability(usable=True),
    )
    assert rec.capacity_bucket == "E"
    role_map = {item.role: item for item in rec.recommended_bundle.role_models}
    assert role_map["daily_accelerated"].backend == "llamacpp"
    assert role_map["deep_overnight"].backend == "llamacpp"


def test_primary_model_prefers_daily_accelerated_over_tiny_fast_for_speed_tier_bundles() -> None:
    # Regression test: LocalBundleSpec.primary_model previously only recognized the older
    # general/coding/reasoning roles, so for the new tiny_fast/daily_accelerated/deep_overnight
    # bundles it fell through to "whatever role_models happens to list first" - which is
    # tiny_fast by definition order, silently making a 0.6B utility model the "primary" /
    # default runtime model for a fresh install instead of the intended daily workhorse.
    for bucket in "abcde":
        spec = bundle_spec(f"triple_bucket_{bucket}_no_gpu")
        role_map = spec.role_map
        assert spec.primary_model == role_map["daily_accelerated"], (
            f"triple_bucket_{bucket}_no_gpu.primary_model should be the daily_accelerated model, "
            f"not {spec.primary_model!r}"
        )


# --------------------------------------------------------------------------------------
# Finding, 2026-08-04: an unanchored `(\d+(?:\.\d+)?)b` search matches the FIRST digit-b run in
# a model id, which for an unregistered MoE model id like the OpenRouter free-catalog pick
# "nvidia/nemotron-3-ultra-550b-a55b:free" is the flagship TOTAL-parameter figure ("550b"), not
# the "-a55b" ACTIVE-parameter segment the id actually encodes. That 550.0 (vs the real 55.0)
# fed straight into `_TOOL_SELECTION_MIN_PARAMETER_B` / the tiny-lane VRAM-fit filter in
# core/local_inference_autopilot.py, excluding the manifest as a tool_intent candidate before any
# ranking or free-cloud check ever ran.
# --------------------------------------------------------------------------------------


def test_model_parameter_billions_prefers_active_param_segment_for_unregistered_moe_id() -> None:
    # Real id: this is the exact free-catalog pick used by the OpenRouter BYOK lane.
    assert model_parameter_billions("nvidia/nemotron-3-ultra-550b-a55b:free") == 55.0


def test_model_parameter_billions_still_returns_total_for_a_plain_dense_model_id() -> None:
    # No "-aXXb" active-parameter segment present -> falls through to the plain total-parameter
    # match unaffected, exactly as before this fix.
    assert model_parameter_billions("some-vendor/plain-70b-model") == 70.0


def test_model_parameter_billions_unaffected_for_registered_moe_models() -> None:
    # qwen3:30b-a3b carries an explicit MODEL_METADATA "parameter_count": "30B" (the TOTAL, not
    # active, count) -- the metadata lookup returns before the regex fallback is ever reached, so
    # this fix must not change its answer.
    assert model_parameter_billions("qwen3:30b-a3b") == 30.0
