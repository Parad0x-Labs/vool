from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from apps.vool_agent import ChatTurnResult, ResponseClass
from core.context_retrieval import reset_retrieval_telemetry
from core.human_input_adapter import adapt_user_input
from core.identity_manager import Persona, render_with_persona
from core.memory_first_router import ModelExecutionDecision
from core.persistent_memory import append_conversation_event
from core.remote_fetch_policy import remote_fetch_policy_scope


@pytest.fixture(autouse=True)
def _isolate_remote_fetch_attempts():
    """Reset the process-global remote-fetch attempt counter per test.

    ``remote_fetch_attempt_count()`` feeds ``result["web_calls"]`` and is only zeroed by
    ``remote_fetch_policy_scope`` (entered on the API path, not by direct ``run_once``
    calls). Without this guard a web-calling test earlier in the same pytest process
    leaks its count into the ``web_calls == 0`` assertions here, which is order-dependent
    and only surfaces in unsharded runs. Mirrors the API path's per-request isolation.
    """
    with remote_fetch_policy_scope(None):
        yield


def test_utility_day_and_date_answers_are_clean_and_footerless(make_agent, forbidden_chat_leaks):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    expected_day = datetime.now().astimezone().strftime("%A")
    day_result = agent.run_once(
        "what is the day today ?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    date_result = agent.run_once(
        "what is the date today?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert day_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert expected_day.lower() in day_result["response"].lower()
    assert date_result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "today is" in date_result["response"].lower()
    assert "hive" not in day_result["response"].lower()
    for marker in forbidden_chat_leaks:
        assert marker not in day_result["response"].lower()
        assert marker not in date_result["response"].lower()


def test_utility_time_in_vilnius_binds_real_value_and_never_leaks_placeholder(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what time is now in Vilnius?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in result["response"].lower()
    assert "[time]" not in result["response"].lower()
    assert "vilnius" in result["response"].lower()
    assert result["response"].count(":") >= 1


def test_malformed_vilnius_time_followup_recovers_to_bound_utility_answer(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what time now vilnius",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": [
                {"role": "assistant", "content": "Ask me for the current time if you need it."}
            ],
        },
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in lowered
    assert "[time]" not in lowered
    assert "i can help think, research, write code" not in lowered


def test_what_wheres_is_in_vilnius_recovers_from_recent_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "what where's is in Vilnius?",
        session_id_override="openclaw:vilnius-where-followup",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "conversation_history": [
                {"role": "user", "content": "what time is now in Vilnius?"},
                {"role": "assistant", "content": "Current time in Vilnius is 12:32 EET."},
            ],
        },
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in lowered
    assert "[time]" not in lowered
    assert "i can help think, research, write code" not in lowered


def test_short_vilnius_time_followup_reuses_recent_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override="openclaw:vilnius-short-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "and there?",
        session_id_override="openclaw:vilnius-short-followup",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert first["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert second["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in second["response"].lower()
    assert "[time]" not in second["response"].lower()
    assert "i can help think, research, write code" not in second["response"].lower()


def test_exact_vilnius_malformed_followup_reuses_session_time_context(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("utility fast path should not load context")  # type: ignore[attr-defined]
    session_id = "openclaw:vilnius-exact-followup"

    first = agent.run_once(
        "what time is now in Vilnius?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "what where's is in Vilnius?",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert first["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert second["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "current time in vilnius is" in second["response"].lower()
    assert "[time]" not in second["response"].lower()
    assert "weather" not in second["response"].lower()


def test_direct_math_overrides_stale_toly_context(make_agent):
    session_id = "openclaw:math-after-toly"
    adapt_user_input("who is Toly in Solana?", session_id=session_id)
    append_conversation_event(
        session_id=session_id,
        user_input="who is Toly in Solana?",
        assistant_output="Toly is Anatoly Yakovenko, one of Solana's co-founders.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("direct math should bypass stale conversational context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "17 * 19",
        session_id_override=session_id,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["response"] == "17 * 19 = 323."
    assert "toly" not in result["response"].lower()


def test_wrapped_direct_math_uses_arithmetic_route_without_model(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("arithmetic should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "What is 17 * 23?",
        session_id_override="openclaw:wrapped-math",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == "17 * 23 = 391."
    assert result["route"] == "arithmetic"
    assert result["model_execution"]["used_model"] is False
    assert result["fast_path_hit"] is True
    assert result["prompt_eval_count"] == 0
    assert result["model_calls"] == 0
    assert result["web_calls"] == 0


def test_same_chat_math_recall_is_authoritative_when_providers_are_unavailable(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("same-chat recall must not load memory context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        side_effect=AssertionError("same-chat recall must not depend on provider fallback")
    )
    session_id = "openclaw:same-chat-math-recall"

    first = agent.run_once(
        "37 × 24",
        session_id_override=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )
    second = agent.run_once(
        "39 × 24",
        session_id_override=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )
    recalled = agent.run_once(
        "What math answers appeared earlier?",
        session_id_override=session_id,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert first["response"] == "37*24 = 888."
    assert second["response"] == "39*24 = 936."
    assert recalled["response"] == "37 × 24 = 888\n39 × 24 = 936"
    assert recalled["route"] == "same_chat_history"
    assert recalled["route_reason"] == "authoritative_visible_math_history"
    assert recalled["model_execution"]["used_model"] is False
    assert recalled["model_calls"] == 0
    assert recalled["web_calls"] == 0


def test_plain_chat_result_exposes_minimal_model_route_telemetry(make_agent):
    reset_retrieval_telemetry()
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="minimal-route-telemetry",
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            used_model=True,
            output_text="A mutex provides mutual exclusion for shared state.",
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        "Explain what a mutex is briefly.",
        session_id_override="openclaw:minimal-route-telemetry",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["route"] == "plain_task_minimal:qwen2.5:7b"
    assert result["route_reason"] == "ordinary_plain_text_task"
    assert result["model_selected"] == "qwen2.5:7b"
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0
    assert result["fast_path_hit"] is False
    assert result["model_residency"]["known"] is False


@pytest.mark.parametrize(
    ("provider_id", "model_name", "local_only_mode"),
    [
        ("ollama-local:qwen2.5:7b", "qwen2.5:7b", True),
        ("openrouter-byok:nvidia/nemotron-free", "nvidia/nemotron-free", False),
    ],
)
def test_ordinary_multi_part_qa_answers_every_part_on_local_and_cloud_surfaces(
    make_agent,
    provider_id: str,
    model_name: str,
    local_only_mode: bool,
):
    prompt = "Explain ocean blue. Calculate 39 × 24. Give 7-word title."
    answer = (
        "1. Water absorbs longer wavelengths more strongly, so scattered blue light dominates.\n"
        "2. 39 × 24 = 936.\n"
        "3. Blue Depths Shape Our Living Ocean World"
    )
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash=f"multi-part-{model_name}",
            provider_id=provider_id,
            model_name=model_name,
            used_model=True,
            output_text=answer,
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        prompt,
        session_id_override=f"ordinary-multi-{model_name}",
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
            "local_only_mode": local_only_mode,
        },
    )

    assert result["response"] == answer
    assert result["route_reason"] == "ordinary_plain_text_task"
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0
    from core.execution.planner import should_attempt_tool_intent

    assert should_attempt_tool_intent(
        prompt,
        task_class="chat_conversation",
        source_context={"surface": "api", "platform": "api"},
    ) is False
    model_context = agent.memory_router.resolve.call_args.kwargs["source_context"]
    assert model_context["plain_task_kind"] == "multi_part_qa"


def test_incomplete_multi_part_provider_text_never_becomes_the_final_answer(make_agent):
    prompt = "Explain ocean blue. Calculate 39 × 24. Give 7-word title."
    leaked_selection_text = (
        "nvidia/nemotron-3-ultra-550b-a55b:free was the only model this turn was allowed to use"
    )
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="multi-part-provider-leak",
            provider_id="openrouter-byok:nvidia/nemotron-free",
            model_name="nvidia/nemotron-free",
            used_model=True,
            output_text=leaked_selection_text,
            confidence=0.2,
            trust_score=0.2,
        )
    )

    result = agent.run_once(
        prompt,
        session_id_override="ordinary-multi-provider-leak",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert leaked_selection_text not in result["response"]
    assert "model" not in result["response"].lower()
    assert "provider" not in result["response"].lower()
    assert "complete answer to every part" in result["response"].lower()


def test_real_run_once_preserves_model_answer_after_orchestration_cleanup(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="mixed-orchestration-answer",
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            used_model=True,
            output_text=(
                "4821 * 37 = 178377.\n\n"
                '{"task_envelope":{"task_id":"queen-1"},"capacity_state":{"availability_state":"ready"}}'
            ),
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        "Summarize this calculation briefly.",
        session_id_override="openclaw:mixed-orchestration-answer",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["response"] == "4821 * 37 = 178377."
    assert result["model_execution"]["used_model"] is True


def test_plain_task_minimal_skips_runtime_transcript(monkeypatch):
    from core.prompt_normalizer import _history_messages_for_chat

    def _fail_transcript(*_args, **_kwargs):
        raise AssertionError("plain text tasks must not pull runtime transcript or capsule context")

    monkeypatch.setattr("core.prompt_normalizer.canonical_runtime_transcript", _fail_transcript)

    messages, source = _history_messages_for_chat(
        {"surface": "api", "platform": "api", "conversation_history": [{"role": "user", "content": "Translate this."}]},
        runtime_session_id="plain-task-history-skip",
        current_user_text="Translate to Lithuanian: hello",
        prompt_profile="plain_task_minimal",
    )

    assert messages == []
    assert source == "plain_task_no_history"


def test_plain_task_minimal_skips_context_loader(make_agent):
    reset_retrieval_telemetry()
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("plain text tasks must not load retrieval context")  # type: ignore[attr-defined]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="plain-task-no-context",
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            used_model=True,
            output_text="Labas.",
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        "Translate to Lithuanian: hello.",
        session_id_override="plain-task-no-context",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": False},
    )

    assert result["route"] == "plain_task_minimal:qwen2.5:7b"
    assert result["route_reason"] == "ordinary_plain_text_task"
    assert result["model_calls"] == 1
    assert result["web_calls"] == 0
    assert result["capsule_mode"] != "distilled"
    agent.context_loader.load.assert_not_called()


def test_canonical_project_explanation_loads_grounding_context(make_agent):
    reset_retrieval_telemetry()
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider_execution",
            task_hash="canonical-project-grounding",
            provider_id="ollama-local:qwen2.5:7b",
            model_name="qwen2.5:7b",
            used_model=True,
            output_text="VOOL is Parad0x Labs' local-first personal agent.",
            confidence=0.9,
            trust_score=0.9,
        )
    )

    result = agent.run_once(
        "Explain what VOOL is and who builds it.",
        session_id_override="openclaw:canonical-project-grounding",
        source_context={
            "surface": "api",
            "platform": "api",
            "allow_remote_fetch": False,
        },
    )

    agent.context_loader.load.assert_called_once()
    model_call = agent.memory_router.resolve.call_args.kwargs
    assert model_call["source_context"]["canonical_grounding_required"] is True
    assert "plain_task_kind" not in model_call["source_context"]
    assert result["route"] == "model_minimal:qwen2.5:7b"


def test_canonical_grounding_disables_plain_task_prompt_profile() -> None:
    from core.prompt_normalizer import _chat_system_prompt_profile

    assert _chat_system_prompt_profile(
        output_mode="plain_text",
        task_kind="conversation",
        plain_task_kind="explanation",
        user_text="Explain what VOOL is and who builds it.",
        canonical_grounding_required=True,
    ) == "chat_minimal"


def test_startup_sequence_stays_deterministic(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("startup fast path should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "A new session was started via /new or /reset. Execute your Session Startup sequence now - read the required files before responding to the user.",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.SMALLTALK.value
    assert result["model_execution"]["used_model"] is False
    assert "new session is clean" in result["response"].lower()


@pytest.mark.parametrize(
    ("prompt", "provider_text", "expected"),
    [
        ("ohmy gad yu not a dumbs anymore?!", "Better than before, yes. The conversation lane still needs work.", "better than before"),
        ("you sound weird", "The routing is still too stitched together.", "routing is still too stitched together"),
        ("why are you acting like this", "The routing is still too stitched together.", "routing is still too stitched together"),
    ],
)
def test_evaluative_turns_stay_conversational_and_do_not_carry_hive_footer(
    make_agent,
    prompt,
    provider_text,
    expected,
):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash=f"evaluative-{prompt}",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=provider_text,
            confidence=0.84,
            trust_score=0.84,
        )
    )
    result = agent.run_once(
        prompt,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.GENERIC_CONVERSATION.value
    assert expected in result["response"].lower()
    assert "hive:" not in result["response"].lower()
    assert result["model_execution"]["used_model"] is False


def _is_bounded_greeting_reply(text: str) -> bool:
    """A greeting reply is now a random opener from the time-aware pools (optionally + a task tail),
    so tests assert membership in the pool rather than an exact echo string."""
    from core.agent_runtime.fast_paths_utility import (
        _AFTERNOON_OPENERS,
        _DAY_OPENERS,
        _EVENING_OPENERS,
        _GENERIC_OPENERS,
        _MORNING_OPENERS,
        _NIGHT_OPENERS,
    )

    openers = (
        _GENERIC_OPENERS + _MORNING_OPENERS + _AFTERNOON_OPENERS
        + _EVENING_OPENERS + _NIGHT_OPENERS + _DAY_OPENERS
    )
    return bool(text.strip()) and any(text.startswith(o.rstrip(".!?")) for o in openers)


@pytest.mark.parametrize(
    ("prompt", "expected_snippet"),
    [
        # None => a random bounded greeting (asserted via _is_bounded_greeting_reply); the model
        # must NOT be hit either way.
        ("hey", None),
        ("hello", None),
        ("how are you", "running clean. what do you need"),
    ],
)
def test_chat_surface_smalltalk_uses_bounded_fast_path_on_openclaw_surface(make_agent, prompt, expected_snippet):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("trivial smalltalk should not hit model wording"))  # type: ignore[assignment]

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:{prompt}",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    if expected_snippet is None:
        assert _is_bounded_greeting_reply(result["response"])
    else:
        assert expected_snippet in result["response"].lower()
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"
    assert agent.memory_router.resolve.call_count == 0


def test_chat_surface_help_uses_deterministic_fast_path(make_agent):
    agent = make_agent()
    agent._help_capabilities_text = mock.Mock(return_value="deterministic help lane")  # type: ignore[method-assign]
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("help should stay on deterministic fast path"))  # type: ignore[assignment]

    result = agent.run_once(
        "help",
        session_id_override="openclaw:help-fast-path",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response"] == "deterministic help lane"
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"


def test_repeated_chat_greetings_on_openclaw_surface_use_bounded_fast_path(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("trivial smalltalk should stay on bounded fast path"))  # type: ignore[assignment]

    first = agent.run_once(
        "hey",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    second = agent.run_once(
        "yo",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )
    third = agent.run_once(
        "hello",
        session_id_override="openclaw:greeting-loop",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    # Greeting fast-path returns a random bounded greeting; the point of the contract is that all
    # three stay on the fast path (never hit the model), not the exact wording.
    assert _is_bounded_greeting_reply(first["response"])
    assert _is_bounded_greeting_reply(second["response"])
    assert "skip the greeting" not in third["response"].lower()
    assert first["model_execution"]["used_model"] is False
    assert second["model_execution"]["used_model"] is False
    assert third["model_execution"]["used_model"] is False


def test_api_surface_smalltalk_uses_bounded_fast_path(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("api smalltalk should not hit model wording"))  # type: ignore[assignment]

    result = agent.run_once(
        "hey",
        session_id_override="openclaw:api-hey",
        source_context={"surface": "api", "platform": "api"},
    )

    assert _is_bounded_greeting_reply(result["response"])
    assert result["model_execution"]["used_model"] is False
    assert result["model_execution"]["source"] == "fast_path"


def test_low_verbosity_persona_wrapper_does_not_clip_to_first_paragraph():
    persona = Persona(
        persona_id="default",
        display_name="VOOL",
        spirit_anchor="anchor",
        tone="direct",
        verbosity="low",
        risk_tolerance=0.2,
        explanation_depth=0.4,
        execution_style="direct",
        strictness=0.5,
    )

    rendered = render_with_persona(
        "First paragraph stays.\n\nSecond paragraph still shows up.",
        persona,
    )

    assert rendered == "First paragraph stays.\n\nSecond paragraph still shows up."


def test_sanitization_contract_strips_runtime_preamble_and_forbidden_tool_garbage(make_agent):
    agent = make_agent()
    text = (
        "Real steps completed:\n"
        "- workspace.search_text\n\n"
        "I won't fake it: the model returned an invalid tool payload with no intent name."
    )
    result = ChatTurnResult(text=text, response_class=ResponseClass.TASK_FAILED_USER_SAFE)

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I couldn't map that cleanly to a real action."


def test_sanitization_contract_rewrites_botty_live_fallback(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="I pulled live evidence for this turn, but I couldn't produce a clean final synthesis in this run.",
        response_class=ResponseClass.UTILITY_ANSWER,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I checked, but I couldn't ground a confident answer from the evidence I found."
    assert "clean final synthesis" not in shaped.lower()


def test_sanitization_contract_rewrites_botty_conversation_fallback(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="I couldn't produce a grounded conversational reply in this run.",
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I couldn't answer that cleanly. Ask it another way."
    assert "grounded conversational reply" not in shaped.lower()


def test_sanitization_contract_strips_generic_planner_scaffold(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text="review problem\n- choose safe next step\n- validate result",
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I'm here and ready to help. What do you want to do?"
    assert "review problem" not in shaped.lower()
    assert "choose safe next step" not in shaped.lower()
    assert "validate result" not in shaped.lower()


def test_sanitization_contract_strips_generic_planner_scaffold_from_wrapped_json_payload(make_agent):
    agent = make_agent()
    result = ChatTurnResult(
        text='{"summary":"review problem","bullets":["choose safe next step","validate result"]}',
        response_class=ResponseClass.GENERIC_CONVERSATION,
    )

    shaped = agent._shape_user_facing_text(result)

    assert shaped == "I'm here and ready to help. What do you want to do?"
    assert "review problem" not in shaped.lower()
    assert "choose safe next step" not in shaped.lower()
    assert "validate result" not in shaped.lower()


def test_step_by_step_prompt_still_strips_planner_wrapper_from_chat_surface(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="step-by-step-wrapper",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Claim the task, post progress, then deliver the result.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    with mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="Here's what I'd suggest:\n\n- claim the task\n- post progress\n- deliver the result",
    ), mock.patch(
        "core.agent_runtime.agent.classify",
        return_value={"task_class": "system_design", "risk_flags": [], "confidence_hint": 0.74},
    ), mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "do all step by step",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert "here's what i'd suggest" not in lowered
    assert "claim the task" in lowered
    assert "post progress" in lowered
    assert "deliver the result" in lowered


def test_footer_policy_only_allows_selection_and_approval(make_agent):
    agent = make_agent()
    source_context = {"surface": "openclaw", "platform": "openclaw"}

    assert not agent._should_attach_hive_footer(
        ChatTurnResult(text="Today is Thursday, 2026-03-12.", response_class=ResponseClass.UTILITY_ANSWER),
        source_context=source_context,
    )
    assert not agent._should_attach_hive_footer(
        ChatTurnResult(text="Available Hive tasks right now...", response_class=ResponseClass.TASK_LIST),
        source_context=source_context,
    )
    assert agent._should_attach_hive_footer(
        ChatTurnResult(text="Pick one by name or short `#id`.", response_class=ResponseClass.TASK_SELECTION_CLARIFICATION),
        source_context=source_context,
    )
    assert agent._should_attach_hive_footer(
        ChatTurnResult(text="Approval required before file write.", response_class=ResponseClass.APPROVAL_REQUIRED),
        source_context=source_context,
    )


def test_explicit_short_answer_request_can_still_stay_short(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="short-answer",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Yes. Use boredom as a signal.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "short answer only: is boredom useful?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response"] == "Yes. Use boredom as a signal."
    assert "\n\n" not in result["response"]


def test_capability_truth_query_reports_unwired_email_honestly(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]
    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only.",
            },
            {
                "capability_id": "operator.discord_post",
                "surface": "communication",
                "supported": False,
                "support_level": "unsupported",
                "claim": "send Discord messages through the configured bridge",
                "unsupported_reason": "Discord bridge sending is not configured on this runtime.",
            },
        ],
    ):
        result = agent.run_once(
            "can you send email from here?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert "email sending is not wired on this runtime" in result["response"].lower()
    assert "draft the email text" in result["response"].lower()
    assert result["model_execution"]["used_model"] is False


def test_capability_truth_query_distinguishes_partial_support_from_full_autonomy(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]
    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            }
        ],
    ):
        result = agent.run_once(
            "can you build a full ios app end to end?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "partially" in result["response"].lower()
    assert "not a full autonomous build/debug/test loop" in result["response"].lower()


def test_capability_truth_query_distinguishes_impossible_from_unwired(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "can you read my mind?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "outside what this runtime can actually do" in result["response"].lower()


def test_capability_truth_query_reports_self_tool_creation_limits_honestly(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability truth should not load context")  # type: ignore[attr-defined]

    result = agent.run_once(
        "can you create your own tools if you need one?",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "task-local helper files or scripts" in lowered
    assert "cannot auto-register" in lowered or "cannot register" in lowered


def test_generic_capability_inventory_prompt_uses_grounded_fast_path(make_agent):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability inventory should not load context")  # type: ignore[attr-defined]

    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.build_scaffold",
                "surface": "workspace",
                "supported": True,
                "support_level": "partial",
                "claim": "write narrow Telegram or Discord bot scaffolds into the active workspace",
                "partial_reason": "This is scaffold-level support only, not a full autonomous build/debug/test loop.",
            },
            {
                "capability_id": "workspace.write",
                "surface": "workspace",
                "supported": True,
                "support_level": "full",
                "claim": "read and write files inside the active workspace",
            },
        ],
    ):
        result = agent.run_once(
            "what can you do right now on this machine?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "wired on this runtime" in lowered
    assert "read and write files inside the active workspace" in lowered
    assert "not a full autonomous build/debug/test loop" in lowered


@pytest.mark.parametrize(
    "prompt",
    [
        "one short line only: what can you actually do on this machine right now?",
        "real quick, what can you do locally on this machine right now?",
        "in one clean line, what are your actual local powers here?",
    ],
)
def test_wrapped_capability_inventory_prompts_still_return_grounded_local_powers(make_agent, prompt):
    agent = make_agent()
    agent.context_loader.load.side_effect = AssertionError("capability inventory should not load context")  # type: ignore[attr-defined]

    with mock.patch.object(
        agent,
        "_capability_ledger_entries",
        return_value=[
            {
                "capability_id": "workspace.write",
                "surface": "workspace",
                "supported": True,
                "support_level": "full",
                "claim": "read and write files inside the active workspace",
            },
            {
                "capability_id": "operator.download",
                "surface": "operator",
                "supported": True,
                "support_level": "full",
                "claim": "download files into local bounded roots",
            },
            {
                "capability_id": "sandbox.command",
                "surface": "sandbox",
                "supported": True,
                "support_level": "full",
                "claim": "run bounded local sandbox commands",
            },
        ],
    ):
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == ResponseClass.UTILITY_ANSWER.value
    assert result["model_execution"]["used_model"] is False
    assert "\n" not in result["response"]
    assert "local powers here" in lowered
    assert "workspace file/folder read-write" in lowered
    assert "downloads" in lowered
    assert "sandbox commands" in lowered


def test_api_surface_greeting_falls_back_to_bounded_greeting_when_provider_is_cold(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="no_provider_available",
            task_hash="api-greeting-cold",
            confidence=0.3,
            trust_score=0.3,
            used_model=False,
        )
    )

    result = agent.run_once(
        "hello",
        source_context={"surface": "api", "platform": "api"},
    )

    assert result["response_class"] == ResponseClass.SMALLTALK.value
    assert result["model_execution"]["used_model"] is False
    # Even with a cold/absent provider, a greeting lands a bounded greeting (never the model
    # non-answer), now drawn at random from the time-aware pools.
    assert _is_bounded_greeting_reply(result["response"])


def test_agentic_build_request_routes_to_the_model_builder_not_a_fake_brief(make_agent, monkeypatch):
    # Point the builder's model call at an unreachable host so this stays a fast, deterministic
    # ROUTING assertion (no dependency on a live local model on the test machine).
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:1")
    agent = make_agent()
    result = agent.run_once(
        "build a web scraper service in this workspace and write the files",
        source_context={
            "surface": "openclaw",
            "platform": "openclaw",
            "workspace": "/tmp/vool-builder-gap",
        },
    )

    # An agentic build request now routes to the real model-driven builder (generate -> write -> test
    # -> iterate), replacing the old capability gap. Deterministic regardless of whether a local model
    # is reachable: with a model it returns a build report; without one it honestly says it could not
    # build -- it never fakes a tutorial/brief or claims success.
    assert "builder_model_build" in str(result.get("route") or "")
    lowered = result["response"].lower()
    assert "do not have a real bounded builder path" not in lowered  # not the stale capability gap
    assert "step 1" not in lowered and "here's how you" not in lowered  # not a faked tutorial
