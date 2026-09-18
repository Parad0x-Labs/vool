from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from adapters.base_adapter import ModelResponse
from core.hardware_tier import MachineProbe
from core.local_inference_autopilot import (
    _deep_lane_fits,
    _message_complexity,
    _select_primary_capability,
    build_local_inference_autopilot_plan,
    build_prefix_cache_plan,
    compile_context_capsule,
    select_daily_residency_model_tag,
)
from core.local_inference_evidence import (
    hydrate_capability_truth_with_benchmarks,
    latest_local_inference_benchmarks,
    record_ollama_generate_benchmark,
)
from core.memory_first_router import MemoryFirstRouter
from core.model_health import reset_provider_health
from core.provider_routing import ProviderCapabilityTruth
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest


def _capability(
    model_id: str,
    *,
    role_fit: str,
    tokens_per_second: float,
    tool_support: tuple[str, ...] = ("structured_json",),
    availability_state: str = "ready",
) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=8192,
        tool_support=tool_support,
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state=availability_state,
    )


def _local_capabilities() -> tuple[ProviderCapabilityTruth, ...]:
    return (
        _capability("qwen2.5:0.5b", role_fit="drone", tokens_per_second=313.0),
        _capability("qwen3:8b", role_fit="drone", tokens_per_second=16.4),
        _capability("qwen3:14b", role_fit="queen", tokens_per_second=11.4, tool_support=("structured_json", "code_complex")),
        _capability("qwen2.5:32b", role_fit="queen", tokens_per_second=0.3, tool_support=("structured_json", "code_complex")),
    )


def _remote_capability(
    model_id: str,
    *,
    role_fit: str = "queen",
    tokens_per_second: float = 0.0,
) -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id=f"openrouter-byok:{model_id}",
        model_id=model_id,
        role_fit=role_fit,
        context_window=131072,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=tokens_per_second,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="",
        locality="remote",
        privacy_class="remote_provider",
        queue_depth=0,
        max_safe_concurrency=2,
        availability_state="ready",
    )


def _llamacpp_capability() -> ProviderCapabilityTruth:
    return ProviderCapabilityTruth(
        provider_id="llamacpp-local:qwen2.5:14b-gguf",
        model_id="qwen2.5:14b-gguf",
        role_fit="drone",
        context_window=4096,
        tool_support=("structured_json", "code_complex"),
        structured_output_support=True,
        tokens_per_second=0.0,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="q4_K_M",
        locality="local",
        privacy_class="local_private",
        queue_depth=0,
        max_safe_concurrency=1,
        availability_state="ready",
    )


def test_context_capsule_redacts_private_paths_and_keeps_stable_prefix_hash_off_user_prompt() -> None:
    fake_provider_token = "sk" + "-proj-" + ("abcDEF1234567890" * 2)
    source_context = {
        "repo_identity": "vool-hive-mind",
        "memory_capsule": "Web0 is local-first. Source lives at /Users/loop/private/web0.",
        "diff_summary": f"Touched /Users/loop/.openclaw/openclaw.json with {fake_provider_token}.",
        "constraints": ["local only", "never expose private paths"],
        "evidence_refs": ["file:///Users/loop/private/log.txt"],
    }

    first = compile_context_capsule(user_text="tell me about web0", source_context=source_context)
    second = compile_context_capsule(user_text="different current prompt", source_context=source_context)

    assert first.stable_prefix_hash == second.stable_prefix_hash
    assert "/Users/" not in first.compressed_prompt
    assert fake_provider_token not in first.compressed_prompt
    assert "<private-path>" in first.compressed_prompt
    assert first.omitted_private_items >= 2


def test_autopilot_routes_tiny_daily_and_deep_without_defaulting_to_32b() -> None:
    capabilities = _local_capabilities()

    tiny = build_local_inference_autopilot_plan(
        user_text="classify this tool intent",
        task_kind="tool_intent",
        output_mode="tool_intent",
        provider_role="drone",
        capability_truth=capabilities,
    )
    daily = build_local_inference_autopilot_plan(
        user_text="what can we do today?",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=capabilities,
    )
    deep = build_local_inference_autopilot_plan(
        user_text="patch the local runtime and run tests",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert tiny.lane == "tiny"
    assert tiny.selected_model == "qwen2.5:0.5b"
    assert daily.lane == "daily"
    assert daily.selected_model == "qwen3:8b"
    assert deep.lane == "deep"
    assert deep.selected_model == "qwen3:14b"
    assert deep.verifier_required is True
    assert deep.verifier_model == "qwen3:8b"
    assert "qwen2.5:32b" not in {deep.selected_model, deep.verifier_model}
    assert any(action.model_id == "qwen2.5:32b" and action.action == "refuse_default" for action in deep.residency)
    assert any("oversized_lane_not_default" in warning for warning in deep.warnings)


def test_autopilot_keeps_daily_repo_work_off_deep_queen_lane() -> None:
    capabilities = _local_capabilities()

    plan = build_local_inference_autopilot_plan(
        user_text="Using workspace tools only, read pyproject.toml and summarize the project metadata.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_daily_lane_prefers_installed_baseline_tag_on_near_tie() -> None:
    # qwen2.5:7b and qwen3:8b share the daily size band and (here) the same tok/s. qwen3 has no
    # proven no-think artifact on this runtime, so ordinary Auto must prefer the non-thinking model
    # even without a baseline hint. The baseline hint preserves the same truthful choice.
    caps = (
        _capability("qwen2.5:7b", role_fit="drone", tokens_per_second=18.0),
        _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),
    )
    common = dict(
        user_text="summarize the project metadata",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="drone",
        capability_truth=caps,
    )
    without = build_local_inference_autopilot_plan(**common)
    with_tag = build_local_inference_autopilot_plan(**common, default_model_tag="qwen2.5:7b")

    assert without.lane == "daily" and with_tag.lane == "daily"
    assert without.selected_model == "qwen2.5:7b"
    assert with_tag.selected_model == "qwen2.5:7b"


def test_set5_daily_auto_prefers_reliable_non_thinking_model_over_faster_reasoning_tokens() -> None:
    """Exact live shape: qwen3 reported ~33 tok/s but emitted reasoning; qwen2.5 had no prior TPS."""

    plan = build_local_inference_autopilot_plan(
        user_text="Explain the distinction in two concise sentences.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(
            _capability("qwen3:4b", role_fit="drone", tokens_per_second=33.0),
            _capability("qwen2.5:7b", role_fit="drone", tokens_per_second=0.0),
        ),
        default_model_tag="qwen2.5:7b",
    )

    assert plan.lane == "daily"
    assert plan.selected_model == "qwen2.5:7b"


