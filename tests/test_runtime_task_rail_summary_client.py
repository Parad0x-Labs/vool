from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from core.runtime_task_rail_summary_client import (
    RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT,
)


class RuntimeTaskRailSummaryClientTests(unittest.TestCase):
    def _summary(self, *, session: dict | None = None, events: list[dict] | None = None) -> dict:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is required for the task-rail presentation probe")
        program = (
            RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
            + "\nconsole.log(JSON.stringify(buildSummary("
            + json.dumps(session or {"status": "completed"})
            + ", "
            + json.dumps(events or [])
            + ")));"
        )
        result = subprocess.run([node, "-e", program], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_summary_script_keeps_stage_and_receipt_contract(self) -> None:
        script = RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
        self.assertIn("function buildSummary(session, events)", script)
        self.assertIn("received: Boolean(serverStages.received)", script)
        self.assertIn("packet: Boolean(serverStages.packet)", script)
        self.assertIn("bundle: Boolean(serverStages.bundle)", script)
        self.assertIn("result: Boolean(serverStages.result)", script)
        self.assertIn("stopReason", script)
        self.assertIn("queryCompletedCount", script)
        self.assertIn("artifactRows", script)
        self.assertIn("approvalState", script)
        self.assertIn("rollbackState", script)
        self.assertIn("verifierState", script)
        self.assertIn("reviewState", script)
        self.assertIn("validationState", script)
        self.assertIn("toolReceiptCount", script)

    def test_model_review_and_validation_are_projected_independently(self) -> None:
        events = [
            {"event_type": "model_lane_verifier_completed", "task_role": "verifier", "status": "completed"},
            {"event_type": "tool_failed", "tool_name": "workspace.run_tests", "status": "failed"},
        ]
        summary = self._summary(events=events)
        self.assertEqual(summary["reviewState"], "passed")
        self.assertEqual(summary["validationState"], "failed")

    def test_live_model_review_does_not_promote_validation(self) -> None:
        summary = self._summary(
            events=[{"event_type": "model_lane_verifier_completed", "status": "completed"}]
        )

        self.assertEqual(summary["reviewState"], "passed")
        self.assertEqual(summary["validationState"], "not_run")

    def test_live_model_review_outcomes_preserve_verdict_vs_execution_health(self) -> None:
        cases = {
            "model_lane_verifier_completed": "passed",
            "model_lane_verifier_flagged": "flagged",
            "model_lane_verifier_blocked": "blocked",
            "model_lane_verifier_degraded": "degraded",
            "model_lane_verifier_failed": "runtime_failed",
        }
        for event_type, expected in cases.items():
            with self.subTest(event_type=event_type):
                summary = self._summary(events=[{"event_type": event_type, "status": "failed"}])
                self.assertEqual(summary["reviewState"], expected)
                self.assertEqual(summary["validationState"], "not_run")
                if expected != "flagged":
                    self.assertNotEqual(summary["reviewState"], "flagged")

    def test_live_validation_does_not_promote_model_review(self) -> None:
        summary = self._summary(
            events=[
                {
                    "event_type": "task_envelope_step_completed",
                    "task_role": "verifier",
                    "intent": "workspace.run_tests",
                    "status": "completed",
                }
            ]
        )

        self.assertEqual(summary["reviewState"], "not_run")
        self.assertEqual(summary["validationState"], "passed")

    def test_ambiguous_legacy_verifier_state_is_not_promoted(self) -> None:
        summary = self._summary(
            session={
                "status": "completed",
                "execution_history": {"bounded_execution": {"verifier_state": "passed"}},
            }
        )

        self.assertEqual(summary["verifierState"], "passed")
        self.assertEqual(summary["reviewState"], "not_run")
        self.assertEqual(summary["validationState"], "not_run")

    def test_legacy_collapsed_model_review_failure_is_unavailable_not_flagged(self) -> None:
        summary = self._summary(
            session={
                "status": "completed",
                "execution_history": {
                    "bounded_execution": {
                        "verifier_state": "failed",
                        "model_review_state": "failed",
                    }
                },
            }
        )

        self.assertEqual(summary["verifierState"], "failed")
        self.assertEqual(summary["reviewState"], "unavailable")
        self.assertNotEqual(summary["reviewState"], "flagged")

    def test_legacy_state_with_typed_provenance_maps_only_that_dimension(self) -> None:
        legacy_session = {
            "status": "completed",
            "execution_history": {"bounded_execution": {"verifier_state": "passed"}},
        }
        validation = self._summary(
            session=legacy_session,
            events=[
                {
                    "event_type": "tool_executed",
                    "tool_name": "workspace.run_lint",
                    "status": "completed",
                }
            ],
        )
        review = self._summary(
            session=legacy_session,
            events=[{"event_type": "model_lane_verifier_completed", "status": "completed"}],
        )

        self.assertEqual((validation["reviewState"], validation["validationState"]), ("not_run", "passed"))
        self.assertEqual((review["reviewState"], review["validationState"]), ("passed", "not_run"))

    def test_saved_distinct_states_reach_the_client_without_live_events(self) -> None:
        cases = (
            ({"model_review_state": "passed"}, ("passed", "not_run")),
            ({"validation_state": "passed"}, ("not_run", "passed")),
            (
                {"model_review_state": "flagged", "validation_state": "passed"},
                ("flagged", "passed"),
            ),
            ({"model_review_state": "blocked"}, ("blocked", "not_run")),
            ({"model_review_state": "degraded"}, ("degraded", "not_run")),
            ({"model_review_state": "runtime_failed"}, ("runtime_failed", "not_run")),
        )
        for bounded, expected in cases:
            with self.subTest(bounded=bounded):
                summary = self._summary(
                    session={
                        "status": "completed",
                        "execution_history": {"bounded_execution": bounded},
                    }
                )
                self.assertEqual((summary["reviewState"], summary["validationState"]), expected)

    def test_summary_script_accumulates_lane_tokens_and_cost(self) -> None:
        # The cockpit needs per-session lane (local vs cloud), tokens, and cost derived from the
        # model_usage / cost_class runtime events, exposed on the summary for the stat cards.
        script = RUNTIME_TASK_RAIL_SUMMARY_CLIENT_SCRIPT
        self.assertIn("laneCostClass", script)
        self.assertIn("event.event_type === 'model_usage'", script)
        self.assertIn("event.cost_class", script)
        self.assertIn("promptTokens", script)
        self.assertIn("outputTokens", script)
        self.assertIn("usdActual", script)
        self.assertIn("lane: laneCostClass === 'paid_cloud' ? 'cloud'", script)


if __name__ == "__main__":
    unittest.main()
