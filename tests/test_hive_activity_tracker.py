from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

from apps.vool_agent import VoolAgent
from core.curiosity_roamer import AdaptiveResearchResult
from core.hive_activity_tracker import (
    HiveActivityTracker,
    HiveActivityTrackerConfig,
    load_hive_activity_tracker_config,
    prune_stale_hive_interaction_state,
    session_hive_state,
    snooze_hive_prompts,
    update_session_hive_state,
)
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from storage.curiosity_state import queue_curiosity_topic, record_curiosity_run, update_curiosity_topic
from storage.db import get_connection
from storage.migrations import run_migrations


def setup_function() -> None:
    run_migrations()
    conn = get_connection()
    try:
        for table in (
            "session_hive_watch_state",
            "curiosity_runs",
            "curiosity_topics",
            "local_tasks",
            "learning_shards",
            "knowledge_holders",
            "knowledge_manifests",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def _tracker(payload: dict) -> HiveActivityTracker:
    return HiveActivityTracker(
        HiveActivityTrackerConfig(
            enabled=True,
            watcher_api_url="http://watch.example.test/api/dashboard",
            timeout_seconds=2,
        ),
        fetch_json=lambda url, timeout_seconds, context: {"ok": True, "result": payload},
    )


def test_build_chat_footer_surfaces_new_available_research_and_respects_snooze() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 3},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:hive-footer",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )
    assert "want me to list them" in footer.lower()

    snooze_hive_prompts("openclaw:hive-footer", minutes=60)
    footer = tracker.build_chat_footer(
        session_id="openclaw:hive-footer",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )
    assert "want me to list them" not in footer.lower()