def test_daily_penalty_is_not_a_global_block_when_only_thinking_model_is_available() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="Explain the distinction briefly.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(
            _capability("qwen3:4b", role_fit="drone", tokens_per_second=33.0),
        ),
    )

    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:4b"


def test_explicit_reasoning_lane_keeps_thinking_model_eligible() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="Reason step by step about the architecture tradeoff.",
        task_kind="reasoning",
        output_mode="plain_text",
        provider_role="queen",
        capability_truth=(
            _capability("qwen3:14b", role_fit="queen", tokens_per_second=11.0),
            _capability("qwen2.5:7b", role_fit="drone", tokens_per_second=20.0),
        ),
    )

    assert plan.lane == "deep"
    assert plan.selected_model == "qwen3:14b"


def test_auxiliary_residency_priority_matches_daily_reliable_candidate_matrix() -> None:
    from core.local_inference_autopilot import prioritize_daily_residency_capabilities

    qwen3 = _capability("qwen3:4b", role_fit="drone", tokens_per_second=33.0)
    qwen25 = _capability("qwen2.5:7b", role_fit="drone", tokens_per_second=0.0)
    remote = _remote_capability("free/helper", tokens_per_second=80.0)

    ranked = prioritize_daily_residency_capabilities(
        (qwen3, qwen25, remote),
        default_model_tag="qwen2.5:7b",
    )

    assert ranked[0].model_id == "qwen2.5:7b"
    assert {item.provider_id for item in ranked} == {
        qwen3.provider_id,
        qwen25.provider_id,
        remote.provider_id,
    }


def test_auxiliary_residency_priority_preserves_sole_thinking_and_proven_tiny_nothink() -> None:
    from core.local_inference_autopilot import prioritize_daily_residency_capabilities

    qwen3 = _capability("qwen3:4b", role_fit="drone", tokens_per_second=33.0)
    tiny_nothink = _capability("qwen3:0.6b:nothink", role_fit="drone", tokens_per_second=120.0)

    assert prioritize_daily_residency_capabilities((qwen3,))[0] is qwen3
    assert prioritize_daily_residency_capabilities((qwen3, tiny_nothink))[0] is tiny_nothink


def test_boot_inventory_selection_reuses_daily_residency_policy() -> None:
    assert select_daily_residency_model_tag(
        ["qwen3:4b", "qwen2.5:7b"],
        default_model_tag="qwen3:4b",
    ) == "qwen2.5:7b"
    assert select_daily_residency_model_tag(
        ["qwen3:4b"],
        default_model_tag="qwen3:4b",
    ) == "qwen3:4b"
    assert select_daily_residency_model_tag(
        ["qwen3:4b", "vool-qwen3-30b-a3b:nothink", "qwen2.5:7b"],
        default_model_tag="qwen3:4b",
    ) == "vool-qwen3-30b-a3b:nothink"


def test_boot_inventory_selection_is_installed_only_and_deduplicates_tags() -> None:
    assert select_daily_residency_model_tag([], default_model_tag="qwen2.5:7b") == ""
    assert select_daily_residency_model_tag(
        ["QWEN2.5:7B", "qwen2.5:7b", "qwen2.5:14b"],
        default_model_tag="qwen3:4b",
    ) == "QWEN2.5:7B"


def test_message_complexity_trivial_greetings() -> None:
    for msg in ("Hi", "hey there", "thanks!", "thanks so much", "good morning team", "cheers", "gn", "how are you"):
        assert _message_complexity(msg) == "trivial", msg


def test_message_complexity_heavy_imperative_builds() -> None:
    # Imperative commands to build a software artifact -> smartest lane.
    for msg in (
        "build me test123.null website",
        "create a telegram bot with an api backend",
        "build me an app",
        "make a website",
        "code a website for me",
        "make a landing page",
        "build a game",
        "build an online store",
        "please build me a dashboard",
        "can you scaffold a full-stack platform",
        "deploy the site",
    ):
        assert _message_complexity(msg) == "heavy", msg


def test_message_complexity_neutral_not_over_escalated() -> None:
    # Adversarially-found false positives: questions, negations, third-party statements, tooling
    # chatter, and topic-not-target phrasing must NOT be escalated to the slow deep model.
    for msg in (
        "how do i build a website?",
        "should i build an app or a website?",
        "don't build me a website, just advise",
        "i do not want to build an app",
        "my friend wants to build a bot",
        "when did they deploy the site?",
        "the docs say to run npm run build for the app",
        "write me a blog post about building a website",
        "set up my api key",
        "i need relationship advice",
        "what changed in the config?",
        "why did the build fail?",
        "write a short reply",
    ):
        assert _message_complexity(msg) == "", msg


def test_autopilot_routes_trivial_greeting_to_tiny_fast_lane() -> None:
    # "Hi" -> fastest model, even though the classifier labels it chat/normalization (daily).
    plan = build_local_inference_autopilot_plan(
        user_text="Hi",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
    )
    assert plan.lane == "tiny"
    assert plan.selected_model == "qwen2.5:0.5b"


def test_autopilot_routes_agentic_build_to_deep_smart_lane() -> None:
    # "build me test123.null website" -> smartest model, though the classifier under-labels a bare
    # website build (no telegram/bot/api keyword) as chat/normalization -> daily.
    plan = build_local_inference_autopilot_plan(
        user_text="build me test123.null website",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
    )
    assert plan.lane == "deep"
    assert plan.selected_model == "qwen3:14b"


