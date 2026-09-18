"""The anchored-signature column round-trip and the pure getSignatureStatuses parser.

Both survive the retirement of the anchor broadcast: the column is additive storage and the parser
is network-free. ``confirm_signature`` (an opt-in, read-only status check) still rides the
authority-free ``_rpc_call`` door; the two door tests below red if that import is dropped again.
"""
from __future__ import annotations

import unittest
import uuid

import pytest

import core.solana_anchor as anchor
from core.final_response_store import (
    get_final_response,
    set_anchored_signature,
    store_final_response,
)
from storage.db import get_connection
from storage.migrations import run_migrations


class AnchoredSignatureRoundTripTests(unittest.TestCase):
    """#4 — the captured anchor tx signature persists on the finalized row (column kept; nothing writes it in production now)."""

    def setUp(self) -> None:
        run_migrations()

    def _new_task_id(self) -> str:
        return f"task-{uuid.uuid4().hex}"

    def test_signature_round_trips_on_finalized_row(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="raw text",
            rendered="rendered text",
            status="finalized",
            confidence=0.9,
        )
        # Before capture the column exists but is null (additive, no default).
        before = get_final_response(task_id)
        assert before is not None
        self.assertIn("anchored_signature", before)
        self.assertIsNone(before["anchored_signature"])

        sig = "5" + "z" * 80  # plausible base58 tx signature
        updated = set_anchored_signature(task_id, sig)
        self.assertTrue(updated)

        after = get_final_response(task_id)
        assert after is not None
        self.assertEqual(after["anchored_signature"], sig)
        # The rest of the row is untouched by the additive update.
        self.assertEqual(after["rendered_persona_text"], "rendered text")
        self.assertEqual(after["status_marker"], "finalized")

    def test_set_signature_is_noop_for_unknown_task(self) -> None:
        self.assertFalse(set_anchored_signature(self._new_task_id(), "deadbeef"))

    def test_set_signature_rejects_empty_inputs(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="r",
            rendered="r",
            status="finalized",
            confidence=0.5,
        )
        self.assertFalse(set_anchored_signature(task_id, ""))
        self.assertFalse(set_anchored_signature("", "sig"))

    def test_column_migration_is_idempotent(self) -> None:
        task_id = self._new_task_id()
        store_final_response(
            parent_task_id=task_id,
            raw="r",
            rendered="r",
            status="finalized",
            confidence=0.5,
        )
        # Repeated reads/writes must not error or duplicate the column.
        self.assertIsNotNone(get_final_response(task_id))
        self.assertTrue(set_anchored_signature(task_id, "sig-1"))
        self.assertTrue(set_anchored_signature(task_id, "sig-2"))
        conn = get_connection()
        try:
            cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(finalized_responses)").fetchall()]
        finally:
            conn.close()
        self.assertEqual(cols.count("anchored_signature"), 1)


class ConfirmSignatureParserTests(unittest.TestCase):
    """#14 — the optional confirm helper parses a getSignatureStatuses fixture."""

    # A representative getSignatureStatuses RPC result for a landed tx.
    _LANDED_FIXTURE = {
        "context": {"slot": 82_493_733},
        "value": [
            {
                "slot": 72_191_500,
                "confirmations": None,
                "err": None,
                "confirmationStatus": "finalized",
            }
        ],
    }

    def test_parses_landed_status(self) -> None:
        status = anchor.parse_signature_status(self._LANDED_FIXTURE)
        assert status is not None
        self.assertEqual(status["confirmationStatus"], "finalized")
        self.assertIsNone(status["err"])
        self.assertEqual(status["slot"], 72_191_500)

    def test_unknown_signature_value_null(self) -> None:
        self.assertIsNone(anchor.parse_signature_status({"context": {}, "value": [None]}))

    def test_empty_or_bad_shapes(self) -> None:
        self.assertIsNone(anchor.parse_signature_status(None))
        self.assertIsNone(anchor.parse_signature_status({}))
        self.assertIsNone(anchor.parse_signature_status({"value": []}))
        self.assertIsNone(anchor.parse_signature_status({"value": "nope"}))

    def test_confirm_signature_is_a_read_only_status_check_with_no_broadcast(self) -> None:
        # Whatever the RPC door's state, confirming a signature must never reach a signing or
        # broadcast surface: the only method it may ever issue is getSignatureStatuses.
        calls: list[str] = []

        def spying_rpc(method: str, params: list, **_kw) -> object:
            calls.append(method)
            return self._LANDED_FIXTURE

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(anchor, "_rpc_call", spying_rpc, raising=False)
            anchor.confirm_signature("somesig")
        self.assertTrue(set(calls) <= {"getSignatureStatuses"}, calls)
        self.assertNotIn("sendTransaction", calls)

    def test_module_exposes_the_rpc_door_confirm_signature_depends_on(self) -> None:
        self.assertTrue(callable(getattr(anchor, "_rpc_call", None)), "solana_anchor._rpc_call is missing")

    def test_confirm_signature_uses_helper_without_hot_path(self) -> None:
        calls: list[tuple[str, list]] = []

        def fake_rpc(method: str, params: list, **_kw) -> object:
            calls.append((method, params))
            return self._LANDED_FIXTURE

        # raising=True: the door must already exist for this to be a real substitution, not a fabrication
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(anchor, "_rpc_call", fake_rpc)
            out = anchor.confirm_signature("somesig", commitment="finalized")

        assert out is not None
        self.assertEqual(out["confirmationStatus"], "finalized")
        self.assertEqual(out["requested_commitment"], "finalized")
        # Exactly one light status call; searchTransactionHistory requested.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "getSignatureStatuses")
        self.assertEqual(calls[0][1][0], ["somesig"])
        self.assertTrue(calls[0][1][1]["searchTransactionHistory"])

    def test_confirm_signature_empty_returns_none(self) -> None:
        self.assertIsNone(anchor.confirm_signature(""))

    def test_confirm_signature_swallows_rpc_failure(self) -> None:
        def boom(method: str, params: list, **_kw) -> object:
            raise RuntimeError("rpc down")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(anchor, "_rpc_call", boom, raising=False)
            self.assertIsNone(anchor.confirm_signature("somesig"))


if __name__ == "__main__":
    unittest.main()
