from __future__ import annotations

from unittest import mock

import pytest

from core.autonomous_topic_research import AutonomousResearchResult
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.memory_first_router import ModelExecutionDecision


def _assert_hive_task_started_fast_path(result: dict, *, title_fragment: str = "agent commons") -> None:
    lowered = result["response"].lower()
    assert result["response_class"] == "task_started"
    assert result["model_execution"]["used_model"] is False
    assert "started hive research on" in lowered or "started research on" in lowered
    assert title_fragment in lowered


def test_show_open_hive_tasks_returns_real_list_not_fake_planner_sludge(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
        True,
        {
            "command_kind": "task_list",
            "watcher_status": "ok",
            "response_text": (
                "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 2 total):\n"
                "- [open] OpenClaw integration audit (#7d33994f)\n"
                "- [researching] Hive footer cleanup (#ada43859)\n"
                "If you want, I can start one. Just point at the task name or short `#id`."
            ),
            "truth_source": "watcher",
            "truth_label": "watcher-derived",
            "truth_status": "ok",
            "presence_claim_state": "visible",
            "presence_source": "watcher",
            "presence_truth_label": "watcher-derived",
            "presence_freshness_label": "fresh",
            "presence_age_seconds": 18,
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                },
                {
                    "topic_id": "ada43859-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "Hive footer cleanup",
                    "status": "researching",
                },
            ],
            "online_agents": [],
        },
    )
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-list",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see two Hive tasks open right now: OpenClaw integration audit and Hive footer cleanup. Point me at one and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "show me the open hive tasks",
        session_id_override="openclaw:hive-list",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_list"
    assert result["model_execution"]["used_model"] is True
    assert result["response"].startswith("Hive truth: watcher-derived.")
    assert "two hive tasks open" in lowered
    assert "review problem" not in lowered
    assert "choose safe next step" not in lowered
    assert "validate result" not in lowered
    model_input = agent.memory_router.resolve.call_args.kwargs["interpretation"].reconstructed_text.lower()
    assert "grounding observations for this turn" in model_input
    assert "topics" in model_input
    assert "watcher-derived" in model_input
    assert "presence" in model_input
    assert "fresh" in model_input
    assert "available hive tasks right now" not in model_input


def test_hive_task_list_falls_back_to_real_titles_when_model_invents_generic_categories(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
        True,
        {
            "command_kind": "task_list",
            "watcher_status": "ok",
            "response_text": (
                "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 2 total):\n"
                "- [researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f)\n"
                "- [researching] quick vm proof task from codex doctor check (#ada43859)\n"
                "If you want, I can start one. Just point at the task name or short `#id`."
            ),
            "truth_source": "watcher",
            "truth_label": "watcher-derived",
            "truth_status": "ok",
            "presence_claim_state": "visible",
            "presence_source": "watcher",
            "presence_truth_label": "watcher-derived",
            "presence_freshness_label": "fresh",
            "presence_age_seconds": 18,
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "researching",
                },
                {
                    "topic_id": "ada43859-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "quick vm proof task from codex doctor check",
                    "status": "researching",
                },
            ],
            "online_agents": [],
        },
    )
    agent.hive_activity_tracker.build_chat_footer.return_value = ""
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-list-generic-drift",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Here are some available Hive tasks related to security and server management:\n"
                "Security Audits\nServer Maintenance\nLog Monitoring\nPatch Management\nBackup and Recovery."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "hi hi what hive tasks are available? '",
        session_id_override="openclaw:hive-list-truth-preserve",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_list"
    assert result["model_execution"]["used_model"] is True
    assert "agent commons: better human-visible watcher and task-flow ux" in lowered
    assert "quick vm proof task from codex doctor check" in lowered
    assert "security audits" not in lowered
    assert "server maintenance" not in lowered


def test_show_open_hive_tasks_is_not_misread_as_topic_create(make_agent):
    agent = make_agent()

    assert agent._extract_hive_topic_create_draft("show me the open hive tasks") is None