def test_autopilot_trivial_stays_daily_when_no_tiny_model_available() -> None:
    # Guard: a greeting must not escalate to a lane with no viable model.
    plan = build_local_inference_autopilot_plan(
        user_text="hey",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(_capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),),
    )
    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_heavy_build_stays_daily_when_no_deep_model_available() -> None:
    # Guard: an agentic build must not block on small hardware; it uses the best available model.
    plan = build_local_inference_autopilot_plan(
        user_text="build me a full website with an api backend",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(_capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),),
    )
    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_deep_lane_fits_respects_free_vram() -> None:
    caps = _local_capabilities()  # includes qwen3:14b (>=13B) plus smaller models
    assert _deep_lane_fits(caps, free_vram_gb=None) is True   # unknown VRAM -> don't downgrade
    assert _deep_lane_fits(caps, free_vram_gb=24.0) is True   # plenty of VRAM -> a 14B fits
    assert _deep_lane_fits(caps, free_vram_gb=7.0) is False   # ~7 GB can't hold a ~14B model
    small = (_capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),)
    assert _deep_lane_fits(small, free_vram_gb=None) is False  # no >=13B model at all


def test_autopilot_heavy_build_stays_daily_when_deep_model_does_not_fit_vram() -> None:
    # The GTX 1080 case: a 14B deep model exists but won't fit ~7 GB free VRAM, so the build stays on
    # the GPU-served daily model instead of a too-big model that would run (slowly) on CPU.
    plan = build_local_inference_autopilot_plan(
        user_text="build me a test123.null website",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
        free_vram_gb=7.0,
    )
    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_heavy_build_uses_deep_when_deep_model_fits_vram() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="build me a test123.null website",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
        free_vram_gb=24.0,
    )
    assert plan.lane == "deep"
    assert plan.selected_model == "qwen3:14b"


def test_autopilot_verifier_task_downgrades_from_deep_when_no_deep_model_fits() -> None:
    # A verifier-worthy task (risk term "refactor") would normally go deep; with no fitting deep model
    # it uses the best-fitting daily model rather than a too-big model on CPU.
    plan = build_local_inference_autopilot_plan(
        user_text="refactor the auth module and run the tests",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
        free_vram_gb=7.0,
    )
    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_ordinary_question_is_not_escalated_to_deep() -> None:
    # Anti-over-escalation: a normal question stays on the daily lane, not the slow deep model.
    plan = build_local_inference_autopilot_plan(
        user_text="what does this config option do?",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=_local_capabilities(),
    )
    assert plan.lane == "daily"
    assert plan.selected_model == "qwen3:8b"


def test_autopilot_prefers_live_llamacpp_specialist_for_deep_with_independent_small_verifier() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="High-risk engineering task. Refactor the adaptive lane proof architecture: identify two failure modes and the minimal tests. Verifier required.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=(_llamacpp_capability(), *_local_capabilities()[1:3]),
    )

    assert plan.lane == "deep"
    assert plan.selected_provider_id == "llamacpp-local:qwen2.5:14b-gguf"
    assert plan.selected_model == "qwen2.5:14b-gguf"
    assert plan.verifier_required is True
    assert plan.verifier_provider_id == "ollama-local:qwen3:8b"
    assert plan.verifier_model == "qwen3:8b"


def test_autopilot_prefers_measured_fast_nothink_default_for_daily_lane() -> None:
    capabilities = (
        _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.8),
        _capability("vool-qwen3-30b-a3b:nothink", role_fit="drone", tokens_per_second=37.2),
    )

    daily = build_local_inference_autopilot_plan(
        # A plain daily helper ask (not an agentic build, which now escalates to the deep lane).
        user_text="summarize the standup notes into three short bullets",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=capabilities,
    )

    assert daily.lane == "daily"
    assert daily.selected_model == "vool-qwen3-30b-a3b:nothink"
    assert not any("vool-qwen3-30b-a3b:nothink:oversized_lane_not_default" in warning for warning in daily.warnings)


def test_autopilot_emits_apple_mlx_flags_eagle_candidate_and_sysctl_warning() -> None:
    probe = MachineProbe(
        cpu_cores=12,
        ram_gb=48.0,
        gpu_name="Apple Silicon",
        vram_gb=48.0,
        accelerator="mps",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Darwin"), mock.patch(
        "core.local_inference_autopilot._read_iogpu_wired_limit_mb",
        return_value=20_000,
    ):
        plan = build_local_inference_autopilot_plan(
            user_text="write a short local helper reply",
            task_kind="normalization_assist",
            output_mode="plain_text",
            provider_role="auto",
            capability_truth=(_capability("qwen3:8b", role_fit="drone", tokens_per_second=44.0),),
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.framework == "ollama_mlx"
    assert plan.runtime_flags["OLLAMA_MLX"] == "1"
    assert plan.runtime_flags["num_gpu"] == 999
    assert plan.entropy_escalation_threshold == 0.35
    assert plan.suffix_decode_eligible is False
    assert "framework" in phase_names
    assert "eagle_candidate" in phase_names
    assert "speculative_decoding" not in phase_names
    assert "sysctl_warning" in phase_names
    assert any(phase.name == "sysctl_warning" and phase.status == "blocked" for phase in plan.phases)


def test_autopilot_marks_suffix_decode_tasks_and_does_not_stack_eagle() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Linux"):
        plan = build_local_inference_autopilot_plan(
            user_text="classify the next tool call",
            task_kind="tool_intent",
            output_mode="tool_intent",
            provider_role="drone",
            capability_truth=(
                _capability("qwen3:0.6b", role_fit="drone", tokens_per_second=220.0),
                _capability("qwen3:8b", role_fit="drone", tokens_per_second=44.0),
            ),
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.selected_model == "qwen3:0.6b"
    assert plan.framework == "llama_cpp"
    assert plan.runtime_flags["cache_type_k"] == "q8_0"
    assert plan.suffix_decode_eligible is True
    assert "suffix_decoding" in phase_names
    assert "speculative_decoding" not in phase_names
    assert "eagle_candidate" not in phase_names


def test_autopilot_routes_vulkan_accelerator_to_vulkan_lane() -> None:
    # Inert Vulkan lane (issue #21): reachable only when a real "vulkan" accelerator is detected, which
    # never happens on CUDA/mps/CPU boxes. Fabricate the probe to prove the new branch is reachable; the
    # tok/s claim is deferred to real AMD/Intel hardware.
    probe = MachineProbe(
        cpu_cores=8,
        ram_gb=16.0,
        gpu_name="AMD Radeon 780M",
        vram_gb=4.0,
        accelerator="vulkan",
    )
    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Linux"):
        plan = build_local_inference_autopilot_plan(
            user_text="write a short local helper reply",
            task_kind="normalization_assist",
            output_mode="plain_text",
            provider_role="auto",
            capability_truth=(_capability("qwen3:8b", role_fit="drone", tokens_per_second=20.0),),
            machine_probe=probe,
        )
    assert plan.framework == "vulkan"
    assert plan.runtime_flags["ngl"] == 999


def test_autopilot_adds_moe_flags_without_eagle_for_hybrid_lanes() -> None:
    probe = MachineProbe(
        cpu_cores=16,
        ram_gb=64.0,
        gpu_name="RTX 4090",
        vram_gb=24.0,
        accelerator="cuda",
    )

    with mock.patch("core.local_inference_autopilot.platform.system", return_value="Linux"):
        plan = build_local_inference_autopilot_plan(
            user_text="use the heavy local lane for deep synthesis",
            task_kind="action_plan",
            output_mode="action_plan",
            provider_role="queen",
            capability_truth=(
                _capability("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=75.0),
            ),
            source_context={"autopilot_allow_heavy_model": True},
            machine_probe=probe,
        )

    phase_names = {phase.name for phase in plan.phases}
    assert plan.selected_model == "qwen3.5:35b-a3b"
    assert plan.runtime_flags["ngl"] == 999
    assert plan.runtime_flags["fit_target"] == 2048
    assert "speculative_decoding" not in phase_names
    assert "eagle_candidate" not in phase_names


def test_autopilot_blocks_35b_as_default_until_explicitly_requested() -> None:
    capabilities = (
        _capability(
            "qwen3.5:35b-a3b",
            role_fit="queen",
            tokens_per_second=1.2,
            tool_support=("structured_json", "code_complex"),
        ),
    )

    blocked = build_local_inference_autopilot_plan(
        user_text="patch this hard runtime bug",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )
    explicit = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=capabilities,
    )

    assert blocked.lane == "deep"
    assert blocked.selected_model is None
    assert blocked.verifier_model is None
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "refuse_default" for action in blocked.residency)
    assert any("oversized_lane_not_default" in warning for warning in blocked.warnings)
    assert explicit.selected_model == "qwen3.5:35b-a3b"
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "load_explicit_only" for action in explicit.residency)


