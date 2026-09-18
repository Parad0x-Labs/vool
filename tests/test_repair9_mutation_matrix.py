"""Repair 9's required mutation matrix (Mnemosyne review, 2026-08-06), proven against REAL
execution/idempotency signals rather than a row count:

  remove process lock             -> database constraint still prevents duplicate execution
  remove database uniqueness      -> cross-process test produces duplicate execution and fails
  remove both                     -> duplicate execution is visible in the invocation count
  remove origin/trigger wiring    -> idempotency tests fail

In this architecture, `create_runtime_attempt`'s `idempotent_replay` flag IS the execution gate:
`_execute_attempt_retry_locked` only ever runs the fetch when `idempotent_replay is False`. Proving
"at most one caller gets `idempotent_replay=False`" is therefore equivalent to proving "at most one
execution occurs" -- the single-process tests below additionally confirm this with a REAL transport
invocation counter, closing the gap the original row-counting test had (a duplicate real execution
could occur while only one row remained visible).
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
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_attempt_subtasks,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


def _mp_create_retry_worker_sabotaged_no_uniqueness(db_path: str, parent_attempt_id: str, trigger_turn: str, barrier, out_queue) -> None:
    """Sabotaged worker for Mutation B/C: neuters `compute_retry_idempotency_key` to always
    return "" BEFORE calling the real `create_runtime_attempt` -- this is a faithful "database
    uniqueness disabled" sabotage that keeps the surrounding SQL valid (dropping the actual
    UNIQUE INDEX instead makes `create_runtime_attempt`'s own ON CONFLICT clause reference a
    now-missing constraint and raise OperationalError, a DIFFERENT failure mode -- fail-loud, not
    fail-open -- confirmed directly rather than assumed). With the key neutered,
    `create_runtime_attempt` takes its plain-INSERT branch unconditionally, so no two callers are
    ever deduplicated against each other -- reproducing "no uniqueness enforced" end to end."""
    import core.runtime_continuity as runtime_continuity_module
    from core.runtime_continuity import configure_runtime_continuity_db_path, create_runtime_attempt

    configure_runtime_continuity_db_path(db_path)
    runtime_continuity_module.compute_retry_idempotency_key = lambda *_a, **_k: ""
    barrier.wait()
    row = create_runtime_attempt(
        session_id="s1", original_request="req", answer_mode="LIVE_DATA",
        parent_attempt_id=parent_attempt_id, root_attempt_id=parent_attempt_id,
        trigger_user_turn_id=trigger_turn, resolution_intent="RETRY_ATTEMPT", execution_generation=2,
    )
    out_queue.put((row["attempt_id"], bool(row["idempotent_replay"])))


def _make_parent_with_failed_weather() -> dict:
    parent = create_runtime_attempt(session_id="s1", original_request="weather in Kaunas", answer_mode="LIVE_DATA")
    upsert_runtime_attempt_subtask(
        attempt_id=parent["attempt_id"], subtask_id="p:weather:kaunas", plan_id="plan-orig",
        operation="weather_lookup", entity_type="city", entity_key="kaunas",
        arguments={"location": "kaunas"}, lifecycle_state="FAILED",
        failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
        retryable=True, retry_reason="transient tool failure",
    )
    return finalize_runtime_attempt(parent["attempt_id"])


class MutationARemoveProcessLockTests(unittest.TestCase):
    """remove process lock -> database constraint still prevents duplicate execution."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "mutation_a.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_with_the_process_lock_defeated_the_db_constraint_still_allows_only_one_execution(self) -> None:
        import core.agent_runtime.attempt_retry as attempt_retry_module
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.weather_result_contract import WeatherResult

        parent = _make_parent_with_failed_weather()
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        # Mutation: every call gets its OWN fresh lock object, so the process-local lock never
        # actually provides mutual exclusion between the two threads below -- equivalent to
        # removing it.
        def defeated_lock_for(_parent_attempt_id: str) -> threading.Lock:
            return threading.Lock()

        invocation_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            with count_lock:
                invocation_count += 1
            time.sleep(0.05)
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
                trigger_user_turn_id="turn-mutation-a", resolution_intent="RETRY_ATTEMPT",
            )
            with results_lock:
                results.append(result)

        with mock.patch.object(attempt_retry_module, "_retry_lock_for", side_effect=defeated_lock_for), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(invocation_count, 1, "the database constraint alone must still prevent a second execution")
        attempt_ids = {r["attempt"]["attempt_id"] for r in results}
        self.assertEqual(len(attempt_ids), 1)


