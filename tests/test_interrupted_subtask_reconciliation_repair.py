"""Repair 6 (Mnemosyne review, 2026-08-06): the parent attempt transitions to ABANDONED on
restart, but its own PLANNED/RUNNING subtask stayed PLANNED/RUNNING forever -- no result, no
failure reason, omitted entirely from the failure explanation, and carried FORWARD unchanged by a
retry (as if it were completed work) instead of being rerun. Fixed: the restart-abandon sweep now
transitions any PLANNED/RUNNING subtask of an attempt it abandons into FAILED /
failure_class=interrupted / retryable, in the SAME per-attempt transaction as the parent's own
transition, and names it in the attempt's retry_from_stage. Retry auto-reruns it like any other
transient failure.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.agent_runtime.attempt_followup import render_attempt_failure_explanation
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    get_runtime_attempt,
    list_runtime_attempt_subtasks,
    mark_stale_runtime_attempts_abandoned,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class InterruptedSubtaskReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "interrupted.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _make_attempt_with_one_running_and_one_succeeded(self) -> dict:
        attempt = create_runtime_attempt(
            session_id="s1", original_request="gold price and weather in Kaunas", answer_mode="LIVE_DATA",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="SUCCEEDED",
            result_summary={"price": 4320.7, "currency": "USD", "source": "Yahoo Finance", "retrieved_at": "2026-08-06 07:30 UTC"},
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="RUNNING",
        )
        return attempt

    def test_restart_sweep_transitions_running_subtask_to_failed_interrupted(self) -> None:
        attempt = self._make_attempt_with_one_running_and_one_succeeded()
        # The parent itself must ALSO be non-terminal for the sweep to touch it.
        from core.runtime_continuity import update_runtime_attempt

        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")

        abandoned_count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(abandoned_count, 1)

        reloaded = get_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(reloaded["lifecycle_state"], "ABANDONED")
        self.assertEqual(reloaded["retry_from_stage"], "p:weather:kaunas")

        subtasks = {s["subtask_id"]: s for s in list_runtime_attempt_subtasks(attempt["attempt_id"])}
        interrupted = subtasks["p:weather:kaunas"]
        self.assertEqual(interrupted["lifecycle_state"], "FAILED")
        self.assertEqual(interrupted["failure_class"], "interrupted")
        self.assertTrue(interrupted["retryable"])
        self.assertIn("restart", interrupted["failure_reason"].lower())

        # The completed sibling is untouched.
        succeeded = subtasks["p:market:gold"]
        self.assertEqual(succeeded["lifecycle_state"], "SUCCEEDED")
        self.assertEqual(succeeded["result_summary"].get("price"), 4320.7)

    def test_failure_explanation_names_the_interrupted_subtask_as_retryable(self) -> None:
        attempt = self._make_attempt_with_one_running_and_one_succeeded()
        from core.runtime_continuity import update_runtime_attempt

        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")
        mark_stale_runtime_attempts_abandoned()

        reloaded = get_runtime_attempt(attempt["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(attempt["attempt_id"])
        explanation = render_attempt_failure_explanation(reloaded, subtasks)

        self.assertIn("Kaunas", explanation)
        self.assertIn("restart", explanation.lower())
        self.assertIn("Gold", explanation)  # successful sibling retained, per the existing renderer
        self.assertIn("retried", explanation.lower())

    def test_retry_reruns_the_interrupted_subtask_instead_of_carrying_it_forward(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import update_runtime_attempt
        from core.weather_result_contract import WeatherResult

        attempt = self._make_attempt_with_one_running_and_one_succeeded()
        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")
        mark_stale_runtime_attempts_abandoned()

        parent = get_runtime_attempt(attempt["attempt_id"])
        parent_subtasks = list_runtime_attempt_subtasks(attempt["attempt_id"])

        def fake_weather(location: str, **_kwargs):
            return WeatherResult(
                location=location, place_label=location, condition="Sunny", temperature_c=28.0,
                feels_like_c=27.0, humidity_pct=40.0, wind_kmph=8.0, source_label="wttr.in",
                source_url="https://x", observed_at="02:35 PM",
            )

        def tripwire(*_a, **_k):
            raise AssertionError("carried-forward SUCCEEDED subtask was re-fetched")

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=tripwire):
            result = execute_attempt_retry(
                parent, parent_subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-after-restart",
            )

        self.assertEqual(result["carried_forward_count"], 1)  # gold, untouched
        self.assertEqual(result["rerun_count"], 1)  # the interrupted weather subtask
        self.assertIn("Kaunas", result["rendered"])
        self.assertIn("Sunny", result["rendered"])
        # A new generation mints its own plan id, so subtask ids are not stable across generations
        # (see the carried-forward provenance fields for cross-generation lineage instead).
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        reran = next(s for s in new_subtasks if s.get("entity_key") == "kaunas")
        carried = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(reran["lifecycle_state"], "SUCCEEDED")
        self.assertTrue(reran["rerun_reason"])
        self.assertIn("interrupted", reran["rerun_reason"])
        self.assertEqual(carried["carried_forward_from_attempt_id"], attempt["attempt_id"])

    def test_two_interrupted_subtasks_in_the_same_attempt_are_both_named(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="gold and silver", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={}, lifecycle_state="PLANNED",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:market:silver", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="silver",
            arguments={}, lifecycle_state="RUNNING",
        )
        from core.runtime_continuity import update_runtime_attempt

        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")
        mark_stale_runtime_attempts_abandoned()

        reloaded = get_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(reloaded["retry_from_stage"], "p:market:gold,p:market:silver")
        subtasks = {s["subtask_id"]: s for s in list_runtime_attempt_subtasks(attempt["attempt_id"])}
        self.assertEqual(subtasks["p:market:gold"]["failure_class"], "interrupted")
        self.assertEqual(subtasks["p:market:silver"]["failure_class"], "interrupted")

    def test_waiting_approval_sibling_is_not_treated_as_interrupted(self) -> None:
        """WAITING_APPROVAL is a deliberate pause, not a crash -- when the PARENT attempt is
        genuinely swept (e.g. one of its OTHER subtasks was still RUNNING), a sibling subtask
        sitting at WAITING_APPROVAL must be left exactly as it is, never force-failed alongside it."""
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:approval", plan_id="plan-orig",
            operation="workspace.write_file", arguments={}, lifecycle_state="WAITING_APPROVAL",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="RUNNING",
        )
        from core.runtime_continuity import update_runtime_attempt

        update_runtime_attempt(attempt["attempt_id"], lifecycle_state="RUNNING")
        abandoned_count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(abandoned_count, 1)

        subtasks = {s["subtask_id"]: s for s in list_runtime_attempt_subtasks(attempt["attempt_id"])}
        self.assertEqual(subtasks["p:approval"]["lifecycle_state"], "WAITING_APPROVAL")
        self.assertEqual(subtasks["p:approval"]["failure_class"], "")
        self.assertEqual(subtasks["p:weather:kaunas"]["lifecycle_state"], "FAILED")
        self.assertEqual(subtasks["p:weather:kaunas"]["failure_class"], "interrupted")


if __name__ == "__main__":
    unittest.main()
