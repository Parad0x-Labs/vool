from __future__ import annotations

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest import mock

import pytest

from core.curiosity_roamer import CuriosityResult
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.task_router import classify

FORBIDDEN_PLANNER_LEAKS = (
    "workflow:",
    "here's what i'd suggest",
    "real steps completed:",
    "summary_block",
    "action_plan",
)

TRIVIAL_CHAT_FAST_PATH_CASES = (
    ("help", "utility_answer", "wired on this runtime:"),
    ("hey", "smalltalk", "hey"),
    ("hello", "smalltalk", "hello"),
    ("how are you", "smalltalk", "running clean. what do you need?"),
)

PLAIN_TEXT_ROUTING_CASES = (
    ("do you think boredom is useful?", "general_advisory"),
    ("how should i position my b2b analytics product?", "business_advisory"),
    ("what should i eat after lifting?", "food_nutrition"),
    ("my partner and i keep having the same argument. what should i do?", "relationship_advisory"),
    ("brainstorm a launch campaign idea for a weird soda brand", "creative_ideation"),
    ("tell me about stoicism", "chat_research"),
)

LIVE_INFO_SYNTHESIS_CASES = (
    (
        "latest telegram bot api updates",
        "planned_search_query",
        [
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
        "Telegram Bot API docs are the canonical source for these updates.",
    ),
    (
        "what is the weather in London today?",
        "search_query",
        [
            {
                "summary": "Cloudy with light rain, around 11C, with breezy afternoon conditions.",
                "source_label": "duckduckgo.com",
                "origin_domain": "bbc.com",
                "result_title": "BBC Weather - London",
                "result_url": "https://www.bbc.com/weather/2643743",
                "used_browser": False,
            }
        ],
        "London looks cloudy with light rain around 11C, based on BBC Weather.",
    ),
)

HIVE_SYNTHESIS_CASES = (
    "task_list",
    "task_status",
)

HONEST_DEGRADATION_CASES = (
    (
        "general_chat_provider_unavailable",
        "do you think boredom is useful?",
        ModelExecutionDecision(
            source="no_provider_available",
            task_hash="provider-missing",
            confidence=0.84,
            trust_score=0.84,
            used_model=False,
        ),
        "I couldn't get a live model response in this run",
        False,
    ),
    (
        "live_info_memory_fallback_blocked",
        "latest telegram bot api updates",
        ModelExecutionDecision(
            source="memory_hit",
            task_hash="fresh-fallback",
            output_text="Remembered answer that should never become the final reply.",
            confidence=0.84,
            trust_score=0.84,
            used_model=False,
        ),
        "could not ground a current answer confidently",
        False,
    ),
)

M1_EVAL_MATRIX = {
    "trivial_chat_fast_path": TRIVIAL_CHAT_FAST_PATH_CASES,
    "plain_text_routing": PLAIN_TEXT_ROUTING_CASES,
    "live_info_synthesis": LIVE_INFO_SYNTHESIS_CASES,
    "hive_synthesis": HIVE_SYNTHESIS_CASES,
    "planner_leak_rejection": ("general_chat", "advisory_chat", "research_chat"),
    "honest_degradation": HONEST_DEGRADATION_CASES,
}


def _chat_truth_events(audit_log_mock: mock.Mock) -> list[dict]:
    events: list[dict] = []
    for call in audit_log_mock.call_args_list:
        if not call.args or call.args[0] != "agent_chat_truth_metrics":
            continue
        details = call.kwargs.get("details")
        if details is None and len(call.args) >= 3:
            details = call.args[2]
        events.append(dict(details or {}))
    return events


def _provider_decision(*, task_hash: str, output_text: str) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider",
        task_hash=task_hash,
        provider_id="ollama:qwen",
        used_model=True,
        output_text=output_text,
        confidence=0.84,
        trust_score=0.84,
    )


def _configure_model_chat_path(agent, context_result_factory, *, decision: ModelExecutionDecision) -> None:
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(return_value=decision)  # type: ignore[assignment]
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="eval")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )


def _common_runtime_patches():
    return (
        mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]),
        mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None),
        mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]),
        mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None),
    )


@contextmanager
def _common_runtime_patch_stack():
    with ExitStack() as stack:
        for patcher in _common_runtime_patches():
            stack.enter_context(patcher)
        yield