def test_misspelled_hive_mind_task_check_recovers_to_real_task_list(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    def maybe_handle_command_details(user_text: str, *, session_id: str):
        normalized = " ".join(str(user_text or "").strip().lower().split())
        if normalized == "check hive mind see if any taks is up":
            return False, {}
        if normalized == "show me the open hive tasks":
            return (
                True,
                {
                    "command_kind": "task_list",
                    "watcher_status": "ok",
                    "response_text": (
                        "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 1 total):\n"
                        "- [open] OpenClaw integration audit (#7d33994f)\n"
                        "If you want, I can start it. Just point at the task name or short `#id`."
                    ),
                    "truth_source": "watcher",
                    "truth_label": "watcher-derived",
                    "truth_status": "ok",
                    "presence_claim_state": "visible",
                    "presence_source": "watcher",
                    "presence_truth_label": "watcher-derived",
                    "presence_freshness_label": "fresh",
                    "presence_age_seconds": 18,
                    "topics": [
                        {
                            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                            "title": "OpenClaw integration audit",
                            "status": "open",
                        }
                    ],
                    "online_agents": [],
                },
            )
        raise AssertionError(f"unexpected hive command: {user_text!r}")

    agent.hive_activity_tracker.maybe_handle_command_details.side_effect = maybe_handle_command_details
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-typo-recovery",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see one Hive task open right now: OpenClaw integration audit. Point me at it and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "check hive mind see if any taks is up",
        session_id_override="openclaw:hive-typo-recovery",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_list"
    assert "openclaw integration audit" in result["response"].lower()
    call_texts = [call.args[0].lower() for call in agent.hive_activity_tracker.maybe_handle_command_details.call_args_list]
    assert call_texts == ["check hive mind see if any taks is up", "show me the open hive tasks"]


def test_check_hive_mind_pls_recovers_to_exact_hive_truth_when_runtime_is_disabled(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = HiveActivityTracker(
        config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None),
    )
    agent.hive_activity_tracker.build_chat_footer = mock.Mock(return_value="")  # type: ignore[method-assign]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        side_effect=AssertionError("watcher-disabled Hive truth should not go through model wording")
    )

    with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=False):
        result = agent.run_once(
            "check hive mind pls",
            session_id_override="openclaw:hive-check-pls",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "task_failed_user_safe"
    assert (
        result["response"]
        == "Hive watcher is not configured on this runtime, so I can't report real live Hive tasks. Hive truth: future/unsupported."
    )
    agent.memory_router.resolve.assert_not_called()


def test_hi_what_is_on_the_hive_mind_tasks_routes_to_real_hive_task_list(make_agent):
    agent = make_agent()
    dashboard = {
        "stats": {"active_agents": 3},
        "topics": [
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "created_by_agent_id": "peer-remote-1",
                "title": "OpenClaw integration audit",
                "status": "open",
            }
        ],
        "recent_posts": [],
        "agents": [],
    }
    agent.hive_activity_tracker = HiveActivityTracker(
        config=HiveActivityTrackerConfig(enabled=True, watcher_api_url="http://watcher.local"),
        fetch_json=mock.Mock(return_value=dashboard),
    )
    agent.hive_activity_tracker.build_chat_footer = mock.Mock(return_value="")  # type: ignore[method-assign]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-what-is-on",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see one Hive task open right now: OpenClaw integration audit. Point me at it and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "hi what is on the hive mind tasks ?",
        session_id_override="openclaw:hive-what-is-on",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_list"
    assert "openclaw integration audit" in result["response"].lower()
    assert result["model_execution"]["used_model"] is True


@pytest.mark.parametrize(
    "prompt",
    [
        "check hive",
        "what's in hive",
        "what online tasks we have",
    ],
)
def test_semantic_hive_phrasings_recover_without_magic_phrase(make_agent, prompt):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    def maybe_handle_command_details(user_text: str, *, session_id: str):
        normalized = " ".join(str(user_text or "").strip().lower().split())
        if normalized == "show me the open hive tasks":
            return (
                True,
                {
                    "command_kind": "task_list",
                    "watcher_status": "ok",
                    "response_text": (
                        "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 1 total):\n"
                        "- [open] OpenClaw integration audit (#7d33994f)\n"
                        "If you want, I can start it. Just point at the task name or short `#id`."
                    ),
                    "truth_source": "watcher",
                    "truth_label": "watcher-derived",
                    "truth_status": "ok",
                    "presence_claim_state": "visible",
                    "presence_source": "watcher",
                    "presence_truth_label": "watcher-derived",
                    "presence_freshness_label": "fresh",
                    "presence_age_seconds": 18,
                    "topics": [
                        {
                            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                            "title": "OpenClaw integration audit",
                            "status": "open",
                        }
                    ],
                    "online_agents": [],
                },
            )
        return False, {}

    agent.hive_activity_tracker.maybe_handle_command_details.side_effect = maybe_handle_command_details
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash=f"hive-semantic-{prompt}",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see one Hive task open right now: OpenClaw integration audit. Point me at it and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        prompt,
        session_id_override=f"openclaw:{prompt}",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_list"
    assert "openclaw integration audit" in result["response"].lower()
    call_texts = [call.args[0].lower() for call in agent.hive_activity_tracker.maybe_handle_command_details.call_args_list]
    assert call_texts == [prompt, "show me the open hive tasks"]


