from __future__ import annotations

from unittest import mock

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.autonomous_topic_research import AutonomousResearchResult
from core.curiosity_roamer import CuriosityResult
from core.hive_activity_tracker import prune_stale_hive_interaction_state, session_hive_state, update_session_hive_state
from core.memory_first_router import ModelExecutionDecision
from core.task_router import classify


def test_utility_turn_preserves_pending_hive_selection_context(make_agent):
    agent = make_agent()
    session_id = "openclaw:preserve-hive-selection"
    update_session_hive_state(
        session_id,
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
    )
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what is the date today?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    state = session_hive_state(session_id)
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    # M1: utility turns intentionally take over and set mode to "utility"
    assert state["interaction_mode"] == "utility"


def test_interaction_transition_modes_are_centralized(make_agent):
    agent = make_agent()
    session_id = "openclaw:transition-contract"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="Pick one by name.", response_class=ResponseClass.TASK_SELECTION_CLARIFICATION),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "hive_task_selection_pending"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="Started Hive research.", response_class=ResponseClass.TASK_STARTED),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "hive_task_active"

    agent._apply_interaction_transition(
        session_id,
        ChatTurnResult(text="I couldn't map that cleanly.", response_class=ResponseClass.TASK_FAILED_USER_SAFE),
    )
    assert session_hive_state(session_id)["interaction_mode"] == "error_recovery"


def test_stale_hive_selection_state_expires_cleanly():
    session_id = "openclaw:state-expiry"
    update_session_hive_state(
        session_id,
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={"shown_topic_ids": ["topic-1"], "shown_titles": ["OpenClaw integration audit"]},
    )
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE session_hive_watch_state SET updated_at = '2026-03-10T10:00:00+00:00' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state = prune_stale_hive_interaction_state(session_id)
    assert state["interaction_mode"] == ""
    assert state["interaction_payload"] == {}
    assert state["pending_topic_ids"] == []


def test_ambiguous_hive_followup_clarifies_instead_of_silent_guess(make_agent):
    agent = make_agent()
    queue_rows = [
        {
            "topic_id": "topic-1-aaaaaaaa",
            "title": "OpenClaw integration audit",
            "status": "open",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "topic-2-bbbbbbbb",
            "title": "Hive footer cleanup",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
            "shown_titles": ["OpenClaw integration audit", "Hive footer cleanup"],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(ok=True, status="completed", topic_id="topic-1-aaaaaaaa"),
    ) as research_topic_from_signal:
        result = agent.run_once(
            "review the problem",
            session_id_override="openclaw:ambiguous-followup",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.TASK_SELECTION_CLARIFICATION.value
    response_lower = result["response"].lower()
    assert any(
        phrase in response_lower
        for phrase in (
            "which task would you like to start with",
            "pick one by name or short",
            "choose one",
            "which task",
            "multiple",
            "openclaw integration audit",
            "hive footer cleanup",
        )
    )
    research_topic_from_signal.assert_not_called()


def test_fresh_web_query_beats_evaluative_detection(make_agent, context_result_factory, enable_web):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are the canonical source for these updates.",
            confidence=0.82,
            trust_score=0.82,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
                "confidence": 0.66,
            }
        ],
    ) as planned_search, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders", return_value=[]
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert planned_search.called
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "canonical source" in result["response"].lower()
    assert result["model_execution"]["used_model"] is True


