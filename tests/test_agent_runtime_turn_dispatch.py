from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import (
    VoolAgent,
    classify,
    dispatch_operator_action,
    dispatch_outbound_post_intent,
    parse_channel_post_intent,
    parse_operator_action_intent,
)
from core.agent_runtime import turn_dispatch


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_prepare_turn_task_bundle_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    interpreted = SimpleNamespace(as_context=lambda: {"intent": "test"})

    with mock.patch(
        "core.agent_runtime.turn_dispatch.prepare_turn_task_bundle",
        return_value={"task": mock.Mock(task_id="task-123"), "classification": {"task_class": "research"}},
    ) as prepare_turn_task_bundle:
        result = agent._prepare_turn_task_bundle(
            effective_input="research this",
            user_input="research this",
            session_id="turn-dispatch-session",
            source_context={"surface": "openclaw"},
            interpreted=interpreted,
        )

    assert result["classification"]["task_class"] == "research"
    prepare_turn_task_bundle.assert_called_once_with(
        agent,
        effective_input="research this",
        user_input="research this",
        session_id="turn-dispatch-session",
        source_context={"surface": "openclaw"},
        interpreted=interpreted,
        classify_fn=classify,
        parse_channel_post_intent_fn=parse_channel_post_intent,
        dispatch_outbound_post_intent_fn=dispatch_outbound_post_intent,
        parse_operator_action_intent_fn=parse_operator_action_intent,
        dispatch_operator_action_fn=dispatch_operator_action,
    )


def test_prepare_turn_task_bundle_uses_app_level_hive_confirmation_override() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")
    interpreted = SimpleNamespace(as_context=lambda: {"intent": "test"})

    with mock.patch.object(agent, "_resolve_runtime_task", return_value=task), mock.patch.object(
        agent,
        "_update_runtime_checkpoint_context",
    ), mock.patch.object(
        agent,
        "_update_task_class",
    ), mock.patch.object(
        agent,
        "_emit_runtime_event",
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_create_confirmation",
        return_value={"response": "confirmed"},
    ) as maybe_handle_hive_create_confirmation:
        result = turn_dispatch.prepare_turn_task_bundle(
            agent,
            effective_input="yes",
            user_input="yes",
            session_id="turn-dispatch-session",
            source_context={"surface": "openclaw"},
            interpreted=interpreted,
            classify_fn=mock.Mock(return_value={"task_class": "research"}),
            parse_channel_post_intent_fn=mock.Mock(return_value=(None, None)),
            dispatch_outbound_post_intent_fn=mock.Mock(),
            parse_operator_action_intent_fn=mock.Mock(return_value=None),
            dispatch_operator_action_fn=mock.Mock(),
        )

    assert result == {"result": {"response": "confirmed"}}
    maybe_handle_hive_create_confirmation.assert_called_once_with(
        "yes",
        task=task,
        session_id="turn-dispatch-session",
        source_context={"surface": "openclaw"},
    )


def test_prepare_turn_task_bundle_returns_task_and_classification_when_no_frontdoor_action_fires() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")
    interpreted = SimpleNamespace(as_context=lambda: {"intent": "test"})
    classify_fn = mock.Mock(return_value={"task_class": "research", "confidence_hint": 0.9})

    with mock.patch.object(agent, "_resolve_runtime_task", return_value=task), mock.patch.object(
        agent,
        "_update_runtime_checkpoint_context",
    ) as update_runtime_checkpoint_context, mock.patch.object(
        agent,
        "_update_task_class",
    ) as update_task_class, mock.patch.object(
        agent,
        "_emit_runtime_event",
    ) as emit_runtime_event, mock.patch.object(
        agent,
        "_maybe_handle_hive_create_confirmation",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_extract_hive_topic_create_draft",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_topic_mutation_request",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_topic_create_request",
        return_value=None,
    ), mock.patch.object(
        agent,
        "_maybe_run_builder_controller",
        return_value=None,
    ):
        result = turn_dispatch.prepare_turn_task_bundle(
            agent,
            effective_input="research this",
            user_input="research this",
            session_id="turn-dispatch-session",
            source_context={"surface": "openclaw"},
            interpreted=interpreted,
            classify_fn=classify_fn,
            parse_channel_post_intent_fn=mock.Mock(return_value=(None, None)),
            dispatch_outbound_post_intent_fn=mock.Mock(),
            parse_operator_action_intent_fn=mock.Mock(return_value=None),
            dispatch_operator_action_fn=mock.Mock(),
        )

    assert result["task"] is task
    assert result["classification"]["task_class"] == "research"
    update_runtime_checkpoint_context.assert_any_call({"surface": "openclaw"}, task_id="task-123")
    update_task_class.assert_called_once_with("task-123", "research")
    emit_runtime_event.assert_called_once()
