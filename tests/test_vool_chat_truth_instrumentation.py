from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from core.autonomous_topic_research import AutonomousResearchResult
from core.context_scope import ContextAccessPolicy
from core.curiosity_roamer import CuriosityResult
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.prompt_normalizer import normalize_prompt
from core.reasoning_engine import inspect_user_response_shape


def _chat_truth_events(audit_log_mock: mock.Mock) -> list[dict]:
    events: list[dict] = []
    for call in audit_log_mock.call_args_list:
        if not call.args:
            continue
        if call.args[0] != "agent_chat_truth_metrics":
            continue
        details = call.kwargs.get("details")
        if details is None and len(call.args) >= 3:
            details = call.args[2]
        events.append(dict(details or {}))
    return events


def test_prompt_normalizer_adds_chat_truth_metadata_for_chat_surface(context_result_factory):
    session_id = f"openclaw:chat-truth-{uuid.uuid4().hex}"
    source_context = {
        "surface": "openclaw",
        "platform": "openclaw",
        "runtime_session_id": session_id,
        "conversation_history": [
            {"role": "assistant", "content": "Previous answer."}
        ],
    }
    ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context=source_context,
    )
    request = normalize_prompt(
        task=SimpleNamespace(task_id="task-1", task_summary="Explain event loops"),
        classification={"task_class": "system_design", "risk_flags": []},
        interpretation=SimpleNamespace(
            reconstructed_text="Explain event loops",
            topic_hints=["architecture"],
            understanding_confidence=0.82,
        ),
        context_result=SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.4,
            assembled_context=lambda: "Prior note: user cares about precise wording.",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.4,
                total_tokens_used=lambda: 12,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        ),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="calm"),
        output_mode="plain_text",
        task_kind="conversation",
        trace_id="trace-1",
        surface="openclaw",
        source_context=source_context,
    )

    metadata = dict(request.metadata.get("chat_truth_prompt") or {})
    assert metadata["surface"] == "openclaw"
    assert metadata["task_kind"] == "conversation"
    assert metadata["output_mode"] == "plain_text"
    assert metadata["structured_output"] is False
    assert metadata["history_messages"] == 1
    assert metadata["transcript_source"] == "client_conversation_history"
    assert metadata["context_attached"] is True


def test_response_shape_inspector_flags_planner_leakage_for_renderer_text():
    metrics = inspect_user_response_shape(
        "Workflow:\n- classified task as `research`\n\nHere's what I'd suggest:\n- search trusted sources",
        surface="openclaw",
        rendered_via="reasoning_engine",
    )

    assert metrics["chat_surface"] is True
    assert metrics["planner_leakage"] is True
    assert metrics["template_renderer_hit"] is True
    assert metrics["template_fallback_hit"] is True


def test_fast_path_emits_chat_truth_metrics(make_agent):
    agent = make_agent()

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log:
        result = agent.run_once(
            "what is the date today?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response_class"] == "utility_answer"
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is True
    assert events[0]["model_inference_used"] is False
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["planner_leakage"] is False
    assert events[0]["template_renderer_hit"] is False


def test_renderer_path_emits_chat_truth_metrics(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="renderer",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Model candidate output for architecture reasoning.",
            confidence=0.82,
            trust_score=0.82,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.classify",
        return_value={"task_class": "system_design", "risk_flags": [], "confidence_hint": 0.74},
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("conceptual architecture chat should not trigger live adaptive research"),
    ), mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "Explain the event loop architecture tradeoffs.",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response"] == "Model candidate output for architecture reasoning."
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["rendered_via"] == "model_final_wording"
    assert events[0]["model_execution_source"] == "provider"


@pytest.mark.parametrize(
    ("prompt", "reply"),
    [
        (
            "do you think boredom is useful?",
            "Boredom is useful when it exposes that your environment is under-stimulating rather than your brain being broken.",
        ),
        (
            "how should i position my b2b analytics product?",
            "Position it around the painful decision it makes faster, not around dashboards.",
        ),
        (
            "tell me about stoicism",
            "Stoicism is a practical philosophy about attention, judgment, and discipline, but modern versions often flatten its ethics.",
        ),
    ],
)
def test_plain_text_chat_paths_use_model_as_final_speaker(
    make_agent,
    context_result_factory,
    prompt: str,
    reply: str,
):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash=f"plain-text-{prompt}",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=reply,
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        side_effect=AssertionError("plain conversational chat should not trigger live lookup"),
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders",
        return_value=[],
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            prompt,
            session_id_override=f"openclaw:plain-text:{prompt}",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response"] == reply
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["rendered_via"] == "model_final_wording"


