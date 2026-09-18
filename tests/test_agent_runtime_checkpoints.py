from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import (
    checkpoints,
    runtime_checkpoint_io_adapter,
    runtime_checkpoint_lane_policy,
)
from core.agent_runtime.request_authority import (
    REQUEST_PROVENANCE_KEY,
    request_provenance_for_visible_user_text,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_prepare_runtime_checkpoint_uses_app_level_runtime_continuity_functions() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.latest_resumable_checkpoint", return_value=None) as latest_resumable_checkpoint_mock, mock.patch(
        "core.agent_runtime.agent.create_runtime_checkpoint",
        return_value={"checkpoint_id": "runtime-checkpoint-1"},
    ) as create_runtime_checkpoint_mock:
        bundle = agent._prepare_runtime_checkpoint(
            session_id="session-123",
            raw_user_input="inspect tool receipts",
            effective_input="inspect tool receipts",
            source_context={"surface": "openclaw"},
        )

    latest_resumable_checkpoint_mock.assert_called_once_with("session-123")
    create_runtime_checkpoint_mock.assert_called_once()
    create_kwargs = create_runtime_checkpoint_mock.call_args.kwargs
    assert create_kwargs["session_id"] == "session-123"
    assert create_kwargs["request_text"] == "inspect tool receipts"
    created_context = dict(create_kwargs["source_context"])
    # A fresh immutable turn id is minted for every user message.
    assert str(created_context.pop("turn_id", "")).startswith("turn-")
    assert created_context.pop(REQUEST_PROVENANCE_KEY) == (
        request_provenance_for_visible_user_text(
            "inspect tool receipts",
            session_id="session-123",
        )
    )
    assert created_context == {
        "surface": "openclaw",
        "runtime_session_id": "session-123",
        "session_id": "session-123",
    }
    assert bundle["state"] == "created"
    assert bundle["source_context"]["runtime_checkpoint_id"] == "runtime-checkpoint-1"
    assert str(bundle["source_context"].get("turn_id") or "").startswith("turn-")


def test_prepare_runtime_checkpoint_adapter_uses_app_level_runtime_continuity_functions() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.latest_resumable_checkpoint", return_value=None), mock.patch(
        "core.agent_runtime.agent.create_runtime_checkpoint",
        return_value={"checkpoint_id": "runtime-checkpoint-1"},
    ):
        direct_bundle = runtime_checkpoint_io_adapter.prepare_runtime_checkpoint(
            agent,
            session_id="session-123",
            raw_user_input="inspect tool receipts",
            effective_input="inspect tool receipts",
            source_context={"surface": "openclaw"},
        )

    assert direct_bundle["state"] == "created"
    assert direct_bundle["source_context"]["runtime_checkpoint_id"] == "runtime-checkpoint-1"


def test_resolve_runtime_task_uses_app_level_task_accessors() -> None:
    agent = _build_agent()
    existing_task = object()

    with mock.patch(
        "core.agent_runtime.agent.get_runtime_checkpoint",
        return_value={"task_id": "task-123"},
    ) as get_runtime_checkpoint_mock, mock.patch(
        "core.agent_runtime.agent.load_task_record",
        return_value=existing_task,
    ) as load_task_record_mock, mock.patch(
        "core.agent_runtime.agent.create_task_record"
    ) as create_task_record_mock:
        resolved = agent._resolve_runtime_task(
            effective_input="inspect checkpoint state",
            session_id="session-123",
            source_context={"runtime_checkpoint_id": "runtime-checkpoint-1"},
        )

    assert resolved is existing_task
    get_runtime_checkpoint_mock.assert_called_once_with("runtime-checkpoint-1")
    load_task_record_mock.assert_called_once_with("task-123")
    create_task_record_mock.assert_not_called()


def test_update_runtime_checkpoint_context_uses_app_level_writer() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.update_runtime_checkpoint") as update_runtime_checkpoint_mock:
        agent._update_runtime_checkpoint_context(
            {"runtime_checkpoint_id": "runtime-checkpoint-1", "surface": "openclaw"},
            task_id="task-123",
            task_class="debugging",
        )

    update_runtime_checkpoint_mock.assert_called_once_with(
        "runtime-checkpoint-1",
        task_id="task-123",
        task_class="debugging",
        source_context={"runtime_checkpoint_id": "runtime-checkpoint-1", "surface": "openclaw"},
    )


