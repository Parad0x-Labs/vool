from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.agent_runtime import (
    hive_topic_delete_effects,
    hive_topic_delete_failures,
    hive_topic_delete_preflight,
    hive_topic_delete_runtime,
    hive_topic_update_effects,
    hive_topic_update_failures,
    hive_topic_update_preflight,
    hive_topic_update_runtime,
)


def _build_task() -> SimpleNamespace:
    return SimpleNamespace(task_id="task-123")


def _build_action_agent() -> SimpleNamespace:
    return SimpleNamespace(
        public_hive_bridge=SimpleNamespace(
            enabled=lambda: True,
            write_enabled=lambda: True,
            update_public_topic=lambda **_: {
                "ok": True,
                "topic_id": "topic-1",
                "topic_result": {"title": "Updated title"},
            },
            delete_public_topic=lambda **_: {"ok": True},
        ),
        _resolve_hive_topic_for_mutation=lambda **_: {"topic_id": "topic-1", "title": "Tracked topic"},
        _extract_hive_topic_hint=lambda text: "topic-1",
        _extract_hive_topic_update_draft=lambda text: {"title": "Better title", "summary": "Better summary", "topic_tags": ["runtime"]},
        _prepare_public_hive_topic_copy=lambda **_: {"ok": True, "title": "Better title", "summary": "Better summary"},
        hive_activity_tracker=SimpleNamespace(note_watched_topic=lambda **_: None),
        _action_fast_path_result=lambda **kwargs: kwargs,
    )


def test_hive_topic_update_runtime_delegates_to_split_helpers() -> None:
    agent = _build_action_agent()
    task = _build_task()

    with mock.patch(
        "core.agent_runtime.hive_topic_update_runtime.prepare_hive_topic_update_request",
        return_value={"ok": True, "topic": {"topic_id": "topic-1"}, "update_draft": {}, "public_copy": {}, "next_title": "Next title"},
    ) as prepare_mock, mock.patch(
        "core.agent_runtime.hive_topic_update_runtime.finalize_hive_topic_update",
        return_value={"response": "updated"},
    ) as finalize_mock:
        result = hive_topic_update_runtime.handle_hive_topic_update_request(
            agent,
            "update hive task topic-1 with better title",
            task=task,
            session_id="session-1",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "updated"}
    prepare_mock.assert_called_once_with(
        agent,
        "update hive task topic-1 with better title",
        task=task,
        session_id="session-1",
        source_context={"surface": "openclaw"},
    )
    finalize_mock.assert_called_once_with(
        agent,
        task=task,
        session_id="session-1",
        source_context={"surface": "openclaw"},
        user_input="update hive task topic-1 with better title",
        topic={"topic_id": "topic-1"},
        update_draft={},
        public_copy={},
        next_title="Next title",
    )


def test_hive_topic_update_preflight_blocks_missing_target() -> None:
    agent = _build_action_agent()
    agent._resolve_hive_topic_for_mutation = lambda **_: None  # type: ignore[assignment]

    result = hive_topic_update_preflight.prepare_hive_topic_update_request(
        agent,
        "update hive task with better title",
        task=_build_task(),
        session_id="session-1",
        source_context={"surface": "openclaw"},
    )

    assert result["ok"] is False
    assert result["result"]["reason"] == "hive_topic_update_missing_target"
    assert result["result"]["details"] == {"status": "missing_topic"}


def test_hive_topic_update_effects_maps_route_unavailable() -> None:
    agent = _build_action_agent()
    agent.public_hive_bridge.update_public_topic = lambda **_: {"ok": False, "status": "route_unavailable"}  # type: ignore[assignment]

    result = hive_topic_update_effects.finalize_hive_topic_update(
        agent,
        task=_build_task(),
        session_id="session-1",
        source_context={"surface": "openclaw"},
        user_input="update hive task topic-1 with better title",
        topic={"topic_id": "topic-1", "title": "Tracked topic", "topic_tags": ["runtime"]},
        update_draft={"title": "Better title", "summary": "Better summary", "topic_tags": ["runtime"]},
        public_copy={"title": "Better title", "summary": "Better summary"},
        next_title="Next title",
    )

    assert result["reason"] == "hive_topic_update_route_unavailable"
    assert result["details"] == {"status": "route_unavailable"}
    assert hive_topic_update_effects.build_hive_topic_update_failure_result is hive_topic_update_failures.build_hive_topic_update_failure_result


def test_hive_topic_delete_runtime_delegates_to_split_helpers() -> None:
    agent = _build_action_agent()
    task = _build_task()

    with mock.patch(
        "core.agent_runtime.hive_topic_delete_runtime.prepare_hive_topic_delete_request",
        return_value={"ok": True, "topic": {"topic_id": "topic-1", "title": "Tracked topic"}},
    ) as prepare_mock, mock.patch(
        "core.agent_runtime.hive_topic_delete_runtime.finalize_hive_topic_delete",
        return_value={"response": "deleted"},
    ) as finalize_mock:
        result = hive_topic_delete_runtime.handle_hive_topic_delete_request(
            agent,
            "delete hive task topic-1",
            task=task,
            session_id="session-1",
            source_context={"surface": "openclaw"},
        )

    assert result == {"response": "deleted"}
    prepare_mock.assert_called_once_with(
        agent,
        "delete hive task topic-1",
        task=task,
        session_id="session-1",
        source_context={"surface": "openclaw"},
    )
    finalize_mock.assert_called_once_with(
        agent,
        task=task,
        session_id="session-1",
        source_context={"surface": "openclaw"},
        user_input="delete hive task topic-1",
        topic={"topic_id": "topic-1", "title": "Tracked topic"},
    )


def test_hive_topic_delete_preflight_blocks_missing_target() -> None:
    agent = _build_action_agent()
    agent._resolve_hive_topic_for_mutation = lambda **_: None  # type: ignore[assignment]

    result = hive_topic_delete_preflight.prepare_hive_topic_delete_request(
        agent,
        "delete hive task topic-1",
        task=_build_task(),
        session_id="session-1",
        source_context={"surface": "openclaw"},
    )

    assert result["ok"] is False
    assert result["result"]["reason"] == "hive_topic_delete_missing_target"
    assert result["result"]["details"] == {"status": "missing_topic"}


def test_hive_topic_delete_effects_maps_not_deletable() -> None:
    agent = _build_action_agent()
    agent.public_hive_bridge.delete_public_topic = lambda **_: {"ok": False, "status": "not_deletable"}  # type: ignore[assignment]

    result = hive_topic_delete_effects.finalize_hive_topic_delete(
        agent,
        task=_build_task(),
        session_id="session-1",
        source_context={"surface": "openclaw"},
        user_input="delete hive task topic-1",
        topic={"topic_id": "topic-1", "title": "Tracked topic"},
    )

    assert result["reason"] == "hive_topic_delete_not_deletable"
    assert result["details"] == {"status": "not_deletable"}
    assert hive_topic_delete_effects.build_hive_topic_delete_failure_result is hive_topic_delete_failures.build_hive_topic_delete_failure_result