@pytest.mark.parametrize(
    ("prompt", "decision", "expected_snippet", "blocked_text", "expected_model_use"),
    [
        (
            "do you think boredom is useful?",
            ModelExecutionDecision(
                source="exact_cache_hit",
                task_hash="cache-hit",
                output_text="Cached answer that should never become the final reply.",
                confidence=0.84,
                trust_score=0.84,
                used_model=False,
            ),
            "I'm not passing cached text off as a fresh answer.",
            "Cached answer that should never become the final reply.",
            False,
        ),
        (
            "do you think boredom is useful?",
            ModelExecutionDecision(
                source="memory_hit",
                task_hash="memory-hit",
                output_text="Remembered answer that should never become the final reply.",
                confidence=0.84,
                trust_score=0.84,
                used_model=False,
            ),
            "I'm not presenting remembered text as a fresh answer.",
            "Remembered answer that should never become the final reply.",
            False,
        ),
        (
            "do you think boredom is useful?",
            ModelExecutionDecision(
                source="no_provider_available",
                task_hash="provider-missing",
                confidence=0.84,
                trust_score=0.84,
                used_model=False,
            ),
            "I couldn't get a live model response in this run",
            "",
            False,
        ),
        (
            "how should i position my b2b analytics product?",
            ModelExecutionDecision(
                source="provider_execution",
                task_hash="provider-empty",
                provider_id="ollama:qwen",
                confidence=0.84,
                trust_score=0.84,
                used_model=True,
                output_text="",
            ),
            "I couldn't get a usable model response in this run",
            "",
            True,
        ),
    ],
)
def test_plain_text_chat_never_lets_cache_memory_or_provider_failure_become_final_speaker(
    make_agent,
    context_result_factory,
    prompt: str,
    decision: ModelExecutionDecision,
    expected_snippet: str,
    blocked_text: str,
    expected_model_use: bool,
):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(return_value=decision)  # type: ignore[assignment]
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        side_effect=AssertionError("chat fallback should not use planner renderer"),
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders",
        return_value=[],
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            prompt,
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert expected_snippet in result["response"]
    if blocked_text:
        assert blocked_text not in result["response"]
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is expected_model_use
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["model_execution_source"] == decision.source
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["rendered_via"] == "honest_degraded_chat"


@pytest.mark.parametrize(
    ("prompt", "reply", "must_contain", "must_not_contain"),
    [
        (
            "do you think boredom is useful?",
            "Here's what I'd suggest:\n\n- Treat boredom as a signal, not a defect.",
            "Treat boredom as a signal",
            ["Here's what I'd suggest", "Workflow:", "Real steps completed:"],
        ),
        (
            "how should i position my b2b analytics product?",
            "Workflow:\n- classified task as `business_advisory`\n\nFocus on the painful decision it makes faster.",
            "Focus on the painful decision",
            ["Workflow:", "Here's what I'd suggest", "Real steps completed:"],
        ),
        (
            "tell me about stoicism",
            'Real steps completed:\n- web.search: compared modern summaries.\n\n{"summary":"Stoicism is about judgment, not emotional numbness.","bullets":["It trains attention.","It trains discipline."]}',
            "Stoicism is about judgment",
            ['{"summary"', '"bullets"', "Workflow:", "Here's what I'd suggest", "Real steps completed:"],
        ),
    ],
)
def test_non_plan_chat_strips_planner_leakage_from_model_output(
    make_agent,
    context_result_factory,
    prompt: str,
    reply: str,
    must_contain: str,
    must_not_contain: list[str],
):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash=f"leak-{prompt}",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=reply,
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders",
        return_value=[],
    ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            prompt,
            session_id_override=f"openclaw:planner-leak:{prompt}",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert must_contain in result["response"]
    for forbidden in must_not_contain:
        assert forbidden not in result["response"]
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["rendered_via"] == "model_final_wording"


def test_explicit_plan_request_can_still_use_planner_renderer(make_agent, context_result_factory):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="explicit-plan",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Start with an inventory.\n- Map secrets\n- Lock ingress",
            structured_output={
                "summary": "Start with an inventory.",
                "steps": ["Map secrets", "Lock ingress"],
            },
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=MediaAnalysisResult(False, reason="no_external_media")
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.classify",
        return_value={"task_class": "system_design", "risk_flags": [], "confidence_hint": 0.74},
    ), mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "give me a step-by-step plan to harden this architecture",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert str(result["response"] or "").strip()
    assert len(events) == 1
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["template_renderer_hit"] is True
    assert events[0]["rendered_via"] == "reasoning_engine"
    assert "-" in result["response"]



