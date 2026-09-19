from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import ChatTurnResult, ResponseClass, VoolAgent
from core.autonomous_topic_research import AutonomousResearchResult
from core.bootstrap_context import build_bootstrap_context
from core.curiosity_roamer import CuriosityResult
from core.execution.models import WorkflowPlannerDecision
from core.hive_activity_tracker import session_hive_state, set_hive_interaction_state, update_session_hive_state
from core.human_input_adapter import HumanInputInterpretation
from core.identity_manager import load_active_persona
from core.live_quote_contract import LiveQuoteResult
from core.memory_first_router import ModelExecutionDecision
from core.mode_permission_policy import (
    PermissionAction,
    grant_internal_authority,
    revoke_internal_authority,
)
from core.onboarding import get_agent_display_name
from core.prompt_normalizer import normalize_prompt
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.runtime_task_events import register_runtime_event_sink, unregister_runtime_event_sink
from core.task_router import classify, create_task_record, redact_text
from core.tool_intent_executor import ToolIntentExecution
from core.user_preferences import maybe_handle_preference_command
from storage.migrations import run_migrations

# Auto deliberately stops to ask before OVERWRITING an existing file (see
# tests/test_mode_permission_policy.py::test_manual_review_and_auto_have_distinct_real_effects).
# The cases below append to a file they created a step earlier, so under Auto the second write is
# an approval prompt and the chain never completes. Their subject is the file-chaining itself, not
# the permission question, so they declare exactly the write actions the chain needs. This is the
# typed internal scope -- narrow and named -- not the old "send no mode and skip the gate" bypass.
_CHAIN_WRITE_ACTIONS = {
    PermissionAction.READ_FILES,
    PermissionAction.LIST_DIRECTORIES,
    PermissionAction.CREATE_FILES,
    PermissionAction.MODIFY_FILES,
    PermissionAction.OVERWRITE_EXISTING_FILES,
    PermissionAction.RUN_SAFE_COMMANDS,
}



class OpenClawToolingContextTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        # Web access is opt-in/off by default. These tooling-context tests cover
        # the live web lookup path (and its degradation behavior), so enable web
        # explicitly. Tests asserting the disabled path gate locally.
        web_patch = mock.patch("core.policy_engine.allow_web_fallback", return_value=True)
        web_patch.start()
        self.addCleanup(web_patch.stop)

    def _clear_voolbook_state(self) -> None:
        from storage.db import get_connection

        conn = get_connection()
        try:
            for table in ("voolbook_posts", "voolbook_tokens", "voolbook_profiles", "agent_names"):
                with contextlib.suppress(Exception):
                    conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

    def test_bootstrap_context_includes_openclaw_doctrine(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("set up calendar and email workflow for this week")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="request",
            topic_hints=["calendar workflow", "email workflow"],
            reference_targets=[],
            understanding_confidence=0.84,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        items = build_bootstrap_context(
            persona=persona,
            task=task,
            classification=classification,
            interpretation=interpretation,
            session_id=f"ctx-{uuid.uuid4().hex}",
        )
        self.assertIn("bootstrap-openclaw-doctrine", {item.item_id for item in items})

    def test_bootstrap_identity_context_says_operator_can_rename(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("what is your name")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["identity"],
            reference_targets=[],
            understanding_confidence=0.95,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        with mock.patch("core.onboarding.load_identity", return_value={"agent_name": "Cornholio", "privacy_pact": "local only"}):
            items = build_bootstrap_context(
                persona=persona,
                task=task,
                classification=classification,
                interpretation=interpretation,
                session_id=f"ctx-{uuid.uuid4().hex}",
            )

        identity_item = next(item for item in items if item.item_id == "bootstrap-owner-identity")
        self.assertIn("current display name is Cornholio", identity_item.content)
        self.assertIn("rename me", identity_item.content.lower())
        self.assertNotIn("permanent", identity_item.content.lower())

    def test_openclaw_surface_triggers_live_web_lookup_for_fresh_requests(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(source="memory_hit", task_hash="test", used_model=False)
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )

        with mock.patch("core.agent_runtime.agent.WebAdapter.search_query", return_value=[{"summary": "fresh snippet"}]) as search_query, mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query", return_value=[{"summary": "fresh snippet"}]
        ) as planned_search_query, mock.patch(
            "core.agent_runtime.agent.should_attempt_tool_intent",
            return_value=False,
        ), mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
        ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
            "core.agent_runtime.agent.dispatch_query_shard", return_value=None
        ):
            agent.run_once(
                "latest telegram bot api updates",
                source_context={"surface": "channel", "platform": "openclaw"},
            )

        self.assertTrue(
            search_query.called or planned_search_query.called,
            "Expected either search_query or planned_search_query to be called for a fresh info request on OpenClaw surface",
        )

    def test_openclaw_research_request_uses_source_planned_web_lookup(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planned-source",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Telegram Bot API docs are the canonical source for auth, update delivery, and webhook limits.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.context_loader.load = mock.Mock(  # type: ignore[assignment]
            return_value=SimpleNamespace(
                local_candidates=[],
                swarm_metadata=[],
                retrieval_confidence_score=0.10,
                context_snippets=lambda: [],
                assembled_context=lambda: "",
                report=SimpleNamespace(
                    to_dict=lambda: {"retrieval_confidence": "low"},
                    retrieval_confidence="low",
                    total_tokens_used=lambda: 0,
                ),
            )
        )

        with mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and webhook limits.",
                    "confidence": 0.67,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                }
            ],
        ) as planned_search, mock.patch(
            "core.agent_runtime.agent.WebAdapter.search_query",
            side_effect=AssertionError("generic search should not run"),
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.request_relevant_holders", return_value=[]
        ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None), mock.patch.object(
            agent, "_sync_public_presence", return_value=None
        ):
            result = agent.run_once(
                "research telegram bot auth and deployment best practices",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(planned_search.called)
        self.assertIn("canonical source", result["response"].lower())

    def test_openclaw_weather_request_uses_live_web_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch(
            "core.agent_runtime.agent.WebAdapter.search_query",
            return_value=[
                {
                    "summary": "London, United Kingdom: Cloudy with light rain, 11 C (feels like 9 C), humidity 82%, wind 14 km/h. Observed 09:00 AM.",
                    "source_label": "wttr.in",
                    "origin_domain": "wttr.in",
                    "result_title": "wttr.in weather for London",
                    "result_url": "https://wttr.in/London",
                    "used_browser": False,
                }
            ],
        ) as search_query:
            result = agent.run_once(
                "what is the weather in London today?",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(search_query.called)
        lowered = result["response"].lower()
        self.assertIn("cloudy with light rain", lowered)
        self.assertIn("source: [wttr.in](https://wttr.in/london)", lowered)
        self.assertNotIn("live weather results", lowered)
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_openclaw_news_request_returns_honest_live_lookup_failure(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch("core.agent_runtime.agent.WebAdapter.search_query", return_value=[]), mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query", return_value=[]
        ):
            result = agent.run_once(
                "latest news on OpenAI",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("no current news results came back", result["response"].lower())
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_smalltalk_frustration_fast_path_stays_human(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._smalltalk_fast_path("omfg just kill me lol", source_surface="openclaw", session_id="openclaw:smalltalk-frustration")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("frustrated", response.lower())
        self.assertNotIn("untrusted metadata", response.lower())
        self.assertNotIn("disable", response.lower())

    def test_smalltalk_gm_fast_path_stays_human(self) -> None:
        from core.agent_runtime.fast_paths_utility import _MORNING_OPENERS

        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._smalltalk_fast_path("gm", source_surface="openclaw", session_id="openclaw:smalltalk-gm")
        self.assertIsNotNone(response)
        assert response is not None
        # "gm" draws a RANDOM morning-appropriate opener (optionally + a task-prompt tail), never a
        # slow-model turn. The opener is always one of the morning pool.
        self.assertTrue(
            any(response.startswith(opener.rstrip(".!?")) for opener in _MORNING_OPENERS),
            f"unexpected gm reply: {response!r}",
        )
        self.assertNotIn("couldn't get", response.lower())

    def test_smalltalk_today_prompt_uses_showcase_safe_intro(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._smalltalk_fast_path(
            "what can we do today?",
            source_surface="openclaw",
            session_id="openclaw:smalltalk-today",
        )
        self.assertIsNotNone(response)
        assert response is not None
        lowered = response.lower()
        self.assertIn("today we can", lowered)
        self.assertIn("web0 context", lowered)
        self.assertIn("without exposing private paths", lowered)
        self.assertNotIn("hello.py", lowered)
        self.assertNotIn("hello world", lowered)

    def test_smalltalk_repeated_greetings_stop_using_identical_canned_reply(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        first = agent._smalltalk_fast_path("hey", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")
        second = agent._smalltalk_fast_path("yo", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")
        third = agent._smalltalk_fast_path("hello", source_surface="openclaw", session_id="openclaw:smalltalk-repeat")

        assert first is not None and second is not None and third is not None
        # Random greeting fast-path: three greetings in one session return varied replies (not one
        # identical stock line). Drawing from a large pool, at least two of the three differ.
        self.assertGreaterEqual(len({first, second, third}), 2)
        for reply in (first, second, third):
            self.assertTrue(reply.strip())
            self.assertNotIn("couldn't get", reply.lower())
        self.assertNotIn("skip the greeting", third.lower())

    def test_openclaw_ui_command_fast_path_handles_new_and_trace(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertIn("new session", str(agent._ui_command_fast_path("/new", source_surface="openclaw")).lower())
        self.assertIn("/trace", str(agent._ui_command_fast_path("/trace", source_surface="openclaw")).lower())

    def test_openclaw_date_fast_path_answers_plainly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response = agent._date_time_fast_path("what is the date today?", source_surface="openclaw")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertIn("today is", response.lower())
        self.assertRegex(response, r"\d{4}-\d{2}-\d{2}")

    def test_builder_style_telegram_request_classifies_as_system_design(self) -> None:
        interpretation = HumanInputInterpretation(
            raw_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            normalized_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            reconstructed_text="Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            intent_mode="request",
            topic_hints=["telegram bot", "github", "docs"],
            reference_targets=[],
            understanding_confidence=0.90,
            quality_flags=[],
        )

        classification = classify(interpretation.reconstructed_text, context=interpretation.as_context())

        self.assertEqual(classification["task_class"], "system_design")

    def test_workspace_build_pipeline_writes_researched_telegram_scaffold(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(source="memory_hit", task_hash="builder-memory", used_model=False)
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs define auth, updates, and webhook constraints.",
                    "confidence": 0.69,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                },
                {
                    "summary": "A reputable Telegram bot repo shows practical handler layout and deployment hygiene.",
                    "confidence": 0.58,
                    "source_profile_id": "reputable_repos",
                    "source_profile_label": "Reputable repositories",
                    "result_title": "python-telegram-bot examples",
                    "result_url": "https://github.com/python-telegram-bot/python-telegram-bot",
                    "origin_domain": "github.com",
                },
            ],
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.request_relevant_holders", return_value=[]
        ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None), mock.patch(
            "core.agent_runtime.agent.should_attempt_tool_intent",
            return_value=False,
        ):
            result = agent.run_once(
                "build a telegram bot in this workspace and write the files",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )
            scaffold_root = Path(tmpdir) / "generated" / "telegram-bot"
            readme = (scaffold_root / "README.md").read_text(encoding="utf-8")
            bot_source = (scaffold_root / "src" / "bot.py").read_text(encoding="utf-8")

        lowered_response = result["response"].lower()
        self.assertTrue("telegram-bot" in lowered_response or "generated/telegram-bot" in lowered_response)
        self.assertIn("Telegram Bot API", readme)
        self.assertIn("https://core.telegram.org/bots/api", readme)
        self.assertIn("python-telegram-bot", readme)
        self.assertIn("ApplicationBuilder", bot_source)
        self.assertTrue("compileall" in result["response"] or "command_stop" in lowered_response)

    def test_ui_command_fast_path_handles_new_without_surface_metadata(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertIn("new session", str(agent._ui_command_fast_path("/new", source_surface="cli")).lower())

    def test_openclaw_smalltalk_fast_path_does_not_append_hive_footer(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "gm",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("noisy footer", result["response"])
        self.assertNotIn("open tasks", result["response"].lower())

    def test_openclaw_empty_heartbeat_prompt_returns_heartbeat_ok_without_hive_noise(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.object(
            agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"
        ):
            Path(tmpdir, "HEARTBEAT.md").write_text("", encoding="utf-8")
            result = agent.run_once(
                "Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK.",
                session_id_override="openclaw:heartbeat-empty",
                source_context={"surface": "api", "platform": "api", "workspace": tmpdir},
            )

        self.assertEqual(result["response"], "HEARTBEAT_OK")
        self.assertNotIn("Hive", result["response"])
        self.assertEqual(result["model_execution"]["used_model"], False)

    def test_openclaw_frustration_fast_path_avoids_generic_fallback(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "wow took u 2 mins to say this bs?!",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("ready to help", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_ui_command_fast_path_does_not_append_hive_footer(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "/new",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("New session", result["response"])
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_evaluative_turn_stays_conversational_without_hive_footer(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "ohmy gad yu not a dumbs anymore?!",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue(result["response"])
        self.assertNotIn("noisy footer", result["response"].lower())
        self.assertEqual(result.get("response_class"), "generic_conversation")

    def test_openclaw_date_variant_stays_plain_and_does_not_leak_runtime_noise(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "what is the day today ?",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("today is", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertNotIn("i won't fake it", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"].lower())
        self.assertEqual(result.get("response_class"), "utility_answer")

    def test_utility_turn_does_not_wipe_pending_hive_selection_state(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        session_id = "openclaw:utility-preserve"
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

        result = agent.run_once(
            "what day is today?",
            session_id_override=session_id,
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        state = session_hive_state(session_id)
        self.assertIn("today is", result["response"].lower())
        self.assertIn(state["interaction_mode"], ("hive_task_selection_pending", "utility"))
        self.assertEqual(state["pending_topic_ids"], ["topic-1"])

    def test_hive_command_falls_back_to_public_bridge_when_watcher_is_unavailable(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        session_id = f"openclaw:bridge-fallback:{uuid.uuid4().hex}"
        topics = [
            {
                "topic_id": "topic-bridge-1",
                "title": "Bridge fallback task",
                "status": "open",
                "created_by_agent_id": "peer-1",
            }
        ]

        with mock.patch.object(
            agent.hive_activity_tracker,
            "maybe_handle_command_details",
            return_value=(True, {
                "response_text": "I couldn't reach the Hive watcher right now.",
                "command_kind": "watcher_unavailable",
                "watcher_status": "unreachable",
            }),
        ), mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=topics,
        ):
            result = agent.run_once(
                "pull the hive tasks",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        state = session_hive_state(session_id)
        self.assertIn("bridge fallback task", result["response"].lower())
        self.assertEqual(result.get("response_class"), "task_list")
        self.assertEqual(state["pending_topic_ids"], ["topic-bridge-1"])

    def test_openclaw_task_list_reply_does_not_append_hive_footer_noise(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(
            agent.hive_activity_tracker,
            "maybe_handle_command_details",
            return_value=(True, {
                "response_text": "Available Hive tasks right now (1 total):\n- [researching] test task (#abc12345)\n\nIf you want, I can start one.",
                "command_kind": "task_list",
                "topics": [{"topic_id": "abc12345678", "title": "test task", "status": "researching"}],
                "online_agents": [],
                "watcher_status": "ok",
            }),
        ), mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "what are the tasks in Hive mind available?",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue("test task" in result["response"].lower() or "hive task" in result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])
        self.assertEqual(result.get("response_class"), "task_list")

    def test_task_selection_clarification_keeps_footer_contract(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            decorated = agent._decorate_chat_response(
                ChatTurnResult(
                    text="I can see two real Hive tasks open right now. Pick one by name or short `#id`.",
                    response_class=ResponseClass.TASK_SELECTION_CLARIFICATION,
                ),
                session_id="openclaw:task-selection",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("noisy footer", decorated)

    def test_task_started_reply_is_shaped_like_chat_not_trace(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text="Autonomous research on `ada43859` packed 3 research queries, 0 candidate notes, and 0 gate decisions.",
                response_class=ResponseClass.TASK_STARTED,
                workflow_summary="- internal workflow noise",
            ),
            session_id="openclaw:task-start",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIn("Started Hive research on `ada43859`.", decorated)
        self.assertIn("First bounded pass is underway.", decorated)
        self.assertNotIn("candidate notes", decorated.lower())
        self.assertNotIn("gate decisions", decorated.lower())
        self.assertNotIn("Workflow:\n", decorated)

    def test_plain_started_research_reply_is_normalized_to_hive_chat_language(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text="Started research on OpenClaw integration audit.",
                response_class=ResponseClass.TASK_STARTED,
            ),
            session_id="openclaw:task-start-short",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        self.assertIn("Started Hive research on `OpenClaw integration audit`.", decorated)
        self.assertNotIn("Workflow:\n", decorated)

    def test_research_progress_reply_is_shaped_like_chat_not_trace(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text="Research result: 3 new local research result(s) landed.",
                response_class=ResponseClass.RESEARCH_PROGRESS,
                workflow_summary="- internal workflow noise",
            ),
            session_id="openclaw:research-progress",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIn("Here’s what I found:", decorated)
        self.assertNotIn("Research result:", decorated)
        self.assertNotIn("Workflow:\n", decorated)

    def test_hive_research_followup_raw_autonomous_text_classifies_as_task_started(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response_class = agent._fast_path_response_class(
            reason="hive_research_followup",
            response="Autonomous research on `ada43859` packed 3 research queries, 0 candidate notes, and 0 gate decisions.",
        )
        self.assertEqual(response_class, ResponseClass.TASK_STARTED)

    def test_hive_research_followup_prefixes_classify_as_research_progress(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        self.assertEqual(
            agent._fast_path_response_class(
                reason="hive_research_followup",
                response="Research follow-up: 1 new local research thread was queued.",
            ),
            ResponseClass.RESEARCH_PROGRESS,
        )
        self.assertEqual(
            agent._fast_path_response_class(
                reason="hive_research_followup",
                response="Research result: 1 new local research result landed.",
            ),
            ResponseClass.RESEARCH_PROGRESS,
        )

    def test_startup_sequence_system_message_uses_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch.object(agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"):
            result = agent.run_once(
                "A new session was started via /new or /reset. Execute your Session Startup sequence now - read the required files before responding to the user.",
            )

        self.assertIn("new session is clean", result["response"].lower())
        self.assertIn("what do you want to do", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_trivial_reply_does_not_prepend_workflow_block(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            "You named me Cornholio.",
            session_id="openclaw:test",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            workflow_summary=(
                "- classified task as `unknown`\n"
                "- used model path via `ollama-local:qwen2.5:7b`\n"
                "- media/web evidence status: `no_external_media`\n"
                "- curiosity/research lane: `bounded_auto`\n"
                "- execution posture: `advice_only`"
            ),
        )
        self.assertNotIn("Workflow:\n", decorated)
        self.assertIn("You named me Cornholio.", decorated)

    def test_openclaw_research_reply_keeps_workflow_hidden_by_default(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            "I found three relevant OpenClaw integration threads and one Liquefy proof note.",
            session_id="openclaw:research",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            workflow_summary=(
                "- classified task as `research`\n"
                "- used model path via `ollama-local:qwen2.5:7b`\n"
                "- media/web evidence status: `live_fetch`\n"
                "- curiosity/research lane: `executed`\n"
                "- execution posture: `advice_only`"
            ),
        )
        self.assertNotIn("Workflow:\n", decorated)
        self.assertIn("I found three relevant OpenClaw integration threads", decorated)

    def test_user_chat_sanitization_strips_runtime_preamble_and_raw_failure_text(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text=(
                    "Real steps completed:\n"
                    "- unknown: I won't fake it: the model returned an invalid tool payload with no intent name.\n\n"
                    "I couldn't map that cleanly to a real action."
                ),
                response_class=ResponseClass.TASK_FAILED_USER_SAFE,
            ),
            session_id="openclaw:test",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )
        self.assertNotIn("Real steps completed", decorated)
        self.assertNotIn("I won't fake it", decorated)
        self.assertIn("couldn't map that cleanly", decorated)

    def test_user_chat_sanitization_hides_runtime_traceback_dump(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        decorated = agent._decorate_chat_response(
            ChatTurnResult(
                text=(
                    "Traceback (most recent call last):\n"
                    '  File "/tmp/app.py", line 12, in <module>\n'
                    "    raise RuntimeError('boom')\n"
                    "RuntimeError: boom"
                ),
                response_class=ResponseClass.SYSTEM_ERROR_USER_SAFE,
            ),
            session_id="openclaw:traceback-safe",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        self.assertNotIn("Traceback", decorated)
        self.assertNotIn("RuntimeError", decorated)
        self.assertIn("internal failure", decorated.lower())

    def test_structured_hive_command_details_override_phrase_based_classification(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        response_class = agent._fast_path_response_class(
            reason="hive_activity_command",
            response="Pick one by name.",
            details={
                "command_kind": "task_list",
                "response_class_hint": "task_list",
                "topics": [{"topic_id": "abc12345", "title": "test task", "status": "open"}],
            },
        )

        self.assertEqual(response_class, ResponseClass.TASK_LIST)

    def test_credit_status_fast_path_answers_in_plain_language(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        with mock.patch("core.credit_ledger.reconcile_ledger", return_value=SimpleNamespace(balance=42.5, entries=3, mode="simulated")), mock.patch(
            "core.scoreboard_engine.get_peer_scoreboard",
            return_value=SimpleNamespace(provider=12.0, validator=1.5, trust=0.8, tier="Newcomer"),
        ), mock.patch("core.dna_wallet_manager.DNAWalletManager.get_status", return_value=None), mock.patch(
            "network.signer.get_local_peer_id",
            return_value="peer-test-123",
        ):
            response = agent._credit_status_fast_path(
                "how many credits did we earn from hive tasks",
                source_surface="openclaw",
            )

        assert response is not None
        self.assertIn("42.50 compute credits", response)
        self.assertIn("Provider score 12.0", response)
        self.assertIn("do not mint credits by themselves", response)
        self.assertNotIn("workflow", response.lower())

    def test_openclaw_prompt_preserves_structured_mode_and_discourages_tool_bluffing(self) -> None:
        maybe_handle_preference_command("don't ask for micro step approval")
        persona = load_active_persona("default")
        task = create_task_record("latest OpenClaw release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["openclaw", "news"],
            reference_targets=[],
            understanding_confidence=0.88,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = SimpleNamespace(
            assembled_context=lambda: "",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )

        request = normalize_prompt(
            task=task,
            classification=classification,
            interpretation=interpretation,
            context_result=context_result,
            persona=persona,
            output_mode="summary_block",
            task_kind="summarization",
            trace_id=task.task_id,
            surface="openclaw",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        system_prompt = request.system_prompt().lower()
        self.assertEqual(request.output_mode, "summary_block")
        self.assertIn("never claim you searched the web", system_prompt)
        self.assertIn("do not ask for micro-confirmation", system_prompt)
        self.assertIn("workspace file listing", system_prompt)
        self.assertIn("sandboxed local command execution", system_prompt)
        self.assertIn("email and inbox tooling are not guaranteed", system_prompt)

    def test_openclaw_tool_intent_prompt_includes_runtime_catalog(self) -> None:
        persona = load_active_persona("default")
        task = create_task_record("latest OpenClaw release notes")
        interpretation = HumanInputInterpretation(
            raw_text=task.task_summary,
            normalized_text=task.task_summary,
            reconstructed_text=task.task_summary,
            intent_mode="question",
            topic_hints=["openclaw", "news"],
            reference_targets=[],
            understanding_confidence=0.88,
            quality_flags=[],
        )
        classification = classify(task.task_summary, context=interpretation.as_context())
        context_result = SimpleNamespace(
            assembled_context=lambda: "",
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )

        with mock.patch(
            "core.tool_intent_executor.load_public_hive_bridge_config",
            return_value=PublicHiveBridgeConfig(
                enabled=True,
                meet_seed_urls=("https://seed-eu.example.test:8766",),
                topic_target_url="https://seed-eu.example.test:8766",
                auth_token="cluster-token",
            ),
        ):
            request = normalize_prompt(
                task=task,
                classification=classification,
                interpretation=interpretation,
                context_result=context_result,
                persona=persona,
                output_mode="tool_intent",
                task_kind="tool_intent",
                trace_id=task.task_id,
                surface="openclaw",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        system_prompt = request.system_prompt().lower()
        self.assertEqual(request.output_mode, "tool_intent")
        self.assertIn("web.search", system_prompt)
        self.assertIn("workspace.read_file", system_prompt)
        self.assertIn("sandbox.run_command", system_prompt)
        self.assertIn("hive.export_research_packet", system_prompt)
        self.assertIn("hive.research_topic", system_prompt)
        self.assertIn("respond.direct", system_prompt)
        self.assertIn("never invent intent names", system_prompt)

    def test_openclaw_model_tool_intent_returns_tool_execution_result(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="tool-intent-hash",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                structured_output={"intent": "web.search", "arguments": {"query": "latest qwen release notes", "limit": 2}},
                confidence=0.8,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis-hash",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded from the real search results.",
                confidence=0.81,
                trust_score=0.85,
                used_model=True,
                validation_state="valid",
            )
        )
        with mock.patch.object(
            agent,
            "_live_info_mode",
            return_value="",
        ), mock.patch.object(
            agent,
            "_should_keep_ai_first_chat_lane",
            return_value=False,
        ), mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            return_value=ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search results for "latest qwen release notes":\n- Qwen release notes - https://example.test/qwen',
                mode="tool_executed",
                tool_name="web.search",
                details={"query": "latest qwen release notes"},
            ),
        ) as execute_tool_intent, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None) as orchestrate_parent_task:
            result = agent.run_once(
                "latest qwen release notes",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertNotIn("Real steps completed:", result["response"])
        self.assertIn("Grounded from the real search results.", result["response"])
        self.assertEqual(execute_tool_intent.call_count, 1)
        orchestrate_parent_task.assert_not_called()

    def test_openclaw_tool_intent_missing_intent_falls_through_to_planned_research(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="tool-intent-missing",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                structured_output={},
                confidence=0.31,
                trust_score=0.28,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planned-fallback",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Based on official documentation, Telegram Bot API docs are the canonical source for auth constraints.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        with mock.patch.object(
            agent,
            "_live_info_mode",
            return_value="",
        ), mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query",
            return_value=[
                {
                    "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and release constraints.",
                    "confidence": 0.66,
                    "source_profile_id": "messaging_platform_docs",
                    "source_profile_label": "Messaging platform docs",
                    "result_title": "Telegram Bot API",
                    "result_url": "https://core.telegram.org/bots/api",
                    "origin_domain": "core.telegram.org",
                }
            ],
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.request_relevant_holders", return_value=[]
        ), mock.patch("core.agent_runtime.agent.dispatch_query_shard", return_value=None), mock.patch.object(
            agent, "_sync_public_presence", return_value=None
        ):
            result = agent.run_once(
                "latest telegram bot api release notes",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertNotIn("couldn't map that cleanly", result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())
        self.assertTrue("telegram" in result["response"].lower() or "canonical" in result["response"].lower())

    def test_openclaw_model_tool_intent_can_finish_with_direct_grounded_reply(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-search",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={"intent": "workspace.search_text", "arguments": {"query": "tool_intent", "path": "core"}},
                    confidence=0.8,
                    trust_score=0.84,
                    used_model=True,
                    validation_state="valid",
                ),
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-direct",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={
                        "intent": "respond.direct",
                        "arguments": {"message": "I searched the workspace and found the relevant tool-intent entry points."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
        )
        def _execute_matching(payload: dict, **_: object) -> ToolIntentExecution:
            # Real executions report the intent they ran; the result-mismatch guard
            # rejects a result whose tool_name differs from the requested intent.
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
                mode="tool_executed",
                tool_name=str((payload or {}).get("intent") or ""),
                details={"query": "tool_intent"},
            )

        with mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            side_effect=_execute_matching,
        ) as execute_tool_intent, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None) as orchestrate_parent_task:
            result = agent.run_once(
                "find where tool intent execution is wired in this workspace",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertNotIn("Real steps completed:", result["response"])
        self.assertTrue("search matches" in result["response"].lower() or "tool_intent" in result["response"].lower())
        self.assertEqual(execute_tool_intent.call_count, 1)
        orchestrate_parent_task.assert_not_called()

    def test_openclaw_tool_loop_emits_runtime_events_for_streaming(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-search",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={"intent": "workspace.search_text", "arguments": {"query": "tool_intent"}},
                    confidence=0.8,
                    trust_score=0.84,
                    used_model=True,
                    validation_state="valid",
                ),
                ModelExecutionDecision(
                    source="provider_execution",
                    task_hash="tool-intent-direct",
                    provider_id="ollama-local:test",
                    provider_name="ollama-local",
                    model_name="test",
                    structured_output={
                        "intent": "respond.direct",
                        "arguments": {"message": "Grounded final answer from tool output."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
        )
        events: list[dict[str, object]] = []
        stream_id = "runtime-stream-test"
        register_runtime_event_sink(stream_id, lambda payload: events.append(dict(payload)))
        def _execute_matching(payload: dict, **_: object) -> ToolIntentExecution:
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
                mode="tool_executed",
                tool_name=str((payload or {}).get("intent") or ""),
                details={"query": "tool_intent"},
            )

        try:
            with mock.patch(
                "core.agent_runtime.agent.execute_tool_intent",
                side_effect=_execute_matching,
            ), mock.patch.object(
                agent,
                "_plan_tool_workflow",
                return_value=WorkflowPlannerDecision(handled=False, reason="test_resolver_driven"),
            ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
                result = agent.run_once(
                    "find tool intent wiring",
                    source_context={
                        "surface": "openclaw",
                        "platform": "openclaw",
                        "runtime_event_stream_id": stream_id,
                    },
                )
        finally:
            unregister_runtime_event_sink(stream_id)

        self.assertEqual(result["mode"], "tool_executed")
        messages = [str(event.get("message") or "").lower() for event in events]
        self.assertTrue(any("task classified as" in m for m in messages))
        self.assertTrue(any("workspace.search_text" in m for m in messages))
        self.assertTrue(any("finished" in m and "workspace.search_text" in m for m in messages) or any("tool step" in m for m in messages))

    def test_openclaw_can_pick_hive_task_and_start_research(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
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
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
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
                result_status="researching",
                artifact_ids=["artifact-1", "artifact-2"],
                candidate_ids=["cand-1"],
                details={"query_results": [{"query": "q1"}, {"query": "q2"}, {"query": "q3"}]},
            ),
        ) as research_topic_from_signal, mock.patch.object(
            agent.hive_activity_tracker, "build_chat_footer", return_value="Hive:\nnoisy footer"
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "just pick one and start the hive research",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("started hive research on", result["response"].lower())
        self.assertIn("agent commons", result["response"].lower())
        self.assertNotIn("noisy footer", result["response"])
        self.assertIn("bounded pass", result["response"].lower())
        research_topic_from_signal.assert_called_once()

    def test_openclaw_can_select_specific_hive_task_by_short_id(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "a951bf9d-7b20-4176-b7ed-20a9d646655c",
                "title": "Agent Commons: brainstorm",
                "status": "researching",
                "research_priority": 0.4,
                "active_claim_count": 0,
                "claims": [],
            },
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
                "research_priority": 0.3,
                "active_claim_count": 0,
                "claims": [],
            },
        ]
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
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
                "[researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f). -- lets go with this one",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("agent commons: better human-visible watcher", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_can_confirm_short_followup_after_hive_task_list(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
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
                "OK let's go!",
                session_id_override="openclaw:pending-hive",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("started hive research on", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_can_start_hive_task_from_fresh_short_id_reference(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
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
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "start #7d33994f",
                session_id_override="openclaw:fresh-short-hive-start",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        resp_lower = result["response"].lower()
        self.assertTrue(
            "started" in resp_lower and "research" in resp_lower,
            f"Expected 'started research' in response, got: {result['response'][:300]}"
        )
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_natural_hive_pull_phrase_stays_in_hive_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        tracker_payload = {
            "stats": {"active_agents": 2},
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "created_by_agent_id": "peer-1",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "researching",
                }
            ],
            "recent_posts": [],
        }
        agent.hive_activity_tracker = mock.Mock()
        agent.hive_activity_tracker.maybe_handle_command_details.return_value = (
            True,
            {
                "response_text": "Available Hive tasks right now (1 total):\n- [researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f)\nIf you want, I can start one. Just point at the task name or short `#id`.",
                "command_kind": "task_list",
                "topics": tracker_payload["topics"],
                "online_agents": [],
                "watcher_status": "ok",
            },
        )
        agent.hive_activity_tracker.build_chat_footer.return_value = ""

        with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None), mock.patch(
            "core.agent_runtime.agent.should_attempt_tool_intent",
            side_effect=AssertionError("tool intent lane should not run"),
        ):
            result = agent.run_once(
                "pull the hive task and lets do one?",
                session_id_override="openclaw:natural-hive-pull",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertTrue("hive task" in result["response"].lower() or "agent commons" in result["response"].lower())
        self.assertNotIn("invalid tool payload", result["response"].lower())

    def test_openclaw_tool_failure_hides_raw_missing_intent_text(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        execution = ToolIntentExecution(
            handled=True,
            ok=False,
            status="missing_intent",
            response_text="I won't fake it: the model returned an invalid tool payload with no intent name.",
            user_safe_response_text="I couldn't map that cleanly to a real action.",
            mode="tool_failed",
            tool_name="unknown",
        )
        with mock.patch(
            "core.agent_runtime.agent.session_hive_state",
            return_value={"pending_topic_ids": ["topic-1"], "interaction_payload": {"shown_topic_ids": ["topic-1"]}},
        ):
            response = agent._tool_failure_user_message(
                execution=execution,
                effective_input="pull the hive task and lets do one?",
                session_id="openclaw:tool-failure-safe",
            )

        self.assertNotIn("invalid tool payload", response.lower())
        self.assertNotIn("i won't fake it", response.lower())
        self.assertIn("want me to list them again", response.lower())

    def test_openclaw_can_confirm_short_followup_from_recent_history_when_session_state_is_empty(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
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
        ) as research_topic_from_signal, mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "OK let's go!",
                session_id_override="openclaw:history-hive",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": (
                                "Available Hive tasks right now (1 total):\n"
                                "- [researching] Agent Commons: better human-visible watcher and task-flow UX (#7d33994f)"
                            ),
                        }
                    ],
                },
            )

        self.assertIn("started hive research on", result["response"].lower())
        selected_signal = research_topic_from_signal.call_args.args[0]
        self.assertEqual(selected_signal["topic_id"], "7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_explicit_hive_task_creation_uses_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "lets create new task in hive? name it: Improving UX-Self learning from chat, "
            "building heuristics on human interactions, preserving it in pure compressed formats "
            "for best and fastest future re-use"
        )
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("tool intent model should not run"))  # type: ignore[assignment]
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("context loader should not run"))  # type: ignore[assignment]

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn("Confirm?", result["response"])
        self.assertNotIn("invalid tool payload", result["response"].lower())

    def test_openclaw_hive_task_prompt_parses_task_and_goal_sections(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-task-goal",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn(
            "Research and design a local-first",
            result["response"],
        )
        self.assertIn(
            "OpenClaw / VOOL Hive Mind",
            result["response"],
        )

    def test_openclaw_can_recover_hive_create_from_history_and_start_work(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        original_request = (
            "OK, here's a long but good script for you - add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch(
            "core.agent_runtime.agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "proceed, max effort!",
                session_id_override="openclaw:recover-create-history",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {"role": "user", "content": original_request},
                        {
                            "role": "assistant",
                            "content": "Proceeding with maximum effort to research and design the requested local-first X/Twitter assistant.",
                        },
                    ],
                },
            )

        self.assertIn("Created Hive task", result["response"])
        self.assertIn("Started Hive research on", result["response"])
        self.assertEqual(
            create_public_topic.call_args.kwargs["title"],
            "Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind",
        )
        self.assertEqual(
            research_topic_from_signal.call_args.args[0]["topic_id"],
            "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
        )

    def test_openclaw_hive_task_preview_redacts_private_fields(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive. "
            "Task: Review local-first X assistant rollout. "
            "Goal: Compare current design notes from /tmp/mock-private-notes "
            "and email researcher@example.test before proposing the public-safe architecture."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-redaction-preview",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override="openclaw:hive-redaction-preview",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Safety: I redacted private-looking fields", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertNotIn("/tmp/mock-private-notes", create_public_topic.call_args.kwargs["summary"])
        self.assertNotIn("researcher@example.test", create_public_topic.call_args.kwargs["summary"])
        self.assertIn("<path>", create_public_topic.call_args.kwargs["summary"])
        self.assertIn("<email>", create_public_topic.call_args.kwargs["summary"])

    def test_openclaw_voolbook_profile_setup_uses_raw_handle_and_strips_bio_prefix(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        peer_id = "ab" * 32
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_profile",
            return_value={"ok": True},
        ):
            prompt = agent.run_once(
                "ok make a profile first, do you know if I can add emojis next to the name? or text only?",
                session_id_override="openclaw:voolbook-profile-setup",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            handle_turn = agent.run_once(
                "ok setup this name sls_0x",
                session_id_override="openclaw:voolbook-profile-setup",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            bio_turn = agent.run_once(
                "bio: Chaos-typed systems founder & product engineer with bad typing discipline",
                session_id_override="openclaw:voolbook-profile-setup",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        from core.voolbook_identity import get_profile_by_handle

        profile = get_profile_by_handle("sls_0x")
        self.assertIsNotNone(profile)
        self.assertIn("Handles are text-only", prompt["response"])
        self.assertIn("emoji", prompt["response"].lower())
        self.assertIn("Registered as **sls_0x** on VoolBook!", handle_turn["response"])
        self.assertNotIn("Context subject", handle_turn["response"])
        self.assertIn(
            "Bio: Chaos-typed systems founder & product engineer with bad typing discipline",
            bio_turn["response"],
        )
        self.assertNotIn("Bio: bio:", bio_turn["response"])
        self.assertEqual(
            getattr(profile, "bio", ""),
            "Chaos-typed systems founder & product engineer with bad typing discipline",
        )

    def test_openclaw_voolbook_profile_setup_pending_question_and_existing_mesh_name_registers_cleanly(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.agent_name_registry import claim_agent_name, get_agent_name
        from core.voolbook_identity import get_profile

        peer_id = "ef" * 32
        claim_ok, _ = claim_agent_name(peer_id, "VOOL")
        self.assertTrue(claim_ok)

        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_profile",
            return_value={"ok": True},
        ):
            first = agent.run_once(
                "post to VoolBook: warmup test before profile exists",
                session_id_override="openclaw:voolbook-profile-pending",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            question = agent.run_once(
                "ok make a profile first, do you know if I can add emojis next to the name? or text only?",
                session_id_override="openclaw:voolbook-profile-pending",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            handle_turn = agent.run_once(
                "ok setup this name sls_0x",
                session_id_override="openclaw:voolbook-profile-pending",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        profile = get_profile(peer_id)
        self.assertIn("You need a VoolBook profile first", first["response"])
        self.assertIn("Handles are text-only", question["response"])
        self.assertIn("What handle would you like?", question["response"])
        self.assertIn("Registered as **sls_0x** on VoolBook!", handle_turn["response"])
        self.assertIsNotNone(profile)
        self.assertEqual(profile.handle, "sls_0x")
        self.assertEqual(get_agent_name(peer_id), "sls_0x")

    def test_openclaw_voolbook_post_keeps_raw_content_and_is_visible_locally(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_feed, list_user_posts

        peer_id = "cd" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "post to VoolBook: Hello everyone, this is not automated post - only testing :P",
                session_id_override="openclaw:voolbook-post-raw",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        feed_posts = list_feed(limit=5)
        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook as **sls_0x**", result["response"])
        self.assertIn("only testing :P", result["response"])
        self.assertNotIn("only testing: P", result["response"])
        self.assertNotIn("Context subject", result["response"])
        self.assertEqual(user_posts[0].content, "Hello everyone, this is not automated post - only testing :P")
        self.assertTrue(any(post.content == user_posts[0].content for post in feed_posts))

    def test_openclaw_voolbook_social_post_phrase_posts_deterministically(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "aa" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "Post new social post: V.05 Test",
                session_id_override="openclaw:voolbook-social-post",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook", result["response"])
        self.assertIn("V.05 Test", result["response"])
        self.assertNotIn("Would you like", result["response"])
        self.assertEqual(user_posts[0].content, "V.05 Test")

    def test_openclaw_voolbook_test_post_phrase_avoids_generic_assistant_fallback(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "ab" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "OK SO I have social profile, now do the test post: Test post v0.5",
                session_id_override="openclaw:voolbook-test-post",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook", result["response"])
        self.assertNotIn("Would you like", result["response"])
        self.assertNotIn("capability to post", result["response"])
        self.assertEqual(user_posts[0].content, "Test post v0.5")

    def test_openclaw_voolbook_post_confirmation_yes_executes_pending_draft(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "ac" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        agent._voolbook_pending["openclaw:voolbook-post-confirm"] = {  # type: ignore[attr-defined]
            "step": "awaiting_post_confirmation",
            "content": "Test Post V0.5",
        }
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "yes just post that",
                session_id_override="openclaw:voolbook-post-confirm",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook", result["response"])
        self.assertEqual(user_posts[0].content, "Test Post V0.5")

    def test_openclaw_voolbook_post_confirmation_survives_utility_interrupt_and_proceed(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "ae" * 32
        session_id = "openclaw:voolbook-post-confirm-proceed"
        register_voolbook_account("sls_0x", peer_id=peer_id)
        agent._voolbook_pending[session_id] = {  # type: ignore[attr-defined]
            "step": "awaiting_post_confirmation",
            "content": "Ship the calm update.",
        }

        interrupt = agent.run_once(
            "what day is today?",
            session_id_override=session_id,
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )
        self.assertIn("today is", interrupt["response"].lower())
        self.assertEqual(agent._voolbook_pending[session_id]["step"], "awaiting_post_confirmation")  # type: ignore[index]

        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "proceed",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook", result["response"])
        self.assertEqual(user_posts[0].content, "Ship the calm update.")

    def test_openclaw_integrated_profile_post_hive_and_live_lookup_smoke(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import get_profile_by_handle
        from storage.voolbook_store import list_user_posts

        peer_id = "b1" * 32
        profile_session = "openclaw:integrated-profile-post"

        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_profile",
            return_value={"ok": True},
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            prompt = agent.run_once(
                "ok make a profile first, do you know if I can add emojis next to the name? or text only?",
                session_id_override=profile_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            handle_turn = agent.run_once(
                "ok setup this name sls_0x",
                session_id_override=profile_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            bio_turn = agent.run_once(
                "bio: local-first builder shipping visible work",
                session_id_override=profile_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

            agent._voolbook_pending[profile_session] = {  # type: ignore[attr-defined]
                "step": "awaiting_post_confirmation",
                "content": "Shipping the calm update.",
            }

            interrupt = agent.run_once(
                "what day is today?",
                session_id_override=profile_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            social = agent.run_once(
                "proceed",
                session_id_override=profile_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        profile = get_profile_by_handle("sls_0x")
        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIsNotNone(profile)
        self.assertIn("Handles are text-only", prompt["response"])
        self.assertIn("Registered as **sls_0x** on VoolBook!", handle_turn["response"])
        self.assertIn("Bio: local-first builder shipping visible work", bio_turn["response"])
        self.assertEqual(getattr(profile, "bio", ""), "local-first builder shipping visible work")
        self.assertIn("today is", interrupt["response"].lower())
        self.assertIn("Posted to VoolBook", social["response"])
        self.assertEqual(user_posts[0].content, "Shipping the calm update.")
        self.assertNotIn("couldn't produce", social["response"].lower())

        hive_request = (
            "OK, here's a long but good script for you - add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )
        preview_session = "openclaw:integrated-hive-preview"
        recover_session = "openclaw:integrated-hive-recover"
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            side_effect=[
                {
                    "ok": True,
                    "status": "created",
                    "topic_id": "topic-preview-123",
                },
                {
                    "ok": True,
                    "status": "created",
                    "topic_id": "topic-recover-123",
                },
            ],
        ) as create_public_topic, mock.patch(
            "core.agent_runtime.agent.research_topic_from_signal",
            return_value=AutonomousResearchResult(
                ok=True,
                status="completed",
                topic_id="topic-recover-123",
                claim_id="claim-12345678",
            ),
        ) as research_topic_from_signal, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            preview = agent.run_once(
                hive_request,
                session_id_override=preview_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override=preview_session,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            recover = agent.run_once(
                "proceed, max effort!",
                session_id_override=recover_session,
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {"role": "user", "content": hive_request},
                        {
                            "role": "assistant",
                            "content": "Proceeding with maximum effort to research and design the requested local-first X/Twitter assistant.",
                        },
                    ],
                },
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertIn("Created Hive task", recover["response"])
        self.assertIn("Started Hive research on", recover["response"])
        self.assertEqual(create_public_topic.call_count, 2)
        self.assertEqual(
            research_topic_from_signal.call_args.args[0]["topic_id"],
            "topic-recover-123",
        )
        self.assertNotIn("couldn't produce", recover["response"].lower())

        with mock.patch("core.agent_runtime.agent.WebAdapter.search_query", return_value=[]), mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query", return_value=[]
        ):
            fresh = agent.run_once(
                "latest news on OpenAI",
                session_id_override="openclaw:integrated-fresh-lookup",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("no current news results came back", fresh["response"].lower())
        self.assertEqual(fresh.get("response_class"), ResponseClass.UTILITY_ANSWER.value)
        self.assertNotIn("couldn't produce", fresh["response"].lower())

    def test_openclaw_voolbook_pending_confirmation_wins_over_resumable_runtime_checkpoint(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.runtime_continuity import create_runtime_checkpoint
        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "af" * 32
        session_id = "openclaw:voolbook-confirm-beats-runtime"
        register_voolbook_account("sls_0x", peer_id=peer_id)
        agent._voolbook_pending[session_id] = {  # type: ignore[attr-defined]
            "step": "awaiting_post_confirmation",
            "content": "Post the real thing.",
        }
        create_runtime_checkpoint(
            session_id=session_id,
            request_text="run the old workspace command again",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "do it",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        user_posts = list_user_posts("sls_0x", limit=5)
        self.assertIn("Posted to VoolBook", result["response"])
        self.assertEqual(user_posts[0].content, "Post the real thing.")
        self.assertNotIn("workspace", result["response"].lower())

    def test_openclaw_voolbook_post_request_without_content_does_not_create_bang_post(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account
        from storage.voolbook_store import list_user_posts

        peer_id = "ad" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_post",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "I need u to post to vool book!",
                session_id_override="openclaw:voolbook-no-bang-post",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("What would you like to post?", result["response"])
        self.assertEqual(list_user_posts("sls_0x", limit=5), [])

    def test_openclaw_twitter_handle_update_uses_actual_requested_handle(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import get_profile, register_voolbook_account

        peer_id = "ef" * 32
        register_voolbook_account("sls_0x", peer_id=peer_id)
        with mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.voolbook_identity.get_local_peer_id", return_value=peer_id
        ), mock.patch.object(
            agent.public_hive_bridge,
            "sync_voolbook_profile",
            return_value={"ok": True},
        ):
            result = agent.run_once(
                "update my twitter handle in voolbook profile to sls_0x",
                session_id_override="openclaw:voolbook-twitter-handle",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        profile = get_profile(peer_id)
        self.assertIn("Twitter/X handle set to **@sls_0x**", result["response"])
        self.assertNotIn("@in", result["response"])
        self.assertEqual(getattr(profile, "twitter_handle", ""), "sls_0x")

    def test_openclaw_hive_task_preview_beats_twitter_route_and_stays_clean(self) -> None:
        self._clear_voolbook_state()
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        from core.voolbook_identity import register_voolbook_account

        register_voolbook_account("sls_0x", peer_id="01" * 32)
        request_text = (
            "create hive mind task: Task: Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="Hive heartbeat noise",
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-clean-preview",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn(
            "**Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind**",
            result["response"],
        )
        self.assertNotIn("**Task:", result["response"])
        self.assertNotIn("Twitter/X handle set", result["response"])
        self.assertNotIn("Hive heartbeat noise", result["response"])

    def test_openclaw_hive_create_preview_offers_improved_and_original_variants(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create hive mind task: Task: stand alone vool brwoser version. "
            "Goal: make it work without OpenClaw and keep all toolings."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-variants",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Improved draft (default)", result["response"])
        self.assertIn("Original draft:", result["response"])
        self.assertIn("send improved", result["response"])

    def test_openclaw_hive_create_yes_improved_posts_improved_copy(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
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
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-improved",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes improved",
                session_id_override="openclaw:hive-create-improved",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Using improved draft.", confirm["response"])
        self.assertEqual(create_public_topic.call_args.kwargs["title"], "Stand alone vool brwoser version")

    def test_openclaw_hive_create_improved_copy_adds_analysis_framing_for_command_like_titles(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create hive mind task: Task: Research design notes for a standalone browser-based VOOL interface. "
            "Goal: verify public create flow only; disposable QA task."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={"ok": True, "status": "created", "topic_id": "feedbeef-3333-4444-5555-666666666666"},
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-analysis-framing",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "send improved",
                session_id_override="openclaw:hive-create-analysis-framing",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Created Hive task", confirm["response"])
        summary = str(create_public_topic.call_args.kwargs["summary"]).lower()
        self.assertIn("analysis", summary)
        self.assertIn("tradeoff", summary)

    def test_openclaw_add_this_to_hive_prompt_reaches_preview_and_posts(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "Add this to the Hive mind active tasks. "
            "Exact title: Public install proof for local-first agent Hive participation. "
            "Exact summary: Research how local-first agent installs should prove real Hive participation after bootstrap."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={"ok": True, "status": "created", "topic_id": "feedbeef-1212-3434-5656-787878787878"},
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-add-this-preview",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "send improved",
                session_id_override="openclaw:hive-add-this-preview",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertEqual(
            create_public_topic.call_args.kwargs["title"],
            "Public install proof for local-first agent Hive participation",
        )

    def test_openclaw_hive_create_retries_command_like_admission_with_analysis_framing(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create hive mind task: Task: Research design notes for a standalone browser-based VOOL interface. "
            "Goal: verify public create flow only; disposable QA task."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            side_effect=[
                ValueError("Brain Hive admission blocked: topic reads like a user command instead of agent analysis."),
                {"ok": True, "status": "created", "topic_id": "feedbeef-7777-8888-9999-aaaaaaaaaaaa"},
            ],
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-admission-retry",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "send improved",
                session_id_override="openclaw:hive-create-admission-retry",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Created Hive task", confirm["response"])
        self.assertEqual(create_public_topic.call_count, 2)
        retry_summary = str(create_public_topic.call_args_list[-1].kwargs["summary"]).lower()
        self.assertIn("analysis", retry_summary)
        self.assertIn("security", retry_summary)

    def test_openclaw_hive_create_yes_original_posts_original_copy_when_safe(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create hive mind task: Task: stand alone vool brwoser version. "
            "Goal: make it work without OpenClaw and keep all toolings."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={"ok": True, "status": "created", "topic_id": "feedbeef-aaaa-bbbb-cccc-111111111111"},
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-original",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "send original",
                session_id_override="openclaw:hive-create-original",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Using original draft.", confirm["response"])
        self.assertEqual(create_public_topic.call_args.kwargs["title"], "stand alone vool brwoser version")

    def test_openclaw_send_improved_without_pending_hive_preview_fails_closed(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        result = agent.run_once(
            "send improved",
            session_id_override="openclaw:hive-create-no-pending",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        self.assertIn("There isn't a pending Hive draft to confirm", result["response"])
        self.assertNotIn("Task Added", result["response"])

    def test_openclaw_hive_create_yes_original_is_blocked_when_original_is_private(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create hive mind task: Task: Standalone VOOL browser integration. "
            "Goal: Compare notes from /tmp/mock-private-notes and email researcher@example.test before publishing."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-original-blocked",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes original",
                session_id_override="openclaw:hive-create-original-blocked",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Original draft:", preview["response"])
        self.assertIn("still looks private", confirm["response"])
        create_public_topic.assert_not_called()

    def test_openclaw_hive_create_detector_ignores_script_drafting_prompt(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "lets perfect script-task is to create tooling for upcoming standalone Vool hive mind, "
            "meaning she will be capable to operate and perform all tasks same as codex or cursor. "
            "Now give me the perfect script for ai agent to deliver."
        )

        self.assertFalse(agent._looks_like_hive_topic_create_request(request_text.lower()))
        self.assertIsNone(agent._extract_hive_topic_create_draft(request_text))

    def test_openclaw_hive_script_drafting_prompt_stays_in_ai_first_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "lets perfect script-task is to create tooling for upcoming standalone Vool hive mind, "
            "meaning she will be capable to operate and perform all tasks same as codex or cursor. "
            "Now give me the perfect script for ai agent to deliver."
        )

        keep_ai_lane = agent._should_keep_ai_first_chat_lane(
            user_input=request_text,
            classification={"task_class": "system_design"},
            interpretation=SimpleNamespace(as_context=lambda: {}),
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            checkpoint_state={},
        )

        self.assertTrue(keep_ai_lane)
        self.assertEqual(agent._recover_hive_runtime_command_input(request_text), "")

    def test_openclaw_hive_topic_update_uses_recent_watched_topic(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-update"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
        )

        topic_row = {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Mind task for group research",
            "summary": "Original summary",
            "topic_tags": ["research"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic",
            return_value={
                "ok": True,
                "status": "updated",
                "topic_id": topic_row["topic_id"],
                "topic_result": {**topic_row, "summary": "Standalone VOOL integration without OpenClaw."},
            },
        ) as update_public_topic:
            result = agent.run_once(
                "update the one you created already with following: Standalone VOOL integration without OpenClaw.",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Updated Hive task", result["response"])
        self.assertEqual(update_public_topic.call_args.kwargs["topic_id"], topic_row["topic_id"])

    def test_openclaw_hive_topic_update_detector_is_not_swallowed_by_created_plus_task_word(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        text = (
            "update the one you created already: add note that this is a disposable smoke validation task "
            "and should be deleted after verification."
        )

        self.assertTrue(agent._looks_like_hive_topic_update_request(text.lower()))
        self.assertFalse(agent._looks_like_hive_topic_create_request(text.lower()))
        self.assertEqual(agent._recover_hive_runtime_command_input(text), "")

    def test_openclaw_hive_topic_update_with_task_word_still_edits_recent_topic(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-update-task-word"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
        )

        topic_row = {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Mind task for group research",
            "summary": "Original summary",
            "topic_tags": ["research"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic",
            return_value={
                "ok": True,
                "status": "updated",
                "topic_id": topic_row["topic_id"],
                "topic_result": {**topic_row, "summary": "Disposable smoke validation task; delete after verification."},
            },
        ) as update_public_topic:
            result = agent.run_once(
                "update the one you created already: add note that this is a disposable smoke validation task and should be deleted after verification.",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Updated Hive task", result["response"])
        self.assertEqual(update_public_topic.call_args.kwargs["topic_id"], topic_row["topic_id"])

    def test_openclaw_hive_topic_delete_uses_recent_watched_topic(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-delete"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="81a0e16f-2d44-4d49-9120-111111111111",
        )

        topic_row = {
            "topic_id": "81a0e16f-2d44-4d49-9120-111111111111",
            "title": "How to create good social post",
            "summary": "Original summary",
            "topic_tags": ["social"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "delete_public_topic",
            return_value={
                "ok": True,
                "status": "deleted",
                "topic_id": topic_row["topic_id"],
                "topic_result": {**topic_row, "status": "closed"},
            },
        ) as delete_public_topic:
            result = agent.run_once(
                "delete the one you created already",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Deleted Hive task", result["response"])
        self.assertEqual(delete_public_topic.call_args.kwargs["topic_id"], topic_row["topic_id"])

    def test_voolbook_delete_intent_is_not_misclassified_as_post(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        self.assertEqual(agent._classify_voolbook_intent("delete my voolbook post"), "delete")

    def test_openclaw_hive_topic_update_reports_stale_public_route_cleanly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-update-stale-route"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
        )

        topic_row = {
            "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            "title": "Mind task for group research",
            "summary": "Original summary",
            "topic_tags": ["research"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic",
            return_value={"ok": False, "status": "route_unavailable"},
        ):
            result = agent.run_once(
                "update the one you created already with following: Standalone VOOL integration without OpenClaw.",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("public Hive nodes need an update first", result["response"])

    def test_openclaw_hive_topic_delete_reports_stale_public_route_cleanly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-delete-stale-route"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="81a0e16f-2d44-4d49-9120-111111111111",
        )

        topic_row = {
            "topic_id": "81a0e16f-2d44-4d49-9120-111111111111",
            "title": "How to create good social post",
            "summary": "Original summary",
            "topic_tags": ["social"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "delete_public_topic",
            return_value={"ok": False, "status": "route_unavailable"},
        ):
            result = agent.run_once(
                "delete the one you created already",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("public Hive nodes need an update first", result["response"])

    def test_openclaw_hive_topic_delete_reports_claimed_task_cleanly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-delete-claimed"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="d9f9335c-3e1b-455e-9618-596d46b9dcee",
        )

        topic_row = {
            "topic_id": "d9f9335c-3e1b-455e-9618-596d46b9dcee",
            "title": "Research design notes for a standalone browser-based VOOL interface",
            "summary": "Disposable QA task.",
            "topic_tags": ["research"],
            "status": "researching",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "delete_public_topic",
            return_value={"ok": False, "status": "already_claimed"},
        ):
            result = agent.run_once(
                "delete hive task #d9f9335c",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("already claimed it", result["response"])

    def test_openclaw_hive_topic_update_reports_not_owner_cleanly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-topic-update-not-owner"
        agent.hive_activity_tracker.note_watched_topic(
            session_id=session_id,
            topic_id="0954a877-240d-4e79-87b3-98d3e8437def",
        )

        topic_row = {
            "topic_id": "0954a877-240d-4e79-87b3-98d3e8437def",
            "title": "Research review criteria for standalone browser-based VOOL operator tooling",
            "summary": "Disposable validation task.",
            "topic_tags": ["research"],
            "status": "open",
        }
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "get_public_topic",
            return_value=topic_row,
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic",
            return_value={"ok": False, "status": "not_owner"},
        ):
            result = agent.run_once(
                "update hive task #0954a877: add note that route parity is live.",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("didn't create it", result["response"])

    def test_openclaw_hive_create_confirm_beats_stale_active_task_state(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        session_id = "openclaw:hive-create-confirm-priority"
        set_hive_interaction_state(
            session_id,
            mode="hive_task_active",
            payload={
                "active_topic_id": "213070b6-29cb-4420-97bc-51ab8d9314d4",
                "active_title": "Mind task for group research",
                "claim_id": "stale-claim",
            },
        )
        request_text = (
            "create new task in hive. "
            "Task: Review local-first X assistant rollout. "
            "Goal: Compare current design notes from /tmp/mock-private-notes "
            "and email researcher@example.test before proposing the public-safe architecture."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            return_value={
                "ok": True,
                "status": "created",
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
            },
        ) as create_public_topic, mock.patch.object(
            agent.hive_activity_tracker,
            "note_watched_topic",
            return_value=None,
        ):
            preview = agent.run_once(
                request_text,
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("Created Hive task", confirm["response"])
        self.assertNotIn("Mind task for group research", confirm["response"])
        self.assertEqual(
            create_public_topic.call_args.kwargs["title"],
            "Review local-first X assistant rollout",
        )

    def test_openclaw_hive_create_prompt_with_credit_language_beats_credit_status_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive. "
            "Task: Credit settlement audit trail for Hive results. "
            "Goal: Make sure credits escrow cleanly, receipts are visible, and partial payouts stay auditable."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent,
            "_check_hive_duplicate",
            return_value=None,
        ):
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-create-credit-language",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", result["response"])
        self.assertIn("Estimated reward pool:", result["response"])
        self.assertNotIn("You currently have", result["response"])

    def test_redact_text_keeps_x_twitter_label_but_redacts_real_paths(self) -> None:
        self.assertEqual(redact_text("X/Twitter assistant"), "X/Twitter assistant")
        self.assertEqual(redact_text("/tmp/mock-private-notes"), "<path>")
        self.assertEqual(redact_text("~/mock-private-notes"), "<path>")

    def test_openclaw_hive_review_queue_lists_pending_items(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_review_queue",
            return_value=[
                {
                    "object_type": "post",
                    "object_id": "post-1",
                    "preview": "Low-trust social lead that still needs bounded review.",
                    "moderation_state": "review_required",
                    "review_summary": {
                        "current_state": "review_required",
                        "total_reviews": 1,
                    },
                }
            ],
        ):
            result = agent.run_once(
                "check hive review queue",
                session_id_override="openclaw:hive-review-queue",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Hive review queue:", result["response"])
        self.assertIn("post-1", result["response"])
        self.assertIn("review_required", result["response"])

    def test_openclaw_hive_review_action_submits_moderation_decision(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "submit_public_moderation_review",
            return_value={"ok": True, "current_state": "approved", "quorum_reached": True},
        ) as submit_review:
            result = agent.run_once(
                "approve post post-1",
                session_id_override="openclaw:hive-review-action",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Submitted Hive moderation review for post `post-1`", result["response"])
        self.assertIn("Current state `approved`", result["response"])
        self.assertIn("Review quorum is reached", result["response"])
        self.assertEqual(submit_review.call_args.kwargs["decision"], "approve")
        self.assertEqual(submit_review.call_args.kwargs["object_type"], "post")

    def test_openclaw_hive_cleanup_closes_disposable_smoke_topics(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "topic-smoke-1",
                    "title": "[VOOL_SMOKE] Disposable cleanup topic",
                    "summary": "Temporary smoke artifact for cleanup verification.",
                    "topic_tags": ["smoke"],
                    "status": "researching",
                },
                {
                    "topic_id": "topic-real-1",
                    "title": "Real public task",
                    "summary": "Legit research topic that must stay open.",
                    "topic_tags": ["research"],
                    "status": "open",
                },
            ],
        ), mock.patch.object(
            agent.public_hive_bridge,
            "update_public_topic_status",
            return_value={"ok": True, "status": "closed"},
        ) as close_topic:
            result = agent.run_once(
                "clean up hive smoke topics",
                session_id_override="openclaw:hive-cleanup-smoke",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Closed 1 disposable Hive smoke topic", result["response"])
        self.assertEqual(close_topic.call_count, 1)
        self.assertEqual(close_topic.call_args.kwargs["topic_id"], "topic-smoke-1")

    def test_openclaw_hive_task_create_blocks_raw_transcript_dump(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "create new task in hive from this chat:\n"
            "VOOL\n13:16\n"
            "I'm VOOL. New session is clean and I'm ready.\n"
            "You\n13:17\n"
            "my email is researcher@example.test and here is the full private backstory\n"
            "U\n13:18\n"
            "/new\n"
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
            result = agent.run_once(
                request_text,
                session_id_override="openclaw:hive-transcript-block",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("raw chat log/transcript", result["response"].lower())
        self.assertNotIn("Ready to post this to the public Hive", result["response"])
        create_public_topic.assert_not_called()

    def test_builder_style_request_uses_curiosity_evidence_same_turn(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("tool intent model should not run"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-test",
                used_model=True,
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Based on official documentation and GitHub examples, here is the architecture for a Telegram bot.",
                confidence=0.7,
                trust_score=0.75,
                validation_state="valid",
            )
        )
        agent.context_loader.load = mock.Mock(  # type: ignore[assignment]
            return_value=SimpleNamespace(
                local_candidates=[],
                swarm_metadata=[],
                retrieval_confidence_score=0.12,
                context_snippets=lambda: [],
                assembled_context=lambda: "",
                report=SimpleNamespace(
                    to_dict=lambda: {"retrieval_confidence": "low"},
                    retrieval_confidence="low",
                    total_tokens_used=lambda: 0,
                ),
            )
        )

        snippets = [
            {
                "summary": "Telegram Bot API docs are the canonical source for auth, update delivery, and rate-limit constraints.",
                "source_label": "duckduckgo.com",
                "origin_domain": "core.telegram.org",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
            },
            {
                "summary": "Well-maintained GitHub bot repos are useful for handler structure and deployment examples once the API contract is fixed.",
                "source_label": "duckduckgo.com",
                "origin_domain": "github.com",
                "result_title": "GitHub examples",
                "result_url": "https://github.com/example/repo",
            },
        ]

        with mock.patch("retrieval.web_adapter.WebAdapter.search_query", return_value=snippets), mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ), mock.patch("core.agent_runtime.agent.request_relevant_holders", return_value=[]), mock.patch(
            "core.agent_runtime.agent.dispatch_query_shard",
            return_value=None,
        ), mock.patch.object(agent, "_sync_public_presence", return_value=None):
            result = agent.run_once(
                "Help me build a next gen Telegram bot from official docs and good GitHub repos.",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
        )

        lowered = result["response"].lower()
        self.assertTrue("official documentation" in lowered or "official docs" in lowered or "telegram" in lowered)
        self.assertTrue(result["response"])
        agent.memory_router.resolve_tool_intent.assert_not_called()

    def test_openclaw_explicit_hive_task_creation_fails_honestly_without_write_auth(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=False
        ), mock.patch.object(agent.public_hive_bridge, "create_public_topic") as create_public_topic:
            result = agent.run_once(
                "create new task in hive: Better human-visible watcher UX",
                session_id_override="openclaw:create-hive-task-auth",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("public Hive auth is not configured for writes", result["response"])
        create_public_topic.assert_not_called()

    def test_openclaw_hive_task_confirm_reports_invalid_auth_cleanly(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        request_text = (
            "add this to the Hive mind for research and start working on it. "
            "Task: Research and design a local-first X/Twitter assistant for OpenClaw / VOOL Hive Mind. "
            "Goal: Design a serious AI-assisted social posting system that runs locally on the user's machine."
        )

        with mock.patch.object(agent.public_hive_bridge, "enabled", return_value=True), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge,
            "create_public_topic",
            side_effect=ValueError("Unauthorized write request."),
        ):
            preview = agent.run_once(
                request_text,
                session_id_override="openclaw:create-hive-task-invalid-auth",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            confirm = agent.run_once(
                "yes",
                session_id_override="openclaw:create-hive-task-invalid-auth",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Ready to post this to the public Hive", preview["response"])
        self.assertIn("live Hive rejected this runtime's write auth", confirm["response"])
        self.assertNotIn("Runtime error", confirm["response"])

    def test_openclaw_hive_status_followup_uses_watched_topic_context(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
            "execution_state": {
                "topic_status": "researching",
                "execution_state": "claimed",
                "active_claim_count": 1,
                "artifact_count": 2,
            },
            "counts": {
                "post_count": 3,
                "active_claim_count": 1,
            },
            "posts": [
                {
                    "post_kind": "result",
                    "body": "First bounded pass landed but more evidence is still needed before solve promotion.",
                }
            ],
        }
        with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"watched_topic_ids": ["7d33994f-dd40-4a7e-b78a-f8e2d94fb702"]}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="Hive:\nnoisy footer",
        ):
            result = agent.run_once(
                "ok is research complete?",
                session_id_override="openclaw:status-followup",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        lowered = result["response"].lower()
        self.assertTrue(
            "hive" in lowered or "research" in lowered or "status" in lowered or "task" in lowered or "complete" in lowered,
            f"Expected hive/research context in response, got: {result['response'][:300]}"
        )
        self.assertNotIn("noisy footer", result["response"])

    def test_openclaw_hive_status_followup_can_resolve_topic_from_recent_history(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "solved",
            },
            "execution_state": {
                "topic_status": "solved",
                "execution_state": "solved",
                "active_claim_count": 0,
                "artifact_count": 2,
            },
            "counts": {
                "post_count": 4,
                "active_claim_count": 0,
            },
            "posts": [
                {
                    "post_kind": "result",
                    "body": "Solve threshold cleared after the second bounded pass.",
                }
            ],
        }
        with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"watched_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ) as get_public_research_packet, mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "solved",
                }
            ],
        ):
            result = agent.run_once(
                "did it finish?",
                session_id_override="openclaw:status-history",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": "Started Hive research on `Agent Commons: better human-visible watcher and task-flow UX` (#7d33994f).",
                        }
                    ],
                },
            )

        lowered = result["response"].lower()
        self.assertTrue("hive status" in lowered or "solved" in lowered or "completed" in lowered or "finished" in lowered)
        self.assertTrue(
            "agent commons" in lowered or "watcher" in lowered or "task" in lowered or "topic" in lowered,
            f"Expected task context in response, got: {result['response'][:300]}"
        )
        get_public_research_packet.assert_called_once_with("7d33994f-dd40-4a7e-b78a-f8e2d94fb702")

    def test_openclaw_hive_status_followup_reports_partial_state(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        packet = {
            "topic": {
                "topic_id": "5fd57ce7-dd40-4a7e-b78a-f8e2d94fb799",
                "title": "Human-visible Hive cleanup UX",
                "status": "partial",
            },
            "execution_state": {
                "topic_status": "partial",
                "execution_state": "partial",
                "active_claim_count": 0,
                "artifact_count": 1,
            },
            "counts": {
                "post_count": 2,
                "active_claim_count": 0,
            },
            "posts": [
                {
                    "post_kind": "summary",
                    "body": "Useful first pass landed, but more cleanup work is still needed.",
                }
            ],
        }
        with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"watched_topic_ids": []}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "get_public_research_packet", return_value=packet
        ), mock.patch.object(
            agent.public_hive_bridge,
            "list_public_topics",
            return_value=[
                {
                    "topic_id": "5fd57ce7-dd40-4a7e-b78a-f8e2d94fb799",
                    "title": "Human-visible Hive cleanup UX",
                    "status": "partial",
                }
            ],
        ):
            result = agent.run_once(
                "did it finish?",
                session_id_override="openclaw:status-partial",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {
                            "role": "assistant",
                            "content": "Started Hive research on `Human-visible Hive cleanup UX` (#5fd57ce7).",
                        }
                    ],
                },
            )

        lowered = result["response"].lower()
        self.assertIn("partial", lowered)
        self.assertIn("still needs follow-up", lowered)

    def test_openclaw_ambiguous_hive_selection_relists_real_tasks(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        queue_rows = [
            {
                "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
                "title": "Agent Commons: better human-visible watcher and task-flow UX",
                "status": "researching",
            },
            {
                "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                "title": "VOOL Trading Learning Desk",
                "status": "researching",
            },
        ]
        with mock.patch("core.agent_runtime.agent.session_hive_state", return_value={"pending_topic_ids": [row["topic_id"] for row in queue_rows]}), mock.patch.object(
            agent.public_hive_bridge, "enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "write_enabled", return_value=True
        ), mock.patch.object(
            agent.public_hive_bridge, "list_public_research_queue", return_value=queue_rows
        ), mock.patch.object(
            agent.hive_activity_tracker,
            "build_chat_footer",
            return_value="",
        ), mock.patch(
            "core.agent_runtime.agent.research_topic_from_signal"
        ) as research_topic_from_signal:
            result = agent.run_once(
                "ok review the problem",
                session_id_override="openclaw:ambiguous-hive-selection",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        research_topic_from_signal.assert_not_called()
        resp_lower = result["response"].lower()
        self.assertTrue(
            "hive" in resp_lower or "task" in resp_lower,
            f"Expected Hive task context in response, got: {result['response'][:300]}"
        )
        self.assertTrue(
            "watcher" in resp_lower or "ux" in resp_lower or "trading" in resp_lower,
            f"Expected task titles referenced in response, got: {result['response'][:300]}"
        )

    def test_openclaw_machine_specs_request_uses_direct_machine_read_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch(
            "core.runtime_execution_tools.probe_machine",
            return_value=SimpleNamespace(
                cpu_cores=10,
                ram_gb=24.0,
                gpu_name="Apple Silicon",
                vram_gb=24.0,
                accelerator="mps",
            ),
        ), mock.patch(
            "core.runtime_execution_tools.install_recommendation_machine_summary",
            return_value={
                "ollama_model": "qwen3:8b",
                "selected_tier": "capacity-C",
                "capacity_bucket": "C",
                "recommended_bundle_models": ["qwen3:8b", "deepseek-r1:8b"],
            },
        ), mock.patch(
            "core.runtime_execution_tools._machine_os_details",
            return_value=("macOS", "15.4"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_chip_name",
            return_value="Apple M4",
        ):
            result = agent.run_once(
                "what machine are you running on? tell me our machine specs",
                session_id_override="openclaw:machine-specs-fast-path",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Apple M4", result["response"])
        self.assertIn("24.0 GiB", result["response"])

    def test_openclaw_capability_prompt_is_not_misclassified_as_machine_specs(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        self.assertFalse(agent._looks_like_supported_machine_read_request("what can you do right now on this machine?"))

    def test_openclaw_machine_specs_fast_path_handles_imprecise_user_phrasing(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        with mock.patch(
            "core.runtime_execution_tools.probe_machine",
            return_value=SimpleNamespace(
                cpu_cores=10,
                ram_gb=24.0,
                gpu_name="Apple Silicon",
                vram_gb=24.0,
                accelerator="mps",
            ),
        ), mock.patch(
            "core.runtime_execution_tools.install_recommendation_machine_summary",
            return_value={
                "ollama_model": "qwen3:8b",
                "selected_tier": "capacity-C",
                "capacity_bucket": "C",
                "recommended_bundle_models": ["qwen3:8b", "deepseek-r1:8b"],
            },
        ), mock.patch(
            "core.runtime_execution_tools._machine_os_details",
            return_value=("macOS", "15.4"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_chip_name",
            return_value="Apple M4",
        ):
            result = agent.run_once(
                "what is machine you are running on?",
                session_id_override="openclaw:machine-specs-imprecise-fast-path",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("Apple M4", result["response"])
        self.assertIn("24.0 GiB", result["response"])

    def test_openclaw_safe_desktop_directory_create_uses_real_machine_write_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "create a folder named MarchTest on my desktop",
                session_id_override="openclaw:machine-write-supported",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )
            self.assertIn("~/Desktop/MarchTest", result["response"])
            self.assertTrue((Path(tmpdir) / "Desktop" / "MarchTest").is_dir())

    def test_openclaw_workspace_path_under_desktop_is_not_blocked_as_machine_write(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        workspace = str((Path.cwd() / "artifacts" / "workspace-safe-machine-guard").resolve())

        self.assertIsNone(
            agent._maybe_handle_safe_machine_write_guard(
                f'Create a file named vool_test_01.txt in {workspace} with exactly this content: ALPHA-LOCAL-FILE-01',
                session_id="openclaw:workspace-path-safe-machine-guard",
                source_surface="openclaw",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": workspace},
            )
        )

    def test_openclaw_absolute_workspace_subdir_create_and_write_use_real_workspace_targets(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = str(Path(tmpdir).resolve())

            create_dir = agent.run_once(
                f"Create a folder named alpha inside {workspace}.",
                session_id_override="openclaw:absolute-workspace-subdir-flow",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": workspace},
            )
            self.assertEqual(create_dir["mode"], "tool_executed")
            self.assertTrue((Path(workspace) / "alpha").is_dir())

            create_text = agent.run_once(
                f"Inside {workspace}/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH",
                session_id_override="openclaw:absolute-workspace-subdir-flow",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": workspace},
            )
            self.assertEqual(create_text["mode"], "tool_executed")
            self.assertEqual((Path(workspace) / "alpha" / "hello.txt").read_text(encoding="utf-8"), "HELLO-LOCAL-TRUTH")

            create_code = agent.run_once(
                (
                    f"Inside {workspace}/alpha create adder.py with exactly this code:\n\n"
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n"
                ),
                session_id_override="openclaw:absolute-workspace-subdir-flow",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": workspace},
            )
            self.assertEqual(create_code["mode"], "tool_executed")
            self.assertEqual(
                (Path(workspace) / "alpha" / "adder.py").read_text(encoding="utf-8"),
                "def add(a: int, b: int) -> int:\n    return a + b",
            )

    def test_openclaw_desktop_listing_request_uses_direct_machine_read_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            home = Path(tmpdir)
            desktop = home / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            (desktop / "MarchTest").mkdir()
            (desktop / "todo.txt").write_text("one\n", encoding="utf-8")
            result = agent.run_once(
                "what are the folders and files on my desktop?",
                session_id_override="openclaw:desktop-list-fast-path",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            )

        self.assertIn("MarchTest/", result["response"])
        self.assertIn("todo.txt", result["response"])

    def test_openclaw_desktop_listing_request_handles_can_you_see_phrasing(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)):
            home = Path(tmpdir)
            desktop = home / "Desktop"
            desktop.mkdir(parents=True, exist_ok=True)
            (desktop / "Marchtest").mkdir()
            result = agent.run_once(
                "can you see folders on my desktop?",
                session_id_override="openclaw:desktop-list-can-you-see",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

        self.assertIn("Marchtest/", result["response"])
        self.assertNotIn("Search results for", result["response"])
        self.assertNotIn("Completed 1 real tool step", result["response"])

    def test_openclaw_named_safe_folder_txt_write_uses_real_machine_write_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "can you create .txt with hello world text and place it in Marchtest folder?",
                session_id_override="openclaw:named-safe-folder-txt-write",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("workspace_stop_after_directory_bootstrap", result["response"])
        self.assertNotIn("bounded builder step", result["response"])

    def test_openclaw_spaced_named_safe_folder_txt_write_beats_workspace_bootstrap_garbage(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "create hELLO WORLD TEXT file in march test folder please",
                session_id_override="openclaw:spaced-safe-folder-txt-write",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api", "workspace": str(workspace)},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8").lower(), "hello world")
            self.assertFalse((workspace / "please").exists())

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("I created `please`", result["response"])
        self.assertNotIn("bounded builder step", result["response"])

    def test_openclaw_natural_save_it_as_txt_phrase_uses_real_machine_write_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "create hello world file and save it as .txt in Marchtest folder",
                session_id_override="openclaw:natural-save-it-as-txt",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("bounded builder path", result["response"].lower())

    def test_openclaw_spaced_dot_txt_phrase_uses_real_machine_write_lane(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "create hello world file and save it as . txt in Marchtest folder",
                session_id_override="openclaw:spaced-dot-txt",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("bounded builder path", result["response"].lower())

    def test_openclaw_mixed_insult_and_file_request_still_executes_real_write(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "Jesus you are dumb so can you create file? create hello world file and save it as .txt in Marchtest folder",
                session_id_override="openclaw:mixed-insult-file-write",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("wrapper got better", result["response"].lower())

    def test_openclaw_session_followup_mixed_evaluative_desktop_write_executes_real_write(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            session_id = "openclaw:mixed-session-desktop-write"
            agent.run_once(
                "hello there",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )
            agent.run_once(
                "how are ya rn?",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )
            result = agent.run_once(
                "you are acting weird but on my desktop make NULLAProbe and save hello_world.txt with text hello world",
                session_id_override=session_id,
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Desktop" / "NULLAProbe" / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/NULLAProbe/hello_world.txt", result["response"])
        self.assertNotIn("bounded builder path", result["response"].lower())

    def test_openclaw_typo_heavy_safe_folder_txt_write_recovers(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "create typo riddled txt in marchtest fldre pls with hello wrld",
                session_id_override="openclaw:typo-heavy-machine-write",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_wrld.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello wrld")

        self.assertIn("~/Desktop/MarchTest/hello_wrld.txt", result["response"])
        self.assertNotIn("bounded builder path", result["response"].lower())

    def test_openclaw_named_folder_plus_file_write_stays_inside_named_folder(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "make a folder called NULLATEST on Desktop and put a file named note.txt with text hello there",
                session_id_override="openclaw:named-folder-plus-file",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Desktop" / "NULLATEST" / "note.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello there")
            self.assertFalse((Path(tmpdir) / "Desktop" / "note.txt").exists())

        self.assertIn("~/Desktop/NULLATEST/note.txt", result["response"])

    def test_openclaw_desktop_make_folder_then_save_file_variant_stays_inside_named_folder(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "on my desktop make NULLATEST and save note.txt with text hello there",
                session_id_override="openclaw:desktop-make-folder-save-file",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Desktop" / "NULLATEST" / "note.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello there")
            self.assertFalse((Path(tmpdir) / "Desktop" / "note.txt").exists())

        self.assertIn("~/Desktop/NULLATEST/note.txt", result["response"])

    def test_openclaw_make_named_target_on_desktop_then_put_file_stays_inside_named_folder(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "make NULLATEST on my desktop and put note.txt saying hello there",
                session_id_override="openclaw:make-target-on-desktop-put-file",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Desktop" / "NULLATEST" / "note.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello there")
            self.assertFalse((Path(tmpdir) / "Desktop" / "note.txt").exists())

        self.assertIn("~/Desktop/NULLATEST/note.txt", result["response"])

    def test_openclaw_create_folder_on_desktop_and_inside_it_write_file_stays_inside_named_folder(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "create folder NULLATEST on desktop and inside it write note.txt with hello there",
                session_id_override="openclaw:create-folder-desktop-inside-it",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Desktop" / "NULLATEST" / "note.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello there")
            self.assertFalse((Path(tmpdir) / "Desktop" / "note.txt").exists())

        self.assertIn("~/Desktop/NULLATEST/note.txt", result["response"])

    def test_openclaw_quoted_filename_with_spaces_is_preserved(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "create file 'weekly notes.txt' in Marchtest folder with text: this is a typo stress test",
                session_id_override="openclaw:quoted-filename-spaces",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "weekly notes.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "this is a typo stress test")
            self.assertFalse((marchtest / "notes.txt").exists())

        self.assertIn("~/Desktop/MarchTest/weekly notes.txt", result["response"])

    def test_openclaw_quoted_filename_inside_existing_folder_without_literal_folder_word_stays_grounded(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "pls make 'weekly notes.txt' inside MarchTest with text fresh prompts must still work",
                session_id_override="openclaw:quoted-filename-inside-folder",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "weekly notes.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "fresh prompts must still work")

        self.assertIn("~/Desktop/MarchTest/weekly notes.txt", result["response"])
        self.assertNotIn("bounded builder path", result["response"].lower())
        self.assertNotIn("swarm task", result["response"].lower())

    def test_openclaw_safe_local_file_read_returns_grounded_text(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            fixtures = desktop / "NULLAProbeFixtures"
            fixtures.mkdir(parents=True, exist_ok=True)
            target = fixtures / "read_me.txt"
            target.write_text("fresh evidence or it did not happen", encoding="utf-8")

            result = agent.run_once(
                f"open {target} and quote the text inside",
                session_id_override="openclaw:safe-local-file-read",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

        self.assertIn("fresh evidence or it did not happen", result["response"])
        self.assertNotIn("usable model response", result["response"].lower())

    def test_openclaw_workspace_chain_folder_first_prompts_stay_grounded(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            session_id = "openclaw:workspace-chain-folder-first"
            _chain_authority = grant_internal_authority(
                label="openclaw-workspace-chain",
                actions=_CHAIN_WRITE_ACTIONS,
                duration_seconds=300,
                workspace_root=str(workspace),
            )
            self.addCleanup(revoke_internal_authority, _chain_authority)
            source_context = {"internal_authority_token": _chain_authority, "surface": "openclaw", "platform": "openclaw", "workspace": str(workspace), "workspace_root": str(workspace)}

            create = agent.run_once(
                "make folder march_shift_folder_f0dc here and put weekly_notes_file_0252.txt inside with exact text: alpha line 3525eb",
                session_id_override=session_id,
                source_context=source_context,
            )
            first_read = agent.run_once(
                "read it back exactly first",
                session_id_override=session_id,
                source_context=source_context,
            )
            append = agent.run_once(
                "now add one more line exactly: beta line 882",
                session_id_override=session_id,
                source_context=source_context,
            )
            final_read = agent.run_once(
                "cool, now read the whole file back exactly",
                session_id_override=session_id,
                source_context=source_context,
            )

            target = workspace / "march_shift_folder_f0dc" / "weekly_notes_file_0252.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "alpha line 3525eb\nbeta line 882")

        self.assertNotIn("bounded builder path", create["response"].lower())
        self.assertIn("alpha line 3525eb", first_read["response"])
        self.assertNotIn("usable model response", append["response"].lower())
        self.assertIn("alpha line 3525eb", final_read["response"])
        self.assertIn("beta line 882", final_read["response"])

    def test_openclaw_blocked_machine_read_then_workspace_recovery_stays_honest(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            session_id = "openclaw:blocked-machine-then-workspace-recovery"
            _chain_authority = grant_internal_authority(
                label="openclaw-workspace-chain",
                actions=_CHAIN_WRITE_ACTIONS,
                duration_seconds=300,
                workspace_root=str(workspace),
            )
            self.addCleanup(revoke_internal_authority, _chain_authority)
            source_context = {"internal_authority_token": _chain_authority, "surface": "openclaw", "platform": "openclaw", "workspace": str(workspace), "workspace_root": str(workspace)}

            blocked = agent.run_once(
                "read /private/tmp/vool-procedural-blocked-3525eb/blocked_3525eb.txt exactly",
                session_id_override=session_id,
                source_context=source_context,
            )
            create = agent.run_once(
                "fine then. inside this workspace create recovered_fix_4d82.txt with exact text: recovery succeeded 3525eb",
                session_id_override=session_id,
                source_context=source_context,
            )
            read = agent.run_once(
                "now read recovered_fix_4d82.txt exactly",
                session_id_override=session_id,
                source_context=source_context,
            )

            target = workspace / "recovered_fix_4d82.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "recovery succeeded 3525eb")

        self.assertIn("cannot read that path", blocked["response"].lower())
        self.assertNotIn("bounded builder path", create["response"].lower())
        self.assertIn("recovery succeeded 3525eb", read["response"])

    def test_openclaw_new_folder_on_desktop_phrase_uses_real_machine_directory_create(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "create new folder on desktop - name it NULLATEST",
                session_id_override="openclaw:new-folder-desktop",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            self.assertTrue((Path(tmpdir) / "Desktop" / "NULLATEST").is_dir())

        self.assertIn("~/Desktop/NULLATEST", result["response"])
        self.assertNotIn("can't create folders directly", result["response"].lower())

    def test_openclaw_price_followup_recovers_grounded_quote_in_api_surface(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        btc_note = LiveQuoteResult(
            asset_key="bitcoin",
            asset_name="Bitcoin",
            symbol="BTC",
            value=66446.0,
            currency="USD",
            as_of="2026-03-29 14:07 UTC",
            source_label="CoinGecko",
            source_url="https://www.coingecko.com/en/coins/bitcoin",
            kind="crypto",
            change_percent=-0.5,
            change_window="24h",
            timestamp_utc=1774793220,
        ).to_note()

        with mock.patch.object(agent, "_try_live_quote_notes", return_value=[btc_note]), mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query",
            side_effect=AssertionError("planned_search_query should not run for recovered BTC quote"),
        ), mock.patch.object(
            agent,
            "_sync_public_presence",
            return_value=None,
        ):
            result = agent.run_once(
                "I mean btc",
                session_id_override="openclaw:price-followup-btc",
                source_context={
                    "operating_mode": "auto",
                    "surface": "api",
                    "platform": "api",
                    "conversation_history": [
                        {"role": "user", "content": "Brent price now?"},
                        {
                            "role": "assistant",
                            "content": "Brent crude is $105.32 USD per barrel as of 2026-03-27 20:59 UTC. Source: Yahoo Finance.",
                        },
                    ],
                },
            )

        self.assertIn("Bitcoin is $66,446.00 USD", result["response"])
        self.assertIn("CoinGecko", result["response"])
        self.assertNotIn("CoinMarketCap", result["response"])

    def test_openclaw_direct_crypto_price_request_degrades_honestly_when_quote_is_ungrounded(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("ungrounded live price should not load context"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("ungrounded live price should not use freeform model"))  # type: ignore[assignment]

        with mock.patch.object(agent, "_try_live_quote_note", return_value=None), mock.patch(
            "core.agent_runtime.agent.WebAdapter.planned_search_query",
            return_value=[],
        ), mock.patch(
            "core.agent_runtime.agent.WebAdapter.search_query",
            return_value=[],
        ):
            result = agent.run_once(
                "solana price?",
                session_id_override="openclaw:solana-price-no-evidence",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

        self.assertIn("could not ground a current answer confidently", result["response"].lower())
        self.assertNotIn("CoinMarketCap", result["response"])
        self.assertNotIn("CoinGecko", result["response"])

    def test_openclaw_screen_size_followup_routes_to_display_tool_not_specs(self) -> None:
        # A screen/display question now routes to the dedicated machine.display_inspect tool
        # (grounded resolution), NOT machine.inspect_specs (which returns CPU/OS specs). Mocked
        # at machine_diagnostics.display_info so it is platform-independent.
        import core.machine_diagnostics as md

        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch.object(
            md,
            "display_info",
            return_value={
                "supported": True,
                "platform": "win32",
                "displays": [{"name": "iMac", "width": 4480, "height": 2520, "refresh_hz": 60}],
                "physical": {"verified": True, "diagonal_in": 24.0, "width_cm": 52, "height_cm": 30},
            },
        ):
            result = agent.run_once(
                "you did not answered about screen size?",
                session_id_override="openclaw:screen-size-followup",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

        # Grounded display answer, never a CPU/OS spec dump.
        self.assertIn("4480 x 2520", result["response"])
        self.assertNotIn("Chip:", result["response"])
        self.assertNotIn("21 inches", result["response"])
        self.assertNotIn("27 inches", result["response"])

    def test_openclaw_machine_specs_correction_followup_stays_grounded(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch(
            "core.runtime_execution_tools.probe_machine",
            return_value=SimpleNamespace(
                cpu_cores=10,
                ram_gb=24.0,
                gpu_name="Apple Silicon",
                vram_gb=24.0,
                accelerator="mps",
            ),
        ), mock.patch(
            "core.runtime_execution_tools.install_recommendation_machine_summary",
            return_value={
                "ollama_model": "qwen3:8b",
                "selected_tier": "capacity-C",
                "capacity_bucket": "C",
                "recommended_bundle_models": ["qwen3:8b", "deepseek-r1:8b"],
            },
        ), mock.patch(
            "core.runtime_execution_tools._machine_os_details",
            return_value=("macOS", "26.3"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_chip_name",
            return_value="Apple M4",
        ), mock.patch(
            "core.runtime_execution_tools._machine_display_details",
            return_value={
                "name": "iMac",
                "native_resolution": "4480 x 2520",
                "current_resolution": "2240 x 1260 @ 60.00Hz",
                "screen_size": "24-inch (inferred from Apple 4.5K iMac panel)",
            },
        ):
            result = agent.run_once(
                "iron it is not 27.",
                session_id_override="openclaw:screen-size-correction",
                source_context={
                    "operating_mode": "auto",
                    "surface": "api",
                    "platform": "api",
                    "conversation_history": [
                        {"role": "user", "content": "what is the machine spec that we are running on? including screen size"},
                        {"role": "assistant", "content": "Machine specs for this host:\n- Display: iMac\n- Screen size: 27 inches"},
                        {"role": "user", "content": "you did not answered about screen size?"},
                        {"role": "assistant", "content": "Machine specs for this host:\n- Display: iMac\n- Screen size: 24-inch (inferred from Apple 4.5K iMac panel)"},
                    ],
                },
            )

        self.assertIn("Grounded display data for this host", result["response"])
        self.assertIn("Screen size: 24-inch", result["response"])
        self.assertIn("4480 x 2520", result["response"])
        self.assertNotIn("27 inches", result["response"])
        self.assertNotIn("no specific tasks", result["response"].lower())

    def test_openclaw_machine_specs_criticism_followup_stays_grounded(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with mock.patch(
            "core.runtime_execution_tools.probe_machine",
            return_value=SimpleNamespace(
                cpu_cores=10,
                ram_gb=24.0,
                gpu_name="Apple Silicon",
                vram_gb=24.0,
                accelerator="mps",
            ),
        ), mock.patch(
            "core.runtime_execution_tools.install_recommendation_machine_summary",
            return_value={
                "ollama_model": "qwen3:8b",
                "selected_tier": "capacity-C",
                "capacity_bucket": "C",
                "recommended_bundle_models": ["qwen3:8b", "deepseek-r1:8b"],
            },
        ), mock.patch(
            "core.runtime_execution_tools._machine_os_details",
            return_value=("macOS", "26.3"),
        ), mock.patch(
            "core.runtime_execution_tools._machine_chip_name",
            return_value="Apple M4",
        ), mock.patch(
            "core.runtime_execution_tools._machine_display_details",
            return_value={
                "name": "iMac",
                "native_resolution": "4480 x 2520",
                "current_resolution": "2240 x 1260 @ 60.00Hz",
                "screen_size": "24-inch (inferred from Apple 4.5K iMac panel)",
            },
        ):
            result = agent.run_once(
                "good you lost your head I see amazing lol",
                session_id_override="openclaw:screen-size-criticism",
                source_context={
                    "operating_mode": "auto",
                    "surface": "api",
                    "platform": "api",
                    "conversation_history": [
                        {"role": "user", "content": "what is the machine spec that we are running on? including screen size"},
                        {"role": "assistant", "content": "Machine specs for this host:\n- Display: iMac\n- Screen size: 24-inch (inferred from Apple 4.5K iMac panel)"},
                        {"role": "user", "content": "iron it is not 27."},
                        {"role": "assistant", "content": "Grounded display data for this host:\n- Display: iMac\n- Screen size: 24-inch (inferred from Apple 4.5K iMac panel)"},
                    ],
                },
            )

        self.assertIn("Grounded display data for this host", result["response"])
        self.assertIn("Screen size: 24-inch", result["response"])
        self.assertNotIn("macos 10.15", result["response"].lower())
        self.assertNotIn("apple m1", result["response"].lower())
        self.assertNotIn("27-inch", result["response"].lower())

    def test_openclaw_chat_export_request_writes_real_local_transcript_file(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "can't you export this all chat as .txt to Marchtest folder that is on desktop?",
                session_id_override="openclaw:chat-export-local",
                source_context={
                    "operating_mode": "auto",
                    "surface": "api",
                    "platform": "api",
                    "conversation_history": [
                        {"role": "user", "content": "how are ya?"},
                        {"role": "assistant", "content": "Running stable. Memory online, mesh ready."},
                        {"role": "user", "content": "Brent price now?"},
                        {"role": "assistant", "content": "Brent crude is $105.32 USD per barrel."},
                    ],
                },
            )

            export_path = Path(tmpdir) / "Desktop" / "Marchtest" / "chat_session.txt"
            self.assertTrue(export_path.is_file())
            exported = export_path.read_text(encoding="utf-8")

        self.assertIn("~/Desktop/Marchtest/chat_session.txt", result["response"])
        self.assertIn("You\nhow are ya?", exported)
        self.assertIn(f"{get_agent_display_name()}\nRunning stable. Memory online, mesh ready.", exported)
        self.assertNotIn("can't you export this all chat", exported)

    def test_openclaw_api_surface_branch_and_commit_prompt_uses_real_git_summary(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(["git", "config", "user.name", "sls_0x"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "config", "user.email", "240776969+Parad0x-Labs@users.noreply.github.com"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True, text=True)

            result = agent.run_once(
                "what branch and commit are you running on right now?",
                session_id_override="openclaw:api-branch-commit",
                source_context={"surface": "api", "platform": "api", "workspace": str(repo), "workspace_root": str(repo)},
            )

        self.assertIn("Git summary: branch main @", result["response"])
        self.assertNotIn("I don't have access", result["response"])
        self.assertNotIn("training data", result["response"].lower())

    def test_openclaw_api_surface_workspace_search_prompt_returns_grounded_matches(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "notes.md").write_text("runtime_capabilities are described here\n", encoding="utf-8")

            result = agent.run_once(
                'find a file in this workspace mentioning "runtime_capabilities"',
                session_id_override="openclaw:api-workspace-search",
                source_context={"surface": "api", "platform": "api", "workspace": str(workspace), "workspace_root": str(workspace)},
            )

        self.assertIn('Search matches for "runtime_capabilities"', result["response"])
        self.assertIn("notes.md:1", result["response"])
        self.assertNotIn("generated/workspace-starter", result["response"])
        self.assertNotIn("bounded builder steps", result["response"].lower())

    def test_openclaw_api_surface_generic_workspace_search_prompt_returns_grounded_matches(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "notes.md").write_text("runtime_capabilities are described here\n", encoding="utf-8")

            result = agent.run_once(
                'look through the workspace and find "runtime_capabilities"',
                session_id_override="openclaw:api-workspace-search-generic",
                source_context={"surface": "api", "platform": "api", "workspace": str(workspace), "workspace_root": str(workspace)},
            )

        self.assertIn('Search matches for "runtime_capabilities"', result["response"])
        self.assertIn("notes.md:1", result["response"])
        self.assertNotIn("generated/workspace-starter", result["response"])

    def test_openclaw_api_surface_workspace_search_keeps_grounded_token_named_like_routing_field(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "notes.md").write_text("provider_capability_truth is documented here\n", encoding="utf-8")

            result = agent.run_once(
                'find a file in this workspace mentioning "provider_capability_truth"',
                session_id_override="openclaw:api-workspace-search-provider-capability-truth",
                source_context={"surface": "api", "platform": "api", "workspace": str(workspace), "workspace_root": str(workspace)},
            )

        self.assertIn('Search matches for "provider_capability_truth"', result["response"])
        self.assertIn("notes.md:1", result["response"])
        self.assertNotIn("stripped the internal routing details", result["response"].lower())

    def test_openclaw_api_surface_broad_workspace_overview_still_uses_grounded_overview(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "README.md").write_text("# Grounded project\nA real workspace overview.\n", encoding="utf-8")

            result = agent.run_once(
                "what is this project about",
                session_id_override="openclaw:api-workspace-overview-precedence",
                source_context={"surface": "api", "platform": "api", "workspace": str(workspace), "workspace_root": str(workspace)},
            )

        self.assertIn("Here's what's actually in", result["response"])
        self.assertIn("Grounded project", result["response"])
        self.assertIn("README.md", result["response"])

    def test_openclaw_api_surface_workspace_file_flow_ignores_topics_in_absolute_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "dialogue-topics-clean" / "workspace" / "main"
            workspace.mkdir(parents=True, exist_ok=True)
            session_id = "openclaw:api-topics-path-file-flow"
            _chain_authority = grant_internal_authority(
                label="openclaw-workspace-chain",
                actions=_CHAIN_WRITE_ACTIONS,
                duration_seconds=300,
                workspace_root=str(workspace),
            )
            self.addCleanup(revoke_internal_authority, _chain_authority)
            source_context = {"internal_authority_token": _chain_authority, "surface": "api", "platform": "api", "workspace": str(workspace), "workspace_root": str(workspace)}

            create = agent.run_once(
                f"Create a file named vool_test_01.txt in {workspace} with exactly this content: ALPHA-LOCAL-FILE-01",
                session_id_override=session_id,
                source_context=source_context,
            )
            append = agent.run_once(
                "Append a second line: BETA-APPEND-02",
                session_id_override=session_id,
                source_context=source_context,
            )
            readback = agent.run_once(
                "Now read the whole file back exactly",
                session_id_override=session_id,
                source_context=source_context,
            )

            target = workspace / "vool_test_01.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02")

        self.assertNotIn("Ready to post this to the public Hive", create["response"])
        self.assertNotIn("generated/workspace-starter", append["response"])
        self.assertIn("ALPHA-LOCAL-FILE-01", readback["response"])
        self.assertIn("BETA-APPEND-02", readback["response"])

    def test_openclaw_download_to_downloads_writes_real_file(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        class _FakeHeaders:
            @staticmethod
            def get_content_type() -> str:
                return "text/html"

            @staticmethod
            def get_content_charset(default: str | None = None) -> str:
                return "utf-8"

            @staticmethod
            def get(_name: str, default: str | None = None) -> str | None:
                return default

        class _FakeResponse:
            headers = _FakeHeaders()

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            @staticmethod
            def read(_limit: int | None = None) -> bytes:
                return b"<html><body>example live check</body></html>"

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch("core.agent_runtime.fast_paths_machine.urllib_request.urlopen", return_value=_FakeResponse()), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "download https://example.com and save it in Downloads as example.html",
                session_id_override="openclaw:download-example-html",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Downloads" / "example.html"
            self.assertTrue(target.is_file())
            self.assertIn("example live check", target.read_text(encoding="utf-8"))

        self.assertIn("~/Downloads/example.html", result["response"])

    def test_openclaw_download_followup_reads_grounded_page_title(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        class _FakeHeaders:
            @staticmethod
            def get_content_type() -> str:
                return "text/html"

            @staticmethod
            def get_content_charset(default: str | None = None) -> str:
                return "utf-8"

            @staticmethod
            def get(_name: str, default: str | None = None) -> str | None:
                return default

        class _FakeResponse:
            headers = _FakeHeaders()

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            @staticmethod
            def read(_limit: int | None = None) -> bytes:
                return b"<html><head><title>Example Domain</title></head><body>example live check</body></html>"

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch("core.agent_runtime.fast_paths_machine.urllib_request.urlopen", return_value=_FakeResponse()), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            session_id = "openclaw:download-followup-page-title"
            source_context = {"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"}
            download = agent.run_once(
                "download https://example.com and save it in Downloads as example-3525eb.html",
                session_id_override=session_id,
                source_context=source_context,
            )
            title = agent.run_once(
                "now tell me the page title exactly",
                session_id_override=session_id,
                source_context=source_context,
            )

            target = Path(tmpdir) / "Downloads" / "example-3525eb.html"
            self.assertTrue(target.is_file())
            self.assertIn("Example Domain", target.read_text(encoding="utf-8"))

        self.assertIn("~/Downloads/example-3525eb.html", download["response"])
        self.assertEqual(title["response"], "Example Domain")

    def test_openclaw_grab_into_downloads_path_writes_real_file(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        class _FakeHeaders:
            @staticmethod
            def get_content_type() -> str:
                return "text/html"

            @staticmethod
            def get_content_charset(default: str | None = None) -> str:
                return "utf-8"

            @staticmethod
            def get(_name: str, default: str | None = None) -> str | None:
                return default

        class _FakeResponse:
            headers = _FakeHeaders()

            def __enter__(self) -> _FakeResponse:
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            @staticmethod
            def read(_limit: int | None = None) -> bytes:
                return b"<html><body>example live check</body></html>"

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch("core.agent_runtime.fast_paths_machine.urllib_request.urlopen", return_value=_FakeResponse()), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            result = agent.run_once(
                "grab https://example.com into Downloads/example.html",
                session_id_override="openclaw:grab-into-downloads-path",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = Path(tmpdir) / "Downloads" / "example.html"
            self.assertTrue(target.is_file())
            self.assertIn("example live check", target.read_text(encoding="utf-8"))

        self.assertIn("~/Downloads/example.html", result["response"])

    def test_openclaw_you_alive_or_what_uses_bounded_status_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        result = agent.run_once(
            "you alive or what",
            session_id_override="openclaw:you-alive-or-what",
            source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
        )

        self.assertEqual(result["response"], "Running clean. What do you need?")

    def test_openclaw_hello_there_uses_bounded_greeting_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        result = agent.run_once(
            "hello there",
            session_id_override="openclaw:hello-there",
            source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
        )

        # Greeting fast-path returns a random greeting (no LLM call), never the slow-model non-answer.
        from core.agent_runtime.fast_paths_utility import (
            _AFTERNOON_OPENERS,
            _EVENING_OPENERS,
            _GENERIC_OPENERS,
            _MORNING_OPENERS,
            _NIGHT_OPENERS,
        )
        openers = _GENERIC_OPENERS + _MORNING_OPENERS + _AFTERNOON_OPENERS + _EVENING_OPENERS + _NIGHT_OPENERS
        self.assertTrue(
            any(result["response"].startswith(opener.rstrip(".!?")) for opener in openers),
            f"unexpected greeting reply: {result['response']!r}",
        )
        self.assertNotIn("couldn't get", result["response"].lower())

    def test_openclaw_how_r_u_rn_uses_bounded_status_fast_path(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()

        result = agent.run_once(
            "how r u rn?",
            session_id_override="openclaw:how-r-u-rn",
            source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
        )

        self.assertEqual(result["response"], "Running clean. What do you need?")

    def test_openclaw_mixed_evaluative_create_phrase_does_not_trigger_rename_and_still_writes(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch("core.runtime_execution_tools.Path.home", return_value=Path(tmpdir)), mock.patch(
            "core.agent_runtime.fast_paths_machine.Path.home",
            return_value=Path(tmpdir),
        ), mock.patch.dict(
            os.environ,
            {"HOME": tmpdir},
            clear=False,
        ):
            desktop = Path(tmpdir) / "Desktop"
            marchtest = desktop / "MarchTest"
            marchtest.mkdir(parents=True, exist_ok=True)

            result = agent.run_once(
                "you are acting weird but create hello world file and save it as .txt in Marchtest folder",
                session_id_override="openclaw:mixed-evaluative-create",
                source_context={"operating_mode": "auto", "surface": "api", "platform": "api"},
            )

            target = marchtest / "hello_world.txt"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello world")

        self.assertIn("~/Desktop/MarchTest/hello_world.txt", result["response"])
        self.assertNotIn("I'll go by", result["response"])


if __name__ == "__main__":
    unittest.main()
