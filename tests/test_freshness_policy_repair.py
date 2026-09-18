"""Repair 7 (Mnemosyne review, 2026-08-06): a successful market/weather result was ALWAYS carried
forward on retry, including a five-day-old one -- refresh_required/refresh_reason stayed at their
defaults everywhere. `evaluate_subtask_freshness`/`plan_retry_generation` are pure functions (no
I/O), so every test here uses a controlled `now_iso` rather than waiting in real time.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.agent_runtime.attempt_retry import evaluate_subtask_freshness, plan_retry_generation
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_attempt_subtasks,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations

_NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()


def _iso_minutes_ago(minutes: int) -> str:
    return (_NOW - timedelta(minutes=minutes)).isoformat()


class EvaluateSubtaskFreshnessTests(unittest.TestCase):
    """Pure-function tests -- no I/O, controlled timestamps only."""

    def test_immediate_result_is_not_stale(self) -> None:
        row = {"operation": "market_quote", "completed_at": _iso_minutes_ago(1)}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)

    def test_market_quote_past_its_ttl_is_stale(self) -> None:
        row = {"operation": "market_quote", "completed_at": _iso_minutes_ago(16)}
        refresh_required, reason = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)
        self.assertIn("market_quote", reason)

    def test_market_quote_five_days_old_is_stale(self) -> None:
        """The exact confirmed defect: a five-day-old result was always carried forward."""
        row = {"operation": "market_quote", "completed_at": (_NOW - timedelta(days=5)).isoformat()}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)

    def test_weather_lookup_has_a_longer_ttl_than_market_quote(self) -> None:
        row = {"operation": "weather_lookup", "completed_at": _iso_minutes_ago(30)}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertFalse(refresh_required, "30 minutes is within the weather TTL even though it exceeds the market TTL")

    def test_weather_lookup_past_its_ttl_is_stale(self) -> None:
        row = {"operation": "weather_lookup", "completed_at": _iso_minutes_ago(90)}
        refresh_required, reason = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertTrue(refresh_required)
        self.assertIn("weather_lookup", reason)

    def test_operation_with_no_defined_ttl_is_never_stale(self) -> None:
        """unsupported_market_entity and anything else without a TTL entry: never auto-refreshed."""
        row = {"operation": "unsupported_market_entity", "completed_at": (_NOW - timedelta(days=30)).isoformat()}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)

    def test_missing_completed_at_is_never_treated_as_stale(self) -> None:
        """Unknown age is not evidence of staleness -- carry forward rather than refresh on a guess."""
        row = {"operation": "market_quote", "completed_at": ""}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)

    def test_unparseable_completed_at_is_never_treated_as_stale(self) -> None:
        row = {"operation": "market_quote", "completed_at": "02:35 PM"}
        refresh_required, _ = evaluate_subtask_freshness(row, now_iso=_NOW_ISO)
        self.assertFalse(refresh_required)


class PlanRetryGenerationFreshnessTests(unittest.TestCase):
    def test_stale_succeeded_row_is_moved_to_rerun(self) -> None:
        row = {"lifecycle_state": "SUCCEEDED", "failure_class": "", "operation": "market_quote", "completed_at": _iso_minutes_ago(20)}
        carry, rerun = plan_retry_generation([row], plan_id="p", now_iso=_NOW_ISO)
        self.assertEqual(len(carry), 0)
        self.assertEqual(len(rerun), 1)
        self.assertIn("_refresh_reason", rerun[0])

    def test_fresh_succeeded_row_stays_carried_forward(self) -> None:
        row = {"lifecycle_state": "SUCCEEDED", "failure_class": "", "operation": "market_quote", "completed_at": _iso_minutes_ago(2)}
        carry, rerun = plan_retry_generation([row], plan_id="p", now_iso=_NOW_ISO)
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_no_now_iso_skips_freshness_entirely_preserving_prior_behavior(self) -> None:
        """Every EXISTING caller that does not pass now_iso is byte-for-byte unaffected -- a stale
        row is still carried forward exactly as it was before this repair."""
        row = {"lifecycle_state": "SUCCEEDED", "failure_class": "", "operation": "market_quote", "completed_at": (_NOW - timedelta(days=5)).isoformat()}
        carry, rerun = plan_retry_generation([row], plan_id="p")
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)

    def test_unsupported_entity_is_never_refreshed_regardless_of_age(self) -> None:
        row = {"lifecycle_state": "UNSUPPORTED_ENTITY", "failure_class": "unsupported", "operation": "unsupported_market_entity", "completed_at": (_NOW - timedelta(days=30)).isoformat()}
        carry, rerun = plan_retry_generation([row], plan_id="p", now_iso=_NOW_ISO)
        self.assertEqual(len(carry), 1)
        self.assertEqual(len(rerun), 0)


class ExecuteAttemptRetryFreshnessIntegrationTests(unittest.TestCase):
    """Full integration through execute_attempt_retry, proving a stale result is genuinely
    refetched (not just reclassified) and the fresh sibling is not."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "freshness.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_stale_market_result_is_refreshed_on_retry(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.live_quote_contract import LiveQuoteResult
        from core.runtime_continuity import get_runtime_attempt

        parent = create_runtime_attempt(session_id="s1", original_request="gold and bitcoin", answer_mode="LIVE_DATA")
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        now_stamp = datetime.now(timezone.utc).isoformat()
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="SUCCEEDED",
            result_summary={"price": 4000.0, "currency": "USD", "source": "Yahoo Finance", "retrieved_at": "5 days ago"},
            completed_at=five_days_ago,
        )
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:bitcoin", plan_id="plan-orig",
            operation="market_quote", entity_type="crypto", entity_key="bitcoin",
            arguments={"asset_key": "bitcoin", "kind": "crypto"}, lifecycle_state="SUCCEEDED",
            result_summary={"price": 64000.0, "currency": "USD", "source": "CoinGecko", "retrieved_at": "just now"},
            completed_at=now_stamp,
        )
        finalize_runtime_attempt(parent["attempt_id"])
        reloaded_parent = get_runtime_attempt(parent["attempt_id"])
        parent_subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4350.0, currency="USD",
                as_of="2026-08-06 12:00 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.1,
            )]

        def tripwire_crypto(*_a, **_k):
            raise AssertionError("fresh bitcoin result was re-fetched even though it was not stale")

        with mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity), \
             mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=tripwire_crypto):
            result = execute_attempt_retry(
                reloaded_parent, parent_subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-freshness",
            )

        self.assertEqual(result["carried_forward_count"], 1)  # bitcoin, fresh
        self.assertEqual(result["rerun_count"], 1)  # gold, stale
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        refreshed = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        carried = next(s for s in new_subtasks if s.get("entity_key") == "bitcoin")
        self.assertTrue(refreshed["refresh_required"])
        self.assertIn("market_quote", refreshed["refresh_reason"])
        self.assertEqual(refreshed["result_summary"].get("price"), 4350.0)
        self.assertFalse(carried["refresh_required"])
        self.assertEqual(carried["result_summary"].get("price"), 64000.0)


if __name__ == "__main__":
    unittest.main()