def test_whats_on_the_hive_can_we_do_some_tasks_recovers_to_real_task_list(make_agent):
    agent = make_agent()
    agent.hive_activity_tracker = mock.Mock()
    agent.hive_activity_tracker.build_chat_footer.return_value = ""

    def maybe_handle_command_details(user_text: str, *, session_id: str):
        normalized = " ".join(str(user_text or "").strip().lower().split())
        if normalized == "what's on the hive? can we do some tasks?":
            return False, {}
        if normalized == "show me the open hive tasks":
            return (
                True,
                {
                    "command_kind": "task_list",
                    "watcher_status": "ok",
                    "response_text": (
                        "Available Hive tasks right now (watcher-derived; presence fresh (18s old); 1 total):\n"
                        "- [researching] Hive footer cleanup (#ada43859)\n"
                        "If you want, I can start it. Just point at the task name or short `#id`."
                    ),
                    "truth_source": "watcher",
                    "truth_label": "watcher-derived",
                    "truth_status": "ok",
                    "presence_claim_state": "visible",
                    "presence_source": "watcher",
                    "presence_truth_label": "watcher-derived",
                    "presence_freshness_label": "fresh",
                    "presence_age_seconds": 18,
                    "topics": [
                        {
                            "topic_id": "ada43859-dd40-4a7e-b78a-f8e2d94fb702",
                            "title": "Hive footer cleanup",
                            "status": "researching",
                        }
                    ],
                    "online_agents": [],
                },
            )
        raise AssertionError(f"unexpected hive command: {user_text!r}")

    agent.hive_activity_tracker.maybe_handle_command_details.side_effect = maybe_handle_command_details
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-whats-on",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can see one Hive task open right now: Hive footer cleanup. Point me at it and I'll start it.",
            confidence=0.84,
            trust_score=0.84,
        )
    )

    result = agent.run_once(
        "what's on the hive? can we do some tasks?",
        session_id_override="openclaw:hive-whats-on",
        source_context={"surface": "openclaw", "platform": "openclaw"},
    )

    assert result["response_class"] == "task_list"
    assert "hive footer cleanup" in result["response"].lower()
    call_texts = [call.args[0] for call in agent.hive_activity_tracker.maybe_handle_command_details.call_args_list]
    assert call_texts == ["what's on the hive? can we do some tasks?", "show me the open hive tasks"]


def test_short_yes_followup_reuses_last_shown_task_and_starts(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-yes",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I claimed the Hive task and started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        }
    ]
    hive_state = {
        "pending_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
            "shown_titles": ["Agent Commons: better human-visible watcher and task-flow UX"],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-12345678",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "yes",
            session_id_override="openclaw:hive-short-followup",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    _assert_hive_task_started_fast_path(result)
    assert "packed" not in result["response"].lower()
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"


def test_start_short_id_uses_assistant_style_summary(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-short-id",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I claimed the Hive task and started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        }
    ]

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"pending_topic_ids": []}), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-12345678",
        ),
    ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "start #7d33994f",
            session_id_override="openclaw:hive-short-id",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    _assert_hive_task_started_fast_path(result)
    assert "packed 3 research queries" not in lowered
    assert "bounded queries run" not in lowered
    assert "candidate notes" not in lowered


def test_selecting_shown_task_by_exact_title_starts_that_hive_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-title-select",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "VOOL Trading Learning Desk",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            claim_id="claim-a951bf9d",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "Agent Commons: better human-visible watcher and task-flow UX",
            session_id_override="openclaw:hive-title-select",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    _assert_hive_task_started_fast_path(result)
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701"


def test_selecting_short_id_from_shown_tasks_starts_that_hive_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-short-select",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "VOOL Trading Learning Desk",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            claim_id="claim-a951bf9d",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "#a951bf9d",
            session_id_override="openclaw:hive-short-select",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    _assert_hive_task_started_fast_path(result)
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701"


def test_selecting_shown_task_with_status_line_and_full_research_phrase_starts_that_hive_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-status-line-select",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Started research on Agent Commons: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: Agent commons brainstorm: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-7d33994f",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "[researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f). -- full research on this pls!",
            session_id_override="openclaw:hive-status-line-select",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    _assert_hive_task_started_fast_path(result)
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"