def test_autopilot_blocks_explicit_heavy_when_no_heavy_lane_is_healthy() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="action_plan",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=(
            _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0),
            _capability(
                "qwen3:14b",
                role_fit="queen",
                tokens_per_second=11.4,
                tool_support=("structured_json", "code_complex"),
            ),
            _capability("qwen3.5:35b-a3b", role_fit="queen", tokens_per_second=0.4, availability_state="blocked"),
        ),
    )

    assert plan.lane == "deep"
    assert plan.selected_model is None
    assert "explicit_heavy_lane_unavailable" in plan.warnings
    assert any(action.model_id == "qwen3.5:35b-a3b" and action.action == "refuse_default" for action in plan.residency)


def test_autopilot_does_not_substitute_32b_for_explicit_35b_request() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="use the 35b heavy local lane for this hard runtime bug",
        task_kind="action_plan",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=(
            _capability("qwen2.5:32b", role_fit="queen", tokens_per_second=0.6, tool_support=("structured_json", "code_complex")),
            _capability("qwen3:14b", role_fit="queen", tokens_per_second=11.4, tool_support=("structured_json", "code_complex")),
        ),
    )

    assert plan.selected_model is None
    assert "explicit_heavy_lane_unavailable" in plan.warnings


def test_autopilot_progress_phases_are_concrete_and_block_when_no_lane_exists() -> None:
    plan = build_local_inference_autopilot_plan(
        user_text="delete stale configs then patch code",
        task_kind="coding_help_complex",
        output_mode="action_plan",
        provider_role="queen",
        capability_truth=tuple(),
    )

    phase_names = [phase.name for phase in plan.phases]
    assert plan.lane == "human"
    assert phase_names == ["route", "retrieve", "compress", "framework", "preload", "generate", "verify", "test", "repair"]
    assert plan.framework == "unknown"
    assert plan.runtime_flags == {}
    assert any(phase.name == "preload" and phase.status == "blocked" for phase in plan.phases)
    assert "no_local_or_provider_lane_available" in plan.warnings


def test_prefix_cache_plan_exposes_backend_specific_truth() -> None:
    llama_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="llama.cpp")
    mlx_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="mlx-lm")
    ollama_plan = build_prefix_cache_plan(stable_prefix_hash="abc123", backend="ollama")

    assert llama_plan.supported is True
    assert llama_plan.action == "slot_save_restore"
    assert mlx_plan.supported is True
    assert mlx_plan.action == "cache_prompt"
    assert ollama_plan.supported is False
    assert ollama_plan.action == "preload_keep_alive"


def test_local_inference_ledger_hydrates_provider_truth_for_autopilot() -> None:
    provider_id = "ollama-local:test-ledger-8b"
    fact = record_ollama_generate_benchmark(
        provider_id=provider_id,
        model_id="qwen3:8b",
        prompt="Write one short paragraph.",
        response_payload={
            "eval_count": 40,
            "eval_duration": 2_000_000_000,
            "load_duration": 120_000_000,
            "prompt_eval_duration": 80_000_000,
        },
        context_window=4096,
        processor="100% GPU",
        quantization="q4_K_M",
    )

    latest = latest_local_inference_benchmarks(provider_ids=(provider_id,), limit=4)
    hydrated = hydrate_capability_truth_with_benchmarks(
        (
            ProviderCapabilityTruth(
                provider_id=provider_id,
                model_id="qwen3:8b",
                role_fit="drone",
                context_window=0,
                tool_support=("structured_json",),
                structured_output_support=True,
                tokens_per_second=0.0,
                ram_budget_gb=0.0,
                vram_budget_gb=0.0,
                quantization="",
                locality="local",
                privacy_class="local_private",
                queue_depth=0,
                max_safe_concurrency=1,
            ),
        )
    )
    plan = build_local_inference_autopilot_plan(
        user_text="normal daily helper answer",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=hydrated,
    )

    assert latest[0].benchmark_id == fact.benchmark_id
    assert latest[0].tokens_per_second == 20.0
    assert hydrated[0].tokens_per_second == 20.0
    assert hydrated[0].context_window == 4096
    assert hydrated[0].quantization == "q4_K_M"
    assert hydrated[0].measurement_source == "local_inference_benchmark"
    assert hydrated[0].measured_at == fact.created_at
    assert plan.evidence_refs == (f"measured:{provider_id}:tok_s=20.00",)
    assert plan.prefix_cache.backend == "ollama"