class MutationBRemoveDatabaseUniquenessCrossProcessTests(unittest.TestCase):
    """remove database uniqueness/transaction -> cross-process test produces duplicate execution
    and fails. `idempotent_replay=False` IS the execution gate in this architecture (only a caller
    that sees it proceeds to run the fetch) -- so a cross-process race in which BOTH callers see
    `idempotent_replay=False` is, by construction, a duplicate execution."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "mutation_b.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self.parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_control_with_the_real_index_cross_process_at_most_one_would_execute(self) -> None:
        from tests.test_retry_idempotency_repair import _mp_create_retry_worker

        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        out_queue = ctx.Queue()
        procs = [
            ctx.Process(
                target=_mp_create_retry_worker,
                args=(str(self._db_path), self.parent["attempt_id"], "turn-mutation-b-control", barrier, out_queue),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        results = [out_queue.get(timeout=5) for _ in range(2)]
        would_execute = [r for r in results if not r[1]]  # idempotent_replay is False
        self.assertEqual(len(would_execute), 1, "with the real index intact, only one process may proceed to execute")

    def test_sabotage_neutered_uniqueness_lets_both_processes_believe_they_should_execute(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        out_queue = ctx.Queue()
        procs = [
            ctx.Process(
                target=_mp_create_retry_worker_sabotaged_no_uniqueness,
                args=(str(self._db_path), self.parent["attempt_id"], "turn-mutation-b-sabotage", barrier, out_queue),
            )
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        results = [out_queue.get(timeout=5) for _ in range(2)]
        would_execute = [r for r in results if not r[1]]
        self.assertEqual(
            len(would_execute), 2,
            "sabotage (neutered idempotency key) should have let BOTH processes believe they should execute",
        )


class MutationCRemoveBothTests(unittest.TestCase):
    """remove both -> duplicate execution is visible in the invocation count."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "mutation_c.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_removing_both_guards_makes_the_duplicate_execution_visible_in_the_invocation_count(self) -> None:
        import core.agent_runtime.attempt_retry as attempt_retry_module
        import core.runtime_continuity as runtime_continuity_module
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.weather_result_contract import WeatherResult

        parent = _make_parent_with_failed_weather()
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        # Mutation 1: process lock defeated.
        def defeated_lock_for(_parent_attempt_id: str) -> threading.Lock:
            return threading.Lock()

        invocation_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            with count_lock:
                invocation_count += 1
            time.sleep(0.05)
            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=20.0,
                feels_like_c=19.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="01:00 PM",
            )

        def worker() -> None:
            barrier.wait()
            execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1",
                trigger_user_turn_id="turn-mutation-c", resolution_intent="RETRY_ATTEMPT",
            )

        # Mutation 2: database uniqueness constraint effectively removed -- see
        # `_mp_create_retry_worker_sabotaged_no_uniqueness`'s docstring for why neutering the key
        # computation is used instead of dropping the actual index (which breaks the surrounding
        # SQL outright rather than silently allowing duplicates).
        with mock.patch.object(attempt_retry_module, "_retry_lock_for", side_effect=defeated_lock_for), \
             mock.patch.object(runtime_continuity_module, "compute_retry_idempotency_key", side_effect=lambda *_a, **_k: ""), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        self.assertEqual(
            invocation_count, 2,
            "with BOTH guards removed, the duplicate execution must be visible in the invocation count",
        )


class MutationDRemoveTurnWiringTests(unittest.TestCase):
    """remove origin/trigger wiring -> idempotency tests fail. Without a real trigger turn,
    create_runtime_attempt cannot build a durable idempotency key at all and refuses outright."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "mutation_d.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_empty_trigger_turn_id_makes_a_retry_attempt_impossible_to_create(self) -> None:
        parent = _make_parent_with_failed_weather()
        with self.assertRaises(ValueError):
            create_runtime_attempt(
                session_id="s1", original_request="req", answer_mode="LIVE_DATA",
                parent_attempt_id=parent["attempt_id"], root_attempt_id=parent["attempt_id"],
                trigger_user_turn_id="", execution_generation=2,
            )

    def test_dropped_turn_wiring_at_the_agent_layer_fails_the_retry_honestly(self) -> None:
        """Reproduces Repair 4's exact defect at the vool_agent.py boundary: with
        `_canonical_user_turn_id` always returning "" (as it did before that repair), a retry
        request must fail honestly rather than silently create an unkeyed generation."""
        import tempfile as _tempfile

        from apps.vool_agent import VoolAgent

        tmp2 = _tempfile.TemporaryDirectory()
        try:
            db_path2 = Path(tmp2.name) / "mutation_d_agent.db"
            run_migrations(db_path=db_path2)
            configure_runtime_continuity_db_path(str(db_path2))
            reset_runtime_continuity_state()
            agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

            from core.live_quote_contract import LiveQuoteResult

            def fake_crypto(coin_ids, **_kwargs):
                return [LiveQuoteResult(
                    asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=1.0, currency="USD",
                    as_of="x", source_label="CoinGecko", source_url="https://x", kind="crypto", change_percent=0.0,
                )]

            def fake_commodity(_q, targets, **_kwargs):
                return [LiveQuoteResult(
                    asset_key="gold", asset_name="Gold", symbol="GC=F", value=1.0, currency="USD",
                    as_of="x", source_label="Yahoo Finance", source_url="https://x", kind="commodity",
                    unit_label="per troy ounce", change_percent=0.0,
                )]

            def fake_weather(location, **_kwargs):
                if "atlantis" in location.lower():
                    from urllib.error import HTTPError

                    raise HTTPError("https://wttr.in/atlantisxyzabc123", 500, "Internal Server Error", None, None)
                from core.weather_result_contract import WeatherResult

                return WeatherResult(
                    location=location, place_label=location.title(), condition="Sunny", temperature_c=1.0,
                    feels_like_c=1.0, humidity_pct=1.0, wind_kmph=1.0, source_label="wttr.in",
                    source_url="https://x", observed_at="02:35 PM",
                )

            with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto), \
                 mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity), \
                 mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
                incident = agent.run_once(
                    "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas",
                    source_context={"surface": "openclaw", "platform": "openclaw"},
                )
                sid = incident["session_id"]

                with mock.patch.object(VoolAgent, "_canonical_user_turn_id", staticmethod(lambda source_context: "")):
                    retry_result = agent.run_once(
                        "Retry the exact failed request now.",
                        source_context={"surface": "openclaw", "platform": "openclaw"}, session_id_override=sid,
                    )
            self.assertIn("no resolvable identity", retry_result.get("response", "").lower())
        finally:
            reset_runtime_continuity_state()
            configure_runtime_continuity_db_path(None)
            tmp2.cleanup()


if __name__ == "__main__":
    unittest.main()
