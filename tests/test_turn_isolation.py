"""Turn/tool result isolation: regression tests for the live contamination incident.

Live bug (2026-07-14, installed app): "how many drives do I have?" answered correctly;
"find me dropbox folder pls" then (a) routed to web.search instead of any local search
and (b) -- with every real model lane down -- the emergency 0.6b synthesis copied the
previous drive answer verbatim from the whitespace-collapsed prompt transcript, and the
copy shipped as the reply. These tests pin every layer of the fix: the echo guard, the
duplicate-render dedupe, checkpoint transient-state hygiene, per-turn identity, resume
gating, and end-to-end turn isolation through a real VoolAgent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.checkpoints import prepare_runtime_checkpoint
from core.agent_runtime.orchestrator import render_tool_loop_response, synthesis_echoes_prior_reply
from core.agent_runtime.proceed_intent_support import ProceedIntentSupportMixin
from core.agent_runtime.request_authority import (
    REQUEST_PROVENANCE_KEY,
    request_provenance_for_visible_user_text,
)
from core.memory_first_router import ModelExecutionDecision
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    latest_resumable_checkpoint,
    list_runtime_session_events,
    record_runtime_tool_progress,
    reset_runtime_continuity_state,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
)
from core.runtime_execution_tools import RuntimeExecutionResult
from core.tool_intent_executor import ToolIntentExecution, execute_tool_intent
from storage.migrations import run_migrations

# The live incident's shape: the drive answer as rendered (multi-line, double spaces)
# vs the whitespace-collapsed copy the 0.6b model parroted from the prompt transcript.
_DRIVE_ANSWER_RENDERED = (
    "Drive space:\nC:\\  26.5 GB free of 476.4 GB (94% used)\n"
    "D:\\  120.0 GB free of 500.0 GB (76% used)\n"
    "Total free: 146.5 GB across 2 drive(s)."
)
_DRIVE_ANSWER_COLLAPSED = " ".join(_DRIVE_ANSWER_RENDERED.split())


class _ProceedProbe(ProceedIntentSupportMixin):
    """Minimal agent stand-in exposing the proceed/resume matchers to prepare_runtime_checkpoint."""


class EchoGuardTests(unittest.TestCase):
    def _history(self) -> list[dict[str, str]]:
        return [
            {"role": "user", "content": "how many drives do i have?"},
            {"role": "assistant", "content": _DRIVE_ANSWER_RENDERED},
            {"role": "user", "content": "find me dropbox folder pls, im not sure where it is"},
        ]

    def test_exact_echo_is_detected(self) -> None:
        self.assertTrue(synthesis_echoes_prior_reply(_DRIVE_ANSWER_RENDERED, self._history()))

    def test_whitespace_collapsed_echo_is_detected(self) -> None:
        # The exact live failure: byte-identical AFTER whitespace collapse.
        self.assertTrue(synthesis_echoes_prior_reply(_DRIVE_ANSWER_COLLAPSED, self._history()))

    def test_containment_echo_is_detected(self) -> None:
        padded = "Sure! " + _DRIVE_ANSWER_COLLAPSED
        self.assertTrue(synthesis_echoes_prior_reply(_DRIVE_ANSWER_COLLAPSED, [
            {"role": "assistant", "content": padded},
        ]))

    def test_fresh_answer_is_not_flagged(self) -> None:
        fresh = (
            "I searched C:\\ and D:\\ for a folder named 'dropbox' and found it at "
            "C:\\Users\\you\\Dropbox. Open it from Explorer or tell me what to do next."
        )
        self.assertFalse(synthesis_echoes_prior_reply(fresh, self._history()))

    def test_short_texts_never_flag(self) -> None:
        history = [{"role": "assistant", "content": "Yes."}]
        self.assertFalse(synthesis_echoes_prior_reply("Yes.", history))

    def test_user_messages_do_not_count_as_echo_source(self) -> None:
        text = "find the dropbox folder on this machine and report every match you can see " * 2
        history = [{"role": "user", "content": text}]
        self.assertFalse(synthesis_echoes_prior_reply(text, history))


class RenderDedupeTests(unittest.TestCase):
    def test_step_block_dropped_when_message_restates_last_summary(self) -> None:
        steps = [{"tool_name": "machine.disk_usage", "summary": "Drive space: C:\\ 26.5 GB free", "status": "executed"}]
        rendered = render_tool_loop_response(
            final_message="Here you go. Drive space: C:\\ 26.5 GB free right now.",
            executed_steps=steps,
            include_step_summary=True,
        )
        self.assertNotIn("Real steps completed:", rendered)

    def test_step_block_kept_for_distinct_message(self) -> None:
        steps = [{"tool_name": "web.search", "summary": "3 results for dropbox docs", "status": "executed"}]
        rendered = render_tool_loop_response(
            final_message="Dropbox stores its default folder under your user profile.",
            executed_steps=steps,
            include_step_summary=True,
        )
        self.assertIn("Real steps completed:", rendered)


class CheckpointHygieneTests(unittest.TestCase):
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

    def _checkpoint_with_state(self, session_id: str = "openclaw:hygiene") -> dict:
        request_text = "how many drives do i have?"
        checkpoint = create_runtime_checkpoint(
            session_id=session_id,
            request_text=request_text,
            source_context={
                "runtime_session_id": session_id,
                REQUEST_PROVENANCE_KEY: request_provenance_for_visible_user_text(
                    request_text,
                    session_id=session_id,
                ),
            },
        )
        record_runtime_tool_progress(
            checkpoint["checkpoint_id"],
            executed_steps=[{"tool_name": "machine.disk_usage", "summary": "Drive space: ...", "status": "executed"}],
            loop_source_context={"runtime_session_id": session_id},
            seen_tool_payloads={"sig-1"},
            pending_tool_payload={"intent": "machine.disk_usage", "arguments": {}},
            last_tool_payload={"intent": "machine.disk_usage", "arguments": {}},
            last_tool_response={"response_text": _DRIVE_ANSWER_RENDERED},
            last_tool_name="machine.disk_usage",
            status="running",
        )
        return checkpoint

    def test_finalize_completed_wipes_transient_tool_state(self) -> None:
        checkpoint = self._checkpoint_with_state()
        finalize_runtime_checkpoint(checkpoint["checkpoint_id"], status="completed", final_response="done")
        stored = get_runtime_checkpoint(checkpoint["checkpoint_id"])
        assert stored is not None
        state = dict(stored.get("state") or {})
        self.assertEqual(state.get("executed_steps"), [])
        self.assertIsNone(state.get("pending_tool_payload"))
        self.assertIsNone(state.get("last_tool_payload"))
        self.assertIsNone(state.get("last_tool_response"))
        self.assertEqual(dict(stored.get("pending_intent") or {}), {})

    def test_pending_approval_keeps_state_for_resume(self) -> None:
        checkpoint = self._checkpoint_with_state("openclaw:hygiene-2")
        finalize_runtime_checkpoint(checkpoint["checkpoint_id"], status="pending_approval")
        stored = get_runtime_checkpoint(checkpoint["checkpoint_id"])
        assert stored is not None
        state = dict(stored.get("state") or {})
        self.assertTrue(state.get("executed_steps"))
        self.assertTrue(state.get("pending_tool_payload"))

    def _prepare(self, *, raw_input: str, session_id: str, source_context: dict | None = None) -> dict:
        return prepare_runtime_checkpoint(
            _ProceedProbe(),
            session_id=session_id,
            raw_user_input=raw_input,
            effective_input=raw_input,
            source_context=source_context or {},
            latest_resumable_checkpoint_fn=latest_resumable_checkpoint,
            resume_runtime_checkpoint_fn=resume_runtime_checkpoint,
            create_runtime_checkpoint_fn=create_runtime_checkpoint,
        )

    def test_every_turn_gets_a_fresh_turn_id(self) -> None:
        first = self._prepare(raw_input="how many drives do i have?", session_id="openclaw:turnid")
        second = self._prepare(raw_input="find me dropbox folder pls", session_id="openclaw:turnid")
        turn_1 = str(first["source_context"].get("turn_id") or "")
        turn_2 = str(second["source_context"].get("turn_id") or "")
        self.assertTrue(turn_1.startswith("turn-"))
        self.assertTrue(turn_2.startswith("turn-"))
        self.assertNotEqual(turn_1, turn_2)

    def test_running_checkpoint_is_never_adopted_by_the_next_message(self) -> None:
        # A concurrent in-flight turn leaves a 'running' checkpoint; a proceed-looking
        # message must never attach to it ("missing_resume" -- an honest "nothing to
        # resume" -- or a fresh checkpoint are both safe; adoption is the bug).
        self._checkpoint_with_state("openclaw:running-guard")
        prepared = self._prepare(raw_input="continue", session_id="openclaw:running-guard")
        self.assertNotEqual(prepared["state"], "resumed")
        self.assertFalse(prepared["source_context"].get("runtime_checkpoint_resumed"))

    def test_interrupted_checkpoint_resumes_on_continue_with_flag(self) -> None:
        checkpoint = self._checkpoint_with_state("openclaw:resume-guard")
        update_runtime_checkpoint(checkpoint["checkpoint_id"], status="interrupted")
        prepared = self._prepare(raw_input="continue", session_id="openclaw:resume-guard")
        self.assertEqual(prepared["state"], "resumed")
        self.assertTrue(prepared["source_context"].get("runtime_checkpoint_resumed"))
        self.assertTrue(str(prepared["source_context"].get("turn_id") or "").startswith("turn-"))

    def test_unrelated_message_never_resumes_an_interrupted_checkpoint(self) -> None:
        checkpoint = self._checkpoint_with_state("openclaw:unrelated-guard")
        update_runtime_checkpoint(checkpoint["checkpoint_id"], status="interrupted")
        prepared = self._prepare(
            raw_input="find me dropbox folder pls, im not sure where it is",
            session_id="openclaw:unrelated-guard",
        )
        self.assertEqual(prepared["state"], "created")
        self.assertFalse(prepared["source_context"].get("runtime_checkpoint_resumed"))

    def test_caller_cannot_spoof_the_resumed_flag(self) -> None:
        prepared = self._prepare(
            raw_input="find me dropbox folder pls",
            session_id="openclaw:spoof-guard",
            source_context={"runtime_checkpoint_resumed": True},
        )
        self.assertEqual(prepared["state"], "created")
        self.assertFalse(prepared["source_context"].get("runtime_checkpoint_resumed"))

    def test_tool_call_id_is_stamped_on_every_execution(self) -> None:
        execution = execute_tool_intent(
            {"intent": "definitely.not_a_tool", "arguments": {}},
            task_id="task-1",
            session_id="openclaw:callid",
            source_context={"surface": "openclaw"},
            hive_activity_tracker=mock.Mock(),
            public_hive_bridge=None,
        )
        self.assertTrue(str(execution.details.get("tool_call_id") or "").startswith("tool-"))


def _stub_context() -> SimpleNamespace:
    return SimpleNamespace(
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


def _machine_stub(intent: str, arguments: dict, source_context: dict | None = None, **_: object) -> RuntimeExecutionResult:
    if intent == "machine.disk_usage":
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text=_DRIVE_ANSWER_RENDERED,
            details={"observation": {"schema": "tool_observation_v1", "intent": intent, "ok": True, "status": "executed", "tool_surface": "machine"}},
        )
    if intent == "machine.find_folder":
        name = str((arguments or {}).get("name") or "")
        return RuntimeExecutionResult(
            handled=True, ok=True, status="executed",
            response_text=f"No folder matching '{name}' found on C:\\ (searched to depth 6; system folders skipped).",
            details={"matches": [], "observation": {"schema": "tool_observation_v1", "intent": intent, "ok": True, "status": "executed", "tool_surface": "machine"}},
        )
    return RuntimeExecutionResult(handled=False, ok=False, status="unsupported", response_text="")


class TurnIsolationAgentTests(unittest.TestCase):
    """End-to-end: the live incident's turn sequence through a real VoolAgent."""

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

    def test_drive_turn_then_dropbox_turn_do_not_contaminate(self) -> None:
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(return_value=_stub_context())  # type: ignore[assignment]

        with mock.patch(
            "core.agent_runtime.fast_paths_machine.execute_authorized_runtime_tool",
            side_effect=_machine_stub,
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            first = agent.run_once(
                "how many drives do i have?",
                session_id_override="openclaw:isolation",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            second = agent.run_once(
                "find me dropbox folder pls, im not sure where it is",
                session_id_override="openclaw:isolation",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )
            third = agent.run_once(
                "find me dropbox folder pls, im not sure where it is",
                session_id_override="openclaw:isolation",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        # Turn 1: the drive answer, once (no duplicate paraphrase of the same data).
        self.assertIn("Drive space:", first["response"])
        self.assertEqual(first["response"].count("Total free:"), 1)
        # Turns 2 and 3: the dropbox turns run the LOCAL folder search and never leak
        # the previous drive answer -- the exact live contamination.
        for result in (second, third):
            self.assertIn("dropbox", result["response"].lower())
            self.assertNotIn("Drive space:", result["response"])
            self.assertNotIn("26.5", result["response"])
            self.assertNotIn("Total free:", result["response"])

    def test_tool_loop_echo_synthesis_is_rejected_and_grounded(self) -> None:
        # Reproduce the incident mechanism inside the tool loop: a real tool runs, then
        # the synthesis model returns a copy of the PREVIOUS assistant reply. The loop
        # must reject the echo, answer from the grounded tool result, and record the
        # rejection as a diagnostic event.
        #
        # The executed tool is pinned to `web.search` rather than left to routing. It was always
        # `web.search` that this turn ran -- the prompt classified `research` and the loop planned a
        # web step -- but nothing here said so, and the guard under test only exists on the
        # synthesis path. When a routing fix (QA-050-024) correctly stopped calling a LOCAL folder
        # search "web research", the turn moved to a single-step `workspace.search_text`, which the
        # deterministic direct-render shortcut answers with the tool's own text and NO model call at
        # all. The user-facing assertions below still passed -- vacuously, because the synthesis mock
        # was never consulted -- and only the missing event showed it. The lane the shortcut takes is
        # pinned by `test_a_single_step_workspace_read_never_consults_the_synthesis_model`.
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(return_value=_stub_context())  # type: ignore[assignment]
        tool_decision = ModelExecutionDecision(
            source="provider_execution",
            task_hash="tool-intent-search",
            provider_id="ollama-local:test",
            provider_name="ollama-local",
            model_name="test",
            structured_output={"intent": "web.search", "arguments": {"query": "dropbox"}},
            confidence=0.8,
            trust_score=0.84,
            used_model=True,
            validation_state="valid",
        )
        agent.memory_router.resolve_tool_intent = mock.Mock(return_value=tool_decision)  # type: ignore[assignment]
        # The degraded model "answers" with the previous assistant reply, collapsed --
        # byte-for-byte the live failure.
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="echo-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="qwen3:0.6b",
                output_text=_DRIVE_ANSWER_COLLAPSED,
                confidence=0.4,
                trust_score=0.4,
                used_model=True,
                validation_state="valid",
            )
        )

        def _matching_execute(payload: dict, **_: object) -> ToolIntentExecution:
            intent = str((payload or {}).get("intent") or "")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text=f'Search matches for "dropbox" via {intent}:\n- no matches found',
                mode="tool_executed",
                tool_name=intent,
                details={"query": "dropbox"},
            )

        # Bypass the deterministic folder fast path/plan (tested elsewhere) so this turn
        # exercises the generic tool loop + synthesis, where the live echo happened.
        with mock.patch(
            "core.agent_runtime.fast_paths_machine.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.execution.planner.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            side_effect=_matching_execute,
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "find me dropbox folder pls, im not sure where it is",
                session_id_override="openclaw:echo-loop",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [
                        {"role": "user", "content": "how many drives do i have?"},
                        {"role": "assistant", "content": _DRIVE_ANSWER_RENDERED},
                    ],
                },
            )

        self.assertNotIn(_DRIVE_ANSWER_COLLAPSED, result["response"])
        self.assertNotIn("26.5", result["response"])
        # Non-vacuity: this guard only exists on the synthesis path, so a turn that never
        # consulted the synthesis model cannot have exercised it, however green the assertions
        # above look. That is exactly how this test kept passing while the event disappeared.
        self.assertTrue(
            agent.memory_router.resolve.called,
            "the synthesis model was never consulted, so the rejection guard never ran",
        )
        events = list_runtime_session_events("openclaw:echo-loop", after_seq=0, limit=200)
        self.assertTrue(
            any(event["event_type"] == "stale_result_rejected" for event in events),
            [event["event_type"] for event in events],
        )

    def test_tool_loop_foreign_syntax_synthesis_is_rejected_and_grounded(self) -> None:
        # Brick 2: after a real tool produced observations, the synthesis model returns another
        # platform's tool-call vocabulary (`<|tool_call>call:google_search(...)`) instead of an
        # answer. The terminal validator must reject the leaked syntax, answer from the grounded
        # tool result, and record the rejection -- so the fake tool request never reaches the user.
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(return_value=_stub_context())  # type: ignore[assignment]
        tool_decision = ModelExecutionDecision(
            source="provider_execution",
            task_hash="tool-intent-search",
            provider_id="ollama-local:test",
            provider_name="ollama-local",
            model_name="test",
            structured_output={"intent": "web.search", "arguments": {"query": "dropbox"}},
            confidence=0.8,
            trust_score=0.84,
            used_model=True,
            validation_state="valid",
        )
        agent.memory_router.resolve_tool_intent = mock.Mock(return_value=tool_decision)  # type: ignore[assignment]
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="foreign-synthesis",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="gemma:2b",
                output_text='<|tool_call>call:google_search("dropbox folder")',
                confidence=0.4,
                trust_score=0.4,
                used_model=True,
                validation_state="valid",
            )
        )

        def _matching_execute(payload: dict, **_: object) -> ToolIntentExecution:
            intent = str((payload or {}).get("intent") or "")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text=f'Search matches for "dropbox" via {intent}:\n- ~/Desktop/dropbox',
                mode="tool_executed",
                tool_name=intent,
                details={"query": "dropbox"},
            )

        with mock.patch(
            "core.agent_runtime.fast_paths_machine.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.execution.planner.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            side_effect=_matching_execute,
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "find me dropbox folder pls, im not sure where it is",
                session_id_override="openclaw:foreign-loop",
                source_context={
                    "surface": "openclaw",
                    "platform": "openclaw",
                    "conversation_history": [],
                },
            )

        # The leaked tool syntax must NOT reach the user; the grounded tool result does.
        self.assertNotIn("google_search", result["response"])
        self.assertNotIn("tool_call", result["response"].lower())
        self.assertIn("dropbox", result["response"].lower())
        # Non-vacuity: this guard only exists on the synthesis path, so a turn that never
        # consulted the synthesis model cannot have exercised it, however green the assertions
        # above look. That is exactly how this test kept passing while the event disappeared.
        self.assertTrue(
            agent.memory_router.resolve.called,
            "the synthesis model was never consulted, so the rejection guard never ran",
        )
        events = list_runtime_session_events("openclaw:foreign-loop", after_seq=0, limit=200)
        self.assertTrue(
            any(event["event_type"] == "stale_result_rejected" for event in events),
            [event["event_type"] for event in events],
        )

    def test_a_single_step_workspace_read_never_consults_the_synthesis_model(self) -> None:
        # The other lane, pinned so it cannot drift into the one above or silently disappear.
        #
        # A single deterministic workspace read is answered with the tool's OWN text and no model
        # call: the exact result is stronger evidence than a paraphrase, and a measured incident
        # (fabricated listings -- "index.html, styles.css" invented for a folder holding README.md,
        # pollen.py, requirements.txt, samples.csv) is why. So `stale_result_rejected` is correctly
        # ABSENT here: nothing was rejected because nothing was ever synthesized. The two tests
        # above assert the opposite for the synthesis path; together they say which lane owes which
        # guarantee, which is what nobody could tell when the event went missing.
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        agent.start()
        agent.context_loader.load = mock.Mock(return_value=_stub_context())  # type: ignore[assignment]
        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="tool-intent-workspace",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="test",
                structured_output={
                    "intent": "workspace.search_text",
                    "arguments": {"query": "dropbox"},
                },
                confidence=0.8,
                trust_score=0.84,
                used_model=True,
                validation_state="valid",
            )
        )
        # If this is ever consulted, the shortcut has stopped being a shortcut.
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=ModelExecutionDecision(
                source="provider_execution",
                task_hash="must-not-run",
                provider_id="ollama-local:test",
                provider_name="ollama-local",
                model_name="gemma:2b",
                output_text='<|tool_call>call:google_search("dropbox folder")',
                confidence=0.4,
                trust_score=0.4,
                used_model=True,
                validation_state="valid",
            )
        )

        def _matching_execute(payload: dict, **_: object) -> ToolIntentExecution:
            intent = str((payload or {}).get("intent") or "")
            return ToolIntentExecution(
                handled=True,
                ok=True,
                status="executed",
                response_text=f'Search matches for "dropbox" via {intent}:\n- ~/Desktop/dropbox',
                mode="tool_executed",
                tool_name=intent,
                details={"query": "dropbox"},
            )

        with mock.patch(
            "core.agent_runtime.fast_paths_machine.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.execution.planner.machine_folder_search_intent",
            return_value=None,
        ), mock.patch(
            "core.agent_runtime.agent.execute_tool_intent",
            side_effect=_matching_execute,
        ), mock.patch("core.agent_runtime.agent.orchestrate_parent_task", return_value=None):
            result = agent.run_once(
                "find me dropbox folder pls, im not sure where it is",
                session_id_override="openclaw:direct-render",
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        self.assertFalse(
            agent.memory_router.resolve.called,
            "a deterministic single-step workspace read paraphrased its result through a model",
        )
        self.assertIn("dropbox", result["response"].lower())
        self.assertNotIn("google_search", result["response"])


if __name__ == "__main__":
    unittest.main()