def test_hydration_preserves_the_verified_free_cloud_tool_capable_flag() -> None:
    """Regression guard, 2026-08-04: `hydrate_capability_truth_with_benchmarks` rebuilds a fresh
    `ProviderCapabilityTruth` field-by-field instead of using `dataclasses.replace`. The first time
    this was written, `is_verified_free_cloud_tool_capable` was missing from that reconstruction, so
    it silently reset to its dataclass default (False) on every turn where the manifest already had
    a recorded benchmark fact -- which OpenRouter manifests get after any real call
    (`OpenAICompatibleAdapter._record_benchmark_best_effort`). That would have quietly undone the
    drone-role free-cloud priority fix in `local_inference_autopilot.py` the second time a given
    free-cloud provider was ever used, while the FIRST use (never benchmarked yet) kept working --
    exactly the kind of gap that passes on a fresh unit test and fails on a warmed-up runtime.
    """
    provider_id = "openrouter-byok:test-free-8b"
    record_ollama_generate_benchmark(
        provider_id=provider_id,
        model_id="test-free-8b",
        prompt="Write one short paragraph.",
        response_payload={
            "eval_count": 40,
            "eval_duration": 2_000_000_000,
            "load_duration": 120_000_000,
            "prompt_eval_duration": 80_000_000,
        },
    )
    capability = ProviderCapabilityTruth(
        provider_id=provider_id,
        model_id="test-free-8b",
        role_fit="queen",
        context_window=131072,
        tool_support=("structured_json",),
        structured_output_support=True,
        tokens_per_second=0.0,
        ram_budget_gb=0.0,
        vram_budget_gb=0.0,
        quantization="",
        locality="remote",
        privacy_class="remote_provider",
        queue_depth=0,
        max_safe_concurrency=2,
        is_verified_free_cloud_tool_capable=True,
    )
    hydrated = hydrate_capability_truth_with_benchmarks((capability,))
    assert hydrated[0].measurement_source == "local_inference_benchmark", (
        "test assumption broken: this capability must actually go through the benchmark "
        "reconstruction branch, not the early-return no-fact path"
    )
    assert hydrated[0].is_verified_free_cloud_tool_capable is True, (
        "the free-cloud/tool_intent signal must survive benchmark hydration, or the drone-role "
        "priority fix silently stops applying after the first recorded call"
    )


def test_local_inference_ledger_failure_keeps_manifest_truth_available() -> None:
    capability = _capability("qwen3:8b", role_fit="drone", tokens_per_second=18.0)

    with mock.patch(
        "core.local_inference_evidence.latest_local_inference_benchmarks",
        side_effect=RuntimeError("db locked"),
    ):
        hydrated = hydrate_capability_truth_with_benchmarks((capability,))

    assert hydrated == (capability,)
    assert hydrated[0].measurement_source == "manifest"


def test_memory_router_prioritizes_autopilot_lane_and_emits_runtime_plan() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    heavy = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:32b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
                "tokens_per_second": 0.3,
                "vram_budget_gb": 4.0,
                "tool_support": ["structured_json", "code_complex"],
        },
    )
    verifier = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
                "tokens_per_second": 11.4,
                "vram_budget_gb": 4.0,
                "tool_support": ["structured_json", "code_complex"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-test-stream", events.append)

    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.estimate_cost_class.return_value = "free_local"
    adapter.get_license_metadata.return_value = {}
    adapter.run_text_task.return_value = ModelResponse(output_text="Patch plan ready.", confidence=0.78)

    task = SimpleNamespace(task_id="autopilot-task", task_summary="patch this local runtime safely")
    interpretation = SimpleNamespace(reconstructed_text="patch this local runtime safely")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.2,
        report=context_report,
    )

    try:
        # This asserts the PLANNER's preference (throughput-ranked verifier), so the host hardware
        # must be neutralized: with a real device probe, a GPU-less runner computes a RAM footprint
        # for the 14B that exceeds its free RAM, marks it hardware-unfit, and the planner falls back
        # to the 32B -- making the outcome depend on the CI runner. Force the probe to "unknown" so the
        # selection is decided by the plan, not the box it runs on (matches the mocked free-VRAM=None).
        with mock.patch("core.provider_routing._device_probe", return_value=None), mock.patch(
            "core.memory_first_router._cached_free_vram_gb", return_value=None
        ), mock.patch(
            "core.memory_first_router.rank_provider_candidates", return_value=[heavy, verifier]
        ), mock.patch.object(
            router.registry,
            "build_adapter",
            return_value=adapter,
        ) as build_adapter:
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "debugging"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-task-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-test-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-test-stream")

    assert build_adapter.call_args.args[0].model_name == "qwen3:14b"
    assert decision.provider_id == verifier.provider_id
    assert decision.details["ranked_candidates"][0] == verifier.provider_id
    assert decision.details["autopilot_plan"]["selected_model"] == "qwen3:14b"
    assert decision.details["autopilot_plan"]["lane"] == "deep"
    assert events[0]["event_type"] == "model_routing_started"
    assert events[0]["autopilot_plan"]["selected_model"] == "qwen3:14b"
    proof_events = [event for event in events if event["event_type"] == "model_lane_proof"]
    assert proof_events
    proof = proof_events[-1]
    assert proof["schema"] == "vool.model_lane_proof.v1"
    assert proof["phase"] == "completed"
    assert proof["lane"] == "deep"
    assert proof["provider_id"] == verifier.provider_id
    assert proof["actual_adapter_provider_id"] == verifier.provider_id
    assert proof["actual_adapter_model_id"] == "qwen3:14b"
    assert proof["measurement_source"] == "manifest"
    assert proof["verifier_status"] == "blocked"
    assert proof["verifier_provider_id"] == ""
    assert proof["verifier_model_id"] == ""
    assert proof["kv_cache_status"] == "ollama=not_supported_keep_alive_only"
    assert proof["speculative_status"] == "inactive"
    assert proof["eagle_status"] == "unsupported_by_backend"
    assert decision.details["lane_proof"]["schema"] == "vool.model_lane_proof.v1"


