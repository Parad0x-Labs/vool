"""Final Repair 2 (Mnemosyne final review, 2026-08-07): a session already damaged by Repair 1's
own predecessor bug (event_count left permanently understating MAX(seq)) stayed permanently
broken, because `append_runtime_event` derived the next sequence from `runtime_sessions.event_count`
alone. Every later call recomputed the SAME already-taken seq and hit the `(session_id, seq)`
primary key -- a session that entered this state before this fix landed had NO path back to health.

Fixed by taking `max(event_count, MAX(persisted seq)) + 1` (`_next_session_seq`), applied both to
`append_runtime_event` and to the restart-abandon sweep's own per-session cache (Repair 1's own
cache had the identical vulnerability -- seeded from event_count alone).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.runtime_continuity import (
    _next_session_seq,
    append_runtime_event,
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    list_runtime_session_events,
    mark_stale_runtime_attempts_abandoned,
    reset_runtime_continuity_state,
)
from storage.migrations import run_migrations


def _force_desync(db_path: Path, session_id: str, *, event_count: int) -> None:
    """Directly sets runtime_sessions.event_count to a value that disagrees with the actual
    persisted MAX(seq) -- reproducing exactly the damage the pre-Repair-1 bug left behind, without
    needing to actually run the buggy code."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE runtime_sessions SET event_count = ? WHERE session_id = ?", (event_count, session_id))
        conn.commit()
    finally:
        conn.close()


