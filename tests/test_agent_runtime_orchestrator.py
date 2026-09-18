from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import ChatTurnResult, VoolAgent, ResponseClass
from core.agent_runtime import orchestrator


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_apply_interaction_transition_uses_app_level_state_accessors() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.agent.session_hive_state",
        return_value={"interaction_mode": "", "interaction_payload": {}, "pending_topic_ids": []},
    ) as session_hive_state_mock, mock.patch("core.agent_runtime.agent.set_hive_interaction_state") as set_hive_interaction_state_mock:
        agent._apply_interaction_transition(
            "session-123",
            ChatTurnResult(text="Pick one by name.", response_class=ResponseClass.TASK_SELECTION_CLARIFICATION),
        )

    session_hive_state_mock.assert_called_once_with("session-123")
    set_hive_interaction_state_mock.assert_called_once_with(
        "session-123",
        mode="hive_task_selection_pending",
        payload={},
    )


def test_emit_runtime_event_uses_app_level_emitter_and_checkpoint_id() -> None:
    agent = _build_agent()

    with mock.patch.object(agent, "_runtime_checkpoint_id", return_value="checkpoint-123"), mock.patch(
        "core.agent_runtime.agent.emit_runtime_event"
    ) as emit_runtime_event_mock:
        agent._emit_runtime_event(
            {"runtime_event_stream_id": "stream-1"},
            event_type="task_started",
            message="Task started",
            request_id="req-1",
        )

    emit_runtime_event_mock.assert_called_once_with(
        {"runtime_event_stream_id": "stream-1"},
        event_type="task_started",
        message="Task started",
        details={"request_id": "req-1", "checkpoint_id": "checkpoint-123"},
    )


def test_tool_loop_final_message_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    synthesis = SimpleNamespace(structured_output={"summary": "Done", "bullets": ["step one", "step two"]}, output_text="")

    assert agent._tool_loop_final_message(synthesis, [{"summary": "last step"}]) == orchestrator.tool_loop_final_message(
        synthesis,
        [{"summary": "last step"}],
    )


def test_tool_loop_final_message_falls_back_to_git_summary_observation() -> None:
    synthesis = SimpleNamespace(structured_output={}, output_text="")
    message = orchestrator.tool_loop_final_message(
        synthesis,
        [
            {
                "tool_name": "workspace.git_summary",
                "summary": "Git summary: branch main @ abc123; 7 visible branches (5 local, 2 remote tracking); 3 commits on 2026-03-30; 1 commits on 2026-03-29; dirty no",
                "observation": {
                    "intent": "workspace.git_summary",
                    "cwd": "/tmp/repo",
                    "branch": "main",
                    "commit": "abc123",
                    "dirty": False,
                    "local_branch_count": 5,
                    "remote_branch_count": 2,
                    "total_branch_count": 7,
                    "today_date": "2026-03-30",
                    "today_commit_count": 3,
                    "yesterday_date": "2026-03-29",
                    "yesterday_commit_count": 1,
                    "commit_count_scope": "all_visible_refs_unique",
                    "timezone_label": "UTC",
                },
            }
        ],
    )

    assert "Git repo summary for `/tmp/repo`:" in message
    assert "- visible branches: 7 total (5 local, 2 remote tracking)" in message
    assert "- commits on 2026-03-30: 3" in message
    assert "- commits on 2026-03-29: 1" in message


def test_runtime_preview_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "This is a long enough response to preview cleanly across the extracted orchestrator module boundary."

    assert agent._runtime_preview(text, limit=40) == orchestrator.runtime_preview(text, limit=40)


def test_render_tool_loop_response_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    steps = [{"tool_name": "workspace.read_file", "summary": "loaded app config"}]

    assert agent._render_tool_loop_response(
        final_message="done",
        executed_steps=steps,
        include_step_summary=True,
    ) == orchestrator.render_tool_loop_response(
        final_message="done",
        executed_steps=steps,
        include_step_summary=True,
    )


def test_live_runtime_stream_enabled_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    source_context = {"runtime_event_stream_id": "stream-123"}

    assert agent._live_runtime_stream_enabled(source_context) == orchestrator.live_runtime_stream_enabled(source_context)


def test_task_workflow_summary_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    context_result = SimpleNamespace(report=SimpleNamespace(retrieval_confidence=0.82))

    assert agent._task_workflow_summary(
        classification={"task_class": "debugging"},
        context_result=context_result,
        model_execution={"provider_id": "ollama:qwen", "used_model": True},
        media_analysis={"reason": "no_external_media"},
        curiosity_result={"mode": "off"},
        gate_mode="tool_preview",
    ) == orchestrator.task_workflow_summary(
        classification={"task_class": "debugging"},
        context_result=context_result,
        model_execution={"provider_id": "ollama:qwen", "used_model": True},
        media_analysis={"reason": "no_external_media"},
        curiosity_result={"mode": "off"},
        gate_mode="tool_preview",
    )
