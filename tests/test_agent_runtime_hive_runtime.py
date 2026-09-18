from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import hive_runtime


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_recover_hive_runtime_command_input_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    text = "check the hive tasks please"

    assert agent._recover_hive_runtime_command_input(text) == hive_runtime.recover_hive_runtime_command_input(
        agent,
        text,
        looks_like_semantic_hive_request_fn=mock.Mock(return_value=False),
    )


def test_maybe_handle_hive_runtime_command_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.hive_runtime.maybe_handle_hive_runtime_command",
        return_value=(True, "ok", True, {"command_kind": "list"}),
    ) as maybe_handle_hive_runtime_command:
        result = agent._maybe_handle_hive_runtime_command("show me the hive tasks", session_id="hive-runtime-session")

    assert result == (True, "ok", True, {"command_kind": "list"})
    maybe_handle_hive_runtime_command.assert_called_once_with(
        agent,
        "show me the hive tasks",
        session_id="hive-runtime-session",
    )


def test_maybe_handle_hive_runtime_command_uses_app_level_bridge_fallback_override() -> None:
    agent = _build_agent()

    with mock.patch.object(
        agent.hive_activity_tracker,
        "maybe_handle_command_details",
        return_value=(True, {"response_text": "I couldn't reach the Hive watcher", "command_kind": "watcher_unavailable"}),
    ), mock.patch.object(
        agent,
        "_maybe_handle_hive_bridge_fallback",
        return_value={"response_text": "bridge fallback reply", "command_kind": "task_list_bridge_fallback"},
    ) as maybe_handle_hive_bridge_fallback:
        result = agent._maybe_handle_hive_runtime_command("show me hive tasks", session_id="hive-runtime-session")

    assert result == (
        True,
        "bridge fallback reply",
        True,
        {"response_text": "bridge fallback reply", "command_kind": "task_list_bridge_fallback"},
    )
    maybe_handle_hive_bridge_fallback.assert_called_once()


def test_store_hive_topic_selection_state_preserves_existing_watched_topics() -> None:
    topics = [
        {"topic_id": "topic-1", "title": "First topic"},
        {"topic_id": "topic-2", "title": "Second topic"},
    ]
    update_session_hive_state = mock.Mock()

    hive_runtime.store_hive_topic_selection_state(
        "session-hive-selection",
        topics,
        session_hive_state_fn=mock.Mock(
            return_value={
                "watched_topic_ids": ["watched-1"],
                "seen_post_ids": ["post-1"],
                "seen_curiosity_topic_ids": ["curiosity-1"],
                "seen_curiosity_run_ids": ["run-1"],
                "seen_agent_ids": ["agent-1"],
                "last_active_agents": 2,
            }
        ),
        update_session_hive_state_fn=update_session_hive_state,
    )

    update_session_hive_state.assert_called_once_with(
        "session-hive-selection",
        watched_topic_ids=["watched-1"],
        seen_post_ids=["post-1"],
        pending_topic_ids=["topic-1", "topic-2"],
        seen_curiosity_topic_ids=["curiosity-1"],
        seen_curiosity_run_ids=["run-1"],
        seen_agent_ids=["agent-1"],
        last_active_agents=2,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={"shown_topic_ids": ["topic-1", "topic-2"], "shown_titles": ["First topic", "Second topic"]},
    )


def test_recover_hive_runtime_command_input_ignores_workspace_path_with_hive_and_work_substrings() -> None:
    agent = _build_agent()
    prompt = (
        "Create a file named vool_test_01.txt in "
        "/Users/test/vool-hive-mind/artifacts/acceptance_runs/2026-03-27-fresh-proof/workspace/main "
        "with exactly this content: ALPHA-LOCAL-FILE-01"
    )

    recovered = hive_runtime.recover_hive_runtime_command_input(
        agent,
        prompt,
        looks_like_semantic_hive_request_fn=mock.Mock(return_value=False),
    )

    assert recovered == ""


def test_extract_hive_topic_create_draft_ignores_workspace_path_with_topics_segment() -> None:
    agent = _build_agent()
    prompt = (
        "Create a file named vool_test_01.txt in "
        "/Users/test/vool-hive-mind/artifacts/acceptance_runs/2026-03-31-dialogue-topics-clean/workspace/main "
        "with exactly this content: ALPHA-LOCAL-FILE-01"
    )

    assert agent._extract_hive_topic_create_draft(prompt) is None
