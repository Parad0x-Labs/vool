from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent


def _build_agent() -> VoolAgent:
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent.start()
    return agent


def test_hive_task_preview_requires_confirm_before_create() -> None:
    agent = _build_agent()
    request_text = (
        "create hive mind task: Task: stand alone vool brwoser version. "
        "Goal: make it work without OpenClaw and keep all toolings."
    )

    with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
        preview = agent.run_once(
            request_text,
            session_id_override="acceptance:hive-preview",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert "Ready to post this to the public Hive" in preview["response"]
    create_public_topic.assert_not_called()


def test_hive_task_confirm_posts_after_preview() -> None:
    agent = _build_agent()
    request_text = (
        "create hive mind task: Task: stand alone vool brwoser version. "
        "Goal: make it work without OpenClaw and keep all toolings."
    )

    with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge,
        "create_public_topic",
        return_value={"ok": True, "status": "created", "topic_id": "feedbeef-1111-2222-3333-444444444444"},
    ) as create_public_topic, mock.patch.object(agent.hive_activity_tracker, "note_watched_topic", return_value=None):
        agent.run_once(
            request_text,
            session_id_override="acceptance:hive-confirm",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        confirm = agent.run_once(
            "yes improved",
            session_id_override="acceptance:hive-confirm",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert "Created Hive task" in confirm["response"]
    assert create_public_topic.call_count == 1
