from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from apps.vool_agent import (
    ChatTurnResult,
    VoolAgent,
    ResponseClass,
    adapt_user_input,
    append_conversation_event,
    audit_logger,
    build_generalized_query,
    build_media_context_snippets,
    build_plan,
    dispatch_query_shard,
    explicit_planner_style_requested,
    feedback_engine,
    from_task_result,
    ingest_media_evidence,
    orchestrate_parent_task,
    policy_engine,
    render_response,
    request_relevant_holders,
    should_use_planner_renderer,
)
from core.identity_manager import load_active_persona
from core.memory_first_router import ModelExecutionDecision
from storage.shard_reuse_outcomes import summarize_reuse_outcomes_for_shards


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _configure_grounded_turn_agent(
    agent: VoolAgent, *, context_result: Any, queries_run: list[str] | None = None
) -> tuple[Any, dict[str, str], Any, Any]:
    task = SimpleNamespace(
        task_id="task-123",
        task_summary="Research proof receipts",
        environment_os="darwin",
        environment_shell="zsh",
        environment_runtime="python",
        environment_version_hint="3.12",
    )
    classification = {"task_class": "research"}
    interpreted = adapt_user_input("research proof receipts", session_id="turn-reasoning-session")
    persona = load_active_persona(agent.persona_id)
    adaptive_research = SimpleNamespace(
        enabled=False,
        tool_gap_note="",
        admitted_uncertainty=False,
        notes=[],
        reason="not_needed",
        strategy="none",
        actions_taken=[],
        # The real `AdaptiveResearchResult` always carries this; the double must too, or a caller
        # that reads it is "tested" against a shape the runtime never produces.
        queries_run=list(queries_run or []),
        to_dict=lambda: {"enabled": False, "reason": "not_needed", "strategy": "none", "actions_taken": []},
    )

    agent.context_loader.load = mock.Mock(return_value=context_result)  # type: ignore[assignment]
    agent._should_frontload_curiosity = mock.Mock(return_value=False)  # type: ignore[method-assign]
    agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[method-assign]
    agent._model_routing_profile = mock.Mock(return_value=(classification, {"output_mode": ""}))  # type: ignore[method-assign]
    agent._collect_adaptive_research = mock.Mock(return_value=adaptive_research)  # type: ignore[method-assign]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="reasoning-hash",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="",
            confidence=0.82,
            trust_score=0.82,
        )
    )
    agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
        return_value=SimpleNamespace(
            used_provider=False,
            provider_id="",
            candidate_id="",
            reason="no_media",
            evidence_items=[],
            analysis_text="",
        )
    )
    agent._collect_live_web_notes = mock.Mock(return_value=[])  # type: ignore[method-assign]
    agent._web_note_plan_candidates = mock.Mock(return_value=[])  # type: ignore[method-assign]
    agent._default_gate = mock.Mock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(mode="advice_only", requires_user_approval=False)
    )
    agent._maybe_publish_public_task = mock.Mock(return_value={})  # type: ignore[method-assign]
    agent._grounded_response_class = mock.Mock(return_value=ResponseClass.GENERIC_CONVERSATION)  # type: ignore[method-assign]
    agent._turn_result = mock.Mock(  # type: ignore[method-assign]
        return_value=ChatTurnResult(
            text="rendered answer",
            response_class=ResponseClass.GENERIC_CONVERSATION,
            workflow_summary="workflow summary",
            debug_origin="grounded_plan",
        )
    )
    agent._apply_interaction_transition = mock.Mock()  # type: ignore[method-assign]
    agent._decorate_chat_response = mock.Mock(return_value="decorated answer")  # type: ignore[method-assign]
    agent._emit_chat_truth_metrics = mock.Mock()  # type: ignore[method-assign]
    agent._emit_runtime_event = mock.Mock()  # type: ignore[method-assign]
    agent._finalize_runtime_checkpoint = mock.Mock()  # type: ignore[method-assign]
    agent._runtime_preview = mock.Mock(return_value="preview")  # type: ignore[method-assign]
    agent._task_workflow_summary = mock.Mock(return_value="workflow summary")  # type: ignore[method-assign]
    agent._chat_surface_honest_degraded_response = mock.Mock(return_value="degraded answer")  # type: ignore[method-assign]
    agent._store_local_shard = mock.Mock()  # type: ignore[method-assign]
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()  # type: ignore[method-assign]
    return task, classification, interpreted, persona


