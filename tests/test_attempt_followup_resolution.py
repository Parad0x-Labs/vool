"""Step 9/10: the runtime-owned follow-up resolver, driven through the REAL production entry
point (`VoolAgent.run_once`), plus the sabotage mutations Step 9/10's checkpoint requires --
each one must turn its corresponding test red without the guard it exercises.

Every positive test here drives `run_once()`, not the helper functions directly -- the same
discipline as Checkpoint 6.5's sabotage tests: proving the REAL production dispatch behaves
correctly, not just the isolated unit.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from apps.vool_agent import VoolAgent
from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
from storage.migrations import run_migrations

_SOURCE_CONTEXT = {"surface": "openclaw", "platform": "openclaw"}
_INCIDENT_TEXT = "market prices for gold and bitcoin plus weather in Atlantisxyzabc123 and Kaunas"


def _tripwire(*_args, **_kwargs):
    raise AssertionError("ordinary turn frontdoor was reached -- a follow-up leaked past the resolver")


class AttemptFollowupResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "followup.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        self.agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _submit_incident(self) -> str:
        """Submits the exact incident text with deterministic mocks for every real network call
        pytest's own safety net would otherwise block (see `tests/conftest.py`'s
        "public hive live network blocked under pytest" guard) -- Gold/Bitcoin/Kaunas succeed,
        Atlantisxyzabc123 fails with the exact HTTP 500 the checkpoint's production sequence
        checks for."""
        from core.live_quote_contract import LiveQuoteResult
        from core.weather_result_contract import WeatherResult

        def fake_crypto(coin_ids, **_kwargs):
            return [LiveQuoteResult(
                asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64781.0, currency="USD",
                as_of="2026-08-06 16:20 UTC", source_label="CoinGecko", source_url="https://x",
                kind="crypto", change_percent=0.43,
            )]

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4320.7, currency="USD",
                as_of="2026-08-06 07:30 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.36,
            )]

        def fake_weather(location: str, **_kwargs):
            if "atlantis" in location.lower():
                from urllib.error import HTTPError

                raise HTTPError("https://wttr.in/atlantisxyzabc123", 500, "Internal Server Error", None, None)
            return WeatherResult(
                location=location, place_label=location.title(), condition="Sunny", temperature_c=28.0,
                feels_like_c=27.0, humidity_pct=40.0, wind_kmph=8.0, source_label="wttr.in",
                source_url="https://x", observed_at="02:35 PM",
            )

        with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            result = self.agent.run_once(_INCIDENT_TEXT, source_context=dict(_SOURCE_CONTEXT))
        return result["session_id"]

    # --- positive production-path tests -----------------------------------------------------

    def test_explain_failure_binds_to_the_persisted_attempt_no_bootstrap_leak(self) -> None:
        sid = self._submit_incident()
        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire):
            result = self.agent.run_once("Why did that request fail?", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        response = result["response"]
        self.assertIn("HTTPError", response)
        self.assertIn("500", response)
        for banned in ("x402", "usdc", "wallet", "windows hello", "os-native consent"):
            self.assertNotIn(banned, response.lower(), f"leaked unrelated bootstrap term: {banned}")
        self.assertEqual(result["model_calls"], 0)

    def test_explain_failure_mentions_successful_siblings_retained(self) -> None:
        sid = self._submit_incident()
        result = self.agent.run_once("why did that fail?", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        response = result["response"]
        self.assertTrue("Bitcoin" in response and "Gold" in response and "Kaunas" in response)

    def test_list_original_entities_reads_from_persisted_subtasks(self) -> None:
        sid = self._submit_incident()
        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire):
            result = self.agent.run_once(
                "Which assets and cities did I originally ask for?", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid,
            )
        for name in ("Gold", "Bitcoin", "Kaunas", "Atlantisxyzabc123"):
            self.assertIn(name, result["response"])
        self.assertEqual(result["model_calls"], 0)

    def test_retry_never_reaches_frontdoor_or_adaptive_research(self) -> None:
        sid = self._submit_incident()

        def fake_weather(location: str, **_kwargs):
            from core.weather_result_contract import WeatherResult

            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=22.0,
                feels_like_c=21.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="04:00 PM",
            )

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            result = self.agent.run_once("Retry the exact failed request now.", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        self.assertEqual(result["model_calls"], 0)
        self.assertIn("Atlantisxyzabc123", result["response"])
        self.assertNotIn("HTTP Error 500", result["response"])  # the rerun succeeded this time

    def test_repeat_original_request_reconstructs_without_refetching(self) -> None:
        """A fully-successful attempt's "check the original message" must reconstruct from
        storage, never re-fetch."""
        # Build a clean, fully-successful incident (no forced failure) to exercise the SUCCEEDED
        # reconstruction branch specifically.
        with mock.patch("tools.web.web_research.structured_weather_lookup") as fake:
            from core.weather_result_contract import WeatherResult

            fake.return_value = WeatherResult(
                location="kaunas", place_label="Kaunas", condition="Sunny", temperature_c=28.0,
                feels_like_c=27.0, humidity_pct=40.0, wind_kmph=8.0, source_label="wttr.in",
                source_url="https://x", observed_at="02:35 PM",
            )
            first = self.agent.run_once("weather in Kaunas and Vilnius", source_context=dict(_SOURCE_CONTEXT))
        sid = first["session_id"]

        def tripwire_refetch(*_a, **_k):
            raise AssertionError("repeat-original-request re-fetched instead of reconstructing")

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=_tripwire), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=tripwire_refetch):
            result = self.agent.run_once("check the original message and answer it", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        self.assertIn("Kaunas", result["response"])

    def test_no_attempt_in_session_falls_through_honestly(self) -> None:
        """A follow-up phrase with NO prior attempt in the session must not fabricate an
        antecedent -- it falls through to the ordinary pipeline."""
        reached = {"frontdoor": False}

        def mark_reached(*_a, **_k):
            reached["frontdoor"] = True
            return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=mark_reached):
            self.agent.run_once("why did that fail?", source_context=dict(_SOURCE_CONTEXT))
        self.assertTrue(reached["frontdoor"], "a follow-up with no resolvable attempt must fall through")

    def test_retry_idempotency_a_repeated_retry_does_not_duplicate_execution(self) -> None:
        """Repair 9 (Mnemosyne review, 2026-08-06): the original version of this test counted
        `runtime_attempts` rows to prove "no duplicate execution" -- with the process lock removed,
        two REAL executions can occur while only one child ROW remains (the second execution's
        upsert overwrites the first's, or the loser's write is simply never distinguished from the
        winner's), so a row count alone stays green even when the underlying guarantee is broken.
        Fixed to assert the actual transport invocation count.

        This also corrects the test's PREMISE: two independently-typed "Retry the exact failed
        request now." messages each mint their OWN canonical `dialogue_turns.turn_id` (Repair 4)
        and are therefore two genuinely DIFFERENT retry requests, each entitled to its own
        generation (Step 10 correction F: "a genuinely new retry turn creates a new generation").
        The scenario this test represents -- the SAME logical retry submitted twice (a network-
        level double-send, a UI double-click resubmitting the identical already-recorded turn) --
        is reproduced by patching the turn-intake layer to hand BOTH concurrent calls the SAME
        turn id, exactly as a genuine duplicate delivery would.
        """
        sid = self._submit_incident()

        invocation_count = 0
        count_lock = threading.Lock()

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            with count_lock:
                invocation_count += 1
            import time as _t
            _t.sleep(0.05)
            from core.weather_result_contract import WeatherResult

            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=22.0,
                feels_like_c=21.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="04:00 PM",
            )

        results: list[dict] = []
        results_lock = threading.Lock()
        shared_turn_id = "turn-duplicate-delivery-0001"

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather), \
             mock.patch("core.human_input_adapter.record_dialogue_turn", return_value=shared_turn_id):
            def _retry():
                # Duplicate deliveries present the same canonical ingress identity.
                # The dialogue writer persists that identity; it no longer mints it.
                result = self.agent.run_once(
                    "Retry the exact failed request now.",
                    source_context={**_SOURCE_CONTEXT, "_canonical_user_turn_id": shared_turn_id},
                    session_id_override=sid,
                )
                with results_lock:
                    results.append(result)

            threads = [threading.Thread(target=_retry) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(len(results), 2)
        # The real correctness guarantee: the transport-level fetch ran exactly once, not just
        # that exactly one row happens to remain.
        self.assertEqual(invocation_count, 1, "the weather fetch ran more than once -- duplicate execution")

        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT COUNT(*) FROM runtime_attempts WHERE parent_attempt_id != '' AND session_id = ?", (sid,),
        ).fetchone()
        conn.close()
        self.assertEqual(rows[0], 1, f"expected exactly 1 retry child from 2 concurrent identical-turn retries, found {rows[0]}")

    def test_retry_idempotency_forced_interleaving_dedupes_to_one_child(self) -> None:
        """Deterministic (timing-independent) reproduction of the CI flake in
        `test_retry_idempotency_a_repeated_retry_does_not_duplicate_execution` (main run
        32477557132: "2 != 1 : expected exactly 1 retry child from 2 concurrent identical-turn
        retries, found 2"). That test relies on a 50ms fetch sleep to keep both retries in flight,
        so it only fails when contended parallel load (e.g. `ops/verify.py --workers 4`) widens the
        window enough for the losing interleaving to occur -- it passes locally 46/46.

        Here the exact bad interleaving is FORCED with two events rather than left to the scheduler:

          1. `child_created` gates the SECOND resolving thread until the FIRST thread has committed
             its retry child row, so thread B deterministically resolves that fresh RECEIVED child
             as "the latest unresolved-or-partial attempt in session" -- a DIFFERENT immediate
             parent than thread A resolved (the original failed attempt).
          2. `sibling_resolved` holds thread A's re-fetch open until B has finished resolving, so
             B never races A into a terminal (SUCCEEDED) child it would skip; the child B picks up
             is guaranteed non-terminal.

        Before the fix the two racers key their idempotency on their DIFFERENT immediate parents,
        so the `ON CONFLICT (retry_idempotency_key)` insert cannot collapse them: 2 child rows, 2
        real fetches. The fix scopes the key to the retry chain's ROOT attempt -- which both racers
        share -- so the database constraint collapses them to one child and one fetch. This test is
        the load-bearing guard for that invariant: reverting the key from root-scoped back to
        parent-scoped turns it red (see the report's sabotage/mutation proof).
        """
        import core.runtime_continuity as rc

        sid = self._submit_incident()

        invocation_count = 0
        count_lock = threading.Lock()
        first_fetch_seen = {"done": False}

        child_created = threading.Event()
        sibling_resolved = threading.Event()
        order_lock = threading.Lock()
        resolve_calls = {"n": 0}

        real_resolver = rc.latest_unresolved_or_partial_attempt
        real_create = rc.create_runtime_attempt

        def fake_weather(location: str, **_kwargs):
            nonlocal invocation_count
            with count_lock:
                invocation_count += 1
                is_first = not first_fetch_seen["done"]
                first_fetch_seen["done"] = True
            if is_first:
                # Hold the winner's re-fetch open (its child stays non-terminal) until the sibling
                # thread has resolved its own parent -- so B is guaranteed to pick up A's fresh
                # child, not an already-completed one it would skip.
                sibling_resolved.wait(timeout=5.0)
            from core.weather_result_contract import WeatherResult

            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=22.0,
                feels_like_c=21.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="04:00 PM",
            )

        # ARITY MATCHES PRODUCTION (core/runtime_continuity.py: latest_unresolved_or_partial_attempt
        # takes a keyword-only exclude_attempt_id). The original mock predated that parameter, so
        # every call raised TypeError, the follow-up lane swallowed it whole, and the retry never
        # ran -- which is why this test observed ZERO fetches behind a message about duplicates.
        def ordered_resolver(session_id: str, *, exclude_attempt_id: str = ""):
            with order_lock:
                resolve_calls["n"] += 1
                n = resolve_calls["n"]
            if n >= 2:
                # Second thread: wait until the first thread's retry child is committed, so this
                # resolution deterministically returns THAT child as its parent.
                child_created.wait(timeout=5.0)
            attempt = real_resolver(session_id, exclude_attempt_id=exclude_attempt_id)
            if n >= 2:
                sibling_resolved.set()
            return attempt

        def signalling_create(**kwargs):
            row = real_create(**kwargs)
            if str(kwargs.get("parent_attempt_id") or "").strip() and not row.get("idempotent_replay"):
                child_created.set()
            return row

        shared_turn_id = "turn-forced-interleave-0001"
        results: list[dict] = []
        results_lock = threading.Lock()

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather), \
             mock.patch("core.human_input_adapter.record_dialogue_turn", return_value=shared_turn_id), \
             mock.patch("core.runtime_continuity.latest_unresolved_or_partial_attempt", side_effect=ordered_resolver), \
             mock.patch("core.runtime_continuity.create_runtime_attempt", side_effect=signalling_create):
            def _retry():
                result = self.agent.run_once(
                    "Retry the exact failed request now.",
                    source_context={**_SOURCE_CONTEXT, "_canonical_user_turn_id": shared_turn_id},
                    session_id_override=sid,
                )
                with results_lock:
                    results.append(result)

            threads = [threading.Thread(target=_retry) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

        # Guard against a hang leaving a live event blocking a joined thread.
        sibling_resolved.set()
        child_created.set()

        self.assertEqual(len(results), 2, "both forced-interleaving retry threads must finish")
        self.assertEqual(invocation_count, 1, "the weather fetch ran more than once -- duplicate execution under forced interleaving")

        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT COUNT(*) FROM runtime_attempts WHERE parent_attempt_id != '' AND session_id = ?", (sid,),
        ).fetchone()
        children = conn.execute("SELECT attempt_id, parent_attempt_id, root_attempt_id, trigger_user_turn_id, attempt_role, retry_idempotency_key FROM runtime_attempts WHERE parent_attempt_id != '' AND session_id = ?", (sid,)).fetchall()
        conn.close()
        self.assertEqual({child[3] for child in children}, {shared_turn_id})
        self.assertEqual(
            rows[0], 1,
            f"forced-interleaving concurrent identical-turn retries must dedupe to 1 child, found {rows[0]}: {children}",
        )

    def test_a_genuinely_new_retry_turn_is_allowed_to_create_a_new_generation(self) -> None:
        """The corollary this test's old premise was missing (Step 10 correction F): two
        SEPARATELY typed "retry" messages -- each a real, distinct user turn -- are two different
        logical retries and BOTH are entitled to execute, producing two children, not one."""
        sid = self._submit_incident()

        def fake_weather(location: str, **_kwargs):
            from core.weather_result_contract import WeatherResult

            return WeatherResult(
                location=location, place_label=location, condition="Clear", temperature_c=22.0,
                feels_like_c=21.0, humidity_pct=50.0, wind_kmph=5.0, source_label="wttr.in",
                source_url="https://x", observed_at="04:00 PM",
            )

        with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            self.agent.run_once("Retry the exact failed request now.", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
            self.agent.run_once("Retry the exact failed request now.", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)

        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT COUNT(*) FROM runtime_attempts WHERE parent_attempt_id != '' AND session_id = ?", (sid,),
        ).fetchone()
        conn.close()
        self.assertEqual(rows[0], 2, "two genuinely separate retry turns must each get their own generation")

    # --- required sabotage mutations ---------------------------------------------------------

    def test_sabotage_removing_attempt_lookup_falls_through_to_frontdoor(self) -> None:
        """Sabotage #1: remove attempt lookup. With resolution forced to find nothing, the exact
        SAME failure-explanation query that correctly bound to the attempt now reaches the
        ordinary pipeline instead -- proving the lookup is load-bearing."""
        sid = self._submit_incident()
        reached = {"frontdoor": False}

        def mark_reached(*_a, **_k):
            reached["frontdoor"] = True
            return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=mark_reached), \
             mock.patch("core.agent_runtime.attempt_followup.resolve_followup_attempt", return_value=(None, "sabotaged: lookup disabled")):
            self.agent.run_once("why did that fail?", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        self.assertTrue(reached["frontdoor"], "removing attempt lookup should have let the turn fall through")

    def test_sabotage_removing_classifier_routes_retry_into_frontdoor(self) -> None:
        """Sabotage #3: without classification, "Retry the exact failed request now." is no
        longer recognized as a runtime action and reaches the ordinary pipeline (which, before
        this whole mechanism existed, is exactly what routed it into adaptive web research)."""
        sid = self._submit_incident()
        reached = {"frontdoor": False}

        def mark_reached(*_a, **_k):
            reached["frontdoor"] = True
            return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=mark_reached), \
             mock.patch("core.agent_runtime.attempt_followup.classify_followup_intent", return_value=None):
            self.agent.run_once("Retry the exact failed request now.", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        self.assertTrue(reached["frontdoor"], "removing classification should have let retry text fall through")

    def test_sabotage_removing_parent_root_linkage_breaks_the_chain_assertion(self) -> None:
        """Sabotage #5: force create_runtime_attempt to ignore parent/root ids -- the chain-
        linkage assertion that normally passes must fail."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.runtime_continuity import (
            create_runtime_attempt,
            finalize_runtime_attempt,
            list_runtime_attempt_subtasks,
            upsert_runtime_attempt_subtask,
        )

        parent = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA", plan_id="p")
        upsert_runtime_attempt_subtask(attempt_id=parent["attempt_id"], subtask_id="a", lifecycle_state="FAILED", failure_class="transient")
        parent = finalize_runtime_attempt(parent["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        real_create = create_runtime_attempt

        def sabotaged_create(**kwargs):
            kwargs["parent_attempt_id"] = ""
            kwargs["root_attempt_id"] = ""
            return real_create(**kwargs)

        # execute_attempt_retry imports create_runtime_attempt lazily FROM core.runtime_continuity
        # inside its own body, so the patch target is the source module, not attempt_retry's
        # (transient, function-local) namespace.
        with mock.patch("core.runtime_continuity.create_runtime_attempt", side_effect=sabotaged_create), \
             mock.patch("core.agent_runtime.live_data_runner.run_live_data_plan", return_value=[]):
            result = execute_attempt_retry(parent, subtasks, session_id="s1", checkpoint_id="cp")
        self.assertNotEqual(
            result["attempt"]["parent_attempt_id"], parent["attempt_id"],
            "sabotage should have broken the parent linkage this assertion checks",
        )

    def test_sabotage_stripping_failure_reason_loses_the_exact_error_text(self) -> None:
        """Sabotage #9: strip failure_reason before persisting -- the explanation must no longer
        contain the exact HTTP error text."""
        from core.agent_runtime.attempt_followup import render_attempt_failure_explanation
        from core.runtime_continuity import (
            create_runtime_attempt,
            finalize_runtime_attempt,
            list_runtime_attempt_subtasks,
            upsert_runtime_attempt_subtask,
        )

        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="a", lifecycle_state="FAILED",
            failure_reason="",  # sabotaged: stripped
        )
        finalized = finalize_runtime_attempt(attempt["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(attempt["attempt_id"])
        rendered = render_attempt_failure_explanation(finalized, subtasks)
        self.assertIn("no reason recorded", rendered)  # the honest fallback, not a fabricated reason

        # Control: with the real failure_reason present, it appears verbatim.
        attempt2 = create_runtime_attempt(session_id="s1", original_request="req2", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt2["attempt_id"], subtask_id="a", lifecycle_state="FAILED",
            failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
        )
        finalized2 = finalize_runtime_attempt(attempt2["attempt_id"])
        subtasks2 = list_runtime_attempt_subtasks(attempt2["attempt_id"])
        rendered2 = render_attempt_failure_explanation(finalized2, subtasks2)
        self.assertIn("HTTPError: HTTP Error 500: Internal Server Error", rendered2)

    def test_sabotage_restart_without_reloading_attempts_loses_resolution(self) -> None:
        """Sabotage #11: simulate "restart didn't reload attempts" by forcing the lookup to
        return None regardless of what's in the database -- the follow-up must fall through,
        proving the restart-survival property (Step 7) is what actually makes resolution work."""
        sid = self._submit_incident()
        reached = {"frontdoor": False}

        def mark_reached(*_a, **_k):
            reached["frontdoor"] = True
            return {"response": "ok", "route": "test", "session_id": "x", "source_context": {}}

        with mock.patch("core.agent_runtime.agent.VoolAgent._handle_turn_frontdoor", side_effect=mark_reached), \
             mock.patch("core.runtime_continuity.latest_unresolved_or_partial_attempt", return_value=None):
            self.agent.run_once("why did that fail?", source_context=dict(_SOURCE_CONTEXT), session_id_override=sid)
        self.assertTrue(reached["frontdoor"], "a lookup that can't find the (still-persisted) attempt should fall through")


if __name__ == "__main__":
    unittest.main()