def test_memory_router_blocks_explicit_heavy_plan_without_adapter_fallback() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=None,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": None,
            "selected_model": None,
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "", "supported": False},
            "warnings": ["explicit_heavy_lane_unavailable"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-block-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-block-task", task_summary="use 35b heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the 35b heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=AssertionError("explicit heavy block must not invoke fallback adapter"),
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-block-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-block-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-block-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "autopilot_blocked"
    assert decision.used_model is False
    assert proof["phase"] == "blocked"
    assert proof["lane"] == "deep"
    assert proof["fallback_reason"] == "explicit_heavy_lane_unavailable"


def test_memory_router_blocks_missing_planned_heavy_manifest_without_fallback() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id="ollama-local:qwen3.5:35b-a3b",
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": "ollama-local:qwen3.5:35b-a3b",
            "selected_model": "qwen3.5:35b-a3b",
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            "prefix_cache": {"backend": "ollama", "supported": False},
            "warnings": [],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-missing-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-missing-task", task_summary="use 35b heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the 35b heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=AssertionError("missing planned heavy provider must not invoke fallback adapter"),
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-missing-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-missing-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-missing-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "autopilot_blocked"
    assert proof["phase"] == "blocked"
    assert proof["planned_model_id"] == "qwen3.5:35b-a3b"
    assert proof["fallback_reason"] == "explicit_heavy_lane_unavailable"


def test_memory_router_does_not_fallback_after_planned_heavy_lane_failure() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    heavy = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen2.5:32b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen2.5",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 0.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    fallback = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    heavy_adapter = mock.Mock()
    heavy_adapter.health_check.return_value = {"ok": True}
    heavy_adapter.run_structured_task.side_effect = RuntimeError("too slow")
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=heavy.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": heavy.provider_id,
            "selected_model": heavy.model_name,
            "verifier_required": True,
            "verifier_provider_id": None,
            "verifier_model": None,
            # This scenario's own interpretation text ("use the heavy local lane") genuinely
            # requests a heavy model -- a real (non-mocked) build_local_inference_autopilot_plan
            # call would set explicit_heavy=True here via _explicit_heavy_requested's "heavy"
            # marker. R1 repair: _planned_heavy_manifest_failed now requires this to be true
            # before applying the no-smaller-fallback contract, so the fixture must carry it to
            # keep asserting the behavior this test actually means to cover.
            "explicit_heavy": True,
            "prefix_cache": {"backend": "ollama", "supported": False},
            "warnings": [],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-heavy-failed-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-heavy-failed-task", task_summary="use heavy local lane")
    interpretation = SimpleNamespace(reconstructed_text="use the heavy local lane")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
        if manifest.provider_id == heavy.provider_id:
            return heavy_adapter
        raise AssertionError("planned heavy failure must not invoke smaller fallback adapter")

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[heavy, fallback]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=build_adapter,
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "system_design"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-heavy-failed-hash",
                task_kind="action_plan",
                output_mode="action_plan",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-heavy-failed-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-heavy-failed-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert decision.source == "explicit_heavy_lane_failed"
    assert proof["phase"] == "failed"
    assert proof["provider_id"] == heavy.provider_id
    assert proof["fallback_reason"] == "explicit_heavy_lane_failed:too slow"


def test_auto_selected_large_cloud_model_failure_still_falls_back_to_healthy_local() -> None:
    """R1 -- ARGUS-confirmed defect: Auto routing can pick a large model purely on ranking merit
    (a free cloud candidate winning normal scoring), with no heavy model ever requested by the
    user. Before this repair, that alone -- the SELECTED model's id string parsing >=24B -- was
    enough to abort the whole fallback loop on failure, skipping a healthy ranked local candidate
    behind it. explicit_heavy=False (Auto never asked for this) must mean the ordinary fallback
    to candidate B still happens."""
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    large_free_cloud = ModelProviderManifest(
        provider_name="openrouter-free",
        model_name="mistralai/mistral-small-3.2-24b-instruct:free",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        weight_location="external",
        runtime_dependency="remote-openai-compatible-provider",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1"},
        metadata={"deployment_class": "cloud", "orchestration_role": "drone", "cost_class": "free_local"},
    )
    healthy_local = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={"deployment_class": "local", "orchestration_role": "queen", "tokens_per_second": 12.0},
    )
    large_adapter = mock.Mock()
    large_adapter.health_check.return_value = {"ok": True}
    large_adapter.run_text_task.side_effect = RuntimeError("provider_http_500:internal server error")
    local_adapter = mock.Mock()
    local_adapter.health_check.return_value = {"ok": True}
    local_adapter.get_license_metadata.return_value = {}
    local_adapter.run_text_task.return_value = ModelResponse(
        output_text="The fallback model answered.",
        provider_id=healthy_local.provider_id,
        model_name=healthy_local.model_name,
        output_mode="plain_text",
    )
    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=large_free_cloud.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "daily",
            "selected_provider_id": large_free_cloud.provider_id,
            "selected_model": large_free_cloud.model_name,
            "verifier_required": False,
            "verifier_provider_id": None,
            "verifier_model": None,
            # The load-bearing fact this test exists to prove: Auto picked this model on ranking
            # merit alone, nobody asked for a heavy model this turn.
            "explicit_heavy": False,
            "prefix_cache": {"backend": "none", "supported": False},
            "warnings": [],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-auto-large-cloud-stream", events.append)
    task = SimpleNamespace(task_id="autopilot-auto-large-cloud-task", task_summary="what time is it")
    interpretation = SimpleNamespace(reconstructed_text="what time is it")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
        if manifest.provider_id == large_free_cloud.provider_id:
            return large_adapter
        if manifest.provider_id == healthy_local.provider_id:
            return local_adapter
        raise AssertionError(f"unexpected manifest {manifest.provider_id}")

    try:
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates", return_value=[large_free_cloud, healthy_local]
        ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "daily"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-auto-large-cloud-hash",
                task_kind="chat",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="drone",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-auto-large-cloud-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-auto-large-cloud-stream")

    assert local_adapter.run_text_task.called, "candidate B (the healthy local model) must have been attempted"
    assert decision.source != "explicit_heavy_lane_failed"
    assert decision.used_model is True
    assert decision.model_name == healthy_local.model_name


