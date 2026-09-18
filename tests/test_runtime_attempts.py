"""Step 7: generic runtime-attempt persistence -- CRUD, reconciliation, and the concurrency
correction that motivated normalizing subtasks into their own table.

Checkpoint 6.5's design map first proposed a single `subtasks_json` blob per attempt, updated by
read-modify-write. HARBORMASTER rejected that shape: concurrent workers completing different
subtasks would race on the same row, and a naive read-modify-write can silently lose one worker's
result under interleaving SQLite's own statement atomicity does nothing to prevent (the read and
the write are two separate statements). This file proves both halves of that correction: the
REJECTED shape actually loses a transition under real thread interleaving, and the shipped
per-subtask-row design does not.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.runtime_continuity import (
    AttemptLifecycle,
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    get_runtime_attempt,
    latest_runtime_attempt,
    list_runtime_attempt_subtasks,
    mark_stale_runtime_attempts_abandoned,
    reconcile_attempt_lifecycle,
    record_attempt_approval,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class RuntimeAttemptCrudTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "attempts.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_create_attempt_is_a_first_generation_root_of_itself(self) -> None:
        attempt = create_runtime_attempt(
            session_id="s1", original_request="market prices for bitcoin", answer_mode="LIVE_DATA",
        )
        self.assertEqual(attempt["root_attempt_id"], attempt["attempt_id"])
        self.assertEqual(attempt["parent_attempt_id"], "")
        self.assertEqual(attempt["execution_generation"], 1)
        self.assertEqual(attempt["lifecycle_state"], AttemptLifecycle.RECEIVED.value)

    def test_a_retry_links_to_root_and_parent_without_mutating_the_prior_attempt(self) -> None:
        first = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=first["attempt_id"], subtask_id="a", lifecycle_state="FAILED",
            failure_reason="network down",
        )
        finalize_runtime_attempt(first["attempt_id"])

        retry = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            root_attempt_id=first["root_attempt_id"], parent_attempt_id=first["attempt_id"],
            trigger_user_turn_id="turn-retry-1", execution_generation=2,
        )
        self.assertEqual(retry["root_attempt_id"], first["attempt_id"])
        self.assertEqual(retry["parent_attempt_id"], first["attempt_id"])
        self.assertEqual(retry["execution_generation"], 2)
        # The prior attempt's own row is untouched by creating a retry.
        prior_reloaded = get_runtime_attempt(first["attempt_id"])
        self.assertEqual(prior_reloaded["lifecycle_state"], AttemptLifecycle.FAILED_TOOL.value)

    def test_original_request_survives_unbounded_for_a_realistic_large_request(self) -> None:
        # ~6000 chars: bigger than the old, rejected 4000-char cap, well under the 32KB safety
        # limit. Must NOT be truncated.
        big_request = "please fetch prices for: " + ", ".join(f"asset{i}" for i in range(600))
        self.assertGreater(len(big_request), 4000)
        attempt = create_runtime_attempt(session_id="s1", original_request=big_request, answer_mode="LIVE_DATA")
        self.assertFalse(attempt["original_request_truncated"])
        self.assertEqual(attempt["original_request_snapshot"], big_request)

    def test_a_request_over_the_safety_limit_is_marked_truncated_not_silently_shortened(self) -> None:
        huge_request = "x" * 40_000
        attempt = create_runtime_attempt(session_id="s1", original_request=huge_request, answer_mode="LIVE_DATA")
        self.assertTrue(attempt["original_request_truncated"])
        self.assertLess(len(attempt["original_request_snapshot"]), len(huge_request))
        # The hash is of the FULL text, not the truncated snapshot -- identity is still verifiable.
        import hashlib

        self.assertEqual(attempt["original_request_hash"], hashlib.sha256(huge_request.encode()).hexdigest())

    def test_subtask_rows_are_independent_and_survive_reconciliation(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="market:gold", operation="market_quote",
            entity_type="commodity", entity_key="gold", lifecycle_state="SUCCEEDED",
            result_summary={"price": 4320.7},
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="weather:atlantis", operation="weather_lookup",
            entity_type="city", entity_key="atlantis", lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
            retryable=True, retry_reason="transient network error",
        )
        rows = list_runtime_attempt_subtasks(attempt["attempt_id"])
        self.assertEqual(len(rows), 2)
        failed = next(r for r in rows if r["subtask_id"] == "weather:atlantis")
        self.assertEqual(failed["failure_reason"], "HTTPError: HTTP Error 500: Internal Server Error")
        self.assertTrue(failed["retryable"])

        finalized = finalize_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(finalized["lifecycle_state"], AttemptLifecycle.PARTIAL_SUCCESS.value)
        self.assertIn("weather:atlantis", finalized["retry_from_stage"])

    def test_latest_runtime_attempt_returns_the_most_recently_updated(self) -> None:
        create_runtime_attempt(session_id="s1", original_request="first", answer_mode="LIVE_DATA")
        time.sleep(0.01)
        second = create_runtime_attempt(session_id="s1", original_request="second", answer_mode="LIVE_DATA")
        latest = latest_runtime_attempt("s1")
        self.assertEqual(latest["attempt_id"], second["attempt_id"])

    def test_stale_nonterminal_attempts_are_abandoned_not_deleted(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        self.assertEqual(attempt["lifecycle_state"], AttemptLifecycle.RECEIVED.value)
        count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(count, 1)
        reloaded = get_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(reloaded["lifecycle_state"], AttemptLifecycle.ABANDONED.value)
        self.assertTrue(reloaded["retryable"])
        self.assertNotEqual(reloaded["terminal_reason"], "")

    def test_abandonment_emits_a_visible_activity_event(self) -> None:
        from core.runtime_continuity import list_runtime_session_events

        create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        mark_stale_runtime_attempts_abandoned()
        events = list_runtime_session_events("s1", limit=50)
        types = [e["event_type"] for e in events]
        self.assertIn("runtime_attempt_abandoned", types)

    def test_a_terminal_attempt_is_not_swept(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(attempt_id=attempt["attempt_id"], subtask_id="a", lifecycle_state="SUCCEEDED")
        finalize_runtime_attempt(attempt["attempt_id"])
        count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(count, 0)
        reloaded = get_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(reloaded["lifecycle_state"], AttemptLifecycle.SUCCEEDED.value)

    def test_approval_decision_is_durable_and_digest_scoped(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        record = record_attempt_approval(
            attempt_id=attempt["attempt_id"], subtask_id="market:gold",
            action_digest="digest-abc", plan_digest="plan-digest-xyz",
            policy_version="v1", requested_scope="web.research", decision="allow",
        )
        self.assertTrue(record["approval_id"])
        self.assertEqual(record["action_digest"], "digest-abc")
        self.assertEqual(record["decision"], "allow")


class ReconcileAttemptLifecycleTests(unittest.TestCase):
    """Pure-function tests -- no I/O -- for every rule Step 7 correction #8 specifies."""

    def test_all_succeeded_is_succeeded(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED", "SUCCEEDED"])
        self.assertEqual(state, "SUCCEEDED")

    def test_mixed_success_and_failure_is_partial_success(self) -> None:
        state, reason = reconcile_attempt_lifecycle(["SUCCEEDED", "FAILED"])
        self.assertEqual(state, "PARTIAL_SUCCESS")
        self.assertIn("1/2", reason)

    def test_mixed_success_and_unsupported_is_also_partial_success(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED", "UNSUPPORTED_ENTITY"])
        self.assertEqual(state, "PARTIAL_SUCCESS")

    def test_all_failed_is_failed_tool(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["FAILED", "FAILED"])
        self.assertEqual(state, "FAILED_TOOL")

    def test_provider_unavailable_before_execution_is_failed_provider(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED"], provider_available=False)
        self.assertEqual(state, "FAILED_PROVIDER")

    def test_synthesis_failure_after_all_tools_succeed_is_failed_synthesis(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED", "SUCCEEDED"], synthesis_ok=False)
        self.assertEqual(state, "FAILED_SYNTHESIS")

    def test_invalid_plan_is_failed_validation(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED"], plan_valid=False)
        self.assertEqual(state, "FAILED_VALIDATION")

    def test_no_subtasks_is_failed_validation(self) -> None:
        state, _ = reconcile_attempt_lifecycle([])
        self.assertEqual(state, "FAILED_VALIDATION")

    def test_any_waiting_approval_makes_the_whole_attempt_waiting_approval(self) -> None:
        state, _ = reconcile_attempt_lifecycle(["SUCCEEDED", "WAITING_APPROVAL"])
        self.assertEqual(state, "WAITING_APPROVAL")

    def test_unsupported_entity_alone_is_failed_tool_not_succeeded(self) -> None:
        """UNSUPPORTED_ENTITY must never be silently treated as success."""
        state, _ = reconcile_attempt_lifecycle(["UNSUPPORTED_ENTITY"])
        self.assertEqual(state, "FAILED_TOOL")


class RuntimeAttemptRetryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "attempts.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_unsupported_entity_is_not_retryable_by_default(self) -> None:
        """Correction #6: do not automatically retry UNSUPPORTED_ENTITY."""
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="market:madeupcoin",
            lifecycle_state="UNSUPPORTED_ENTITY", failure_class="unsupported",
            failure_reason="not a recognized market entity", retryable=False,
            retry_reason="unsupported unless capabilities or the request change",
        )
        rows = list_runtime_attempt_subtasks(attempt["attempt_id"])
        self.assertFalse(rows[0]["retryable"])

    def test_succeeded_attempt_is_not_retryable(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(attempt_id=attempt["attempt_id"], subtask_id="a", lifecycle_state="SUCCEEDED")
        finalized = finalize_runtime_attempt(attempt["attempt_id"])
        self.assertFalse(finalized["retryable"])

    def test_failed_tool_attempt_is_retryable(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(attempt_id=attempt["attempt_id"], subtask_id="a", lifecycle_state="FAILED")
        finalized = finalize_runtime_attempt(attempt["attempt_id"])
        self.assertTrue(finalized["retryable"])


# ============================================================================
# Required concurrency sabotage (Step 7 correction #11).
# ============================================================================


def _rejected_single_blob_write(conn: sqlite3.Connection, attempt_id: str, subtask_id: str, lock: threading.Lock) -> None:
    """Mimics the REJECTED design: one JSON blob per attempt, read-modify-write with no
    compare-and-swap. The `time.sleep` between read and write is not a timing accident -- it
    deliberately widens the race window so the interleaving this test exists to prove is
    reproduced every run, not occasionally."""
    row = conn.execute("SELECT blob FROM rejected_single_blob WHERE attempt_id = ?", (attempt_id,)).fetchone()
    blob = json.loads(row[0]) if row else {}
    time.sleep(0.02)  # widen the read-modify-write race window deterministically
    blob[subtask_id] = "done"
    with lock:  # only serializes the SQL write itself, not the read-modify-write as a unit
        conn.execute("UPDATE rejected_single_blob SET blob = ? WHERE attempt_id = ?", (json.dumps(blob), attempt_id))
        conn.commit()


class ConcurrencySabotageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "attempts.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_sabotage_rejected_whole_blob_design_loses_a_concurrent_transition(self) -> None:
        """Proves the REJECTED shape is actually dangerous, not just theoretically so: seven
        threads each set their own key in one shared JSON blob via unlocked read-modify-write.
        At least one transition must be lost."""
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("CREATE TABLE IF NOT EXISTS rejected_single_blob (attempt_id TEXT PRIMARY KEY, blob TEXT)")
        conn.execute("INSERT INTO rejected_single_blob (attempt_id, blob) VALUES ('a1', '{}')")
        conn.commit()
        write_lock = threading.Lock()

        threads = [
            threading.Thread(target=_rejected_single_blob_write, args=(conn, "a1", f"sub:{i}", write_lock))
            for i in range(7)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        row = conn.execute("SELECT blob FROM rejected_single_blob WHERE attempt_id = 'a1'").fetchone()
        final_blob = json.loads(row[0])
        conn.close()
        self.assertLess(
            len(final_blob), 7,
            "expected the unlocked read-modify-write to lose at least one of the 7 concurrent "
            "transitions -- if this assertion fails, the sabotage itself stopped reproducing the "
            "hazard and no longer proves anything about the production design",
        )

    def test_production_per_row_design_preserves_all_seven_concurrent_transitions(self) -> None:
        """The shipped design: seven threads, each completing a DIFFERENT subtask_id (its own
        primary key), under the same deliberately-widened interleaving the sabotage above uses.
        All seven must survive -- there is no shared blob to race on."""
        import random

        attempt = create_runtime_attempt(session_id="s1", original_request="7-way concurrency", answer_mode="LIVE_DATA")

        def worker(i: int) -> None:
            time.sleep(random.uniform(0, 0.03))
            upsert_runtime_attempt_subtask(
                attempt_id=attempt["attempt_id"], subtask_id=f"sub:{i}",
                operation="market_quote", entity_type="crypto", entity_key=f"coin{i}",
                lifecycle_state="SUCCEEDED", result_summary={"price": i * 100},
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(7)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = list_runtime_attempt_subtasks(attempt["attempt_id"])
        self.assertEqual(len(rows), 7)
        self.assertEqual({r["subtask_id"] for r in rows}, {f"sub:{i}" for i in range(7)})
        self.assertTrue(all(r["lifecycle_state"] == "SUCCEEDED" for r in rows))

        finalized = finalize_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(finalized["lifecycle_state"], AttemptLifecycle.SUCCEEDED.value)


if __name__ == "__main__":
    unittest.main()
