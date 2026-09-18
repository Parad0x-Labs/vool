from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.hardware_tier import MachineProbe

# Speed-tier roles describe throughput character (orthogonal to the older
# general/coding/reasoning/heavy_reasoning/lightweight_utility roles, which describe
# content specialization). Every capacity bucket A-E resolves to exactly these three
# roles so no hardware tier — including the weakest — collapses to a single model.
SPEED_TIER_ROLES: tuple[str, ...] = ("tiny_fast", "daily_accelerated", "deep_overnight")


@dataclass(frozen=True)
class BundleRoleModel:
    role: str
    model: str
    backend: str = "ollama"  # "ollama" | "llamacpp"
    expected_tokens_per_second: float = 0.0  # 0.0 = unmeasured; do not print a number
    requires_gpu_backend: bool = False  # only include this role if a live-verified GPU backend exists
    offload_note: str = ""  # e.g. "Partial GPU offload; expect ~10-14 tok/s, fine for overnight batch use."

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model": self.model,
            "backend": self.backend,
            "expected_tokens_per_second": self.expected_tokens_per_second,
            "requires_gpu_backend": self.requires_gpu_backend,
            "offload_note": self.offload_note,
        }


@dataclass(frozen=True)
class LocalBundleSpec:
    bundle_id: str
    kind: str
    display_name: str
    role_models: tuple[BundleRoleModel, ...]
    summary: str
    gpu_conditional: bool = False  # True => this spec assumes a live-verified llama.cpp GPU backend

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(item.model for item in self.role_models)

    @property
    def role_map(self) -> dict[str, str]:
        return {item.role: item.model for item in self.role_models}

    @property
    def primary_model(self) -> str:
        role_map = self.role_map
        return (
            role_map.get("general")
            or role_map.get("daily_accelerated")
            or role_map.get("coding")
            or role_map.get("reasoning")
            or next(iter(role_map.values()), "qwen3:8b")
        )


@dataclass(frozen=True)
class LocalBundleRecommendation:
    capacity_bucket: str
    local_multi_llm_fit: str
    free_disk_gb: float
    recommended_bundle: LocalBundleSpec
    fallback_bundle: LocalBundleSpec
    safe_disk_floor_gb: float
    advanced_optional_allowed: bool
    advanced_optional_profile: str
    selection_reasons: tuple[str, ...]
    legacy_mode: bool = False
    gpu_capability_used: bool = False


MODEL_STORAGE_GB: dict[str, float] = {
    "qwen3:0.6b": 0.7,
    "qwen3:4b": 2.5,
    "qwen3:8b": 5.2,
    "qwen3:14b": 9.3,
    "qwen3:30b": 19.0,
    "qwen3:30b-a3b": 19.0,
    "vool-qwen3-30b-a3b:nothink": 19.0,
    "qwen3.5:35b-a3b": 23.0,
    "deepseek-r1:8b": 5.2,
    "deepseek-r1:14b": 9.0,
    "deepseek-r1:32b": 20.0,
    "gemma3:4b": 3.3,
    "gemma3:12b": 8.1,
    "gemma3:12b-qat": 8.1,
    "mistral-small:24b": 14.0,
    "qwen2.5:0.5b": 1.0,
    "qwen2.5:3b": 3.5,
    "qwen2.5:7b": 8.0,
    "qwen2.5:14b": 16.0,
    "qwen2.5:14b-gguf": 18.0,
    "qwen2.5:32b": 36.0,
    "qwen2.5:72b": 80.0,
    "nomic-embed-text": 0.3,
    "nomic-embed-text:latest": 0.3,
    # llama.cpp-served GGUF quants for the GPU-accelerated daily/deep tiers. These are
    # the exact model class measured at ~35-36 tok/s via full GPU offload on a legacy
    # GTX 1080 test host (see core/llamacpp_capability_probe.py for the live check that
    # gates when these are actually offered, instead of trusting a GPU name heuristic).
    "qwen2.5:7b-instruct-q4_k_m": 4.7,
    "qwen2.5:14b-instruct-q4_k_m": 9.0,
    "qwen2.5:32b-instruct-q4_k_m": 20.0,
    "deepseek-r1:14b-qwen-distill-q4_k_m": 9.5,
    "qwen3:30b-a3b-q4_k_m": 18.5,
}

# GGUF source registry for llama.cpp-served models, keyed by the same logical model
# name used in BundleRoleModel.model so lookups stay uniform with MODEL_STORAGE_GB.
# llama.cpp doesn't consume Ollama's name:tag registry — it needs a HF repo + filename.
GGUF_MODEL_SOURCES: dict[str, dict[str, str]] = {
    "qwen2.5:7b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
    },
    "qwen2.5:14b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-14B-Instruct-GGUF",
        "filename": "qwen2.5-14b-instruct-q4_k_m.gguf",
    },
    "qwen2.5:32b-instruct-q4_k_m": {
        "repo": "Qwen/Qwen2.5-32B-Instruct-GGUF",
        "filename": "qwen2.5-32b-instruct-q4_k_m.gguf",
    },
    "deepseek-r1:14b-qwen-distill-q4_k_m": {
        "repo": "unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf",
    },
    "qwen3:30b-a3b-q4_k_m": {
        "repo": "Qwen/Qwen3-30B-A3B-GGUF",
        "filename": "Qwen3-30B-A3B-Q4_K_M.gguf",
    },
}