def test_explicit_pin_on_a_heavy_model_that_fails_never_silently_substitutes() -> None:
    """R1 control -- distinct from the Auto case above, and deliberately NOT relying on the R1
    explicit_heavy gate to prove it. `explicit_heavy=False` here on purpose: an explicit pin (the
    user/caller named THIS exact model, not Auto ranking) that fails must never silently answer
    as a different model, heavy or not, and that guarantee comes from
    core.memory_first_router.MemoryFirstRouter._execute_provider_task's own requested_manifest
    narrowing (ranked_manifests collapses to exactly the pinned provider) -- a completely
    separate mechanism from the R1 gate, and one this repair must not touch or weaken."""
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    heavy = router.registry.register_manifest(
        {
            "provider_name": "ollama-local",
            "model_name": "qwen2.5:32b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://ollama.com/library/qwen2.5",
            "weight_location": "external",
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "enabled": True,
            "metadata": {"deployment_class": "local", "orchestration_role": "queen"},
        }
    )
    healthy_local = router.registry.register_manifest(
        {
            "provider_name": "ollama-local",
            "model_name": "qwen3:8b",
            "source_type": "http",
            "adapter_type": "local_qwen_provider",
            "license_name": "Apache-2.0",
            "license_reference": "https://ollama.com/library/qwen3",
            "weight_location": "external",
            "runtime_dependency": "ollama",
            "capabilities": ["summarize", "format", "structured_json"],
            "runtime_config": {"base_url": "http://127.0.0.1:11434"},
            "enabled": True,
            "metadata": {"deployment_class": "local", "orchestration_role": "queen"},
        }
    )
    heavy_adapter = mock.Mock()
    heavy_adapter.health_check.return_value = {"ok": True}
    heavy_adapter.run_text_task.side_effect = RuntimeError("too slow")
    local_adapter = mock.Mock()
    local_adapter.health_check.return_value = {"ok": True}
    local_adapter.run_text_task.return_value = ModelResponse(
        output_text="B answered instead.", provider_id=healthy_local.provider_id,
        model_name=healthy_local.model_name, output_mode="plain_text",
    )
    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=heavy.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "daily",
            "selected_provider_id": heavy.provider_id,
            "selected_model": heavy.model_name,
            "verifier_required": False,
            "verifier_provider_id": None,
            "verifier_model": None,
            # Deliberately False: this test proves the PIN mechanism alone (ranked_manifests
            # narrowed to the one requested provider) prevents substitution, independent of
            # whether the R1 explicit_heavy gate would also have fired.
            "explicit_heavy": False,
            "prefix_cache": {"backend": "ollama", "supported": False},
            "warnings": [],
        },
    )
    task = SimpleNamespace(task_id="autopilot-explicit-pin-task", task_summary="use qwen2.5:32b specifically")
    interpretation = SimpleNamespace(reconstructed_text="use qwen2.5:32b specifically")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    def build_adapter(manifest) -> mock.Mock:
        if manifest.provider_id == heavy.provider_id:
            return heavy_adapter
        raise AssertionError(f"explicit pin must never invoke a different manifest, got {manifest.provider_id}")

    with mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=[heavy, healthy_local]
    ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
        "core.memory_first_router.build_local_inference_autopilot_plan",
        return_value=fake_plan,
    ):
        decision = router._execute_provider_task(
            task=task,
            classification={"task_class": "daily"},
            interpretation=interpretation,
            context_result=context_result,
            persona=SimpleNamespace(),
            task_hash="autopilot-explicit-pin-hash",
            task_kind="chat",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            source_context={"requested_model": heavy.model_name, "surface": "openclaw"},
        )

    assert not local_adapter.run_text_task.called, "must never silently substitute a different model"
    assert decision.used_model is not True
    assert decision.model_name != healthy_local.model_name


def test_memory_router_marks_planned_actual_lane_mismatch_as_failed_proof() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    actual = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "drone",
            "tokens_per_second": 18.0,
            "tool_support": ["structured_json"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-mismatch-stream", events.append)

    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.supports_streaming.return_value = False
    adapter.estimate_cost_class.return_value = "free_local"
    adapter.get_license_metadata.return_value = {}
    adapter.run_text_task.return_value = ModelResponse(output_text="Fallback answer.", confidence=0.72)
    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id="ollama-local:qwen3:14b",
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "daily",
            "selected_provider_id": "ollama-local:qwen3:14b",
            "selected_model": "qwen3:14b",
            "verifier_required": False,
            "verifier_provider_id": None,
            "prefix_cache": {
                "backend": "ollama",
                "supported": False,
            },
        },
    )
    task = SimpleNamespace(task_id="autopilot-mismatch-task", task_summary="answer simply")
    interpretation = SimpleNamespace(reconstructed_text="answer simply")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[actual]), mock.patch.object(
            router.registry,
            "build_adapter",
            return_value=adapter,
        ), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "quick_answer"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-mismatch-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="auto",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-mismatch-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-mismatch-stream")

    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert proof["phase"] == "failed"
    assert proof["mismatch"] is True
    assert proof["failure_reason"] == "planned_adapter_mismatch"
    assert proof["planned_provider_id"] == "ollama-local:qwen3:14b"
    assert proof["actual_adapter_provider_id"] == actual.provider_id
    assert decision.details["lane_proof"]["mismatch"] is True