def test_load_hive_activity_tracker_config_uses_live_default_timeout_budget(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_HIVE_WATCH_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("VOOL_HIVE_WATCHER_URL", raising=False)
    monkeypatch.setattr("core.hive_activity_tracker._load_watcher_url_from_manifest", lambda: "https://watch.example.test")
    monkeypatch.setattr("core.hive_activity_tracker._load_agent_bootstrap_tls", lambda: {})

    cfg = load_hive_activity_tracker_config()

    assert cfg.enabled is True
    assert cfg.watcher_api_url == "https://watch.example.test/api/dashboard"
    assert cfg.timeout_seconds == 8


def test_build_chat_footer_reports_watched_topic_updates_once() -> None:
    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    tracker = _tracker(
        {
            "generated_at": fresh_ts,
            "stats": {"active_agents": 2},
            "topics": [
                {
                    "topic_id": "topic-watch-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "Liquefy runtime proof",
                    "status": "researching",
                }
            ],
            "agents": [
                {
                    "agent_id": "peer-agent-1",
                    "display_name": "Scout-1",
                    "status": "online",
                    "online": True,
                }
            ],
            "recent_posts": [
                {
                    "post_id": "post-1",
                    "topic_id": "topic-watch-1",
                    "topic_title": "Liquefy runtime proof",
                }
            ],
        }
    )
    tracker.note_watched_topic(session_id="openclaw:watch", topic_id="topic-watch-1")

    footer = tracker.build_chat_footer(
        session_id="openclaw:watch",
        hive_followups_enabled=True,
        idle_research_assist=False,
    )
    assert "new research post" in footer.lower()
    assert "new agent" in footer.lower()
    assert "watcher-derived" in footer.lower()
    assert "fresh" in footer.lower()

    footer = tracker.build_chat_footer(
        session_id="openclaw:watch",
        hive_followups_enabled=True,
        idle_research_assist=False,
    )
    assert "new research post" not in footer.lower()
    assert "new agent" not in footer.lower()


def test_pull_available_tasks_command_returns_topic_list() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command("pull available tasks now", session_id="openclaw:pull")
    assert handled is True
    assert "available hive tasks" in response.lower()
    state = session_hive_state("openclaw:pull")
    assert state["pending_topic_ids"] == ["topic-1"]


def test_pull_available_tasks_details_include_structured_response_hint() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, details = tracker.maybe_handle_command_details("pull available tasks now", session_id="openclaw:pull-details")

    assert handled is True
    assert details["command_kind"] == "task_list"
    assert details["response_class_hint"] == "task_list"


def test_ignore_hive_prompt_control_returns_generic_hint() -> None:
    tracker = _tracker({"stats": {"active_agents": 0}, "topics": [], "recent_posts": []})

    handled, details = tracker.maybe_handle_command_details("ignore hive for now", session_id="openclaw:ignore-hive")

    assert handled is True
    assert details["command_kind"] == "prompt_control"
    assert details["response_class_hint"] == "generic_conversation"


def test_workspace_path_with_online_and_workspace_substrings_does_not_trigger_hive_overview() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command(
        "Create a file named vool_test_01.txt in "
        "/Users/test/vool-online-acceptance-proof/workspace/main "
        "with exactly this content: ALPHA-LOCAL-FILE-01",
        session_id="openclaw:no-hive-false-positive",
    )

    assert handled is False
    assert response == ""


def test_prune_stale_hive_interaction_state_clears_old_selection_context() -> None:
    session_id = "openclaw:stale-hive-selection"
    update_session_hive_state(
        session_id,
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={
            "shown_topic_ids": ["topic-1"],
            "shown_titles": ["OpenClaw integration audit"],
        },
    )

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE session_hive_watch_state SET updated_at = '2026-03-10T10:00:00+00:00' WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    finally:
        conn.close()

    state = prune_stale_hive_interaction_state(session_id)
    assert state["interaction_mode"] == ""
    assert state["interaction_payload"] == {}
    assert state["pending_topic_ids"] == []


def test_pull_the_tasks_command_returns_topic_list_without_extra_context() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command("pull the tasks", session_id="openclaw:plain-pull")
    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_contextual_pull_the_tasks_uses_pending_hive_prompt_state() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:contextual-pull",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )
    assert "want me to list them" in footer.lower()

    handled, response = tracker.maybe_handle_command("ok. pull the tasks", session_id="openclaw:contextual-pull")

    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_contextual_lets_pull_online_tasks_uses_pending_hive_prompt_state() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:contextual-online-pull",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )
    assert "want me to list them" in footer.lower()

    handled, response = tracker.maybe_handle_command(
        "ok lets pull online tasks and lets see what we can work with",
        session_id="openclaw:contextual-online-pull",
    )

    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_pull_the_hive_task_and_lets_do_one_returns_real_task_list() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command(
        "pull the hive task and lets do one?",
        session_id="openclaw:natural-hive-pull",
    )

    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()
    state = session_hive_state("openclaw:natural-hive-pull")
    assert state["interaction_mode"] == "hive_task_selection_pending"
    assert state["interaction_payload"]["shown_topic_ids"] == ["topic-1"]


def test_pull_available_tasks_command_collapses_duplicate_titles() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "researching",
                },
                {
                    "topic_id": "topic-2",
                    "created_by_agent_id": "peer-remote-2",
                    "title": "OpenClaw integration audit",
                    "status": "researching",
                },
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command("pull available tasks now", session_id="openclaw:dedupe")
    assert handled is True
    assert response.lower().count("openclaw integration audit") == 1
    assert "share the same title" in response.lower()


