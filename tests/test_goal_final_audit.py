"""Goal lifecycle regressions: real store/controller, controlled agent seam.

These cases are final-audit regression pins, not independent model holdouts.
Real daemon/provider-boundary acceptance remains a separate journey.
"""
from __future__ import annotations

import json
import threading
import time
from unittest import mock

from core import goal_workflow as store
from core.goal_executor import execute_goal, reconcile_interrupted_step, retry_waiting_step
from core.web.api import goal_api
from tests.test_goal_workflow import GoalWorkflowTestBase


class GoalFinalAuditTests(GoalWorkflowTestBase):
    def _one_step(self, *, verify=None):
        goal = self._goal()
        raw = {"id": "s1", "title": "Perform the requested work"}
        if verify:
            raw["verify"] = verify
        goal["plan"] = store.build_plan([raw], provenance="model")
        store.save_goal(goal)
        return goal

    def test_cancelled_step_without_verification_does_not_complete(self):
        goal = self._one_step()
        worker = store._GoalWorker(goal["goal_id"], "chat-a")
        store.register_worker(worker)

        def interrupted(*_args, **_kwargs):
            store.request_pause(goal["goal_id"])
            return {"task_outcome": "failed", "response": "Cancelled before any work"}

        with mock.patch("core.web.api.runtime.run_agent", interrupted):
            execute_goal(object(), goal["goal_id"], base_context={}, model="test")
        fresh = store.load_goal(goal["goal_id"])
        self.assertEqual(fresh["state"], "paused")
        self.assertEqual(fresh["plan"]["steps"][0]["state"], "interrupted")
        self.assertEqual(store.completed_step_count(fresh["plan"]), (0, 1))

    def test_recovery_without_verify_does_not_replay_recorded_effect(self):
        goal = self._one_step()
        step = goal["plan"]["steps"][0]
        step.update(state="interrupted", turn_id="settled-effect")
        with mock.patch("core.goal_executor._step_events", return_value=[
            {"event_type": "tool_executed", "tool_name": "workspace.write_file", "path": "append.txt"}
        ]):
            reconcile_interrupted_step(goal, step)
        self.assertEqual(step["state"], "blocked")
        self.assertIn("reconcil", step["reason"])

    def test_recovery_checks_other_actions_in_the_same_turn(self):
        goal = self._one_step(verify={"tool": "workspace.read_file", "path_contains": "report.txt"})
        step = goal["plan"]["steps"][0]
        step.update(state="interrupted", turn_id="partially-committed")
        with mock.patch("core.goal_executor._step_events", return_value=[
            {"event_type": "tool_executed", "tool_name": "workspace.write_file", "path": "append.txt"}
        ]):
            reconcile_interrupted_step(goal, step)
        self.assertEqual(step["state"], "blocked")

    def test_replanning_id_collision_retains_completed_receipt_and_dependencies(self):
        goal = self._one_step()
        old = goal["plan"]["steps"][0]
        old.update(state="completed", turn_id="original-receipt", attempts=1)
        goal["state"] = "paused"
        store.save_goal(goal)
        store.request_revise(goal["goal_id"], "Write a different file, then inspect it")
        proposed = [{"id": "s1", "title": "Write the new file"},
                    {"id": "s2", "title": "Inspect the new file", "depends_on": ["s1"]}]
        with mock.patch("core.goal_executor.run_planner_turn", return_value=(proposed, "")), mock.patch(
            "core.web.api.runtime.run_agent", return_value={"task_outcome": "success", "fulfillment_outcome": {"fulfillment_status": "fulfilled"}}
        ) as calls:
            execute_goal(object(), goal["goal_id"], base_context={}, model="test")
        fresh = store.load_goal(goal["goal_id"])
        steps = fresh["plan"]["steps"]
        retained = next((s for s in steps if s.get("turn_id") == "original-receipt"), None)
        self.assertIsNotNone(retained)
        self.assertEqual(retained["state"], "completed")
        first_new = next(s for s in steps if s["title"] == "Write the new file")
        second_new = next(s for s in steps if s["title"] == "Inspect the new file")
        self.assertNotEqual(first_new["id"], retained["id"])
        self.assertEqual(second_new["depends_on"], [first_new["id"]])
        self.assertEqual(calls.call_count, 2)

    def test_replanning_same_completed_work_does_not_dispatch_it_again(self):
        goal = self._one_step()
        old = goal["plan"]["steps"][0]
        old.update(state="completed", turn_id="original-receipt", attempts=1)
        goal["state"] = "paused"
        store.save_goal(goal)
        store.request_revise(goal["goal_id"], "Keep the requested work")
        with mock.patch("core.goal_executor.run_planner_turn", return_value=([{ "id": "s1", "title": old["title"]}], "")), mock.patch(
            "core.web.api.runtime.run_agent", return_value={"task_outcome": "success", "fulfillment_outcome": {"fulfillment_status": "fulfilled"}}
        ) as calls:
            execute_goal(object(), goal["goal_id"], base_context={}, model="test")
        fresh = store.load_goal(goal["goal_id"])
        self.assertEqual(fresh["plan"]["steps"][0]["turn_id"], "original-receipt")
        self.assertEqual(calls.call_count, 0)

    def test_planning_is_registered_on_the_worker_for_pause(self):
        goal = self._goal(steps="none")
        worker = store._GoalWorker(goal["goal_id"], goal["session_id"])
        store.register_worker(worker)
        observed = []

        def planning(*_args, **kwargs):
            turn = worker.current_step_turn()
            store.request_pause(goal["goal_id"])
            observed.append((turn, kwargs["source_context"].get("cancel_event", threading.Event()).is_set()))
            return {"response": '{"steps":[{"id":"s1","title":"work"}]}'}

        with mock.patch("core.web.api.runtime.run_agent", planning):
            execute_goal(object(), goal["goal_id"], base_context={}, model="test")
        self.assertTrue(observed[0][0])
        self.assertTrue(observed[0][1])
        self.assertEqual(store.load_goal(goal["goal_id"])["state"], "paused")

    def test_simultaneous_spawns_admit_exactly_one_worker(self):
        goal = self._one_step()
        entered = threading.Event()
        release_claim = threading.Event()
        release_worker = threading.Event()
        claims = []
        executions = []
        real_claim = store.claim_worker_epoch

        def hold_claim(goal_id):
            claims.append(goal_id)
            entered.set()
            release_claim.wait(3)
            return real_claim(goal_id)

        def hold_execution(_runtime, worker, *_args):
            executions.append(worker)
            release_worker.wait(3)
            store.unregister_worker(goal["goal_id"], worker)

        results = []
        def start():
            results.append(goal_api.spawn_goal_worker(object(), goal, base_context={}, model="test"))
        with mock.patch.object(goal_api, "claim_worker_epoch", hold_claim), mock.patch.object(goal_api, "_run_worker", hold_execution):
            first = threading.Thread(target=start)
            second = threading.Thread(target=start)
            first.start()
            self.assertTrue(entered.wait(3))
            second.start()
            time.sleep(0.05)
            release_claim.set()
            first.join(3)
            second.join(3)
            release_worker.set()
            for w in executions:
                w.finished.wait(3)
        self.assertEqual(len(claims), 1)
        self.assertEqual(len([r for r in results if r is not None]), 1)

    def test_answer_verification_is_preserved_by_plan_normalization(self):
        plan = store.build_plan([{"title": "Report the computation", "verify": {"answer_contains": "42"}}], provenance="model")
        self.assertEqual(json.loads(plan["steps"][0]["verify"])["answer_contains"], "42")

    def test_approved_step_can_retry_with_the_original_permission_identity(self):
        from core.mode_permission_policy import decide_tool_call, resolve_approval
        goal = self._one_step()
        turn_id = f'{goal["goal_id"]}:s1:a1'
        context = {"runtime_session_id": goal["session_id"], "cancel_turn_id": turn_id, "operating_mode": "manual", "workspace": str(self._tmp.name)}
        args = {"path": "approved.txt", "content": "approved bytes"}
        ask = decide_tool_call(intent="workspace.write_file", arguments=args, task_id=turn_id, source_context=context)
        self.assertFalse(ask.allowed)
        self.assertIsNotNone(ask.approval_request)
        token = ask.approval_request["approval_id"]
        self.assertIsNotNone(resolve_approval(token, decision="allow", scope="once"))
        step = goal["plan"]["steps"][0]
        step.update(state="waiting_approval", turn_id=turn_id, attempts=1, approval_id=token)
        goal.update(state="waiting_approval", current_step_id="s1")
        goal["exec_context"] = {"workspace": str(self._tmp.name)}
        store.save_goal(goal)
        retry = retry_waiting_step(goal["goal_id"])
        self.assertTrue(retry["ok"], retry)
        epoch = store.claim_worker_epoch(goal["goal_id"])
        worker = store._GoalWorker(goal["goal_id"], goal["session_id"], epoch=epoch)
        worker.arm_approval_token("s1", retry["pending_approval_token"])
        store.register_worker(worker)
        decisions = []
        def action(_runtime, _prompt, **kwargs):
            ctx = kwargs["source_context"]
            decision = decide_tool_call(intent="workspace.write_file", arguments=args, task_id=ctx["cancel_turn_id"], source_context=ctx)
            decisions.append(decision.allowed)
            return {"task_outcome": "success" if decision.allowed else "pending_approval", "fulfillment_outcome": {"fulfillment_status": "fulfilled" if decision.allowed else "blocked", "failure_codes": [] if decision.allowed else ["approval_required"]}}
        with mock.patch("core.web.api.runtime.run_agent", action):
            execute_goal(object(), goal["goal_id"], base_context=context, model="test")
        self.assertEqual(decisions, [True])
        self.assertEqual(store.load_goal(goal["goal_id"])["state"], "completed")

    def test_resume_while_cancelled_turn_is_settling_queues_one_successor(self):
        goal = self._one_step()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def held(_runtime, _prompt, **kwargs):
            calls.append(kwargs["source_context"]["cancel_turn_id"])
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return {"task_outcome": "success", "fulfillment_outcome": {"fulfillment_status": "fulfilled"}}
        runtime = object()
        with mock.patch("core.web.api.runtime.run_agent", held):
            goal_api.spawn_goal_worker(runtime, goal, base_context={}, model="test")
            self.assertTrue(entered.wait(3))
            body = {"goal_id": goal["goal_id"]}
            self.assertEqual(goal_api.goal_control("pause", body, "chat-a", runtime=runtime)[0], 200)
            self.assertEqual(goal_api.goal_control("resume", body, "chat-a", runtime=runtime)[0], 200)
            release.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline and store.load_goal(goal["goal_id"])["state"] != "completed":
                time.sleep(0.03)
            self.assertEqual(store.load_goal(goal["goal_id"])["state"], "completed")
            self.assertEqual(len(calls), 2)

    def test_resume_cannot_reset_the_dispatch_allowance(self):
        goal = self._one_step()
        goal["plan"]["dispatches"] = store.MAX_GOAL_TURNS
        store.save_goal(goal)
        with mock.patch("core.web.api.runtime.run_agent") as calls:
            execute_goal(object(), goal["goal_id"], base_context={}, model="test")
        self.assertEqual(calls.call_count, 0)
        self.assertIn("dispatch budget", store.load_goal(goal["goal_id"])["last_reason"])