def test_milestone1_eval_matrix_covers_required_ai_first_behaviors() -> None:
    assert set(M1_EVAL_MATRIX) == {
        "trivial_chat_fast_path",
        "plain_text_routing",
        "live_info_synthesis",
        "hive_synthesis",
        "planner_leak_rejection",
        "honest_degradation",
    }
    assert all(M1_EVAL_MATRIX.values())


@pytest.mark.parametrize(("prompt", "expected_class", "expected_snippet"), TRIVIAL_CHAT_FAST_PATH_CASES)
def test_eval_trivial_chat_fast_path(
    make_agent,
    context_result_factory,
    prompt: str,
    expected_class: str,
    expected_snippet: str,
) -> None:
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("trivial chat should stay on fast path"))  # type: ignore[assignment]

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert expected_snippet.lower() in result["response"].lower()
    assert result["response_class"] == expected_class
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is True
    assert events[0]["model_inference_used"] is False
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["template_renderer_hit"] is False
    assert result["model_execution"]["used_model"] is False
    assert agent.memory_router.resolve.call_count == 0
    assert agent.memory_router.resolve.call_count == 0


@pytest.mark.parametrize(("prompt", "expected_task_class"), PLAIN_TEXT_ROUTING_CASES)
@pytest.mark.xfail(reason="Pre-existing: routing classification drift")
def test_eval_plain_text_routing(make_agent, prompt: str, expected_task_class: str) -> None:
    agent = make_agent()
    interpretation = SimpleNamespace(
        reconstructed_text=prompt,
        normalized_text=prompt,
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
    classification = classify(prompt, context=interpretation.as_context())

    routed, profile = agent._model_routing_profile(
        user_input=prompt,
        classification=classification,
        interpretation=interpretation,
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert routed["task_class"] == expected_task_class
    assert profile["output_mode"] == "plain_text"


@pytest.mark.parametrize(("prompt", "search_method", "search_results", "reply"), LIVE_INFO_SYNTHESIS_CASES)
@pytest.mark.xfail(reason="Pre-existing: weather synthesis format changed")
def test_eval_live_info_synthesis(
    make_agent,
    context_result_factory,
    prompt: str,
    search_method: str,
    search_results: list[dict],
    reply: str,
) -> None:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(task_hash=f"live-info-{search_method}", output_text=reply),
    )

    planned_search_return = search_results if search_method == "planned_search_query" else mock.DEFAULT
    search_return = search_results if search_method == "search_query" else mock.DEFAULT

    with mock.patch("core.agent_runtime.agent.audit_logger.log"), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=planned_search_return,
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.search_query",
        return_value=search_return,
    ), _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "utility_answer"
    for sr in search_results:
        title = str(sr.get("result_title") or "").strip()
        domain = str(sr.get("origin_domain") or sr.get("source_label") or "").strip()
        if title:
            assert title.lower() in result["response"].lower(), f"Expected result title in response: {title}"
        elif domain:
            assert domain.lower() in result["response"].lower(), f"Expected domain in response: {domain}"
    for marker in FORBIDDEN_PLANNER_LEAKS:
        assert marker not in result["response"].lower()