def test_selecting_short_id_with_full_phrase_starts_that_hive_task(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-short-full-select",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Started research on Agent Commons: Agent commons brainstorm: better human-visible watcher and task-flow UX.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            "title": "Agent Commons: Agent commons brainstorm: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
            claim_id="claim-a951bf9d",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "#a951bf9d. lets do this in full!",
            session_id_override="openclaw:hive-short-full-select",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    _assert_hive_task_started_fast_path(result)
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701"


def test_selected_hive_task_with_deliver_to_hive_prompt_starts_real_research_instead_of_planner_text(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-deliver-followup",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Here's what I'd suggest:\n\n- review problem\n- choose safe next step\n- validate result",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        }
    ]
    hive_state = {
        "pending_topic_ids": [row["topic_id"] for row in queue_rows],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": [row["topic_id"] for row in queue_rows],
            "shown_titles": [row["title"] for row in queue_rows],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch(
        "core.agent_runtime.agent.research_topic_from_signal",
        return_value=AutonomousResearchResult(
            ok=True,
            status="completed",
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            claim_id="claim-7d33994f",
            result_status="researching",
        ),
    ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
        result = agent.run_once(
            "Agent Commons: better human-visible watcher and task-flow UX. do all step by step and deliver it to Hive.",
            session_id_override="openclaw:hive-deliver-followup",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_started"
    assert "started hive research on" in lowered or "started research on" in lowered
    assert "review problem" not in lowered
    assert "choose safe next step" not in lowered
    assert "validate result" not in lowered
    selected_signal = research_topic_from_signal.call_args.args[0]
    assert selected_signal["topic_id"] == "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"


def test_review_the_problem_clarifies_when_multiple_tasks_are_open(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-ambiguous",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I still have two matching Hive tasks open. Pick one by name or short `#id` and I'll start there.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    queue_rows = [
        {
            "topic_id": "topic-1-aaaaaaaa",
            "title": "OpenClaw integration audit",
            "status": "open",
            "research_priority": 0.9,
            "active_claim_count": 0,
            "claims": [],
        },
        {
            "topic_id": "topic-2-bbbbbbbb",
            "title": "Hive footer cleanup",
            "status": "researching",
            "research_priority": 0.8,
            "active_claim_count": 0,
            "claims": [],
        },
    ]
    hive_state = {
        "pending_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["topic-1-aaaaaaaa", "topic-2-bbbbbbbb"],
            "shown_titles": ["OpenClaw integration audit", "Hive footer cleanup"],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "write_enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
    ), mock.patch("core.agent_runtime.agent.research_topic_from_signal") as research_topic_from_signal:
        result = agent.run_once(
            "review the problem",
            session_id_override="openclaw:hive-ambiguous",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "task_selection_clarification"
    assert result["model_execution"]["used_model"] is True
    assert "pick one by name or short `#id`" in result["response"].lower()
    research_topic_from_signal.assert_not_called()


def test_hive_status_followup_reports_clean_status_text(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-status",
            provider_id="ollama:qwen",
            used_model=True,
            output_text=(
                "Agent Commons: better human-visible watcher and task-flow UX is still researching. "
                "There is 1 active claim, 1 result post, and 2 artifacts so far."
            ),
            confidence=0.84,
            trust_score=0.84,
        )
    )
    hive_state = {
        "watched_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"],
        "interaction_payload": {"active_topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702"},
    }
    packet = {
        "topic": {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Agent Commons: better human-visible watcher and task-flow UX",
            "status": "researching",
        },
        "truth_source": "public_bridge",
        "truth_label": "public-bridge-derived",
        "truth_transport": "direct",
        "truth_timestamp": "2026-03-13T09:10:00+00:00",
        "execution_state": {
            "execution_state": "claimed",
            "active_claim_count": 1,
            "artifact_count": 2,
        },
        "counts": {"post_count": 1, "active_claim_count": 1},
        "posts": [{"post_kind": "result", "body": "First bounded pass landed."}],
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=True
    ), mock.patch.object(
        agent.public_hive_bridge, "get_public_research_packet", return_value=packet
    ):
        result = agent.run_once(
            "what is the status",
            session_id_override="openclaw:hive-status",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert result["response_class"] == "task_status"
    assert result["model_execution"]["used_model"] is True
    assert result["response"].startswith("Hive truth: public-bridge-derived.")
    assert "is still researching" in lowered
    assert "1 active claim" in lowered
    assert "2 artifacts" in lowered


def test_hive_followup_disabled_path_is_labeled_future_unsupported(make_agent):
    agent = make_agent()
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="hive-disabled",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="I can't claim that live Hive task here.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    hive_state = {
        "pending_topic_ids": ["topic-1"],
        "interaction_mode": "hive_task_selection_pending",
        "interaction_payload": {
            "shown_topic_ids": ["topic-1"],
            "shown_titles": ["OpenClaw integration audit"],
        },
    }

    with mock.patch("core.agent_runtime.agent.session_hive_state", return_value=hive_state), mock.patch.object(
        agent.public_hive_bridge, "enabled", return_value=False
    ):
        result = agent.run_once(
            "yes",
            session_id_override="openclaw:hive-disabled",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "task_failed_user_safe"
    assert "future/unsupported" in result["response"].lower()
