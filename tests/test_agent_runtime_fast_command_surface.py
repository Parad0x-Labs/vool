from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_fast_command_surface_credit_command_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.fast_command_surface.maybe_handle_credit_command",
        return_value={"response": "delegated credit"},
    ) as maybe_handle_credit_command:
        result = agent._maybe_handle_credit_command(
            "send 5 credits to peer-1",
            session_id="session-123",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "delegated credit"}
    maybe_handle_credit_command.assert_called_once_with(
        agent,
        "send 5 credits to peer-1",
        session_id="session-123",
        source_context={"surface": "openclaw"},
        signer_module=mock.ANY,
        transfer_credits_fn=mock.ANY,
        get_credit_balance_fn=mock.ANY,
        escrow_credits_for_task_fn=mock.ANY,
        session_hive_state_fn=mock.ANY,
        runtime_session_id_fn=mock.ANY,
    )


def test_fast_command_surface_fast_path_result_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.fast_command_surface.fast_path_result",
        return_value={"response": "delegated fast path"},
    ) as fast_path_result:
        result = agent._fast_path_result(
            session_id="session-123",
            user_input="what time is it?",
            response="Current time is 12:00.",
            confidence=0.97,
            source_context={"surface": "openclaw"},
            reason="date_time_fast_path",
        )

    assert result == {"response": "delegated fast path"}
    fast_path_result.assert_called_once_with(
        agent,
        session_id="session-123",
        user_input="what time is it?",
        response="Current time is 12:00.",
        confidence=0.97,
        source_context={"surface": "openclaw"},
        reason="date_time_fast_path",
        append_conversation_event_fn=mock.ANY,
        audit_logger_module=mock.ANY,
    )


def test_fast_command_surface_action_fast_path_result_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.fast_command_surface.action_fast_path_result",
        return_value={"response": "delegated action"},
    ) as action_fast_path_result:
        result = agent._action_fast_path_result(
            task_id="task-123",
            session_id="session-123",
            user_input="post this to telegram",
            response="Queued the post.",
            confidence=0.91,
            source_context={"surface": "openclaw", "platform": "openclaw"},
            reason="channel_post_action",
            success=True,
            workflow_summary="posted",
        )

    assert result == {"response": "delegated action"}
    action_fast_path_result.assert_called_once_with(
        agent,
        task_id="task-123",
        session_id="session-123",
        user_input="post this to telegram",
        response="Queued the post.",
        confidence=0.91,
        source_context={"surface": "openclaw", "platform": "openclaw"},
        reason="channel_post_action",
        success=True,
        details=None,
        mode_override=None,
        task_outcome=None,
        learned_plan=None,
        workflow_summary="posted",
        append_conversation_event_fn=mock.ANY,
        audit_logger_module=mock.ANY,
        explicit_planner_style_requested_fn=mock.ANY,
    )


def test_successful_action_fast_path_emits_completed_runtime_event() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ), mock.patch.object(agent, "_finalize_runtime_checkpoint") as finalize_checkpoint:
        result = agent._action_fast_path_result(
            task_id="task-123",
            session_id="session-123",
            user_input="refresh the models catalog",
            response="Catalog refreshed from openrouter.ai just now — 18 free of 341 models.",
            confidence=0.9,
            source_context={"surface": "desktop", "_owner_local": True},
            reason="catalog_refresh_intent",
            success=True,
            details={"tool_backed": True},
            task_outcome="success",
        )

    assert result["mode"] == "tool_queued"
    assert any(event["event_type"] == "task_completed" for event in events)
    assert not any(event["event_type"] == "task_failed" for event in events)
    assert finalize_checkpoint.call_args.kwargs["status"] == "completed"


def test_failed_action_fast_path_still_emits_failed_runtime_event() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ), mock.patch.object(agent, "_finalize_runtime_checkpoint") as finalize_checkpoint:
        result = agent._action_fast_path_result(
            task_id="task-123",
            session_id="session-123",
            user_input="refresh the models catalog",
            response="I could not reach openrouter.ai.",
            confidence=0.9,
            source_context={"surface": "desktop", "_owner_local": True},
            reason="catalog_refresh_intent",
            success=False,
            details={"tool_backed": True},
            task_outcome="failed",
        )

    assert result["mode"] == "tool_failed"
    assert any(event["event_type"] == "task_failed" for event in events)
    assert not any(event["event_type"] == "task_completed" for event in events)
    assert finalize_checkpoint.call_args.kwargs["status"] == "failed"


def test_direct_workspace_pyproject_read_uses_workspace_tool_without_planner() -> None:
    agent = _build_agent()
    execution = SimpleNamespace(
        ok=True,
        response_text="File `pyproject.toml`:\n1: [project]\n2: name = \"vool-hive-mind\"\n3: requires-python = \">=3.11\"",
        details={
            "path": "pyproject.toml",
            "lines": [
                {"line_number": 1, "text": "[project]"},
                {"line_number": 2, "text": 'name = "vool-hive-mind"'},
                {"line_number": 3, "text": 'requires-python = ">=3.11"'},
            ],
        },
    )

    with mock.patch.object(agent, "_plan_tool_workflow") as planner, mock.patch(
        "core.agent_runtime.fast_paths_utility.execute_runtime_tool",
        return_value=execution,
    ) as execute_runtime_tool, mock.patch.object(agent, "_fast_path_result", return_value={"response": "ok"}) as fast_path_result:
        result = agent._maybe_handle_direct_workspace_runtime_request(
            "Using workspace tools only, read pyproject. toml and tell me the project name plus the Python version requirement.",
            session_id="session-123",
            source_surface="runtime",
            source_context={"surface": "openclaw", "workspace_root": "/tmp/workspace"},
        )

    assert result == {"response": "ok"}
    planner.assert_not_called()
    execute_runtime_tool.assert_called_once_with(
        "workspace.read_file",
        # 160 -> 2000 on 2026-08-03: the lane no longer overrides the tool's own default
        # downward. What this test pins is that the read happens WITHOUT the planner.
        {"path": "pyproject.toml", "start_line": 1, "max_lines": 2000},
        source_context={"surface": "openclaw", "workspace_root": "/tmp/workspace"},
    )
    assert fast_path_result.call_args.kwargs["reason"] == "workspace_runtime_fast_path"
    assert fast_path_result.call_args.kwargs["response"] == (
        "Project name: `vool-hive-mind`. Python requirement: `>=3.11`. Read via `workspace.read_file`."
    )


