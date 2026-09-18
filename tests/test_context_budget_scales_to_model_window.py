"""The retrieved-context budgets grow to the window of the model that will answer.

`scale_budget_to_window` landed (750fbb8) reading the window from `source_context` -- and nothing
on any live path ever wrote those keys, so every real turn kept the smallest-model budgets: the
scaler was a guaranteed no-op. The answering model itself is resolved only AFTER the context
exists (the loader runs before `memory_router.resolve`), so the fix pre-resolves the window: the
router runs the same candidate ranking `resolve` will run later, reads the window off the planned
candidates, and stamps it into the loader's copy of the context.

These tests pin all four pieces:

* the scaling arithmetic and its never-shrink guarantee (`core/context_budgeter.py`)
* the window-key contract the loader and router share (`available_prompt_tokens`)
* the loader honouring a stamped window end to end (`core/tiered_context_loader.py`)
* the router's pre-resolution and both live call sites passing the stamped context
  (`core/memory_first_router.py`, `core/agent_runtime/chat_surface.py`,
  `core/agent_runtime/turn_reasoning.py`)
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

from core.context_budgeter import (
    ContextBudget,
    available_prompt_tokens,
    normalize_budget,
    scale_budget_to_window,
)
from core.context_namespace import ensure_chat_namespace
from core.human_input_adapter import HumanInputInterpretation, adapt_user_input
from core.identity_manager import load_active_persona
from core.memory_first_router import MemoryFirstRouter, ModelExecutionDecision
from core.model_registry import ModelRegistry
from core.task_router import classify, create_task_record, model_execution_profile
from core.tiered_context_loader import TieredContextLoader
from storage.db import get_connection
from storage.migrations import run_migrations

_DECLARED = ContextBudget()  # total 900, bootstrap 180, relevant 520, cold 0


# --------------------------------------------------------------------------------------
# The scaling arithmetic and its never-shrink guarantee
# --------------------------------------------------------------------------------------


class TestScaleBudgetToWindow:
    def test_an_unknown_window_leaves_the_declared_budget_untouched(self) -> None:
        for window in (None, 0, -8192):
            assert scale_budget_to_window(_DECLARED, available_prompt_tokens=window) == _DECLARED

    def test_a_window_too_small_to_add_room_changes_nothing(self) -> None:
        # room = window * 0.25; growth requires room STRICTLY above the declared total.
        for window in (2000, 3600):
            assert scale_budget_to_window(_DECLARED, available_prompt_tokens=window) == _DECLARED

    def test_a_qwen3_8b_window_grows_every_layer_proportionally(self) -> None:
        scaled = scale_budget_to_window(_DECLARED, available_prompt_tokens=24576)
        room = int(24576 * 0.25)
        factor = room / _DECLARED.total_tokens
        assert scaled.total_tokens == room == 6144
        assert scaled.bootstrap_tokens == int(_DECLARED.bootstrap_tokens * factor)
        assert scaled.relevant_tokens == int(_DECLARED.relevant_tokens * factor)
        assert scaled.cold_tokens == 0
        assert scaled.bootstrap_tokens > _DECLARED.bootstrap_tokens
        assert scaled.relevant_tokens > _DECLARED.relevant_tokens
        assert scaled.bootstrap_tokens + scaled.relevant_tokens + scaled.cold_tokens <= scaled.total_tokens
        # Item-count gates are per-layer shape, not size -- scaling must not touch them.
        assert scaled.max_bootstrap_items == _DECLARED.max_bootstrap_items
        assert scaled.max_relevant_items == _DECLARED.max_relevant_items
        assert scaled.max_cold_items == _DECLARED.max_cold_items

    def test_a_200k_window_takes_the_declared_share_of_the_prompt(self) -> None:
        scaled = scale_budget_to_window(_DECLARED, available_prompt_tokens=200_000)
        assert scaled.total_tokens == 50_000
        factor = 50_000 / _DECLARED.total_tokens
        assert scaled.bootstrap_tokens == int(_DECLARED.bootstrap_tokens * factor)
        assert scaled.relevant_tokens == int(_DECLARED.relevant_tokens * factor)

    def test_no_window_ever_shrinks_a_layer(self) -> None:
        for window in (0, 100, 3600, 4096, 8192, 24576, 131_072, 200_000):
            scaled = scale_budget_to_window(_DECLARED, available_prompt_tokens=window)
            assert scaled.total_tokens >= _DECLARED.total_tokens, window
            assert scaled.bootstrap_tokens >= _DECLARED.bootstrap_tokens, window
            assert scaled.relevant_tokens >= _DECLARED.relevant_tokens, window
            assert scaled.cold_tokens >= _DECLARED.cold_tokens, window

    def test_a_scaled_budget_survives_normalization_intact(self) -> None:
        # The loader always normalizes after scaling; the pair must not fight each other.
        scaled = normalize_budget(scale_budget_to_window(_DECLARED, available_prompt_tokens=131_072))
        assert scaled.total_tokens == int(131_072 * 0.25)
        assert scaled.relevant_tokens + scaled.cold_tokens <= scaled.total_tokens - scaled.bootstrap_tokens


# --------------------------------------------------------------------------------------
# The window-key contract shared by the loader and the router
# --------------------------------------------------------------------------------------


class TestAvailablePromptTokens:
    def test_reads_each_known_key(self) -> None:
        for key in ("available_prompt_tokens", "model_context_window", "context_window", "num_ctx"):
            assert available_prompt_tokens({key: 8192}) == 8192

    def test_the_most_specific_key_wins(self) -> None:
        assert (
            available_prompt_tokens({"available_prompt_tokens": 1000, "model_context_window": 9999}) == 1000
        )

    def test_string_values_parse(self) -> None:
        assert available_prompt_tokens({"num_ctx": "8192"}) == 8192

    def test_garbage_in_one_key_falls_through_to_the_next(self) -> None:
        assert available_prompt_tokens({"model_context_window": "banana", "num_ctx": 4096}) == 4096

    def test_nothing_carried_means_do_not_scale(self) -> None:
        assert available_prompt_tokens(None) == 0
        assert available_prompt_tokens({}) == 0
        assert available_prompt_tokens({"model_context_window": 0}) == 0
        assert available_prompt_tokens({"model_context_window": -4096}) == 0


# --------------------------------------------------------------------------------------
# The loader honours a stamped window end to end
# --------------------------------------------------------------------------------------


def _interpretation(text: str) -> HumanInputInterpretation:
    return HumanInputInterpretation(
        raw_text=text,
        normalized_text=text,
        reconstructed_text=text,
        intent_mode="request",
        topic_hints=[],
        reference_targets=[],
        understanding_confidence=0.72,
        quality_flags=[],
        needs_clarification=False,
        turn_id=None,
    )


class TestLoaderScalesToStampedWindow:
    def setup_method(self) -> None:
        run_migrations()

    def _load(self, source_context: dict) -> object:
        task = create_task_record("summarize the deployment history of this workspace")
        interpretation = _interpretation(task.task_summary)
        classification = classify(task.task_summary, context=interpretation.as_context())
        session_id = f"window-scale-{uuid.uuid4().hex[:12]}"
        ensure_chat_namespace(session_id, grant_confirmed_profile=True)
        return TieredContextLoader().load(
            task=task,
            classification=classification,
            interpretation=interpretation,
            persona=load_active_persona("default"),
            session_id=session_id,
            source_context=source_context,
        )

    def test_a_stamped_window_scales_the_report_budgets(self) -> None:
        baseline = self._load({"surface": "api"})
        scaled = self._load({"surface": "api", "model_context_window": 200_000})
        assert scaled.report.total_context_budget == 50_000
        assert scaled.report.total_context_budget > baseline.report.total_context_budget
        assert scaled.report.bootstrap_budget > baseline.report.bootstrap_budget
        assert scaled.report.relevant_budget > baseline.report.relevant_budget

    def test_an_absent_or_zero_window_keeps_the_declared_budgets(self) -> None:
        absent = self._load({"surface": "api"})
        zero = self._load({"surface": "api", "model_context_window": 0})
        assert absent.report.total_context_budget == zero.report.total_context_budget
        assert absent.report.bootstrap_budget == zero.report.bootstrap_budget


# --------------------------------------------------------------------------------------
# The router pre-resolves the window before the loader runs
# --------------------------------------------------------------------------------------


class TestPlannedContextWindow:
    def setup_method(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM model_provider_manifests")
            conn.commit()
        finally:
            conn.close()
        self.registry = ModelRegistry()
        self.router = MemoryFirstRouter(self.registry)

    def _manifest(self, *, provider_name: str, model_name: str, context_window: int | None):
        metadata: dict = {"orchestration_role": "drone"}
        if context_window is not None:
            metadata["context_window"] = int(context_window)
        return self.registry.register_manifest(
            {
                "provider_name": provider_name,
                "model_name": model_name,
                "source_type": "http",
                "adapter_type": "local_qwen_provider",
                "license_name": "Apache-2.0",
                "license_reference": "https://www.apache.org/licenses/LICENSE-2.0",
                "weight_location": "user-supplied",
                "weights_bundled": False,
                "redistribution_allowed": True,
                "runtime_dependency": "openai-compatible-local-runtime",
                "capabilities": ["summarize", "structured_json"],
                "runtime_config": {"base_url": "http://127.0.0.1:11434"},
                "enabled": True,
                "metadata": metadata,
            }
        )

    def test_an_explicit_pick_is_the_turns_window(self) -> None:
        picked = self._manifest(provider_name="paid-cloud", model_name="big-cloud", context_window=131_072)
        planned = self.router.planned_context_window_tokens(
            classification={"task_class": "chat_conversation"},
            source_context={"surface": "api", "requested_model": picked.provider_id},
        )
        assert planned == 131_072

    def test_the_auto_lane_plans_for_the_smallest_ranked_candidate(self) -> None:
        big = self._manifest(provider_name="local-big", model_name="qwen3:8b", context_window=24_576)
        small = self._manifest(provider_name="local-small", model_name="llama3.2:3b", context_window=8_192)
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[big, small],
        ) as rank:
            planned = self.router.planned_context_window_tokens(
                classification={"task_class": "chat_conversation"},
                source_context={"surface": "api"},
            )
        assert planned == 8_192
        # The plan must mirror the ranking resolve() will run later -- same profile-derived
        # knobs, and no paid candidates because the auto lane never holds a reservation.
        profile = model_execution_profile("chat_conversation", chat_surface=True)
        assert rank.call_args.kwargs["task_kind"] == profile["task_kind"]
        assert rank.call_args.kwargs["output_mode"] == profile["output_mode"]
        assert rank.call_args.kwargs["allow_paid_fallback"] is False
        assert rank.call_args.kwargs["swarm_size"] == 4
        assert rank.call_args.kwargs["min_trust"] == 0.45
        assert rank.call_args.kwargs["enforce_hardware_fit"] is True

    def test_a_non_text_candidate_never_sets_the_window(self) -> None:
        text = self._manifest(provider_name="local-text", model_name="qwen3:8b", context_window=24_576)
        vision = self._manifest(provider_name="local-vision", model_name="llava:7b", context_window=2_048)
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[text, vision],
        ):
            planned = self.router.planned_context_window_tokens(
                classification={"task_class": "chat_conversation"},
                source_context={"surface": "api"},
            )
        assert planned == 24_576

    def test_any_candidate_with_an_unknown_window_disables_scaling(self) -> None:
        known = self._manifest(provider_name="local-known", model_name="qwen3:8b", context_window=24_576)
        unknown = self._manifest(provider_name="local-unknown", model_name="mystery:7b", context_window=None)
        with mock.patch(
            "core.memory_first_router.rank_provider_candidates",
            return_value=[known, unknown],
        ):
            planned = self.router.planned_context_window_tokens(
                classification={"task_class": "chat_conversation"},
                source_context={"surface": "api"},
            )
        assert planned == 0

    def test_no_candidates_means_no_scaling(self) -> None:
        with mock.patch("core.memory_first_router.rank_provider_candidates", return_value=[]):
            planned = self.router.planned_context_window_tokens(
                classification={"task_class": "chat_conversation"},
                source_context={"surface": "api"},
            )
        assert planned == 0

    def test_stamp_writes_the_planned_window_without_mutating_the_caller(self) -> None:
        original = {"surface": "api"}
        with mock.patch.object(
            self.router, "planned_context_window_tokens", return_value=24_576
        ):
            stamped = self.router.stamp_planned_context_window(
                original, classification={"task_class": "chat_conversation"}
            )
        assert stamped["model_context_window"] == 24_576
        assert "model_context_window" not in original

    def test_stamp_keeps_a_window_the_turn_already_carries(self) -> None:
        with mock.patch.object(self.router, "planned_context_window_tokens") as planner:
            stamped = self.router.stamp_planned_context_window(
                {"surface": "api", "available_prompt_tokens": 1234},
                classification={"task_class": "chat_conversation"},
            )
        planner.assert_not_called()
        assert "model_context_window" not in stamped
        assert stamped["available_prompt_tokens"] == 1234

    def test_stamp_never_costs_the_turn_when_planning_fails(self) -> None:
        with mock.patch.object(
            self.router, "planned_context_window_tokens", side_effect=RuntimeError("registry down")
        ):
            stamped = self.router.stamp_planned_context_window(
                {"surface": "api"}, classification={"task_class": "chat_conversation"}
            )
        assert "model_context_window" not in stamped
        assert stamped["surface"] == "api"


# --------------------------------------------------------------------------------------
# Both live call sites pass the stamped context to the loader
# --------------------------------------------------------------------------------------


def test_the_grounded_turn_loads_context_with_the_planned_window(make_agent) -> None:
    agent = make_agent()
    agent.memory_router.planned_context_window_tokens = mock.Mock(return_value=131_072)
    decision = ModelExecutionDecision(
        source="provider",
        task_hash="h",
        provider_id="local:qwen",
        used_model=True,
        output_text="The codec repeats its header on every frame.",
    )
    _drive_grounded_turn(agent, decision=decision, asked="why does the codec repeat its header?")
    stamped = agent.context_loader.load.call_args.kwargs["source_context"]
    assert stamped["model_context_window"] == 131_072


def test_the_chat_surface_wording_turn_loads_context_with_the_planned_window(make_agent) -> None:
    from core.agent_runtime.chat_surface import chat_surface_model_wording_result

    agent = make_agent()
    agent.memory_router.planned_context_window_tokens = mock.Mock(return_value=65_536)
    agent.memory_router.resolve = mock.Mock(
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="h",
            provider_id="local:qwen",
            used_model=True,
            output_text="Here is the answer.",
        )
    )
    agent._decorate_chat_response = mock.Mock(side_effect=lambda result, **_: str(result.text or ""))
    session_id = f"window-wiring-{uuid.uuid4().hex[:12]}"
    chat_surface_model_wording_result(
        agent,
        session_id=session_id,
        user_input="tell me about the roadmap",
        source_context={"surface": "api", "platform": "api"},
        persona=load_active_persona(agent.persona_id),
        interpretation=adapt_user_input("tell me about the roadmap", session_id=session_id),
        task_class="chat_conversation",
        response_class=agent.ResponseClass.GENERIC_CONVERSATION,
        reason="test-window-wiring",
        model_input="tell me about the roadmap",
        fallback_response="fallback",
    )
    stamped = agent.context_loader.load.call_args.kwargs["source_context"]
    assert stamped["model_context_window"] == 65_536


def _drive_grounded_turn(agent, *, decision, asked: str) -> None:
    """Run the real grounded turn, stubbing only what needs a network or a database.

    Mirrors the harness in test_token_usage_is_measured_not_guessed.py: the loader is already a
    Mock (make_agent), so the assertion under test is about the `source_context` the REAL
    turn_reasoning code hands it.
    """

    adaptive = SimpleNamespace(
        enabled=False,
        tool_gap_note="",
        admitted_uncertainty=False,
        notes=[],
        reason="not_needed",
        strategy="none",
        actions_taken=[],
        to_dict=lambda: {"enabled": False, "reason": "not_needed"},
    )
    task = SimpleNamespace(
        task_id="task-window-wiring",
        task_summary=asked,
        environment_os="darwin",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}

    agent._should_frontload_curiosity = mock.Mock(return_value=False)
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive)
    agent.memory_router.resolve = mock.Mock(return_value=decision)
    agent.media_pipeline.analyze = mock.Mock(
        return_value=SimpleNamespace(
            used_provider=False,
            provider_id="",
            candidate_id="",
            reason="no_media",
            evidence_items=[],
            analysis_text="",
        )
    )
    agent._collect_live_web_notes = mock.Mock(return_value=[])
    agent._web_note_plan_candidates = mock.Mock(return_value=[])
    agent._default_gate = mock.Mock(
        return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False)
    )
    agent._maybe_publish_public_task = mock.Mock(return_value={})
    agent._store_local_shard = mock.Mock()
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()
    agent._decorate_chat_response = mock.Mock(side_effect=lambda result, **_: str(result.text or ""))

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch("core.agent_runtime.agent.build_media_context_snippets", return_value=[]), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72, evidence_sources=[])
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        agent._execute_grounded_turn(
            task=task,
            effective_input=asked,
            classification=classification,
            interpreted=adapt_user_input(asked, session_id="window-wiring-grounded"),
            persona=load_active_persona(agent.persona_id),
            session_id="window-wiring-grounded",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
