"""Repair 2/3/9 (Mnemosyne review, 2026-08-06): a process-local threading.Lock plus a
lifecycle-state-scoped row check is NOT a durable idempotency guarantee -- Mnemosyne reproduced two
real OS processes both executing the same retry. The fix is a real database uniqueness constraint
on (parent_attempt_id, trigger_user_turn_id, resolution_intent) that survives terminal completion
(Repair 3), proven here with genuine concurrent threads AND genuine concurrent OS processes sharing
one on-disk database -- and with an ACTUAL transport-invocation counter (Repair 9), not a row count,
since a duplicate real execution can happen while the row count still reads 1.
"""

from __future__ import annotations

import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core.runtime_continuity import (
    compute_retry_idempotency_key,
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class ComputeRetryIdempotencyKeyTests(unittest.TestCase):
    def test_same_triple_same_key(self) -> None:
        k1 = compute_retry_idempotency_key("attempt-a", "turn-1", "RETRY_ATTEMPT")
        k2 = compute_retry_idempotency_key("attempt-a", "turn-1", "RETRY_ATTEMPT")
        self.assertEqual(k1, k2)

    def test_different_trigger_turn_different_key(self) -> None:
        """A genuinely NEW user retry turn must be free to create a new generation."""
        k1 = compute_retry_idempotency_key("attempt-a", "turn-1", "RETRY_ATTEMPT")
        k2 = compute_retry_idempotency_key("attempt-a", "turn-2", "RETRY_ATTEMPT")
        self.assertNotEqual(k1, k2)

    def test_different_intent_different_key(self) -> None:
        k1 = compute_retry_idempotency_key("attempt-a", "turn-1", "RETRY_ATTEMPT")
        k2 = compute_retry_idempotency_key("attempt-a", "turn-1", "REPEAT_ORIGINAL_REQUEST")
        self.assertNotEqual(k1, k2)

    def test_different_parent_different_key(self) -> None:
        k1 = compute_retry_idempotency_key("attempt-a", "turn-1", "RETRY_ATTEMPT")
        k2 = compute_retry_idempotency_key("attempt-b", "turn-1", "RETRY_ATTEMPT")
        self.assertNotEqual(k1, k2)


class CreateRuntimeAttemptIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "idem.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self.parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _retry(self, *, trigger_turn: str, intent: str = "RETRY_ATTEMPT", generation: int = 2) -> dict:
        return create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=self.parent["attempt_id"], root_attempt_id=self.parent["attempt_id"],
            trigger_user_turn_id=trigger_turn, resolution_intent=intent, execution_generation=generation,
        )

    def test_empty_trigger_turn_on_a_retry_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_runtime_attempt(
                session_id="s1", original_request="req", answer_mode="LIVE_DATA",
                parent_attempt_id=self.parent["attempt_id"], root_attempt_id=self.parent["attempt_id"],
                trigger_user_turn_id="", execution_generation=2,
            )

    def test_repeated_call_same_triple_returns_the_same_row(self) -> None:
        first = self._retry(trigger_turn="turn-x")
        second = self._retry(trigger_turn="turn-x")
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])

    def test_same_turn_different_immediate_parents_in_one_chain_dedupe_by_root(self) -> None:
        """The concurrency root cause behind CI flake 32477557132, isolated at the create seam
        WITHOUT threads (so the process-local probe in `execute_attempt_retry` cannot mask it): two
        retries of the SAME logical retry (same trigger turn + intent) can resolve DIFFERENT
        immediate parents within ONE chain. Under contention, racer A resolves the original attempt
        X while racer B resolves a freshly-created retry child Y (parent X) as "the latest
        unresolved attempt." Both share the chain ROOT (X). The idempotency key is scoped to that
        root, so B's create collapses onto A's row instead of minting a second child.

        This is the load-bearing sabotage for the authoritative (cross-process) half of the fix:
        reverting `create_runtime_attempt`'s key from `resolved_root` back to `clean_parent_id`
        turns this test red, and the probe change cannot hide that here because nothing calls the
        probe. The in-process end-to-end guard is
        `tests/test_attempt_followup_resolution.py::...forced_interleaving_dedupes_to_one_child`.
        """
        root = self.parent  # created in setUp: an original attempt, root_attempt_id == its own id
        self.assertEqual(root["root_attempt_id"], root["attempt_id"])

        # A first retry generation of the chain: child Y, parent = X, root = X.
        child_y = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=root["attempt_id"], root_attempt_id=root["attempt_id"],
            trigger_user_turn_id="turn-first-generation", resolution_intent="RETRY_ATTEMPT",
            execution_generation=2,
        )
        self.assertFalse(child_y["idempotent_replay"])
        self.assertEqual(child_y["root_attempt_id"], root["attempt_id"])
        self.assertNotEqual(child_y["attempt_id"], root["attempt_id"])

        # Two racers for the SAME logical retry turn, having resolved DIFFERENT immediate parents:
        #   A resolved the original X; B resolved the child Y. Same root, same turn, same intent.
        turn = "turn-same-logical-retry"
        a = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=root["attempt_id"], root_attempt_id=root["attempt_id"],
            trigger_user_turn_id=turn, resolution_intent="RETRY_ATTEMPT", execution_generation=2,
        )
        b = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=child_y["attempt_id"], root_attempt_id=root["attempt_id"],
            trigger_user_turn_id=turn, resolution_intent="RETRY_ATTEMPT", execution_generation=3,
        )
        self.assertFalse(a["idempotent_replay"])
        self.assertTrue(
            b["idempotent_replay"],
            "a sibling retry with a different immediate parent but the SAME chain root+turn+intent "
            "must collapse onto the first, not create a second child",
        )
        self.assertEqual(a["attempt_id"], b["attempt_id"])

        # Negative control: a genuinely different trigger turn on the same chain is NOT collapsed --
        # the root scoping must not over-merge distinct user retries into one generation.
        fresh = create_runtime_attempt(
            session_id="s1", original_request="req", answer_mode="LIVE_DATA",
            parent_attempt_id=child_y["attempt_id"], root_attempt_id=root["attempt_id"],
            trigger_user_turn_id="turn-genuinely-different", resolution_intent="RETRY_ATTEMPT",
            execution_generation=3,
        )
        self.assertFalse(fresh["idempotent_replay"])
        self.assertNotEqual(fresh["attempt_id"], a["attempt_id"])

    def test_idempotency_survives_every_terminal_state(self) -> None:
        """Repair 3: a REPLAYED request must dedupe past completion, not just while in flight."""
        for terminal_state in (
            "SUCCEEDED", "PARTIAL_SUCCESS", "FAILED_TOOL", "FAILED_PROVIDER",
            "FAILED_SYNTHESIS", "FAILED_VALIDATION", "CANCELLED",
        ):
            with self.subTest(terminal_state=terminal_state):
                reset_runtime_continuity_state()
                parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
                first = create_runtime_attempt(
                    session_id="s1", original_request="req", answer_mode="LIVE_DATA",
                    parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
                    trigger_user_turn_id="turn-terminal", execution_generation=2,
                )
                from core.runtime_continuity import update_runtime_attempt

                update_runtime_attempt(first["attempt_id"], lifecycle_state=terminal_state, completed=True)
                replay = create_runtime_attempt(
                    session_id="s1", original_request="req", answer_mode="LIVE_DATA",
                    parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
                    trigger_user_turn_id="turn-terminal", execution_generation=2,
                )
                self.assertEqual(replay["attempt_id"], first["attempt_id"])
                self.assertTrue(replay["idempotent_replay"])
                # A genuinely different retry turn against the same terminal parent is NOT blocked.
                fresh = create_runtime_attempt(
                    session_id="s1", original_request="req", answer_mode="LIVE_DATA",
                    parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
                    trigger_user_turn_id="turn-genuinely-new", execution_generation=3,
                )
                self.assertNotEqual(fresh["attempt_id"], first["attempt_id"])
                self.assertFalse(fresh["idempotent_replay"])

    def test_two_threads_racing_the_same_trigger_turn_produce_one_winner(self) -> None:
        results: list[dict] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            row = self._retry(trigger_turn="turn-thread-race")
            with lock:
                results.append(row)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(results), 2)
        attempt_ids = {r["attempt_id"] for r in results}
        self.assertEqual(len(attempt_ids), 1, "both threads must resolve to the SAME child attempt")
        winners = [r for r in results if not r["idempotent_replay"]]
        self.assertEqual(len(winners), 1, "exactly one thread may create the row")