def test_memory_router_invokes_independent_verifier_lane_before_final_proof() -> None:
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    primary = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:8b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "drone",
            "tokens_per_second": 18.0,
            "tool_support": ["structured_json"],
        },
    )
    verifier = ModelProviderManifest(
        provider_name="ollama-local",
        model_name="qwen3:14b",
        source_type="http",
        adapter_type="local_qwen_provider",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "format", "structured_json", "code_complex"],
        runtime_config={"base_url": "http://127.0.0.1:11434"},
        metadata={
            "deployment_class": "local",
            "orchestration_role": "queen",
            "tokens_per_second": 11.4,
            "tool_support": ["structured_json", "code_complex"],
        },
    )
    events: list[dict] = []
    register_runtime_event_sink("autopilot-verifier-stream", events.append)

    primary_adapter = mock.Mock()
    primary_adapter.health_check.return_value = {"ok": True}
    primary_adapter.supports_streaming.return_value = False
    primary_adapter.estimate_cost_class.return_value = "free_local"
    primary_adapter.get_license_metadata.return_value = {}
    primary_adapter.run_text_task.return_value = ModelResponse(output_text="Primary patch plan.", confidence=0.75)
    verifier_adapter = mock.Mock()
    verifier_adapter.health_check.return_value = {"ok": True}
    verifier_adapter.supports_streaming.return_value = False
    verifier_adapter.run_text_task.return_value = ModelResponse(
        output_text="VERDICT: PASS - no blockers found.",
        confidence=0.7,
    )
    fake_plan = SimpleNamespace(
        lane="deep",
        selected_provider_id=primary.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1",
            "lane": "deep",
            "selected_provider_id": primary.provider_id,
            "selected_model": primary.model_name,
            "verifier_required": True,
            "verifier_provider_id": verifier.provider_id,
            "verifier_model": verifier.model_name,
            "prefix_cache": {"backend": "ollama", "supported": False},
        },
    )
    task = SimpleNamespace(task_id="autopilot-verifier-task", task_summary="review a risky patch plan")
    interpretation = SimpleNamespace(reconstructed_text="review a risky patch plan")
    context_report = SimpleNamespace(retrieval_confidence=0.2)
    context_report.to_dict = lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []}
    context_result = SimpleNamespace(retrieval_confidence_score=0.2, report=context_report)

    try:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[primary, verifier]), mock.patch.object(
            router.registry,
            "build_adapter",
            side_effect=[primary_adapter, verifier_adapter],
        ) as build_adapter, mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan",
            return_value=fake_plan,
        ):
            decision = router._execute_provider_task(
                task=task,
                classification={"task_class": "debugging"},
                interpretation=interpretation,
                context_result=context_result,
                persona=SimpleNamespace(),
                task_hash="autopilot-verifier-hash",
                task_kind="normalization_assist",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                source_context={"runtime_event_stream_id": "autopilot-verifier-stream", "surface": "openclaw"},
            )
    finally:
        unregister_runtime_event_sink("autopilot-verifier-stream")

    assert [call.args[0].provider_id for call in build_adapter.call_args_list] == [
        primary.provider_id,
        verifier.provider_id,
    ]
    assert verifier_adapter.run_text_task.call_count == 1
    verifier_events = [event for event in events if event["event_type"].startswith("model_lane_verifier_")]
    assert [event["event_type"] for event in verifier_events] == [
        "model_lane_verifier_started",
        "model_lane_verifier_completed",
    ]
    proof = [event for event in events if event["event_type"] == "model_lane_proof"][-1]
    assert proof["phase"] == "completed"
    assert proof["lane"] == "deep"
    assert proof["provider_id"] == primary.provider_id
    assert proof["verifier_status"] == "independent_completed"
    assert decision.details["lane_proof"]["verifier_status"] == "independent_completed"


# --------------------------------------------------------------------------------------
# Finding, 2026-08-04: `_select_primary_capability`'s non-explicit-heavy `viable` filter (the
# path every output_mode=="tool_intent" turn hits, since `_resolve_lane` routes tool_intent
# straight to the tiny lane) required `model_parameter_billions(item.model_id) < 24.0` for EVERY
# candidate regardless of locality. A remote API candidate loads nothing onto local hardware, so
# a filter meant to protect local VRAM wrongly excluded it before any ranking or free-cloud check
# ever ran -- confirmed live with the OpenRouter free pick "nvidia/nemotron-3-ultra-550b-a55b:free"
# (55B active parameters, >= 24.0).
# --------------------------------------------------------------------------------------


def test_tiny_lane_viable_filter_does_not_exclude_an_oversized_remote_candidate() -> None:
    remote = _remote_capability("nvidia/nemotron-3-ultra-550b-a55b:free")
    selected = _select_primary_capability(
        (remote,),
        lane="tiny",
        provider_role="drone",
        explicit_heavy=False,
        requested_heavy_marker=None,
    )
    assert selected is not None and selected.model_id == "nvidia/nemotron-3-ultra-550b-a55b:free", (
        "a >=24B REMOTE candidate must still be selectable for tool_intent -- the local VRAM-fit "
        "size cap must not apply to a candidate that needs no local hardware at all"
    )


def test_tiny_lane_prefers_verified_free_cloud_over_local_when_both_are_viable() -> None:
    """Superseded 2026-08-04 (final-skeptic pass): this test used to pin "local always wins the
    tiny lane" as the correct default. That default was live-confirmed to contradict the
    operator's own explicit priority order ("the best of Cloud and local llm (priority is cloud
    free openrouter ai's)") -- `_role_bonus` in `core/provider_routing.py` was fixed to prefer a
    verified-free, tool_intent-capable remote manifest, but `_select_primary_capability` is what
    ACTUALLY decides the live tool_intent pick (`_resolve_lane` always routes tool_intent to
    "tiny", and `_can_prioritize_autopilot_selection` unconditionally promotes this function's
    pick whenever `allow_paid_fallback` is False, which it always is for role=="drone") -- so
    fixing `_role_bonus` alone left local winning 100% of real unpinned drives. This test now pins
    the corrected default: a verified-free, tool_intent-capable remote candidate wins over a
    comparable local one when both are viable.
    """
    import dataclasses

    local = _capability("qwen3:8b", role_fit="drone", tokens_per_second=16.4)
    remote = dataclasses.replace(
        _remote_capability("nvidia/nemotron-3-ultra-550b-a55b:free"),
        is_verified_free_cloud_tool_capable=True,
    )
    selected = _select_primary_capability(
        (local, remote),
        lane="tiny",
        provider_role="drone",
        explicit_heavy=False,
        requested_heavy_marker=None,
    )
    assert selected is not None and selected.model_id == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_tiny_lane_does_not_boost_an_oversized_remote_candidate_without_the_verified_flag() -> None:
    """The bonus is keyed on `is_verified_free_cloud_tool_capable` -- an ordinary (unverified, or
    not tool_intent-capable) remote candidate must not out-rank a real local drone model just for
    being remote. Regression guard for the fix above: without this, a mis-set or stale flag would
    silently flip every remote candidate to the front, not only a genuinely free, capable one."""
    local = _capability("qwen3:8b", role_fit="drone", tokens_per_second=16.4)
    remote = _remote_capability("nvidia/nemotron-3-ultra-550b-a55b:free")  # flag defaults to False
    selected = _select_primary_capability(
        (local, remote),
        lane="tiny",
        provider_role="drone",
        explicit_heavy=False,
        requested_heavy_marker=None,
    )
    assert selected is not None and selected.model_id == "qwen3:8b"
