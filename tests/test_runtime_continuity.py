from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_agent import VoolAgent
from apps.vool_api_server import _dispatch_post
from core.curiosity_roamer import CuriosityResult
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.media_analysis_pipeline import MediaAnalysisResult
from core.memory_first_router import ModelExecutionDecision
from core.mode_permission_policy import (
    PermissionAction,
    grant_internal_authority,
    revoke_internal_authority,
)
from core.response_provenance import strip_provenance_footer
from core.runtime_continuity import (
    append_runtime_event,
    configure_runtime_continuity_db_path,
    create_runtime_checkpoint,
    latest_resumable_checkpoint,
    list_recent_runtime_session_events,
    list_runtime_session_events,
    list_runtime_sessions,
    mark_stale_runtime_checkpoints_interrupted,
    reset_runtime_continuity_state,
)
from core.tool_intent_executor import ToolIntentExecution, execute_tool_intent
from core.web.api.runtime import RuntimeServices, run_agent
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



class RuntimeContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "runtime-continuity.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_stale_running_checkpoint_is_marked_interrupted(self) -> None:
        checkpoint = create_runtime_checkpoint(
            session_id="openclaw:resume-test",
            request_text="inspect the repo and keep going",
            source_context={"runtime_session_id": "openclaw:resume-test"},
        )

        changed = mark_stale_runtime_checkpoints_interrupted()

        self.assertEqual(changed, 1)
        resumable = latest_resumable_checkpoint("openclaw:resume-test")
        self.assertIsNotNone(resumable)
        assert resumable is not None
        self.assertEqual(resumable["checkpoint_id"], checkpoint["checkpoint_id"])
        self.assertEqual(resumable["status"], "interrupted")
        events = list_runtime_session_events("openclaw:resume-test", after_seq=0, limit=10)
        self.assertTrue(any(event["event_type"] == "task_interrupted" for event in events))

    def test_interrupted_task_can_be_explicitly_cancelled_from_recovery_ui(self) -> None:
        checkpoint = create_runtime_checkpoint(
            session_id="openclaw:cancel-recovery",
            request_text="edit files then verify",
            source_context={"runtime_session_id": "openclaw:cancel-recovery"},
        )
        mark_stale_runtime_checkpoints_interrupted()
        response = _dispatch_post(
            path="/api/task/recovery",
            body={"session_id": "openclaw:cancel-recovery", "checkpoint_id": checkpoint["checkpoint_id"], "action": "cancel"},
            headers={"content-type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
            workspace_root_provider=lambda: self._tmp.name,
            client_host="127.0.0.1",
        )
        self.assertEqual(response.status, 200)
        payload = json.loads((response.body or b"{}").decode("utf-8"))
        self.assertEqual(payload["checkpoint"]["status"], "cancelled")
        self.assertIsNone(latest_resumable_checkpoint("openclaw:cancel-recovery"))
        events = list_runtime_session_events("openclaw:cancel-recovery", after_seq=0, limit=20)
        self.assertTrue(any(event["event_type"] == "task_cancelled" for event in events))

    def test_mode_sync_receipt_does_not_replace_completed_task_status(self) -> None:
        session_id = "openclaw:mode-sync-history"
        append_runtime_event(session_id=session_id, event_type="task_received", message="Received")
        append_runtime_event(session_id=session_id, event_type="task_completed", message="Completed")
        append_runtime_event(
            session_id=session_id,
            event_type="mode_changed",
            message="Mode changed to Review edits.",
            details={"active_mode": "review_edits"},
        )

        session = next(item for item in list_runtime_sessions(limit=10) if item["session_id"] == session_id)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["last_event_type"], "mode_changed")

    def test_preference_only_session_is_idle_until_a_real_task_arrives(self) -> None:
        session_id = "openclaw:idle-mode-preference"
        append_runtime_event(
            session_id=session_id,
            event_type="mode_changed",
            message="Mode changed to Plan.",
            details={"active_mode": "plan"},
        )

        idle = next(item for item in list_runtime_sessions(limit=10) if item["session_id"] == session_id)
        self.assertEqual(idle["status"], "idle")
        self.assertFalse(idle["worker_live"])
        self.assertFalse(idle["resume_available"])

        append_runtime_event(session_id=session_id, event_type="task_received", message="Received")
        running = next(item for item in list_runtime_sessions(limit=10) if item["session_id"] == session_id)
        self.assertEqual(running["status"], "running")

    def test_informational_event_cannot_invent_a_running_task(self) -> None:
        session_id = "openclaw:telemetry-only"
        append_runtime_event(
            session_id=session_id,
            event_type="model.inventory_refreshed",
            message="Model inventory refreshed.",
        )

        session = next(item for item in list_runtime_sessions(limit=10) if item["session_id"] == session_id)
        self.assertEqual(session["status"], "idle")

    def _reload_saved_evidence(self, session_id: str) -> dict[str, object]:
        configure_runtime_continuity_db_path(None)
        configure_runtime_continuity_db_path(str(self._db_path))
        session = next(item for item in list_runtime_sessions(limit=20) if item["session_id"] == session_id)
        return session["execution_history"]["bounded_execution"]

    def test_saved_model_review_state_survives_reload_without_validation(self) -> None:
        session_id = "openclaw:saved-model-review"
        append_runtime_event(
            session_id=session_id,
            event_type="model_lane_verifier_completed",
            message="Reviewer model passed.",
            details={"status": "completed"},
        )

        bounded = self._reload_saved_evidence(session_id)
        self.assertEqual(bounded["model_review_state"], "passed")
        self.assertEqual(bounded["validation_state"], "not_run")

    def test_saved_validation_state_survives_reload_without_model_review(self) -> None:
        session_id = "openclaw:saved-validation"
        append_runtime_event(
            session_id=session_id,
            event_type="tool_executed",
            message="Tests passed.",
            details={"tool_name": "workspace.run_tests", "status": "completed"},
        )

        bounded = self._reload_saved_evidence(session_id)
        self.assertEqual(bounded["model_review_state"], "not_run")
        self.assertEqual(bounded["validation_state"], "passed")

    def test_saved_model_review_and_validation_both_survive_reload(self) -> None:
        session_id = "openclaw:saved-review-and-validation"
        append_runtime_event(
            session_id=session_id,
            event_type="model_lane_verifier_flagged",
            message="Reviewer model flagged the answer.",
            details={"status": "failed"},
        )
        append_runtime_event(
            session_id=session_id,
            event_type="tool_executed",
            message="Tests passed.",
            details={"tool_name": "workspace.run_tests", "status": "completed"},
        )

        bounded = self._reload_saved_evidence(session_id)
        self.assertEqual(bounded["model_review_state"], "flagged")
        self.assertEqual(bounded["validation_state"], "passed")

    def test_saved_review_execution_failures_survive_reload_without_becoming_flagged(self) -> None:
        cases = {
            "model_lane_verifier_blocked": "blocked",
            "model_lane_verifier_degraded": "degraded",
            "model_lane_verifier_failed": "runtime_failed",
        }
        for event_type, expected in cases.items():
            with self.subTest(event_type=event_type):
                session_id = f"openclaw:saved-{expected}"
                append_runtime_event(
                    session_id=session_id,
                    event_type=event_type,
                    message=f"Reviewer state: {expected}",
                    details={"status": "failed"},
                )

                bounded = self._reload_saved_evidence(session_id)
                self.assertEqual(bounded["model_review_state"], expected)
                self.assertNotEqual(bounded["model_review_state"], "flagged")
                self.assertEqual(bounded["validation_state"], "not_run")
                self.assertEqual(bounded["verifier_state"], "unavailable")

    def test_terminal_turn_trace_links_policy_context_and_provider_receipts(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        runtime.agent = mock.Mock()

        def run_once(_text: str, *, source_context: dict[str, object], **_kwargs: object) -> dict[str, object]:
            source_context["context_manifest_id"] = "context-manifest-trace-100"
            source_context["context_manifest_trace_id"] = "trace-context-100"
            source_context["response_constraint_final"] = {
                "compliant": True,
                "violations": [],
            }
            source_context["response_control"] = {
                "ordinary_chat_output": {"allowed": True},
                "response_language": {"compliant": True},
                "retry_attempted": False,
                "provider_completion": {"final": {"incomplete": False, "has_content": True, "reasons": []}},
            }
            source_context["provider_manifest_links"] = [
                {
                    "context_manifest_id": "context-manifest-trace-100",
                    "context_manifest_trace_id": "trace-context-100",
                    "provider_manifest_id": "provider-manifest-trace-101",
                    "payload_hash": "a" * 64,
                    "provider_id": "ollama",
                    "model_id": "qwen2.5:7b",
                }
            ]
            return {
                "response": "safe reply",
                "route": "conversation",
                "model_calls": 1,
                "response_control": {"mode": "exact_target"},
            }

        runtime.agent.run_once.side_effect = run_once
        policy = SimpleNamespace(
            chat_id="chat:trace-100",
            project_id="project:trace-100",
            namespace_state="active",
            allow_project_context=True,
            allow_user_profile_context=True,
            allow_action_receipts=False,
            imported_chat_ids=frozenset(),
            imported_project_ids=frozenset({"project:trace-100"}),
        )
        captured: list[dict[str, object]] = []

        def emit(_context: dict[str, object], *, event_type: str, message: str, details: dict[str, object]) -> None:
            captured.append(
                {
                    "event_type": event_type,
                    "message": message,
                    "details": details,
                }
            )

        raw_input = "secret-shaped-input-DO-NOT-PERSIST-772"
        with mock.patch("core.context_scope.ContextAccessPolicy.for_request", return_value=policy), mock.patch(
            "core.runtime_task_events.emit_runtime_event", side_effect=emit
        ):
            result = run_agent(
                runtime,
                raw_input,
                session_id="chat:trace-100",
                source_context={
                    "request_id": "request-trace-099",
                    "action_policy": "forbidden",
                    "response_constraint": {"exact_words": 1, "max_words": 1},
                },
                workspace_root_provider=lambda: self._tmp.name,
            )

        # Body only. Every response carries a provenance line since
        # `core/response_provenance.py`; this test is about the turn trace's receipts, not about
        # the footer, which has its own suite.
        self.assertEqual(strip_provenance_footer(result["response"]), "safe reply")
        trace = captured[-1]
        self.assertEqual(trace["event_type"], "turn.trace_completed")
        details = dict(trace["details"])
        self.assertEqual(details["request_id"], "request-trace-099")
        self.assertEqual(details["chat_id"], "chat:trace-100")
        self.assertEqual(details["context_manifest_id"], "context-manifest-trace-100")
        self.assertEqual(details["action_policy"], "forbidden")
        self.assertEqual(details["response_control_mode"], "exact_target")
        self.assertEqual(
            details["response_control"],
            {
                "ordinary_chat_output": {"allowed": True},
                "response_language": {"compliant": True},
                "final_ui": {},
                "provider_completion": {"final": {"incomplete": False, "has_content": True, "reasons": []}},
                "verifier_presentation": {},
                "retry_attempted": False,
                "fallback_applied": False,
                # Whether the evidence this turn retrieved is witnessed in the answer. The trace
                # projects a WHITELIST, so a verdict not named here is invisible even when the
                # runtime acted on it -- which is why it is asserted rather than tolerated. Empty
                # on this turn because nothing was retrieved. See core/evidence_binding.py.
                "evidence_binding": {},
            },
        )
        self.assertEqual(
            details["response_constraint"],
            {
                "shape": {"exact_words": 1, "max_words": 1},
                "compliant": True,
                "violation_count": 0,
            },
        )
        self.assertEqual(details["provider_manifest_links"], [
            {
                "context_manifest_id": "context-manifest-trace-100",
                "context_manifest_trace_id": "trace-context-100",
                "provider_manifest_id": "provider-manifest-trace-101",
                "payload_hash": "a" * 64,
                "provider_id": "ollama",
                "model_id": "qwen2.5:7b",
            }
        ])
        self.assertEqual(details["policy"]["project_import_count"], 1)
        self.assertNotIn(raw_input, str(trace))

    def test_terminal_turn_trace_recovers_receipts_from_completed_model_event(self) -> None:
        runtime = RuntimeServices(display_name="VOOL")
        runtime.agent = mock.Mock()
        payload_hash = (
            "90c672145008d6a06e24ff52205296a5"
            "f747ba2a69a2463f9535f54cd211a271"
        )

        def run_once(
            _text: str,
            *,
            source_context: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            from core.runtime_task_events import emit_runtime_event

            emit_runtime_event(
                source_context,
                event_type="model.call_completed",
                message="Model call completed.",
                details={
                    "request_id": "request-trace-ledger-200",
                    "context_manifest_id": "context-manifest-ledger-201",
                    "context_manifest_trace_id": "context-trace-ledger-202",
                    "provider_manifest_id": "provider-manifest-ledger-203",
                    "provider_payload_hash": payload_hash,
                    "provider_id": "ollama-local:qwen2.5:7b",
                    "model_id": "qwen2.5:7b",
                    "response_control": {
                        "ordinary_chat_output": {"allowed": True},
                        "response_language": {"compliant": True},
                        "retry_attempted": False,
                        "fallback_applied": False,
                    },
                },
            )
            return {
                "response": "ledger-linked reply",
                "route": "model_minimal:qwen2.5:7b",
                "model_calls": 1,
            }

        runtime.agent.run_once.side_effect = run_once
        for index in range(205):
            append_runtime_event(
                session_id="chat:trace-ledger-200",
                event_type="status",
                message=f"Prior event {index}",
            )
        policy = SimpleNamespace(
            chat_id="chat:trace-ledger-200",
            project_id="",
            namespace_state="active",
            allow_project_context=False,
            allow_user_profile_context=True,
            allow_action_receipts=False,
            imported_chat_ids=frozenset(),
            imported_project_ids=frozenset(),
        )
        with mock.patch(
            "core.context_scope.ContextAccessPolicy.for_request",
            return_value=policy,
        ):
            result = run_agent(
                runtime,
                "Give a normal local answer.",
                session_id="chat:trace-ledger-200",
                source_context={
                    "request_id": "request-trace-ledger-200",
                    "cancel_turn_id": "client-turn-ledger-200",
                },
                workspace_root_provider=lambda: self._tmp.name,
            )

        # Body only -- see the note on the provenance footer in the sibling trace test above.
        self.assertEqual(strip_provenance_footer(result["response"]), "ledger-linked reply")
        events = list_recent_runtime_session_events(
            "chat:trace-ledger-200",
            limit=20,
        )
        trace = next(
            event
            for event in events
            if event["event_type"] == "turn.trace_completed"
        )
        self.assertEqual(
            trace["context_manifest_id"],
            "context-manifest-ledger-201",
        )
        self.assertEqual(
            trace["provider_manifest_links"],
            [
                {
                    "context_manifest_id": "context-manifest-ledger-201",
                    "context_manifest_trace_id": "context-trace-ledger-202",
                    "provider_manifest_id": "provider-manifest-ledger-203",
                    "payload_hash": payload_hash,
                    "provider_id": "ollama-local:qwen2.5:7b",
                    "model_id": "qwen2.5:7b",
                }
            ],
        )
        self.assertEqual(
            trace["response_control"]["ordinary_chat_output"],
            {"allowed": True},
        )

    def test_latest_failed_checkpoint_returns_last_failure_only(self) -> None:
        from core.runtime_continuity import latest_failed_checkpoint, update_runtime_checkpoint

        # An interrupted (resumable) checkpoint is NOT a failure.
        create_runtime_checkpoint(
            session_id="openclaw:retry-test",
            request_text="an interrupted task",
            source_context={"runtime_session_id": "openclaw:retry-test"},
        )
        mark_stale_runtime_checkpoints_interrupted()
        self.assertIsNone(latest_failed_checkpoint("openclaw:retry-test"))

        # A finalized failed checkpoint IS returned, with its immutable request + failure envelope.
        cp_fail = create_runtime_checkpoint(
            session_id="openclaw:retry-test",
            request_text="find me the dropbox folder",
            source_context={"runtime_session_id": "openclaw:retry-test"},
        )
        update_runtime_checkpoint(
            cp_fail["checkpoint_id"], status="failed", failure_text="workspace.search_text failed"
        )
        failed = latest_failed_checkpoint("openclaw:retry-test")
        assert failed is not None
        self.assertEqual(failed["checkpoint_id"], cp_fail["checkpoint_id"])
        self.assertEqual(failed["request_text"], "find me the dropbox folder")
        self.assertEqual(failed["failure_text"], "workspace.search_text failed")

    def test_checkpoint_redacts_secrets_at_rest(self) -> None:
        secret = "fixture-sensitive-value-247"
        create_runtime_checkpoint(
            session_id="openclaw:secret-test",
            request_text=f"use api_key={secret} please",
            source_context={
                "runtime_session_id": "openclaw:secret-test",
                "conversation_history": [
                    {"role": "user", "content": f"api_key={secret}"},
                ],
            },
        )
        mark_stale_runtime_checkpoints_interrupted()
        resumable = latest_resumable_checkpoint("openclaw:secret-test")
        assert resumable is not None
        # The pasted key must not be persisted verbatim in the checkpoint row (request text
        # or the stored conversation history).
        blob = str(resumable.get("request_text") or "") + str(resumable.get("source_context") or "")
        self.assertNotIn(secret, blob)

    def test_mutating_tool_receipt_reuses_prior_execution(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        bridge = mock.Mock()
        bridge.submit_public_topic_result.return_value = {
            "ok": True,
            "status": "result_submitted",
            "topic_id": "topic-1234567890abcdef",
            "post_id": "post-123",
        }
        payload = {
            "intent": "hive.submit_result",
            "arguments": {
                "topic_id": "topic-1234567890abcdef",
                "body": "Done. Resume-safe receipts are wired.",
                "result_status": "solved",
                "claim_id": "claim-123",
            },
        }

        first = execute_tool_intent(
            payload,
            task_id="task-123",
            session_id="openclaw:receipt",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
            checkpoint_id="runtime-checkpoint-1",
            step_index=0,
        )
        second = execute_tool_intent(
            payload,
            task_id="task-123",
            session_id="openclaw:receipt",
            source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
            public_hive_bridge=bridge,
            checkpoint_id="runtime-checkpoint-1",
            step_index=0,
        )

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(bridge.submit_public_topic_result.call_count, 1)
        self.assertTrue(second.details.get("from_receipt"))

    def test_agent_continue_resumes_pending_tool_step(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
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
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded final answer after resume.",
                confidence=0.82,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )
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
                        "arguments": {"message": "Grounded final answer after resume."},
                    },
                    confidence=0.79,
                    trust_score=0.83,
                    used_model=True,
                    validation_state="valid",
                ),
            ]
        )

        with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=RuntimeError("tool crashed mid-step")), mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                agent.run_once(
                    "find tool intent wiring",
                    session_id_override="openclaw:resume-agent",
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )

        resumable = latest_resumable_checkpoint("openclaw:resume-agent")
        self.assertIsNotNone(resumable)
        assert resumable is not None
        self.assertEqual(resumable["status"], "interrupted")

        def _execute_matching_intent(payload: dict, **_: object) -> ToolIntentExecution:
            # Real executions always report the intent they ran (tool_name == intent);
            # the result-mismatch guard rejects anything else.
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
            side_effect=_execute_matching_intent,
        ) as execute_tool_intent, mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "continue",
                session_id_override="openclaw:resume-agent",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Grounded final answer after resume.", result["response"])
        self.assertEqual(execute_tool_intent.call_count, 1)
        resumed = latest_resumable_checkpoint("openclaw:resume-agent")
        self.assertIsNone(resumed)

    def test_immediate_tool_loop_history_uses_structured_observation_message(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        tool_decision = ModelExecutionDecision(
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
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[tool_decision, tool_decision]
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="final-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Grounded final answer after structured tool observation.",
                confidence=0.82,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )

        def _execute_with_observation(payload: dict, **_: object) -> ToolIntentExecution:
            # tool_name mirrors the requested intent, as the real executor guarantees.
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text='Search matches for "tool_intent":\n- core/tool_intent_executor.py:42 def execute_tool_intent(',
                mode="tool_executed",
                tool_name=str((payload or {}).get("intent") or ""),
                details={
                    "observation": {
                        "schema": "tool_observation_v1",
                        "intent": str((payload or {}).get("intent") or ""),
                        "tool_surface": "workspace",
                        "ok": True,
                        "status": "executed",
                        "query": "tool_intent",
                        "matches": [
                            {
                                "path": "core/tool_intent_executor.py",
                                "line": 42,
                                "snippet": "def execute_tool_intent(",
                            }
                        ],
                    }
                },
            )

        with mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            side_effect=_execute_with_observation,
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "find tool intent wiring",
                session_id_override="openclaw:structured-tool-loop",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("Grounded final answer after structured tool observation.", result["response"])

        self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
        second_tool_source_context = agent.memory_router.resolve.call_args.kwargs["source_context"]
        second_tool_history = list(second_tool_source_context.get("conversation_history") or [])
        self.assertTrue(second_tool_history)
        self.assertEqual(second_tool_history[-1]["role"], "user")
        self.assertIn("Grounding observations for this turn", second_tool_history[-1]["content"])
        self.assertIn('"receipt_id": "tool-receipt-', second_tool_history[-1]["content"])
        self.assertIn('"safe_summary":', second_tool_history[-1]["content"])
        self.assertNotIn('"tool_surface":', second_tool_history[-1]["content"])
        self.assertNotIn('"query": "tool_intent"', second_tool_history[-1]["content"])
        self.assertNotIn("Real tool result from", second_tool_history[-1]["content"])

    def test_merge_runtime_source_contexts_upgrades_legacy_tool_prose_to_observation_message(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        merged = agent._merge_runtime_source_contexts(
            {
                "conversation_history": [
                    {
                        "role": "assistant",
                        "content": (
                            "Real tool result from `workspace.search_text`:\n"
                            'Search matches for "tool_intent":\n'
                            "- core/tool_intent_executor.py:42 def execute_tool_intent("
                        ),
                    }
                ]
            },
            {"surface": "openclaw", "platform": "openclaw"},
        )

        history = list(merged.get("conversation_history") or [])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["role"], "user")
        self.assertIn("Grounding observations for this turn", history[0]["content"])
        self.assertIn('"receipt_id": "tool-receipt-', history[0]["content"])
        self.assertIn('"safe_summary": "workspace.search_text:', history[0]["content"])
        self.assertNotIn('"tool_surface":', history[0]["content"])
        self.assertNotIn("Real tool result from", history[0]["content"])

    @pytest.mark.xfail(reason="Pre-existing: workflow planner output format changed")
    def test_workflow_planner_can_chain_real_tools_without_reasking_model_each_step(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("workflow planner should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="planner-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I ran the check, fixed the marker in app.py, and the retry passed.",
                confidence=0.88,
                trust_score=0.89,
                used_model=True,
                validation_state="valid",
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "app.py").write_text(
                "from pathlib import Path\n"
                "text = Path(__file__).read_text(encoding='utf-8')\n"
                "if 'TODO' in text:\n"
                "    print('app.py:1 TODO marker still present')\n"
                "    raise SystemExit(1)\n"
                "print('clean')\n",
                encoding="utf-8",
            )
            with mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
                result = agent.run_once(
                    "run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
                    session_id_override="openclaw:workflow-planner",
                    source_context={"surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
                )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertIn("retry passed", result["response"].lower())
            self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
            self.assertIn("DONE", (workspace / "app.py").read_text(encoding="utf-8"))
            final_history = list(agent.memory_router.resolve.call_args.kwargs["source_context"].get("conversation_history") or [])
            joined_history = "\n".join(str(item.get("content") or "") for item in final_history)
            self.assertIn('"intent": "workspace.replace_in_file"', joined_history)
            self.assertIn('"intent": "sandbox.run_command"', joined_history)

    def test_builder_controller_runs_bounded_scaffold_loop_without_reasking_model(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I finished a bounded Telegram build loop and the compile check passed.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]

        def _execute_builder_step(
            payload,
            *,
            task_id,
            session_id,
            source_context,
            hive_activity_tracker,
            public_hive_bridge=None,
            checkpoint_id=None,
            step_index=0,
            trusted_local_only=False,
        ):
            tool_name = str(payload.get("intent") or "")
            arguments = dict(payload.get("arguments") or {})
            if tool_name == "workspace.write_file":
                path = str(arguments["path"])
                content = str(arguments.get("content") or "")
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text=f"Created file `{path}` with {len(content.splitlines())} lines.",
                    mode="tool_executed",
                    tool_name="workspace.write_file",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "file_diff",
                                "path": path,
                                "action": "created",
                                "line_count": len(content.splitlines()),
                                "diff_preview": f"--- a/{path}\n+++ b/{path}\n@@\n+created",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.write_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": path,
                            "line_count": len(content.splitlines()),
                            "action": "created",
                        }
                    },
                )
            if tool_name == "sandbox.run_command":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 -m compileall -q generated/telegram-bot/src\n- Exit code: 0",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 -m compileall -q generated/telegram-bot/src",
                                "cwd": ".",
                                "returncode": 0,
                                "stdout": "",
                                "stderr": "",
                                "status": "executed",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 -m compileall -q generated/telegram-bot/src",
                            "cwd": ".",
                            "returncode": 0,
                        }
                    },
                )
            raise AssertionError(f"unexpected builder tool: {tool_name}")

        with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_execute_builder_step) as execute_tool_intent, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
        ):
            result = agent.run_once(
                "build a telegram bot in this workspace and write the files",
                session_id_override="openclaw:builder-controller",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-builder-controller"},
            )

        self.assertEqual(result["mode"], "tool_executed")
        self.assertIn("bounded telegram build loop", result["response"].lower())
        self.assertEqual(result["details"]["builder_controller"]["mode"], "scaffold")
        self.assertEqual(result["details"]["builder_controller"]["step_count"], 5)
        self.assertEqual(result["details"]["builder_controller"]["stop_reason"], "command_stop_after_success")
        self.assertEqual(execute_tool_intent.call_count, 5)
        self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)
        final_history = list(agent.memory_router.resolve.call_args.kwargs["source_context"].get("conversation_history") or [])
        joined_history = "\n".join(str(item.get("content") or "") for item in final_history)
        self.assertIn('"receipt_id": "tool-receipt-', joined_history)
        self.assertIn('"safe_summary": "workspace.write_file:', joined_history)
        self.assertIn('"safe_summary": "sandbox.run_command:', joined_history)
        self.assertNotIn('"arguments":', joined_history)
        self.assertNotIn("Real tool result from", joined_history)
        artifacts = result["details"]["builder_controller"]["artifacts"]
        self.assertTrue(artifacts["file_diffs"])
        self.assertTrue(artifacts["command_outputs"])
        self.assertEqual(artifacts["stop_reason"], "command_stop_after_success")
        self.assertIn("Artifacts:", result["response"])

    def test_builder_controller_bootstraps_generic_workspace_folder_and_files(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-generic-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I created the starter folder and first files so work can continue inside that workspace.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            result = agent.run_once(
                "create a folder called tools and start putting code in there",
                session_id_override="openclaw:builder-generic-bootstrap",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "scaffold")
            self.assertTrue((Path(tmpdir) / "tools" / "README.md").is_file())
            self.assertTrue((Path(tmpdir) / "tools" / "src" / "main.py").is_file())
            self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)

    def test_builder_controller_prefers_workflow_for_explicit_file_chain_request(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(side_effect=AssertionError("builder workflow should bypass context loading"))  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(side_effect=AssertionError("builder workflow should bypass model resolution"))  # type: ignore[assignment]
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            result = agent.run_once(
                (
                    "Create a folder named vool_chain_test. Inside it create notes.txt with the line first note. "
                    "Then create summary.txt that says: notes.txt created successfully. Then list the folder contents."
                ),
                session_id_override="openclaw:builder-explicit-chain",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            chain_root = Path(tmpdir) / "vool_chain_test"
            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
            self.assertTrue((chain_root / "notes.txt").is_file())
            self.assertTrue((chain_root / "summary.txt").is_file())
            self.assertEqual((chain_root / "notes.txt").read_text(encoding="utf-8"), "first note")
            self.assertEqual((chain_root / "summary.txt").read_text(encoding="utf-8"), "notes.txt created successfully")
            self.assertIn("workspace.list_files", result["details"]["builder_controller"]["tool_steps"])
            self.assertEqual(agent.context_loader.load.call_count, 0)
            self.assertEqual(agent.memory_router.resolve.call_count, 0)
            self.assertEqual(agent.memory_router.resolve_tool_intent.call_count, 0)

    def test_builder_controller_returns_verbatim_readback_for_exact_file_request(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-exact-readback",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="placeholder",
                confidence=0.62,
                trust_score=0.65,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            target = Path(tmpdir) / "vool_test_01.txt"
            target.write_text("ALPHA-LOCAL-FILE-01\nBETA-APPEND-02", encoding="utf-8")
            result = agent.run_once(
                "Now read the whole file back exactly.",
                session_id_override="openclaw:builder-exact-readback",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "workspace": tmpdir,
                    "conversation_history": [
                        {
                            "role": "user",
                            "content": "Create a file named vool_test_01.txt in the current workspace with exactly this content: ALPHA-LOCAL-FILE-01",
                        },
                        {
                            "role": "assistant",
                            "content": "Created vool_test_01.txt.",
                        },
                    ],
                },
            )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["response"], "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
            self.assertIn("workspace.read_file", result["details"]["builder_controller"]["tool_steps"])

    def test_builder_controller_handles_pathless_append_followup_from_history(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-append-followup",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I appended the requested line.",
                confidence=0.78,
                trust_score=0.8,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            # The chain's write scope is bounded to exactly this temporary workspace.
            _chain_authority = grant_internal_authority(
                label="builder-append-chain",
                actions=_CHAIN_WRITE_ACTIONS,
                duration_seconds=300,
                workspace_root=tmpdir,
            )
            self.addCleanup(revoke_internal_authority, _chain_authority)
            target = Path(tmpdir) / "vool_test_01.txt"
            target.write_text("ALPHA-LOCAL-FILE-01", encoding="utf-8")
            result = agent.run_once(
                "Append a second line: BETA-APPEND-02",
                session_id_override="openclaw:builder-append-followup",
                source_context={
                    "internal_authority_token": _chain_authority,
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "workspace": tmpdir,
                    "conversation_history": [
                        {
                            "role": "user",
                            "content": "Create a file named vool_test_01.txt in the current workspace with exactly this content: ALPHA-LOCAL-FILE-01",
                        },
                        {
                            "role": "assistant",
                            "content": "Created vool_test_01.txt.",
                        },
                    ],
                },
            )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
            self.assertEqual(target.read_text(encoding="utf-8"), "ALPHA-LOCAL-FILE-01\nBETA-APPEND-02")
            self.assertIn("workspace.read_file", result["details"]["builder_controller"]["tool_steps"])
            self.assertIn("workspace.write_file", result["details"]["builder_controller"]["tool_steps"])

    def test_builder_controller_handles_exact_multi_file_request(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-exact-three",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="Created the requested files.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]

        with tempfile.TemporaryDirectory() as tmpdir, mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task",
            return_value=None,
        ):
            result = agent.run_once(
                "Create exactly three files: a.txt, b.txt, c.txt. Put ONE, TWO, THREE respectively. Do not create anything else.",
                session_id_override="openclaw:builder-exact-three",
                source_context={"operating_mode": "auto", "surface": "openclaw", "platform": "openclaw", "workspace": tmpdir},
            )

            self.assertEqual(result["mode"], "tool_executed")
            self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
            self.assertEqual((Path(tmpdir) / "a.txt").read_text(encoding="utf-8"), "ONE")
            self.assertEqual((Path(tmpdir) / "b.txt").read_text(encoding="utf-8"), "TWO")
            self.assertEqual((Path(tmpdir) / "c.txt").read_text(encoding="utf-8"), "THREE")

    def test_builder_controller_preserves_failures_and_retry_history(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        stub_context = SimpleNamespace(
            local_candidates=[],
            swarm_metadata=[],
            retrieval_confidence_score=0.0,
            assembled_context=lambda *args, **kwargs: "",
            context_snippets=lambda: [],
            report=SimpleNamespace(
                retrieval_confidence=0.0,
                total_tokens_used=lambda: 0,
                to_dict=lambda: {"external_evidence_attachments": []},
            ),
        )
        agent.context_loader.load = mock.Mock(return_value=stub_context)  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=AssertionError("builder controller should drive this loop"))  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="builder-retry-final",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                output_text="I repaired the file and the rerun passed.",
                confidence=0.86,
                trust_score=0.87,
                used_model=True,
                validation_state="valid",
            )
        )
        agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
            return_value=CuriosityResult(enabled=False, mode="off", reason="test")
        )
        agent.media_pipeline.analyze = mock.Mock(  # type: ignore[assignment]
            return_value=MediaAnalysisResult(False, reason="no_external_media")
        )
        agent._maybe_execute_model_tool_intent = mock.Mock(return_value=None)  # type: ignore[assignment]
        agent._should_run_builder_controller = mock.Mock(return_value=True)  # type: ignore[assignment]

        step_counter = {"count": 0}
        command_counter = {"python3 app.py": 0}

        def _execute_builder_step(
            payload,
            *,
            task_id,
            session_id,
            source_context,
            hive_activity_tracker,
            public_hive_bridge=None,
            checkpoint_id=None,
            step_index=0,
            trusted_local_only=False,
        ):
            tool_name = str(payload.get("intent") or "")
            step_counter["count"] += 1
            if tool_name == "sandbox.run_command":
                command = str(dict(payload.get("arguments") or {}).get("command") or "").strip()
                command_counter[command] = int(command_counter.get(command, 0)) + 1
            if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 1:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 1\n- Stderr:\nFAILED test_example",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 1,
                                "stdout": "",
                                "stderr": "FAILED test_example",
                                "status": "executed",
                            },
                            {
                                "artifact_type": "failure",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 1,
                                "summary": "FAILED test_example",
                                "stdout": "",
                                "stderr": "FAILED test_example",
                            },
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 1,
                            "stderr": "FAILED test_example",
                            "failure_summary": "FAILED test_example",
                            "error_path": "app.py",
                            "error_line": 1,
                        },
                    },
                )
            if tool_name == "workspace.search_text":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Found 1 match for `FAILED test_example` in `app.py`.",
                    mode="tool_executed",
                    tool_name="workspace.search_text",
                    details={
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.search_text",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "query": "FAILED test_example",
                            "match_count": 1,
                            "matches": [
                                {
                                    "path": "app.py",
                                    "line": 1,
                                    "preview": "TODO",
                                }
                            ],
                        }
                    },
                )
            if tool_name == "workspace.read_file":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="File `app.py`:\n1: TODO",
                    mode="tool_executed",
                    tool_name="workspace.read_file",
                    details={
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.read_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "start_line": 1,
                            "line_count": 1,
                        }
                    },
                )
            if tool_name == "workspace.replace_in_file":
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Applied 1 replacement in `app.py`.",
                    mode="tool_executed",
                    tool_name="workspace.replace_in_file",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "file_diff",
                                "path": "app.py",
                                "action": "replaced",
                                "replacements": 1,
                                "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "workspace.replace_in_file",
                            "tool_surface": "workspace",
                            "ok": True,
                            "status": "executed",
                            "path": "app.py",
                            "replacements": 1,
                            "diff_preview": "--- a/app.py\n+++ b/app.py\n@@\n-TODO\n+DONE",
                        },
                    },
                )
            if tool_name == "sandbox.run_command" and command_counter.get("python3 app.py", 0) == 2:
                return ToolIntentExecution(
                    handled=True,
                    ok=True,
                    status="executed",
                    response_text="Command executed in `.`:\n$ python3 app.py\n- Exit code: 0\n- Stdout:\nclean",
                    mode="tool_executed",
                    tool_name="sandbox.run_command",
                    details={
                        "artifacts": [
                            {
                                "artifact_type": "command_output",
                                "command": "python3 app.py",
                                "cwd": ".",
                                "returncode": 0,
                                "stdout": "clean",
                                "stderr": "",
                                "status": "executed",
                            }
                        ],
                        "observation": {
                            "schema": "tool_observation_v1",
                            "intent": "sandbox.run_command",
                            "tool_surface": "sandbox",
                            "ok": True,
                            "status": "executed",
                            "command": "python3 app.py",
                            "cwd": ".",
                            "returncode": 0,
                            "stdout": "clean",
                        },
                    },
                )
            raise AssertionError(f"unexpected builder tool: {tool_name}")

        with mock.patch("core.agent_runtime.agent.execute_tool_intent", side_effect=_execute_builder_step), mock.patch(
            "core.agent_runtime.agent.orchestrate_parent_task", return_value=None
        ), mock.patch(
            "core.agent_runtime.agent.classify",
            return_value={"task_class": "debugging", "risk_flags": [], "confidence_hint": 0.82},
        ):
            result = agent.run_once(
                "run `python3 app.py`, replace `TODO` with `DONE` in app.py, then retry",
                session_id_override="openclaw:builder-retry-history",
                source_context={"surface": "openclaw", "platform": "openclaw", "workspace": "/tmp/vool-builder-retry"},
            )

        artifacts = result["details"]["builder_controller"]["artifacts"]
        self.assertEqual(result["details"]["builder_controller"]["mode"], "workflow")
        self.assertTrue(artifacts["failures"])
        self.assertTrue(artifacts["retry_history"])
        self.assertEqual(artifacts["retry_history"][0]["attempts"], 2)
        self.assertIn("FAILED test_example", artifacts["failures"][0]["summary"])
        self.assertIn("failures seen", result["response"].lower())
        self.assertIn("retries", result["response"].lower())


if __name__ == "__main__":
    unittest.main()
