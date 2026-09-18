"""Repair 1 (Mnemosyne review, 2026-08-06): mark_stale_runtime_attempts_abandoned must write
runtime_sessions.event_count back in the SAME transaction as the Activity event it inserts, or the
session's event sequence permanently diverges from what the next append_runtime_event computes --
confirmed live: every later append for that session raised
`UNIQUE constraint failed: runtime_session_events.session_id, seq` and the session was left
permanently 500ing. Also required: distinct sequential events for 2+ stale attempts in one session,
one broken session unable to block reconciliation of another, and attempt-transition + Activity
event committed as one atomic unit per attempt.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.runtime_continuity as rc
from core.runtime_continuity import (
    AttemptLifecycle,
    append_runtime_event,
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    get_runtime_attempt,
    list_runtime_session_events,
    mark_stale_runtime_attempts_abandoned,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


class RestartSweepEventCounterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "sweep.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_one_interrupted_attempt_is_abandoned_and_the_next_event_still_appends(self) -> None:
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        self.assertEqual(attempt["lifecycle_state"], AttemptLifecycle.RECEIVED.value)

        abandoned_count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(abandoned_count, 1)
        reloaded = get_runtime_attempt(attempt["attempt_id"])
        self.assertEqual(reloaded["lifecycle_state"], AttemptLifecycle.ABANDONED.value)

        events = list_runtime_session_events("s1")
        abandon_events = [e for e in events if e["event_type"] == "runtime_attempt_abandoned"]
        self.assertEqual(len(abandon_events), 1)

        # The defect this repairs: the next event used to collide on the same seq.
        next_event = append_runtime_event(session_id="s1", event_type="status", message="next turn")
        self.assertGreater(next_event["seq"], abandon_events[0]["seq"])

    def test_two_interrupted_attempts_in_one_session_get_distinct_sequential_events(self) -> None:
        a1 = create_runtime_attempt(session_id="s1", original_request="req 1", answer_mode="LIVE_DATA")
        a2 = create_runtime_attempt(session_id="s1", original_request="req 2", answer_mode="LIVE_DATA")

        abandoned_count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(abandoned_count, 2)
        self.assertEqual(get_runtime_attempt(a1["attempt_id"])["lifecycle_state"], AttemptLifecycle.ABANDONED.value)
        self.assertEqual(get_runtime_attempt(a2["attempt_id"])["lifecycle_state"], AttemptLifecycle.ABANDONED.value)

        events = list_runtime_session_events("s1")
        abandon_seqs = [e["seq"] for e in events if e["event_type"] == "runtime_attempt_abandoned"]
        self.assertEqual(len(abandon_seqs), 2)
        self.assertEqual(len(set(abandon_seqs)), 2, "both restart events must land on distinct sequence numbers")

        next_event = append_runtime_event(session_id="s1", event_type="status", message="next turn")
        self.assertGreater(next_event["seq"], max(abandon_seqs))

    def test_two_sessions_one_broken_fixture_cannot_block_the_other_session(self) -> None:
        good = create_runtime_attempt(session_id="s-good", original_request="req", answer_mode="LIVE_DATA")
        bad = create_runtime_attempt(session_id="s-bad", original_request="req", answer_mode="LIVE_DATA")

        real_upsert = rc._upsert_runtime_session

        def flaky_upsert(conn, *, session_id, **kwargs):
            if session_id == "s-bad":
                raise sqlite3.OperationalError("simulated malformed fixture")
            return real_upsert(conn, session_id=session_id, **kwargs)

        with mock.patch.object(rc, "_upsert_runtime_session", side_effect=flaky_upsert):
            abandoned_count = mark_stale_runtime_attempts_abandoned()

        # The healthy session's attempt is reconciled regardless of the broken one.
        self.assertEqual(abandoned_count, 1)
        self.assertEqual(get_runtime_attempt(good["attempt_id"])["lifecycle_state"], AttemptLifecycle.ABANDONED.value)
        # The broken row's own transaction rolled back -- it stays RECEIVED, not half-abandoned.
        self.assertEqual(get_runtime_attempt(bad["attempt_id"])["lifecycle_state"], AttemptLifecycle.RECEIVED.value)

        # A later sweep (e.g. after the underlying fault clears) can still pick the broken one up.
        with mock.patch.object(rc, "_upsert_runtime_session", side_effect=flaky_upsert):
            second_pass = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(second_pass, 0, "already-abandoned good attempt must not be reprocessed")
        recovered = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(recovered, 1)
        self.assertEqual(get_runtime_attempt(bad["attempt_id"])["lifecycle_state"], AttemptLifecycle.ABANDONED.value)


class SabotageEventCounterTests(unittest.TestCase):
    """Sabotage: remove the runtime-session counter update -- the next append must reproduce the
    exact UNIQUE constraint failure Mnemosyne found live, and turn red."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "sabotage.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _sabotaged_sweep_without_counter_update(self) -> None:
        """A faithful reproduction of the pre-Repair-1 defect against the real database: inserts
        the restart event using `event_count + 1` but never writes the new count back -- exactly
        the line Repair 1 added and this helper omits."""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT attempt_id, session_id FROM runtime_attempts WHERE lifecycle_state IN ('RECEIVED','PLANNED','RUNNING')"
            ).fetchall()
            for row in rows:
                attempt_id = str(row["attempt_id"])
                session_id = str(row["session_id"] or "")
                conn.execute(
                    "UPDATE runtime_attempts SET lifecycle_state = 'ABANDONED' WHERE attempt_id = ?",
                    (attempt_id,),
                )
                if session_id:
                    existing = conn.execute(
                        "SELECT event_count FROM runtime_sessions WHERE session_id = ?", (session_id,),
                    ).fetchone()
                    seq = int(existing["event_count"] if existing else 0) + 1
                    conn.execute(
                        "INSERT INTO runtime_session_events (session_id, seq, event_type, message, details_json, created_at) "
                        "VALUES (?, ?, 'runtime_attempt_abandoned', 'restart', '{}', '2026-01-01T00:00:00+00:00')",
                        (session_id, seq),
                    )
                    # BUG (sabotage): no write-back of runtime_sessions.event_count here.
            conn.commit()
        finally:
            conn.close()

    def test_sabotage_missing_counter_update_alone_now_self_heals_final_repair_2(self) -> None:
        """Final Repair 2 (Mnemosyne final review): `append_runtime_event` no longer trusts
        `runtime_sessions.event_count` alone -- it self-heals from `MAX(persisted seq)`. This
        sabotage (the sweep never writing event_count back) used to be fatal on its own; it no
        longer is, because a SECOND, independent guard now catches exactly this desync. This is
        the intended, improved behavior, not a regression -- see the combined-sabotage test below
        for the version that still reproduces the original collision."""
        for i in range(3):
            append_runtime_event(session_id="s1", event_type="status", message=f"turn {i}")
        create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        self._sabotaged_sweep_without_counter_update()
        appended = append_runtime_event(session_id="s1", event_type="status", message="next turn after restart")
        self.assertIsInstance(appended["seq"], int)

    def test_sabotage_missing_counter_update_and_neutered_selfheal_reproduces_the_collision(self) -> None:
        """Combined sabotage: the sweep never writes event_count back (Repair 1's own guard
        removed) AND `_next_session_seq` is neutered to ignore MAX(persisted seq) (Final Repair 2's
        guard removed) -- with BOTH guards gone, the original collision reproduces exactly as it
        did before either repair existed."""
        for i in range(3):
            append_runtime_event(session_id="s1", event_type="status", message=f"turn {i}")
        create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        self._sabotaged_sweep_without_counter_update()
        with mock.patch.object(
            rc, "_next_session_seq",
            side_effect=lambda conn, session_id, recorded_event_count: int(recorded_event_count or 0) + 1,
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                append_runtime_event(session_id="s1", event_type="status", message="next turn after restart")

    def test_control_the_real_sweep_does_not_reproduce_the_failure(self) -> None:
        for i in range(3):
            append_runtime_event(session_id="s1", event_type="status", message=f"turn {i}")
        create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        mark_stale_runtime_attempts_abandoned()
        appended = append_runtime_event(session_id="s1", event_type="status", message="next turn after restart")
        self.assertIsInstance(appended["seq"], int)


if __name__ == "__main__":
    unittest.main()
