"""The explicit /goal workflow: store, plan model, controller semantics, and event mapping.

These tests run against an isolated store database (the storage-layer test pattern) and never
touch a real profile. The served/browser behavior is proven separately by the served rig;
what is pinned here is the durable truth the card, the commands and Activity all read.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.goal_executor import (
    _classify_turn_outcome,
    retry_waiting_step,
    step_source_context,
    verification_holds,
)
from core.goal_workflow import (
    build_plan,
    completed_step_count,
    derived_single_step_plan,
    emit_goal_event,
    goal_public_view,
    load_active_goal_for_session,
    load_goal,
    load_latest_goal_for_session,
    new_goal_id,
    parse_goal_command,
    parse_planner_answer,
    reconcile_goal_if_orphaned,
    request_pause,
    request_resume,
    request_stop,
    reset_goal_state_for_tests,
    save_goal,
)
from core.task_event_model import build_task_event
from storage.db import configure_default_db_path
from storage.migrations import run_migrations


class GoalWorkflowTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "goal-workflow.db"
        run_migrations(db_path=self._db_path)
        self._previous_db_override = configure_default_db_path(self._db_path)
        reset_goal_state_for_tests()

    def tearDown(self) -> None:
        reset_goal_state_for_tests()
        configure_default_db_path(self._previous_db_override)
        self._tmp.cleanup()

    def _goal(self, session_id: str = "chat-a", state: str = "running", steps: str = "two") -> dict:
        plan = (
            build_plan(
                [
                    {"id": "s1", "title": "first", "verify": {"tool": "workspace.write_file", "path_contains": "a.txt"}},
                    {"id": "s2", "title": "second", "depends_on": ["s1"]},
                ],
                provenance="model",
            )
            if steps == "two"
            else {}
        )
        goal = {
            "goal_id": new_goal_id(),
            "session_id": session_id,
            "project_id": "",
            "objective": "write the demo page and verify it",
            "acceptance": "",
            "plan": plan,
            "state": state,
            "revision": 1 if plan else 0,
            "current_step_id": "",
            "created_at": 1.0,
            "updated_at": 1.0,
            "ended_at": None,
            "started_at": 1.0,
            "paused_at": None,
            "paused_seconds": 0.0,
            "created_turn_id": "turn-1",
            "last_reason": "",
            "model": "test-model",
            "exec_context": {},
        }
        save_goal(goal)
        return goal


class PlanModelTests(GoalWorkflowTestBase):
    def test_plan_validation_builds_stable_ids_and_dependencies(self) -> None:
        plan = build_plan(
            [
                {"id": "a", "title": "one", "verify": {"tool": "workspace.write_file", "path_contains": "x"}},
                {"title": "two", "depends_on": ["a"]},
            ],
            provenance="model",
        )
        self.assertEqual(plan["revision"], 1)
        self.assertEqual(plan["provenance"], "model")
        self.assertEqual([s["id"] for s in plan["steps"]], ["a", plan["steps"][1]["id"]])
        self.assertEqual(plan["steps"][1]["depends_on"], ["a"])
        self.assertEqual(plan["steps"][0]["state"], "pending")
        self.assertEqual(json.loads(plan["steps"][0]["verify"]), {"tool": "workspace.write_file", "path_contains": "x"})

    def test_plan_rejects_cycles_duplicates_and_oversize(self) -> None:
        with self.assertRaises(ValueError):
            build_plan([{"id": "a", "title": "x", "depends_on": ["b"]}, {"id": "b", "title": "y", "depends_on": ["a"]}], provenance="model")
        with self.assertRaises(ValueError):
            build_plan([{"id": "a", "title": "x"}, {"id": "a", "title": "y"}], provenance="model")
        with self.assertRaises(ValueError):
            build_plan([{"id": f"s{i}", "title": f"t{i}"} for i in range(9)], provenance="model")
        with self.assertRaises(ValueError):
            build_plan([{"id": "a", "title": ""}], provenance="model")
        with self.assertRaises(ValueError):
            build_plan([{"id": "a", "title": "self-dep", "depends_on": ["a"]}], provenance="model")

    def test_derived_single_step_plan_is_honest_fallback(self) -> None:
        plan = derived_single_step_plan("do the thing")
        self.assertEqual(plan["provenance"], "derived")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["title"], "do the thing")

    def test_planner_answer_parsing(self) -> None:
        self.assertEqual(
            parse_planner_answer('```json\n{"steps": [{"id": "s1", "title": "t"}]}\n```'),
            [{"id": "s1", "title": "t"}],
        )
        self.assertEqual(parse_planner_answer('{"steps": [{"title": "t"}]}'), [{"title": "t"}])
        self.assertIsNone(parse_planner_answer("I will now build it."))
        self.assertIsNone(parse_planner_answer('{"steps": []}'))
        self.assertIsNone(parse_planner_answer(""))

    def test_command_parsing(self) -> None:
        self.assertEqual(parse_goal_command("/goal"), {"sub": "", "arg": ""})
        self.assertEqual(parse_goal_command("/goal build the site"), {"sub": "", "arg": "build the site"})
        self.assertEqual(parse_goal_command("/goal  status"), {"sub": "status", "arg": ""})
        self.assertEqual(parse_goal_command("/goal stop"), {"sub": "stop", "arg": ""})
        self.assertEqual(parse_goal_command("/goal resume now"), {"sub": "resume", "arg": "now"})
        self.assertEqual(parse_goal_command("/goal revise new objective"), {"sub": "revise", "arg": "new objective"})
        self.assertIsNone(parse_goal_command("/goals"))
        self.assertIsNone(parse_goal_command("please /goal"))


class GoalStoreTests(GoalWorkflowTestBase):
    def test_save_load_roundtrip(self) -> None:
        goal = self._goal()
        loaded = load_goal(goal["goal_id"])
        assert loaded is not None
        self.assertEqual(loaded["objective"], "write the demo page and verify it")
        self.assertEqual(loaded["plan"]["steps"][0]["id"], "s1")
        self.assertEqual(loaded["state"], "running")
        self.assertEqual(loaded["model"], "test-model")

    def test_one_active_goal_per_chat_and_latest_lookup(self) -> None:
        goal = self._goal(state="running")
        self._goal(state="completed")  # an older terminal goal does not shadow the active one
        active = load_active_goal_for_session("chat-a")
        assert active is not None
        self.assertEqual(active["goal_id"], goal["goal_id"])
        latest = load_latest_goal_for_session("chat-a")
        assert latest is not None
        self.assertEqual(latest["goal_id"], goal["goal_id"])
        self.assertIsNone(load_active_goal_for_session("chat-b"))

    def test_public_view_counts_and_elapsed(self) -> None:
        goal = self._goal()
        goal["plan"]["steps"][0]["state"] = "completed"
        save_goal(goal)
        view = goal_public_view(goal)
        self.assertEqual((view["completed"], view["total"]), (1, 2))
        self.assertTrue(view["has_plan"])
        self.assertGreaterEqual(view["elapsed_seconds"], 0.0)
        empty = self._goal(steps="none")
        view = goal_public_view(empty)
        self.assertFalse(view["has_plan"])
        self.assertEqual((view["completed"], view["total"]), (0, 0))


class VerificationTests(GoalWorkflowTestBase):
    def test_verification_reads_recorded_tool_events_only(self) -> None:
        plan = build_plan([{"id": "s1", "title": "write", "verify": {"tool": "workspace.write_file", "path_contains": "a.txt"}}], provenance="model")
        step = plan["steps"][0]
        ok, _ = verification_holds(step, [
            {"event_type": "tool_executed", "tool_name": "workspace.write_file", "path": "ws/a.txt"},
        ])
        self.assertTrue(ok)
        missing, reason = verification_holds(step, [
            {"event_type": "tool_executed", "tool_name": "workspace.read_file", "path": "ws/a.txt"},
            {"event_type": "tool_failed", "tool_name": "workspace.write_file", "path": "ws/a.txt"},
        ])
        self.assertFalse(missing)
        self.assertIn("verification not met", reason)

    def test_step_without_verification_holds_on_turn_events(self) -> None:
        plan = build_plan([{"id": "s1", "title": "think"}], provenance="model")
        ok, _ = verification_holds(plan["steps"][0], [])
        self.assertTrue(ok)

    def test_turn_outcome_classification_never_invents_success(self) -> None:
        self.assertEqual(_classify_turn_outcome({"fulfillment_outcome": {"fulfillment_status": "fulfilled"}, "task_outcome": "success"}, False), ("completed", ""))
        self.assertEqual(_classify_turn_outcome({"task_outcome": "pending_approval"}, False)[0], "waiting_approval")
        # A block that IS a live approval wait.
        self.assertEqual(
            _classify_turn_outcome({"fulfillment_outcome": {"fulfillment_status": "blocked", "failure_codes": ["approval_required"]}}, False)[0],
            "waiting_approval",
        )
        # A budget/capability block is a resource hold, never an approval.
        self.assertEqual(
            _classify_turn_outcome({"fulfillment_outcome": {"fulfillment_status": "blocked", "failure_codes": ["budget_exhausted"]}}, False)[0],
            "blocked",
        )
        self.assertEqual(_classify_turn_outcome({"fulfillment_outcome": {"fulfillment_status": "failed", "failure_codes": ["x"]}}, False)[0], "failed")
        self.assertEqual(_classify_turn_outcome({"fulfillment_outcome": {"fulfillment_status": "cancelled"}}, False)[0], "cancelled")
        self.assertEqual(_classify_turn_outcome({}, True)[0], "interrupted")
        self.assertEqual(_classify_turn_outcome({"error": "boom"}, False)[0], "failed")
        self.assertEqual(_classify_turn_outcome({}, False)[0], "failed")

    def test_prose_cannot_manufacture_completion(self) -> None:
        # The review's reproduced defect: a nonempty refusal with a failure-shaped typed
        # outcome and no fulfillment evidence completed. It must fail.
        result = {
            "task_outcome": "provider_did_not_answer",
            "response": "I could not complete the work.",
        }
        self.assertEqual(_classify_turn_outcome(result, False)[0], "failed")
        # A fulfilled receipt with a success outcome still completes.
        self.assertEqual(
            _classify_turn_outcome({"task_outcome": "success", "fulfillment_outcome": {"fulfillment_status": "fulfilled"}, "response": "done"}, False)[0],
            "completed",
        )

    def test_step_context_drops_stream_and_authority_keys(self) -> None:
        base = {
            "runtime_event_stream_id": "stream-1",
            "cancel_event": "event",
            "bypass_token": "tok",
            "mode_approval_token": "appr",
            "workspace": "/tmp/ws",
            "runtime_session_id": "chat-a",
            "operating_mode": "manual",
        }
        ctx = step_source_context(base, goal={"goal_id": "g1"}, step={"id": "s1"}, attempt=1, turn_id="g1:s1:a1")
        self.assertNotIn("runtime_event_stream_id", ctx)
        self.assertNotIn("bypass_token", ctx)
        self.assertNotIn("mode_approval_token", ctx)
        self.assertNotIn("cancel_event", ctx)
        self.assertEqual(ctx["cancel_turn_id"], "g1:s1:a1")
        self.assertEqual(ctx["workspace"], "/tmp/ws")
        # No pinned mode: the authority resolves the chat's LIVE session record per consult.
        self.assertNotIn("operating_mode", ctx)
        self.assertNotIn("operating_mode_revision", ctx)
        self.assertEqual(ctx["goal_step"]["revision"], 0)
        ctx2 = step_source_context(base, goal={"goal_id": "g1"}, step={"id": "s1"}, attempt=2, turn_id="t", approval_token="tok2")
        self.assertEqual(ctx2["mode_approval_token"], "tok2")


class ControlSemanticsTests(GoalWorkflowTestBase):
    def test_pause_preserves_settlement_and_resume_reconciles_receipts(self) -> None:
        goal = self._goal()
        goal["state"] = "running"
        goal["current_step_id"] = "s1"
        goal["plan"]["steps"][0]["state"] = "running"
        save_goal(goal)
        outcome = request_pause(goal["goal_id"])
        self.assertTrue(outcome["ok"])
        paused = load_goal(goal["goal_id"])
        assert paused is not None
        self.assertEqual(paused["state"], "paused")
        self.assertIsNotNone(paused["paused_at"])
        # Pause does NOT pre-write a step state it has not observed: the in-flight step
        # stays exactly as the worker left it until the turn settles and is classified.
        self.assertEqual(paused["plan"]["steps"][0]["state"], "running")

        # Resume reconciles the interrupted step against its recorded turn's receipts.
        paused = load_goal(goal["goal_id"])
        assert paused is not None
        paused["plan"]["steps"][0]["state"] = "interrupted"
        paused["plan"]["steps"][0]["turn_id"] = "turn-x"
        save_goal(paused)
        # The interrupted turn's verification ALREADY holds in the ledger: completed, never
        # replayed.
        from unittest import mock

        with mock.patch(
            "core.goal_executor._step_events",
            return_value=[{"event_type": "tool_executed", "tool_name": "workspace.write_file", "path": "ws/a.txt"}],
        ):
            outcome = request_resume(goal["goal_id"])
        self.assertTrue(outcome["ok"])
        resumed = load_goal(goal["goal_id"])
        assert resumed is not None
        self.assertEqual(resumed["state"], "running")
        self.assertEqual(resumed["plan"]["steps"][0]["state"], "completed")
        self.assertIsNone(resumed["paused_at"])

    def test_resume_marks_unverifiable_effect_a_reconciliation_need(self) -> None:
        from unittest import mock

        goal = self._goal(state="paused")
        goal["plan"]["steps"][0]["state"] = "interrupted"
        goal["plan"]["steps"][0]["turn_id"] = "turn-y"
        save_goal(goal)
        # The expected tool RAN in the interrupted turn but never verifiably completed:
        # replaying it could duplicate a real effect — a reconciliation need, honestly.
        with mock.patch(
            "core.goal_executor._step_events",
            return_value=[{"event_type": "tool_failed", "tool_name": "workspace.write_file", "path": "ws/a.txt"}],
        ):
            outcome = request_resume(goal["goal_id"])
        self.assertTrue(outcome["ok"])
        resumed = load_goal(goal["goal_id"])
        assert resumed is not None
        self.assertEqual(resumed["plan"]["steps"][0]["state"], "blocked")
        self.assertIn("reconciliation", resumed["plan"]["steps"][0]["reason"])

    def test_resume_redispatches_only_effect_free_interrupted_steps(self) -> None:
        from unittest import mock

        goal = self._goal(state="paused")
        goal["plan"]["steps"][0]["state"] = "interrupted"
        goal["plan"]["steps"][0]["turn_id"] = "turn-z"
        save_goal(goal)
        with mock.patch("core.goal_executor._step_events", return_value=[]):
            outcome = request_resume(goal["goal_id"])
        self.assertTrue(outcome["ok"])
        resumed = load_goal(goal["goal_id"])
        assert resumed is not None
        self.assertEqual(resumed["plan"]["steps"][0]["state"], "pending")

    def test_stop_is_terminal_and_refuses_recontrol(self) -> None:
        goal = self._goal(state="running")
        outcome = request_stop(goal["goal_id"])
        self.assertTrue(outcome["ok"])
        stopped = load_goal(goal["goal_id"])
        assert stopped is not None
        self.assertEqual(stopped["state"], "cancelled")
        self.assertIsNotNone(stopped["ended_at"])
        self.assertEqual(request_stop(goal["goal_id"])["ok"], False)
        self.assertEqual(request_pause(goal["goal_id"])["ok"], False)
        self.assertEqual(request_resume(goal["goal_id"])["ok"], False)

    def test_controls_refuse_unknown_goal(self) -> None:
        self.assertEqual(request_pause("nope")["ok"], False)
        self.assertEqual(request_resume("nope")["ok"], False)
        self.assertEqual(request_stop("nope")["ok"], False)

    def test_retry_applies_only_to_holding_states(self) -> None:
        goal = self._goal(state="running")
        self.assertEqual(retry_waiting_step(goal["goal_id"])["ok"], False)
        goal["state"] = "waiting_approval"
        goal["current_step_id"] = "s1"
        goal["plan"]["steps"][0]["state"] = "waiting_approval"
        save_goal(goal)
        # Without a recorded pending approval, the retry refuses honestly instead of
        # claiming a token was used.
        outcome = retry_waiting_step(goal["goal_id"])
        self.assertEqual(outcome["ok"], False)
        self.assertIn("no approved action", outcome["error"])
        # An arbitrary string is not an approval from the existing authority.
        outcome = retry_waiting_step(goal["goal_id"], approval_token="tok-123")
        self.assertFalse(outcome["ok"])
        self.assertEqual(load_goal(goal["goal_id"])["state"], "waiting_approval")

    def test_worker_token_armed_for_one_step_one_use(self) -> None:
        from core.goal_workflow import _GoalWorker

        worker = _GoalWorker("g", "chat", epoch=1)
        worker.arm_approval_token("s1", "tok")
        self.assertEqual(worker.take_approval_token("s2"), "", "a token never applies to another step")
        self.assertEqual(worker.take_approval_token("s1"), "tok")
        self.assertEqual(worker.take_approval_token("s1"), "", "one use only")


class ReconciliationTests(GoalWorkflowTestBase):
    def test_orphaned_running_goal_reads_interrupted(self) -> None:
        goal = self._goal(state="running")
        goal["plan"]["steps"][0]["state"] = "running"
        save_goal(goal)
        self.assertTrue(reconcile_goal_if_orphaned(goal["goal_id"]))
        reconciled = load_goal(goal["goal_id"])
        assert reconciled is not None
        self.assertEqual(reconciled["state"], "interrupted")
        self.assertEqual(reconciled["plan"]["steps"][0]["state"], "interrupted")
        # second pass is a no-op: interrupted is never re-touched
        self.assertFalse(reconcile_goal_if_orphaned(goal["goal_id"]))

    def test_holding_and_terminal_goals_are_never_reconciled(self) -> None:
        waiting = self._goal(state="waiting_approval")
        self.assertFalse(reconcile_goal_if_orphaned(waiting["goal_id"]))
        self.assertEqual(load_goal(waiting["goal_id"])["state"], "waiting_approval")
        done = self._goal(state="completed")
        self.assertFalse(reconcile_goal_if_orphaned(done["goal_id"]))
        self.assertEqual(load_goal(done["goal_id"])["state"], "completed")
        cancelled = self._goal(state="cancelled")
        self.assertFalse(reconcile_goal_if_orphaned(cancelled["goal_id"]))


class EventMappingTests(GoalWorkflowTestBase):
    def test_goal_event_maps_with_goal_payload(self) -> None:
        mapped = build_task_event({"event_type": "goal_updated", "message": "step started", "goal": {"state": "running", "completed": 1}, "seq": 7})
        assert mapped is not None
        self.assertEqual(mapped["type"], "goal.updated")
        self.assertEqual(mapped["goal"]["state"], "running")
        self.assertEqual(mapped["seq"], 7)

    def test_unknown_events_stay_dropped(self) -> None:
        self.assertIsNone(build_task_event({"event_type": "goal_secret_internal"}))

    def test_emit_goal_event_writes_ledger_rows(self) -> None:
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            list_runtime_session_events,
            reset_runtime_continuity_state,
        )

        events_db = self._db_path  # shared store keeps the test simple and honest
        configure_runtime_continuity_db_path(events_db)
        try:
            goal = self._goal()
            view = emit_goal_event({"runtime_session_id": "chat-a"}, goal, "Goal paused")
            rows = list_runtime_session_events("chat-a", after_seq=0, limit=50)
            goal_rows = [r for r in rows if r.get("event_type") == "goal_updated"]
            self.assertEqual(len(goal_rows), 1)
            self.assertEqual(goal_rows[0]["goal"]["state"], view["state"])
        finally:
            reset_runtime_continuity_state()


class RegistryTests(GoalWorkflowTestBase):
    def test_goals_status_registered_read_only_with_goal_alias(self) -> None:
        from core.command_registry.registry import registry

        reg = registry()
        spec = reg.lookup("goals.status")
        assert spec is not None
        self.assertEqual(spec.effects, "read_only")
        self.assertIn("goal", spec.aliases)
        # Lifecycle controls deliberately have no registry command (single chat-bound door).
        self.assertIsNone(reg.lookup("goals.pause"))
        self.assertIsNone(reg.lookup("goals.stop"))
