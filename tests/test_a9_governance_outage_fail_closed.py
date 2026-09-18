"""A9 PASS-003 outage repair — governance-store readiness fails CLOSED.

Root cause (Sol PASS-003 final confirmation, deterministic FAIL): the old
``governance_store_ready()`` probed the governed table directly and caught ANY
exception as ``False``, so a hosted-but-unreachable store (DB outage) was
indistinguishable from a store positively proven absent. The outage-derived
``False`` was also cached per process. Callers therefore classified an outage
as "no governance hosted here" and served ERASED assistant history into the
canonical transcript and the provider-facing request.

Repaired invariant: readiness is tri-state —

  READY        table positively proven present (proven; cached);
  ABSENT       table positively proven absent   (proven; cached);
  UNAVAILABLE  the probe itself failed          (unknown; NEVER cached).

A readiness outage is never proof that governance does not apply: for
privacy-governed assistant-history handling UNAVAILABLE behaves fail-closed,
and recovery after a transient failure re-probes instead of trusting a
poisoned cache.

Coverage map (each numbered case is independent):
  1   store positively proven absent -> ungoverned legacy history still serves
  2   store READY -> erased content blocked, ungoverned control serves
  3a  forced DB outage -> UNAVAILABLE, ready()==True, nothing cached
  3b  unadjudicable assistant history does not reach the transcript
  4a  exact PASS-003 counterexample, canonical transcript leg
  4b  exact PASS-003 counterexample, provider-facing normalize_prompt leg
  5a  outage never caches a durable permissive False
  5b  recovery after the transient failure (no manual cache reset)
  5c  warm READY cache + outage still fails closed
  6   writer persistence gate fails closed during the same outage

One seam, real stores: finalize_answer -> erase_finalization_payload ->
canonical_runtime_transcript / normalize_prompt, with ONLY
``core.finalization.get_connection`` forced to raise for the outage cases.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_OUTAGE = RuntimeError("governance database unavailable")

_ERASED = 'alpha  opaque-id-A9\n\nquoted: "keep exact"'
_ERASED_CARRIED = "   \t " + _ERASED + "\n  "
_UNGOVERNED = "an ordinary ungoverned answer about tyre pressure"


class _IsolatedStoresTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import os

        self._tmp = tempfile.TemporaryDirectory()
        home = Path(self._tmp.name) / "home"
        os.environ["VOOL_HOME"] = str(home)
        os.environ["VOOL_MIRROR_DATA_DIR"] = str(home / "relay_mirror")
        from core.runtime_paths import configure_runtime_home

        configure_runtime_home(home)
        self._db_path = Path(self._tmp.name) / "a9outage.db"
        from storage.migrations import run_migrations

        run_migrations(db_path=str(self._db_path))
        import storage.db as sdb

        sdb.configure_default_db_path(str(self._db_path))
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )

        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()
        from core.conductor.obligation_ledger import clear_active_set

        clear_active_set()
        from core.semantic.semantic_result_seam import reset_admission

        reset_admission()
        from core.semantic.semantic_admissions import clear_execution_context

        clear_execution_context()
        from core.finalization import reset_governance_readiness_for_tests

        reset_governance_readiness_for_tests()

    def tearDown(self) -> None:
        import os

        from core.finalization import reset_governance_readiness_for_tests
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )
        from core.runtime_paths import configure_runtime_home

        reset_governance_readiness_for_tests()
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        import storage.db as sdb

        sdb.configure_default_db_path(None)
        configure_runtime_home(None)
        os.environ.pop("VOOL_HOME", None)
        os.environ.pop("VOOL_MIRROR_DATA_DIR", None)
        self._tmp.cleanup()


class _GovernanceHarness(_IsolatedStoresTestCase):
    sess = "sess-a9-outage"

    def _admit_finalize(self, text: str):
        from core.semantic.semantic_admissions import set_request_context
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            reset_admission,
        )

        req = f"og-{uuid.uuid4().hex[:12]}"
        reset_admission()
        admit_semantic_result({"response": text, "route_reason": "model_lane"})

        @contextmanager
        def _scope():
            set_request_context(req)
            try:
                yield
            finally:
                set_request_context("")

        with _scope():
            from core.finalization import finalize_answer

            return finalize_answer(
                turn_id=f"og-{uuid.uuid4().hex[:10]}", canonical_content=text
            )

    def erase(self, text: str) -> str:
        fid = str(self._admit_finalize(text)["finalization_id"])
        from core.finalization import erase_finalization_payload

        self.assertTrue(erase_finalization_payload(fid, reason="outage-suite"))
        return fid

    def assemble(self, history_items: list[dict]) -> tuple[list[dict], str]:
        import core.bootstrap_context as bc
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        return bc.canonical_runtime_transcript(
            session_id=self.sess,
            source_context={"client_conversation_history": history_items},
            current_user_text="current turn",
        )

    @staticmethod
    def joined(transcript) -> str:
        return "\n".join(str(i.get("content") or "") for i in transcript)

    @contextmanager
    def outage(self):
        """Force ONLY core.finalization.get_connection to raise — the exact
        PASS-003 seam. Everything else stays real."""
        with mock.patch(
            "core.finalization.get_connection", side_effect=_OUTAGE
        ):
            yield

    def history_with_erased(self) -> list[dict]:
        return [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": _ERASED_CARRIED},
            {"role": "user", "content": "next"},
        ]


class AbsentStoreLegacyTests(_GovernanceHarness):
    def test_1_proven_absent_store_serves_ungoverned_legacy_history(self) -> None:
        """A store positively proven absent stays honest legacy: history is
        ungoverned and serves. Absence is proven by a COMPLETED probe, never
        inferred from a probe failure."""
        import storage.db as sdb

        conn = sdb.get_connection()
        try:
            conn.execute("DROP TABLE IF EXISTS a8_governed_derivatives")
            conn.execute("DROP TABLE IF EXISTS a7_governance_events")
            conn.execute("DROP TABLE IF EXISTS a7_finalizations")
            conn.commit()
        finally:
            conn.close()
        import core.finalization as fin

        fin.reset_governance_readiness_for_tests()
        self.assertEqual(fin.governance_store_state(), fin.GOVERNANCE_STORE_ABSENT)
        self.assertFalse(fin.governance_store_ready())
        transcript, _source = self.assemble(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": _UNGOVERNED},
            ]
        )
        self.assertIn(_UNGOVERNED, self.joined(transcript))


class HostedStoreControlTests(_GovernanceHarness):
    def test_2_ready_store_blocks_erased_and_serves_ungoverned_control(self) -> None:
        """With the store READY the current invariant holds: governed/erased
        content is blocked while an ungoverned sibling still serves."""
        self.erase(_ERASED)
        import core.finalization as fin

        self.assertEqual(fin.governance_store_state(), fin.GOVERNANCE_STORE_READY)
        transcript, _source = self.assemble(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": _UNGOVERNED},
                {"role": "assistant", "content": _ERASED_CARRIED},
            ]
        )
        joined = self.joined(transcript)
        self.assertIn(_UNGOVERNED, joined)
        self.assertNotIn("keep exact", joined)


class OutageDiscriminationTests(_GovernanceHarness):
    def test_3a_readiness_outage_is_unavailable_not_absent(self) -> None:
        """The PASS-003 root cause: a failed readiness probe must report
        UNAVAILABLE (unknown), never classify as store absence, and never
        cache a permissive False."""
        import core.finalization as fin

        self.erase(_ERASED)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            self.assertEqual(
                fin.governance_store_state(), fin.GOVERNANCE_STORE_UNAVAILABLE
            )
            self.assertTrue(
                fin.governance_store_ready(),
                "an outage was classified as proof that governance does not apply",
            )
            self.assertIsNone(
                fin._a8_store_ready_cache,
                "outage-derived readiness was cached as a durable verdict",
            )

    def test_3b_unadjudicable_assistant_history_does_not_serve(self) -> None:
        """During the outage no assistant item's availability can be safely
        adjudicated, so none reaches provider-facing context; user items are
        not governed by this gate and stay."""
        import core.finalization as fin

        self.erase(_ERASED)
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            transcript, _source = self.assemble(
                [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": _UNGOVERNED},
                    {"role": "user", "content": "next"},
                ]
            )
        joined = self.joined(transcript)
        self.assertNotIn(_UNGOVERNED, joined)
        self.assertIn("q", joined)
        self.assertIn("next", joined)


class Pass003CounterexampleTests(_GovernanceHarness):
    """The exact deterministic shape from the Sol PASS-003 final confirmation:
    real hosted store holding an ERASED finalization, cold readiness cache,
    ONLY core.finalization.get_connection raising."""

    def test_4a_native_transcript_has_no_erased_leak(self) -> None:
        import core.finalization as fin

        self.erase(_ERASED)
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            from core.persistent_memory import _assistant_text_unavailable

            self.assertTrue(
                _assistant_text_unavailable(_ERASED, ""),
                "delegated availability resolution resolved an outage into a "
                "permissive verdict (the PASS-003 fail-open)",
            )
            transcript, _source = self.assemble(self.history_with_erased())
        joined = self.joined(transcript)
        self.assertNotIn("keep exact", joined)
        self.assertNotIn(" ".join(_ERASED.split()), joined)
        self.assertIn("q", joined)
        self.assertIn("next", joined)

    def test_4b_provider_messages_have_no_erased_leak(self) -> None:
        import core.finalization as fin

        self.erase(_ERASED)
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        fin.reset_governance_readiness_for_tests()
        current = "current turn"
        with self.outage():
            from core.prompt_normalizer import normalize_prompt

            request = normalize_prompt(
                task=SimpleNamespace(task_id="og-4b", task_summary=current),
                classification={"task_class": "chat_conversation", "risk_flags": []},
                interpretation=SimpleNamespace(
                    raw_text=current,
                    normalized_text=current,
                    reconstructed_text=current,
                    intent_mode="request",
                    topic_hints=[],
                    reference_targets=[],
                    understanding_confidence=0.9,
                    quality_flags=[],
                    is_continuation=True,
                    state_mutation=None,
                    turn_id="turn-og-4b",
                ),
                context_result=SimpleNamespace(
                    report=SimpleNamespace(to_dict=lambda: {}),
                    assembled_context=lambda **_kw: "",
                ),
                persona=SimpleNamespace(
                    persona_id="default", display_name="VOOL", tone="direct"
                ),
                output_mode="plain_text",
                task_kind="conversation",
                trace_id="og-4b",
                surface="api",
                source_context={
                    "surface": "api",
                    "platform": "api",
                    "runtime_session_id": self.sess,
                    "chat_id": self.sess,
                    "client_conversation_history": [
                        *self.history_with_erased(),
                        {
                            "role": "user",
                            "content": current,
                            "turn_id": "turn-og-4b",
                        },
                    ],
                },
            )
        provider_text = "\n".join(m.content for m in request.messages)
        self.assertNotIn("keep exact", provider_text)
        self.assertNotIn(" ".join(_ERASED.split()), provider_text)


class CacheAndRecoveryTests(_GovernanceHarness):
    def test_5a_outage_never_caches_a_permissive_false(self) -> None:
        import core.finalization as fin

        self.erase(_ERASED)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            for _ in range(3):
                self.assertEqual(
                    fin.governance_store_state(), fin.GOVERNANCE_STORE_UNAVAILABLE
                )
                self.assertIsNone(fin._a8_store_ready_cache)

    def test_5b_recovery_after_transient_outage_without_manual_reset(self) -> None:
        """After the transient failure clears, the NEXT call re-probes and
        governance resumes: erased stays blocked by real adjudication and
        ungoverned history serves again. No manual cache reset in between."""
        import core.finalization as fin

        self.erase(_ERASED)
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            transcript, _source = self.assemble(
                [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": _UNGOVERNED},
                ]
            )
            self.assertNotIn(_UNGOVERNED, self.joined(transcript))
        # Outage over. Readiness must recover on its own.
        self.assertEqual(fin.governance_store_state(), fin.GOVERNANCE_STORE_READY)
        transcript, _source = self.assemble(
            [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": _UNGOVERNED},
                {"role": "assistant", "content": _ERASED_CARRIED},
            ]
        )
        joined = self.joined(transcript)
        self.assertIn(_UNGOVERNED, joined)
        self.assertNotIn("keep exact", joined)

    def test_5c_warm_ready_cache_plus_outage_still_fails_closed(self) -> None:
        """A store proven READY before the outage keeps failing closed while
        the DB is down (the lookup failure itself is fail-closed territory)."""
        import core.finalization as fin

        self.erase(_ERASED)
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        self.assertEqual(fin.governance_store_state(), fin.GOVERNANCE_STORE_READY)
        with self.outage():
            transcript, _source = self.assemble(self.history_with_erased())
        self.assertNotIn("keep exact", self.joined(transcript))


class WriterGateTests(_GovernanceHarness):
    def test_6_writer_persistence_gate_fails_closed_during_outage(self) -> None:
        """Same root cause on the write side: with a cold readiness cache an
        outage previously read as store absence and let governed bytes
        persist. UNAVAILABLE must refuse, and recovery must restore normal
        adjudication."""
        import core.finalization as fin

        self.erase(_ERASED)
        fin.reset_governance_readiness_for_tests()
        with self.outage():
            self.assertFalse(
                fin.writer_may_persist_text(_ERASED),
                "writer gate persisted governed bytes during a readiness outage",
            )
            self.assertFalse(fin.writer_may_persist_text(_UNGOVERNED))
        self.assertFalse(fin.writer_may_persist_text(_ERASED))
        self.assertTrue(fin.writer_may_persist_text(_UNGOVERNED))


if __name__ == "__main__":
    unittest.main()