MODEL_METADATA: dict[str, dict[str, Any]] = {
    "qwen3:0.6b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "0.6B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:4b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "4B",
        "bundle_role": "lightweight_utility",
        "eagle3_draft_eligible": False,
    },
    "qwen3:8b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "8B",
        "bundle_role": "general",
        "eagle3_draft_eligible": True,
    },
    "qwen3:14b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "14B",
        "bundle_role": "coding",
        "eagle3_draft_eligible": True,
    },
    "qwen3:30b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
    },
    "qwen3:30b-a3b": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
    },
    "vool-qwen3-30b-a3b:nothink": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "user-managed local Ollama model",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "general",
        "eagle3_draft_eligible": False,
        "thinking_disabled": True,
        "tokens_per_second": 37.2,
        "quantization": "local-measured",
        "notes": "Measured fast local no-think default when installed.",
    },
    "qwen3.5:35b-a3b": {
        "family": "qwen3.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen3.5",
        "parameter_count": "35B",
        "active_parameter_count": "3.3B",
        "architecture": "hybrid_moe",
        "bundle_role": "heavy_reasoning",
        "eagle3_draft_eligible": False,
        "constant_vram": True,
        "max_context": 262144,
    },
    "deepseek-r1:8b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "8B",
        # Honest labeling: these Ollama `deepseek-r1:*` tags are DISTILLS, not the 671B flagship.
        "display_name": "DeepSeek-R1-Distill-Llama-8B",
        "distilled": True,
        "base_model": "DeepSeek-R1",
        "honest_note": "8B reasoning distill of DeepSeek-R1 (Llama-8B base) — not the 671B flagship.",
    },
    "deepseek-r1:14b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "14B",
        "display_name": "DeepSeek-R1-Distill-Qwen-14B",
        "distilled": True,
        "base_model": "DeepSeek-R1",
        "honest_note": "14B reasoning distill of DeepSeek-R1 (Qwen-14B base) — not the 671B flagship.",
    },
    "deepseek-r1:32b": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://ollama.com/library/deepseek-r1",
        "parameter_count": "32B",
        "display_name": "DeepSeek-R1-Distill-Qwen-32B",
        "distilled": True,
        "base_model": "DeepSeek-R1",
        "honest_note": "32B reasoning distill of DeepSeek-R1 (Qwen-32B base) — not the 671B flagship.",
    },
    "gemma3:4b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "4B",
    },
    "gemma3:12b": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
    },
    "gemma3:12b-qat": {
        "family": "gemma3",
        "license_name": "Gemma Terms",
        "license_reference": "https://ollama.com/library/gemma3",
        "parameter_count": "12B",
        "bundle_role": "general",
        "qat": True,
        "eagle3_draft_eligible": False,
    },
    "mistral-small:24b": {
        "family": "mistral-small",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/mistral-small",
        "parameter_count": "24B",
    },
    "qwen2.5:0.5b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "0.5B",
    },
    "qwen2.5:3b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "3B",
    },
    "qwen2.5:7b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "7B",
    },
    "qwen2.5:14b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "14B",
    },
    "qwen2.5:32b": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://ollama.com/library/qwen2.5",
        "parameter_count": "32B",
    },
    "qwen2.5:7b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF",
        "parameter_count": "7B",
        "bundle_role": "daily_accelerated",
        "eagle3_draft_eligible": False,
    },
    "qwen2.5:14b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-14B-Instruct-GGUF",
        "parameter_count": "14B",
        "bundle_role": "daily_accelerated",
    },
    "qwen2.5:32b-instruct-q4_k_m": {
        "family": "qwen2.5",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen2.5-32B-Instruct-GGUF",
        "parameter_count": "32B",
        "bundle_role": "deep_overnight",
    },
    "deepseek-r1:14b-qwen-distill-q4_k_m": {
        "family": "deepseek-r1",
        "license_name": "MIT",
        "license_reference": "https://huggingface.co/unsloth/DeepSeek-R1-Distill-Qwen-14B-GGUF",
        "parameter_count": "14B",
        "bundle_role": "deep_overnight",
        "display_name": "DeepSeek-R1-Distill-Qwen-14B (Q4_K_M)",
        "distilled": True,
        "base_model": "DeepSeek-R1",
        "honest_note": "14B reasoning distill of DeepSeek-R1 (Qwen-14B base), Q4_K_M — not the 671B flagship.",
    },
    "qwen3:30b-a3b-q4_k_m": {
        "family": "qwen3",
        "license_name": "Apache-2.0",
        "license_reference": "https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF",
        "parameter_count": "30B",
        "active_parameter_count": "3B",
        "architecture": "moe",
        "bundle_role": "deep_overnight",
    },
}