def _mp_create_retry_worker(db_path: str, parent_attempt_id: str, trigger_turn: str, barrier, out_queue) -> None:
    """Top-level (picklable) worker for the genuine two-OS-process reproduction. Each process opens
    its OWN connection to the SAME on-disk database file -- this is what a process-local lock
    cannot see across, and exactly what Mnemosyne's live reproduction exercised."""
    from core.runtime_continuity import configure_runtime_continuity_db_path, create_runtime_attempt
    from storage.db import configure_default_db_path

    # The invocation fence and attempt ledger share the process's selected database.
    # Spawned workers do not inherit the parent's in-process storage override.
    configure_default_db_path(db_path)
    configure_runtime_continuity_db_path(db_path)
    barrier.wait()
    row = create_runtime_attempt(
        session_id="s1", original_request="req", answer_mode="LIVE_DATA",
        parent_attempt_id=parent_attempt_id, root_attempt_id=parent_attempt_id,
        trigger_user_turn_id=trigger_turn, resolution_intent="RETRY_ATTEMPT", execution_generation=2,
    )
    out_queue.put((row["attempt_id"], bool(row["idempotent_replay"])))


class CrossProcessIdempotencyTests(unittest.TestCase):
    """Repair 2's explicit required test: two independent OS processes, not threads -- a
    process-local lock is invisible across a process boundary by construction, so only a real
    database constraint can make this pass."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "crossproc.db"
        run_migrations(db_path=self._db_path)
        from storage.db import configure_default_db_path

        configure_default_db_path(str(self._db_path))
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self.parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        from storage.db import configure_default_db_path

        configure_runtime_continuity_db_path(None)
        configure_default_db_path(None)
        self._tmp.cleanup()

    def test_two_os_processes_racing_the_same_retry_produce_one_row(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        out_queue = ctx.Queue()
        procs = [
            ctx.Process(
                target=_mp_create_retry_worker,
                args=(str(self._db_path), self.parent["attempt_id"], "turn-cross-process", barrier, out_queue),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        self.assertTrue(all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs])

        results = [out_queue.get(timeout=5) for _ in range(2)]
        attempt_ids = {r[0] for r in results}
        self.assertEqual(len(attempt_ids), 1, "two separate OS processes must agree on ONE child attempt")
        winners = [r for r in results if not r[1]]
        self.assertEqual(len(winners), 1, "exactly one process may have created the row")

        # Durable confirmation directly against the shared database: exactly one row for this key.
        import sqlite3

        from core.runtime_continuity import compute_retry_idempotency_key

        key = compute_retry_idempotency_key(self.parent["attempt_id"], "turn-cross-process", "RETRY_ATTEMPT")
        conn = sqlite3.connect(str(self._db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM runtime_attempts WHERE retry_idempotency_key = ?", (key,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


class ExecuteAttemptRetryIdempotencyTests(unittest.TestCase):
    """Repair 9: the concurrency guarantee is proven by an actual transport-invocation counter, not
    a row count -- a row count stays 1 even if the underlying fetch ran twice, which is exactly the
    gap Mnemosyne found in the original concurrency test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "execretry.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _make_parent(self) -> dict:
        parent = create_runtime_attempt(session_id="s1", original_request="weather in Kaunas", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
            operation="weather_lookup", entity_type="city", entity_key="kaunas",
            arguments={"location": "kaunas"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
            retryable=True, retry_reason="transient tool failure",
        )
        return finalize_runtime_attempt(parent["attempt_id"])

    def test_two_concurrent_retry_calls_same_trigger_turn_fetch_exactly_once(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import list_runtime_attempt_subtasks
        from core.weather_result_contract import WeatherResult

        parent = self._make_parent()
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        invocation_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            with count_lock:
                invocation_count += 1
            time.sleep(0.05)  # widen the race window so both threads are genuinely in flight
            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=20.0,
                feels_like_c=19.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="01:00 PM",
            )

        results: list[dict] = []
        results_lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-concurrent", resolution_intent="RETRY_ATTEMPT",
            )
            with results_lock:
                results.append(result)

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(invocation_count, 1, "the transport fetch ran more than once -- duplicate execution")
        attempt_ids = {r["attempt"]["attempt_id"] for r in results}
        self.assertEqual(len(attempt_ids), 1, "both callers must receive the SAME logical result")
        rendered_texts = {r["rendered"] for r in results}
        self.assertEqual(len(rendered_texts), 1, "both callers must receive the SAME rendered answer")

    def test_replay_after_original_response_is_lost_returns_the_same_result_without_refetching(self) -> None:
        """'Client replay after the original response was lost': the first call completes and its
        response is discarded by the test (simulating a dropped HTTP response); a second call with
        the identical trigger turn must return the same result without a second fetch."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import list_runtime_attempt_subtasks
        from core.weather_result_contract import WeatherResult

        parent = self._make_parent()
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])
        invocation_count = 0

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            invocation_count += 1
            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=20.0,
                feels_like_c=19.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="01:00 PM",
            )

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            first = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-replay", resolution_intent="RETRY_ATTEMPT",
            )
            del first  # simulate the original HTTP response never reaching the client
            second = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-replay", resolution_intent="RETRY_ATTEMPT",
            )
        self.assertEqual(invocation_count, 1)
        self.assertTrue(second["idempotent_replay"])
        self.assertIn("Kaunas", second["rendered"])

    def test_restart_after_attempt_creation_but_before_response_delivery(self) -> None:
        """Simulates a crash between the retry row being created (idempotency key committed) and
        the response ever being computed: only `create_runtime_attempt` ran, nothing executed
        against it. The next call with the same trigger turn (the "restart" -- a fresh call, as a
        new process would make) must reuse that row, not create a duplicate generation, and must
        render honestly from whatever is persisted rather than fabricating a result."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import list_runtime_attempt_subtasks

        parent = self._make_parent()
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        crashed_row = create_runtime_attempt(
            session_id="s1", original_request=str(parent.get("original_request_snapshot") or ""),
            answer_mode="LIVE_DATA", parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
            trigger_user_turn_id="turn-crash-restart", resolution_intent="RETRY_ATTEMPT", execution_generation=2,
        )
        self.assertFalse(crashed_row["idempotent_replay"])

        result = execute_attempt_retry(
            parent, subtasks, session_id="s1", checkpoint_id="cp-1",
            trigger_user_turn_id="turn-crash-restart", resolution_intent="RETRY_ATTEMPT",
        )
        self.assertTrue(result["idempotent_replay"])
        self.assertEqual(result["attempt"]["attempt_id"], crashed_row["attempt_id"])
        # No second child was created for the same trigger turn.
        replay_count = create_runtime_attempt(
            session_id="s1", original_request=str(parent.get("original_request_snapshot") or ""),
            answer_mode="LIVE_DATA", parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
            trigger_user_turn_id="turn-crash-restart", resolution_intent="RETRY_ATTEMPT", execution_generation=2,
        )
        self.assertEqual(replay_count["attempt_id"], crashed_row["attempt_id"])