def test_hive_followup_emits_backed_tool_claim_metrics(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-followup-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I claimed the Hive task and started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        }
    ]
    hive_state = {
        "pending_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
            "shown_titles": ["Agent Commons: better human-visible watcher and task-flow UX"],
        },
    }

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.session_hive_state", return_value=hive_state
    ), mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-12345678",
        ),
    ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "yes",
            session_id_override="openclaw:hive-truth-metric",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response_class"] == "task_started"
    assert len(events) == 1
    assert events[0]["reason"] == "hive_research_followup"
    assert events[0]["fast_path_hit"] is True
    assert events[0]["model_inference_used"] is False
    assert events[0]["model_final_answer_hit"] is False
    assert events[0]["tool_claim_present"] is True
    assert events[0]["tool_backed_claim_present"] is True
    assert events[0]["tool_backed_claim_count"] == 1
    assert "hive" in events[0]["tool_backing_sources"]


def test_hive_activity_command_emits_model_wording_metrics(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
        True,
        {
            "command_kind": "task_list",
            "watcher_status": "ok",
            "response_text": (
                "Available Hive tasks right now (watcher-derived; presence fresh (22s old); 2 total):\n"
                "- [open] OpenClaw integration audit (#7d33994f)\n"
                "- [researching] Hive footer cleanup (#ada43859)\n"
                "If you want, I can start one. Just point at the task name or short `#id`."
            ),
            "truth_source": "watcher",
            "truth_label": "watcher-derived",
            "truth_status": "ok",
            "presence_claim_state": "visible",
            "presence_source": "watcher",
            "presence_truth_label": "watcher-derived",
            "presence_freshness_label": "fresh",
            "presence_age_seconds": 22,
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                },
                {
                    "topic_id": "ada43859-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "Hive footer cleanup",
                    "status": "researching",
                },
            ],
            "online_agents": [],
        },
    )
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-activity-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see two Hive tasks open right now: OpenClaw integration audit and Hive footer cleanup. Point me at one and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log:
        result = agent.run_once(
            "show me the open hive tasks",
            session_id_override="openclaw:hive-activity-metrics",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response_class"] == "task_list"
    assert len(events) == 1
    assert events[0]["reason"] == "hive_activity_model_wording"
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["tool_backing_sources"] == ["hive"]
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input
    assert "topics" in model_input
    assert "watcher-derived" in model_input
    assert "presence" in model_input
    assert "fresh" in model_input
    assert "available hive tasks right now" not in model_input


def test_hive_status_followup_emits_model_wording_metrics(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-status-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Agent Commons: better human-visible watcher and task-flow UX is still researching. "
                "There is 1 active claim, 1 result post, and 2 artifacts so far."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )
    hive_state = {
        "watched_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_payload": {"active_topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"},
    }
    packet = {
        "topic": {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
        },
        "truth_source": "public_bridge",
        "truth_label": "public-bridge-derived",
        "truth_transport": "direct",
        "truth_timestamp": "2026-03-13T09:10:00+00:00",
        "execution_state": {
            "execution_state": "claimed",
            "active_claim_count": 1,
            "artifact_count": 2,
        },
        "counts": {"post_count": 1, "active_claim_count": 1},
        "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
    }

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.session_hive_state", return_value=hive_state
    ), mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
        agent.public_hive_bridge, "get_public_research_packet", return_value=packet
    ):
        result = agent.run_once(
            "what is the status",
            session_id_override="openclaw:hive-status-metrics",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response_class"] == "task_status"
    assert len(events) == 1
    assert events[0]["reason"] == "hive_status_model_wording"
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["tool_backing_sources"] == ["hive"]
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "public-bridge-derived" in model_input


def test_credit_status_emits_model_wording_metrics(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="credit-status-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="You currently have 42.50 compute credits. Plain public Hive posts do not mint credits by themselves.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.credit_ledger.reconcile_ledger",
        return_value=SimpleNamespace(balance=42.5, entries=3, mode="simulated"),
    ), mock.patch(
        "core.scoreboard_engine.get_peer_scoreboard",
        return_value=SimpleNamespace(provider=12.0, validator=1.5, trust=0.8, tier="Newcomer"),
    ), mock.patch("core.dna_wallet_manager.DNAWalletManager.get_status", return_value=None), mock.patch(
        "network.signer.get_local_peer_id",
        return_value="peer-test-123",
    ):
        result = agent.run_once(
            "what is my credit balance?",
            session_id_override="openclaw:credit-status-metrics",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert result["response_class"] == "utility_answer"
    assert len(events) == 1
    assert events[0]["reason"] == "credit_status_model_wording"
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False


@mock.patch("core.agent_runtime.agent.audit_logger.log")
@mock.patch("core.agent_runtime.agent.adapt_user_input")
def test_greeting_evaluative_and_help_emit_fast_path_metrics(
    adapt_user_input_mock: mock.Mock,
    audit_log: mock.Mock,
    make_agent,
    context_result_factory,
):
    agent = make_agent()

    prompts = [
        ("hey", "hey"),
        ("hello", "hello"),
        ("how are you", "running clean. what do you need?"),
        ("help", "wired on this runtime:"),
        ("you sound weird", "routing is still too stitched together."),
    ]

    def _adapt(text: str, session_id: str | None = None, **_kwargs):
        return SimpleNamespace(
            reconstructed_text=text,
            normalized_text=text,
            understanding_confidence=0.86,
            topic_hints=[],
            quality_flags=[],
            as_context=lambda: {},
        )

    adapt_user_input_mock.side_effect = _adapt
    session_prefix = uuid.uuid4().hex

    for prompt, reply in prompts:
        agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("greeting/evaluative/help metrics should stay on fast path"))  # type: ignore[assignment]
        result = agent.run_once(
            prompt,
            session_id_override=f"openclaw:{session_prefix}:{prompt}",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        assert reply in result["response"].lower()

    events = _chat_truth_events(audit_log)
    fast_path_events = [event for event in events if event.get("rendered_via") == "fast_path"]

    assert len(fast_path_events) == 5
    for event in fast_path_events:
        assert event["fast_path_hit"] is True
        assert event["model_inference_used"] is False
        assert event["model_final_answer_hit"] is False
        assert event["template_renderer_hit"] is False


def test_live_info_emits_model_wording_metrics_with_web_backing(make_agent, context_result_factory, enable_web):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="live-info-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are the canonical source for these updates.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
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
        agent.run_once(
            "latest telegram bot api updates",
            session_id_override="openclaw:live-info-metrics",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    events = _chat_truth_events(audit_log)
    assert len(events) == 1
    assert events[0]["reason"] == "live_info_model_wording"
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["tool_backing_sources"] == ["web_lookup"]
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input or "answer only using the search results below" in model_input
    assert "sources" in model_input
    assert "live web results for" not in model_input


def test_builder_controller_chat_surface_uses_model_wording_over_structured_observations(
    make_agent,
    context_result_factory,
):
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="workspace-build-metrics",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "I finished a bounded Telegram build loop in `generated/telegram-bot`, wrote the files, and the compile check passed. "
                "Start with the README and `src/bot.py`."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )

    def _execute_tool_intent(
        payload: dict[str, object],
        *,
        task_id: str,
        session_id: str,
        source_context: dict[str, object] | None,
        hive_activity_tracker,
        public_hive_bridge=None,
        checkpoint_id=None,
        step_index=0,
        trusted_local_only=False,
    ):
        tool_name = str(payload.get("intent") or "")
        arguments = dict(payload.get("arguments") or {})
        if tool_name == "workspace.write_file":
            path = str(arguments["path"])
            content = str(arguments.get("content") or "")
            return SimpleNamespace(
                handled=True,
                ok=True,
                status="executed",
                response_text=f"Created file `{path}` with {len(content.splitlines())} lines.",
                mode="tool_executed",
                tool_name="workspace.write_file",
                details={
                    "path": path,
                    "line_count": len(content.splitlines()),
                    "artifacts": [
                        {
                            "artifact_type": "file_diff",
                            "path": path,
                            "action": "created",
                            "line_count": len(content.splitlines()),
                            "diff_preview": f"--- a/{path}\n+++ b/{path}\n@@\n+created",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": path,
                        "line_count": len(content.splitlines()),
                        "action": "created",
                    },
                },
            )
        if tool_name == "sandbox.run_command":
            return SimpleNamespace(
                handled=True,
                ok=True,
                status="executed",
                response_text="Command executed in `.`:\n$ python3 -m compileall -q generated/telegram-bot/src\n- Exit code: 0",
                mode="tool_executed",
                tool_name="sandbox.run_command",
                details={
                    "command": "python3 -m compileall -q generated/telegram-bot/src",
                    "cwd": ".",
                    "returncode": 0,
                    "artifacts": [
                        {
                            "artifact_type": "command_output",
                            "command": "python3 -m compileall -q generated/telegram-bot/src",
                            "cwd": ".",
                            "returncode": 0,
                            "stdout": "",
                            "stderr": "",
                            "status": "executed",
                        }
                    ],
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 -m compileall -q generated/telegram-bot/src",
                        "cwd": ".",
                        "returncode": 0,
                    },
                },
            )
        raise AssertionError(f"unexpected tool call: {tool_name}")

    with mock.patch("core.agent_runtime.agent.audit_logger.log") as audit_log, mock.patch(
        "core.agent_runtime.agent.classify",
        return_value={"task_class": "integration_orchestration", "risk_flags": [], "confidence_hint": 0.78},
    ), mock.patch(
        "core.agent_runtime.agent.execute_tool_intent",
        side_effect=_execute_tool_intent,
    ), mock.patch("core.agent_runtime.agent.ingest_media_evidence", return_value=[]), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard", return_value=None):
        result = agent.run_once(
            "build a telegram bot in the workspace and write the files",
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "workspace": "/tmp/vool-builder-smoke",
            },
        )

    events = _chat_truth_events(audit_log)
    assert result["response"].startswith("I finished a bounded Telegram build loop")
    assert result["model_execution"]["used_model"] is True
    assert result["mode"] == "tool_executed"
    assert result["details"]["builder_controller"]["mode"] == "scaffold"
    assert result["details"]["builder_controller"]["step_count"] == 5
    assert result["details"]["builder_controller"]["stop_reason"] == "command_stop_after_success"
    assert result["details"]["builder_controller"]["artifacts"]["file_diffs"]
    assert result["details"]["builder_controller"]["artifacts"]["command_outputs"]
    assert len(events) == 1
    assert events[0]["reason"] == "builder_controller_model_wording"
    assert events[0]["fast_path_hit"] is False
    assert events[0]["model_inference_used"] is True
    assert events[0]["model_final_answer_hit"] is True
    assert events[0]["template_renderer_hit"] is False
    assert events[0]["tool_backing_sources"] == ["workspace", "sandbox"]
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input
    assert "bounded_builder" in model_input
    assert "executed_steps" in model_input
    assert "step_count" in model_input
    assert "artifacts" in model_input
    assert "generated/telegram-bot" in model_input
    assert "compileall" in model_input
    assert "wrote a telegram python scaffold" not in model_input
    assert "files written:" not in model_input
    history = list(agent.memory_router.resolve.call_args.kwargs["source_context"].get("conversation_history") or [])
    joined_history = "\n".join(str(item.get("content") or "") for item in history)
    assert '"receipt_id": "tool-receipt-' in joined_history
    assert '"safe_summary": "workspace.write_file:' in joined_history
    assert '"safe_summary": "sandbox.run_command:' in joined_history
    assert '"intent":' not in joined_history
    assert "Real tool result from" not in joined_history
    assert "Artifacts:" in result["response"]


@pytest.mark.parametrize('prompt', [
    'Explain in two sentences how a finite state machine differs from a queue.',
    'Explain the event loop architecture tradeoffs.',
])
@pytest.mark.parametrize('source,used_model', [
    ('no_provider_available', False),
    ('selected_model_blocked', True),
    ('selected_model_blocked', False),
    ('provider', True),
])
def test_native_terminal_event_agrees_with_provider_outcome(
    make_agent, context_result_factory, prompt, source, used_model,
):
    """A normal-return refusal must not publish completion to the native companion."""
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())
    agent.memory_router.resolve = mock.Mock(return_value=ModelExecutionDecision(
        source=source, task_hash='native-terminal', provider_id='synthetic-free-provider',
        used_model=used_model,
        output_text='A state machine tracks transitions. A queue stores pending work.' if source == 'provider' else '',
        confidence=0.82, trust_score=0.82,
        details={'model_was_attempted': used_model},
    ))
    agent.curiosity.maybe_roam = mock.Mock(return_value=CuriosityResult(enabled=False, mode='off', reason='test'))
    agent.media_pipeline.analyze = mock.Mock(return_value=MediaAnalysisResult(False, reason='no_external_media'))
    with mock.patch.object(agent, '_emit_runtime_event', wraps=agent._emit_runtime_event) as emit, \
         mock.patch('core.agent_runtime.agent.WebAdapter.planned_search_query', side_effect=AssertionError('No lookup authorized')), \
         mock.patch('core.agent_runtime.agent.ingest_media_evidence', return_value=[]), \
         mock.patch('core.agent_runtime.agent.orchestrate_parent_task', return_value=None), \
         mock.patch('core.agent_runtime.agent.request_relevant_holders', return_value=[]), \
         mock.patch('core.agent_runtime.agent.dispatch_query_shard', return_value=None):
        result = agent.run_once(prompt, source_context={'surface': 'openclaw', 'platform': 'openclaw'})
    assert result['model_execution']['source'] == source
    terminal = [c.kwargs['event_type'] for c in emit.call_args_list
                if c.kwargs.get('event_type') in {'task_completed', 'task_failed', 'task_cancelled'}]
    assert terminal == ['task_completed' if source == 'provider' else 'task_failed']
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(result)
    blocked = source == 'selected_model_blocked' and not used_model
    expected = 'fulfilled' if source == 'provider' else 'blocked' if blocked else 'failed'
    assert outcome.fulfillment_status.value == expected
    if expected != 'fulfilled':
        assert outcome.retryable is (not blocked)