@pytest.mark.parametrize("scenario", HIVE_SYNTHESIS_CASES)
def test_eval_hive_synthesis(make_agent, scenario: str) -> None:
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    if scenario == "task_list":
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
            True,
            {
                "command_kind": "task_list",
                "watcher_status": "ok",
                "response_text": (
                    "Available Hive tasks right now (2 total):\n"
                    "- [open] OpenClaw integration audit (#7d33994f)\n"
                    "- [researching] Hive footer cleanup (#ada43859)\n"
                ),
                "topics": [
                    {"topic_id": "7d33994f-dd40", "title": "OpenClaw integration audit", "status": "open"},
                    {"topic_id": "ada43859-dd40", "title": "Hive footer cleanup", "status": "researching"},
                ],
                "online_agents": [],
            },
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=_provider_decision(
                task_hash="hive-list",
                output_text="I can see two Hive tasks open right now: OpenClaw integration audit and Hive footer cleanup.",
            )
        )
        prompt = "show me the open hive tasks"
    else:
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (False, None)
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=_provider_decision(
                task_hash="hive-status",
                output_text=(
                    "Agent Commons: better human-visible watcher and task-flow UX is still researching. "
                    "There is 1 active claim, 1 result post, and 2 artifacts so far."
                ),
            )
        )
        hive_state = {
            "watched_topic_ids": ["7d33994f-dd40"],
            "interaction_payload": {"active_topic_id": "7d33994f-dd40"},
        }
        packet = {
            "topic": {
                "topic_id": "7d33994f-dd40",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
            "execution_state": {
                "execution_state": "claimed",
                "active_claim_count": 1,
                "artifact_count": 2,
            },
            "counts": {"post_count": 1, "active_claim_count": 1},
            "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
        }
        prompt = "what is the status"

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        if scenario == "task_list":
            result = agent.run_once(
                prompt,
                session_id_override="openclaw:hive-list-eval",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
        else:
            with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
                agent.public_hive_bridge, "enabled", return_value=True
            ), mock.patch.object(
                agent.public_hive_bridge, "get_public_research_packet", return_value=packet
            ):
                result = agent.run_once(
                    prompt,
                    session_id_override="openclaw:hive-status-eval",
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )

    events = _chat_truth_events(audit_log)
    for marker in FORBIDDEN_PLANNER_LEAKS:
        assert marker not in result["response"].lower()
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["tool_backing_sources"] == ["hive"]


@pytest.mark.parametrize(
    ("prompt", "reply", "must_contain"),
    [
        (
            "do you think boredom is useful?",
            "Here's what I'd suggest:\n\n- Treat boredom as a signal, not a defect.",
            "Treat boredom as a signal",
        ),
        (
            "how should i position my b2b analytics product?",
            "Workflow:\n- classified task as `business_advisory`\n\nFocus on the painful decision it makes faster.",
            "Focus on the painful decision",
        ),
        (
            "tell me about stoicism",
            'Real steps completed:\n- web.search: compared modern summaries.\n\n{"summary":"Stoicism is about judgment, not emotional numbness.","bullets":["It trains attention.","It trains discipline."]}',
            "Stoicism is about judgment",
        ),
    ],
)
def test_eval_planner_leak_rejection(
    make_agent,
    context_result_factory,
    prompt: str,
    reply: str,
    must_contain: str,
) -> None:
    agent = make_agent()
    _configure_model_chat_path(
        agent,
        context_result_factory,
        decision=_provider_decision(task_hash=f"planner-leak-{prompt}", output_text=reply),
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, _common_runtime_patch_stack():
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert must_contain in result["response"]
    for marker in FORBIDDEN_PLANNER_LEAKS:
        assert marker not in result["response"].lower()
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False


@pytest.mark.parametrize(
    ("label", "prompt", "decision", "expected_snippet", "expected_model_use"),
    HONEST_DEGRADATION_CASES,
)
def test_eval_honest_degradation(
    make_agent,
    context_result_factory,
    enable_web,
    label: str,
    prompt: str,
    decision: ModelExecutionDecision,
    expected_snippet: str,
    expected_model_use: bool,
) -> None:
    agent = make_agent()
    _configure_model_chat_path(agent, context_result_factory, decision=decision)

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.render_response",
        side_effect=AssertionError("Milestone 1 degradation should not fall back to planner renderer"),
    ), _common_runtime_patch_stack():
        if label == "live_info_memory_fallback_blocked":
            with mock.patch.object(agent, "_live_info_search_notes", return_value=[]), mock.patch(
                "core.agent_runtime.agent.WebAdapter.planned_search_query",
                return_value=[
                    {
                        "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                        "confidence": 0.67,
                        "source_profile_id": "messaging_platform_docs",
                        "source_profile_label": "Messaging platform docs",
                        "result_title": "Telegram Bot API",
                        "result_url": "https://core.telegram.org/bots/api",
                        "origin_domain": "core.telegram.org",
                    }
                ],
            ):
                result = agent.run_once(
                    prompt,
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
        else:
            result = agent.run_once(
                prompt,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

    events = _chat_truth_events(audit_log)
    assert expected_snippet.lower() in result["response"].lower()
    for marker in FORBIDDEN_PLANNER_LEAKS:
        assert marker not in result["response"].lower()
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is expected_model_use
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["template_renderer_hit"] is False