def test_execute_grounded_turn_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")
    classification = {"task_class": "research"}
    interpreted = SimpleNamespace(understanding_confidence=0.8)
    persona = mock.sentinel.persona

    with mock.patch(
        "core.agent_runtime.turn_reasoning.execute_grounded_turn",
        return_value={"response": "done"},
    ) as execute_grounded_turn:
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "done"}
    execute_grounded_turn.assert_called_once_with(
        agent,
        task=task,
        effective_input="research proof receipts",
        classification=classification,
        interpreted=interpreted,
        persona=persona,
        session_id="turn-reasoning-session",
        source_context={"surface": "openclaw"},
        adapt_user_input_fn=adapt_user_input,
        ingest_media_evidence_fn=ingest_media_evidence,
        build_media_context_snippets_fn=build_media_context_snippets,
        orchestrate_parent_task_fn=orchestrate_parent_task,
        build_plan_fn=build_plan,
        render_response_fn=render_response,
        explicit_planner_style_requested_fn=explicit_planner_style_requested,
        should_use_planner_renderer_fn=should_use_planner_renderer,
        request_relevant_holders_fn=request_relevant_holders,
        dispatch_query_shard_fn=dispatch_query_shard,
        build_generalized_query_fn=build_generalized_query,
        feedback_engine_module=feedback_engine,
        policy_engine_module=policy_engine,
        from_task_result_fn=from_task_result,
        append_conversation_event_fn=append_conversation_event,
        audit_logger_module=audit_logger,
    )


def test_execute_grounded_turn_uses_app_level_render_override(make_agent, context_result_factory) -> None:
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(
        agent,
        context_result=context_result_factory(local_candidates=[{"candidate_id": "local-1"}], retrieval_confidence_score=0.9),
    )

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None) as orchestrate_parent_task_mock, mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        return_value=SimpleNamespace(confidence=0.72),
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ) as render_response_mock, mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response"] == "decorated answer"
    render_response_mock.assert_called_once()
    orchestrate_parent_task_mock.assert_called_once()


def test_execute_grounded_turn_keeps_a_forbidden_turn_out_of_curiosity_and_parent_orchestration(
    make_agent,
    context_result_factory,
) -> None:
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(
        agent,
        context_result=context_result_factory(
            local_candidates=[], retrieval_confidence_score=0.0
        ),
    )
    agent.curiosity.maybe_roam = mock.Mock()  # type: ignore[assignment]

    with mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ) as orchestrate_parent_task_mock, mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72)
    ), mock.patch("core.agent_runtime.agent.render_response", return_value="rendered answer"), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=True
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "action_policy": "forbidden",
            },
        )

    assert result["response"] == "decorated answer"
    orchestrate_parent_task_mock.assert_not_called()
    agent.curiosity.maybe_roam.assert_not_called()


def test_execute_grounded_turn_uses_app_level_swarm_query_overrides(make_agent, context_result_factory) -> None:
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(
        agent,
        context_result=context_result_factory(local_candidates=[], retrieval_confidence_score=0.0),
    )

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        return_value=SimpleNamespace(confidence=0.72),
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ), mock.patch(
        "core.agent_runtime.agent.build_generalized_query",
        return_value={"query_id": "query-123"},
    ) as build_generalized_query_mock, mock.patch(
        "core.agent_runtime.agent.request_relevant_holders",
        return_value=[],
    ) as request_relevant_holders_mock, mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard",
        return_value=None,
    ) as dispatch_query_shard_mock:
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response"] == "decorated answer"
    build_generalized_query_mock.assert_called_once_with(task, classification)
    request_relevant_holders_mock.assert_called_once_with("research", task.task_summary, query_id="query-123", limit=3)
    dispatch_query_shard_mock.assert_called_once_with({"query_id": "query-123"}, limit=5)