def test_fast_path_result_emits_lane_proof_without_model_inference() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ):
        result = agent._fast_path_result(
            session_id="session-123",
            user_input="hi",
            response="Hi.",
            confidence=0.97,
            source_context={"surface": "openclaw"},
            reason="smalltalk_fast_path",
        )

    proof = next(event for event in events if event["event_type"] == "model_lane_proof")
    assert result["model_execution"] == {"source": "fast_path", "used_model": False}
    assert proof["schema"] == "vool.model_lane_proof.v1"
    assert proof["lane"] == "tiny"
    assert proof["phase"] == "completed"
    assert proof["provider_id"] == "runtime-fast-path"
    assert proof["actual_adapter_provider_id"] == ""
    assert proof["measurement_source"] == "deterministic_fast_path"
    assert proof["fallback_reason"] == "model_not_used"
    assert proof["speculative_status"] == "inactive"


def test_fast_path_result_marks_explicit_heavy_model_blocked_as_deep_blocked() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ):
        agent._fast_path_result(
            session_id="session-123",
            user_input="Explicitly use qwen3.5:35b-a3b.",
            response="blocked",
            confidence=0.99,
            source_context={"surface": "openclaw"},
            reason="explicit_heavy_model_blocked",
        )

    proof = next(event for event in events if event["event_type"] == "model_lane_proof")
    assert proof["lane"] == "deep"
    assert proof["complexity"] == "hard"
    assert proof["phase"] == "blocked"
    assert proof["planned_model_id"] == "qwen3.5:35b-a3b"
    assert proof["fallback_reason"] == "explicit_heavy_lane_unavailable"
    assert proof["verifier_status"] == "blocked_no_primary"


def test_action_fast_path_result_emits_lane_proof_without_model_inference() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ):
        result = agent._action_fast_path_result(
            task_id="task-123",
            session_id="session-123",
            user_input="run the workflow",
            response="That request did not resolve cleanly.",
            confidence=0.61,
            source_context={"surface": "openclaw"},
            reason="tool_workflow_failed",
            success=False,
            task_outcome="failed",
        )

    proof = next(event for event in events if event["event_type"] == "model_lane_proof")
    assert result["model_execution"] == {"source": "channel_action", "used_model": False}
    assert proof["schema"] == "vool.model_lane_proof.v1"
    assert proof["lane"] == "daily"
    assert proof["phase"] == "failed"
    assert proof["provider_id"] == "runtime-action"
    assert proof["backend"] == "tool_workflow"
    assert proof["measurement_source"] == "runtime_action_path"
    assert proof["fallback_reason"] == "model_not_used"
    assert proof["speculative_status"] == "inactive"


def test_model_tool_intent_advice_result_does_not_overwrite_model_lane_proof() -> None:
    agent = _build_agent()
    events: list[dict] = []

    with mock.patch.object(
        agent,
        "_emit_runtime_event",
        side_effect=lambda source_context, **payload: events.append({"source_context": source_context, **payload}),
    ):
        result = agent._action_fast_path_result(
            task_id="task-123",
            session_id="session-123",
            user_input="explain the patch plan without editing files",
            response="Use the model lane and report proof.",
            confidence=0.72,
            source_context={"surface": "openclaw"},
            reason="model_tool_intent_direct_response",
            success=True,
            details={"tool_steps": []},
            mode_override="advice_only",
            task_outcome="success",
        )

    assert result["model_execution"] == {"source": "channel_action", "used_model": False}
    assert not [event for event in events if event["event_type"] == "model_lane_proof"]


def test_fast_command_surface_help_and_capability_truth_facades_delegate_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.fast_command_surface.help_capabilities_text",
        return_value="help text",
    ) as help_capabilities_text, mock.patch(
        "core.agent_runtime.fast_command_surface.maybe_handle_capability_truth_request",
        return_value={"response": "truth"},
    ) as maybe_handle_capability_truth_request:
        help_text = agent._help_capabilities_text()
        result = agent._maybe_handle_capability_truth_request(
            "can you send email?",
            session_id="session-123",
            source_context={"surface": "openclaw"},
        )

    assert help_text == "help text"
    assert result == {"response": "truth"}
    help_capabilities_text.assert_called_once_with(agent)
    maybe_handle_capability_truth_request.assert_called_once_with(
        agent,
        "can you send email?",
        session_id="session-123",
        source_context={"surface": "openclaw"},
        capability_truth_for_request_fn=mock.ANY,
        render_capability_truth_response_fn=mock.ANY,
    )
