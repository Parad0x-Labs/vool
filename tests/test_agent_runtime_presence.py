from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime import presence


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_sync_public_presence_uses_app_level_name_provider() -> None:
    agent = _build_agent()

    with mock.patch("core.agent_runtime.agent.get_agent_display_name", return_value="Patched Agent"), mock.patch.object(
        agent.public_hive_bridge,
        "sync_presence",
        return_value={"ok": True, "status": "posted"},
    ) as sync_presence:
        agent._sync_public_presence(status="idle", source_context={"surface": "openclaw"})

    assert sync_presence.call_args.kwargs["agent_name"] == "Patched Agent"
    assert agent._public_presence_registered is True


def test_sync_public_presence_queues_async_for_live_runtime() -> None:
    agent = VoolAgent(backend_name="torch-mps", device="mps", persona_id="default")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    seen: dict[str, object] = {}

    def slow_sync_presence(**kwargs):
        seen.update(kwargs)
        started.set()
        release.wait(timeout=2.0)
        finished.set()
        return {"ok": True, "status": "posted"}

    with mock.patch("core.agent_runtime.agent.get_agent_display_name", return_value="Async Agent"), mock.patch.object(
        agent.public_hive_bridge,
        "sync_presence",
        side_effect=slow_sync_presence,
    ):
        started_at = time.perf_counter()
        agent._sync_public_presence(status="idle", source_context={"surface": "openclaw"})
        elapsed = time.perf_counter() - started_at
        assert elapsed < 0.25
        assert started.wait(timeout=1.0) is True
        release.set()
        assert finished.wait(timeout=1.0) is True

    deadline = time.time() + 1.0
    while time.time() < deadline and not agent._public_presence_registered:
        time.sleep(0.01)

    assert seen["agent_name"] == "Async Agent"
    assert agent._public_presence_registered is True


def test_start_public_presence_heartbeat_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.presence.start_public_presence_heartbeat",
        return_value=None,
    ) as start_public_presence_heartbeat:
        agent._start_public_presence_heartbeat()

    start_public_presence_heartbeat.assert_called_once_with(
        agent,
        thread_factory=mock.ANY,
    )


def test_start_idle_commons_loop_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.presence.start_idle_commons_loop",
        return_value=None,
    ) as start_idle_commons_loop:
        agent._start_idle_commons_loop()

    start_idle_commons_loop.assert_called_once_with(
        agent,
        thread_factory=mock.ANY,
    )


def test_maybe_run_idle_commons_once_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.presence.maybe_run_idle_commons_once",
        return_value=None,
    ) as maybe_run_idle_commons_once:
        agent._maybe_run_idle_commons_once()

    maybe_run_idle_commons_once.assert_called_once_with(
        agent,
        load_preferences_fn=mock.ANY,
        time_fn=mock.ANY,
        audit_log_fn=mock.ANY,
    )


def test_maybe_run_autonomous_hive_research_once_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.presence.maybe_run_autonomous_hive_research_once",
        return_value=None,
    ) as maybe_run_autonomous_hive_research_once:
        agent._maybe_run_autonomous_hive_research_once()

    maybe_run_autonomous_hive_research_once.assert_called_once_with(
        agent,
        load_preferences_fn=mock.ANY,
        time_fn=mock.ANY,
        pick_signal_fn=mock.ANY,
        research_topic_fn=mock.ANY,
        audit_log_fn=mock.ANY,
    )


def test_normalize_public_presence_status_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._normalize_public_presence_status("busy") == presence.normalize_public_presence_status(agent, "busy")


def test_idle_public_presence_status_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._idle_public_presence_status() == presence.idle_public_presence_status(
        load_preferences_fn=mock.Mock(return_value=mock.Mock(accept_hive_tasks=True))
    )