def test_execute_grounded_turn_records_remote_shard_reuse_outcome(make_agent) -> None:
    agent = make_agent()
    citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-123",
        "receipt_id": "receipt-123",
        "source_peer_id": "peer-123",
        "source_node_id": "node-123",
        "manifest_id": "manifest-123",
        "content_hash": "content-123",
        "validation_state": "signature_and_manifest_verified",
        "fetched_at": "2026-03-25T00:00:00+00:00",
    }
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.74,
        assembled_context=lambda: "",
        context_snippets=lambda: [
            {
                "title": "Cached remote shard",
                "source_type": "remote_shard_cache",
                "summary": "Remote proof notes",
                "citation": dict(citation),
            }
        ],
        report=SimpleNamespace(
            retrieval_confidence=0.74,
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )
    task, classification, interpreted, persona = _configure_grounded_turn_agent(agent, context_result=context_result)

    def _plan_with_counterfactual(*, evidence: dict[str, Any], **_: Any) -> Any:
        remote_context = [
            snippet
            for snippet in list(evidence.get("context_snippets") or [])
            if isinstance(snippet, dict)
            and isinstance(snippet.get("citation"), dict)
            and str(snippet["citation"].get("kind") or "").strip() == "remote_shard"
        ]
        if remote_context:
            return SimpleNamespace(confidence=0.72, evidence_sources=["context:remote_shard_cache"])
        return SimpleNamespace(confidence=0.61, evidence_sources=["model_deferred"])

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        side_effect=_plan_with_counterfactual,
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=True),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["swarm_reuse_outcome_count"] == 1
    assert result["swarm_reuse_citations"][0]["selected_for_plan"] is True
    assert result["swarm_reuse_citations"][0]["answer_backed"] is True
    assert result["swarm_reuse_citations"][0]["quality_backed"] is True
    assert result["swarm_reuse_citations"][0]["counterfactual_tested"] is True
    assert result["swarm_reuse_citations"][0]["counterfactual_confidence_drop"] == pytest.approx(0.11)
    assert result["swarm_reuse_citations"][0]["response_reason"] == "grounded_plan_response"
    summary = summarize_reuse_outcomes_for_shards([citation["shard_id"]])
    assert summary[citation["shard_id"]]["total_count"] == 1
    assert summary[citation["shard_id"]]["success_count"] == 1
    assert summary[citation["shard_id"]]["durable_count"] == 1
    assert summary[citation["shard_id"]]["selected_count"] == 1
    assert summary[citation["shard_id"]]["answer_backed_count"] == 1
    assert summary[citation["shard_id"]]["quality_backed_count"] == 1
    assert summary[citation["shard_id"]]["last_outcome_label"] == "durable_success"
    assert summary[citation["shard_id"]]["last_response_class"] == ResponseClass.GENERIC_CONVERSATION.value


def test_execute_grounded_turn_only_marks_first_remote_shard_as_answer_backed(make_agent) -> None:
    agent = make_agent()
    primary_citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-primary",
        "receipt_id": "receipt-primary",
        "source_peer_id": "peer-primary",
        "source_node_id": "node-primary",
        "manifest_id": "manifest-primary",
        "content_hash": "content-primary",
        "validation_state": "signature_and_manifest_verified",
        "fetched_at": "2026-03-25T00:00:00+00:00",
    }
    incidental_citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-incidental",
        "receipt_id": "receipt-incidental",
        "source_peer_id": "peer-incidental",
        "source_node_id": "node-incidental",
        "manifest_id": "manifest-incidental",
        "content_hash": "content-incidental",
        "validation_state": "signature_and_manifest_verified",
        "fetched_at": "2026-03-25T00:00:00+00:00",
    }
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.74,
        assembled_context=lambda: "",
        context_snippets=lambda: [
            {
                "title": "Primary cached remote shard",
                "source_type": "remote_shard_cache",
                "summary": "Primary remote proof notes",
                "citation": dict(primary_citation),
            },
            {
                "title": "Incidental cached remote shard",
                "source_type": "remote_shard_cache",
                "summary": "Incidental remote proof notes",
                "citation": dict(incidental_citation),
            },
        ],
        report=SimpleNamespace(
            retrieval_confidence=0.74,
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )
    task, classification, interpreted, persona = _configure_grounded_turn_agent(agent, context_result=context_result)

    def _plan_with_counterfactual(*, evidence: dict[str, Any], **_: Any) -> Any:
        remote_shard_ids = [
            str(snippet["citation"].get("shard_id") or "").strip()
            for snippet in list(evidence.get("context_snippets") or [])
            if isinstance(snippet, dict)
            and isinstance(snippet.get("citation"), dict)
            and str(snippet["citation"].get("kind") or "").strip() == "remote_shard"
        ]
        if "remote-shard-primary" in remote_shard_ids:
            return SimpleNamespace(confidence=0.72, evidence_sources=["context:remote_shard_cache"])
        return SimpleNamespace(confidence=0.64, evidence_sources=["model_deferred"])

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        side_effect=_plan_with_counterfactual,
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=True),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session-multi",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["swarm_reuse_outcome_count"] == 2
    assert result["swarm_reuse_citations"][0]["selected_for_plan"] is True
    assert result["swarm_reuse_citations"][0]["answer_backed"] is True
    assert result["swarm_reuse_citations"][0]["quality_backed"] is True
    assert result["swarm_reuse_citations"][1]["selected_for_plan"] is False
    assert result["swarm_reuse_citations"][1]["answer_backed"] is False
    assert result["swarm_reuse_citations"][1]["quality_backed"] is False
    summary = summarize_reuse_outcomes_for_shards(
        [primary_citation["shard_id"], incidental_citation["shard_id"]]
    )
    assert summary[primary_citation["shard_id"]]["answer_backed_count"] == 1
    assert summary[primary_citation["shard_id"]]["quality_backed_count"] == 1
    assert summary[incidental_citation["shard_id"]]["answer_backed_count"] == 0
    assert summary[incidental_citation["shard_id"]]["quality_backed_count"] == 0


