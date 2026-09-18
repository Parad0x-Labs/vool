from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import (
    hive_topic_delete_runtime,
    hive_topic_mutation_detection,
    hive_topic_mutation_resolver,
    hive_topic_mutation_runtime,
    hive_topic_update_runtime,
    hive_topics,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_maybe_handle_hive_topic_create_request_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch(
        "core.agent_runtime.hive_topics.maybe_handle_hive_topic_create_request",
        return_value={"response": "create preview"},
    ) as maybe_handle_hive_topic_create_request:
        result = agent._maybe_handle_hive_topic_create_request(
            "create hive task: improve proof page receipts",
            task=task,
            session_id="hive-create-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "create preview"}
    maybe_handle_hive_topic_create_request.assert_called_once_with(
        agent,
        "create hive task: improve proof page receipts",
        task=task,
        session_id="hive-create-session",
        source_context={"surface": "openclaw"},
    )


def test_maybe_handle_hive_create_confirmation_uses_app_level_execute_override() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")
    pending = {
        "default_variant": "improved",
        "variants": {"improved": {"title": "Improved title", "summary": "Improved summary"}},
    }

    with mock.patch.object(
        agent,
        "_load_pending_hive_create",
        return_value=pending,
    ) as load_pending, mock.patch.object(
        agent,
        "_clear_hive_create_pending",
    ) as clear_pending, mock.patch.object(
        agent,
        "_execute_confirmed_hive_create",
        return_value={"response": "created"},
    ) as execute_confirmed:
        result = hive_topics.maybe_handle_hive_create_confirmation(
            agent,
            "yes",
            task=task,
            session_id="hive-confirm-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "created"}
    load_pending.assert_called_once()
    clear_pending.assert_called_once_with("hive-confirm-session")
    execute_confirmed.assert_called_once_with(
        pending,
        task=task,
        session_id="hive-confirm-session",
        source_context={"surface": "openclaw"},
        user_input="yes",
        variant="improved",
    )


def test_looks_like_hive_topic_create_request_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "create new hive task: fix open proof receipts"

    assert agent._looks_like_hive_topic_create_request(text) == hive_topics.looks_like_hive_topic_create_request(
        agent,
        text,
    )


def test_resolve_hive_topic_for_mutation_facade_uses_app_level_session_state_override() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.agent.session_hive_state",
        return_value={"interaction_payload": {"active_topic_id": "topic-1"}, "watched_topic_ids": []},
    ) as session_hive_state_mock, mock.patch.object(
        agent.public_hive_bridge,
        "get_public_topic",
        return_value={"topic_id": "topic-1", "title": "Tracked topic"},
    ) as get_public_topic:
        result = agent._resolve_hive_topic_for_mutation(
            session_id="hive-mutation-session",
            topic_hint="",
        )

    assert result == {"topic_id": "topic-1", "title": "Tracked topic"}
    session_hive_state_mock.assert_called_once_with("hive-mutation-session")
    get_public_topic.assert_called_once_with("topic-1", include_flagged=True)


def test_maybe_handle_hive_topic_mutation_request_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch(
        "core.agent_runtime.hive_topics.maybe_handle_hive_topic_mutation_request",
        return_value={"response": "mutation reply"},
    ) as maybe_handle_hive_topic_mutation_request:
        result = agent._maybe_handle_hive_topic_mutation_request(
            "update hive task deadbeef with: better summary",
            task=task,
            session_id="hive-mutation-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "mutation reply"}
    maybe_handle_hive_topic_mutation_request.assert_called_once_with(
        agent,
        "update hive task deadbeef with: better summary",
        task=task,
        session_id="hive-mutation-session",
        source_context={"surface": "openclaw"},
    )


def test_maybe_handle_hive_topic_mutation_request_uses_app_level_update_override() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch.object(
        agent,
        "_handle_hive_topic_update_request",
        return_value={"response": "updated"},
    ) as handle_update:
        result = hive_topics.maybe_handle_hive_topic_mutation_request(
            agent,
            "update hive task deadbeef with: better summary",
            task=task,
            session_id="hive-mutation-session",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "updated"}
    handle_update.assert_called_once_with(
        "update hive task deadbeef with: better summary",
        task=task,
        session_id="hive-mutation-session",
        source_context={"surface": "openclaw"},
    )


def test_hive_topic_update_detector_rejects_a_conversational_topic_shift() -> None:
    agent = _build_agent()

    assert not agent._looks_like_hive_topic_update_request(
        "let's change topic completely: why do people keep notebooks?"
    )


def test_hive_topic_mutation_respects_an_explicit_no_action_policy() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch.object(agent, "_handle_hive_topic_update_request") as handle_update:
        result = hive_topics.maybe_handle_hive_topic_mutation_request(
            agent,
            "update hive task deadbeef with a better summary",
            task=task,
            session_id="hive-mutation-session",
            source_context={"action_policy": "forbidden"},
        )

    assert result is None
    handle_update.assert_not_called()


def test_hive_topic_mutation_exports_stay_available_from_legacy_module() -> None:
    assert hive_topics.maybe_handle_hive_topic_mutation_request is hive_topic_mutation_detection.maybe_handle_hive_topic_mutation_request
    assert hive_topics.looks_like_hive_topic_update_request is hive_topic_mutation_detection.looks_like_hive_topic_update_request
    assert hive_topics.looks_like_hive_topic_delete_request is hive_topic_mutation_detection.looks_like_hive_topic_delete_request
    assert hive_topics.extract_hive_topic_update_draft is hive_topic_mutation_detection.extract_hive_topic_update_draft
    assert hive_topics.resolve_hive_topic_for_mutation is hive_topic_mutation_runtime.resolve_hive_topic_for_mutation
    assert hive_topics.handle_hive_topic_update_request is hive_topic_mutation_runtime.handle_hive_topic_update_request
    assert hive_topics.handle_hive_topic_delete_request is hive_topic_mutation_runtime.handle_hive_topic_delete_request


def test_hive_topic_mutation_runtime_reexports_new_leaf_modules() -> None:
    assert hive_topic_mutation_runtime.resolve_hive_topic_for_mutation is hive_topic_mutation_resolver.resolve_hive_topic_for_mutation
    assert hive_topic_mutation_runtime.handle_hive_topic_update_request is hive_topic_update_runtime.handle_hive_topic_update_request
    assert hive_topic_mutation_runtime.handle_hive_topic_delete_request is hive_topic_delete_runtime.handle_hive_topic_delete_request
