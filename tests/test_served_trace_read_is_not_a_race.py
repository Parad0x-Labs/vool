"""The overlapping-session trace loss, reproduced deterministically at its real seam.

The observed symptom was a served sufficiency run where one scenario's turn trace was
missing. It did not reproduce: seven full runs of the journey came back green
(176 s, 144 s, 123 s, 137 s, 153 s, 122 s, 141 s). Forensics on a preserved failing
run showed scenario 7's two traces are the only adjacent rowid pair in the whole run
(146, 147) while every other trace is 15-19 apart, so the contended interval is real.

Three candidate causes were ruled out by reading the code: session-id collision (a
pure sha256 of distinct inputs), a ContextVar/turn-identity leak (no module state on
that seam), and a `seq` primary-key collision (different sessions). That left a
reading, not a proof, and a reading is not evidence.

This is the proof. The remaining candidate is a **harness durability race**, not a
storage race: `_traces` does ONE unpolled read of `runtime_session_events` right after
the HTTP turn returns, while its sibling `_session_for_turn` polls a 30-second
deadline for exactly this reason. If the daemon's trace write lands microseconds after
the response does -- which is precisely what a contended interval makes likely -- the
single read sees nothing and the harness reports a lost trace that was never lost.

The test below forces that interval instead of waiting for it. It writes the trace row
from a background thread on a short delay and shows the single read misses it while a
polled read of the same store finds it. No production behaviour is involved: the row is
durable the whole time, and it is the harness that looked once and gave up.

Falsification, stated up front: if the unpolled read found the row anyway, or if the
polled read could not find it either, the harness-race explanation would be wrong and
this file would say so by failing.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tests.test_pb01_served_sufficiency_served import _last_trace, _traces

#: How long the simulated daemon write lands after the reader's first look. Far below
#: the polling deadline and far above a single read's window.
WRITE_DELAY_SECONDS = 0.6


def _make_store(home: Path) -> Path:
    db = home / "data" / "vool_web0_v2.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE runtime_session_events ("
            "  session_id TEXT, event_type TEXT, details_json TEXT)"
        )
        conn.commit()
    finally:
        conn.close()
    return db


def _write_trace(db: Path, turn_key: str) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO runtime_session_events (session_id, event_type, details_json) "
            "VALUES (?, 'turn.trace_completed', ?)",
            ("session-a", json.dumps({"turn_key": turn_key})),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_single_unpolled_read_loses_a_trace_that_is_merely_late(tmp_path: Path) -> None:
    """The mechanism, forced. The row is never lost — the reader looked once."""
    db = _make_store(tmp_path)
    writer = threading.Timer(WRITE_DELAY_SECONDS, _write_trace, args=(db, "turn-late"))
    writer.start()
    try:
        assert _traces(tmp_path) == [], (
            "the write landed before the read, so the interval this test needs was not "
            "created — raise WRITE_DELAY_SECONDS rather than trusting this result"
        )
        # The same store, read again after the write lands, holds it. Nothing was lost.
        writer.join(timeout=5)
        time.sleep(0.05)
        late = _traces(tmp_path)
        assert [t["turn_key"] for t in late] == ["turn-late"], (
            "the polled-equivalent read cannot find the row either, so a harness "
            "durability race is NOT the explanation and this hypothesis is falsified"
        )
    finally:
        writer.cancel()


def test_the_trace_reader_now_waits_the_way_its_sibling_already_did(tmp_path: Path) -> None:
    """`_last_trace` must survive the same interval that defeats a single read.

    This is the repair, asserted at the seam rather than described: the assertion site
    that reported the loss now polls, like `_session_for_turn` polls, so a late write is
    waited for instead of being reported as a missing trace.
    """
    db = _make_store(tmp_path)
    writer = threading.Timer(WRITE_DELAY_SECONDS, _write_trace, args=(db, "turn-late"))
    writer.start()
    try:
        trace = _last_trace(tmp_path)
        assert trace["turn_key"] == "turn-late"
    finally:
        writer.cancel()
        writer.join(timeout=5)


def test_the_wait_is_bounded_and_a_genuinely_absent_trace_still_fails(tmp_path: Path) -> None:
    """A poll that never gives up would turn a real loss into a hang.

    The worst case for this repair is not a late trace; it is a trace that never
    arrives, where an unbounded wait converts a clear failure into a test that hangs
    until the suite is killed. It must still fail, and quickly.
    """
    _make_store(tmp_path)
    started = time.monotonic()
    with pytest.raises(AssertionError, match=r"no turn\.trace_completed"):
        _last_trace(tmp_path, timeout=1.0)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"the bounded wait took {elapsed:.1f}s; it is not bounded"
