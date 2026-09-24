"""A9 pass-002 — routing/context/retry repair detectors.

One deterministic detector per Sol pass-001 counterexample family, driven through
REAL production seams. Required coverage map:

- A/B  A8 history privacy: ERASED / WITHHELD governed payloads survive
       normalization and can never re-enter prompt construction.
- C    an exception during execution terminalizes FAILED — never success.
- D    callable cancellation maps to CANCELLED at BOTH attempt and execution
       layer; never the retryable FAILED_PROVIDER vocabulary.
- E    live foreign epoch: stale historical ABANDONED+retryable rows cannot
       authorize adoption while a live conflicting execution exists.
- F    boot-swept abandoned+retryable chain with NO live conflict still adopts.
- G/H  referential retry resolves the PRIOR authoritative execution before the
       current follow-up attempt exists to shadow it; the current turn never
       resolves to itself.
- I    ambiguous/self-only retry referent fabricates no target (falls through).
- J    "why did that fail?" narrates the typed prior failure.

Ledger discipline identical to `test_a9_routing_context_retry_pass001.py`:
runtime-continuity store and L0 ledger share one isolated temp DB.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import storage.db as sdb


def _sha_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class _IsolatedStoresTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import os

        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name) / "home"
        # The session home (root-conftest pin) is borrowed state, not ours to delete:
        # remember it so tearDown puts the process back exactly as it found it.
        # Deleting instead poisoned every later test in the process that reads
        # VOOL_HOME directly (measured: shard tests (8), run 35998811173 -- the
        # spawned-session pin test died on KeyError after this suite ran).
        self._previous_vool_home = os.environ.get("VOOL_HOME")
        self._previous_mirror_dir = os.environ.get("VOOL_MIRROR_DATA_DIR")
        os.environ["VOOL_HOME"] = str(home)
        os.environ["VOOL_MIRROR_DATA_DIR"] = str(home / "relay_mirror")
        from core.runtime_paths import configure_runtime_home

        configure_runtime_home(home)
        self._db_path = Path(self._tmp.name) / "a9p2.db"
        from storage.migrations import run_migrations

        run_migrations(db_path=str(self._db_path))
        sdb.configure_default_db_path(str(self._db_path))
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )

        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        # Same discipline as the Foundation A8 fixtures: neutralize any
        # process-global admission / obligation state a previous test file left
        # behind, or finalize_answer would refuse under its stale open set.
        from core.conductor.obligation_ledger import clear_active_set

        clear_active_set()
        from core.semantic.semantic_result_seam import reset_admission

        reset_admission()
        # A9 pass-003 hygiene: another suite's turn/retry machinery may leave a
        # process-global fence tuple bound (ContextVar); a stale identity would
        # make finalize_answer fence against a row of a foreign DB.
        from core.semantic.semantic_admissions import clear_execution_context

        clear_execution_context()

    def tearDown(self) -> None:
        import os

        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )
        from core.runtime_paths import configure_runtime_home

        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        sdb.configure_default_db_path(None)
        configure_runtime_home(None)
        from core.semantic.semantic_admissions import clear_execution_context

        clear_execution_context()
        if self._previous_vool_home is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = self._previous_vool_home
        if self._previous_mirror_dir is None:
            os.environ.pop("VOOL_MIRROR_DATA_DIR", None)
        else:
            os.environ["VOOL_MIRROR_DATA_DIR"] = self._previous_mirror_dir
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

PAYLOAD_ERASED = 'alpha  opaque-id-A9\n\nquoted: "keep exact"'
PAYLOAD_WITHHELD = 'beta   opaque-id-WH\nquoted: "keep exact"'


def _admit_finalize(text: str):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t1", canonical_content=text)


def _make_unavailable(text: str, state: str) -> str:
    commit = _admit_finalize(text)
    fid = commit["finalization_id"]
    if state == "WITHHELD":
        from core.finalization import AVAILABILITY_WITHHELD, set_availability

        set_availability(fid, AVAILABILITY_WITHHELD, reason="pass002")
    else:
        from core.finalization import erase_finalization_payload

        erase_finalization_payload(fid, reason="pass002")
    return fid


def _normalized(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


# ---------------------------------------------------------------------------
# A/B — history privacy survives normalization (RC-8, CE-1)
# ---------------------------------------------------------------------------


class GovernedHistoryNormalizationGateTests(_IsolatedStoresTestCase):
    def _transcript_with_client_copy(self, payload: str) -> str:
        import core.bootstrap_context as bc
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace("sess-a9p2")
        transcript, _meta = bc.canonical_runtime_transcript(
            session_id="sess-a9p2",
            source_context={"client_conversation_history": [
                {"role": "user", "content": "what was it?"},
                {"role": "assistant", "content": payload},
                {"role": "user", "content": "ok"},
            ]},
            current_user_text="current turn",
        )
        return "\n".join(str(i.get("content") or "") for i in transcript)

    def test_A_erased_governed_history_never_enters_after_normalization(self) -> None:
        from core.finalization import payload_availability_for_text

        _make_unavailable(PAYLOAD_ERASED, "ERASED")
        self.assertEqual(payload_availability_for_text(PAYLOAD_ERASED), "ERASED")
        # A9 pass-003 law upgrade: the canonical collapsed derivative of a governed
        # payload now resolves THROUGH GOVERNING LINEAGE to the same verdict — the
        # old "normalized form is ungoverned" gap is exactly what pass-003 closed.
        self.assertEqual(
            payload_availability_for_text(_normalized(PAYLOAD_ERASED)), "ERASED"
        )
        joined = self._transcript_with_client_copy(PAYLOAD_ERASED)
        self.assertNotIn(
            _normalized(PAYLOAD_ERASED),
            joined,
            "A: ERASED governed history re-entered prompt construction after normalization",
        )

    def test_B_withheld_governed_history_never_enters_after_normalization(self) -> None:
        from core.finalization import payload_availability_for_text

        _make_unavailable(PAYLOAD_WITHHELD, "WITHHELD")
        self.assertEqual(payload_availability_for_text(PAYLOAD_WITHHELD), "WITHHELD")
        # A9 pass-003 law upgrade (same as ERASED above): lineage-bound derivative.
        self.assertEqual(
            payload_availability_for_text(_normalized(PAYLOAD_WITHHELD)), "WITHHELD"
        )
        joined = self._transcript_with_client_copy(PAYLOAD_WITHHELD)
        self.assertNotIn(
            _normalized(PAYLOAD_WITHHELD),
            joined,
            "B: WITHHELD governed history re-entered prompt construction after normalization",
        )


# ---------------------------------------------------------------------------
# C/D — terminal truth at the one L0 boundary (RC-3/RC-4, CE-2/CE-3)
# ---------------------------------------------------------------------------


class _RunOnceHarness(_IsolatedStoresTestCase):
    def _agent(self):
        from apps.vool_agent import VoolAgent

        return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

    def _run(self, agent, text, source_context=None):
        context = {"surface": "openclaw", "platform": "openclaw"}
        if isinstance(source_context, dict):
            context.update(source_context)
        return agent.run_once(text, session_id_override="sess-a9p2", source_context=context)

    def _last_attempt(self):
        from core.runtime_continuity import latest_runtime_attempt

        return latest_runtime_attempt("sess-a9p2")

    def _exec_row(self, root_id):
        from core.invocation.ledger import get_execution

        return get_execution(root_id)


class ExceptionTerminalTruthTests(_RunOnceHarness):
    def test_C_exception_during_execution_is_never_success(self) -> None:
        agent = self._agent()

        def boom(*a, **k):
            raise RuntimeError("forced deterministic execution failure")

        with mock.patch.object(type(agent), "_run_once_inner", side_effect=boom):
            with self.assertRaises(RuntimeError):
                self._run(agent, "explode deterministically")
        attempt = self._last_attempt()
        row = self._exec_row(str(attempt["root_attempt_id"]))
        self.assertNotEqual(attempt["lifecycle_state"], "SUCCEEDED", "C: raised attempt became SUCCEEDED")
        self.assertNotEqual(row["state"], "COMPLETED", "C: raised execution became COMPLETED")
        self.assertEqual(attempt["lifecycle_state"], "FAILED_PROVIDER")
        self.assertEqual(row["state"], "FAILED")


class CallableCancellationTerminalTests(_RunOnceHarness):
    def test_D_callable_cancellation_maps_to_typed_cancelled(self) -> None:
        agent = self._agent()
        ctx = {
            "surface": "openclaw",
            "platform": "openclaw",
            # The router accepts a PLAIN CALLABLE cancellation token; the terminal
            # boundary must recognize the same representation.
            "cancellation_token": lambda: True,
        }
        with mock.patch.object(type(agent), "_run_once_inner", return_value={"success": False}):
            self._run(agent, "stop via callable token", source_context=ctx)
        attempt = self._last_attempt()
        row = self._exec_row(str(attempt["root_attempt_id"]))
        self.assertEqual(attempt["lifecycle_state"], "CANCELLED", "D: callable cancellation mislabeled")
        self.assertEqual(row["state"], "CANCELLED")
        from core.runtime_continuity import latest_unresolved_or_partial_attempt

        unresolved = latest_unresolved_or_partial_attempt("sess-a9p2")
        self.assertNotEqual(
            (unresolved and str(unresolved["attempt_id"])) or "",
            str(attempt["attempt_id"]),
            "D: CANCELLED must stay outside the retryable unresolved set",
        )


# ---------------------------------------------------------------------------
# E/F — durable epoch precedence (RC-6, CE-4)
# ---------------------------------------------------------------------------


class EpochPrecedenceTests(_IsolatedStoresTestCase):
    def _chain_under_foreign_dead_epoch(self, session: str, value: str):
        from core.invocation.ledger import accept_invocation, open_execution
        from core.runtime_continuity import create_runtime_attempt

        parent = create_runtime_attempt(
            session_id=session, original_request="task", origin_user_turn_id=value + "-o"
        )
        accepted = accept_invocation(external_kind="test", external_value=value, principal="owner_local")
        open_execution(
            request_id=accepted["request_id"],
            root_attempt_id=parent["root_attempt_id"],
            runtime_epoch="proc-dead-writer",
        )
        return parent

    def test_E_stale_history_cannot_adopt_over_live_conflicting_execution(self) -> None:
        from core.invocation.ledger import set_execution_terminal
        from core.runtime_continuity import (
            claim_runtime_attempt,
            create_runtime_attempt,
            mark_stale_runtime_attempts_abandoned,
        )

        parent = self._chain_under_foreign_dead_epoch("sess-e", "epoch-live-1")
        swept = mark_stale_runtime_attempts_abandoned()
        self.assertGreaterEqual(swept, 1)
        child = create_runtime_attempt(
            session_id="sess-e",
            original_request="task",
            root_attempt_id=parent["root_attempt_id"],
            parent_attempt_id=parent["attempt_id"],
            trigger_user_turn_id="legit-adoption-turn",
        )
        claim_runtime_attempt(str(child["attempt_id"]), to_state="RUNNING")
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE executions SET runtime_epoch = 'proc-live-foreign' WHERE execution_id = ?",
                (str(parent["root_attempt_id"]),),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(Exception) as ctx:
            create_runtime_attempt(
                session_id="sess-e",
                original_request="task",
                root_attempt_id=parent["root_attempt_id"],
                parent_attempt_id=str(child["attempt_id"]),
                trigger_user_turn_id="stale-history-seizure-turn",
            )
        self.assertIn(
            "MintRefused",
            type(ctx.exception).__name__,
            "E: stale historical retryability authorized adoption over a live conflicting execution",
        )

    def test_F_boot_swept_chain_with_no_live_conflict_still_adopts(self) -> None:
        from core.runtime_continuity import create_runtime_attempt, mark_stale_runtime_attempts_abandoned

        parent = self._chain_under_foreign_dead_epoch("sess-f", "adopt-ok-p002")
        swept = mark_stale_runtime_attempts_abandoned()
        self.assertGreaterEqual(swept, 1)
        child = create_runtime_attempt(
            session_id="sess-f",
            original_request="task",
            root_attempt_id=parent["root_attempt_id"],
            parent_attempt_id=parent["attempt_id"],
            trigger_user_turn_id="clean-adoption-turn",
        )
        self.assertEqual(
            child.get("execution_generation"),
            int(parent["execution_generation"]) + 1,
            "F: valid boot-sweep adoption must keep working when no live conflict exists",
        )


# ---------------------------------------------------------------------------
# G/H/I/J — referential resolution identity + ordering (CE-5)
# ---------------------------------------------------------------------------

_RETRY_PHRASE = "retry the exact failed request now"
_EXPLAIN_PHRASE = "why did that fail?"
_PRIOR_REQUEST = "fetch Kaunas weather"


class ReferentialRetryIdentityTests(_RunOnceHarness):
    def _prior_failed_attempt(self):
        from core.runtime_continuity import create_runtime_attempt, update_runtime_attempt

        prior = create_runtime_attempt(
            session_id="sess-a9p2",
            original_request=_PRIOR_REQUEST,
            origin_user_turn_id="orig-turn-1",
        )
        update_runtime_attempt(
            str(prior["attempt_id"]),
            lifecycle_state="FAILED_PROVIDER",
            terminal_reason="http_error_500",
            retryable=True,
            retry_reason="provider_5xx",
            completed=True,
        )
        return prior

    def _followup_turn_context(self, text: str) -> dict:
        """Open the canonical door exactly as `run_once` does BEFORE referential
        resolution runs — minting + claiming THIS turn's own attempt first."""
        from apps.vool_agent import _r3_open_turn_execution
        from core.invocation.ledger import accept_invocation
        from core.semantic.semantic_admissions import (
            _CURRENT_REQUEST_ID,
            set_request_context,
        )
        from core.turn_contract import TurnRequest

        accepted = accept_invocation(
            external_kind="turn",
            external_value=f"p2-{_sha_hex(text)[:16]}",
            principal="owner_local",
            session_binding="sess-a9p2",
        )
        ctx: dict = {
            "surface": "openclaw",
            "platform": "openclaw",
            "_canonical_user_turn_id": f"dlg-{_sha_hex(text)[:10]}",
        }
        token = set_request_context(accepted["request_id"])
        try:
            # R1b: the door opens execution identity FROM the canonical request the
            # ingress minted — one request/turn/session for the whole turn.
            _r3_open_turn_execution(
                ctx,
                turn_request=TurnRequest.from_ingress(
                    user_text=text,
                    source_context=ctx,
                    request_id=accepted["request_id"],
                    turn_id=f"turn-{_sha_hex(text)[:10]}",
                    session_id="sess-a9p2",
                ),
            )
        finally:
            _CURRENT_REQUEST_ID.reset(token)
        return ctx

    def test_G_retry_resolves_prior_execution_not_own_new_attempt(self) -> None:
        prior = self._prior_failed_attempt()
        ctx = self._followup_turn_context(_RETRY_PHRASE)
        current_attempt_id = str(ctx["_execution_identity"]["attempt_id"])
        agent = self._agent()
        result = agent._maybe_answer_attempt_followup_turn(
            effective_input=_RETRY_PHRASE, session_id="sess-a9p2", source_context=ctx
        )
        self.assertIsNotNone(result, "G: classified retry with a resolvable prior attempt must answer")
        # The chain bound to is the PRIOR root, never this follow-up's own fresh root.
        self.assertNotEqual(current_attempt_id, str(prior["attempt_id"]))
        resolved_child = result.get("response") or ""
        self.assertIsInstance(resolved_child, str)

    def test_H_current_retry_turn_must_not_resolve_to_itself(self) -> None:
        from core.attempt_followup import RETRY_ATTEMPT, resolve_followup_attempt

        ctx = self._followup_turn_context(_RETRY_PHRASE)
        current_attempt_id = str(ctx["_execution_identity"]["attempt_id"])
        resolved, reason = resolve_followup_attempt(
            "sess-a9p2", _RETRY_PHRASE, RETRY_ATTEMPT, exclude_attempt_id=current_attempt_id
        )
        self.assertIsNone(
            resolved,
            f"H: current turn shadowed its own referent ({reason})",
        )

    def test_I_ambiguous_referent_fabricates_no_target(self) -> None:
        agent = self._agent()
        ctx = self._followup_turn_context(_EXPLAIN_PHRASE)
        # No prior attempt in this session at all: the ONLY candidate would be the
        # current phrase itself, i.e. ambiguous/self-only → no fabricated target.
        result = agent._maybe_answer_attempt_followup_turn(
            effective_input=_EXPLAIN_PHRASE, session_id="sess-a9p2", source_context=ctx
        )
        self.assertIsNone(result, "I: fabricated an antecedent out of the follow-up itself")

    def test_J_explain_narrates_typed_prior_failure_cause(self) -> None:
        prior = self._prior_failed_attempt()
        ctx = self._followup_turn_context(_EXPLAIN_PHRASE)
        agent = self._agent()
        result = agent._maybe_answer_attempt_followup_turn(
            effective_input=_EXPLAIN_PHRASE, session_id="sess-a9p2", source_context=ctx
        )
        self.assertIsNotNone(result, "J: explain follow-up must answer against the prior failure")
        response = str((result or {}).get("response") or "")
        self.assertIn(_PRIOR_REQUEST, response, "J: explanation did not bind to the prior request")
        current_attempt_id = str(ctx["_execution_identity"]["attempt_id"])
        self.assertNotEqual(str((result or {}).get("resolved_attempt_id") or ""), current_attempt_id)


if __name__ == "__main__":
    unittest.main()
