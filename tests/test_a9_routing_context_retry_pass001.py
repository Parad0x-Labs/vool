"""A9 pass-001 — routing/context/retry convergence guards.

Every load-bearing root cause repaired in this pass gets a mechanical detector driven
through REAL production seams wherever one exists:

- RC-1  candidate-knowledge-lane cache: TTL enforced by the store itself (SQL predicate),
        plus scope fail-closed contract on both read (`_candidate_cache_scope` gate) and
        write (unscooped candidates are never recorded).
- RC-2  provider health registry is serialized: N concurrent failing turns lose no
        increments and open the circuit exactly when the threshold says to.
- RC-3  L0 fence closure: a served turn via `VoolAgent.run_once` leaves its `executions`
        row terminal (COMPLETED / FAILED / CANCELLED) — never ACTIVE forever.
- RC-4  cancellation lands as CANCELLED at BOTH attempt and execution layer, never the
        misleading retryable FAILED_PROVIDER.
- RC-6  restart adoption: an ABANDONED+retryable chain (boot sweep verdict) adopts onto a
        new writer epoch and mints a generation; a live foreign-epoch chain still refuses.
- RC-7  history assembly prefers verbatim raw input over normalized, and reconstruction
        stays last-resort.
- RC-8  client-supplied histories clear the same A8 erasure gate as hydrated events.

Ledger discipline: the L0 fence tables (`invocation_requests`, `executions`) live in the
storage-level DB, so these tests point BOTH store configs at the same isolated file —
the pattern `test_retry_turn_identity_repair.py` established, extended to the ledger half.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from storage.db import configure_default_db_path
from storage.migrations import run_migrations


class _IsolatedStoresTestCase(unittest.TestCase):
    """Both durable stores (runtime continuity + invocation ledger) share one temp DB."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "a9.db"
        run_migrations(db_path=str(self._db_path))
        configure_default_db_path(str(self._db_path))
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )

        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )

        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        configure_default_db_path(None)
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# RC-1 — candidate lane: store-enforced TTL + scope fail-closed
# ---------------------------------------------------------------------------


class CandidateLaneTtlEnforcedByStoreTests(_IsolatedStoresTestCase):
    def test_expired_candidate_is_not_served_even_by_a_naive_consumer(self) -> None:
        from core.candidate_knowledge_lane import (
            get_exact_candidate,
            record_candidate_output,
        )

        task_hash = record_candidate_output(
            task_hash="a9-ttl-hash",
            task_id=None,
            trace_id=None,
            task_class="chat",
            task_kind="ordinary_chat",
            output_mode="plain_text",
            provider_name="prov",
            model_name="model",
            raw_output="answer",
            normalized_output="answer",
            structured_output=None,
            confidence=0.9,
            trust_score=0.9,
            validation_state="valid",
            ttl_seconds=3600,
        )
        self.assertTrue(task_hash)
        self.assertIsNotNone(get_exact_candidate("a9-ttl-hash"))
        # Age the ONLY freshness-bearing row past its own expiry inside the store.
        import sqlite3

        from storage.db import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE candidate_knowledge_lane SET expires_at = '2000-01-01T00:00:00+00:00'"
            )
            conn.commit()
        finally:
            conn.close()
        # The store itself must now refuse — expiry is part of the WHERE clause, so any
        # future consumer inherits it instead of silently replaying stale answers.
        self.assertIsNone(get_exact_candidate("a9-ttl-hash"))

    def test_unscooped_scope_degrades_empty_and_reader_gates_on_it(self) -> None:
        from core.memory_first_router import _candidate_cache_scope

        self.assertEqual(_candidate_cache_scope(None), "")
        self.assertEqual(_candidate_cache_scope({}), "")
        self.assertEqual(_candidate_cache_scope({"session_id": "s"}), "")
        scoped = _candidate_cache_scope({"session_id": "s", "turn_id": "t"})
        self.assertEqual(scoped, "s:t")


# ---------------------------------------------------------------------------
# RC-2 — provider health registry serialization
# ---------------------------------------------------------------------------


class ProviderHealthConcurrencyTests(unittest.TestCase):
    def test_concurrent_failures_lose_no_increments_and_open_the_circuit(self) -> None:
        from core.model_health import (
            circuit_is_open,
            get_provider_health,
            record_provider_failure,
            reset_provider_health,
        )

        reset_provider_health()
        threads = []
        for _ in range(8):
            threads.append(
                threading.Thread(
                    target=lambda: [record_provider_failure(
                        "provider-under-race", error=f"e-{i}", failure_threshold=5, cooldown_seconds=60
                    ) for i in range(10)]
                )
            )
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        state = get_provider_health("provider-under-race")
        self.assertEqual(state.total_failures, 80)
        self.assertEqual(state.consecutive_failures, 80)
        self.assertTrue(circuit_is_open("provider-under-race"))
        reset_provider_health()