@mock.patch("core.agent_runtime.agent.adapt_user_input")
def test_chat_surface_plain_text_routing_profiles_cover_broad_chat_domains(
    adapt_user_input_mock: mock.Mock,
    make_agent,
):
    agent = make_agent()

    def _adapt(text: str, session_id: str | None = None):
        return mock.Mock(
            reconstructed_text=text,
            normalized_text=text,
            understanding_confidence=0.84,
            topic_hints=[],
            quality_flags=[],
            reference_targets=[],
            as_context=lambda: {
                "topic_hints": [],
                "reference_targets": [],
                "understanding_confidence": 0.84,
                "quality_flags": [],
            },
        )

    adapt_user_input_mock.side_effect = _adapt
    prompts = [
        ("how do i fix this traceback in my python parser?", "debugging"),
        ("npm says module not found. how do i fix it?", "dependency_resolution"),
        ("my config keeps breaking when i load the yaml file", "config"),
        ("design a clean agent architecture for a local telegram bot", "system_design"),
        ("how should i position my b2b analytics product?", "business_advisory"),
        ("what should i eat after lifting?", "food_nutrition"),
        ("my partner and i keep having the same argument. what should i do?", "relationship_advisory"),
        ("brainstorm a launch campaign idea for a weird soda brand", "creative_ideation"),
        ("tell me about stoicism", "chat_research"),
    ]

    for prompt, expected_task_class in prompts:
        interpretation = adapt_user_input_mock(prompt)
        classification = classify(prompt, context=interpretation.as_context())
        routed, profile = agent._model_routing_profile(
            user_input=prompt,
            classification=classification,
            interpretation=interpretation,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        assert routed["task_class"] == expected_task_class
        assert routed["planner_style_requested"] is False
        # The claim this test exists to make: every broad chat domain answers in conversational
        # prose rather than a summary_block.
        assert profile["output_mode"] == "plain_text"
        # It used to also assert `task_kind == "normalization_assist"`, which encoded the defect
        # rather than the intent. That kind declares only {"format"}, so pinning it here meant a
        # `research` or `debugging` turn on the chat surface was handed a helper kind that cannot
        # retrieve -- measured 2026-08-05, an aviation question classified `research` produced an
        # 18-event trace with no search, no tool call and no fetch, and the model then narrated
        # sources that were never fetched. The surface may choose how an answer READS; it may not
        # choose what the turn can DO. Asserted on the capability table, which is stricter than the
        # single hard-coded kind it replaces and covers task kinds added later.
        from core.model_capabilities import TASK_KIND_TO_CAPABILITIES
        from core.task_router import model_execution_profile

        base_kind = str(model_execution_profile(expected_task_class, chat_surface=False)["task_kind"])
        lost = set(TASK_KIND_TO_CAPABILITIES.get(base_kind, set())) - set(
            TASK_KIND_TO_CAPABILITIES.get(str(profile["task_kind"]), set())
        )
        assert not lost, f"{expected_task_class}: chat surface lost {sorted(lost)}"


@mock.patch("core.agent_runtime.agent.adapt_user_input")
def test_chat_surface_explicit_plan_requests_keep_planner_behavior(
    adapt_user_input_mock: mock.Mock,
    make_agent,
):
    agent = make_agent()

    def _adapt(text: str, session_id: str | None = None):
        return mock.Mock(
            reconstructed_text=text,
            normalized_text=text,
            understanding_confidence=0.84,
            topic_hints=[],
            quality_flags=[],
            reference_targets=[],
            as_context=lambda: {
                "topic_hints": [],
                "reference_targets": [],
                "understanding_confidence": 0.84,
                "quality_flags": [],
            },
        )

    adapt_user_input_mock.side_effect = _adapt
    prompts = [
        "give me a plan to fix this traceback in my python parser",
        "give me a workflow to position my b2b analytics product",
        "step-by-step plan for stoicism research",
        "checklist for what i should eat after lifting",
        "rollout plan for a weird soda launch campaign",
    ]

    for prompt in prompts:
        interpretation = adapt_user_input_mock(prompt)
        classification = classify(prompt, context=interpretation.as_context())
        routed, profile = agent._model_routing_profile(
            user_input=prompt,
            classification=classification,
            interpretation=interpretation,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        assert routed["planner_style_requested"] is True
        assert profile["task_kind"] == "action_plan"
        assert profile["output_mode"] == "action_plan"


@mock.patch("core.agent_runtime.agent.adapt_user_input")
def test_non_chat_surface_keeps_legacy_unknown_and_research_profiles(
    adapt_user_input_mock: mock.Mock,
    make_agent,
):
    agent = make_agent()

    def _adapt(text: str, session_id: str | None = None):
        return mock.Mock(
            reconstructed_text=text,
            normalized_text=text,
            understanding_confidence=0.84,
            topic_hints=[],
            quality_flags=[],
            reference_targets=[],
            as_context=lambda: {
                "topic_hints": [],
                "reference_targets": [],
                "understanding_confidence": 0.84,
                "quality_flags": [],
            },
        )

    adapt_user_input_mock.side_effect = _adapt
    prompts = [
        ("tell me about stoicism", "research"),
    ]

    for prompt, expected_task_class in prompts:
        interpretation = adapt_user_input_mock(prompt)
        classification = classify(prompt, context=interpretation.as_context())
        routed, profile = agent._model_routing_profile(
            user_input=prompt,
            classification=classification,
            interpretation=interpretation,
            source_context={"surface": "cli", "platform": "cli"},
        )

        assert routed["task_class"] == expected_task_class
        assert profile["output_mode"] == "summary_block"

    interpretation = adapt_user_input_mock("do you think boredom is useful?")
    classification = classify("do you think boredom is useful?", context=interpretation.as_context())
    assert classification["task_class"] != "risky_system_action"


from storage.db import get_connection