def test_execute_grounded_turn_requires_counterfactual_loss_before_remote_shard_counts_as_answer_backed(
    make_agent,
) -> None:
    agent = make_agent()
    citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-flat",
        "receipt_id": "receipt-flat",
        "source_peer_id": "peer-flat",
        "source_node_id": "node-flat",
        "manifest_id": "manifest-flat",
        "content_hash": "content-flat",
        "validation_state": "signature_and_manifest_verified",
        "fetched_at": "2026-03-25T00:00:00+00:00",
    }
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.74,
        assembled_context=lambda: "",
        context_snippets=lambda: [
            {
                "title": "Cached remote shard",
                "source_type": "remote_shard_cache",
                "summary": "Remote proof notes",
                "citation": dict(citation),
            }
        ],
        report=SimpleNamespace(
            retrieval_confidence=0.74,
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )
    task, classification, interpreted, persona = _configure_grounded_turn_agent(agent, context_result=context_result)

    def _flat_plan(*, evidence: dict[str, Any], **_: Any) -> Any:
        remote_context = [
            snippet
            for snippet in list(evidence.get("context_snippets") or [])
            if isinstance(snippet, dict)
            and isinstance(snippet.get("citation"), dict)
            and str(snippet["citation"].get("kind") or "").strip() == "remote_shard"
        ]
        if remote_context:
            return SimpleNamespace(confidence=0.72, evidence_sources=["context:remote_shard_cache"])
        return SimpleNamespace(confidence=0.72, evidence_sources=["context:remote_shard_cache"])

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        side_effect=_flat_plan,
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=True),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session-flat",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["swarm_reuse_outcome_count"] == 1
    assert result["swarm_reuse_citations"][0]["selected_for_plan"] is True
    assert result["swarm_reuse_citations"][0]["answer_backed"] is False
    assert result["swarm_reuse_citations"][0]["quality_backed"] is False
    assert result["swarm_reuse_citations"][0]["counterfactual_tested"] is True
    assert result["swarm_reuse_citations"][0]["counterfactual_confidence_drop"] == 0.0
    assert result["swarm_reuse_citations"][0]["counterfactual_winning_source_changed"] is False
    summary = summarize_reuse_outcomes_for_shards([citation["shard_id"]])
    assert summary[citation["shard_id"]]["selected_count"] == 1
    assert summary[citation["shard_id"]]["answer_backed_count"] == 0
    assert summary[citation["shard_id"]]["quality_backed_count"] == 0


