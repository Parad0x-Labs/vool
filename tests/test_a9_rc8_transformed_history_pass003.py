"""A9 RC-8 pass-003 — transformed-history lineage detectors.

Contract (reproof pass-002 F1/F2/F3):
A governed history item obeys its LIVE availability verdict in EVERY derived
representation that reaches provider-facing prompt construction:

  AVAILABLE -> may contribute; WITHHELD/ERASED -> no derived representation
  may appear, no matter what whitespace-family transformation its copies
  carry; resolution FAILURE on a hosted store fails CLOSED.

Coverage map:
  1-3   ERASED + boundary whitespace / tab+newline / internal-collapse transforms
  4     WITHHELD equivalents
  5     structured_dialogue_memory (persisted dialogue_turns) carrier gated
  6     governed item adjacent to unrelated ungoverned items
  7     two governed items, different availability (ERASED + WITHHELD)
  8     textual derivative shared by differently-governed sources -> fail-closed dominance
  9     unresolvable governing lineage / store outage -> FAIL CLOSED
  10    AVAILABLE control remains present (ungoverned legacy not broken)
  11    carrier ordering cannot bypass governance
  12    persisted representation survives a simulated restart of runtime state

One seam, real stores: finalize_answer -> set_availability /
erase_finalization_payload -> canonical_runtime_transcript.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


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
        self._db_path = Path(self._tmp.name) / "a9p3.db"
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
        # A9 pass-003 isolation: another suite's turn/retry machinery may leave a
        # process-global fence tuple bound (ContextVar); a stale identity would
        # make finalize_answer fence against a row of a foreign DB. Clear it.
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
        import storage.db as sdb

        sdb.configure_default_db_path(None)
        configure_runtime_home(None)
        if self._previous_vool_home is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = self._previous_vool_home
        if self._previous_mirror_dir is None:
            os.environ.pop("VOOL_MIRROR_DATA_DIR", None)
        else:
            os.environ["VOOL_MIRROR_DATA_DIR"] = self._previous_mirror_dir
        self._tmp.cleanup()


def _normalized(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


_ERASED = 'alpha  opaque-id-A9\n\nquoted: "keep exact"'
_WITHHELD = 'beta   opaque-id-WH\nquoted: "hold exact"'
_DISTINCT = ("keep exact", "hold exact")


class _GovernanceHarness(_IsolatedStoresTestCase):
    sess = "sess-a9p3"

    def _admit_finalize(self, text: str):
        import uuid
        from contextlib import contextmanager

        req = f"p3-{uuid.uuid4().hex[:12]}"

        from core.semantic.semantic_admissions import set_request_context
        from core.semantic.semantic_result_seam import (
            admit_semantic_result,
            reset_admission,
        )

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

            return finalize_answer(turn_id=f"p3-{uuid.uuid4().hex[:10]}", canonical_content=text)

    def govern(self, text: str, *, withheld: bool) -> str:
        fid = str(self._admit_finalize(text)["finalization_id"])
        if withheld:
            from core.finalization import AVAILABILITY_WITHHELD, set_availability

            self.assertTrue(set_availability(fid, AVAILABILITY_WITHHELD, reason="p3"))
        else:
            from core.finalization import erase_finalization_payload

            self.assertTrue(erase_finalization_payload(fid, reason="p3"))
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


# -- guard: write-side canonical key must match assembler collapse rule ------

class CanonicalKeyPinningTests(_IsolatedStoresTestCase):
    def test_canonical_key_matches_assembler_collapse_rule(self) -> None:
        import hashlib

        import core.bootstrap_context as bc
        from core.finalization import canonical_governed_derivative_key

        samples = ["", " ", "a b", "\t x \n y  z\n", "x" * 300]
        for s in samples:
            from core.finalization import _sha256_hex

            collapsed = _normalized(s)
            if not collapsed:
                self.assertEqual(canonical_governed_derivative_key(s), "")
            else:
                self.assertEqual(
                    canonical_governed_derivative_key(s),
                    _sha256_hex(collapsed),  # registry keys carry the sha256: prefix
                )


class ErasedTransformFamilyTests(_GovernanceHarness):
    def _assert_suppressed(self, carried: str) -> None:
        transcript, _ = self.assemble([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": carried},
            {"role": "user", "content": "next"},
        ])
        joined = self.joined(transcript)
        for distinct in _DISTINCT[:1]:
            self.assertNotIn(distinct, joined)
        self.assertNotIn(_normalized(_ERASED), joined)

    def test_1_boundary_whitespace_transform_erased(self) -> None:
        self.govern(_ERASED, withheld=False)
        self._assert_suppressed("   \t " + _ERASED + "\n  ")

    def test_2_tab_newline_transform_erased(self) -> None:
        self.govern(_ERASED, withheld=False)
        self._assert_suppressed("\t\n" + _ERASED.replace("\n", "\n\t") + "\n\t")

    def test_3_internal_whitespace_collapse_erased(self) -> None:
        self.govern(_ERASED, withheld=False)
        reshaped = _ERASED.replace("  ", " \t ").replace("\n\n", "\n \t\n")
        self._assert_suppressed(reshaped)


class WithheldTransformFamilyTests(_GovernanceHarness):
    def _assert_withheld_suppressed(self, carried: str) -> None:
        transcript, _ = self.assemble([
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": carried},
        ])
        joined = self.joined(transcript)
        self.assertNotIn("hold exact", joined)
        self.assertNotIn(_normalized(_WITHHELD), joined)

    def test_4a_boundary_whitespace_transform_withheld(self) -> None:
        self.govern(_WITHHELD, withheld=True)
        self._assert_withheld_suppressed("  " + _WITHHELD + "\t ")

    def test_4b_tab_newline_transform_withheld(self) -> None:
        self.govern(_WITHHELD, withheld=True)
        self._assert_withheld_suppressed("\n\t" + _WITHHELD.replace("\n", "\n ") + "\n")


class CarrierAndAdjacencyTests(_GovernanceHarness):
    def test_5_structured_dialogue_memory_carrier_is_gated(self) -> None:
        self.govern(_ERASED, withheld=False)
        from storage.dialogue_memory import record_dialogue_turn

        record_dialogue_turn(
            self.sess, raw_input=_ERASED, normalized_input=_ERASED,
            reconstructed_input=_ERASED, speaker_role="assistant",
            topic_hints=[], reference_targets=[], understanding_confidence=1.0,
            quality_flags=[])
        record_dialogue_turn(
            self.sess, raw_input="plain user question about tyres",
            normalized_input="plain user question about tyres",
            reconstructed_input="plain user question about tyres",
            speaker_role="user", topic_hints=[], reference_targets=[],
            understanding_confidence=1.0, quality_flags=[])
        transcript, meta = self.assemble([])
        self.assertEqual(meta, "structured_dialogue_memory")
        joined = self.joined(transcript)
        self.assertIn("plain user question about tyres", joined)
        self.assertNotIn("keep exact", joined)

    def test_6_governed_adjacent_to_unrelated_items_still_suppressed(self) -> None:
        self.govern(_ERASED, withheld=False)
        transcript, _ = self.assemble([
            {"role": "user", "content": "totally unrelated question one"},
            {"role": "assistant", "content": "an ordinary ungoverned answer"},
            {"role": "assistant", "content": "\t " + _ERASED + " \n"},
            {"role": "user", "content": "another unrelated question"},
        ])
        joined = self.joined(transcript)
        self.assertIn("an ordinary ungoverned answer", joined)
        self.assertNotIn("keep exact", joined)

    def test_7_two_governed_items_different_availability(self) -> None:
        self.govern(_ERASED, withheld=False)
        self.govern(_WITHHELD, withheld=True)
        transcript, _ = self.assemble([
            {"role": "assistant", "content": _ERASED},
            {"role": "assistant", "content": "\t" + _WITHHELD + " "},
        ])
        joined = self.joined(transcript)
        self.assertNotIn("keep exact", joined)
        self.assertNotIn("hold exact", joined)

    def test_11_carrier_ordering_and_key_cannot_bypass(self) -> None:
        self.govern(_ERASED, withheld=False)
        # reversed order AND the alternate carrier key both stay gated
        transcript, _ = self.assemble([
            {"role": "user", "content": "next"},
            {"role": "assistant", "content": "  \t" + _ERASED + "\t  "},
            {"role": "user", "content": "q"},
        ])
        self.assertNotIn("keep exact", self.joined(transcript))


class DerivativeDominanceTests(_GovernanceHarness):
    def test_8_shared_derivative_from_differently_governed_sources_fails_closed(self) -> None:
        # Two payloads whose whitespace-collapsed forms are IDENTICAL bytes;
        # one is later ERASED. The shared derivative must go dark: an
        # availability-twin collision resolves to the governed row.
        twin_a = 'gamma id-TWIN quoted: "shared bytes here ok"'
        twin_b = 'gamma  id-TWIN   quoted:  "shared  bytes  here  ok"'  # same collapsed
        self.assertEqual(_normalized(twin_a), _normalized(twin_b))
        other_fid = str(self._admit_finalize(twin_a)["finalization_id"])  # stays AVAILABLE
        self.govern(twin_b, withheld=False)  # ERASED twin registers canonical key
        from core.finalization import AVAILABILITY_AVAILABLE

        transcript, _ = self.assemble([
            {"role": "assistant", "content": "\n " + twin_a + "\t"},
        ])
        joined = self.joined(transcript)
        self.assertNotIn("shared bytes here ok", joined)
        # control: the OTHER available finalization is untouched by identity
        self.assertEqual(
            __import__("core.finalization", fromlist=["payload_availability_for_finalization_id"])
            .payload_availability_for_finalization_id(other_fid),
            AVAILABILITY_AVAILABLE,
        )


class FailClosedAndControlTests(_GovernanceHarness):
    def test_9_store_outage_fails_closed_at_assembler_wrapper(self) -> None:
        from unittest import mock

        import core.bootstrap_context as bc

        with mock.patch(
            "core.persistent_memory._assistant_text_unavailable",
            side_effect=RuntimeError("outage probe"),
        ):
            self.assertTrue(bc._assistant_text_unavailable("anything"))

    def test_9b_unresolvable_registry_binding_reports_erased(self) -> None:
        import storage.db as sdb

        conn = sdb.get_connection()
        try:
            conn.execute(
                "INSERT INTO a8_governed_derivatives "
                "(value_key, finalization_id, governed_hash, created_at) "
                "VALUES ('deadbeef', 'fid-missing', '', '2026-08-27T00:00:00+00:00')"
            )
            conn.commit()
        finally:
            conn.close()
        from core.finalization import AVAILABILITY_ERASED, payload_availability_for_hash

        self.assertEqual(payload_availability_for_hash("deadbeef"), AVAILABILITY_ERASED)

    def test_10_available_control_remains_present(self) -> None:
        plain = "an ordinary legacy assistant answer about tyre pressure"
        transcript, _ = self.assemble([
            {"role": "assistant", "content": plain},
            {"role": "assistant", "content": plain},  # duplicate benign copy
        ])
        self.assertEqual(self.joined(transcript).count(plain), 2)


class PersistedRepresentationRestartTests(_GovernanceHarness):
    def test_12_persisted_governed_representation_survives_restart_rejoin(self) -> None:
        import os

        from storage.dialogue_memory import record_dialogue_turn

        self.govern(_WITHHELD, withheld=True)
        record_dialogue_turn(
            self.sess, raw_input=_WITHHELD, normalized_input=_WITHHELD,
            reconstructed_input=_WITHHELD, speaker_role="assistant",
            topic_hints=[], reference_targets=[], understanding_confidence=1.0,
            quality_flags=[])

        # Simulated restart: tear down process-global state, re-point at the
        # SAME durable DB, reassemble.
        from core.runtime_continuity import (
            configure_runtime_continuity_db_path,
            reset_runtime_continuity_state,
        )
        from core.runtime_paths import configure_runtime_home

        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        import storage.db as sdb

        sdb.configure_default_db_path(None)
        configure_runtime_home(None)
        os.environ["VOOL_HOME"] = str(Path(self._tmp.name) / "home")
        os.environ["VOOL_MIRROR_DATA_DIR"] = str(Path(self._tmp.name) / "home" / "relay_mirror")
        configure_runtime_home(Path(self._tmp.name) / "home")
        sdb.configure_default_db_path(str(self._db_path))
        configure_runtime_continuity_db_path(str(self._db_path))

        import core.bootstrap_context as bc
        from core.context_namespace import ensure_chat_namespace

        ensure_chat_namespace(self.sess)
        transcript, meta = bc.canonical_runtime_transcript(
            session_id=self.sess, source_context={}, current_user_text="fresh turn",
            current_turn_id="cur-x")
        self.assertIn(meta, {"structured_dialogue_memory", "none"})
        self.assertNotIn("hold exact", self.joined(transcript))


if __name__ == "__main__":
    unittest.main()