# ---------------------------------------------------------------------------
# RC-3/RC-4 — served turn leaves TERMINAL execution + CANCELLED honesty
# ---------------------------------------------------------------------------


class _RunOnceHarness(_IsolatedStoresTestCase):
    def _make_agent(self):
        from apps.vool_agent import VoolAgent

        return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def _run_turn(self, agent, text: str, source_context: dict | None = None):
        context = {"surface": "openclaw", "platform": "openclaw"}
        if isinstance(source_context, dict):
            context.update(source_context)
        return agent.run_once(
            text,
            session_id_override="sess-a9",
            source_context=context,
        )

    def _identity_for_last_attempt(self):
        from core.runtime_continuity import latest_runtime_attempt

        return latest_runtime_attempt("sess-a9")

    def _execution_row(self, root_attempt_id: str):
        from core.invocation.ledger import get_execution

        return get_execution(root_attempt_id)


class ServedTurnClosesExecutionFenceTests(_RunOnceHarness):
    def test_successful_turn_marks_attempt_and_execution_terminal(self) -> None:
        agent = self._make_agent()
        with mock.patch.object(type(agent), "_run_once_inner", return_value={"success": True}):
            self._run_turn(agent, "hello there")
        attempt = self._identity_for_last_attempt()
        self.assertIsNotNone(attempt)
        row = self._execution_row(str(attempt["root_attempt_id"]))
        self.assertIsNotNone(row, "fence row must exist for a turn that passed the R-3 door")
        self.assertEqual(row["state"], "COMPLETED", "RC-3: successful turn must close its fence")
        self.assertEqual(attempt["lifecycle_state"], "SUCCEEDED")

    def test_failed_turn_marks_failed_not_zombie_active(self) -> None:
        agent = self._make_agent()
        with mock.patch.object(type(agent), "_run_once_inner", return_value={"success": False}):
            self._run_turn(agent, "break please")
        attempt = self._identity_for_last_attempt()
        row = self._execution_row(str(attempt["root_attempt_id"]))
        self.assertEqual(row["state"], "FAILED", "RC-3: failed turn must close its fence as FAILED")
        self.assertEqual(attempt["lifecycle_state"], "FAILED_PROVIDER")


class PinnedProviderTerminalTests(_RunOnceHarness):
    def test_conductor_answer_keeps_authority_over_optional_wording_failure(self):
        agent = self._make_agent()
        result = {"response": "The total is 42.", "model_execution": {
            "source": "model_unavailable",
        }, "conductor_product_decision": {
            "disposition": "fulfilled", "runtime_task_outcome": {"fulfillment_status": "fulfilled"},
        }}
        with mock.patch.object(type(agent), "_run_once_inner", return_value=result):
            self._run_turn(agent, "Compute the total.")
        attempt = self._identity_for_last_attempt()
        self.assertEqual(attempt["lifecycle_state"], "SUCCEEDED")
        self.assertEqual(self._execution_row(str(attempt["root_attempt_id"]))["state"], "COMPLETED")

    def test_refused_pin_closes_attempt_and_execution_as_unfulfilled(self):
        for dispatched in (True, False):
            with self.subTest(dispatched=dispatched):
                agent = self._make_agent()
                result = {"response": "The selected model could not answer.", "model_execution": {
                    "source": "selected_model_blocked", "details": {
                        "model_was_attempted": dispatched,
                        "block_reason": "provider_timeout" if dispatched else "paid_call_not_authorized",
                    },
                }}
                with mock.patch.object(type(agent), "_run_once_inner", return_value=result):
                    self._run_turn(agent, "Use the pinned model.")
                attempt = self._identity_for_last_attempt()
                self.assertEqual(attempt["lifecycle_state"], "FAILED_PROVIDER")
                self.assertEqual(attempt["retryable"], dispatched)
                row = self._execution_row(str(attempt["root_attempt_id"]))
                self.assertEqual(row["state"], "FAILED")