LOCAL_BUNDLE_SPECS: dict[str, LocalBundleSpec] = {
    "single_gemma3_4b": LocalBundleSpec(
        bundle_id="single_gemma3_4b",
        kind="single",
        display_name="Single S2 lightweight fallback",
        role_models=(BundleRoleModel("general", "gemma3:4b"),),
        summary="Single lightweight fallback for constrained hosts.",
    ),
    "single_qwen3_4b": LocalBundleSpec(
        bundle_id="single_qwen3_4b",
        kind="single",
        display_name="Single lightweight Qwen fallback",
        role_models=(BundleRoleModel("general", "qwen3:4b"),),
        summary="Single lightweight Qwen fallback when Gemma is not preferred.",
    ),
    "single_qwen3_8b": LocalBundleSpec(
        bundle_id="single_qwen3_8b",
        kind="single",
        display_name="Single S1 safest default",
        role_models=(BundleRoleModel("general", "qwen3:8b"),),
        summary="Single best-overall local model for constrained-but-capable hosts.",
    ),
    "dual_qwen3_8b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D1 balanced default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Balanced local pair for general companion work plus deeper reasoning and review.",
    ),
    "dual_qwen3_8b_gemma3_4b": LocalBundleSpec(
        bundle_id="dual_qwen3_8b_gemma3_4b",
        kind="dual",
        display_name="Dual D3 lighter fallback",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("lightweight_utility", "gemma3:4b"),
        ),
        summary="Lighter local pair with a cheap utility backup lane.",
    ),
    "dual_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="dual_mistral_small_24b_deepseek_r1_8b",
        kind="dual",
        display_name="Dual D2 coding and reasoning",
        role_models=(
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Stronger local dual for code/tool tasks plus second-pass reasoning.",
    ),
    "triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b": LocalBundleSpec(
        bundle_id="triple_qwen3_8b_mistral_small_24b_deepseek_r1_8b",
        kind="triple",
        display_name="Triple T1 practical default",
        role_models=(
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:8b"),
        ),
        summary="Practical triple with explicit general, coding, and reasoning lanes.",
    ),
    "triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b": LocalBundleSpec(
        bundle_id="triple_qwen3_14b_mistral_small_24b_deepseek_r1_14b",
        kind="triple",
        display_name="Triple T3 enthusiast",
        role_models=(
            BundleRoleModel("general", "qwen3:14b"),
            BundleRoleModel("coding", "mistral-small:24b"),
            BundleRoleModel("reasoning", "deepseek-r1:14b"),
        ),
        summary="High-end triple for strong hosts with clear role separation.",
    ),
    "goblin_stack": LocalBundleSpec(
        bundle_id="goblin_stack",
        kind="triple",
        display_name="Goblin local stack",
        role_models=(
            BundleRoleModel("lightweight_utility", "qwen3:0.6b"),
            BundleRoleModel("general", "qwen3:8b"),
            BundleRoleModel("heavy_reasoning", "qwen3.5:35b-a3b"),
        ),
        summary="Qwen3-family local stack with tiny routing, daily workhorse, and hybrid-MoE deep lane.",
    ),

    # ------------------------------------------------------------------
    # Always-3-tier bucket bundles (tiny_fast / daily_accelerated / deep_overnight).
    # Every bucket A-E gets both a no-GPU (CPU-only, always safe) variant and a
    # gpu_conditional variant that is only ever selected once
    # core.llamacpp_capability_probe.probe_llamacpp_capability() has *measured* real
    # speedup on this exact host — never based on a GPU name heuristic alone. A weak
    # host (bucket A) with a live-verified GPU can genuinely beat a stronger host
    # (bucket C/D) that has no working GPU backend on the daily lane. deep_overnight
    # is honestly allowed to be slow in every row; it exists for tasks queued and
    # left running, not live chat.
    # ------------------------------------------------------------------

    "triple_bucket_a_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_a_no_gpu",
        kind="triple",
        display_name="Bucket A - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=15.0),
            BundleRoleModel("daily_accelerated", "qwen3:4b", expected_tokens_per_second=5.0),
            BundleRoleModel(
                "deep_overnight", "gemma3:4b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Weakest-hardware tier still gets a fast/daily/deep spread, sized for RAM-only inference.",
    ),
    "triple_bucket_a_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_a_gpu_accelerated",
        kind="triple",
        display_name="Bucket A - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=15.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:7b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=35.5, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "deepseek-r1:14b-qwen-distill-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=12.0, requires_gpu_backend=True,
                offload_note="Partial GPU offload; larger reasoning model queued for overnight/batch use.",
            ),
        ),
        gpu_conditional=True,
        summary="Same weak-RAM host, but a live-verified llama.cpp GPU backend unlocks a materially faster daily lane and a real overnight deep lane.",
    ),
    "triple_bucket_b_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_b_no_gpu",
        kind="triple",
        display_name="Bucket B - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=17.0),
            # qwen2.5:7b, not qwen3:8b: an 8B thinking model partial-offloads on a 6-10GB GPU
            # (~47s/turn, some failures) where the 7B fits with headroom and answers in ~4s.
            BundleRoleModel("daily_accelerated", "qwen2.5:7b", expected_tokens_per_second=7.0),
            BundleRoleModel(
                "deep_overnight", "deepseek-r1:14b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket B without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_b_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_b_gpu_accelerated",
        kind="triple",
        display_name="Bucket B - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=17.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:7b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=33.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=17.0, requires_gpu_backend=True,
                offload_note="Near-full GPU offload; comfortably faster deep lane than CPU-only.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket B with a live-verified GPU backend: fast daily lane plus a genuinely usable deep lane.",
    ),
    "triple_bucket_c_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_c_no_gpu",
        kind="triple",
        display_name="Bucket C - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=21.0),
            BundleRoleModel("daily_accelerated", "qwen3:8b", expected_tokens_per_second=8.0),
            BundleRoleModel(
                "deep_overnight", "qwen3:14b", expected_tokens_per_second=5.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket C without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_c_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_c_gpu_accelerated",
        kind="triple",
        display_name="Bucket C - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=21.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=25.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=10.0, requires_gpu_backend=True,
                offload_note="Partial GPU offload; a 32B model won't fully fit under 16GB VRAM, still faster than CPU.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket C with a live-verified GPU backend: a stronger daily lane plus a genuinely usable heavy deep lane.",
    ),
    "triple_bucket_d_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_d_no_gpu",
        kind="triple",
        display_name="Bucket D - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=23.0),
            BundleRoleModel("daily_accelerated", "qwen3:14b", expected_tokens_per_second=7.5),
            BundleRoleModel(
                "deep_overnight", "mistral-small:24b", expected_tokens_per_second=4.0,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket D without a working GPU backend: CPU-sized fast/daily/deep spread.",
    ),
    "triple_bucket_d_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_d_gpu_accelerated",
        kind="triple",
        display_name="Bucket D - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=23.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=29.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen3:30b-a3b-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=21.0, requires_gpu_backend=True,
                offload_note="MoE model with ~3B active params; stays fast even as the deep lane via CPU-expert-offload.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket D with a live-verified GPU backend: strong daily lane plus a fast MoE-based deep lane.",
    ),
    "triple_bucket_e_no_gpu": LocalBundleSpec(
        bundle_id="triple_bucket_e_no_gpu",
        kind="triple",
        display_name="Bucket E - tiny/daily/deep (CPU-only)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=25.0),
            BundleRoleModel("daily_accelerated", "qwen3:14b", expected_tokens_per_second=10.0),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b", expected_tokens_per_second=5.5,
                offload_note="CPU-only; queue this for overnight/batch tasks, not live chat.",
            ),
        ),
        summary="Bucket E without a working GPU backend: a strong CPU host still gets a full fast/daily/deep spread.",
    ),
    "triple_bucket_e_gpu_accelerated": LocalBundleSpec(
        bundle_id="triple_bucket_e_gpu_accelerated",
        kind="triple",
        display_name="Bucket E - tiny/daily/deep (llama.cpp GPU-accelerated)",
        role_models=(
            BundleRoleModel("tiny_fast", "qwen3:0.6b", expected_tokens_per_second=25.0),
            BundleRoleModel(
                "daily_accelerated", "qwen2.5:14b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=35.0, requires_gpu_backend=True,
                offload_note="Full GPU offload (-ngl 999) via a live-verified llama.cpp backend.",
            ),
            BundleRoleModel(
                "deep_overnight", "qwen2.5:32b-instruct-q4_k_m", backend="llamacpp",
                expected_tokens_per_second=17.0, requires_gpu_backend=True,
                offload_note="Full GPU offload; comfortably fits under >=24GB VRAM.",
            ),
        ),
        gpu_conditional=True,
        summary="Bucket E with a live-verified GPU backend: the fastest daily lane and a genuinely fast deep lane too.",
    ),
}