def test_maybe_run_idle_commons_once_updates_tracking_and_publish_state() -> None:
    agent = _build_agent()
    agent._idle_commons_session_id = mock.Mock(return_value="agent-commons:test")  # type: ignore[method-assign]
    agent.curiosity.run_idle_commons = mock.Mock(  # type: ignore[assignment]
        return_value={
            "candidate_id": "candidate-1",
            "topic": {"topic": "Local-first agent swarms", "topic_kind": "technical"},
            "summary": "Local-first summary",
            "public_body": "Local-first public body",
            "topic_tags": ["agents", "mesh"],
        }
    )
    agent.public_hive_bridge.publish_agent_commons_update = mock.Mock(  # type: ignore[assignment]
        return_value={"topic_id": "topic-123", "status": "published"}
    )
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()  # type: ignore[assignment]
    agent._last_user_activity_ts = 0.0
    agent._last_idle_commons_ts = 0.0
    agent._idle_commons_seed_index = 7

    audit_log = mock.Mock()

    presence.maybe_run_idle_commons_once(
        agent,
        load_preferences_fn=lambda: SimpleNamespace(social_commons=True),
        time_fn=lambda: 2000.0,
        audit_log_fn=audit_log,
    )

    agent.curiosity.run_idle_commons.assert_called_once_with(
        session_id="agent-commons:test",
        task_id="agent-commons",
        trace_id="agent-commons",
        seed_index=7,
    )
    agent.public_hive_bridge.publish_agent_commons_update.assert_called_once()
    agent.hive_activity_tracker.note_watched_topic.assert_called_once_with(
        session_id="agent-commons:test",
        topic_id="topic-123",
    )
    assert agent._last_idle_commons_ts == 2000.0
    assert agent._idle_commons_seed_index == 8
    audit_log.assert_called()


def test_maybe_run_autonomous_hive_research_once_updates_presence_and_tracking() -> None:
    agent = _build_agent()
    agent.public_hive_bridge.enabled = mock.Mock(return_value=True)  # type: ignore[assignment]
    agent.public_hive_bridge.list_public_research_queue = mock.Mock(  # type: ignore[assignment]
        return_value=[{"topic_id": "topic-777"}]
    )
    agent._sync_public_presence = mock.Mock()  # type: ignore[method-assign]
    agent._idle_public_presence_status = mock.Mock(return_value="idle")  # type: ignore[method-assign]
    agent.hive_activity_tracker.note_watched_topic = mock.Mock()  # type: ignore[assignment]
    agent._last_user_activity_ts = 0.0
    agent._last_idle_hive_research_ts = 0.0
    audit_log = mock.Mock()
    result = SimpleNamespace(ok=True, topic_id="topic-777", to_dict=lambda: {"topic_id": "topic-777", "ok": True})
    research_topic = mock.Mock(return_value=result)

    presence.maybe_run_autonomous_hive_research_once(
        agent,
        load_preferences_fn=lambda: SimpleNamespace(accept_hive_tasks=True, idle_research_assist=True),
        time_fn=lambda: 2000.0,
        pick_signal_fn=lambda rows: {"topic_id": "topic-777"},
        research_topic_fn=research_topic,
        audit_log_fn=audit_log,
    )

    research_topic.assert_called_once_with(
        {"topic_id": "topic-777"},
        public_hive_bridge=agent.public_hive_bridge,
        curiosity=agent.curiosity,
        hive_activity_tracker=agent.hive_activity_tracker,
        session_id="auto-research:topic-777",
        auto_claim=True,
    )
    assert agent._sync_public_presence.call_args_list == [
        mock.call(
            status="busy",
            source_context={"surface": "background", "platform": "openclaw", "lane": "autonomous_research"},
        ),
        mock.call(
            status="idle",
            source_context={"surface": "background", "platform": "openclaw", "lane": "autonomous_research"},
        ),
    ]
    agent.hive_activity_tracker.note_watched_topic.assert_called_once_with(
        session_id="auto-research:topic-777",
        topic_id="topic-777",
    )
    assert agent._last_idle_hive_research_ts == 2000.0
    audit_log.assert_called()