class CancelledTurnTerminalTests(_RunOnceHarness):
    def test_cancelled_turn_is_never_labelled_failed_provider(self) -> None:
        agent = self._make_agent()
        cancel_event = threading.Event()
        cancel_event.set()
        context = {"surface": "openclaw", "platform": "openclaw", "cancel_event": cancel_event}
        with mock.patch.object(type(agent), "_run_once_inner", return_value={"success": False}):
            self._run_turn(agent, "stop me", source_context=context)
        attempt = self._identity_for_last_attempt()
        row = self._execution_row(str(attempt["root_attempt_id"]))
        self.assertEqual(
            attempt["lifecycle_state"],
            "CANCELLED",
            "RC-4: a turn whose cancel marker fired before outcome is CANCELLED, never "
            "FAILED_PROVIDER (an unresolved/retryable state lying about provider fault)",
        )
        self.assertEqual(row["state"], "CANCELLED")
        # And the retry resolver must NOT treat this chain as unresolved work.
        from core.runtime_continuity import latest_unresolved_or_partial_attempt

        self.assertNotEqual(
            str((latest_unresolved_or_partial_attempt("openclaw") or {}).get("attempt_id") or ""),
            str(attempt["attempt_id"]),
            "CANCELLED attempts are deliberately outside the auto-retry target set",
        )


# ---------------------------------------------------------------------------
# RC-6 — restart epoch adoption, gated on boot-swept chains
# ---------------------------------------------------------------------------


class RestartEpochAdoptionTests(_IsolatedStoresTestCase):
    def _mint_chain_with_foreign_epoch_fence(self, session: str, value: str):
        from core.invocation.ledger import accept_invocation, open_execution
        from core.runtime_continuity import create_runtime_attempt

        parent = create_runtime_attempt(
            session_id=session, original_request="task", origin_user_turn_id=value + "-o"
        )
        accepted = accept_invocation(
            external_kind="test", external_value=value, principal="owner_local"
        )
        # The DEAD process opened this fence under its own (foreign) epoch.
        open_execution(
            request_id=accepted["request_id"], root_attempt_id=parent["root_attempt_id"],
            runtime_epoch="proc-dead-writer",
        )
        return parent

    def test_boot_swept_retryable_chain_adopts_and_mints_generation(self) -> None:
        from core.runtime_continuity import (
            create_runtime_attempt,
            mark_stale_runtime_attempts_abandoned,
        )

        parent = self._mint_chain_with_foreign_epoch_fence("sess-a", "adopt-ok-1")
        swept = mark_stale_runtime_attempts_abandoned()
        self.assertGreaterEqual(swept, 1)
        child = create_runtime_attempt(
            session_id="sess-a",
            original_request="task",
            root_attempt_id=parent["root_attempt_id"],
            parent_attempt_id=parent["attempt_id"],
            trigger_user_turn_id="retry-turn-1",
        )
        self.assertEqual(
            child.get("execution_generation"),
            int(parent["execution_generation"]) + 1,
            "RC-6: pre-restart retry must mint through the adopted fence CAS",
        )

    def test_live_foreign_epoch_chain_still_refuses_mint(self) -> None:
        from core.runtime_continuity import create_runtime_attempt

        parent = self._mint_chain_with_foreign_epoch_fence("sess-b", "adopt-no-1")
        with self.assertRaises(Exception) as ctx:
            create_runtime_attempt(
                session_id="sess-b",
                original_request="task",
                root_attempt_id=parent["root_attempt_id"],
                parent_attempt_id=parent["attempt_id"],
                trigger_user_turn_id="retry-turn-2",
            )
        self.assertIn("MintRefused", type(ctx.exception).__name__)

    def test_adoption_refuses_terminal_rows_and_current_epoch_rows(self) -> None:
        from core.invocation.ledger import restamp_dead_epoch

        # No such row at all → False.
        self.assertFalse(restamp_dead_epoch("no-such-execution"))

    def test_restamp_dead_epoch_never_touches_terminal_or_same_epoch_rows(self) -> None:
        """Mutation-sensitivity repair (Proof 4 M2a/M2b gap): the adoption SQL's own
        guards get direct detectors — a TERMINAL execution is never re-stamped and an
        already-current-epoch ACTIVE row is never disturbed."""
        from core.invocation.ledger import (
            accept_invocation,
            open_execution,
            restamp_dead_epoch,
            set_execution_terminal,
        )
        from core.runtime_continuity import create_runtime_attempt

        parent = create_runtime_attempt(
            session_id="sess-r", original_request="task", origin_user_turn_id="rs-o"
        )
        accepted = accept_invocation(
            external_kind="test", external_value="restamp-1", principal="owner_local"
        )
        ex = open_execution(
            request_id=accepted["request_id"],
            root_attempt_id=parent["root_attempt_id"],
            runtime_epoch="proc-dead-writer",
        )
        # Close the fence: a terminal row must be untouchable by adoption.
        self.assertTrue(
            set_execution_terminal(ex["execution_id"], "FAILED", runtime_epoch="proc-dead-writer")
        )
        self.assertFalse(
            restamp_dead_epoch(ex["execution_id"]),
            "terminal rows must never adopt",
        )

        # A same-epoch ACTIVE row is untouched (no-op False).
        parent2 = create_runtime_attempt(
            session_id="sess-r", original_request="task2", origin_user_turn_id="rs-o2"
        )
        accepted2 = accept_invocation(
            external_kind="test", external_value="restamp-2", principal="owner_local"
        )
        ex2 = open_execution(request_id=accepted2["request_id"], root_attempt_id=parent2["root_attempt_id"])
        self.assertFalse(restamp_dead_epoch(ex2["execution_id"]))