def test_execute_grounded_turn_keeps_answer_backed_remote_shard_non_quality_backed_when_final_response_is_template_fallback(
    make_agent,
) -> None:
    agent = make_agent()
    citation = {
        "kind": "remote_shard",
        "shard_id": "remote-shard-template-fallback",
        "receipt_id": "receipt-template-fallback",
        "source_peer_id": "peer-template-fallback",
        "source_node_id": "node-template-fallback",
        "manifest_id": "manifest-template-fallback",
        "content_hash": "content-template-fallback",
        "validation_state": "signature_and_manifest_verified",
        "fetched_at": "2026-03-25T00:00:00+00:00",
    }
    context_result = SimpleNamespace(
        local_candidates=[],
        swarm_metadata=[],
        retrieval_confidence_score=0.74,
        assembled_context=lambda: "",
        context_snippets=lambda: [
            {
                "title": "Cached remote shard",
                "source_type": "remote_shard_cache",
                "summary": "Remote proof notes",
                "citation": dict(citation),
            }
        ],
        report=SimpleNamespace(
            retrieval_confidence=0.74,
            total_tokens_used=lambda: 0,
            to_dict=lambda: {"external_evidence_attachments": []},
        ),
    )
    task, classification, interpreted, persona = _configure_grounded_turn_agent(agent, context_result=context_result)
    agent._decorate_chat_response = mock.Mock(  # type: ignore[method-assign]
        return_value="Using best known pattern for this answer while evidence is thin."
    )

    def _plan_with_counterfactual(*, evidence: dict[str, Any], **_: Any) -> Any:
        remote_context = [
            snippet
            for snippet in list(evidence.get("context_snippets") or [])
            if isinstance(snippet, dict)
            and isinstance(snippet.get("citation"), dict)
            and str(snippet["citation"].get("kind") or "").strip() == "remote_shard"
        ]
        if remote_context:
            return SimpleNamespace(confidence=0.72, evidence_sources=["context:remote_shard_cache"])
        return SimpleNamespace(confidence=0.61, evidence_sources=["model_deferred"])

    with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.build_plan",
        side_effect=_plan_with_counterfactual,
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="rendered answer",
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested",
        return_value=False,
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer",
        return_value=True,
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=True, is_durable=True),
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.apply",
        return_value=None,
    ):
        result = agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session-template-fallback",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["swarm_reuse_citations"][0]["selected_for_plan"] is True
    assert result["swarm_reuse_citations"][0]["answer_backed"] is True
    assert result["swarm_reuse_citations"][0]["quality_backed"] is False
    summary = summarize_reuse_outcomes_for_shards([citation["shard_id"]])
    assert summary[citation["shard_id"]]["answer_backed_count"] == 1
    assert summary[citation["shard_id"]]["quality_backed_count"] == 0


def _drive_grounded_turn(agent, task, classification, interpreted, persona):
    """Run the turn with its heavy collaborators stubbed -- the file's existing pattern."""
    with mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
    ), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_media_context_snippets", return_value=[]
    ), mock.patch(
        "core.agent_runtime.agent.build_plan", return_value=SimpleNamespace(confidence=0.72)
    ), mock.patch(
        "core.agent_runtime.agent.render_response", return_value="rendered answer"
    ), mock.patch(
        "core.agent_runtime.agent.explicit_planner_style_requested", return_value=False
    ), mock.patch(
        "core.agent_runtime.agent.should_use_planner_renderer", return_value=True
    ), mock.patch(
        "core.agent_runtime.agent.feedback_engine.evaluate_outcome",
        return_value=SimpleNamespace(is_success=False, is_durable=False),
    ), mock.patch("core.agent_runtime.agent.feedback_engine.apply", return_value=None):
        return agent._execute_grounded_turn(
            task=task,
            effective_input="research proof receipts",
            classification=classification,
            interpreted=interpreted,
            persona=persona,
            session_id="turn-reasoning-session",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )


def test_a_turn_that_already_searched_does_not_roam_again_after_the_answer(
    make_agent, context_result_factory
) -> None:
    """The post-response roam must not add its own web exploration to a turn that searched.

    Measured on the served daemon (build 9e437955, "What is the latest stable version of
    Python?"): adaptive research ran two searches, the model answered at 14:37:24, and the reply
    reached the client 96 s later -- `curiosity_roam_completed` at 14:39:00, `task_completed` the
    same second, nothing in between but 30 s maintenance ticks. The roam runs AFTER
    `render_response`, so none of that time could change a word of the answer.
    """
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(
        agent,
        context_result=context_result_factory(local_candidates=[], retrieval_confidence_score=0.0),
        queries_run=["latest stable version of python"],
    )
    from core.curiosity_roamer import CuriosityResult

    # A REAL result, so that with the gate disabled the turn still completes and the failure is
    # this test's own assertion ("the roam ran") rather than a TypeError three frames downstream.
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    result = _drive_grounded_turn(agent, task, classification, interpreted, persona)

    assert result["response"] == "decorated answer"
    assert not agent.curiosity.maybe_roam.called, (
        "the post-response roam ran on a turn that had already searched, adding its own web "
        "exploration to a reply the reader was already waiting for"
    )


def test_a_turn_that_searched_nothing_still_roams(make_agent, context_result_factory) -> None:
    """The control. The gate is about DUPLICATE research, not about disabling curiosity."""
    agent = make_agent()
    task, classification, interpreted, persona = _configure_grounded_turn_agent(
        agent,
        context_result=context_result_factory(local_candidates=[], retrieval_confidence_score=0.0),
        queries_run=[],
    )
    from core.curiosity_roamer import CuriosityResult

    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    _drive_grounded_turn(agent, task, classification, interpreted, persona)

    agent.curiosity.maybe_roam.assert_called_once()