def test_finalize_runtime_checkpoint_uses_app_level_writer() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.finalize_runtime_checkpoint") as finalize_runtime_checkpoint_mock:
        agent._finalize_runtime_checkpoint(
            {"runtime_checkpoint_id": "runtime-checkpoint-1"},
            status="completed",
            final_response="done",
            failure_text="",
        )

    finalize_runtime_checkpoint_mock.assert_called_once_with(
        "runtime-checkpoint-1",
        status="completed",
        final_response="done",
        failure_text="",
    )


def test_finalize_runtime_checkpoint_marks_output_fallback_as_failed_fulfillment() -> None:
    agent = _build_agent()
    source_context = {
        "runtime_checkpoint_id": "runtime-checkpoint-fallback",
        "response_control": {
            "fallback_applied": True,
            "ordinary_chat_output": {
                "allowed": False,
                "reasons": ["missing_requested_parts"],
            },
        },
    }

    with mock.patch("core.agent_runtime.agent.finalize_runtime_checkpoint") as writer:
        agent._finalize_runtime_checkpoint(
            source_context,
            status="completed",
            final_response="I couldn't produce a normal response.",
        )

    writer.assert_called_once_with(
        "runtime-checkpoint-fallback",
        status="completed",
        final_response="I couldn't produce a normal response.",
        failure_text="",
        outcome={
            "fulfillment_status": "failed",
            "failure_stage": "output_validation",
            "failure_codes": ["missing_requested_parts"],
            "retryable": True,
        },
    )


def test_merge_runtime_source_contexts_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    primary = {
        "conversation_history": [
            {
                "role": "assistant",
                "content": (
                    "Real tool result from `workspace.search_text`:\n"
                    'Search matches for "tool_intent":\n'
                    "- core/tool_intent_executor.py:42 def execute_tool_intent("
                ),
            }
        ]
    }
    secondary = {"surface": "openclaw", "platform": "openclaw"}

    assert agent._merge_runtime_source_contexts(primary, secondary) == checkpoints.merge_runtime_source_contexts(
        agent,
        primary,
        secondary,
    )
    assert agent._merge_runtime_source_contexts(primary, secondary) == runtime_checkpoint_io_adapter.merge_runtime_source_contexts(
        agent,
        primary,
        secondary,
    )


def test_should_keep_ai_first_chat_lane_facade_matches_extracted_policy() -> None:
    agent = _build_agent()
    classification = {"task_class": "debugging"}
    interpretation = SimpleNamespace(as_context=lambda: {}, topic_hints=[])

    assert agent._should_keep_ai_first_chat_lane(
        user_input="think through this bug and explain the cause",
        classification=classification,
        interpretation=interpretation,
        source_context={"surface": "channel"},
        checkpoint_state={},
    ) == runtime_checkpoint_lane_policy.should_keep_ai_first_chat_lane(
        agent,
        user_input="think through this bug and explain the cause",
        classification=classification,
        interpretation=interpretation,
        source_context={"surface": "channel"},
        checkpoint_state={},
    )


def test_should_not_keep_ai_first_lane_for_runtime_git_truth_prompt() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(as_context=lambda: {}, topic_hints=[])

    keep_lane = agent._should_keep_ai_first_chat_lane(
        user_input="what branch and commit are you running on right now?",
        classification={"task_class": "unknown"},
        interpretation=interpretation,
        source_context={"surface": "api", "platform": "api"},
        checkpoint_state={},
    )

    assert keep_lane is False


def test_describing_a_workspace_is_conversation_not_execution() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(as_context=lambda: {}, topic_hints=[])

    keep_lane = agent._should_keep_ai_first_chat_lane(
        user_input="Exactly four words: describe a workspace where thinking feels easy.",
        classification={"task_class": "unknown"},
        interpretation=interpretation,
        source_context={"surface": "api", "platform": "api"},
        checkpoint_state={},
    )

    assert keep_lane is True