def test_check_hive_mind_tasks_phrase_returns_topic_list() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command("check the hive mind tasks", session_id="openclaw:natural")
    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_what_is_available_in_hive_phrase_returns_topic_list() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command("what is available in Hive to help with?", session_id="openclaw:available")
    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_what_are_the_tasks_available_for_hive_mind_returns_topic_list() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command(
        "what are the tasks available for Hive mind?",
        session_id="openclaw:available-what-are",
    )
    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_build_chat_footer_reports_local_research_updates_once() -> None:
    tracker = _tracker({"stats": {"active_agents": 1}, "topics": [], "recent_posts": []})
    topic_id = queue_curiosity_topic(
        session_id="openclaw:local-research",
        task_id="task-local-1",
        trace_id="task-local-1",
        topic="Liquefy OpenClaw continuity audit",
        topic_kind="technical",
        reason="user_request",
        priority=0.72,
        source_profiles=[],
    )
    update_curiosity_topic(topic_id, status="completed", candidate_id="cand-local-1")
    record_curiosity_run(
        topic_id=topic_id,
        task_id="task-local-1",
        trace_id="task-local-1",
        query_text="Liquefy OpenClaw continuity audit",
        source_profile_ids=[],
        snippets=[],
        candidate_id="cand-local-1",
        outcome="candidate_recorded",
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:local-research",
        hive_followups_enabled=True,
        idle_research_assist=False,
    )
    assert "new local research thread" in footer.lower()
    assert "new local research result" in footer.lower()

    footer = tracker.build_chat_footer(
        session_id="openclaw:local-research",
        hive_followups_enabled=True,
        idle_research_assist=False,
    )
    assert "new local research thread" not in footer.lower()
    assert "new local research result" not in footer.lower()


def test_build_chat_footer_uses_cleaner_hive_prompt_copy() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 3},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:hive-footer-copy",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )
    assert "want me to list them" in footer.lower()
    assert "ignore hive for 1h" in footer.lower()
    assert "by default i help with research when idle" not in footer.lower()


def test_contextual_what_are_the_tasks_returns_topic_list_when_pending_topics_exist() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    tracker.build_chat_footer(
        session_id="openclaw:contextual-what-tasks",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )

    handled, response = tracker.maybe_handle_command(
        "ok what are the tasks ?",
        session_id="openclaw:contextual-what-tasks",
    )

    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_affirmative_hive_followup_lists_tasks_when_prompt_is_pending() -> None:
    tracker = _tracker(
        {
            "stats": {"active_agents": 4},
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    tracker.build_chat_footer(
        session_id="openclaw:contextual-affirmative",
        hive_followups_enabled=True,
        idle_research_assist=True,
    )

    handled, response = tracker.maybe_handle_command("yes", session_id="openclaw:contextual-affirmative")

    assert handled is True
    assert "available hive tasks" in response.lower()
    assert "point at the task name" in response.lower()


def test_hive_overview_request_reports_online_agents_and_real_tasks() -> None:
    fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=25)).isoformat()
    tracker = _tracker(
        {
            "generated_at": fresh_ts,
            "stats": {"active_agents": 2},
            "agents": [
                {
                    "agent_id": "peer-1",
                    "display_name": "VOOL",
                    "status": "online",
                    "online": True,
                },
                {
                    "agent_id": "peer-2",
                    "display_name": "Trading Scanner",
                    "status": "busy",
                    "online": True,
                },
            ],
            "topics": [
                {
                    "topic_id": "topic-1",
                    "created_by_agent_id": "peer-remote-1",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
            "recent_posts": [],
        }
    )

    handled, response = tracker.maybe_handle_command(
        "what do we have online? any tasks in hive mind?",
        session_id="openclaw:hive-overview",
    )

    assert handled is True
    assert "online now: 2 agent(s)" in response.lower()
    assert "available hive tasks" in response.lower()
    assert "openclaw integration audit" in response.lower()
    assert "watcher-derived" in response.lower()
    assert "fresh" in response.lower()


def test_pull_the_tasks_uses_local_only_label_when_watcher_is_unreachable_but_session_state_exists() -> None:
    tracker = HiveActivityTracker(
        HiveActivityTrackerConfig(
            enabled=True,
            watcher_api_url="http://watch.example.test/api/dashboard",
            timeout_seconds=2,
        ),
        fetch_json=lambda url, timeout_seconds, context: (_ for _ in ()).throw(RuntimeError("watcher offline")),
    )
    update_session_hive_state(
        "openclaw:local-fallback",
        watched_topic_ids=[],
        seen_post_ids=[],
        pending_topic_ids=["topic-1"],
        seen_curiosity_topic_ids=[],
        seen_curiosity_run_ids=[],
        seen_agent_ids=[],
        last_active_agents=0,
        interaction_mode="hive_task_selection_pending",
        interaction_payload={
            "shown_topic_ids": ["topic-1"],
            "shown_titles": ["OpenClaw integration audit"],
        },
    )

    handled, response = tracker.maybe_handle_command("pull the tasks", session_id="openclaw:local-fallback")

    assert handled is True
    assert "local-only" in response.lower()
    assert "live presence unavailable" in response.lower()
    assert "openclaw integration audit" in response.lower()


def test_hive_footer_labels_stale_watcher_presence() -> None:
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=9)).isoformat()
    tracker = _tracker(
        {
            "generated_at": stale_ts,
            "stats": {"active_agents": 1},
            "topics": [],
            "agents": [
                {
                    "agent_id": "peer-stale-1",
                    "display_name": "Watcher Scout",
                    "status": "online",
                    "online": True,
                }
            ],
            "recent_posts": [],
        }
    )

    footer = tracker.build_chat_footer(
        session_id="openclaw:stale-footer",
        hive_followups_enabled=True,
        idle_research_assist=False,
    )

    assert "watcher-derived" in footer.lower()
    assert "stale" in footer.lower()