def model_storage_gb(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    if clean in MODEL_STORAGE_GB:
        return float(MODEL_STORAGE_GB[clean])

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return round(max(1.0, float(match.group(1)) * 0.75), 1)
    return 8.0


def model_total_parameter_billions(model_name: str) -> float:
    """The model's TOTAL parameter count in billions -- the residency/eligibility number.

    Distinct from `model_parameter_billions`, which prefers the MoE ACTIVE count because it
    answers an inference-cost question. Residency and heavy-lane eligibility are about what must
    be LOADED, which is the total: "gemma-4-26b-a4b" is a 26B model with 4B active. Measured
    2026-08-19 (Fable E2): the autopilot's heavy flag read the total (26B) from the name while
    the router's heavy admission read the active count (4B) through `model_parameter_billions`,
    so a pinned MoE model was flagged heavy and then rejected as non-heavy -- refused before any
    adapter was invoked. One question ("what must we fit") now has one answer, here.
    """
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass
    # An MoE product name: "8x22b" is eight 22B experts, not a 22B model.
    moe = re.search(r"(?<![\d.])(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*b\b", clean)
    if moe:
        return float(moe.group(1)) * float(moe.group(2))
    counts = [
        float(match.group(1))
        for match in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", clean)
    ]
    if counts:
        return max(counts)
    return 8.0


def model_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    # An "-aXXb" segment (e.g. "nemotron-3-ultra-550b-a55b") encodes a MoE model's ACTIVE
    # parameter count, which is what actually governs inference cost/capability for an
    # unregistered model id -- prefer it over an earlier, unrelated total-parameter figure
    # ("550b") that an unanchored search would otherwise match first. Registered models never
    # reach this branch (the metadata lookup above returns first), so this only affects model
    # ids with no MODEL_METADATA entry, like a freshly-picked OpenRouter free-catalog model.
    active_match = re.search(r"-a(\d+(?:\.\d+)?)b", clean)
    if active_match:
        return float(active_match.group(1))

    match = re.search(r"(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return 8.0


def model_active_parameter_billions(model_name: str) -> float:
    clean = str(model_name or "").strip().lower()
    metadata = model_metadata(clean)
    raw_count = str(metadata.get("active_parameter_count") or "").strip().lower().rstrip("b")
    if raw_count:
        try:
            return float(raw_count)
        except ValueError:
            pass

    match = re.search(r"-a(\d+(?:\.\d+)?)b", clean)
    if match:
        return float(match.group(1))
    return model_parameter_billions(clean)


def model_metadata(model_name: str) -> dict[str, Any]:
    clean = str(model_name or "").strip().lower()
    return dict(MODEL_METADATA.get(clean) or {})


def model_is_distill(model_name: str) -> bool:
    """True if this model tag is a distilled model, not the flagship it is named after."""
    return bool(MODEL_METADATA.get(str(model_name or "").strip().lower(), {}).get("distilled"))


def honest_model_label(model_name: str) -> str:
    """Return the honest human label for a model tag.

    For distills (e.g. Ollama's `deepseek-r1:14b`, which is a 14B distill of DeepSeek-R1,
    NOT the 671B flagship) this returns the accurate distill name so nothing surfaces to a
    user as the flagship. Falls back to the raw tag when no honest display name is defined.
    """
    clean = str(model_name or "").strip()
    meta = MODEL_METADATA.get(clean.lower(), {})
    display = str(meta.get("display_name") or "").strip()
    return display or clean


def installed_ollama_role_for_model(*, model_name: str, primary_model: str = "") -> str:
    clean = str(model_name or "").strip().lower()
    primary = str(primary_model or "").strip().lower()
    if primary and clean == primary:
        return "general"
    if "mistral-small" in clean or "coder" in clean or "code" in clean:
        return "coding"
    if "deepseek-r1" in clean or "reason" in clean:
        return "reasoning"
    parameter_b = model_parameter_billions(clean)
    if parameter_b <= 3.0:
        return "lightweight_utility"
    if parameter_b >= 24.0:
        return "heavy_reasoning"
    if parameter_b >= 13.0:
        return "reasoning"
    return "general"


def safe_disk_floor_gb(models: tuple[str, ...] | list[str]) -> float:
    total_size = sum(model_storage_gb(item) for item in models if str(item).strip())
    return round(max(total_size * 2.0, total_size + 25.0), 1)


def llamacpp_offload_layers(*, model_name: str, vram_gb: float, layer_count_hint: int = 0) -> int:
    """Returns the -ngl value for llama.cpp given a model and available VRAM.

    999 means "offload everything, it fits." A smaller value means partial offload:
    that many layers go to GPU, the rest stay in CPU RAM and llama.cpp handles the
    split. This is what lets deep_overnight stay honestly labeled — a model too big
    for VRAM still runs, just slower, instead of either refusing to load or silently
    claiming full-GPU speed it can't deliver.
    """
    if vram_gb <= 0:
        return 0
    model_gb = model_storage_gb(model_name)
    if model_gb <= 0:
        return 999
    if vram_gb >= model_gb * 1.15:  # comfortable headroom left over for KV cache
        return 999
    total_layers = layer_count_hint or _estimate_layer_count(model_name)
    usable_vram = max(0.5, vram_gb - 1.5)  # reserve ~1.5GB for KV cache / daily-lane coexistence
    fraction = min(1.0, usable_vram / model_gb)
    return max(1, int(total_layers * fraction))


def _estimate_layer_count(model_name: str) -> int:
    # Rough heuristic: dense transformer layer counts scale close to linearly with
    # parameter count in this size range (roughly 1.4-1.7 layers per billion params
    # for the Qwen/DeepSeek/Mistral families used here). Good enough for sizing a
    # partial-offload split; llama.cpp's own metadata is authoritative at load time.
    params_b = model_parameter_billions(model_name)
    return max(1, round(params_b * 1.6))


def bundle_spec(bundle_id: str) -> LocalBundleSpec:
    return LOCAL_BUNDLE_SPECS[bundle_id]


def local_multi_llm_fit_from_probe(probe: MachineProbe | Mapping[str, Any]) -> str:
    ram_gb = _probe_ram_gb(probe)
    accelerator = _probe_accelerator(probe)
    vram_gb = _probe_effective_vram_gb(probe)
    if accelerator == "mps":
        if ram_gb >= 48.0:
            return "comfortable"
        if ram_gb >= 24.0:
            return "pressure_sensitive"
        return "single_model_only"
    if vram_gb >= 20.0 or ram_gb >= 48.0:
        return "comfortable"
    if vram_gb >= 10.0 or ram_gb >= 24.0:
        return "pressure_sensitive"
    return "single_model_only"


# Bucket thresholds: (memory_gb_ceiling, bucket) — the largest tier a given amount of
# memory can actually run. GPU thresholds size by VRAM (the model lives in VRAM under
# full offload). RAM thresholds size by system RAM for CPU inference, which needs roughly
# 2x the headroom for the same tier. Disk always caps what can be downloaded.
_GPU_BUCKET_THRESHOLDS: tuple[tuple[float, str], ...] = ((6.0, "A"), (10.0, "B"), (16.0, "C"), (24.0, "D"))
_RAM_BUCKET_THRESHOLDS: tuple[tuple[float, str], ...] = ((16.0, "A"), (24.0, "B"), (32.0, "C"), (48.0, "D"))
_DISK_BUCKET_THRESHOLDS: tuple[tuple[float, str], ...] = ((20.0, "A"), (40.0, "B"), (80.0, "C"), (150.0, "D"))
_BUCKET_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "E")


def _bucket_from_thresholds(mem_gb: float, thresholds: tuple[tuple[float, str], ...]) -> str:
    for ceiling, bucket in thresholds:
        if mem_gb < ceiling:
            return bucket
    return "E"


def _probe_physical_vram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    """The card's physical VRAM regardless of whether the accelerator label was downgraded."""
    raw = probe.get("vram_gb") if isinstance(probe, Mapping) else getattr(probe, "vram_gb", None)
    try:
        return float(raw) if raw is not None else 0.0
    except Exception:
        return 0.0


def _probe_accelerator_status(probe: MachineProbe | Mapping[str, Any]) -> str:
    raw = probe.get("accelerator_status") if isinstance(probe, Mapping) else getattr(probe, "accelerator_status", "")
    return str(raw or "").strip().lower()


def _ollama_gpu_serves_model(probe: MachineProbe | Mapping[str, Any]) -> bool:
    """True when a usable discrete NVIDIA GPU is DETECTED (compute-cap classified `usable` by
    core.hardware_tier), independent of any live llama.cpp benchmark.

    Ollama auto-offloads a model into CUDA VRAM whenever a usable NVIDIA card is present — no
    GGUF/llama.cpp path required — so such a host should be VRAM-sized even when the (opt-in)
    llama.cpp probe never ran. Scoped to `cuda` on purpose: AMD/DirectML serving is not a lane
    we've verified yet, so those stay conservatively RAM-sized."""
    accelerator = _probe_accelerator(probe)
    return accelerator == "cuda" and _probe_accelerator_status(probe) == "usable"


def capacity_bucket_for_machine(
    *, probe: MachineProbe | Mapping[str, Any], free_disk_gb: float, gpu_usable: bool = False
) -> str:
    """Largest model tier (A-E) the machine can actually run.

    The tier is sized by VRAM (never dragged down by modest system RAM) whenever the model
    will actually be GPU-served — this is what stops a strong GPU (e.g. a 24GB card in a
    16GB-RAM host) from being handed a tiny bucket. Two independent signals unlock VRAM
    sizing:
      * `gpu_usable=True` — a live core.llamacpp_capability_probe benchmark verified the card
        (this also covers an otherwise-downgraded legacy CUDA card the probe proved works); or
      * a detected usable discrete NVIDIA GPU — Ollama auto-offloads into CUDA VRAM with no
        llama.cpp/GGUF path, so a compute-cap-`usable` card is VRAM-sized even when the opt-in
        llama.cpp probe never ran (`_ollama_gpu_serves_model`).
    Without either, the model runs on the CPU, where system RAM is the real limit (needs ~2x
    headroom), so a strong CPU host is no longer capped at the weakest tier either. Free disk
    always caps the result. The default (`gpu_usable=False`, no usable GPU) is safe CPU sizing.
    """
    ram_gb = _probe_ram_gb(probe)
    accelerator = _probe_accelerator(probe)
    physical_vram = _probe_physical_vram_gb(probe)
    free_gb = float(free_disk_gb or 0.0)

    if accelerator == "mps":
        # Apple unified memory is fast (Metal), but it is SHARED with the OS and every app,
        # so it needs the same ~2x headroom as system RAM rather than the dedicated-VRAM
        # thresholds — a 24GB Mac lands in C, not E.
        base = _bucket_from_thresholds(ram_gb, _RAM_BUCKET_THRESHOLDS)
    elif physical_vram >= 6.0 and (gpu_usable or _ollama_gpu_serves_model(probe)):
        # The model offloads into VRAM (llama.cpp when verified, else Ollama-on-CUDA for a
        # detected usable card) -> size by VRAM, never by host RAM.
        base = _bucket_from_thresholds(physical_vram, _GPU_BUCKET_THRESHOLDS)
    else:
        # CPU inference -> system RAM is the limiter.
        base = _bucket_from_thresholds(ram_gb, _RAM_BUCKET_THRESHOLDS)

    disk_cap = _bucket_from_thresholds(free_gb, _DISK_BUCKET_THRESHOLDS)
    return _BUCKET_ORDER[min(_BUCKET_ORDER.index(base), _BUCKET_ORDER.index(disk_cap))]


def resolve_local_bundle_recommendation(
    *,
    probe: MachineProbe | Mapping[str, Any],
    free_disk_gb: float,
    secondary_local_model_name: str,
    selected_model: str = "",
    gpu_capability: Any = None,
) -> LocalBundleRecommendation:
    """Every capacity bucket A-E always resolves to a 3-role tiny_fast/daily_accelerated/
    deep_overnight bundle — no bucket, including the weakest, collapses to a single
    model. gpu_capability, when provided, should be a
    core.llamacpp_capability_probe.CapabilityProbeResult (or None). It is intentionally
    typed loosely here to avoid a hard import dependency for callers that never probe.
    The GPU-accelerated variant of the bucket's bundle is only ever selected when a
    live probe result says `usable=True` — never from a GPU name heuristic alone. With
    no probe result (gpu_capability=None) or a probe that rejected the GPU, the
    CPU-only variant is used, which is still a full 3-tier spread, just RAM-sized."""
    explicit_model = str(selected_model or "").strip()
    fit = local_multi_llm_fit_from_probe(probe)
    # We only take the GPU-accelerated path when the model can actually be offloaded there:
    # a live-verified probe (usable=True) AND enough VRAM to hold the GPU bundle's daily
    # model (>=6GB) AND not Apple MPS (the ..._gpu_accelerated bundles are llama.cpp
    # CUDA/HIP; an Apple box runs the CPU/Ollama-Metal bundle instead). A live probe that
    # verified an otherwise-downgraded ("cpu"-labelled legacy) CUDA card still counts — the
    # measured verdict is the source of truth. This single flag drives BOTH the bucket
    # sizing and the variant pick, so a VRAM-sized bucket always pairs with a GPU bundle
    # and a RAM-sized bucket with a CPU bundle — they can never desync.
    gpu_usable = bool(gpu_capability is not None and getattr(gpu_capability, "usable", False))
    accelerator = _probe_accelerator(probe)
    physical_vram = _probe_physical_vram_gb(probe)
    gpu_offload = gpu_usable and accelerator != "mps" and physical_vram >= 6.0
    bucket = capacity_bucket_for_machine(probe=probe, free_disk_gb=free_disk_gb, gpu_usable=gpu_offload)
    advanced_allowed = fit != "single_model_only" and free_disk_gb >= model_storage_gb(secondary_local_model_name) + 8.0

    if explicit_model:
        legacy_bundle = LocalBundleSpec(
            bundle_id="legacy_single_explicit_model",
            kind="single",
            display_name="Explicit single model",
            role_models=(BundleRoleModel("general", explicit_model),),
            summary="Preserve an explicitly selected local model without silently replacing it.",
        )
        fallback_bundle = bundle_spec("dual_qwen3_8b_gemma3_4b")
        reasons = (
            f"Explicit primary model `{explicit_model}` is preserved instead of silently switching the runtime to a new bundle.",
            "Bundle auto-selection remains available for fresh installs that do not pin a legacy model.",
        )
        return LocalBundleRecommendation(
            capacity_bucket=bucket,
            local_multi_llm_fit=fit,
            free_disk_gb=round(float(free_disk_gb), 1),
            recommended_bundle=legacy_bundle,
            fallback_bundle=fallback_bundle,
            safe_disk_floor_gb=safe_disk_floor_gb(legacy_bundle.models),
            advanced_optional_allowed=advanced_allowed,
            advanced_optional_profile="local-max" if advanced_allowed else "",
            selection_reasons=reasons,
            legacy_mode=True,
        )

    bucket_key = bucket.lower()
    no_gpu_bundle = bundle_spec(f"triple_bucket_{bucket_key}_no_gpu")
    gpu_bundle = bundle_spec(f"triple_bucket_{bucket_key}_gpu_accelerated")
    if gpu_offload:
        recommended = gpu_bundle
        fallback = no_gpu_bundle  # safe degrade if the GPU later becomes unavailable
        reasons = (
            f"Capacity bucket {bucket} has a live-verified llama.cpp GPU backend "
            f"(measured {getattr(gpu_capability, 'gpu_tokens_per_second', 0.0):.1f} tok/s, "
            f"{getattr(gpu_capability, 'speedup_ratio', 0.0):.1f}x over CPU baseline), so the daily and deep "
            "lanes are both GPU-accelerated instead of RAM-sized.",
            "Every bucket always resolves to a tiny_fast/daily_accelerated/deep_overnight spread, "
            "never a single model, regardless of GPU state.",
        )
    elif physical_vram >= 6.0 and _ollama_gpu_serves_model(probe):
        # Detected usable NVIDIA GPU but no live llama.cpp verdict: Ollama still auto-offloads
        # the Ollama-native bundle into CUDA VRAM, so the tier is VRAM-sized and GPU-served —
        # just not via the (opt-in) llama.cpp/GGUF variant.
        recommended = no_gpu_bundle
        fallback = no_gpu_bundle
        reasons = (
            f"Capacity bucket {bucket} is sized for your GPU's VRAM: a usable NVIDIA card was "
            "detected, so Ollama serves the tiny_fast/daily_accelerated/deep_overnight bundle on "
            "the GPU (CUDA offload) rather than on the CPU.",
            "The heavier llama.cpp/GGUF-accelerated variant is a separate opt-in that needs a "
            "measured benchmark (core.llamacpp_capability_probe); the Ollama bundle here already "
            "runs on the GPU.",
        )
    else:
        recommended = no_gpu_bundle
        fallback = no_gpu_bundle
        reasons = (
            f"Capacity bucket {bucket} has no usable GPU backend, so the "
            "tiny_fast/daily_accelerated/deep_overnight spread is sized for RAM-only inference.",
            "A GPU name alone is never enough to promise acceleration — only a detected "
            "compute-cap-usable NVIDIA card (Ollama-served) or a measured llama.cpp benchmark "
            "unlocks VRAM sizing.",
        )

    return LocalBundleRecommendation(
        capacity_bucket=bucket,
        local_multi_llm_fit=fit,
        free_disk_gb=round(float(free_disk_gb), 1),
        recommended_bundle=recommended,
        fallback_bundle=fallback,
        safe_disk_floor_gb=safe_disk_floor_gb(recommended.models),
        advanced_optional_allowed=advanced_allowed,
        advanced_optional_profile="local-max" if advanced_allowed else "",
        selection_reasons=reasons,
        gpu_capability_used=gpu_offload,
    )


def provider_role_for_bundle_role(bundle_role: str) -> str:
    clean = str(bundle_role or "").strip().lower()
    if clean in {"reasoning", "heavy_reasoning", "deep_overnight"}:
        return "queen"
    return "drone"


def manifest_profile_for_model(*, model_name: str, bundle_role: str) -> dict[str, Any]:
    metadata = model_metadata(model_name)
    family = str(metadata.get("family") or model_name).strip()
    clean_role = str(bundle_role or "general").strip().lower()
    capabilities = ["summarize", "classify", "format", "extract", "structured_json"]
    tool_support = ["structured_json"]
    confidence = 0.67
    notes = "General local Ollama lane."
    if clean_role == "general":
        capabilities.extend(["code_basic", "tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.72
        notes = "General local Ollama companion lane."
    elif clean_role == "coding":
        capabilities.extend(["code_basic", "code_complex", "tool_intent"])
        tool_support.extend(["tool_calls", "code_complex"])
        confidence = 0.77
        notes = "Coding-focused local Ollama lane."
    elif clean_role == "reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.79
        notes = "Reasoning and review local Ollama lane."
    elif clean_role == "heavy_reasoning":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.73
        notes = "Oversized local Ollama lane for explicit deep work."
    elif clean_role == "lightweight_utility":
        capabilities.extend(["tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.63
        notes = "Lightweight local Ollama utility lane for cheap classification and tool intent."
    elif clean_role == "tiny_fast":
        capabilities.extend(["tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.6
        notes = "Always-resident tiny local lane for instant classification, tool intent, and formatting."
    elif clean_role == "daily_accelerated":
        capabilities.extend(["code_basic", "tool_intent"])
        tool_support.append("tool_calls")
        confidence = 0.74
        notes = "Daily-driver local lane, GPU-accelerated via llama.cpp when a live-verified backend exists."
    elif clean_role == "deep_overnight":
        capabilities.extend(["code_basic", "code_complex", "long_context"])
        tool_support.extend(["web_search", "code_complex"])
        confidence = 0.7
        notes = "Slow-but-powerful overnight/batch local lane; honestly framed as non-interactive-speed."
    profile = {
        "family": family,
        "license_name": str(metadata.get("license_name") or "user-managed"),
        "license_reference": str(metadata.get("license_reference") or "user-managed"),
        "parameter_count": str(metadata.get("parameter_count") or "").strip(),
        "capabilities": tuple(dict.fromkeys(capabilities)),
        "tool_support": tuple(dict.fromkeys(tool_support)),
        "confidence_baseline": confidence,
        "notes": notes,
        "orchestration_role": provider_role_for_bundle_role(clean_role),
        "bundle_role": clean_role,
    }
    for key in ("tokens_per_second", "quantization"):
        if metadata.get(key) not in (None, ""):
            profile[key] = metadata[key]
    return profile


def _probe_ram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    if isinstance(probe, Mapping):
        return float(probe.get("ram_gb") or 0.0)
    return float(probe.ram_gb or 0.0)


def _probe_accelerator(probe: MachineProbe | Mapping[str, Any]) -> str:
    if isinstance(probe, Mapping):
        return str(probe.get("accelerator") or "").strip().lower()
    return str(probe.accelerator or "").strip().lower()


def _probe_effective_vram_gb(probe: MachineProbe | Mapping[str, Any]) -> float:
    accelerator = _probe_accelerator(probe)
    if accelerator not in {"cuda", "directml"}:
        return 0.0
    if isinstance(probe, Mapping):
        raw_vram = probe.get("vram_gb")
        return float(raw_vram or 0.0) if raw_vram is not None else 0.0
    return float(probe.vram_gb or 0.0) if probe.vram_gb is not None else 0.0


__all__ = [
    "GGUF_MODEL_SOURCES",
    "LOCAL_BUNDLE_SPECS",
    "MODEL_METADATA",
    "MODEL_STORAGE_GB",
    "SPEED_TIER_ROLES",
    "BundleRoleModel",
    "LocalBundleRecommendation",
    "LocalBundleSpec",
    "bundle_spec",
    "capacity_bucket_for_machine",
    "honest_model_label",
    "installed_ollama_role_for_model",
    "llamacpp_offload_layers",
    "local_multi_llm_fit_from_probe",
    "manifest_profile_for_model",
    "model_active_parameter_billions",
    "model_is_distill",
    "model_metadata",
    "model_parameter_billions",
    "model_storage_gb",
    "provider_role_for_bundle_role",
    "resolve_local_bundle_recommendation",
    "safe_disk_floor_gb",
]
