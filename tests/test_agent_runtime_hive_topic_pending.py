from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import hive_topic_create, hive_topic_pending
from core.agent_runtime.hive_topic_pending_history import (
    recover_hive_create_pending_from_history as history_recover_hive_create_pending_from_history,
)
from core.agent_runtime.hive_topic_pending_payloads import (
    build_hive_create_pending_payload,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_hive_topic_pending_exports_stay_available_from_hive_topic_create() -> None:
    assert (
        hive_topic_create.maybe_handle_hive_create_confirmation
        is hive_topic_pending.maybe_handle_hive_create_confirmation
    )
    assert hive_topic_create.load_pending_hive_create is hive_topic_pending.load_pending_hive_create
    assert hive_topic_pending.recover_hive_create_pending_from_history is history_recover_hive_create_pending_from_history


def test_load_pending_hive_create_restores_payload_from_interaction_state() -> None:
    agent = _build_agent()
    stored = {
        "title": "Improve proof routing",
        "summary": "Improve proof routing summary",
        "topic_tags": ["proof"],
        "task_id": "task-123",
        "auto_start_research": True,
        "default_variant": "improved",
        "variants": {
            "improved": {
                "title": "Improve proof routing",
                "summary": "Improve proof routing summary",
                "topic_tags": ["proof"],
                "auto_start_research": True,
                "preview_note": "Safe to post.",
            }
        },
        "original_blocked_reason": "",
    }

    pending = hive_topic_pending.load_pending_hive_create(
        agent,
        session_id="sess-1",
        source_context=None,
        fallback_task_id="fallback-task",
        allow_history_recovery=False,
        session_hive_state_fn=lambda _: {
            "interaction_mode": "hive_topic_create_pending",
            "interaction_payload": {"pending_hive_create": stored},
        },
    )

    assert pending is not None
    assert pending["title"] == "Improve proof routing"
    assert pending["task_id"] == "task-123"
    assert agent._hive_create_pending["sess-1"]["title"] == "Improve proof routing"


def test_is_pending_hive_create_confirmation_input_requires_pending_state() -> None:
    agent = _build_agent()
    assert not hive_topic_pending.is_pending_hive_create_confirmation_input(
        agent,
        "yes",
        session_id="sess-2",
        source_context=None,
        hive_state={"interaction_payload": {}},
    )

    agent._hive_create_pending["sess-2"] = {"title": "Improve proof routing"}
    assert hive_topic_pending.is_pending_hive_create_confirmation_input(
        agent,
        "yes",
        session_id="sess-2",
        source_context=None,
        hive_state={"interaction_payload": {}},
    )


def test_recover_hive_create_pending_from_history_rebuilds_pending_payload() -> None:
    agent = _build_agent()
    pending = hive_topic_pending.recover_hive_create_pending_from_history(
        agent,
        history=[
            {
                "role": "user",
                "content": "create hive task: Task: improve proof routing Goal: make trace proof links clearer Tags: proof, ux",
            }
        ],
        fallback_task_id="task-xyz",
    )

    assert pending is not None
    assert pending["task_id"] == "task-xyz"
    assert pending["default_variant"] == "improved"
    assert pending["variants"]["improved"]["title"]


def test_build_hive_create_pending_payload_normalizes_variant_payload() -> None:
    agent = _build_agent()

    payload = build_hive_create_pending_payload(
        agent,
        {
            "title": "Improve proof routing",
            "summary": "Improve proof routing summary",
            "topic_tags": ["proof"],
            "task_id": "task-123",
            "auto_start_research": True,
            "variants": {
                "improved": {
                    "title": "Improve proof routing",
                    "summary": "Improve proof routing summary",
                    "topic_tags": ["proof"],
                    "auto_start_research": True,
                    "preview_note": "Safe to post.",
                }
            },
        },
    )

    assert payload["title"] == "Improve proof routing"
    assert payload["task_id"] == "task-123"
    assert payload["variants"]["improved"]["preview_note"] == "Safe to post."


def test_maybe_handle_hive_create_confirmation_can_cancel_pending_preview() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch.object(
        agent,
        "_load_pending_hive_create",
        return_value={"title": "Improve proof routing", "variants": {"improved": {"title": "Improve proof routing"}}},
    ) as load_pending, mock.patch.object(
        agent,
        "_clear_hive_create_pending",
    ) as clear_pending:
        result = hive_topic_pending.maybe_handle_hive_create_confirmation(
            agent,
            "no",
            task=task,
            session_id="sess-3",
            source_context={"surface": "openclaw"},
        )

    assert result is not None
    assert result["response"] == "Got it -- Hive task discarded. What's next?"
    load_pending.assert_called_once()
    clear_pending.assert_called_once_with("sess-3")


def test_maybe_handle_hive_create_confirmation_rejects_orphaned_variant_choice() -> None:
    agent = _build_agent()
    task = mock.Mock(task_id="task-123")

    with mock.patch.object(
        agent,
        "_load_pending_hive_create",
        return_value=None,
    ) as load_pending:
        result = hive_topic_pending.maybe_handle_hive_create_confirmation(
            agent,
            "send improved",
            task=task,
            session_id="sess-4",
            source_context={"surface": "openclaw"},
        )

    assert result is not None
    assert "There isn't a pending Hive draft to confirm" in result["response"]
    load_pending.assert_called_once()