def _max_persisted_seq(db_path: Path, session_id: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT MAX(seq) FROM runtime_session_events WHERE session_id = ?", (session_id,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def _stored_event_count(db_path: Path, session_id: str) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT event_count FROM runtime_sessions WHERE session_id = ?", (session_id,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


class NextSessionSeqPureFunctionTests(unittest.TestCase):
    """Pure-function tests against `_next_session_seq` directly -- no I/O beyond the one query it
    performs; a real connection is still required since it reads `runtime_session_events`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "pure.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def test_event_count_lower_than_max_seq(self) -> None:
        for i in range(5):
            append_runtime_event(session_id="s1", event_type="status", message=f"e{i}")
        _force_desync(self._db_path, "s1", event_count=2)  # actual MAX(seq) is 5
        conn = self._conn()
        try:
            self.assertEqual(_next_session_seq(conn, "s1", 2), 6)
        finally:
            conn.close()

    def test_event_count_equal_to_max_seq(self) -> None:
        for i in range(3):
            append_runtime_event(session_id="s2", event_type="status", message=f"e{i}")
        conn = self._conn()
        try:
            self.assertEqual(_next_session_seq(conn, "s2", 3), 4)
        finally:
            conn.close()

    def test_event_count_higher_than_max_seq(self) -> None:
        """A session with no persisted events yet but a nonzero recorded count (a fresh session
        row created before its first event, or any other benign skew) -- event_count must still
        win when it is genuinely the larger value."""
        conn = self._conn()
        try:
            self.assertEqual(_next_session_seq(conn, "s3-brand-new", 10), 11)
        finally:
            conn.close()


class AppendRuntimeEventSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "selfheal.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_a_desynced_session_self_heals_on_the_next_event(self) -> None:
        for i in range(5):
            append_runtime_event(session_id="damaged", event_type="status", message=f"e{i}")
        self.assertEqual(_max_persisted_seq(self._db_path, "damaged"), 5)
        _force_desync(self._db_path, "damaged", event_count=2)  # reproduces the pre-Repair-1 damage

        appended = append_runtime_event(session_id="damaged", event_type="status", message="healed")

        self.assertEqual(appended["seq"], 6, "must not recompute an already-taken seq")
        self.assertEqual(_stored_event_count(self._db_path, "damaged"), 6)
        self.assertEqual(_max_persisted_seq(self._db_path, "damaged"), 6)

    def test_several_stale_sessions_each_self_heal_independently(self) -> None:
        for sid in ("stale-a", "stale-b", "stale-c"):
            for i in range(4):
                append_runtime_event(session_id=sid, event_type="status", message=f"e{i}")
            _force_desync(self._db_path, sid, event_count=1)

        results = {sid: append_runtime_event(session_id=sid, event_type="status", message="next")["seq"] for sid in ("stale-a", "stale-b", "stale-c")}
        for sid, seq in results.items():
            with self.subTest(sid=sid):
                self.assertEqual(seq, 5)
                self.assertEqual(_stored_event_count(self._db_path, sid), 5)

    def test_one_stale_session_beside_one_healthy_session(self) -> None:
        for i in range(3):
            append_runtime_event(session_id="healthy", event_type="status", message=f"h{i}")
        for i in range(3):
            append_runtime_event(session_id="stale", event_type="status", message=f"s{i}")
        _force_desync(self._db_path, "stale", event_count=0)

        healthy_next = append_runtime_event(session_id="healthy", event_type="status", message="next")
        stale_next = append_runtime_event(session_id="stale", event_type="status", message="next")

        self.assertEqual(healthy_next["seq"], 4, "a healthy session must not be affected by another session's healing")
        self.assertEqual(stale_next["seq"], 4)

    def test_restart_reconciliation_followed_by_an_ordinary_event(self) -> None:
        create_runtime_attempt(session_id="restart-then-event", original_request="req", answer_mode="LIVE_DATA")
        for i in range(3):
            append_runtime_event(session_id="restart-then-event", event_type="status", message=f"e{i}")
        _force_desync(self._db_path, "restart-then-event", event_count=1)

        abandoned_count = mark_stale_runtime_attempts_abandoned()
        self.assertEqual(abandoned_count, 1)

        next_event = append_runtime_event(session_id="restart-then-event", event_type="status", message="after restart")
        events = list_runtime_session_events("restart-then-event")
        self.assertEqual(len(events), len({e["seq"] for e in events}), "no duplicate sequence numbers")
        self.assertEqual(next_event["seq"], max(e["seq"] for e in events))

    def test_repeated_restart_stays_healthy(self) -> None:
        create_runtime_attempt(session_id="repeated-restart", original_request="req", answer_mode="LIVE_DATA")
        append_runtime_event(session_id="repeated-restart", event_type="status", message="e0")
        _force_desync(self._db_path, "repeated-restart", event_count=0)

        mark_stale_runtime_attempts_abandoned()
        create_runtime_attempt(session_id="repeated-restart", original_request="req2", answer_mode="LIVE_DATA")
        mark_stale_runtime_attempts_abandoned()  # second restart, same session, must not collide

        events = list_runtime_session_events("repeated-restart")
        seqs = [e["seq"] for e in events]
        self.assertEqual(len(seqs), len(set(seqs)), "repeated restart must never produce a duplicate seq")
        self.assertEqual(_stored_event_count(self._db_path, "repeated-restart"), max(seqs))

    def test_healed_event_count_converges_to_max_seq(self) -> None:
        for i in range(7):
            append_runtime_event(session_id="converge", event_type="status", message=f"e{i}")
        _force_desync(self._db_path, "converge", event_count=3)
        append_runtime_event(session_id="converge", event_type="status", message="heal")
        self.assertEqual(_stored_event_count(self._db_path, "converge"), _max_persisted_seq(self._db_path, "converge"))


class SabotageEventCounterOnlyTests(unittest.TestCase):
    """Sabotage: return to `event_count + 1` only -- the pre-damaged-session scenario must
    reproduce the exact sequence collision this repair fixes."""

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

    def test_sabotage_event_count_plus_one_only_reproduces_the_collision(self) -> None:
        for i in range(5):
            append_runtime_event(session_id="sabotage-target", event_type="status", message=f"e{i}")
        _force_desync(self._db_path, "sabotage-target", event_count=2)

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            existing = conn.execute(
                "SELECT event_count FROM runtime_sessions WHERE session_id = ?", ("sabotage-target",),
            ).fetchone()
            sabotaged_seq = int(existing["event_count"]) + 1  # the removed guard's own logic
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO runtime_session_events (session_id, seq, event_type, message, details_json, created_at) "
                    "VALUES (?, ?, 'status', 'sabotaged', '{}', '2026-01-01T00:00:00+00:00')",
                    ("sabotage-target", sabotaged_seq),
                )
        finally:
            conn.close()

    def test_control_the_real_append_does_not_reproduce_the_collision(self) -> None:
        for i in range(5):
            append_runtime_event(session_id="control-target", event_type="status", message=f"e{i}")
        _force_desync(self._db_path, "control-target", event_count=2)
        appended = append_runtime_event(session_id="control-target", event_type="status", message="must not collide")
        self.assertEqual(appended["seq"], 6)


if __name__ == "__main__":
    unittest.main()