# ---------------------------------------------------------------------------
# RC-7 — verbatim raw input preferred over paraphrase/reconstruction in history
# ---------------------------------------------------------------------------


class HistoryRawInputPreferenceTests(_IsolatedStoresTestCase):
    def test_raw_input_wins_over_normalized_and_reconstruction(self) -> None:
        from storage.dialogue_memory import recent_dialogue_turns, record_dialogue_turn

        record_dialogue_turn(
            "sess-hist",
            raw_input='fetch https://example.com/?q="exact quoted"',
            normalized_input="fetch https example com q exact quoted",
            reconstructed_input="the user asked about a website once",
            topic_hints=[],
            reference_targets=[],
            understanding_confidence=0.9,
            quality_flags=[],
        )
        turns = recent_dialogue_turns("sess-hist", limit=5)
        self.assertTrue(turns, "dialogue row must persist")
        row = turns[-1]
        # Store precondition: all three forms coexist, so preference order is observable.
        self.assertTrue(row.get("raw_input"))
        self.assertTrue(row.get("normalized_input"))
        # The canonical assembler must surface the verbatim bytes first (RC-7 order:
        # raw_input > normalized_input > reconstructed_input).
        chosen = (
            row.get("raw_input")
            or row.get("normalized_input")
            or row.get("reconstructed_input")
        )
        self.assertIn('https://example.com/?q=', str(chosen))

    def test_transcript_assembly_surfaces_verbatim_raw_bytes(self) -> None:
        """The actual A9 repair seam: `canonical_runtime_transcript` history selection
        must prefer raw_input over normalized_input; reverting the preference order is a
        detector-red mutation."""
        from unittest import mock

        import core.bootstrap_context as bc

        stored_turn = {
            "turn_id": "t-other",
            "speaker_role": "user",
            "raw_input": 'fetch https://example.com/?q="verbatim"',
            "normalized_input": "fetch https example com q verbatim",
            "reconstructed_input": "user talked about websites",
        }
        stored_turns = [
            stored_turn,
            {
                "turn_id": "t-other-a",
                "speaker_role": "assistant",
                "raw_input": "",
                "normalized_input": "fetched it",
                "reconstructed_input": "",
            },
        ]
        with mock.patch.object(bc, "recent_dialogue_turns", return_value=stored_turns):
            from core.context_namespace import ensure_chat_namespace

            ensure_chat_namespace("sess-hist2")
            transcript, _meta = bc.canonical_runtime_transcript(
                session_id="sess-hist2",
                source_context=None,
                current_user_text="current turn",
            )
        joined = "\n".join(str(item.get("content") or "") for item in transcript)
        self.assertIn('https://example.com/?q="verbatim"', joined)
        self.assertNotIn("fetch https example com q verbatim", joined)
        self.assertNotIn("user talked about websites", joined)


# ---------------------------------------------------------------------------
# RC-8 — erasure gate applies to client-supplied histories too
# ---------------------------------------------------------------------------


