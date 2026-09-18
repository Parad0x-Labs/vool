"""Step 10: exact retry -- carry-forward vs rerun classification, and a full execution proving
carried-forward subtasks are never re-fetched while transient failures genuinely rerun.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.agent_runtime.attempt_retry import execute_attempt_retry, plan_retry_generation
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_attempt_subtasks,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class PlanRetryGenerationTests(unittest.TestCase):
    """Pure-function tests -- no I/O."""

    def test_succeeded_is_carried_forward(self) -> None:
        carry, rerun = plan_retry_generation([{"lifecycle_state": "SUCCEEDED"}], plan_id="p")
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_transient_failed_is_rerun(self) -> None:
        carry, rerun = plan_retry_generation(
            [{"lifecycle_state": "FAILED", "failure_class": "transient"}], plan_id="p",
        )
        self.assertEqual(len(carry), 0)
        self.assertEqual(len(rerun), 1)

    def test_unsupported_entity_is_carried_forward_not_rerun(self) -> None:
        """Step 10 correction: do not automatically retry UNSUPPORTED_ENTITY."""
        carry, rerun = plan_retry_generation(
            [{"lifecycle_state": "UNSUPPORTED_ENTITY", "failure_class": "unsupported"}], plan_id="p",
        )
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_waiting_approval_is_carried_forward_not_rerun(self) -> None:
        carry, rerun = plan_retry_generation([{"lifecycle_state": "WAITING_APPROVAL"}], plan_id="p")
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_cancelled_is_carried_forward_not_rerun(self) -> None:
        carry, rerun = plan_retry_generation([{"lifecycle_state": "CANCELLED"}], plan_id="p")
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_sabotage_marking_succeeded_as_auto_rerun_reruns_a_fresh_result(self) -> None:
        """Proves the state-based split is load-bearing: if a SUCCEEDED subtask were (wrongly)
        classified as rerun-eligible, it would be re-executed instead of carried forward. This
        directly exercises the exact production code path the checkpoint asked to sabotage:
        "rerun all successful subtasks"."""
        import core.agent_runtime.attempt_retry as attempt_retry_module

        original = attempt_retry_module._AUTO_RERUN_STATES
        try:
            attempt_retry_module._AUTO_RERUN_STATES = {"FAILED", "SUCCEEDED"}
            _carry, rerun = plan_retry_generation([{"lifecycle_state": "SUCCEEDED", "failure_class": ""}], plan_id="p")
            self.assertEqual(len(rerun), 1, "sabotage should have forced the SUCCEEDED subtask into rerun")
        finally:
            attempt_retry_module._AUTO_RERUN_STATES = original
        # Control: with the real code, the same input is carried forward, never rerun.
        _carry, rerun = plan_retry_generation([{"lifecycle_state": "SUCCEEDED", "failure_class": ""}], plan_id="p")
        self.assertEqual(len(rerun), 0)


class ExecuteAttemptRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "retry.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _make_parent(self) -> tuple[dict, list[dict]]:
        parent = create_runtime_attempt(
            session_id="s1",
            original_request="market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas",
            answer_mode="LIVE_DATA", plan_id="plan-orig",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="SUCCEEDED",
            result_summary={"price": 4320.7, "currency": "USD", "change_24h_pct": 0.36, "source": "Yahoo Finance", "source_url": "https://x", "retrieved_at": "2026-08-06 07:30 UTC"},
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:bitcoin", plan_id="plan-orig",
            operation="market_quote", entity_type="crypto", entity_key="bitcoin",
            arguments={"asset_key": "bitcoin", "kind": "crypto"}, lifecycle_state="SUCCEEDED",
            result_summary={"price": 64781.0, "currency": "USD", "change_24h_pct": 0.43, "source": "CoinGecko", "source_url": "https://x", "retrieved_at": "2026-08-06 16:20 UTC"},
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:weather:atlantisxyzabc123", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="atlantisxyzabc123",
            arguments={"location": "atlantisxyzabc123"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
            retryable=True, retry_reason="transient tool failure",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="SUCCEEDED",
            result_summary={"condition": "Sunny", "temperature_c": 28.0, "high_c": 30.0, "low_c": 19.0, "source": "wttr.in", "source_url": "https://x", "observed_at": "02:35 PM"},
        )
        finalized = finalize_runtime_attempt(parent["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])
        return finalized, subtasks

    def _fake_weather(self, location: str, **_kwargs):
        from core.weather_result_contract import WeatherResult

        return WeatherResult(
            location=location, place_label=location, condition="Moderate rain", temperature_c=21.0,
            feels_like_c=20.0, humidity_pct=80.0, wind_kmph=10.0, source_label="wttr.in",
            source_url="https://x", observed_at="03:00 PM",
        )

    def test_retry_carries_forward_three_and_reruns_one(self) -> None:
        parent, subtasks = self._make_parent()
        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")
        self.assertEqual(result["carried_forward_count"], 3)
        self.assertEqual(result["rerun_count"], 1)
        self.assertEqual(result["attempt"]["lifecycle_state"], "SUCCEEDED")

    def test_retry_never_refetches_carried_forward_market_subtasks(self) -> None:
        """Tripwire proof: if a carried-forward subtask were re-fetched, this raises."""
        parent, subtasks = self._make_parent()

        def tripwire(*_a, **_k):
            raise AssertionError("carried-forward subtask was re-fetched")

        with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=tripwire), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=tripwire), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather):
            execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")  # must not raise

    def test_retry_creates_new_attempt_linked_to_parent_and_root(self) -> None:
        parent, subtasks = self._make_parent()
        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")
        new_attempt = result["attempt"]
        self.assertNotEqual(new_attempt["attempt_id"], parent["attempt_id"])
        self.assertEqual(new_attempt["parent_attempt_id"], parent["attempt_id"])
        self.assertEqual(new_attempt["root_attempt_id"], parent["attempt_id"])
        self.assertEqual(new_attempt["execution_generation"], parent["execution_generation"] + 1)
        self.assertEqual(new_attempt["original_request_hash"], parent["original_request_hash"])
        # The prior attempt's row itself is untouched.
        prior_subtasks = list_runtime_attempt_subtasks(parent["attempt_id"], execution_generation=1)
        self.assertEqual(len(prior_subtasks), 4)

    def test_new_generation_subtask_rows_are_filterable_by_the_attempts_own_generation(self) -> None:
        """Regression: found live -- a retry-created attempt's subtask rows were written with the
        upsert function's DEFAULT execution_generation=1 instead of the new attempt's ACTUAL
        generation (2, 3, ...), so a generation-filtered lookup (exactly what the follow-up
        resolver uses) silently found zero rows for a fully-populated, correctly-answered retry.
        The parent's own subtasks (also generation 1) coincidentally matched the same default,
        which is why this stayed hidden until a SECOND retry generation (2) was queried directly."""
        parent, subtasks = self._make_parent()
        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")
        new_attempt = result["attempt"]
        self.assertEqual(new_attempt["execution_generation"], 2)
        filtered = list_runtime_attempt_subtasks(new_attempt["attempt_id"], execution_generation=2)
        unfiltered = list_runtime_attempt_subtasks(new_attempt["attempt_id"])
        self.assertEqual(len(unfiltered), 4)
        self.assertEqual(
            len(filtered), 4,
            "generation-filtered lookup found 0 of 4 rows -- subtask rows were not stamped with "
            "the retry's actual execution_generation",
        )

    def test_carried_forward_rows_record_provenance(self) -> None:
        parent, subtasks = self._make_parent()
        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=self._fake_weather):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        carried = [s for s in new_subtasks if s["carried_forward_from_attempt_id"]]
        rerun = [s for s in new_subtasks if s["rerun_reason"]]
        self.assertEqual(len(carried), 3)
        self.assertEqual(len(rerun), 1)
        self.assertEqual(carried[0]["carried_forward_from_attempt_id"], parent["attempt_id"])

    def test_second_failure_on_rerun_never_fabricates_a_result(self) -> None:
        """Even when the RETRY itself fails again, the answer stays honest -- no fabricated price
        or forecast, matching the same fail-closed guarantee as the very first attempt."""
        parent, subtasks = self._make_parent()

        def still_failing(location: str, **_kwargs):
            raise Exception("still down")

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=still_failing):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-retry-1")
        self.assertEqual(result["attempt"]["lifecycle_state"], "PARTIAL_SUCCESS")
        self.assertIn("still down", result["rendered"])
        self.assertNotIn("unavailable", result["rendered"].split("Atlantisxyzabc123")[0])  # Bitcoin/Gold untouched


if __name__ == "__main__":
    unittest.main()
