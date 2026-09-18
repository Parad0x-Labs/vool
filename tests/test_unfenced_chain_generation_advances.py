"""A retry chain with no L0 fence row still counts its generations.

`create_runtime_attempt` is the only writer of `runtime_attempts.execution_generation`.
Commit `24f7e9f9` moved allocation onto the L0 fence CAS — `bump_generation(root) + 1` —
and deleted the caller's `parent + 1` arithmetic in `core/attempt_retry.py` in the same
change. The CAS runs only when an `executions` row governs the chain. There was no `else`,
so the parameter's default of 1 became the answer for every chain without one: a chain
rooted before L0 existed (which is every retry chain in an upgraded database), or any lane
that mints an attempt with no A0 request bound to open a fence.

The consequence is not cosmetic. `core/execution_records.py` states that a first attempt
and a re-run "are distinguishable after the fact" precisely because the generation bumps,
and `storage/migrations.py` carries a `(root_attempt_id, execution_generation DESC)` index
to read the newest. With the counter stuck, two rows of one chain both claim generation 1
and the retry is indistinguishable from what it replaced.

The in-code comment said the caller-supplied value "stays the fallback so historical lanes
keep their numbering" — but the same commit deleted the only caller that supplied one, so
the documented fallback was dead by construction.

These tests name the unfenced lane specifically. The three tests this repair turned green
assert the chain arithmetic without saying which lane allocates it, so a future change
could satisfy them through the fenced path alone and leave this one broken again.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


class UnfencedChainGenerationTests(unittest.TestCase):
    """No fence row is ever opened here: nothing binds an A0 request."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db = Path(self._tmp.name) / "unfenced.db"
        run_migrations(db_path=self._db)
        configure_runtime_continuity_db_path(str(self._db))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _root(self) -> dict:
        return create_runtime_attempt(
            session_id="s-unfenced",
            original_request="what did I ask you before?",
            answer_mode="LIVE_DATA",
            plan_id="plan-root",
        )

    def _child(self, parent: dict, plan_id: str) -> dict:
        # Two things the production retry lane supplies and a naive caller does not:
        # a trigger turn (the runtime refuses loudly rather than mint an ambiguous
        # generation without a durable idempotency identity), and the chain ROOT --
        # `resolved_root` is the passed root or this attempt's own id, never derived
        # from the parent, so omitting it silently starts a new one-row chain.
        return create_runtime_attempt(
            root_attempt_id=str(parent["root_attempt_id"] or parent["attempt_id"]),
            session_id="s-unfenced",
            original_request="what did I ask you before?",
            answer_mode="LIVE_DATA",
            plan_id=plan_id,
            parent_attempt_id=str(parent["attempt_id"]),
            trigger_user_turn_id=f"turn-{plan_id}",
        )

    def test_a_retry_of_an_unfenced_chain_advances_the_generation(self) -> None:
        parent = self._root()
        child = self._child(parent, "plan-retry-1")
        self.assertEqual(child["root_attempt_id"], parent["attempt_id"])
        self.assertEqual(
            int(child["execution_generation"]),
            int(parent["execution_generation"]) + 1,
            "an unfenced retry took its parent's generation: the chain counter never advances",
        )

    def test_a_third_attempt_keeps_counting_rather_than_repeating(self) -> None:
        """Two rows at the same generation is the shape the defect produced."""
        parent = self._root()
        second = self._child(parent, "plan-retry-1")
        third = self._child(second, "plan-retry-2")
        generations = [
            int(parent["execution_generation"]),
            int(second["execution_generation"]),
            int(third["execution_generation"]),
        ]
        self.assertEqual(generations, [1, 2, 3], generations)
        self.assertEqual(len(set(generations)), 3, "two attempts of one chain share a generation")

    def test_an_explicit_caller_value_ahead_of_the_chain_still_wins(self) -> None:
        """The repair allocates, it does not overrule.

        Lanes that supply their own numbering (legacy migration, the mutation matrix) pass
        an explicit generation on unfenced chains. Clamping them to chain+1 would be a
        second defect wearing the first one's clothes.
        """
        parent = self._root()
        child = create_runtime_attempt(
            session_id="s-unfenced",
            original_request="what did I ask you before?",
            answer_mode="LIVE_DATA",
            plan_id="plan-explicit",
            root_attempt_id=str(parent["root_attempt_id"] or parent["attempt_id"]),
            parent_attempt_id=str(parent["attempt_id"]),
            trigger_user_turn_id="turn-explicit",
            execution_generation=9,
        )
        self.assertEqual(int(child["execution_generation"]), 9)

    def test_the_durable_row_and_the_returned_row_agree(self) -> None:
        """What was written must equal what the caller was handed.

        The recorded event used to publish the REQUESTED generation rather than the one
        actually minted, so a reader of the event disagreed with the table on every retry.
        """
        import sqlite3

        parent = self._root()
        child = self._child(parent, "plan-retry-1")
        conn = sqlite3.connect(self._db)
        try:
            row = conn.execute(
                "SELECT execution_generation FROM runtime_attempts WHERE attempt_id = ?",
                (str(child["attempt_id"]),),
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, "the retry was never written to runtime_attempts")
        self.assertEqual(
            int(row[0]),
            int(child["execution_generation"]),
            "the durable row disagrees with the row the caller was handed",
        )
        self.assertEqual(int(row[0]), 2)


if __name__ == "__main__":
    unittest.main()