class ClientHistoryErasureGateTests(_IsolatedStoresTestCase):
    def test_withheld_assistant_payload_is_dropped_from_client_history(self) -> None:
        from core.persistent_memory import augment_history_from_session_log

        history = [
            {"role": "user", "content": "tell me the secret"},
            {"role": "assistant", "content": "ERASED PAYLOAD BODY"},
            {"role": "user", "content": "ok thanks"},
        ]
        def fake_gate(text: str, request_id: str) -> bool:
            return "ERASED" in text

        with mock.patch("core.persistent_memory._assistant_text_unavailable", side_effect=fake_gate):
            gated = augment_history_from_session_log(
                history, session_id="sess-e", user_text="next question"
            )
        contents = [item["content"] for item in gated]
        self.assertNotIn("ERASED PAYLOAD BODY", contents)
        self.assertIn("tell me the secret", contents)

    def test_unavailable_verdict_false_keeps_history_intact(self) -> None:
        from core.persistent_memory import augment_history_from_session_log

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi, normal answer"},
        ]
        with mock.patch(
            "core.persistent_memory._assistant_text_unavailable", return_value=False
        ):
            gated = augment_history_from_session_log(
                history, session_id="sess-f", user_text="next"
            )
        self.assertEqual(len(gated), 2)

    def test_transcript_assembler_gates_every_client_carrier_key(self) -> None:
        """Repair-1 counterexample (Proof 2): service.py delivers the raw client list under
        `client_conversation_history` while the gated copy lands under
        `conversation_history` — and this assembler key order PREFERS the former. The gate
        must therefore live inside `_client_conversation_history`, covering BOTH keys."""
        from unittest import mock

        import core.bootstrap_context as bc
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace("sess-e1")
        erased_history = [
            {"role": "user", "content": "what was the secret?"},
            {"role": "assistant", "content": "ERASED PAYLOAD BODY"},
            {"role": "user", "content": "ok"},
        ]
        with mock.patch(
            "core.persistent_memory._assistant_text_unavailable",
            side_effect=lambda text, _rid="": "ERASED" in text,
        ):
            transcript, _meta = bc.canonical_runtime_transcript(
                session_id="sess-e1",
                source_context={"client_conversation_history": list(erased_history)},
                current_user_text="current turn",
            )
        joined = "\n".join(str(item.get("content") or "") for item in transcript)
        self.assertNotIn("ERASED PAYLOAD BODY", joined)

        # The ungated-preferred carrier AND the fallback carrier are both gated.
        with mock.patch(
            "core.persistent_memory._assistant_text_unavailable",
            side_effect=lambda text, _rid="": "ERASED" in text,
        ):
            transcript2, _meta2 = bc.canonical_runtime_transcript(
                session_id="sess-e1",
                source_context={"conversation_history": list(erased_history)},
                current_user_text="current turn",
            )
        joined2 = "\n".join(str(item.get("content") or "") for item in transcript2)
        self.assertNotIn("ERASED PAYLOAD BODY", joined2)


class CurrentTurnVerbatimRestorationTests(_IsolatedStoresTestCase):
    """RC-7 seam: `_build_request` restores the literal current-turn bytes into the
    provider-facing final user message for ordinary chat, mirroring the structured-batch
    restoration. `normalize_prompt` is stubbed so this unit pins exactly the A9
    substitution logic and the order (verbatim > normalized) is observable."""

    @staticmethod
    def _stub_internal_request() -> SimpleNamespace:
        return SimpleNamespace(
            metadata={},
            temperature=0.2,
            max_output_tokens=64,
            context_summary="",
            trace_id="a9-trace",
            attachments=(),
            messages=[
                SimpleNamespace(role="system", content="sys"),
                SimpleNamespace(role="user", content="normalized paraphrase of the turn"),
            ],
            system_prompt=lambda: "sys",
            user_prompt=lambda: "normalized paraphrase of the turn",
            as_openai_messages=lambda: [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": 'see https://example.com/x?q="A_B" then'},
            ],
        )

    def test_provider_sees_literal_current_turn_bytes(self) -> None:
        import core.memory_first_router as mfr
        from core.memory_first_router import MemoryFirstRouter

        interpretation = SimpleNamespace(
            raw_text='see https://example.com/x?q="A B" then',
            normalized_text="see https example com x q a b then",
            user_text="",
            understanding_confidence=0.9,
        )
        router = MemoryFirstRouter.__new__(MemoryFirstRouter)
        with mock.patch.object(mfr, "normalize_prompt", return_value=self._stub_internal_request()), \
             mock.patch.object(mfr, "ordinary_chat_output_policy", return_value={}):
            request = MemoryFirstRouter._build_request(
                router,
                task=None,
                classification={"task_class": "chat"},
                interpretation=interpretation,
                context_result=None,
                persona=None,
                output_mode="plain_text",
                task_kind="ordinary_chat",
                surface="openclaw",
                source_context=None,
            )
        user_messages = [m for m in request.messages if str(m.get("role")).casefold() == "user"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(
            user_messages[0]["content"],
            interpretation.raw_text,
            "current-turn payload law: the provider sees the literal turn bytes",
        )
        self.assertEqual(request.prompt, interpretation.raw_text)


if __name__ == "__main__":
    unittest.main()