class SabotageIdempotencyTests(unittest.TestCase):
    """Sabotage: remove the database uniqueness/transaction (Repair 9's required mutation matrix)
    -- with the real constraint bypassed, two concurrent creates for the same trigger turn produce
    TWO rows, which the real code must never allow."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "sabotage_idem.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_sabotage_dropping_the_uniqueness_index_allows_duplicate_rows(self) -> None:
        """Reproduces the pre-Repair-2 shape directly. A raw INSERT with no ON CONFLICT clause is
        NOT sufficient sabotage on its own -- the partial UNIQUE INDEX Repair 2 added is itself
        what blocks a duplicate at the storage layer regardless of which INSERT shape is used
        (confirmed: attempting this without dropping the index first raises IntegrityError, proving
        the real guarantee lives in the schema, not merely in `create_runtime_attempt`'s SQL
        shape). The actual required mutation is removing the database uniqueness constraint
        itself."""
        import sqlite3

        parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        key = compute_retry_idempotency_key(parent["attempt_id"], "turn-sabotage", "RETRY_ATTEMPT")

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("DROP INDEX idx_runtime_attempts_retry_idempotency")
            for i in range(2):
                conn.execute(
                    "INSERT INTO runtime_attempts (attempt_id, session_id, parent_attempt_id, "
                    "root_attempt_id, retry_idempotency_key, execution_generation, lifecycle_state, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 2, 'RECEIVED', '2026-01-01', '2026-01-01')",
                    (f"attempt-sabotage-{i}", "s1", parent["attempt_id"], parent["attempt_id"], key),
                )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM runtime_attempts WHERE retry_idempotency_key = ?", (key,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 2, "sabotage (dropped index) should have produced a duplicate row")

        # Control: on a database where the real migration ran (the index intact), the identical
        # sequence -- two raw inserts for the same key -- is rejected by the schema itself.
        reset_runtime_continuity_state()
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                "CREATE UNIQUE INDEX idx_runtime_attempts_retry_idempotency "
                "ON runtime_attempts(retry_idempotency_key) WHERE retry_idempotency_key != ''"
            )
            conn.execute("DELETE FROM runtime_attempts")
            conn.commit()
            conn.execute(
                "INSERT INTO runtime_attempts (attempt_id, session_id, parent_attempt_id, "
                "root_attempt_id, retry_idempotency_key, execution_generation, lifecycle_state, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 2, 'RECEIVED', '2026-01-01', '2026-01-01')",
                ("attempt-control-0", "s1", parent["attempt_id"], parent["attempt_id"], key),
            )
            conn.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO runtime_attempts (attempt_id, session_id, parent_attempt_id, "
                    "root_attempt_id, retry_idempotency_key, execution_generation, lifecycle_state, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 2, 'RECEIVED', '2026-01-01', '2026-01-01')",
                    ("attempt-control-1", "s1", parent["attempt_id"], parent["attempt_id"], key),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