@pytest.mark.parametrize('prompt', [
    'Explain in two sentences how a finite state machine differs from a queue.',
    'Explain the event loop architecture tradeoffs.',
])
@pytest.mark.parametrize('missing,recovered', [('mermaid', False), ('table', False), ('chart', True)])
def test_native_terminal_event_agrees_with_output_validation(
    make_agent, context_result_factory, prompt, missing, recovered,
):
    """A completed provider call cannot publish Done over a rejected output."""
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())
    control = {
        'fallback_applied': not recovered,
        'constraint_violations': [] if recovered else ['presentation_format'],
        'response_constraint': {
            'initial_missing_formats': [missing], 'retry_attempted': True,
            'retry_succeeded': recovered, 'compliant': recovered,
        },
    }
    if not recovered:
        control['fulfillment_outcome'] = {
            'fulfillment_status': 'failed', 'failure_stage': 'output_validation',
            'failure_codes': ['response_constraint:presentation_format'], 'retryable': True,
        }

    def resolve(**kwargs):
        kwargs['source_context']['response_control'] = control
        return ModelExecutionDecision(
            source='provider', task_hash='validation-terminal', provider_id='synthetic-free-provider',
            used_model=True, output_text='A state machine tracks transitions. A queue stores pending work.' if recovered else
                '| Result |\n|---|\n| No usable answer was produced for this request. |',
            confidence=0.82, trust_score=0.82,
        )

    agent.memory_router.resolve = mock.Mock(side_effect=resolve)
    agent.curiosity.maybe_roam = mock.Mock(return_value=CuriosityResult(enabled=False, mode='off', reason='test'))
    agent.media_pipeline.analyze = mock.Mock(return_value=MediaAnalysisResult(False, reason='no_external_media'))
    with mock.patch.object(agent, '_emit_runtime_event', wraps=agent._emit_runtime_event) as emit, \
         mock.patch('core.agent_runtime.agent.WebAdapter.planned_search_query', side_effect=AssertionError('No lookup authorized')), \
         mock.patch('core.agent_runtime.agent.ingest_media_evidence', return_value=[]), \
         mock.patch('core.agent_runtime.agent.orchestrate_parent_task', return_value=None), \
         mock.patch('core.agent_runtime.agent.request_relevant_holders', return_value=[]), \
         mock.patch('core.agent_runtime.agent.dispatch_query_shard', return_value=None):
        result = agent.run_once(prompt, source_context={'surface': 'openclaw', 'platform': 'openclaw'})
    terminal = [c.kwargs for c in emit.call_args_list
                if c.kwargs.get('event_type') in {'task_completed', 'task_failed', 'task_cancelled'}]
    assert [e['event_type'] for e in terminal] == ['task_completed' if recovered else 'task_failed']
    from core.runtime_task_outcome import terminal_fulfillment_outcome

    outcome = terminal_fulfillment_outcome(result, source_context=result.get('source_context'))
    assert outcome.fulfillment_status.value == ('fulfilled' if recovered else 'failed')
    if not recovered:
        assert terminal[0]['fulfillment_outcome']['failure_stage'] == 'output_validation'
        assert terminal[0]['fulfillment_outcome']['retryable'] is True
