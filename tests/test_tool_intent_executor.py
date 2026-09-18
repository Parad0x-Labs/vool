from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.curiosity_roamer import AdaptiveResearchResult
from core.execution import (
    ToolIntentExecution as ExtractedToolIntentExecution,
)
from core.execution import (
    plan_tool_workflow as extracted_plan_tool_workflow,
)
from core.execution import (
    should_attempt_tool_intent as extracted_should_attempt_tool_intent,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.public_hive_bridge import PublicHiveBridgeConfig
from core.runtime_execution_tools import execute_runtime_tool
from core.task_router import classify
from core.tool_intent_executor import (
    ToolIntentExecution,
    execute_tool_intent,
    plan_tool_workflow,
    runtime_tool_specs,
    should_attempt_tool_intent,
)


class ToolIntentExecutorTests(unittest.TestCase):
    def test_facade_exports_share_extracted_execution_symbols(self) -> None:
        self.assertIs(plan_tool_workflow, extracted_plan_tool_workflow)
        self.assertIs(should_attempt_tool_intent, extracted_should_attempt_tool_intent)
        self.assertIs(ToolIntentExecution, ExtractedToolIntentExecution)

    def test_builder_style_integration_request_skips_tool_intent_gate(self) -> None:
        should_run = should_attempt_tool_intent(
            "Help me build a next gen Telegram bot from official docs and good GitHub repos.",
            task_class="integration_orchestration",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(should_run)

    def test_advice_only_patch_plan_skips_tool_intent_gate(self) -> None:
        should_run = should_attempt_tool_intent(
            "Plan a safe patch for this repo runtime bug. Do not edit files; explain what should be verified.",
            task_class="system_design",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(should_run)

    def test_runtime_tool_specs_hide_mutating_hive_tools_without_write_auth(self) -> None:
        with mock.patch(
            "core.tool_intent_executor.load_public_hive_bridge_config",
            return_value=PublicHiveBridgeConfig(
                enabled=True,
                meet_seed_urls=("https://seed-eu.example.test:8766",),
                topic_target_url="https://seed-eu.example.test:8766",
                auth_token=None,
            ),
        ), mock.patch(
            "core.tool_intent_executor.load_hive_activity_tracker_config",
            return_value=HiveActivityTrackerConfig(enabled=True, watcher_api_url="https://watch.example.test/api/dashboard"),
        ):
            intents = {item["intent"] for item in runtime_tool_specs()}

        self.assertIn("hive.list_available", intents)
        self.assertIn("hive.list_research_queue", intents)
        self.assertNotIn("hive.research_topic", intents)
        self.assertNotIn("hive.submit_result", intents)

    def test_execute_web_search_intent_formats_results(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch(
            "core.tool_intent_executor.policy_engine.allow_web_fallback", return_value=True
        ), mock.patch("core.tool_intent_executor.load_builtin_tools", return_value=None), mock.patch(
            "core.tool_intent_executor.WebAdapter.planned_search_query",
            return_value=[
                {
                    "result_title": "Qwen release notes",
                    "result_url": "https://example.test/qwen",
                    "summary": "Fresh update summary",
                    "source_profile_label": "Official docs",
                },
                {
                    "result_title": "OpenClaw changelog",
                    "result_url": "https://example.test/openclaw",
                    "summary": "OpenClaw runtime changes",
                    "source_profile_label": "Reputable repositories",
                },
            ],
        ):
            result = execute_tool_intent(
                {"intent": "web.search", "arguments": {"query": "latest qwen release notes", "limit": 2}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_executed")
        self.assertIn("Search results for", result.response_text)
        self.assertIn("https://example.test/qwen", result.response_text)
        self.assertEqual(result.details["observation"]["tool_surface"], "web")
        self.assertEqual(result.details["observation"]["query"], "latest qwen release notes")
        self.assertEqual(result.details["observation"]["results"][0]["url"], "https://example.test/qwen")

    def test_execute_unknown_tool_intent_fails_honestly(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        result = execute_tool_intent(
            {"intent": "fake.magic", "arguments": {}},
            task_id="task-123",
            session_id="session-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
        )

        self.assertTrue(result.handled)
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, "tool_failed")
        self.assertIn("not wired", result.response_text)
        self.assertEqual(result.details["observation"]["status"], "unsupported")

    def test_chat_surface_natural_write_request_reaches_workspace_write_file(self) -> None:
        # Regression for the chat/API router defect: a plain natural-language
        # "create a file ... with ..." request from the OpenClaw chat/API surface
        # must clear the entry gate, get planned into workspace.write_file, and
        # actually execute the workspace write -- not bounce off as "cannot map".
        user_text = "create a file test.txt with hello"
        source_context = {"operating_mode": "auto", "surface": "api", "platform": "api", "workspace_root": "/tmp/vool-router-regression"}

        # 1) Entry gate the chat/API loop checks before doing any tool work.
        self.assertTrue(
            should_attempt_tool_intent(
                user_text,
                task_class=classify(user_text).get("task_class", "unknown"),
                source_context=source_context,
            ),
            "natural file-write request was rejected at the tool-intent entry gate",
        )

        # 2) The deterministic planner the loop runs first must map it to
        #    workspace.write_file (no reliance on the model picking it).
        decision = plan_tool_workflow(
            user_text=user_text,
            task_class="chat_conversation",
            executed_steps=[],
            source_context=source_context,
        )
        self.assertTrue(decision.handled)
        self.assertIsNotNone(decision.next_payload)
        self.assertEqual(decision.next_payload["intent"], "workspace.write_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "test.txt")
        self.assertEqual(decision.next_payload["arguments"]["content"], "hello")

        # 3) Dispatching that payload through the same execute_tool_intent entry
        #    the loop uses must reach and run the real workspace.write_file branch
        #    (FS write mocked so no file is actually created). workspace.write_file
        #    writes through the shared `atomic_write_text` helper (temp file + os.replace),
        #    not `Path.write_text` directly -- mocking the helper is what now stands in
        #    for "no real file touches disk".
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch("core.runtime_execution_tools.atomic_write_text") as atomic_write:
            result = execute_tool_intent(
                decision.next_payload,
                task_id="task-router",
                session_id="session-router",
                source_context=source_context,
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.tool_name, "workspace.write_file")
        self.assertEqual(result.mode, "tool_executed")
        self.assertEqual(result.details["observation"]["intent"], "workspace.write_file")
        # The executor's real workspace.write_file branch wrote the requested content to disk
        # (write mocked). `atomic_write_text(path, content)` -- content is the second argument.
        self.assertTrue(atomic_write.called)
        self.assertEqual(atomic_write.call_args_list[0].args[1], "hello")
        self.assertEqual(result.details["path"], "test.txt")

    def test_write_file_no_longer_calls_path_write_text_directly(self) -> None:
        """Proves the mock-target correction above is a genuine production-call-path fix, not a
        weakened test double: (a) patching the OLD interception point (`Path.write_text`) no
        longer sees the write at all -- because production genuinely stopped calling it -- and
        (b) patching the CURRENT one (`atomic_write_text`) does. Both facts have to hold, or the
        earlier test would be passing for the wrong reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "a.txt"

            with mock.patch("core.runtime_execution_tools.Path.write_text") as legacy_mock:
                result = execute_runtime_tool(
                    "workspace.write_file",
                    {"path": "a.txt", "content": "hello"},
                    source_context={"workspace": tmpdir},
                )
            self.assertTrue(result.ok)
            self.assertFalse(
                legacy_mock.called,
                "Path.write_text must NOT be called by workspace.write_file -- if this starts "
                "failing, production reverted to the old direct-write call path and the atomic-"
                "write guarantee (and the mock in the test above) no longer holds.",
            )
            # The real write actually happened (not mocked this time) -- confirms (a) isn't passing
            # merely because the whole call silently no-op'd.
            self.assertEqual(path.read_text(encoding="utf-8"), "hello")

            path.unlink()
            with mock.patch("core.runtime_execution_tools.atomic_write_text") as current_mock:
                result = execute_runtime_tool(
                    "workspace.write_file",
                    {"path": "a.txt", "content": "hello again"},
                    source_context={"workspace": tmpdir},
                )
            self.assertTrue(result.ok)
            self.assertTrue(current_mock.called, "atomic_write_text IS the current write call site")
            self.assertFalse(path.exists(), "the write was mocked -- no file should have landed")

    def test_trailing_instruction_not_baked_into_file_content(self) -> None:
        # Regression for the greedy create-file content captures: a trailing
        # instruction after the body (" and then run the tests") must NOT be
        # swallowed into the written file. The content stops at the conjunction.
        source_context = {"surface": "api", "platform": "api", "workspace_root": "/tmp/vool-router-regression"}
        for user_text in (
            "create a file a.txt with hello and then run the tests",
            "create a file a.txt with content hello and then run the tests",
            "create a file a.txt with hello and run the tests",
        ):
            decision = plan_tool_workflow(
                user_text=user_text,
                task_class="chat_conversation",
                executed_steps=[],
                source_context=source_context,
            )
            self.assertTrue(decision.handled, user_text)
            self.assertIsNotNone(decision.next_payload, user_text)
            self.assertEqual(decision.next_payload["intent"], "workspace.write_file", user_text)
            self.assertEqual(decision.next_payload["arguments"]["path"], "a.txt", user_text)
            self.assertEqual(decision.next_payload["arguments"]["content"], "hello", user_text)

    def test_legit_single_file_content_with_bare_and_is_preserved(self) -> None:
        # The conjunction stop must stay narrow: a bare " and " between words is
        # real body text, not an instruction boundary, so it stays in the file.
        source_context = {"surface": "api", "platform": "api", "workspace_root": "/tmp/vool-router-regression"}
        decision = plan_tool_workflow(
            user_text="create a file greet.txt with hello and world",
            task_class="chat_conversation",
            executed_steps=[],
            source_context=source_context,
        )
        self.assertTrue(decision.handled)
        self.assertIsNotNone(decision.next_payload)
        self.assertEqual(decision.next_payload["arguments"]["path"], "greet.txt")
        self.assertEqual(decision.next_payload["arguments"]["content"], "hello and world")

    def test_conjoined_two_file_request_yields_two_writes_with_split_content(self) -> None:
        # Regression: "create a file x.txt with A and create a file y.txt with B"
        # must NOT drop the second file or merge its text into the first. The
        # planner emits one write per distinct file, in order, across steps.
        source_context = {"surface": "api", "platform": "api", "workspace_root": "/tmp/vool-router-regression"}
        user_text = "create a file x.txt with A and create a file y.txt with B"

        first = plan_tool_workflow(
            user_text=user_text,
            task_class="chat_conversation",
            executed_steps=[],
            source_context=source_context,
        )
        self.assertTrue(first.handled)
        self.assertIsNotNone(first.next_payload)
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "x.txt")
        self.assertEqual(first.next_payload["arguments"]["content"], "A")

        executed_steps = [
            {
                "tool_name": "workspace.write_file",
                "arguments": {"path": "x.txt", "content": "A"},
                "observation": {"intent": "workspace.write_file"},
            }
        ]
        second = plan_tool_workflow(
            user_text=user_text,
            task_class="chat_conversation",
            executed_steps=executed_steps,
            source_context=source_context,
        )
        self.assertTrue(second.handled)
        self.assertIsNotNone(second.next_payload)
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "y.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "B")

    def test_execute_hive_submit_result_uses_public_bridge(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "status": "result_submitted",
            "topic_id": "topic-1234567890abcdef",
            "post_id": "post-123",
        }

        result = execute_tool_intent(
            {
                "intent": "hive.submit_result",
                "arguments": {
                    "topic_id": "topic-1234567890abcdef",
                    "body": "Done. Real event stream is live.",
                    "result_status": "solved",
                    "claim_id": "claim-123",
                },
            },
            task_id="task-123",
            session_id="session-123",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
        )

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_executed")
        self.assertIn("marked it `solved`", result.response_text)
        bridge.submit_public_topic_result.assert_called_once()

    def test_execute_hive_export_research_packet_uses_public_bridge(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.get_public_research_packet.return_value = {
            "topic": {"topic_id": "topic-1", "title": "Research packet topic"},
            "execution_state": {"execution_state": "claimed"},
            "counts": {"post_count": 3, "evidence_count": 5},
        }

        result = execute_tool_intent(
            {"intent": "hive.export_research_packet", "arguments": {"topic_id": "topic-1"}},
            task_id="task-123",
            session_id="session-123",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
        )

        self.assertTrue(result.ok)
        self.assertIn("Exported machine-readable research packet", result.response_text)
        bridge.get_public_research_packet.assert_called_once_with("topic-1")

    def test_execute_hive_research_topic_uses_autonomous_lane(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        with mock.patch(
            "core.tool_intent_executor.research_topic_from_signal",
            return_value=mock.Mock(
                to_dict=lambda: {
                    "ok": True,
                    "status": "completed",
                    "response_text": "Autonomous research finished.",
                    "artifact_ids": ["artifact-1", "artifact-2"],
                    "candidate_ids": ["candidate-1"],
                }
            ),
        ) as research_topic_from_signal:
            result = execute_tool_intent(
                {"intent": "hive.research_topic", "arguments": {"topic_id": "topic-1"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
                public_hive_bridge=bridge,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")
        self.assertIn("Autonomous research finished", result.response_text)
        research_topic_from_signal.assert_called_once()

    def test_execute_operator_tool_adds_structured_observation(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        dispatch = SimpleNamespace(
            ok=True,
            status="reported",
            response_text="Visible services or startup agents:\n- launchd.test: running",
            details={"services": [{"name": "launchd.test", "state": "running"}]},
            learned_plan=None,
        )

        with mock.patch("core.tool_intent_executor.dispatch_operator_action", return_value=dispatch):
            result = execute_tool_intent(
                {"intent": "operator.inspect_services", "arguments": {}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "tool_executed")
        self.assertNotIn("approval_request", result.details)
        self.assertEqual(result.details["observation"]["tool_surface"], "local_operator")
        self.assertEqual(result.details["observation"]["details"]["services"][0]["name"], "launchd.test")

    def test_execute_web_research_uses_adaptive_controller(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch(
            "core.tool_intent_executor.policy_engine.allow_web_fallback", return_value=True
        ), mock.patch(
            "core.tool_intent_executor.CuriosityRoamer.adaptive_research",
            return_value=AdaptiveResearchResult(
                enabled=True,
                reason="research_task",
                strategy="compare",
                actions_taken=["initial_search", "compare_sources", "stop_answer"],
                queries_run=["supabase firebase bot backend", "supabase firebase comparison tradeoffs sources"],
                notes=[
                    {
                        "result_title": "Supabase docs",
                        "result_url": "https://supabase.com/docs",
                        "summary": "Supabase is Postgres-first.",
                        "origin_domain": "supabase.com",
                    },
                    {
                        "result_title": "Firebase docs",
                        "result_url": "https://firebase.google.com/docs",
                        "summary": "Firebase has tighter managed integrations.",
                        "origin_domain": "firebase.google.com",
                    },
                ],
                source_domains=["supabase.com", "firebase.google.com"],
                evidence_strength="strong",
                compared_sources=True,
                stop_reason="comparison_ready",
            ),
        ):
            result = execute_tool_intent(
                {"intent": "web.research", "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.ok)
        self.assertIn("Adaptive web research", result.response_text)
        self.assertEqual(result.details["observation"]["strategy"], "compare")
        self.assertIn("compare_sources", result.details["observation"]["actions_taken"])
        self.assertEqual(result.details["observation"]["hits"][1]["domain"], "firebase.google.com")

    def test_execute_web_research_reports_uncertainty_honestly(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch(
            "core.tool_intent_executor.policy_engine.allow_web_fallback", return_value=True
        ), mock.patch(
            "core.tool_intent_executor.CuriosityRoamer.adaptive_research",
            return_value=AdaptiveResearchResult(
                enabled=True,
                reason="research_task",
                strategy="verify",
                actions_taken=["initial_search", "verify_claim"],
                queries_run=["verify claim", "claim official source verify"],
                notes=[],
                evidence_strength="none",
                admitted_uncertainty=True,
                uncertainty_reason="No grounded live evidence came back for this question.",
            ),
        ):
            result = execute_tool_intent(
                {"intent": "web.research", "arguments": {"query": "verify a shaky claim"}},
                task_id="task-123",
                session_id="session-123",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertFalse(result.ok)
        self.assertIn("Uncertainty:", result.response_text)
        self.assertTrue(result.details["observation"]["admitted_uncertainty"])
        self.assertEqual(
            result.details["observation"]["uncertainty_reason"],
            "No grounded live evidence came back for this question.",
        )

    def test_explicit_social_lookup_triggers_tool_intent_gate(self) -> None:
        should_run = should_attempt_tool_intent(
            "check Toly on X",
            task_class="unknown",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(should_run)

    def test_workflow_planner_routes_tool_inventory_questions_to_operator_list(self) -> None:
        first = plan_tool_workflow(
            user_text="what tools do you need to complete this task",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_operator_tool_inventory")
        self.assertEqual(first.next_payload["intent"], "operator.list_tools")

    def test_workflow_planner_bootstraps_workspace_directory_for_start_coding_prompt(self) -> None:
        first = plan_tool_workflow(
            user_text="create a folder called tools and start putting code in there",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "tools")

    def test_workflow_planner_bootstraps_nested_workspace_directory_from_path_hint(self) -> None:
        first = plan_tool_workflow(
            user_text="create src/api and write the initial files",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "src/api")

    def test_workflow_planner_routes_safe_desktop_folder_listing_to_machine_read(self) -> None:
        first = plan_tool_workflow(
            user_text="tell me what are the folders on my desktop",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_safe_machine_directory_list")
        self.assertEqual(first.next_payload["intent"], "machine.list_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "~/Desktop")
        self.assertTrue(first.next_payload["arguments"]["directories_only"])

    def test_workflow_planner_routes_can_you_see_desktop_folders_to_machine_read(self) -> None:
        first = plan_tool_workflow(
            user_text="can you see folders on my desktop?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_safe_machine_directory_list")
        self.assertEqual(first.next_payload["intent"], "machine.list_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "~/Desktop")
        self.assertTrue(first.next_payload["arguments"]["directories_only"])

    def test_workflow_planner_routes_desktop_folders_and_files_to_machine_read_without_folder_only_mode(self) -> None:
        first = plan_tool_workflow(
            user_text="what are the folders and files on my desktop?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_safe_machine_directory_list")
        self.assertEqual(first.next_payload["intent"], "machine.list_directory")
        self.assertFalse(first.next_payload["arguments"]["directories_only"])

    def test_workflow_planner_routes_machine_specs_request_to_grounded_machine_tool(self) -> None:
        first = plan_tool_workflow(
            user_text="what machine are you running on? tell me our machine specs",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_machine_specs_inspection")
        self.assertEqual(first.next_payload["intent"], "machine.inspect_specs")

    def test_workflow_planner_routes_drive_count_to_machine_disk_usage(self) -> None:
        first = plan_tool_workflow(
            user_text="how many drives does my PC have",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "api", "platform": "api"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_machine_diagnostics")
        self.assertEqual(first.next_payload["intent"], "machine.disk_usage")

    def test_workflow_planner_routes_imprecise_machine_specs_request_to_grounded_machine_tool(self) -> None:
        first = plan_tool_workflow(
            user_text="what is machine you are running on?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_machine_specs_inspection")
        self.assertEqual(first.next_payload["intent"], "machine.inspect_specs")

    def test_workflow_planner_does_not_route_screen_size_to_machine_specs(self) -> None:
        # Regression: screen/display/resolution questions must NOT route to
        # machine.inspect_specs, which has no display data on Windows and would answer
        # with CPU/OS specs. They belong to the dedicated display capability (and until it
        # exists, to the honest-blocker gate) -- never a mislabeled specs result.
        first = plan_tool_workflow(
            user_text="you did not answered about screen size?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "api", "platform": "api"},
        )

        intent = str((first.next_payload or {}).get("intent") or "")
        self.assertNotEqual(intent, "machine.inspect_specs")

    def test_workflow_planner_does_not_route_capability_question_to_machine_specs(self) -> None:
        first = plan_tool_workflow(
            user_text="what can you do right now on this machine?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(first.handled)

    def test_workflow_planner_allows_workspace_write_under_repo_desktop_path(self) -> None:
        target = (Path.cwd() / "artifacts" / "planner-desktop-path").resolve()
        first = plan_tool_workflow(
            user_text=f"Create a file named vool_test_01.txt in {target} with exactly this content: ALPHA-LOCAL-FILE-01",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": str(Path.cwd())},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "artifacts/planner-desktop-path")

        second = plan_tool_workflow(
            user_text=f"Create a file named vool_test_01.txt in {target} with exactly this content: ALPHA-LOCAL-FILE-01",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "artifacts/planner-desktop-path"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "artifacts/planner-desktop-path",
                        "action": "confirmed",
                        "already_present": True,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": str(Path.cwd())},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "artifacts/planner-desktop-path/vool_test_01.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "ALPHA-LOCAL-FILE-01")

    def test_workflow_planner_routes_named_file_create_in_workspace_root_path(self) -> None:
        workspace = Path.cwd().resolve()
        first = plan_tool_workflow(
            user_text=f"Create a file named vool_test_01.txt in {workspace} with exactly this content: ALPHA-LOCAL-FILE-01",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "api", "platform": "api", "workspace": str(workspace)},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_write_file")
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "vool_test_01.txt")
        self.assertEqual(first.next_payload["arguments"]["content"], "ALPHA-LOCAL-FILE-01")

    def test_workflow_planner_routes_explicit_file_create_to_workspace_write(self) -> None:
        first = plan_tool_workflow(
            user_text="Create a file named vool_test_01.txt in the current workspace with exactly this content: ALPHA-LOCAL-FILE-01",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_write_file")
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "vool_test_01.txt")
        self.assertEqual(first.next_payload["arguments"]["content"], "ALPHA-LOCAL-FILE-01")

    def test_workflow_planner_routes_exact_file_create_with_literal_modifiers(self) -> None:
        first = plan_tool_workflow(
            user_text=(
                "Create a file named vool_test_02.txt in the current workspace "
                "with exactly this content, no extra characters and no newline: ALPHA-LOCAL-FILE-02"
            ),
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_write_file")
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "vool_test_02.txt")
        self.assertEqual(first.next_payload["arguments"]["content"], "ALPHA-LOCAL-FILE-02")

    def test_workflow_planner_routes_inside_absolute_workspace_subdir_file_create(self) -> None:
        prompt = "Inside /tmp/vool_local_tooling_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH"
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "alpha")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "alpha"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "alpha",
                        "action": "confirmed",
                        "already_present": True,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "alpha/hello.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "HELLO-LOCAL-TRUTH")

    def test_workflow_planner_preserves_exact_content_after_literal_modifiers_inside_workspace_subdir(self) -> None:
        prompt = (
            "Inside /tmp/vool_local_tooling_truth/alpha create hello.txt with exactly this content, "
            "no extra characters and no newline: HELLO-LOCAL-TRUTH"
        )
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "alpha")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "alpha"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "alpha",
                        "action": "confirmed",
                        "already_present": True,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "alpha/hello.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "HELLO-LOCAL-TRUTH")

    def test_workflow_planner_preserves_multiline_code_for_inside_absolute_workspace_subdir(self) -> None:
        prompt = (
            "Inside /tmp/vool_local_tooling_truth/alpha create adder.py with exactly this code:\n\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "alpha")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "alpha"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "alpha",
                        "action": "confirmed",
                        "already_present": True,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool_local_tooling_truth"},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "alpha/adder.py")
        self.assertEqual(
            second.next_payload["arguments"]["content"],
            "def add(a: int, b: int) -> int:\n    return a + b",
        )

    def test_workflow_planner_ignores_semantic_tokens_inside_workspace_paths(self) -> None:
        prompt = "Inside /tmp/openclaw_workspace_truth/alpha create hello.txt with exactly this content: HELLO-LOCAL-TRUTH"
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/openclaw_workspace_truth"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "alpha")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "alpha"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "alpha",
                        "action": "created",
                        "already_present": False,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/openclaw_workspace_truth"},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "alpha/hello.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "HELLO-LOCAL-TRUTH")

    def test_workflow_planner_continues_directory_bootstrap_into_file_writes(self) -> None:
        prompt = (
            "Create a folder named vool_chain_test. Inside it create notes.txt with the line first note. "
            "Then create summary.txt that says: notes.txt created successfully. Then list the folder contents."
        )
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "vool_chain_test")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "vool_chain_test"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "vool_chain_test",
                        "action": "created",
                        "already_present": False,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "vool_chain_test/notes.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "first note")

    def test_workflow_planner_reads_before_append_and_supports_exact_readback(self) -> None:
        append_prompt = "Append a second line to vool_test_01.txt: BETA-APPEND-02"
        first = plan_tool_workflow(
            user_text=append_prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_read_before_append")
        self.assertEqual(first.next_payload["intent"], "workspace.read_file")
        self.assertTrue(first.next_payload["arguments"]["verbatim"])

        second = plan_tool_workflow(
            user_text=append_prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "vool_test_01.txt", "start_line": 1, "max_lines": 400, "verbatim": True},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "vool_test_01.txt",
                        "start_line": 1,
                        "line_count": 1,
                        "lines": [{"line_number": 1, "text": "ALPHA-LOCAL-FILE-01"}],
                        "verbatim": True,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_append_after_read")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(
            second.next_payload["arguments"]["content"],
            "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02",
        )

        readback = plan_tool_workflow(
            user_text="Now read the whole file back exactly.",
            task_class="unknown",
            executed_steps=[],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "workspace": "/tmp/vool-acceptance",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "Create a file named vool_test_01.txt in the current workspace with exactly this content: ALPHA-LOCAL-FILE-01",
                    }
                ],
            },
        )
        self.assertTrue(readback.handled)
        self.assertEqual(readback.reason, "planned_workspace_readback")
        self.assertEqual(readback.next_payload["intent"], "workspace.read_file")
        self.assertTrue(readback.next_payload["arguments"]["verbatim"])

    def test_workflow_planner_recovers_last_path_for_append_without_explicit_target(self) -> None:
        decision = plan_tool_workflow(
            user_text="Append a second line: BETA-APPEND-02",
            task_class="unknown",
            executed_steps=[],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "workspace": "/tmp/vool-acceptance",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "Create a file named vool_test_01.txt in the current workspace with exactly this content: ALPHA-LOCAL-FILE-01",
                    }
                ],
            },
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_read_before_append")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "vool_test_01.txt")

    def test_workflow_planner_recovers_relative_readback_path_from_absolute_workspace_history(self) -> None:
        decision = plan_tool_workflow(
            user_text="Now read the whole file back exactly.",
            task_class="unknown",
            executed_steps=[],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "workspace": "/tmp/openclaw_workspace_truth_live",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": (
                            "Inside /tmp/openclaw_workspace_truth_live/alpha create adder.py with exactly this code:\n\n"
                            "def add(a: int, b: int) -> int:\n"
                            "    return a + b\n"
                        ),
                    }
                ],
            },
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_readback")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "alpha/adder.py")
        self.assertTrue(decision.next_payload["arguments"]["verbatim"])

    def test_workflow_planner_handles_folder_first_workspace_chain_prompts(self) -> None:
        prompt = "make folder march_shift_folder_f0dc here and put weekly_notes_file_0252.txt inside with exact text: alpha line 3525eb"
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-procedural"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_directory_bootstrap")
        self.assertEqual(first.next_payload["intent"], "workspace.ensure_directory")
        self.assertEqual(first.next_payload["arguments"]["path"], "march_shift_folder_f0dc")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.ensure_directory",
                    "arguments": {"path": "march_shift_folder_f0dc"},
                    "observation": {
                        "intent": "workspace.ensure_directory",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "march_shift_folder_f0dc",
                        "action": "created",
                        "already_present": False,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-procedural"},
        )

        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_write_after_bootstrap")
        self.assertEqual(second.next_payload["intent"], "workspace.write_file")
        self.assertEqual(second.next_payload["arguments"]["path"], "march_shift_folder_f0dc/weekly_notes_file_0252.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "alpha line 3525eb")

    def test_workflow_planner_recovers_folder_first_history_for_readback_and_append(self) -> None:
        history_prompt = "make folder march_shift_folder_f0dc here and put weekly_notes_file_0252.txt inside with exact text: alpha line 3525eb"
        source_context = {
            "surface": "openclaw",
            "platform": "openclaw",
            "workspace": "/tmp/vool-procedural",
            "conversation_history": [{"role": "user", "content": history_prompt}],
        }

        readback = plan_tool_workflow(
            user_text="read it back exactly first",
            task_class="unknown",
            executed_steps=[],
            source_context=source_context,
        )

        self.assertTrue(readback.handled)
        self.assertEqual(readback.reason, "planned_workspace_readback")
        self.assertEqual(readback.next_payload["intent"], "workspace.read_file")
        self.assertEqual(readback.next_payload["arguments"]["path"], "march_shift_folder_f0dc/weekly_notes_file_0252.txt")
        self.assertTrue(readback.next_payload["arguments"]["verbatim"])

        append = plan_tool_workflow(
            user_text="now add one more line exactly: beta line 882",
            task_class="unknown",
            executed_steps=[],
            source_context=source_context,
        )

        self.assertTrue(append.handled)
        self.assertEqual(append.reason, "planned_read_before_append")
        self.assertEqual(append.next_payload["intent"], "workspace.read_file")
        self.assertEqual(append.next_payload["arguments"]["path"], "march_shift_folder_f0dc/weekly_notes_file_0252.txt")

    def test_workflow_planner_supports_inside_workspace_create_and_explicit_exact_read(self) -> None:
        write = plan_tool_workflow(
            user_text="fine then. inside this workspace create recovered_fix_4d82.txt with exact text: recovery succeeded 3525eb",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-procedural"},
        )

        self.assertTrue(write.handled)
        self.assertEqual(write.reason, "planned_workspace_write_file")
        self.assertEqual(write.next_payload["intent"], "workspace.write_file")
        self.assertEqual(write.next_payload["arguments"]["path"], "recovered_fix_4d82.txt")
        self.assertEqual(write.next_payload["arguments"]["content"], "recovery succeeded 3525eb")

        read = plan_tool_workflow(
            user_text="now read recovered_fix_4d82.txt exactly",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-procedural"},
        )

        self.assertTrue(read.handled)
        self.assertEqual(read.reason, "planned_workspace_readback")
        self.assertEqual(read.next_payload["intent"], "workspace.read_file")
        self.assertEqual(read.next_payload["arguments"]["path"], "recovered_fix_4d82.txt")
        self.assertTrue(read.next_payload["arguments"]["verbatim"])

    def test_should_attempt_tool_intent_for_live_recency_lookup(self) -> None:
        should_run = should_attempt_tool_intent(
            "What happened five minutes ago in global markets?",
            task_class="research",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(should_run)

    def test_workflow_planner_sequences_exact_multi_file_write_request(self) -> None:
        prompt = "Create exactly three files: a.txt, b.txt, c.txt. Put ONE, TWO, THREE respectively. Do not create anything else."
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "a.txt")

        second = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "a.txt", "content": "ONE"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "a.txt",
                        "line_count": 1,
                        "action": "created",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_workspace_next_write")
        self.assertEqual(second.next_payload["arguments"]["path"], "b.txt")
        self.assertEqual(second.next_payload["arguments"]["content"], "TWO")

    def test_workflow_planner_handles_create_file_without_content_colon(self) -> None:
        prompt = "Create file consistency_test. txt with content CONSISTENCY-CHECK"
        first = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_workspace_write_file")
        self.assertEqual(first.next_payload["intent"], "workspace.write_file")
        self.assertEqual(first.next_payload["arguments"]["path"], "consistency_test.txt")
        self.assertEqual(first.next_payload["arguments"]["content"], "CONSISTENCY-CHECK")

    def test_workflow_planner_lists_directory_after_explicit_chain_writes(self) -> None:
        prompt = (
            "Create a folder named vool_chain_test. Inside it create notes.txt with the line first note. "
            "Then create summary.txt that says: notes.txt created successfully. Then list the folder contents."
        )
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "vool_chain_test/notes.txt", "content": "first note"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "vool_chain_test/notes.txt",
                        "line_count": 1,
                        "action": "created",
                    },
                },
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "vool_chain_test/summary.txt", "content": "notes.txt created successfully"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "vool_chain_test/summary.txt",
                        "line_count": 1,
                        "action": "created",
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_list_after_write")
        self.assertEqual(decision.next_payload["intent"], "workspace.list_files")
        self.assertEqual(decision.next_payload["arguments"]["path"], "vool_chain_test")

    def test_workflow_planner_treats_explicit_social_lookup_as_research(self) -> None:
        first = plan_tool_workflow(
            user_text="check Toly on X",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_entity_lookup_search")
        self.assertEqual(first.next_payload["intent"], "web.search")
        self.assertEqual(first.next_payload["arguments"]["query"], "toly x")

    def test_workflow_planner_compacts_public_entity_lookup_before_first_search(self) -> None:
        first = plan_tool_workflow(
            user_text="who is Toly in Solana?",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(first.handled)
        self.assertEqual(first.reason, "planned_entity_lookup_search")
        self.assertEqual(first.next_payload["intent"], "web.search")
        self.assertEqual(first.next_payload["arguments"]["query"], "toly solana")

    def test_workflow_planner_retries_misspelled_public_entity_lookup_and_then_escalates(self) -> None:
        first = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertEqual(first.next_payload["arguments"]["query"], "tolly x solana")

        second = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "tolly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "tolly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.reason, "planned_entity_lookup_retry")
        self.assertEqual(second.next_payload["intent"], "web.search")
        self.assertEqual(second.next_payload["arguments"]["query"], "toly x solana")

        third = plan_tool_workflow(
            user_text="Tolly on X in Solana who is he",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "tolly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "tolly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                },
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "toly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "toly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.reason, "planned_entity_lookup_research")
        self.assertEqual(third.next_payload["intent"], "web.research")
        self.assertEqual(third.next_payload["arguments"]["query"], "toly x solana")

    def test_workflow_planner_can_chain_research_steps(self) -> None:
        first = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "web.search")

        second = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 2,
                        "results": [
                            {"url": "https://supabase.com/docs", "origin_domain": "supabase.com"},
                            {"url": "https://firebase.google.com/docs", "origin_domain": "firebase.google.com"},
                        ],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.next_payload["intent"], "web.fetch")

        third = plan_tool_workflow(
            user_text="compare supabase vs firebase for a telegram bot backend",
            task_class="system_design",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "compare supabase vs firebase for a telegram bot backend"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 2,
                        "results": [
                            {"url": "https://supabase.com/docs", "origin_domain": "supabase.com"},
                            {"url": "https://firebase.google.com/docs", "origin_domain": "firebase.google.com"},
                        ],
                    },
                },
                {
                    "tool_name": "web.fetch",
                    "arguments": {"url": "https://supabase.com/docs"},
                    "observation": {
                        "intent": "web.fetch",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "url": "https://supabase.com/docs",
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.next_payload["intent"], "web.research")

    def test_workflow_planner_can_drive_diagnose_run_inspect_retry(self) -> None:
        first = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(first.handled)
        self.assertEqual(first.next_payload["intent"], "sandbox.run_command")

        second = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(second.handled)
        self.assertEqual(second.next_payload["intent"], "workspace.read_file")

        third = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(third.handled)
        self.assertEqual(third.next_payload["intent"], "workspace.replace_in_file")

        fourth = plan_tool_workflow(
            user_text="run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "sandbox.run_command",
                    "arguments": {"command": "python3 app.py"},
                    "observation": {
                        "intent": "sandbox.run_command",
                        "tool_surface": "sandbox",
                        "ok": True,
                        "status": "executed",
                        "command": "python3 app.py",
                        "returncode": 1,
                        "stdout": "app.py:1 TODO marker still present",
                        "stderr": "",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                    },
                },
                {
                    "tool_name": "workspace.replace_in_file",
                    "arguments": {"path": "app.py", "old_text": "TODO", "new_text": "DONE"},
                    "observation": {
                        "intent": "workspace.replace_in_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "replacements": 1,
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(fourth.handled)
        self.assertEqual(fourth.next_payload["intent"], "sandbox.run_command")

    def test_workflow_planner_routes_git_status_requests_into_runtime_git_status(self) -> None:
        decision = plan_tool_workflow(
            user_text="git status for this repo",
            task_class="file_inspection",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_git_status")
        self.assertEqual(decision.next_payload["intent"], "workspace.git_status")

    def test_workflow_planner_routes_git_activity_requests_into_runtime_git_summary(self) -> None:
        decision = plan_tool_workflow(
            user_text="check this git repo and tell me how many branches there are, how many commits happened today, and how many happened yesterday. Use tools and be exact.",
            task_class="file_inspection",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_git_summary")
        self.assertEqual(decision.next_payload["intent"], "workspace.git_summary")

    def test_workflow_planner_routes_branch_and_commit_prompt_into_runtime_git_summary(self) -> None:
        decision = plan_tool_workflow(
            user_text="what branch and commit are you running on right now?",
            task_class="file_inspection",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_git_summary")
        self.assertEqual(decision.next_payload["intent"], "workspace.git_summary")

    def test_workflow_planner_routes_last_n_commits_prompt_into_runtime_git_summary(self) -> None:
        decision = plan_tool_workflow(
            user_text="list the last 3 commits in this repo",
            task_class="file_inspection",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_git_summary")
        self.assertEqual(decision.next_payload["intent"], "workspace.git_summary")
        self.assertEqual(decision.next_payload["arguments"]["recent_limit"], 3)

    def test_workflow_planner_routes_workspace_search_prompt_into_search_text(self) -> None:
        decision = plan_tool_workflow(
            user_text='find a file in this workspace mentioning "runtime_capabilities"',
            task_class="file_inspection",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_workspace_search_text")
        self.assertEqual(decision.next_payload["intent"], "workspace.search_text")
        self.assertEqual(decision.next_payload["arguments"]["query"], "runtime_capabilities")

    def test_workflow_planner_stops_after_git_inspection(self) -> None:
        decision = plan_tool_workflow(
            user_text="check this git repo and tell me how many branches there are, how many commits happened today, and how many happened yesterday. Use tools and be exact.",
            task_class="file_inspection",
            executed_steps=[
                {
                    "tool_name": "workspace.git_summary",
                    "arguments": {},
                    "observation": {
                        "intent": "workspace.git_summary",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "local_branch_count": 12,
                        "remote_branch_count": 4,
                        "total_branch_count": 16,
                        "today_date": "2026-03-30",
                        "today_commit_count": 10,
                        "yesterday_date": "2026-03-29",
                        "yesterday_commit_count": 10,
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "workspace_stop_after_git_inspection")

    def test_workflow_planner_prefers_validation_intent_for_explicit_pytest_command(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_validation_run")
        self.assertEqual(decision.next_payload["intent"], "workspace.run_tests")
        self.assertEqual(decision.next_payload["arguments"]["command"], "python3 -m pytest -q test_app.py")

    def test_workflow_planner_can_inspect_after_failed_validation_run(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "app.py",
                        "error_line": 2,
                        "diagnostic_query": "AssertionError: assert 41 == 42",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_inspect_after_validation_failure")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "app.py")

    def test_workflow_planner_can_search_after_failed_validation_without_path(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "diagnostic_query": "AssertionError: answer() == 42",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_symbol_search_after_validation_failure")
        self.assertEqual(decision.next_payload["intent"], "workspace.symbol_search")
        self.assertEqual(decision.next_payload["arguments"]["symbol"], "answer")

    def test_workflow_planner_can_continue_from_validation_inspection_into_diagnostic_search(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "AssertionError: answer() == 42",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_symbol_search_after_validation_inspection")
        self.assertEqual(decision.next_payload["intent"], "workspace.symbol_search")
        self.assertEqual(decision.next_payload["arguments"]["symbol"], "answer")

    def test_workflow_planner_reads_first_unseen_match_after_validation_symbol_search(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_read_after_symbol_search")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "app.py")
        self.assertEqual(decision.next_payload["arguments"]["start_line"], 1)

    def test_workflow_planner_falls_back_to_text_search_after_empty_symbol_search(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "no_results",
                        "symbol": "answer",
                        "match_count": 0,
                        "matches": [],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_search_after_symbol_search")
        self.assertEqual(decision.next_payload["intent"], "workspace.search_text")
        self.assertEqual(decision.next_payload["arguments"]["query"], "answer(")

    def test_workflow_planner_can_emit_candidate_repair_after_validation_diagnosis(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 2,
                        "lines": [
                            {"line_number": 1, "text": "def answer():"},
                            {"line_number": 2, "text": "    return 41"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
        self.assertEqual([item["role"] for item in subtasks], ["verifier", "coder", "verifier"])
        coder_steps = list(subtasks[1]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[0]["intent"], "workspace.read_file")
        self.assertEqual(coder_steps[1]["intent"], "workspace.replace_in_file")
        self.assertEqual(coder_steps[1]["arguments"]["path"], "app.py")
        self.assertEqual(coder_steps[1]["arguments"]["old_text"], "return 41")
        self.assertEqual(coder_steps[1]["arguments"]["new_text"], "return 42")

    def test_workflow_planner_can_emit_candidate_repair_for_same_file_literal_binding(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "ANSWER = 41"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return ANSWER"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
        coder_steps = list(subtasks[1]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[1]["intent"], "workspace.replace_in_file")
        self.assertEqual(coder_steps[1]["arguments"]["path"], "app.py")
        self.assertEqual(coder_steps[1]["arguments"]["old_text"], "ANSWER = 41")
        self.assertEqual(coder_steps[1]["arguments"]["new_text"], "ANSWER = 42")

    def test_workflow_planner_does_not_emit_binding_repair_when_same_name_is_ambiguous(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 4, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 5,
                        "lines": [
                            {"line_number": 1, "text": "ANSWER = 41"},
                            {"line_number": 2, "text": "ANSWER = 40"},
                            {"line_number": 4, "text": "def answer():"},
                            {"line_number": 5, "text": "    return ANSWER"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertNotEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertNotEqual(decision.next_payload["intent"], "orchestration.execute_envelope")

    def test_workflow_planner_reads_next_unread_symbol_match_before_rerunning_validation(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 3,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                            {"path": "helpers.py", "line": 1, "kind": "function_definition", "snippet": "def answer_value():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from helpers import answer_value"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return answer_value()"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_next_read_after_symbol_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "helpers.py")

    def test_workflow_planner_can_emit_candidate_repair_after_delegated_helper_read(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 3,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                            {"path": "helpers.py", "line": 1, "kind": "function_definition", "snippet": "def answer_value():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from helpers import answer_value"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return answer_value()"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "helpers.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "helpers.py",
                        "start_line": 1,
                        "line_count": 2,
                        "lines": [
                            {"line_number": 1, "text": "def answer_value():"},
                            {"line_number": 2, "text": "    return 41"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
        coder_steps = list(subtasks[1]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[1]["intent"], "workspace.replace_in_file")
        self.assertEqual(coder_steps[1]["arguments"]["path"], "helpers.py")
        self.assertEqual(coder_steps[1]["arguments"]["old_text"], "return 41")
        self.assertEqual(coder_steps[1]["arguments"]["new_text"], "return 42")

    def test_workflow_planner_can_lookup_delegate_after_wrapper_read(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from helpers import answer_value"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return answer_value()"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_symbol_search_after_delegate_inspection")
        self.assertEqual(decision.next_payload["intent"], "workspace.symbol_search")
        self.assertEqual(decision.next_payload["arguments"]["symbol"], "answer_value")

    def test_workflow_planner_can_emit_candidate_repair_after_imported_literal_binding_read(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 3,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 3, "kind": "function_definition", "snippet": "def answer():"},
                            {"path": "helpers.py", "line": 1, "kind": "reference", "snippet": "ANSWER = 41"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from helpers import ANSWER"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return ANSWER"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "helpers.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "helpers.py",
                        "start_line": 1,
                        "line_count": 1,
                        "lines": [
                            {"line_number": 1, "text": "ANSWER = 41"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        subtasks = list(decision.next_payload["arguments"]["task_envelope"]["inputs"]["subtasks"])
        coder_steps = list(subtasks[1]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[1]["intent"], "workspace.replace_in_file")
        self.assertEqual(coder_steps[1]["arguments"]["path"], "helpers.py")
        self.assertEqual(coder_steps[1]["arguments"]["old_text"], "ANSWER = 41")
        self.assertEqual(coder_steps[1]["arguments"]["new_text"], "ANSWER = 42")

    def test_workflow_planner_reads_next_unread_search_match_before_rerunning_validation(self) -> None:
        decision = plan_tool_workflow(
            user_text="run `python3 -m pytest -q test_app.py` and fix the failing tests",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "workspace.run_tests",
                    "arguments": {"command": "python3 -m pytest -q test_app.py"},
                    "observation": {
                        "intent": "workspace.run_tests",
                        "tool_surface": "workspace",
                        "ok": False,
                        "status": "executed",
                        "command": "python3 -m pytest -q test_app.py",
                        "cwd": ".",
                        "returncode": 1,
                        "success": False,
                        "error_path": "test_app.py",
                        "error_line": 4,
                        "diagnostic_query": "answer(",
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "no_results",
                        "symbol": "answer",
                        "match_count": 0,
                        "matches": [],
                    },
                },
                {
                    "tool_name": "workspace.search_text",
                    "arguments": {"query": "answer(", "limit": 10},
                    "observation": {
                        "intent": "workspace.search_text",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "query": "answer(",
                        "match_count": 3,
                        "matches": [
                            {"path": "test_app.py", "line": 4, "snippet": "assert answer() == 42"},
                            {"path": "app.py", "line": 3, "snippet": "def answer():"},
                            {"path": "helpers.py", "line": 1, "snippet": "def answer_value():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from helpers import answer_value"},
                            {"line_number": 3, "text": "def answer():"},
                            {"line_number": 4, "text": "    return answer_value()"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_next_read_after_search_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "helpers.py")

    def test_workflow_planner_can_emit_orchestrated_operator_envelope_for_patch_and_validate(self) -> None:
        prompt = "replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`"
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class=classify(prompt)["task_class"],
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        self.assertEqual(envelope["role"], "queen")
        subtasks = list(envelope["inputs"]["subtasks"])
        self.assertEqual([item["role"] for item in subtasks], ["coder", "verifier"])
        self.assertEqual(subtasks[0]["inputs"]["runtime_tools"][1]["intent"], "workspace.replace_in_file")
        self.assertEqual(subtasks[1]["inputs"]["runtime_tools"][0]["intent"], "workspace.run_tests")
        self.assertEqual(subtasks[1]["inputs"]["depends_on"], [subtasks[0]["task_id"]])

    def test_workflow_planner_can_emit_search_then_patch_envelope_when_path_is_missing(self) -> None:
        prompt = "replace `return 41` with `return 42`, then run `python3 -m pytest -q test_app.py`"
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class=classify(prompt)["task_class"],
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        subtasks = list(envelope["inputs"]["subtasks"])
        coder_steps = list(subtasks[0]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[0]["intent"], "workspace.search_text")
        self.assertEqual(coder_steps[0]["step_id"], "locate-replacement-target")
        self.assertEqual(coder_steps[1]["arguments"]["path"]["$from_step"], "locate-replacement-target")
        self.assertTrue(coder_steps[1]["arguments"]["path"]["$require_single_match"])
        self.assertEqual(coder_steps[2]["intent"], "workspace.replace_in_file")

    def test_workflow_planner_can_emit_preflight_failing_test_repair_envelope(self) -> None:
        prompt = (
            "tests are failing. replace `return 41` with `return 42` in app.py, "
            "then run `python3 -m pytest -q test_app.py`"
        )
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class=classify(prompt)["task_class"],
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        subtasks = list(envelope["inputs"]["subtasks"])
        self.assertEqual([item["role"] for item in subtasks], ["verifier", "coder", "verifier"])
        preflight = subtasks[0]
        coder = subtasks[1]
        postverify = subtasks[2]
        self.assertTrue(preflight["inputs"]["runtime_tools"][0]["allow_failure"])
        self.assertEqual(preflight["inputs"]["runtime_tools"][0]["intent"], "workspace.run_tests")
        self.assertEqual(coder["inputs"]["depends_on"], [preflight["task_id"]])
        self.assertEqual(postverify["inputs"]["depends_on"], [coder["task_id"]])
        self.assertTrue(postverify["inputs"]["rollback_on_failure"])

    def test_workflow_planner_can_emit_unified_diff_envelope(self) -> None:
        prompt = (
            "apply this patch, then run `python3 -m pytest -q test_app.py`\n"
            "```diff\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def answer():\n"
            "-    return 41\n"
            "+    return 42\n"
            "```\n"
        )
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class=classify(prompt)["task_class"],
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        subtasks = list(envelope["inputs"]["subtasks"])
        self.assertEqual([item["role"] for item in subtasks], ["coder", "verifier"])
        coder_steps = list(subtasks[0]["inputs"]["runtime_tools"])
        self.assertEqual(coder_steps[0]["intent"], "workspace.apply_unified_diff")
        self.assertIn("--- a/app.py", coder_steps[0]["arguments"]["patch"])

    def test_workflow_planner_backstops_patch_and_validate_when_task_class_is_stale(self) -> None:
        prompt = "replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`"

        decision = plan_tool_workflow(
            user_text=prompt,
            task_class="shell_guidance",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        self.assertEqual(envelope["inputs"]["task_class"], "debugging")

    def test_workflow_planner_can_emit_preflight_unified_diff_repair_envelope(self) -> None:
        prompt = (
            "tests are failing. apply this patch, then run `python3 -m pytest -q test_app.py`\n"
            "```diff\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,2 +1,2 @@\n"
            " def answer():\n"
            "-    return 41\n"
            "+    return 42\n"
            "```\n"
        )
        decision = plan_tool_workflow(
            user_text=prompt,
            task_class=classify(prompt)["task_class"],
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_orchestrated_operator_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        subtasks = list(envelope["inputs"]["subtasks"])
        self.assertEqual([item["role"] for item in subtasks], ["verifier", "coder", "verifier"])
        self.assertTrue(subtasks[0]["inputs"]["runtime_tools"][0]["allow_failure"])
        self.assertEqual(subtasks[1]["inputs"]["runtime_tools"][0]["intent"], "workspace.apply_unified_diff")

    def test_workflow_planner_stops_after_successful_orchestrated_operator_envelope(self) -> None:
        decision = plan_tool_workflow(
            user_text="replace `return 41` with `return 42` in app.py, then run `python3 -m pytest -q test_app.py`",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "orchestration.execute_envelope",
                    "arguments": {"task_envelope": {"task_id": "queen-1"}},
                    "observation": {
                        "intent": "orchestration.execute_envelope",
                        "tool_surface": "orchestration",
                        "ok": True,
                        "status": "completed",
                        "task_id": "queen-1",
                        "task_role": "queen",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "orchestration_stop_after_envelope")

    def test_workflow_planner_continues_after_failed_orchestrated_operator_envelope_with_rollback(self) -> None:
        decision = plan_tool_workflow(
            user_text="replace `return 41` with `return 40` in app.py, then run `python3 -m pytest -q test_app.py`",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "orchestration.execute_envelope",
                    "arguments": {"task_envelope": {"task_id": "queen-rollback"}},
                    "observation": {
                        "intent": "orchestration.execute_envelope",
                        "tool_surface": "orchestration",
                        "ok": False,
                        "status": "merge_failed",
                        "task_id": "queen-rollback",
                        "task_role": "queen",
                    },
                    "details": {
                        "merged_result": {
                            "winner": {
                                "task_id": "verify-final",
                                "role": "verifier",
                                "ok": False,
                                "status": "executed",
                                "details": {
                                    "failure_rollback": {
                                        "intent": "workspace.rollback_last_change",
                                        "ok": True,
                                        "status": "executed",
                                    },
                                    "step_results": [
                                        {
                                            "step_id": "verify-final",
                                            "intent": "workspace.run_tests",
                                            "ok": False,
                                            "status": "executed",
                                            "details": {
                                                "observation": {
                                                    "intent": "workspace.run_tests",
                                                    "tool_surface": "workspace",
                                                    "ok": False,
                                                    "status": "executed",
                                                    "command": "python3 -m pytest -q test_app.py",
                                                    "returncode": 1,
                                                    "error_path": "test_app.py",
                                                    "error_line": 4,
                                                    "diagnostic_query": "answer",
                                                }
                                            },
                                        }
                                    ],
                                },
                            }
                        }
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertFalse(decision.stop_after)
        self.assertEqual(decision.reason, "planned_inspect_after_envelope_failure")
        self.assertEqual(decision.next_payload["intent"], "workspace.read_file")
        self.assertEqual(decision.next_payload["arguments"]["path"], "test_app.py")

    def test_workflow_planner_stops_after_failed_orchestrated_operator_envelope_without_rollback(self) -> None:
        decision = plan_tool_workflow(
            user_text="replace `return 41` with `return 40` in app.py, then run `python3 -m pytest -q test_app.py`",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "orchestration.execute_envelope",
                    "arguments": {"task_envelope": {"task_id": "queen-no-rollback"}},
                    "observation": {
                        "intent": "orchestration.execute_envelope",
                        "tool_surface": "orchestration",
                        "ok": False,
                        "status": "merge_failed",
                        "task_id": "queen-no-rollback",
                        "task_role": "queen",
                    },
                    "details": {
                        "merged_result": {
                            "winner": {
                                "task_id": "verify-final",
                                "role": "verifier",
                                "ok": False,
                                "status": "executed",
                                "details": {
                                    "step_results": [
                                        {
                                            "step_id": "verify-final",
                                            "intent": "workspace.run_tests",
                                            "ok": False,
                                            "status": "executed",
                                            "details": {
                                                "observation": {
                                                    "intent": "workspace.run_tests",
                                                    "tool_surface": "workspace",
                                                    "ok": False,
                                                    "status": "executed",
                                                    "command": "python3 -m pytest -q test_app.py",
                                                    "returncode": 1,
                                                    "error_path": "test_app.py",
                                                    "error_line": 4,
                                                    "diagnostic_query": "answer",
                                                }
                                            },
                                        }
                                    ],
                                },
                            }
                        }
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "orchestration_stop_after_envelope")

    def test_workflow_planner_can_retry_candidate_repair_after_failed_envelope_rollback(self) -> None:
        decision = plan_tool_workflow(
            user_text="tests are failing. replace `return 41` with `return 40` in app.py, then run `python3 -m pytest -q test_app.py`",
            task_class="debugging",
            executed_steps=[
                {
                    "tool_name": "orchestration.execute_envelope",
                    "arguments": {"task_envelope": {"task_id": "queen-rollback"}},
                    "observation": {
                        "intent": "orchestration.execute_envelope",
                        "tool_surface": "orchestration",
                        "ok": False,
                        "status": "merge_failed",
                        "task_id": "queen-rollback",
                        "task_role": "queen",
                    },
                    "details": {
                        "merged_result": {
                            "winner": {
                                "task_id": "verify-final",
                                "role": "verifier",
                                "ok": False,
                                "status": "executed",
                                "details": {
                                    "failure_rollback": {
                                        "intent": "workspace.rollback_last_change",
                                        "ok": True,
                                        "status": "executed",
                                    },
                                    "step_results": [
                                        {
                                            "step_id": "verify-final",
                                            "intent": "workspace.run_tests",
                                            "ok": False,
                                            "status": "executed",
                                            "details": {
                                                "observation": {
                                                    "intent": "workspace.run_tests",
                                                    "tool_surface": "workspace",
                                                    "ok": False,
                                                    "status": "executed",
                                                    "command": "python3 -m pytest -q test_app.py",
                                                    "returncode": 1,
                                                    "error_path": "test_app.py",
                                                    "error_line": 4,
                                                    "diagnostic_query": "answer",
                                                }
                                            },
                                        }
                                    ],
                                },
                            }
                        }
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "test_app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "test_app.py",
                        "start_line": 1,
                        "line_count": 4,
                        "lines": [
                            {"line_number": 1, "text": "from app import answer"},
                            {"line_number": 4, "text": "    assert answer() == 42"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.symbol_search",
                    "arguments": {"symbol": "answer", "limit": 10},
                    "observation": {
                        "intent": "workspace.symbol_search",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "symbol": "answer",
                        "match_count": 2,
                        "matches": [
                            {"path": "test_app.py", "line": 1, "kind": "reference", "snippet": "from app import answer"},
                            {"path": "app.py", "line": 1, "kind": "function_definition", "snippet": "def answer():"},
                        ],
                    },
                },
                {
                    "tool_name": "workspace.read_file",
                    "arguments": {"path": "app.py", "start_line": 1, "max_lines": 60},
                    "observation": {
                        "intent": "workspace.read_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "app.py",
                        "start_line": 1,
                        "line_count": 2,
                        "lines": [
                            {"line_number": 1, "text": "def answer():"},
                            {"line_number": 2, "text": "    return 41"},
                        ],
                    },
                },
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-acceptance"},
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_candidate_repair_after_validation_diagnosis")
        self.assertEqual(decision.next_payload["intent"], "orchestration.execute_envelope")
        envelope = dict(decision.next_payload["arguments"]["task_envelope"])
        coder = next(item for item in envelope["inputs"]["subtasks"] if item["role"] == "coder")
        replace_step = next(
            item for item in coder["inputs"]["runtime_tools"] if item["intent"] == "workspace.replace_in_file"
        )
        self.assertEqual(replace_step["arguments"]["old_text"], "return 41")
        self.assertEqual(replace_step["arguments"]["new_text"], "return 42")

    def test_workflow_planner_can_stop_early_when_enough_state_is_gathered(self) -> None:
        decision = plan_tool_workflow(
            user_text="latest qwen release notes",
            task_class="research",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "latest qwen release notes"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "executed",
                        "result_count": 3,
                        "results": [
                            {"url": "https://example.test/qwen-1", "origin_domain": "example.test"},
                            {"url": "https://example.test/qwen-2", "origin_domain": "example-two.test"},
                            {"url": "https://example.test/qwen-3", "origin_domain": "example-three.test"},
                        ],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "research_enough_after_search")

    def test_workflow_planner_can_continue_or_stop_after_workspace_write(self) -> None:
        run_decision = plan_tool_workflow(
            user_text="build a telegram bot in the workspace and then run `python3 -m compileall -q generated/telegram-bot/src`",
            task_class="integration_orchestration",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "generated/telegram-bot/src/bot.py"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "generated/telegram-bot/src/bot.py",
                        "line_count": 42,
                        "action": "created",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(run_decision.handled)
        self.assertEqual(run_decision.next_payload["intent"], "sandbox.run_command")

        stop_decision = plan_tool_workflow(
            user_text="build a telegram bot in the workspace and write the files",
            task_class="integration_orchestration",
            executed_steps=[
                {
                    "tool_name": "workspace.write_file",
                    "arguments": {"path": "generated/telegram-bot/src/bot.py"},
                    "observation": {
                        "intent": "workspace.write_file",
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "path": "generated/telegram-bot/src/bot.py",
                        "line_count": 42,
                        "action": "created",
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertTrue(stop_decision.handled)
        self.assertTrue(stop_decision.stop_after)
        self.assertEqual(stop_decision.reason, "workspace_stop_after_write")

    def test_workflow_planner_does_not_invent_placeholder_hive_task_for_create_these_tasks(self) -> None:
        decision = plan_tool_workflow(
            user_text="create these tasks on Hive",
            task_class="research",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )
        self.assertFalse(decision.handled)
        self.assertEqual(decision.reason, "no_workflow_plan")

    def test_workflow_planner_recovers_hive_create_title_from_recent_history(self) -> None:
        decision = plan_tool_workflow(
            user_text="proceed with creating task",
            task_class="research",
            executed_steps=[],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "lets create new task in hive: Better watcher task UX",
                    }
                ],
            },
        )
        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_hive_create_topic")
        self.assertEqual(decision.next_payload["intent"], "hive.create_topic")
        self.assertEqual(decision.next_payload["arguments"]["title"], "Better watcher task UX")

    def test_should_attempt_tool_intent_for_proceed_followup(self) -> None:
        self.assertTrue(should_attempt_tool_intent("proceed with next steps", task_class="research"))
        self.assertTrue(should_attempt_tool_intent("do all and start working", task_class="research"))

    def test_workflow_planner_recovers_lookup_from_recent_history_for_generic_proceed(self) -> None:
        decision = plan_tool_workflow(
            user_text="proceed",
            task_class="unknown",
            executed_steps=[],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "who is Toly in Solana?",
                    }
                ],
            },
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_entity_lookup_search")
        self.assertEqual(decision.next_payload["intent"], "web.search")
        self.assertEqual(decision.next_payload["arguments"]["query"], "toly solana")

    def test_workflow_planner_generic_proceed_without_recoverable_context_does_not_invent_hive_create(self) -> None:
        decision = plan_tool_workflow(
            user_text="do it",
            task_class="unknown",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(decision.handled)
        self.assertEqual(decision.reason, "no_workflow_plan")

    def test_workflow_planner_generic_proceed_after_failed_entity_lookup_uses_retry_query(self) -> None:
        decision = plan_tool_workflow(
            user_text="do it",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "web.search",
                    "arguments": {"query": "tolly x solana"},
                    "observation": {
                        "intent": "web.search",
                        "tool_surface": "web",
                        "ok": True,
                        "status": "no_results",
                        "query": "tolly x solana",
                        "result_count": 0,
                        "results": [],
                    },
                }
            ],
            source_context={
                "surface": "openclaw",
                "platform": "openclaw",
                "conversation_history": [
                    {
                        "role": "user",
                        "content": "Tolly on X in Solana who is he",
                    }
                ],
            },
        )

        self.assertTrue(decision.handled)
        self.assertEqual(decision.reason, "planned_entity_lookup_retry")
        self.assertEqual(decision.next_payload["intent"], "web.search")
        self.assertEqual(decision.next_payload["arguments"]["query"], "toly x solana")

    def test_workflow_planner_stops_after_safe_machine_directory_list(self) -> None:
        decision = plan_tool_workflow(
            user_text="tell me what are the folders on my desktop",
            task_class="unknown",
            executed_steps=[
                {
                    "tool_name": "machine.list_directory",
                    "arguments": {"path": "~/Desktop", "directories_only": True},
                    "observation": {
                        "intent": "machine.list_directory",
                        "tool_surface": "machine",
                        "ok": True,
                        "status": "executed",
                        "path": "~/Desktop",
                        "count": 2,
                        "directories_only": True,
                        "entries": [
                            {"name": "Projects", "path": "~/Desktop/Projects", "type": "directory"},
                            {"name": "Ideas", "path": "~/Desktop/Ideas", "type": "directory"},
                        ],
                    },
                }
            ],
            source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-tools"},
        )

        self.assertTrue(decision.handled)
        self.assertTrue(decision.stop_after)
        self.assertEqual(decision.reason, "machine_stop_after_list")

    def test_workflow_planner_does_not_invent_nonexistent_email_tool(self) -> None:
        decision = plan_tool_workflow(
            user_text="send an email to ops with the incident summary",
            task_class="integration_orchestration",
            executed_steps=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

        self.assertFalse(decision.handled)
        self.assertEqual(decision.reason, "no_workflow_plan")


if __name__ == "__main__":
    unittest.main()


def test_operator_reports_preserve_real_previews_and_failures():
    from core.execution.operator_tools import execute_operator_tool

    for intent, status, ok, expected in (
        ("operator.inspect_services", "reported", False, "tool_failed"),
        ("operator.inspect_services", "approval_required", False, "tool_preview"),
        ("operator.cleanup_temp_files", "reported", True, "tool_preview"),
        ("operator.cleanup_temp_files", "approval_required", False, "tool_preview"),
    ):
        dispatch = SimpleNamespace(status=status, ok=ok, response_text="fixture", details={}, learned_plan=None)
        result = execute_operator_tool(
            intent, {}, task_id="mode-evidence", session_id="operator-report",
            dispatch_operator_action_fn=lambda *a, _dispatch=dispatch, **kw: _dispatch,
        )
        assert result.mode == expected, (intent, status, result)