def test_agent_does_not_append_hive_footer_to_generic_research_chat_response() -> None:
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.start()
    with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="I see 2 Hive research request(s) available."), mock.patch.object(
        agent.hive_activity_tracker, "maybe_handle_command", return_value=(False, "")
    ), mock.patch.object(agent, "_sync_public_presence", return_value=None), mock.patch.object(
        agent, "_collect_adaptive_research", return_value=AdaptiveResearchResult(enabled=False, reason="test")
    ), mock.patch.object(
        agent, "_collect_live_web_notes", return_value=[]
    ), mock.patch.object(
        agent, "_should_frontload_curiosity", return_value=False
    ), mock.patch.object(
        agent.memory_router,
        "resolve",
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-footer-generic-research",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="OpenClaw and Liquefy integration status is still being researched.",
            confidence=0.84,
            trust_score=0.84,
            validation_state="not_run",
        ),
    ), mock.patch.object(
        agent.media_pipeline,
        "analyze",
        return_value=MediaAnalysisResult(False, reason="no_external_media"),
    ), mock.patch(
        "core.agent_runtime.agent.ingest_media_evidence",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.orchestrate_parent_task",
        return_value=None,
    ), mock.patch(
        "core.agent_runtime.agent.request_relevant_holders",
        return_value=[],
    ), mock.patch(
        "core.agent_runtime.agent.dispatch_query_shard",
        return_value=None,
    ), mock.patch(
        "core.agent_runtime.agent.render_response",
        return_value="OpenClaw and Liquefy integration status is still being researched.",
    ):
        result = agent.run_once(
            "Research OpenClaw and Liquefy integration status",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert "Hive:\nI see 2 Hive research request(s) available." not in result["response"]


def test_help_chat_response_does_not_append_hive_footer() -> None:
    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.start()
    with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Research follow-up: 1 new local research result landed."), mock.patch.object(
        agent, "_sync_public_presence", return_value=None
    ), mock.patch.object(
        agent.memory_router,
        "resolve",
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-footer-help-chat",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can help with coding, research, and grounded runtime tasks.",
            confidence=0.84,
            trust_score=0.84,
            validation_state="not_run",
        ),
    ):
        result = agent.run_once(
            "help",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
    assert "Hive:\nResearch follow-up: 1 new local research result landed." not in result["response"]
